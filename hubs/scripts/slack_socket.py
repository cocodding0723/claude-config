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

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 링크·-I 실행에서도 같은 폴더의 공용 모듈(card_actions·main_terminal·ledger_sync)을 찾음
import card_actions as actions  # noqa: E402
import ledger  # noqa: E402
import ledger_sync  # noqa: E402
import main_terminal as mt  # noqa: E402

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
SPEC = mt.Spec.from_env()  # 메인 판정 기준. slack_push 와 같은 환경 변수(LRM_PUSH_MAIN·LRM_PUSH_PANE)


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


def orca_run(args: list, timeout: float):
    return mt.run_orca(ORCA, args, timeout)


def send_to(handle: str, text: str) -> str:
    """전송 결과 분류. 'sent' | 'gone'(핸들 무효 오류 = 입력 전 거절, 아무 터미널에도 안 들어감) | 'unknown'(시간 초과·일반 오류. 입력됐을 수 있음)"""
    rc, d = mt.run_orca(ORCA, ["terminal", "send", "--terminal", handle, "--text", text, "--enter", "--wait-submit", "20", "--json"], 60)
    if rc == 0:
        return "sent"
    return "gone" if mt.error_code(d) in mt.HANDLE_GONE_CODES else "unknown"


def send_main(text: str) -> bool:
    """메인 터미널로 전달. 대상을 바꿔 다시 보내는 경우는 옛 대상에 입력되지 않았음이 확실할 때만:
    ① 보내기 전 확인에서 옛 대상이 메인이 아님(보낸 적 없음) ② 전송이 핸들 무효 오류로 거절됨.
    시간 초과·일반 오류는 입력됐을 수 있어 재탐색·재전송 없이 실패로 돌려줌(두 터미널에 중복 전달 방지)"""
    handle = main_handle(MAIN_ENV)
    if handle:
        _, why = mt.check_link(orca_run, SPEC, handle)
        if why and why not in mt.REDISCOVER:  # 조회 실패·쓰기 불가: 메인인지 확인 못 함. 보내지 않고 실패(재탐색도 안 함)
            log(f"메인 확인 실패({why}) — 전달 보류")
            return False
        if not why:  # 메인임을 확인함. 설정된 대상으로 보냄
            res = send_to(handle, text)
            if res == "sent":
                return True
            if res != "gone":
                log("메인 전달 실패 — 도착 여부 불명이라 대상을 바꾸지 않음")
                return False
    else:
        why = "no_main_handle"
    found = mt.rediscover(orca_run, SPEC, MAIN_ENV)
    if found.status == "current_ok":  # 다른 쪽이 이미 고침
        new = main_handle(MAIN_ENV)
        return bool(new) and new != handle and send_to(new, text) == "sent"
    if found.status != "updated":
        log(f"메인 재탐색 {found.status} 후보={found.count} (사유={why}) — set_main_terminal.sh 확인" if handle else "메인 터미널 없음 — main.env 의 MAIN_TERMINAL 확인")
        return False
    log(f"재탐색으로 갱신 {found.handle[:8]}")
    return send_to(found.handle, text) == "sent"


def note_thread(kind: str, thread: str, text: str) -> None:
    """카드 스레드에 알림. 실패해도 로그 한 줄만"""
    try:
        subprocess.run([NOTIFY, "--kind", kind, "--thread", thread, text], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as err:
        log(f"Slack 알림 실패 {type(err).__name__}")


def warn_slack(thread: str, text: str) -> None:
    """메인 전달 실패를 카드 스레드에 경고"""
    note_thread("warn", thread, text)


def said_text(did: str, extra: dict, label: str) -> str:
    """메인 전달 문장. slack_push.py 의 스레드 글·반응 해석과 같은 머리: '결정 D-0001 = 선택 2(문구) (버튼)', 직접 입력은 '결정 D-0001 답(자유 글): 원문'"""
    if extra.get("action") == "custom":
        return f"결정 {did} 답(자유 글): {extra.get('text', '')}"
    pick = extra.get("pick")
    name = re.sub(r"^\d+\.\s*", "", label)
    return f"결정 {did} = " + (f"선택 {pick}({name})" if isinstance(pick, int) else name) + " (버튼)"


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
        status, entry = actions.try_take(did)  # 비차단: 다른 프로세스가 등록부 잠금을 쥐어도 수신 스레드는 멈추지 않음
        if status == "busy":  # 등록 여부 불명(처리 중). 일반 카드로 오인하지 않고 별도 스레드가 잠금을 기다려 이어서 처리
            log(f"결정 {did} 등록부 잠금 사용 중 — 백그라운드에서 이어서 처리")
            threading.Thread(target=self.settle, args=(did, ts, title, label, extra), daemon=True).start()
            return
        self.route(entry, did, ts, title, label, extra)

    def settle(self, did: str, ts: str, title: str, label: str, extra: dict) -> None:
        """잠금을 기다려 take 한 뒤 route. 답은 inbox 에 이미 기록돼 있어 프로세스가 죽어도 사라지지 않음"""
        self.route(actions.take(did), did, ts, title, label, extra)

    def route(self, entry, did: str, ts: str, title: str, label: str, extra: dict) -> None:
        """감시기가 건 카드(entry 있음)는 후속 동작만 실행하고 메인에는 전달하지 않음(메인이 같은 일을 또 하지 않게). 일반 카드는 메인 전달(결정 번호 머리줄)"""
        if entry is not None:
            threading.Thread(target=self.run_action, args=(entry, did, ts, extra.get("pick")), daemon=True).start()
            return
        ok = send_main(f"[Slack→메인] {said_text(did, extra, label)} ({ts})")
        if not ok:
            warn_slack(ts, f"메인 전달 실패: {title or did} = {label}\n\n• 무엇: 버튼 답은 기록했지만 메인 터미널로 못 보냄\n• 결과: 메인에 직접 알려 주세요")
        log(f"결정 {did} 버튼 답 기록 · 메인 전달 {'성공' if ok else '실패'}")

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
WATCHERS = ("restart_watch.py", "stall_watch.py")
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
            if res["status"] not in ("unchanged", "busy"):  # busy = CLI 등 다른 프로세스가 동기화 중
                log(f"장부 동기화 {res['status']} 호출={res['calls']} 답글={res['replies']} {res.get('error', '')}".rstrip())
        except ledger.LedgerError as err:  # 장부 내용 오류는 원인·조치 문장이라 그대로 남김. 같은 문장은 한 번만
            if str(err) != self._last_error:
                self._last_error = str(err)
                log(f"장부 동기화 중단: {err}")
        except Exception as err:  # 장부 쪽 오류가 수신기를 죽이지 않게 한 줄만 남김
            log(f"장부 동기화 실패 {type(err).__name__}")
        finally:
            self._busy.release()


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


def watch(client, make, trigger: Trigger, pulse: Pulse, timing: Timing, ledgers: LedgerTick | None = None) -> int:
    start = time.monotonic()
    last_alive = last_refresh = last_watch = start
    down_since: float | None = None
    fails = 0
    while True:
        time.sleep(timing.check_s)
        if ledgers is not None:
            ledgers.tick()
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
    return watch(client, make, trigger, pulse, timing, ledgers)


if __name__ == "__main__":
    sys.exit(main())
