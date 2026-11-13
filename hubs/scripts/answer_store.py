#!/usr/bin/env python3
"""결정 답 공용 기록. Slack 버튼(slack_socket.py)과 문서 사이트(doc_browser.py)가 같은 지점에서 답을 남김.

한 결정에는 먼저 온 답만 인정함. 판정·기록은 flock 아래 한 번에 하므로 두 입구가 겹쳐도 한쪽만 이김.
늦은 쪽은 Late 를 받고 아무것도 실행하지 않음(inbox·done·후속 동작 모두 건드리지 않음).
기록 형식은 Slack 버튼 때와 같음 — progress_digest·todo_facts·panel_collect·hub_split 이 그대로 읽음.
  slack-inbox.jsonl  한 줄: {type: button, parent_ts, channel, title, decision_id, kind: decide, option_text, received_at, action, pick|text, source}
  slack-done.txt     한 줄: 닫힌 카드 ts
환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs)
"""
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import card_actions as actions  # noqa: E402

SLACK, DOCSITE = "slack", "docsite"
SOURCE_NAME = {SLACK: "Slack", DOCSITE: "문서 사이트"}
LATE_TEXT = {SLACK: "이미 Slack 에서 답함", DOCSITE: "이미 문서 사이트에서 답함"}  # 늦은 쪽에 보여 주는 사유
ANSWER_KINDS = ("pick", "approve")  # 이 동작의 반응·글은 답. 보류(hold)는 카드가 열려 있어 답이 아님
LABEL_MAX = 80  # inbox option_text 길이 상한(Slack 버튼 때와 같음)


@dataclass(frozen=True)
class Answer:
    """답 한 건. extra 는 inbox 줄에 합쳐지는 값: {action: pick, pick: N} 또는 {action: custom, text: ...}"""
    did: str
    ts: str  # 카드(메시지) ts. inbox parent_ts·done 의 키
    channel: str
    title: str
    label: str
    source: str
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Claim:
    """claim 결과. status=claimed 면 이번 답이 첫 답. late 면 by 가 먼저 답한 입구"""
    status: str
    by: str = ""
    taken: tuple = ("none", None)  # card_actions.try_take 결과. claimed 일 때만 의미 있음

    @property
    def late(self) -> bool:
        return self.status == "late"

    def late_text(self) -> str:
        return LATE_TEXT.get(self.by, "이미 답함")


class AnswerStore:
    def __init__(self, hub_dir: str | None = None) -> None:
        self.dir = hub_dir or os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
        self.inbox = os.path.join(self.dir, "slack-inbox.jsonl")
        self.done = os.path.join(self.dir, "slack-done.txt")
        self.sent = os.path.join(self.dir, "slack-sent.jsonl")
        self.lock = os.path.join(self.dir, "answer-store.lock")

    def claim(self, ans: Answer, now: float | None = None) -> Claim:
        """첫 답이면 inbox·done 에 기록하고 감시기 카드 등록을 꺼냄(card-actions). 늦은 답은 기록 없이 Late.
        기록은 잠금 안, 등록 꺼내기는 잠금 밖(card_actions 잠금과 겹쳐 잡지 않음). busy 처리는 호출자 몫"""
        with actions.flock(self.lock):
            by = self.first_answer(ans.did, ans.ts)
            if by:
                return Claim("late", by)
            rec = {"type": "button", "parent_ts": ans.ts, "channel": ans.channel, "title": ans.title, "decision_id": ans.did,
                   "kind": "decide", "option_text": ans.label[:LABEL_MAX], "received_at": int(time.time() if now is None else now),
                   **ans.extra, "source": ans.source}
            self.append(self.inbox, json.dumps(rec, ensure_ascii=False) + "\n")
            self.append(self.done, ans.ts + "\n")
        return Claim("claimed", taken=actions.try_take(ans.did))

    def first_answer(self, did: str, ts: str) -> str:
        """이 결정에 이미 인정된 답이 있으면 그 입구(slack|docsite), 없으면 빈 문자열.
        inbox 의 버튼 답·pick/approve 반응·스레드 글, 또는 done 에 카드 ts 가 있으면(사용자가 직접 닫음) 답한 것으로 봄.
        source 가 없는 옛 기록은 Slack 에서 온 것"""
        for d in self.read_inbox():
            if d.get("decision_id") != did or d.get("kind") != "decide":
                continue
            if d.get("type") == "button" or (d.get("type") in ("reaction", "thread_reply") and d.get("action") in ANSWER_KINDS):
                return d.get("source") if d.get("source") in SOURCE_NAME else SLACK
        return SLACK if ts and ts in self.read_done() else ""

    def read_inbox(self) -> list:
        out = []
        try:
            with open(self.inbox, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict):
                        out.append(d)
        except OSError:
            pass
        return out

    def read_done(self) -> set:
        try:
            with open(self.done, encoding="utf-8") as f:
                return set(f.read().split())
        except OSError:
            return set()

    @staticmethod
    def append(path: str, line: str) -> None:
        """추가 전용 한 줄 기록. 같은 줄이 섞이지 않게 한 번의 write"""
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def find_card(self, did: str) -> dict | None:
        """결정 번호의 가장 최근 번호 카드 기록(slack-sent.jsonl: ts·channel·title·options·tag). 없으면 None.
        선택지 문구는 여기서만 읽음 — 화면이 보낸 문구는 믿지 않음"""
        if not re.fullmatch(r"D-[0-9A-Za-z]{1,16}", did or ""):
            return None
        found = None
        try:
            with open(self.sent, encoding="utf-8") as f:
                for line in f:
                    try:
                        s = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(s, dict) and s.get("form_kind") == "decide" and s.get("decision_id") == did \
                            and isinstance(s.get("ts"), str) and isinstance(s.get("options"), dict):
                        found = s
        except OSError:
            return None
        return found
