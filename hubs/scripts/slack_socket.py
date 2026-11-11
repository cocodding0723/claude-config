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
종료 코드: 2 설정 오류(토큰 없음)·첫 연결 실패, 3 재연결 연속 실패(launchd 가 재시작). 0 은 없음 — 신호(SIGTERM)로만 끝남
"""
import json
import os
import re
import shutil
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

DIR = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
POLL = os.path.join(DIR, "slack_poll.sh")
MAX_FAILS = 5  # 재연결 연속 실패 상한. 넘으면 EXIT_RESTART 로 끝냄
EXIT_RESTART = 3
INBOX, DONE = os.path.join(DIR, "slack-inbox.jsonl"), os.path.join(DIR, "slack-done.txt")
MAIN_ENV = os.environ.get("LRM_MAIN_ENV", os.path.join(DIR, "main.env"))
# launchd 기본 PATH(/usr/bin:/bin)에는 orca 가 없어 전달이 실패했음 → PATH 밖 설치 위치도 찾음
ORCA = os.environ.get("LRM_ORCA") or shutil.which("orca") or next(
    (p for p in ("/usr/local/bin/orca", "/Applications/Orca.app/Contents/Resources/bin/orca") if os.path.exists(p)), "orca")
NOTIFY = os.environ.get("LRM_NOTIFY", os.path.join(DIR, "slack_notify.sh"))
BLOCK_RE = re.compile(r"^lrm_decide:([A-Za-z0-9-]{1,32})$")
MODAL_ID = "lrm_custom"
CUSTOM_MAX = 500  # 직접 입력 글자 상한


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


def main_handle(path: str):
    """main.env 의 MAIN_TERMINAL 값만 읽음"""
    try:
        for ln in open(path, encoding="utf-8"):
            if ln.startswith("MAIN_TERMINAL="):
                return ln.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def send_main(text: str) -> bool:
    handle = main_handle(MAIN_ENV)
    if not handle:
        log("메인 터미널 없음 — main.env 의 MAIN_TERMINAL 확인")
        return False
    try:
        p = subprocess.run([ORCA, "terminal", "send", "--terminal", handle, "--text", text, "--enter", "--wait-submit", "20", "--json"],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def warn_slack(thread: str, text: str) -> None:
    """메인 전달 실패를 카드 스레드에 경고. 실패해도 로그 한 줄만"""
    try:
        subprocess.run([NOTIFY, "--kind", "warn", "--thread", thread, text], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as err:
        log(f"Slack 경고 실패 {type(err).__name__}")


class Decide:
    """결정 카드 버튼·직접 입력 처리. 수신 값은 데이터로만 씀(실행 안 함)"""

    def __init__(self, web, me: str) -> None:
        self.web, self.me = web, me

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
            meta = json.dumps({"did": did, "channel": ch, "ts": ts})
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
        hist = self.web.conversations_history(channel=meta["channel"], latest=meta["ts"], inclusive=True, limit=1)
        msg = next((x for x in hist.get("messages", []) if x.get("ts") == meta["ts"]), {"ts": meta["ts"]})
        self.finish(meta["did"], meta["channel"], meta["ts"], msg, "직접 입력: " + text, {"action": "custom", "text": text})

    def finish(self, did: str, ch: str, ts: str, msg: dict, label: str, extra: dict) -> None:
        """카드 닫기 → inbox·done 기록 → 메인 전달. 앞 단계가 실패해도 뒤 단계는 진행"""
        at = time.strftime("%m-%d %H:%M")
        title = card_title(msg)
        try:
            self.web.chat_update(channel=ch, ts=ts, text=title or did, attachments=answered(msg, f"✅ {label} 선택됨 ({at})"))
        except Exception as err:
            log(f"카드 갱신 실패 {type(err).__name__} — 버튼은 남음")
        rec = {"type": "button", "parent_ts": ts, "channel": ch, "title": title, "decision_id": did, "kind": "decide",
               "option_text": label[:80], "received_at": int(time.time()), **extra}
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(DONE, "a", encoding="utf-8") as f:
            f.write(ts + "\n")
        ok = send_main(f"[Slack→메인] 결정: {title or did} = {label} ({ts})")
        if not ok:
            warn_slack(ts, f"메인 전달 실패: {title or did} = {label}\n\n• 무엇: 버튼 답은 기록했지만 메인 터미널로 못 보냄\n• 결과: 메인에 직접 알려 주세요")
        log(f"결정 {did} 버튼 답 기록 · 메인 전달 {'성공' if ok else '실패'}")


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


def watch(client, make, trigger: Trigger, pulse: Pulse, timing: Timing) -> int:
    start = time.monotonic()
    last_alive = last_refresh = start
    down_since: float | None = None
    fails = 0
    while True:
        time.sleep(timing.check_s)
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
    decide = Decide(WebClient(token=bot_token), me)

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
        # 사용자 본인이 보낸 DM·반응만. 봇 자신의 메시지는 무시
        if event.get("bot_id") or (me and event.get("user") not in (me, None)):
            return
        if kind == "message" and event.get("channel_type") == "im":
            trigger.fire("DM")
        elif kind == "reaction_added":
            trigger.fire("반응")

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
    return watch(client, make, trigger, pulse, timing)


if __name__ == "__main__":
    sys.exit(main())
