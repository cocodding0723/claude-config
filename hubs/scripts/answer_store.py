#!/usr/bin/env python3
"""결정 답 공용 기록. Slack 버튼(slack_socket.py)과 문서 사이트(doc_browser.py)가 같은 지점에서 답을 남김.

한 카드(parent_ts)에는 먼저 온 답만 인정함. 같은 번호로 다시 올린 카드는 새 카드라 따로 판정함.
판정·기록은 flock 아래 한 번에 하므로 두 입구가 겹쳐도 한쪽만 이김.
늦은 쪽은 Late 를 받고 아무것도 실행하지 않음(inbox·done·후속 동작 모두 건드리지 않음).
전달 결과는 answer-delivery.jsonl 에 따로 남김. claim 직후 pending 줄을 쓰고 전달이 끝나면 결과 줄을 더함.
늦은 클릭이 결과 없이 오래된 pending 만 있는 답을 만나면 Redeliver 로 1회만 다시 전달함(프로세스가 전달 전에 죽은 경우).
기록 형식은 Slack 버튼 때와 같음 — progress_digest·todo_facts·panel_collect·hub_split 이 그대로 읽음.
  slack-inbox.jsonl  한 줄: {type: button, parent_ts, channel, title, decision_id, kind: decide, option_text, received_at, action, pick|text, source}
  slack-done.txt     한 줄: 닫힌 카드 ts
  answer-delivery.jsonl  한 줄: {decision_id, parent_ts, state: pending|hub|main|proxy|unknown|lost|action, ok, at, retry}
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
DELIVERY_NAME = "answer-delivery.jsonl"
PENDING = "pending"
PENDING_TTL_S = 300  # 이 시간이 지나도 전달 결과가 없으면 전달 전에 종료된 것으로 봄
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
    rec: dict = field(default_factory=dict)  # redeliver 일 때 먼저 기록된 답(inbox 줄)

    @property
    def late(self) -> bool:
        return self.status == "late"

    @property
    def redeliver(self) -> bool:
        return self.status == "redeliver"

    def replay(self) -> tuple:
        """redeliver 의 (extra, label). 먼저 기록된 답 원문 그대로 다시 보내기 위함"""
        extra = {k: self.rec[k] for k in ("action", "pick", "text") if k in self.rec}
        return extra, str(self.rec.get("option_text") or "")

    def late_text(self) -> str:
        return LATE_TEXT.get(self.by, "이미 답함")


class AnswerStore:
    def __init__(self, hub_dir: str | None = None) -> None:
        self.dir = hub_dir or os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
        self.inbox = os.path.join(self.dir, "slack-inbox.jsonl")
        self.done = os.path.join(self.dir, "slack-done.txt")
        self.sent = os.path.join(self.dir, "slack-sent.jsonl")
        self.lock = os.path.join(self.dir, "answer-store.lock")
        self.delivery = os.path.join(self.dir, DELIVERY_NAME)

    def claim(self, ans: Answer, now: float | None = None) -> Claim:
        """첫 답이면 inbox·done 에 기록하고 감시기 카드 등록을 꺼냄(card-actions). 늦은 답은 기록 없이 Late.
        기록은 잠금 안, 등록 꺼내기는 잠금 밖(card_actions 잠금과 겹쳐 잡지 않음). busy 처리는 호출자 몫"""
        with actions.flock(self.lock):
            by, rec = self.find_answer(ans.did, ans.ts)
            if by:
                if self.retry_due(ans.did, ans.ts, rec, now):
                    self.mark(ans.did, ans.ts, PENDING, None, now, retry=True)
                    return Claim("redeliver", by, rec=rec)
                return Claim("late", by)
            rec = {"type": "button", "parent_ts": ans.ts, "channel": ans.channel, "title": ans.title, "decision_id": ans.did,
                   "kind": "decide", "option_text": ans.label[:LABEL_MAX], "received_at": int(time.time() if now is None else now),
                   **ans.extra, "source": ans.source}
            self.append(self.inbox, json.dumps(rec, ensure_ascii=False) + "\n")
            self.append(self.done, ans.ts + "\n")
            self.mark(ans.did, ans.ts, PENDING, None, now)
        return Claim("claimed", taken=actions.try_take(ans.did))

    def first_answer(self, did: str, ts: str) -> str:
        """이 카드(ts)에 이미 인정된 답이 있으면 그 입구(slack|docsite), 없으면 빈 문자열"""
        return self.find_answer(did, ts)[0]

    def find_answer(self, did: str, ts: str) -> tuple:
        """(입구, 근거 inbox 줄). 판정은 그 카드(parent_ts == ts)로만 좁힘 — 같은 번호로 다시 올린 카드는 별개.
        규칙은 judge 참고. 결정 완료 판정은 이 한 곳(확인 목록·할 일 목록·옆 패널 포함)"""
        return self.judge(self.read_inbox(), self.read_done(), did, ts)

    @staticmethod
    def judge(inbox: list, done: set, did: str, ts: str) -> tuple:
        """카드 한 장의 답 판정. 버튼 답은 그 자체로 확정(충돌 카드를 버튼으로 정리할 수 있음). 그다음 done 에 ts 가 있으면(사용자가 직접 닫음) 답한 것으로 봄.
        그 밖에 반응·스레드의 pick/approve 가 한 가지면 답, 서로 다르거나 conflict 표시가 있으면 충돌이라 답 없음.
        source 가 없는 옛 기록은 Slack 에서 온 것"""
        recs = [d for d in inbox if d.get("decision_id") == did and d.get("kind") == "decide" and d.get("parent_ts") == ts]
        src = lambda d: d.get("source") if d.get("source") in SOURCE_NAME else SLACK  # noqa: E731
        for d in recs:
            if d.get("type") == "button":
                return src(d), d
        if ts and ts in done:
            return SLACK, {}
        votes = [d for d in recs if d.get("type") in ("reaction", "thread_reply") and d.get("action") in ANSWER_KINDS]
        if not votes or any(d.get("conflict") is True for d in votes) or len({str(d.get("pick")) for d in votes}) > 1:
            return "", {}
        return src(votes[0]), votes[0]

    def cards(self, did: str = "") -> list:
        """번호 카드 기록(slack-sent.jsonl) 전부, 오래된 순. did 를 주면 그 번호만. 같은 번호로 다시 올린 카드는 각각 한 건.
        각 건은 sent 줄에 by(답한 입구, 없으면 '')를 더한 사본. 파일을 한 번씩만 읽음"""
        inbox, done, out = self.read_inbox(), self.read_done(), []
        try:
            with open(self.sent, encoding="utf-8") as f:
                for line in f:
                    try:
                        s = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(s, dict) and s.get("form_kind") == "decide" and isinstance(s.get("ts"), str) and "options" in s \
                            and re.fullmatch(r"D-[0-9A-Za-z]{1,16}", str(s.get("decision_id") or "")) and (not did or s["decision_id"] == did):
                        out.append({**s, "by": self.judge(inbox, done, s["decision_id"], s["ts"])[0]})
        except OSError:
            return []
        return out

    def closed_by_did(self) -> dict:
        """결정 번호 → 그 번호의 카드가 전부 답으로 닫혔는가. 카드가 하나라도 열려 있으면 False"""
        out = {}
        for c in self.cards():
            out[c["decision_id"]] = out.get(c["decision_id"], True) and bool(c["by"])
        return out

    # ---- 전달 기록 ----
    def mark(self, did: str, ts: str, state: str, ok, now: float | None = None, retry: bool = False) -> None:
        rec = {"decision_id": did, "parent_ts": ts, "state": state, "ok": ok, "at": int(time.time() if now is None else now)}
        if retry:
            rec["retry"] = True
        self.append(self.delivery, json.dumps(rec, ensure_ascii=False) + "\n")

    def record_delivery(self, did: str, ts: str, state: str, ok: bool) -> None:
        """전달 결과(hub|main|proxy|unknown|lost|action) 한 줄. 기록 실패는 답 처리를 막지 않음"""
        try:
            self.mark(did, ts, state, ok)
        except OSError:
            pass

    def deliveries(self, did: str, ts: str) -> list:
        out = []
        try:
            with open(self.delivery, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and d.get("decision_id") == did and d.get("parent_ts") == ts:
                        out.append(d)
        except OSError:
            pass
        return out

    def retry_due(self, did: str, ts: str, rec: dict, now: float | None) -> bool:
        """다시 전달할 답인가: 이 입구가 기록한 버튼 답이고, 전달 줄이 pending 뿐이며 오래됐고, 아직 재전달한 적 없음.
        전달 줄이 아예 없는 옛 답은 전달 여부를 모르므로 다시 보내지 않음"""
        if rec.get("type") != "button" or rec.get("source") not in SOURCE_NAME:
            return False
        rows = self.deliveries(did, ts)
        if not rows or any(r.get("state") != PENDING or r.get("retry") for r in rows):
            return False
        return (time.time() if now is None else now) - rows[-1].get("at", 0) >= PENDING_TTL_S

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

    def find_card(self, did: str, ts: str = "") -> dict | None:
        """결정 번호의 번호 카드 기록(slack-sent.jsonl: ts·channel·title·options·tag). ts 를 주면 그 카드, 없으면 가장 최근 카드. 없으면 None.
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
                            and isinstance(s.get("ts"), str) and isinstance(s.get("options"), dict) \
                            and (not ts or s["ts"] == ts):
                        found = s
        except OSError:
            return None
        return found
