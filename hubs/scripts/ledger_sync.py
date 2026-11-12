#!/usr/bin/env python3
"""피드백 장부 → Slack 진행 스레드 동기화.

장부(ledger.py 가 관리) 내용 해시가 직전 동기화와 다를 때만 Slack 을 부름.
  (a) 진행 스레드 부모 메시지 1개를 chat.update 로 요약 갱신(상태별 개수·미완료 한 줄씩·오늘 완료)
  (b) 바뀐 항목마다 스레드에 한 줄 답글. 회차당 최대 MAX_REPLIES 개로 묶음
부모가 없으면 처음 1회 만들고 ts 를 상태 파일에 저장함(처음 만들 때는 답글 없음 — 18건 도배 방지).
부모를 만들기 전에는 DM 이력에서 기존 부모를 먼저 찾음(상태 파일 유실·저장 실패·게시 결과 불명 뒤 부모 중복 방지).
한 회차(장부 읽기 → 게시 → 상태 저장)는 상태 파일 옆 .lock 으로 프로세스 간 직렬화함. 잠금 중이면 busy 로 건너뜀.
받는 곳은 slack_notify.sh 와 같음: conversations.open(users=SLACK_USER_ID) 의 개인 DM.

토큰은 이 파일이 읽지 않음. 상주 프로세스(slack_socket.py)가 가진 WebClient 를 받아 쓰고,
단독 실행은 slack_socket.load_env 로 slack-bot.env 를 읽어 WebClient 를 만듦(값 출력 없음).

사용: ledger_sync.py [--dry-run] [--json]
환경: LRM_LEDGER 장부 경로 · LRM_LEDGER_STATE 상태 파일(기본 <LRM_HUB_DIR>/ledger-sync.state.json)
      LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs)
종료 코드: 0 성공(변경 없음·다른 동기화 진행 중 포함), 1 Slack 호출 실패, 2 설정 오류(토큰·사용자 ID 없음, 장부 읽기 실패·잘못된 행)
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field

import ledger

HUB_DIR = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
STATE_NAME = "ledger-sync.state.json"
MAX_REPLIES = 5  # 회차당 답글 상한. 넘치는 변경은 마지막 답글 1개에 묶음
RETRIES = 3  # 429 재시도 횟수
MAX_WAIT_S = 120  # 이 시간보다 긴 Retry-After 는 기다리지 않고 not_before 에 기록해 다음 회차로 미룸
LINE_MAX = 70  # 요청 한 줄 글자 상한
TITLE = "피드백 처리 현황"
PARENT_PREFIX = f"*{TITLE}*"  # 부모 본문 첫머리. 기존 부모 검색 기준
SCAN_PAGES = 5  # 기존 부모를 찾을 DM 이력 페이지 수(페이지당 100건)
OPEN_ORDER = ("진행", "접수", "보류")
# 사용자 본문에서 지우는 내부 표기: 항목 번호(F1·P8·D-HUD1), 머리말 라우팅(ui:·vfx: 등), 브랜치, 커밋 해시
INTERNAL = re.compile(r"\bD-[A-Z]+\d+\b|\b[A-Z]\d{1,3}\b|\b(?:ui|vfx|qa|data|config):/?"
                      r"|\b(?:feat|fix|hotfix|release|chore|docs)/[\w./-]+|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
EMPTY_PAREN = re.compile(r"\(\s*[/\s]*\)")
URL = re.compile(r"https?://\S+")
STATE_PHRASE = {"접수": "접수", "진행": "작업 시작", "완료": "완료", "보류": "보류"}
SNAP_KEYS = ("state", "progress", "request", "owner")  # 바뀌면 답글로 알릴 칸


class RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited {retry_after}s")
        self.retry_after = retry_after


class ApiError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SlackApi:
    """slack_sdk WebClient 어댑터. SlackApiError 를 RateLimited·ApiError 로 바꿈(예외 본문·토큰은 남기지 않음)"""

    def __init__(self, web) -> None:
        self.web = web

    def _call(self, fn, **kw) -> dict:
        try:
            return fn(**kw).data
        except Exception as err:
            resp = getattr(err, "response", None)
            if resp is not None and getattr(resp, "status_code", 0) == 429:
                raise RateLimited(float((resp.headers or {}).get("Retry-After", 30)))
            code = ((getattr(resp, "data", None) or {}).get("error")) if resp is not None else None
            raise ApiError(code or type(err).__name__)

    def open_dm(self, user: str) -> str:
        return self._call(self.web.conversations_open, users=user)["channel"]["id"]

    def find_parent(self, channel: str) -> str:
        """DM 이력에서 봇이 올린 가장 최근 부모 ts. 끝까지 보고도 없으면 빈 문자열, SCAN_PAGES 안에 못 끝내면 ApiError"""
        cursor = ""
        for _ in range(SCAN_PAGES):
            kw = {"channel": channel, "limit": 100}
            if cursor:
                kw["cursor"] = cursor
            page = self._call(self.web.conversations_history, **kw)
            for m in page.get("messages", []):
                if m.get("bot_id") and m.get("text", "").startswith(PARENT_PREFIX) and m.get("thread_ts", m["ts"]) == m["ts"]:
                    return m["ts"]
            cursor = (page.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                return ""
        raise ApiError("parent_search_incomplete")  # 이력이 더 남았는데 검색을 접음. 없다고 단정하면 부모가 중복되므로 생성 보류

    def post(self, channel: str, text: str, thread_ts: str = "") -> str:
        kw = {"channel": channel, "text": text}
        if thread_ts:
            kw["thread_ts"] = thread_ts
        return self._call(self.web.chat_postMessage, **kw)["ts"]

    def update(self, channel: str, ts: str, text: str) -> None:
        self._call(self.web.chat_update, channel=channel, ts=ts, text=text)


class DryApi:
    """Slack 을 부르지 않고 보낼 payload 를 모음"""

    def __init__(self) -> None:
        self.sent = []

    def open_dm(self, user: str) -> str:
        return "<개인 DM 채널>"

    def find_parent(self, channel: str) -> str:
        return ""

    def post(self, channel: str, text: str, thread_ts: str = "") -> str:
        self.sent.append({"api": "chat.postMessage", "channel": channel, "thread_ts": thread_ts or None, "text": text})
        return "<새 ts>"

    def update(self, channel: str, ts: str, text: str) -> None:
        self.sent.append({"api": "chat.update", "channel": channel, "ts": ts, "text": text})


@dataclass
class Conf:
    ledger: str = field(default_factory=ledger.ledger_path)
    state: str = field(default_factory=lambda: os.environ.get("LRM_LEDGER_STATE") or os.path.join(HUB_DIR, STATE_NAME))
    user_id: str = ""
    max_replies: int = MAX_REPLIES
    retries: int = RETRIES


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, st: dict) -> None:
    """권한 600 으로 원자적 저장"""
    fd, tmp = tempfile.mkstemp(prefix=".ledger-sync-", suffix=".tmp", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class StateError(Exception):
    """상태 파일을 못 씀. 게시 결과가 기록되지 않으므로 다음 회차가 기존 부모를 찾아 이어받음"""


@contextlib.contextmanager
def exclusive(state_path: str):
    """상태 파일 옆 .lock 에 비차단 배타 잠금. 잡으면 True, 다른 프로세스가 쥐고 있으면 False 를 내줌"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
        lock = open(state_path + ".lock", "a")
    except OSError as err:
        raise ledger.LedgerError(f"동기화 잠금 실패({err.strerror}) — 상태 파일 폴더 권한 확인", ledger.EXIT_IO)
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def digest(rows: list) -> str:
    return hashlib.sha256(ledger.render(rows).encode("utf-8")).hexdigest()


