#!/usr/bin/env python3
"""Slack Socket Mode 수신기: 봇 DM 메시지·반응 이벤트가 오면 slack_poll.sh --once 를 바로 실행.

수집·중복 방지·inbox 기록은 slack_poll.sh 가 그대로 맡음. 이 스크립트는 '언제 돌릴지'만 바꿈.
수신 본문은 데이터로만 다루고 실행·eval 하지 않음. 토큰은 slack-bot.env 에서 읽고 출력하지 않음.

연결 감시: 끊김이 DOWN 임계 이상 이어지면 새 클라이언트로 재연결하고 한 번 수집함. 재연결이
연속 5회 실패하면 종료 코드 3 으로 끝내 launchd KeepAlive 가 새로 시작하게 함. 연결이 살아 있어도
REFRESH 주기마다 계획 재연결(반쯤 죽은 연결 방지). ALIVE 주기마다 생존 한 줄을 남김.

사용: slack_socket.py            (launchd ai.lrm.slacksocket 가 상주 실행)
환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs)
      LRM_SOCKET_CHECK_S 감시 간격(30) · LRM_SOCKET_DOWN_S 끊김 판정(60) ·
      LRM_SOCKET_REFRESH_S 정기 재연결 주기(21600, 0 이면 끔) · LRM_SOCKET_ALIVE_S 생존 기록 주기(600) ·
      LRM_SOCKET_DEBOUNCE_S 수집 묶음 대기(2) · LRM_SOCKET_RETIRE_TIMEOUT_S 옛 클라이언트 runner 종료 대기 상한(5)
      LRM_SOCKET_WATCH_S 감시기(재시작 감지 등) 실행 주기(120, 0 이면 끔)
종료 코드: 2 설정 오류(토큰 없음)·첫 연결 실패, 3 재연결 연속 실패(launchd 가 재시작). 0 은 없음 — 신호(SIGTERM)로만 끝남
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")

from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 링크·-I 실행에서도 같은 폴더의 공용 모듈(card_actions·main_terminal·ledger_sync)을 찾음
import answer_route  # noqa: E402
import main_inbox  # noqa: E402
import answer_store  # noqa: E402
import board_slack  # noqa: E402
import ask_open  # noqa: E402
import card_actions as actions  # noqa: E402
import ledger  # noqa: E402
import ledger_sync  # noqa: E402
import main_terminal as mt  # noqa: E402

DIR = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
POLL = os.path.join(DIR, "slack_poll.sh")
MAX_FAILS = 5  # 재연결 연속 실패 상한. 넘으면 EXIT_RESTART 로 끝냄
EXIT_RESTART = 3
STORE = answer_store.AnswerStore(DIR)  # 결정 답 기록·중복 판정은 문서 사이트와 공용
# launchd 기본 PATH(/usr/bin:/bin)에는 orca 가 없어 전달이 실패했음 → PATH 밖 설치 위치도 찾음
ORCA = mt.find_orca()
NOTIFY = os.environ.get("LRM_NOTIFY", os.path.join(DIR, "slack_notify.sh"))
BLOCK_RE = re.compile(r"^lrm_decide:([A-Za-z0-9-]{1,32})$")
MODAL_ID = "lrm_custom"
CUSTOM_MAX = 500  # 직접 입력 글자 상한
REPLY_LOOKUP = 5  # 스레드에서 카드 하나를 찾을 때 받을 글 수(부모 글이 함께 오므로 1보다 커야 함)
ASK_MARK = "💬 질문 받음"
ASK_LINE = ASK_MARK + " ({at}) — 답을 받은 뒤 버튼으로 고르세요"


def card_title(message: dict) -> str:
    """카드 제목(색 띠 section 첫 줄, 없으면 text). 굵게 표시 '*' 제거"""
    for att in message.get("attachments") or []:
        for b in att.get("blocks") or []:
            if b.get("type") == "section":
                return b["text"]["text"].split("\n", 1)[0].replace("*", "").strip()
    return (message.get("text") or "").split("\n", 1)[0].strip()


def answered(message: dict, line: str) -> list:
    """버튼 블록을 '선택됨' 한 줄로 바꾼 attachments. 본문 section 은 유지"""
    out = []
    for att in message.get("attachments") or []:
        att = dict(att)
        if att.get("blocks"):
            att["blocks"] = [b for b in att["blocks"] if b.get("type") != "actions"] + \
                [{"type": "context", "elements": [{"type": "mrkdwn", "text": line}]}]
        out.append(att)
    return out


def noted(message: dict, line: str) -> list:
    """버튼은 두고 안내 한 줄만 끼운 attachments. 같은 머리('💬')의 앞선 안내는 갈아끼워 질문이 여러 번 와도 한 줄만 남김"""
    out = []
    for att in message.get("attachments") or []:
        att = dict(att)
        if att.get("blocks"):
            keep = [b for b in att["blocks"] if not (b.get("type") == "context" and any(str(e.get("text", "")).startswith(ASK_MARK) for e in b.get("elements") or []))]
            att["blocks"] = keep + [{"type": "context", "elements": [{"type": "mrkdwn", "text": line}]}]
        out.append(att)
    return out


def send_main(text: str, kind: str = "answer") -> bool:
    """메인 받은 편지함(main.jsonl)에 한 줄 남김. 터미널 입력창에는 쓰지 않음(사용자 입력과 섞임). 쓰기 실패는 False.
    kind: answer(결정 답) | question(직접 입력 질문 — 답과 구분)"""
    ok = main_inbox.sender("slack_socket", kind, hub=DIR)(text)
    if not ok:
        log("메인 받은 편지함 쓰기 실패 — hub-inbox 폴더 권한·디스크 확인")
    return ok


def send_question(text: str) -> bool:
    return send_main(text, "question")


def note_thread(kind: str, thread: str, text: str) -> None:
    """카드 스레드에 알림. 실패해도 로그 한 줄만"""
    try:
        subprocess.run([NOTIFY, "--kind", kind, "--thread", thread, text], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as err:
        log(f"Slack 알림 실패 {type(err).__name__}")


def warn_slack(thread: str, text: str) -> None:
    """메인 전달 실패를 카드 스레드에 경고"""
    note_thread("warn", thread, text)


class Decide:
    """결정 카드 버튼·직접 입력 처리. 수신 값은 데이터로만 씀(실행 안 함)"""
    workers: list = []  # 전달 작업 스레드(시험이 끝을 기다리는 용도)

    def __init__(self, web, me: str) -> None:
        self.web, self.me = web, me
        self.router = answer_route.Router(STORE, send_main)
        self.asker = answer_route.Router(STORE, send_question)  # 질문 전용: 메인 기록 kind 가 question

    def handle(self, payload: dict) -> None:
        kind = payload.get("type")
        if self.me and (payload.get("user") or {}).get("id") != self.me:
            return
        if kind == "block_actions":
            self.on_click(payload)
        elif kind == "view_submission" and (payload.get("view") or {}).get("callback_id") == MODAL_ID:
            self.on_submit(payload)

    def on_click(self, p: dict) -> None:
        act = (p.get("actions") or [{}])[0]
        m = BLOCK_RE.match(act.get("block_id", ""))
        if not m:
            return
        did, msg = m.group(1), p.get("message") or {}
        ch, ts = (p.get("channel") or {}).get("id", ""), msg.get("ts", "")
        if act.get("action_id") == "custom":
            meta = {"did": did, "channel": ch, "ts": ts}
            if msg.get("thread_ts") and msg["thread_ts"] != ts:  # 작업 스레드 안의 카드. 제출 때 카드 원문을 그 스레드에서 읽어야 함
                meta["thread_ts"] = msg["thread_ts"]
            meta = json.dumps(meta)
            self.web.views_open(trigger_id=p["trigger_id"], view={
                "type": "modal", "callback_id": MODAL_ID, "private_metadata": meta,
                "title": {"type": "plain_text", "text": "직접 입력"}, "submit": {"type": "plain_text", "text": "보내기"},
                "blocks": [{"type": "input", "block_id": "answer", "label": {"type": "plain_text", "text": card_title(msg)[:150] or did},
                            "element": {"type": "plain_text_input", "action_id": "text", "multiline": True, "max_length": CUSTOM_MAX}}]})
            return
        pick = (act.get("value") or "").split("|", 1)[-1]
        label = (act.get("text") or {}).get("text", pick)
        self.finish(did, ch, ts, msg, label, {"action": "pick", "pick": int(pick) if pick.isdigit() else pick})

    def on_submit(self, p: dict) -> None:
        view = p["view"]
        meta = json.loads(view.get("private_metadata") or "{}")
        text = (((view.get("state") or {}).get("values") or {}).get("answer") or {}).get("text", {}).get("value") or ""
        text = " ".join(text.split())[:CUSTOM_MAX]
        if not text:
            return
        msg = self.card_message(meta["channel"], meta["ts"], meta.get("thread_ts") or "")
        if answer_route.is_question(text) and not STORE.first_answer(meta["did"], meta["ts"]):
            # 질문은 답이 아님: 기록(claim)·카드 닫기 없이 버튼 유지. 답글 자리는 카드가 든 스레드, 맨 위 카드면 카드 자신
            self.ask(meta["did"], meta["channel"], meta["ts"], msg, text, meta.get("thread_ts") or meta["ts"])
            return
        self.finish(meta["did"], meta["channel"], meta["ts"], msg, "직접 입력: " + text, {"action": "custom", "text": text})

    def card_message(self, ch: str, ts: str, thread_ts: str) -> dict:
        """카드 원문. 맨 위 글은 채널 이력에서, 스레드 안 카드는 그 스레드의 답글에서 읽음(채널 이력에는 스레드 답글이 안 나옴). 못 찾으면 ts 만 든 dict"""
        if thread_ts:
            found = self.web.conversations_replies(channel=ch, ts=thread_ts, oldest=ts, latest=ts, inclusive=True, limit=REPLY_LOOKUP)
        else:
            found = self.web.conversations_history(channel=ch, latest=ts, inclusive=True, limit=1)
        return next((x for x in found.get("messages", []) if x.get("ts") == ts), {"ts": ts})

    def update_card(self, ch: str, ts: str, msg: dict, fallback: str, attachments: list, on_fail: str) -> None:
        """카드를 고쳐 씀. 원문을 못 읽은 카드(본문 블록 없음)는 건드리지 않음: 빈 내용으로 덮어쓰면 카드 본문이 지워짐"""
        if not msg.get("attachments"):
            log(f"카드 원문을 못 읽음 — 카드는 그대로 둠({on_fail})")
            return
        try:
            self.web.chat_update(channel=ch, ts=ts, text=card_title(msg) or fallback, attachments=attachments)
        except Exception as err:
            log(f"카드 갱신 실패 {type(err).__name__} — {on_fail}")

    def ask(self, did: str, ch: str, ts: str, msg: dict, text: str, thread: str) -> None:
        """질문: 카드에 안내 한 줄만 달고 원문을 허브·메인에 전달(답 경로와 같은 라우터, kind=question). thread 는 답글이 올라갈 스레드 ts"""
        title = card_title(msg)
        self.update_card(ch, ts, msg, did, noted(msg, ASK_LINE.format(at=time.strftime("%m-%d %H:%M"))), "질문은 전달함")
        try:
            ask_open.note(DIR, thread, did, ts)
        except OSError as err:  # 기록이 없으면 답글에 버튼 안내 줄만 빠짐. 질문 전달은 계속
            log(f"결정 {did} 질문 기록 실패 {type(err).__name__} — 답글에 버튼 안내가 안 붙음")
        self.start(self.send_ask, did, ts, title, text, thread)

    def send_ask(self, did: str, ts: str, title: str, text: str, thread: str) -> None:
        asked = answer_route.asked_text(did, text, thread)
        if board_slack.card_for(did):  # 작업판 결정 카드: 질문은 카드 코멘트로만(카드는 열어 둠), 허브·메인 전달 없음
            self.to_board(did, ts, title, asked, keep_open=True)
            return
        try:
            out = self.asker.ask(did, ts, answer_store.SLACK, asked)
        except Exception as err:
            log(f"결정 {did} 질문 전달 예외 {type(err).__name__}")
            warn_slack(ts, f"질문 전달 예외: {title or did}\n\n• 무엇: 질문을 허브·메인에 못 보냄({type(err).__name__})\n• 결과: 메인에 직접 알려 주세요")
            return
        if not out.ok:
            warn_slack(ts, f"질문 전달 실패: {title or did}\n\n• 무엇: {out.note}\n• 결과: 메인에 직접 알려 주세요")
        log(f"결정 {did} 질문 받음(답 아님) · {out.result()}")

    def finish(self, did: str, ch: str, ts: str, msg: dict, label: str, extra: dict) -> None:
        """답 기록(먼저 온 답만) → 카드 닫기 → 메인 전달. 늦은 답이면 카드에 사유만 남기고 아무것도 실행하지 않음"""
        at = time.strftime("%m-%d %H:%M")
        title = card_title(msg)
        claim = STORE.claim(answer_store.Answer(did, ts, ch, title, label, answer_store.SLACK, extra))
        if claim.redeliver:  # 먼저 온 답이 전달 전에 끊긴 경우: 그 답 원문을 1회 다시 전달. 이번 클릭 자체는 반영하지 않음
            line = f"ℹ️ 이전 답의 전달 기록이 없어 다시 전달함 — 이번 선택은 반영하지 않음 ({at})"
        else:
            line = f"ℹ️ {claim.late_text()} — 이번 선택은 반영하지 않음 ({at})" if claim.late else f"✅ {label} 선택됨 ({at})"
        self.update_card(ch, ts, msg, did, answered(msg, line), "버튼은 남음")
        if claim.redeliver:
            extra0, label0 = claim.replay()
            log(f"결정 {did} 전달 기록 없는 이전 답 — 원문 다시 전달")
            self.start(self.send_out, did, ts, title, label0, extra0, claim.by)
            return
        if claim.late:
            log(f"결정 {did} 버튼 답 무시 — {claim.late_text()}")
            return
        status, entry = claim.taken  # 비차단: 다른 프로세스가 등록부 잠금을 쥐어도 수신 스레드는 멈추지 않음
        if status == "busy":  # 등록 여부 불명(처리 중). 일반 카드로 오인하지 않고 별도 스레드가 잠금을 기다려 이어서 처리
            log(f"결정 {did} 등록부 잠금 사용 중 — 백그라운드에서 이어서 처리")
            threading.Thread(target=self.settle, args=(did, ts, title, label, extra), daemon=True).start()
            return
        self.route(entry, did, ts, title, label, extra)

    def settle(self, did: str, ts: str, title: str, label: str, extra: dict) -> None:
        """잠금을 기다려 take 한 뒤 route. 답은 inbox 에 이미 기록돼 있어 프로세스가 죽어도 사라지지 않음"""
        self.route(actions.take(did), did, ts, title, label, extra)

    def route(self, entry, did: str, ts: str, title: str, label: str, extra: dict) -> None:
        """감시기가 건 카드(entry 있음)는 후속 동작만 실행하고 메인에는 전달하지 않음(메인이 같은 일을 또 하지 않게).
        일반 카드는 결정을 올린 허브에 직접 전달하고 메인에는 참고 한 줄(허브 기록이 없으면 메인으로 — answer_route)"""
        if entry is not None:
            STORE.record_delivery(did, ts, "action", True)
            threading.Thread(target=self.run_action, args=(entry, did, ts, extra.get("pick")), daemon=True).start()
            return
        if board_slack.card_for(did):  # 작업판에서 보낸 결정: 답은 그 카드에만(board-answer), 허브·메인 받은 편지함 거치지 않음
            self.start(self.to_board, did, ts, title, answer_route.said_text(did, extra, label))
            return
        self.start(self.send_out, did, ts, title, label, extra)

    def to_board(self, did: str, ts: str, title: str, text: str, keep_open: bool = False) -> None:
        """작업판 결정 카드에 원문 기록. 실패하면 카드 스레드에 경고하고 옛 경로(허브·메인)로 넘기지 않음 — 작업판이 정본"""
        try:
            out = board_slack.answer(did, text, keep_open=keep_open)
        except board_slack.cards.Fail as err:
            out, why = None, str(err)[:120]
        else:
            why = "" if out else "카드 조회 실패"
        ok = bool(out)
        if not keep_open:
            STORE.record_delivery(did, ts, "board", ok)
        if not ok:
            warn_slack(ts, f"작업판 기록 실패: {title or did}\n\n• 무엇: 답은 받았지만 작업판 카드에 못 남김({why})\n• 결과: board-answer 로 직접 남겨 주세요")
        log(f"결정 {did} {'질문' if keep_open else '버튼 답'} → 작업판 카드 {'기록' if ok else '기록 실패'}")

    @staticmethod
    def start(fn, *args) -> None:
        """허브 전송은 오래 걸릴 수 있어 버튼 처리 스레드 밖에서 실행(다른 버튼 처리를 막지 않음)"""
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()
        Decide.workers.append(t)
        del Decide.workers[:-64]

    def send_out(self, did: str, ts: str, title: str, label: str, extra: dict, source: str = answer_store.SLACK) -> None:
        try:
            out = self.router.route(did, ts, source, answer_route.said_text(did, extra, label),
                                    on_done=lambda o: self.report(did, ts, title, label, o))
        except Exception as err:  # 전송 예외가 스레드를 조용히 죽이지 않게 종류만 남김(본문·traceback 제외). pending 은 늦은 클릭의 재전달이 복구
            log(f"결정 {did} 전달 예외 {type(err).__name__} — 답은 기록됨, 재전달 대기")
            warn_slack(ts, f"전달 예외: {title or did}\n\n• 무엇: 답은 기록했지만 전달 중 오류({type(err).__name__})\n• 결과: 카드를 다시 누르면 재전달함. 급하면 메인에 직접 알려 주세요")
            return
        if out.via == "pending":  # 허브 창 입력 막힘 — 백그라운드 재시도 후 on_done 이 보고
            log(f"결정 {did} 허브 {out.hub} 창 입력 막힘 — 재시도 중(pending)")
            return
        self.report(did, ts, title, label, out)

    def report(self, did: str, ts: str, title: str, label: str, out) -> None:
        if not out.ok:
            where = "메인 받은 편지함" if out.via == "main" else f"{out.hub} 허브·메인 받은 편지함"
            warn_slack(ts, f"{'메인' if out.via == 'main' else '허브'} 전달 실패: {title or did} = {label}\n\n• 무엇: 버튼 답은 기록했지만 {where}로 못 보냄 ({out.note})\n• 결과: 메인에 직접 알려 주세요")
        log(f"결정 {did} 버튼 답 기록 · {out.result()}")

    def run_action(self, entry: dict, did: str, ts: str, pick) -> None:
        """카드 후속 동작 실행(오래 걸릴 수 있어 별도 스레드). 실행했으면 결과를 스레드에 남김"""
        rc, msg = actions.execute(entry, pick)
        log(f"결정 {did} 후속 동작 rc={rc} {msg[:120]}")
        if pick == actions.RUN_PICK:
            note_thread("done" if rc == 0 else "warn", ts, f"{'후속 동작 완료' if rc == 0 else '후속 동작 실패'}\n\n• 결과: {msg[:200]}")


def env_secs(name: str, default: float, allow_zero: bool = False) -> float:
    """환경 변수 초 단위 값. 잘못된 값·음수는 기본값, 0 은 allow_zero 일 때만 허용"""
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 or (allow_zero and value == 0) else default


# 옛 클라이언트 runner 종료 대기 상한. SDK IntervalRunner.shutdown() 의 join 은 상한이 없어, 수신 스레드가 새 세션에
# 들어가는 순간과 겹치면 영구 대기하고 감시 루프가 멈춤(프로세스는 살아 있어 launchd 도 재시작 안 함)
RETIRE_TIMEOUT_S = env_secs("LRM_SOCKET_RETIRE_TIMEOUT_S", 5.0)


@dataclass(frozen=True)
class Timing:
    """감시 주기 한 곳 모음. 값 근거는 hubs/docs/install-orca-main.md Socket Mode 절"""
    check_s: float  # 감시 간격. SDK 가 5초마다 ping 하므로 30초면 일시 끊김 대부분은 스스로 복구된 뒤 확인
    down_s: float  # 이 시간 이상 끊겨 있으면 직접 재연결. SDK 자체 복구가 먼저 일할 여유
    refresh_s: float  # 정기 재연결 주기. 0 이면 끔
    alive_s: float  # 생존 기록 주기
    debounce_s: float  # 연속 이벤트(메시지+반응)를 한 번의 수집으로 묶음

    @classmethod
    def from_env(cls) -> "Timing":
        return cls(
            check_s=env_secs("LRM_SOCKET_CHECK_S", 30.0),
            down_s=env_secs("LRM_SOCKET_DOWN_S", 60.0),
            refresh_s=env_secs("LRM_SOCKET_REFRESH_S", 6 * 3600.0, allow_zero=True),
            alive_s=env_secs("LRM_SOCKET_ALIVE_S", 600.0),
            debounce_s=env_secs("LRM_SOCKET_DEBOUNCE_S", 2.0),
        )


def load_env(path: str) -> dict:
    values = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.removeprefix("export ").strip()
            values[key] = value.strip().strip('"').strip("'")
    return values


# 주기 감시기(새 launchd 를 늘리지 않고 이 상주 프로세스의 감시 루프에 얹음). 각 스크립트의 `check` 를 부름
WATCH_S = env_secs("LRM_SOCKET_WATCH_S", 120.0, allow_zero=True)
WATCH_TIMEOUT_S = 120
WATCHERS = ("restart_watch.py", "stall_watch.py", "coord_watch.py")
_watch_lock = threading.Lock()


def run_watchers() -> None:
    """감시기를 순서대로 실행. 이전 실행이 안 끝났으면 건너뜀. 출력은 첫 줄만 로그에 남김"""
    if not _watch_lock.acquire(blocking=False):
        return
    try:
        for name in WATCHERS:
            path = os.path.join(os.path.dirname(os.path.realpath(__file__)), name)
            try:
                p = subprocess.run([sys.executable, "-I", path, "check"], capture_output=True, text=True, timeout=WATCH_TIMEOUT_S, stdin=subprocess.DEVNULL)
                log(f"감시 {name} rc={p.returncode} {(p.stdout.strip().splitlines() or [''])[0][:100]}")
            except (OSError, subprocess.TimeoutExpired) as err:
                log(f"감시 {name} 실패 {type(err).__name__}")
    finally:
        _watch_lock.release()


def log(msg: str) -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')} [socket] {msg}", flush=True)


class Trigger:
    def __init__(self, debounce_s: float) -> None:
        self._debounce_s = debounce_s
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def fire(self, reason: str) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._run, args=(reason,))
            self._timer.daemon = True
            self._timer.start()

    def _run(self, reason: str) -> None:
        try:
            out = subprocess.run(
                ["/bin/bash", POLL, "--once", "--json"],
                capture_output=True, text=True, timeout=300,
            )
            log(f"{reason} → 수집 rc={out.returncode} {out.stdout.strip()[:120]}")
        except Exception as err:  # 수집 실패는 다음 이벤트·launchd 주기 수집이 메움
            log(f"{reason} → 수집 실패 {type(err).__name__}")


class LedgerTick:
    """피드백 장부 → Slack 진행 스레드 동기화를 감시 회차마다 호출.
    장부는 파일이라 Slack 이벤트가 없음. 이미 토큰을 가진 이 프로세스의 감시 루프(30초)가 해시만 비교하고,
    바뀐 회차에만 Slack 을 부름(ledger_sync.sync). 429 대기가 감시 루프를 막지 않게 스레드로 돌리고 동시 실행은 1개로 제한"""

    def __init__(self, web, me: str) -> None:
        self.api, self.me = ledger_sync.SlackApi(web), me
        self._busy = threading.Lock()
        self._last_error = ""
        self._last_logged: tuple = ()

    def tick(self) -> None:
        conf = ledger_sync.Conf()
        if not os.path.exists(conf.ledger) or not self._busy.acquire(blocking=False):
            return  # 장부 없음(빈 부모 방지)·이전 회차 진행 중
        try:
            threading.Thread(target=self._run, args=(conf,), daemon=True).start()
        except Exception:  # 스레드 생성 실패(자원 부족)면 잠금을 돌려 다음 회차가 이어가게 하고 감시 루프는 살림
            self._busy.release()
            log("장부 동기화 스레드 시작 실패")

    def _run(self, conf) -> None:
        try:
            conf.user_id = self.me
            res = ledger_sync.sync(conf, self.api)
            self._last_error = ""
            if res["status"] == "unchanged":
                self._last_logged = ()  # 정상 복귀 뒤 같은 실패가 다시 나면 새로 남김
            if res["status"] in ("unchanged", "busy"):  # busy = CLI 등 다른 프로세스가 동기화 중
                return
            if res["status"] == "deferred" and not res["calls"]:
                return  # 대기 중 회차는 호출 없이 건너뜀. 대기를 건 실패·429 회차에서 이미 기록함
            key = (res["status"], res.get("error", ""), res.get("wait", 0))
            if key == self._last_logged:
                return  # 같은 실패 반복은 첫 1회와 대기 변경 때만 남김
            self._last_logged = key
            wait = f" 다음 재시도 {res['wait']}초 뒤" if res.get("wait") else ""
            log(f"장부 동기화 {res['status']} 호출={res['calls']} 답글={res['replies']} {res.get('error', '')}{wait}".rstrip())
        except ledger.LedgerError as err:  # 장부 내용 오류는 원인·조치 문장이라 그대로 남김. 같은 문장은 한 번만
            if str(err) != self._last_error:
                self._last_error = str(err)
                log(f"장부 동기화 중단: {err}")
        except Exception as err:  # 장부 쪽 오류가 수신기를 죽이지 않게 한 줄만 남김
            log(f"장부 동기화 실패 {type(err).__name__}")
        finally:
            self._busy.release()


class DocsiteCards:
    """문서 사이트에서 답한 결정의 Slack 카드를 닫음(버튼 제거 + '✅ 선택됨 — 문서 사이트'). 문서 사이트 서버는 토큰이 없어
    이 프로세스가 감시 회차마다 inbox 의 문서 사이트 답을 보고 chat.update 함. 닫은 카드는 docsite-card-closed.txt 에 기록해 한 번만 함.
    버튼이 이미 없는 카드(다른 경로로 갱신됨)는 갱신 없이 기록만. 실패는 종류만 로그하고 다음 회차에 재시도(카드당 최대 TRIES 회)"""
    MARK_NAME, PER_TICK, TRIES = "docsite-card-closed.txt", 3, 3

    def __init__(self, web) -> None:
        self.web = web
        self.mark = os.path.join(DIR, self.MARK_NAME)
        self._busy = threading.Lock()
        self._fails: dict = {}

    def pending(self) -> list:
        try:
            with open(self.mark, encoding="utf-8") as f:
                closed = set(f.read().split())
        except OSError:
            closed = set()
        out = {}
        for d in STORE.read_inbox():
            ts = d.get("parent_ts")
            if d.get("type") == "button" and d.get("kind") == "decide" and d.get("source") == answer_store.DOCSITE and isinstance(ts, str) and ts \
                    and ts not in closed and self._fails.get(ts, 0) < self.TRIES and isinstance(d.get("channel"), str) and d["channel"]:
                out.setdefault(ts, d)
        return list(out.values())[: self.PER_TICK]

    def tick(self) -> None:
        if not self._busy.acquire(blocking=False):
            return
        try:
            todo = self.pending()
            if not todo:
                self._busy.release()
                return
            threading.Thread(target=self._run, args=(todo,), daemon=True).start()
        except Exception:  # 스레드 생성 실패면 잠금을 돌려 다음 회차가 이어가게 함
            self._busy.release()
            log("문서 사이트 카드 닫기 스레드 시작 실패")

    def _run(self, todo: list) -> None:
        try:
            for d in todo:
                self.close(d)
        finally:
            self._busy.release()

    def close(self, d: dict) -> None:
        ts, ch = d["parent_ts"], d["channel"]
        try:
            hist = self.web.conversations_history(channel=ch, latest=ts, inclusive=True, limit=1)
            msg = next((x for x in hist.get("messages", []) if x.get("ts") == ts), None)
            if msg is None:
                raise LookupError("card not found")
            if any(b.get("type") == "actions" for a in msg.get("attachments") or [] for b in a.get("blocks") or []):
                at = time.strftime("%m-%d %H:%M", time.localtime(d.get("received_at") or time.time()))
                line = f"✅ {d.get('option_text') or '답함'} 선택됨 — 문서 사이트 ({at})"
                self.web.chat_update(channel=ch, ts=ts, text=card_title(msg) or d.get("title") or d.get("decision_id", ""), attachments=answered(msg, line))
            answer_store.AnswerStore.append(self.mark, ts + "\n")
            log(f"결정 {d.get('decision_id')} 문서 사이트 답 — Slack 카드 닫음")
        except Exception as err:  # 본문·토큰 없이 종류만
            self._fails[ts] = self._fails.get(ts, 0) + 1
            log(f"문서 사이트 답 카드 닫기 실패 {type(err).__name__} {self._fails[ts]}/{self.TRIES}")


class Pulse:
    """수신·재연결 기록. 마지막 이벤트가 없어도 정상(사용자가 안 쓰는 동안)이라 끊김 판정에는 쓰지 않음"""

    def __init__(self) -> None:
        self.last_event: float | None = None
        self.reconnects = 0

    def touch(self) -> None:
        self.last_event = time.monotonic()

    def idle_text(self, now: float) -> str:
        if self.last_event is None:
            return "없음"
        return f"{int((now - self.last_event) // 60)}분 전"


def is_up(client) -> bool:
    try:
        return bool(client.is_connected())
    except Exception:
        return False


def connect_checked(client) -> None:
    """연결 뒤 실제로 붙었는지 확인. SDK connect() 는 웹소켓 단계 실패를 예외 없이 넘겨 is_connected 로만 알 수 있음.
    connect() 는 핸드셰이크 응답까지 동기로 읽고 돌아오므로(builtin/connection.py) 따로 기다리지 않음"""
    client.connect()
    if not is_up(client):
        raise ConnectionError("연결 안 됨")


def quiet(step: str, fn) -> None:
    try:
        fn()
    except Exception as err:  # 정리 실패는 다음 단계를 막지 않음. 예외 본문은 남기지 않음
        log(f"정리 {step} 실패 {type(err).__name__}")


def end_runner(client) -> None:
    """SDK close() 가 남기는 current_session_runner 스레드 정리. 속성이 없는 SDK 버전은 건너뜀"""
    state = getattr(client, "current_session_state", None)
    if state is not None:
        state.terminated = True  # runner 는 이 플래그로만 수신 루프를 벗어남. 없으면 shutdown 의 join 이 영원히 막힘
    runner = getattr(client, "current_session_runner", None)
    if runner is not None:
        runner.shutdown()


def bounded(step: str, fn, limit_s: float) -> None:
    """fn 을 데몬 스레드에서 돌리고 limit_s 까지만 기다림. 넘기면 스레드 1개 누수를 감수하고 진행(감시 루프 정지가 더 나쁨)"""
    worker = threading.Thread(target=lambda: quiet(step, fn), daemon=True)
    worker.start()
    worker.join(limit_s)
    if worker.is_alive():
        log(f"정리 {step} 시간 초과 {limit_s:g}초")


def retire(client) -> None:
    """클라이언트 정리: close → disconnect → runner 종료. close() 는 되돌릴 수 없어 재사용하지 않음.
    disconnect 재호출은 close() 가 monitor 를 join 한 뒤라 monitor 가 늦게 만든 연결까지 닫음"""
    quiet("close", lambda: client.close())
    quiet("disconnect", lambda: client.disconnect())
    bounded("runner", lambda: end_runner(client), RETIRE_TIMEOUT_S)


def swap(old, make):
    """새 클라이언트를 먼저 연결한 뒤 옛 것을 정리. 연결 안 된 새 클라이언트는 실패로 다룸"""
    fresh = make()
    try:
        connect_checked(fresh)
    except Exception:
        retire(fresh)
        raise
    retire(old)
    return fresh


def watch(client, make, trigger: Trigger, pulse: Pulse, timing: Timing, ledgers: LedgerTick | None = None, cards: DocsiteCards | None = None) -> int:
    start = time.monotonic()
    last_alive = last_refresh = last_watch = start
    down_since: float | None = None
    fails = 0
    while True:
        time.sleep(timing.check_s)
        if ledgers is not None:
            ledgers.tick()
        if cards is not None:
            cards.tick()
        now = time.monotonic()
        up = is_up(client)
        if up:
            if down_since is not None:  # SDK 자체 재연결로 복구됨
                down_since, fails = None, 0
                pulse.reconnects += 1
                log("재연결됨")
                trigger.fire("재연결")
        elif down_since is None:
            down_since = now
        elif now - down_since >= timing.down_s:
            try:
                client = swap(client, make)
            except Exception as err:  # 토큰·전체 예외 문자열은 남기지 않음
                fails += 1
                log(f"재연결 실패 {type(err).__name__} {fails}/{MAX_FAILS}")
                if fails >= MAX_FAILS:
                    log("재연결 연속 실패 — 종료 후 launchd 재시작")
                    retire(client)
                    return EXIT_RESTART
            else:
                down_since, fails, last_refresh = None, 0, now
                pulse.reconnects += 1
                log("재연결됨")
                trigger.fire("재연결")
            up = is_up(client)
        if up and down_since is None and timing.refresh_s and now - last_refresh >= timing.refresh_s:
            log("정기 재연결")
            last_refresh = now  # 실패해도 다음 주기까지 기다림. 옛 연결은 아직 살아 있음
            try:
                client = swap(client, make)
            except Exception as err:
                log(f"정기 재연결 실패 {type(err).__name__}")
            else:
                pulse.reconnects += 1
                trigger.fire("정기 재연결")
        if WATCH_S and now - last_watch >= WATCH_S:
            last_watch = now
            threading.Thread(target=run_watchers, daemon=True).start()
        if now - last_alive >= timing.alive_s:
            last_alive = now
            log(f"생존 연결={'예' if is_up(client) else '아니오'} 마지막이벤트={pulse.idle_text(now)} 재연결={pulse.reconnects}회")


def main() -> int:
    env = load_env(os.path.join(DIR, "slack-bot.env"))
    app_token, bot_token = env.get("SLACK_APP_TOKEN", ""), env.get("SLACK_BOT_TOKEN", "")
    me = env.get("SLACK_USER_ID", "")
    if not app_token.startswith("xapp-") or not bot_token:
        log("SLACK_APP_TOKEN(xapp-) 또는 SLACK_BOT_TOKEN 없음 — slack-bot.env 확인")
        return 2

    timing = Timing.from_env()
    trigger = Trigger(timing.debounce_s)
    pulse = Pulse()
    web = WebClient(token=bot_token)
    decide = Decide(web, me)
    ledgers = LedgerTick(web, me)
    cards = DocsiteCards(web)

    def on_request(c: SocketModeClient, req: SocketModeRequest) -> None:
        pulse.touch()
        c.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type == "interactive":
            try:
                decide.handle(req.payload or {})
            except Exception as err:  # 버튼 처리 실패로 수신기가 죽지 않게 한 줄만 남김
                log(f"버튼 처리 실패 {type(err).__name__}")
            return
        if req.type != "events_api":
            return
        event = (req.payload or {}).get("event", {})
        kind = event.get("type")
        # 사용자 본인이 보낸 DM·반응만. 봇 자신의 메시지는 무시(봇 글마다 오므로 기록 안 함)
        if event.get("bot_id"):
            return
        sub = event.get("subtype") or ""
        if me and event.get("user") not in (me, None):
            log(f"무시 {kind}/{sub or '-'} — 다른 사용자")  # 수신 누락 진단용. 사용자 id·본문은 남기지 않음
            return
        if kind == "message" and event.get("channel_type") == "im":
            trigger.fire(f"DM{'(' + sub + ')' if sub else ''}{'(스레드)' if event.get('thread_ts') else ''}")
        elif kind == "reaction_added":
            trigger.fire("반응")
        else:
            log(f"무시 {kind}/{sub or '-'} channel_type={event.get('channel_type') or '-'}")

    def make() -> SocketModeClient:
        fresh = SocketModeClient(app_token=app_token)
        fresh.socket_mode_request_listeners.append(on_request)
        return fresh

    client = make()
    try:
        connect_checked(client)
    except Exception as err:  # 토큰 오류·네트워크 실패. traceback 대신 한 줄만 남김. KeepAlive 가 30초마다 다시 띄움
        log(f"연결 실패 {type(err).__name__} — SLACK_APP_TOKEN·네트워크 확인")
        retire(client)
        return 2
    log("연결됨")
    trigger.fire("시작")  # 끊긴 동안 쌓인 것 한 번 수집
    return watch(client, make, trigger, pulse, timing, ledgers, cards)


if __name__ == "__main__":
    sys.exit(main())