def snap(rows: list) -> dict:
    return {r["id"]: {k: r[k] for k in SNAP_KEYS} for r in rows}


def plain(text: str) -> str:
    """Slack 본문용 문장. 내부 번호·라우팅 머리말·브랜치·해시를 지우고 공백을 정리"""
    text = " ".join(EMPTY_PAREN.sub("", INTERNAL.sub("", ledger.clean(text))).split())
    return text.replace("( ", "(").replace(" )", ")")


def short(text: str) -> str:
    text = plain(text)
    return text if len(text) <= LINE_MAX else text[: LINE_MAX - 1] + "…"


def proof(evidence: str) -> str:
    """증거 문자열에서 사람이 열어 볼 링크만 Slack 링크로. 경로·해시는 장부에만 남김"""
    m = URL.search(evidence)
    return f" · <{m.group(0)}|증거 보기>" if m else ""


def summary(rows: list, today: str) -> str:
    """부모 메시지 본문. 오늘(YYYY-MM-DD)에 갱신된 완료만 보임. 내부 번호는 싣지 않음"""
    count = {s: sum(r["state"] == s for r in rows) for s in ledger.STATES}
    last = max((r["updated"] for r in rows), default="")
    lines = [f"{PARENT_PREFIX}" + (f" · 마지막 갱신 {last[5:]}" if last else ""),
             " · ".join(f"{ledger.EMOJI[s]} {s} {count[s]}" for s in ledger.STATES), ""]
    for s in OPEN_ORDER:
        lines += [f"{ledger.EMOJI[s]} {short(r['request'])}" + (f" ({plain(r['owner'])})" if plain(r["owner"]) else "")
                  for r in rows if r["state"] == s]
    done = [r for r in rows if r["state"] == ledger.DONE and r["updated"].startswith(today)]
    if done:
        lines += ["", "오늘 끝난 것"]
        lines += [f"{ledger.EMOJI[ledger.DONE]} {short(r['request'])}{proof(r['evidence'])}" for r in done]
    if not any(count[s] for s in OPEN_ORDER) and not done:
        lines.append("처리할 항목 없음")
    return "\n".join(lines).rstrip()


def changes(rows: list, before: dict) -> list:
    """직전 동기화 대비 바뀐 항목 → (id, 답글 한 줄) 목록. 없어진 id 는 무시.
    id 는 묶음 처리용이고 본문에는 요청 이름과 쉬운 상태 문장만 씀.
    옛 상태 파일에는 request·owner 가 없으므로 없는 칸은 바뀐 것으로 보지 않음"""
    out = []
    for r in rows:
        old = before.get(r["id"])
        em, name = ledger.EMOJI.get(r["state"], "·"), short(r["request"])
        if old is None:
            out.append((r["id"], f"📥 새로 접수 · {name}"))
            continue
        parts = []
        if old["state"] != r["state"]:
            parts.append(STATE_PHRASE.get(r["state"], r["state"]))
            if r["state"] == ledger.DONE:
                parts[-1] += proof(r["evidence"])
            elif plain(r["note"]):
                parts.append(short(r["note"]))
        if old.get("request", r["request"]) != r["request"]:
            parts.append("요청 내용 변경")
        if old.get("owner", r["owner"]) != r["owner"]:
            parts.append(f"담당 변경 ({plain(r['owner']) or '없음'})")
        if not parts and old["progress"] != r["progress"]:
            parts.append(f"진행 내용 갱신 · {short(r['note'])}" if plain(r["note"]) else "진행 내용 갱신")
        if parts:
            out.append((r["id"], f"{em} {name} — " + " · ".join(parts)))
    return out


def batches(items: list, limit: int) -> list:
    """답글 묶음 목록. limit 개를 넘으면 앞 limit-1 개는 하나씩, 나머지는 마지막 한 답글에 줄바꿈으로 묶음"""
    if len(items) <= limit:
        return [[i] for i in items]
    return [[i] for i in items[: limit - 1]] + [items[limit - 1:]]


class Deferred(Exception):
    """Retry-After 가 길어 이번 회차를 접음. not_before 까지 호출 안 함"""

    def __init__(self, until: float) -> None:
        super().__init__("deferred")
        self.until = until


def with_retry(fn, conf: Conf, now, sleep):
    """429 면 Retry-After 만큼 쉬고 재시도. MAX_WAIT_S 초과·재시도 소진이면 Deferred"""
    for attempt in range(conf.retries + 1):
        try:
            return fn()
        except RateLimited as rl:
            if rl.retry_after > MAX_WAIT_S or attempt == conf.retries:
                raise Deferred(now() + rl.retry_after)
            sleep(rl.retry_after)


def sync(conf: Conf, api, now=time.time, sleep=time.sleep, dry: bool = False) -> dict:
    """한 회차. 반환 {'status': unchanged|synced|deferred|busy|failed, 'calls': Slack 호출 수, 'replies': 답글 수}.
    dry 면 잠금·상태 저장 없이 payload 만 만듦. Slack 오류는 상태를 바꾸지 않은 채 failed 로 반환(다음 회차가 재시도)"""
    if dry:
        return _round(conf, api, now, sleep, True)
    with exclusive(conf.state) as got:
        if not got:
            return {"status": "busy", "calls": 0, "replies": 0}
        return _round(conf, api, now, sleep, False)


def _round(conf: Conf, api, now, sleep, dry: bool) -> dict:
    rows = ledger.read_rows(conf.ledger)
    ledger.check_rows(rows)  # 증거 없는 완료 등을 Slack 에 알리기 전에 거부
    today = time.strftime("%Y-%m-%d", time.localtime(now()))
    st = load_state(conf.state)
    h = digest(rows)
    if st.get("ts") and st.get("hash") == h and st.get("day") == today:
        return {"status": "unchanged", "calls": 0, "replies": 0}
    if st.get("not_before", 0) > now():
        return {"status": "deferred", "calls": 0, "replies": 0}
    calls = [0]

    def call(fn, *a):
        calls[0] += 1
        return with_retry(lambda: fn(*a), conf, now, sleep)

    def persist():
        if dry:
            return
        try:
            save_state(conf.state, st)
        except OSError:
            raise StateError

    replies = 0
    try:
        if not st.get("channel"):
            st["channel"] = call(api.open_dm, conf.user_id)
            persist()  # 게시 전에 쓰기 가능한지 확인. 못 쓰면 부모를 만들기 전에 중단
        channel, text, ts = st["channel"], summary(rows, today), st.get("ts")
        silent = not ts or "snapshot" not in st  # 부모를 새로 만들거나 이어받으면 기준선만 잡고 답글 없음
        if not ts:
            ts = call(api.find_parent, channel)  # 상태 유실·저장 실패·게시 결과 불명 뒤에는 기존 부모부터 찾음
        if ts:
            try:
                call(api.update, channel, ts, text)
            except ApiError as err:
                if err.code != "message_not_found":
                    raise
                ts, silent = "", True  # 부모가 지워졌으면 새로 만듦
        if not ts:
            ts = call(api.post, channel, text)
        if ts != st.get("ts"):
            st["ts"], st["snapshot"] = ts, snap(rows)  # ts 가 바뀌었으면 항상 silent
            persist()
        if silent:
            st["snapshot"] = snap(rows)
        else:
            before, now_snap = st.get("snapshot") or {}, snap(rows)
            for group in batches(changes(rows, before), conf.max_replies):
                call(api.post, channel, "\n".join(line for _, line in group), ts)
                replies += 1
                for rid, _ in group:
                    before[rid] = now_snap[rid]
                st["snapshot"] = before
                persist()
            st["snapshot"] = now_snap  # 없어진 id 정리
        st.update({"hash": h, "day": today})
        st.pop("not_before", None)
        persist()
    except Deferred as d:
        st["not_before"] = d.until
        try:
            persist()
        except StateError:
            return {"status": "failed", "calls": calls[0], "replies": replies, "error": "state_write_failed"}
        return {"status": "deferred", "calls": calls[0], "replies": replies}
    except StateError:
        return {"status": "failed", "calls": calls[0], "replies": replies, "error": "state_write_failed"}
    except ApiError as err:
        return {"status": "failed", "calls": calls[0], "replies": replies, "error": err.code}
    return {"status": "synced", "calls": calls[0], "replies": replies}


def live_api(conf: Conf):
    """단독 실행용 WebClient. 토큰은 slack_socket.load_env 로 읽고 출력하지 않음"""
    from slack_sdk import WebClient
    from slack_socket import load_env

    try:
        env = load_env(os.path.join(HUB_DIR, "slack-bot.env"))
    except OSError:
        raise SystemExit(_die("slack-bot.env 없음 — 사용자 설정 필요, 또는 --dry-run 으로 payload 만 확인", 2))
    if not env.get("SLACK_BOT_TOKEN") or not env.get("SLACK_USER_ID"):
        raise SystemExit(_die("SLACK_BOT_TOKEN·SLACK_USER_ID 없음 — slack-bot.env 확인", 2))
    conf.user_id = env["SLACK_USER_ID"]
    return SlackApi(WebClient(token=env["SLACK_BOT_TOKEN"]))


def _die(msg: str, code: int) -> int:
    sys.stderr.write(f"오류: {msg}\n")
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ledger_sync.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="피드백 장부가 바뀌었으면 Slack 진행 스레드(부모 요약 + 변경 답글)를 갱신. 바뀐 게 없으면 Slack 호출 0.",
        epilog="예:\n  ledger_sync.py --dry-run          # 보낼 부모 요약·답글만 출력(Slack 호출·상태 저장 없음)\n"
               "  ledger_sync.py                    # 실제 동기화(보통은 slack_socket.py 가 주기적으로 호출)\n"
               "환경: LRM_LEDGER, LRM_LEDGER_STATE, LRM_HUB_DIR\n"
               "종료 코드: 0 성공(변경 없음 포함), 1 Slack 호출 실패, 2 설정 오류")
    p.add_argument("--dry-run", action="store_true", help="Slack 호출·상태 저장 없이 보낼 payload 출력")
    p.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로 출력")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    conf = Conf()
    try:
        api = DryApi() if args.dry_run else live_api(conf)
        res = sync(conf, api, dry=args.dry_run)
    except ledger.LedgerError as err:
        return _die(str(err), 2)
    if args.dry_run:
        res["payloads"] = api.sent
        for p in api.sent:
            where = f" (스레드 {p['thread_ts']})" if p.get("thread_ts") else ""
            print(f"--- {p['api']}{where}\n{p['text']}")
    print(json.dumps(res, ensure_ascii=False) if args.json else f"동기화: {res['status']} · 호출 {res['calls']} · 답글 {res['replies']}")
    return 1 if res["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
