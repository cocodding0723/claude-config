#!/usr/bin/env python3
"""명령 접수기: slack-inbox.jsonl 의 새 사용자 명령에 번호를 매기고 장부에 '접수' 행을 남기며 머리말로 라우팅 대상을 기록.

번호·장부·라우팅 뒤, 담당 허브가 main 이 아니면 그 허브 코디네이터 터미널에 '[접수 #번호] 원문' 한 줄을 하달함(deliver). 명령 해석·실행은 하지 않음.
하달 실패(허브 행·터미널 없음·후보 여러 개·조회 실패·전송 실패)는 조용히 버리지 않음: 장부를 '보류'+'전달 실패' 로 바꾸고 메인 터미널로 알림.
하달은 최대 한 번: 전송 직전 'sending' 을 저장하므로 전송 중 죽은 회차의 명령은 중복 전송 없이 '도착 불명'으로 메인에 알림.
멱등: 메시지 ts 로 접수 여부를 판정하므로 같은 줄을 다시 읽어도 번호·장부 행이 늘지 않음.
동시 실행(폴러·소켓): 상태 파일 옆 .lock 에 flock 을 잡고 한 번에 하나만 처리함.
대상: type 이 note·command 인 최상위 사용자 메시지. 반응·스레드 답글·봇 즉답 명령(대기·PR)은 제외.
첫 설치(상태 파일 없음)에서만 접수 기한(기본 24시간)보다 묵은 줄을 접수 없이 '본 것'으로 표시함. 운영 중 쌓인 미처리 명령은 나이와 무관하게 접수함.

환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs) · LRM_LEDGER 장부 · LRM_INTAKE_ROUTES 머리말 표 · LRM_INTAKE_BUDGET_S 시간 예산
      LRM_HUBS_TABLE 허브 표(기본 작업 폴더 hubs.tsv) · LRM_MAIN_ENV 메인 핸들 파일 · LRM_ORCA orca 실행 파일 · LRM_INTAKE_DELIVER_S 하달 시간 예산
시간 예산: 접수 단계 전체(잠금 대기 포함)에 LRM_INTAKE_BUDGET_S(기본 10초) 상한. 넘기면 하던 일을 멈추고 종료 코드 3 — 폴러 뒤 메인 전달을 막지 않고 다음 회차가 이어받음.
종료 코드: 0 성공(접수 0건 포함), 1 파일 읽기·쓰기 실패, 2 설정 오류(머리말 표 형식 등), 3 시간 예산 초과·잠금 대기 초과(다음 회차 재시도)
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 복사·링크 설치에서도 같은 폴더의 ledger 를 찾음
import ledger  # noqa: E402
import main_terminal as mt  # noqa: E402

HUB_DIR = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
EXIT_IO, EXIT_CONF, EXIT_BUSY = 1, 2, 3
DEFAULT_KEY = "*"
EXACT_KEY, PREFIX_KEY = "!exact", "!prefix"
# seen 값이 skip: 로 시작하면 접수하지 않고 제외한 항목(사유 포함)
SKIP_EMPTY, SKIP_KIND, SKIP_OLD = "skip:빈 명령", "skip:대상 아님", "skip:이관 기한 초과"


@dataclass
class Conf:
    inbox: str = ""
    state: str = ""
    routes_log: str = ""
    ledger: str = ""
    table: str = ""
    kinds: tuple = ("note", "command")
    id_prefix: str = "C"
    id_width: int = 4
    max_age_s: int = 86400  # 첫 설치 이관에만 적용
    req_max: int = 80
    budget_s: float = 10.0  # 접수 한 번의 전체 시간 상한(잠금 대기 포함)
    deliver_s: float = 40.0  # 하달 단계 시간 상한. orca 호출 대기가 길어 접수 예산과 따로 둠
    sent: str = ""  # 하달 상태 파일 {번호: {st, hub, at, why}}
    hubs_table: str = ""
    main_env: str = ""
    orca: str = ""
    main_spec: object = None
    ws_root: str = ""  # hubs.tsv 의 ws_root 가 '-' 인 허브의 작업 폴더 기본 위치(create_hub.sh 의 WS_ROOT 기본값과 같음)
    deliver_max_age_s: int = 3600  # 이보다 묵은 접수는 하달하지 않음(재개 직후 옛 명령이 허브로 쏟아지는 것 방지). 장부·메인 알림으로 남김

    def __post_init__(self) -> None:
        join = lambda n: os.path.join(HUB_DIR, n)  # noqa: E731
        with contextlib.suppress(ValueError):
            self.budget_s = float(os.environ.get("LRM_INTAKE_BUDGET_S", self.budget_s))
        self.inbox = self.inbox or join("slack-inbox.jsonl")
        self.state = self.state or join("intake-state.json")
        self.routes_log = self.routes_log or join("intake-routes.jsonl")
        self.ledger = self.ledger or os.environ.get("LRM_LEDGER") or join("feedback-ledger.tsv")  # 라이브 장부 기본 경로를 쓰지 않음
        with contextlib.suppress(ValueError):
            self.deliver_s = float(os.environ.get("LRM_INTAKE_DELIVER_S", self.deliver_s))
        self.table = self.table or os.environ.get("LRM_INTAKE_ROUTES") or join("intake-routes.tsv")
        self.sent = self.sent or join("intake-sent.json")
        self.hubs_table = self.hubs_table or os.environ.get("LRM_HUBS_TABLE") or join("hubs.tsv")
        self.main_env = self.main_env or os.environ.get("LRM_MAIN_ENV") or join("main.env")
        self.orca = self.orca or os.environ.get("LRM_ORCA") or shutil.which("orca") or "orca"
        self.main_spec = self.main_spec or mt.Spec.from_env()
        self.ws_root = self.ws_root or os.environ.get("LRM_HUB_WS_ROOT") or os.path.expanduser("~/orca/workspaces/LoveRevolutionMaker")


@dataclass
class Routes:
    """머리말 → 허브 표. 데이터 파일 한 곳이 정본"""
    hubs: dict = field(default_factory=dict)
    default: str = ""
    exact: set = field(default_factory=set)
    prefixes: tuple = ()

    def is_reply(self, text: str) -> bool:
        """봇 즉답 명령인가. slack_poll.sh cmd_replies 와 같은 줄 단위 판별(줄 양끝 공백·탭 제거)"""
        for line in text.strip().split("\n"):
            t = line.strip(" \t")
            if t in self.exact or any(t.startswith(p + " ") for p in self.prefixes):
                return True
        return False

    def pick(self, text: str) -> tuple:
        """(허브, 머리말 뗀 본문). 머리말이 없거나 표에 없으면 기본 대상과 원문"""
        m = re.match(r"\s*([A-Za-z0-9_-]+)\s*[:：]\s*(.*)", text, re.S)
        if m and m.group(1).lower() in self.hubs:
            return self.hubs[m.group(1).lower()], m.group(2)
        return self.default, text


def load_routes(path: str) -> Routes:
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as err:
        raise ledger.LedgerError(f"머리말 표 읽기 실패({err.strerror}) — LRM_INTAKE_ROUTES·hubs/intake-routes.tsv 확인", EXIT_CONF)
    rt, prefixes = Routes(), []
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        cells = ln.split("\t")
        if len(cells) != 2 or not all(c.strip() for c in cells):
            raise ledger.LedgerError(f"머리말 표 형식 오류: '{ln[:40]}' — '머리말<TAB>허브' 두 칸", EXIT_CONF)
        key, hub = cells[0].strip(), cells[1].strip()
        if key == EXACT_KEY:
            rt.exact.add(hub)
        elif key == PREFIX_KEY:
            prefixes.append(hub)
        elif key == DEFAULT_KEY:
            rt.default = hub
        else:
            rt.hubs[key.lower()] = hub
    rt.prefixes = tuple(prefixes)
    if not rt.default:
        raise ledger.LedgerError(f"머리말 표에 기본 행 없음 — '{DEFAULT_KEY}<TAB>대상' 한 줄 추가", EXIT_CONF)
    return rt


def read_inbox(path: str) -> list:
    """inbox → (줄 번호, dict) 목록. 줄 번호는 1부터. 깨진 줄은 건너뜀"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as err:
        raise ledger.LedgerError(f"inbox 읽기 실패({err.strerror}) — 경로·권한 확인", EXIT_IO)
    out = []
    for i, ln in enumerate(raw, 1):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append((i, d))
    return out


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("seen"), dict):
            return {"seq": int(st.get("seq", 0)), "seen": st["seen"], "base": st.get("base"),
                    "pending": st["pending"] if isinstance(st.get("pending"), dict) else {}}
    except (OSError, ValueError, TypeError):
        pass
    return {"seq": 0, "seen": {}, "base": None, "pending": {}}


def save_state(path: str, st: dict) -> None:
    d = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp = tempfile.mkstemp(prefix=".intake-", suffix=".tmp", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as err:
        raise ledger.LedgerError(f"접수 상태 쓰기 실패({err.strerror}) — 작업 폴더 권한 확인", EXIT_IO)


class Busy(Exception):
    """시간 예산·잠금 대기 초과. 상태는 예약까지만 저장돼 있어 다음 회차가 이어받음"""


LOCK_POLL_S = 0.05


@contextlib.contextmanager
def locked(path: str, wait_s: float):
    """배타 잠금. 대기는 wait_s 까지만 하고 넘기면 Busy"""
    try:
        with open(path + ".lock", "a") as lk:
            end = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise Busy("접수 잠금 대기 초과")
                    time.sleep(LOCK_POLL_S)
            yield
    except OSError as err:
        raise ledger.LedgerError(f"잠금 실패({err.strerror}) — 작업 폴더 권한 확인", EXIT_IO)


def logged_routes(path: str) -> dict:
    """라우팅 기록의 {번호: ts}. 장부·기록만 쓰고 죽은 회차가 어느 명령의 번호였는지 확인하는 근거"""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    d = json.loads(ln)
                    out[d.get("id")] = d.get("ts")
    except (OSError, ValueError):
        pass
    return out


def same_row(path: str, rid: str, req: str, owner: str) -> bool:
    """기록 없이 장부 행만 남은 번호가 이 명령의 행인가(요청·담당 일치)"""
    return any(r["id"] == rid and r["request"] == req and r["owner"] == owner for r in ledger.read_rows(path))


def command_text(item: dict) -> str:
    return item.get("text") or item.get("command") or ""


def is_intake(item: dict, conf: Conf, routes: Routes) -> bool:
    """접수 대상: 최상위 note·command 중 봇 즉답이 아닌 것. 결정 답(command=hold 등)도 사용자 명령이라 접수함"""
    text = command_text(item)
    return item.get("type") in conf.kinds and not item.get("parent_ts") and bool(text.strip()) and not routes.is_reply(text)


def summary(text: str, limit: int) -> str:
    one = ledger.clean(text)
    return one if len(one) <= limit else one[: limit - 1] + "…"


def intake(conf: Conf, now: float, dry: bool = False) -> dict:
    """새 명령 접수. 결과 {"new":[{id,hub,line,ts}], "skipped": 이번 회차에 제외한 줄 수, "seen": 이미 본 줄 수}"""
    routes = load_routes(conf.table)
    items = read_inbox(conf.inbox)
    new, skipped, seen_n = [], 0, 0
    with locked(conf.state, conf.budget_s):
        st = load_state(conf.state)
        logged = logged_routes(conf.routes_log)
        by_ts = {t: i for i, t in logged.items()}
        migrating = st["base"] is None and not st["seen"]  # 상태가 없는 첫 설치만 기한을 적용
        if migrating:
            st["base"] = now
        for line, it in items:
            ts = str(it.get("ts") or f"line{line}")
            if ts in st["seen"]:
                seen_n += 1
                continue
            if not is_intake(it, conf, routes):
                skipped += 1
                st["seen"][ts] = SKIP_KIND
                continue
            try:
                old = now - float(ts) > conf.max_age_s
            except ValueError:
                old = False
            if migrating and old:  # 첫 설치 때 묵은 기록으로 장부를 채우지 않음. 운영 중 미처리분은 나이와 무관하게 접수
                skipped += 1
                st["seen"][ts] = SKIP_OLD
                continue
            hub, body = routes.pick(command_text(it))
            if not ledger.clean(body):  # 머리말만 있는 명령. 장부 요청 칸이 비면 ledger.add 가 실패해 뒤 명령까지 막힘
                skipped += 1
                st["seen"][ts] = SKIP_EMPTY
                continue
            req = summary(body, conf.req_max)
            rid = st["pending"].get(ts) or by_ts.get(ts)  # 앞 회차가 이 명령에 번호를 쓰고 죽은 경우 같은 번호로 이어감
            while True:
                if rid is None:
                    st["seq"] += 1
                    rid = f"{conf.id_prefix}{st['seq']:0{conf.id_width}d}"
                    st["pending"][ts] = rid
                    if not dry:
                        save_state(conf.state, st)  # 번호 예약을 장부 쓰기보다 먼저 저장해 다른 명령이 같은 번호를 받지 않게 함
                if dry:
                    break
                try:
                    ledger.add(conf.ledger, rid, req, hub, now, False)
                except ledger.LedgerError as err:
                    if err.code != ledger.EXIT_MISSING:
                        raise
                    if logged.get(rid, ts) != ts or (rid not in logged and not same_row(conf.ledger, rid, req, hub)):
                        rid = None  # 번호가 다른 명령의 행으로 이미 쓰임. 새 번호로 다시 예약
                        continue
                if rid not in logged:
                    with open(conf.routes_log, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"id": rid, "ts": ts, "line": line, "hub": hub, "text": body, "at": int(now)}, ensure_ascii=False) + "\n")
                    logged[rid] = ts
                break
            st["pending"].pop(ts, None)
            st["seen"][ts] = rid
            new.append({"id": rid, "hub": hub, "line": line, "ts": ts})
        if not dry:
            save_state(conf.state, st)
    return {"new": new, "skipped": skipped, "seen": seen_n}


# 하달 상태 값. sending 은 전송 직전 저장(전송 중 죽으면 도착 불명으로 취급해 재전송하지 않음)
ST_SENDING, ST_SENT, ST_FAILED, ST_NOTIFIED, ST_LEGACY = "sending", "sent", "failed", "notified", "legacy"
SEND_TIMEOUT, LIST_TIMEOUT_S, SUBMIT_WAIT_S = 30, 15, 20
LINE_MAX = 1500


WORKER_MARK = "worker"  # 제목·pane 제목에 이 말이 들어간 터미널은 코디네이터가 아니라 워커(대소문자 무시)


def hub_path(conf: Conf, hub: str):
    """허브 표(hubs.tsv: slug·이름·역할 파일·ws_root·repo)에서 역할 파일 이름이 role-<hub>.txt 인 행의 코디네이터 작업 폴더 전체 경로
    (<ws_root>/coord-<slug>-hub, ws_root 가 '-' 면 기본 위치). 행이 없으면 None"""
    expand = lambda v: os.path.expanduser(v) if v.startswith("~") else v  # noqa: E731
    try:
        with open(conf.hubs_table, encoding="utf-8") as f:
            for ln in f:
                cells = ln.rstrip("\n").split("\t")
                if ln.startswith("#") or len(cells) < 3:
                    continue
                if os.path.basename(cells[2].strip()) == f"role-{hub}.txt":
                    root = cells[3].strip() if len(cells) > 3 and cells[3].strip() not in ("", "-") else conf.ws_root
                    return os.path.join(expand(root), f"coord-{cells[0].strip()}-hub")
    except OSError:
        pass
    return None


def is_worker(t: dict) -> bool:
    return any(WORKER_MARK in str(t.get(k) or "").lower() for k in ("title", "paneTitle"))


def find_hub_terminal(run, path: str):
    """(핸들|None, 실패 사유|None). 작업 폴더가 허브 전체 경로와 같고 연결된 쓰기 가능 claude 터미널 중 워커 표시(제목·pane 제목)가 없는 것이 정확히 1개일 때만.
    같은 워크트리에 붙은 리뷰·작업 워커는 후보가 아님. 워커만 있으면 코디네이터를 확인할 수 없으므로 하달하지 않음"""
    rows = mt.list_rows(run)
    if rows is None:
        return None, "터미널 목록 조회 실패"
    live = [t for t in rows
            if str(t.get("worktreePath") or "").rstrip("/") == path.rstrip("/") and t.get("agentIdentity") == "claude"
            and t.get("connected") is True and not t.get("orphaned") and t.get("writable") is True]
    hit = [t["handle"] for t in live if not is_worker(t)]
    name = os.path.basename(path.rstrip("/"))
    if not hit:
        return None, f"{name} 코디네이터 터미널 없음" + ("(워커 터미널만 있어 코디네이터 확인 불가)" if live else "")
    return (hit[0], None) if len(hit) == 1 else (None, f"{name} 코디네이터 후보 {len(hit)}개")


def send_line(run, handle: str, text: str) -> str:
    """영수증까지 확인한 결과. 'sent'(입력 접수 + turn_started) | 'rejected'(입력 전 거절: 핸들 무효·accepted=false) |
    'unconfirmed'(입력은 접수됐으나 턴 시작 미확인 — 시간 초과 관찰 등) | 'unknown'(시간 초과·일반 오류·영수증 이상, 입력됐을 수 있음)"""
    rc, d = run(["terminal", "send", "--terminal", handle, "--text", text, "--enter", "--wait-submit", str(SUBMIT_WAIT_S), "--json"], SEND_TIMEOUT)
    if rc != 0:
        return "rejected" if mt.error_code(d) in mt.HANDLE_GONE_CODES else "unknown"
    send = ((d or {}).get("result") or {}).get("send") if isinstance(d, dict) else None
    if not isinstance(send, dict) or not isinstance(send.get("accepted"), bool):
        return "unknown"
    if not send["accepted"]:
        return "rejected"
    prompt = send.get("prompt") if isinstance(send.get("prompt"), dict) else {}
    stages = prompt.get("stages") if isinstance(prompt.get("stages"), list) else []
    return "sent" if "turn_started" in stages else "unconfirmed"


def tell_main(conf: Conf, run, text: str) -> str:
    """메인 터미널로 알림: 'sent' | 'unconfirmed'(도착 불명, 재전송 안 함) | 'failed'(보내지 못함, 다음 회차 재시도).
    slack_socket.send_main 과 같은 원칙: 보내기 전에 check_link 로 메인(경로·claude·pane)임을 확인하고,
    확인 실패가 사람이 고쳐야 풀리는 사유(REDISCOVER)일 때나 입력 전 거절일 때만 재탐색 1회. 조회 실패·쓰기 불가는 보내지 않음"""
    handle = mt.read_handle(conf.main_env)
    if handle:
        _, why = mt.check_link(run, conf.main_spec, handle)
        if why and why not in mt.REDISCOVER:
            return "failed"
        if not why:
            res = send_line(run, handle, text)
            if res in ("sent", "unconfirmed"):
                return res
            if res != "rejected":
                return "failed"
    found = mt.rediscover(run, conf.main_spec, conf.main_env)
    new = found.handle if found.status in ("updated", "current_ok") else ""
    if found.status == "current_ok":
        new = mt.read_handle(conf.main_env) or ""
    if not new or new == handle:
        return "failed"
    res = send_line(run, new, text)
    return res if res in ("sent", "unconfirmed") else "failed"


def load_sent(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_sent(path: str, st: dict) -> None:
    save_state(path, st)  # 같은 원자적 쓰기(임시 파일 → replace)


def line_for(rid: str, text: str) -> str:
    one = ledger.clean(text)
    return f"[접수 #{rid}] " + (one if len(one) <= LINE_MAX else one[: LINE_MAX - 1] + "…")


def mark_failed(conf: Conf, rid: str, hub: str, why: str, now: float) -> None:
    """장부를 '보류'+'전달 실패' 로. 장부 행이 없으면 건너뜀(알림은 계속)"""
    with contextlib.suppress(ledger.LedgerError):
        ledger.set_state(conf.ledger, rid, "보류", None, f"전달 실패: {hub} 허브 — {why}", now, False)


def deliver(conf: Conf, now: float, run=None, dry: bool = False) -> dict:
    """routes 기록 중 아직 하달 안 한 접수를 담당 허브 터미널에 전달. 결과 {"sent","failed","notified","planned"}(번호 목록, planned 는 dry 에서 보냈을 번호).
    기본 대상(main)은 하달 대상 아님. 상태 파일이 없는 첫 실행은 한 시간보다 묵은 기록을 옛 접수(legacy)로 표시만 하고 보내지 않음.
    실패한 접수는 장부 보류 + 메인 알림, 메인 알림이 안 가면 failed 로 남겨 다음 회차에 알림만 다시 시도(허브 재전송은 안 함)"""
    run = run or (lambda a, t: mt.run_orca(conf.orca, a, t))
    routes = load_routes(conf.table)
    out = {"sent": [], "failed": [], "notified": [], "planned": []}
    entries = []
    try:
        with open(conf.routes_log, encoding="utf-8") as f:
            entries = [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        pass
    except ValueError:
        entries = []
    with locked(conf.state, conf.budget_s):
        first = not os.path.exists(conf.sent)
        st = load_sent(conf.sent)
        for e in entries:
            rid, hub = e.get("id"), e.get("hub")
            if not rid:
                continue
            if rid not in st and hub == routes.default:
                st[rid] = {"st": ST_LEGACY, "hub": hub}
                continue
            if rid in st and st[rid].get("st") not in (ST_FAILED, ST_SENDING):
                continue
            if rid not in st and first and now - float(e.get("at") or now) > conf.deliver_max_age_s:
                st[rid] = {"st": ST_LEGACY, "hub": hub}  # 기능 도입 전에 접수된 옛 기록
                continue
            ent = st.get(rid)
            if ent is None:
                age = now - float(e.get("at") or now)
                why = ""
                if age > conf.deliver_max_age_s:
                    why = "접수 후 한 시간이 지나 하달하지 않음"
                else:
                    path = hub_path(conf, hub)
                    handle, why = (None, f"허브 표에 {hub} 행 없음") if path is None else find_hub_terminal(run, path)
                    if handle and dry:  # 계획만. 전송 호출·상태 기록 없음
                        out["planned"].append(rid)
                        continue
                    if handle:
                        st[rid] = {"st": ST_SENDING, "hub": hub, "at": int(now)}
                        if not dry:
                            save_sent(conf.sent, st)  # 전송 직전 저장: 이 뒤에 죽으면 재전송 없이 도착 불명 처리
                        res = send_line(run, handle, line_for(rid, e.get("text") or ""))
                        if res == "sent":
                            st[rid] = {"st": ST_SENT, "hub": hub, "at": int(now)}
                            out["sent"].append(rid)
                            if not dry:
                                with contextlib.suppress(ledger.LedgerError):
                                    ledger.set_state(conf.ledger, rid, "진행", None, f"{hub} 허브에 하달", now, False)
                            continue
                        why = {"rejected": "허브 터미널이 입력을 거절함",
                               "unconfirmed": "입력은 접수됐지만 턴 시작 미확인·도착 여부 불명(재전송 안 함, 허브에서 직접 확인)"}.get(res, "전송 응답 없음·도착 여부 불명(재전송 안 함, 허브에서 직접 확인)")
                st[rid] = {"st": ST_FAILED, "hub": hub, "at": int(now), "why": why}
            elif ent.get("st") == ST_SENDING:  # 전송 중 죽은 회차
                st[rid] = {"st": ST_FAILED, "hub": hub, "at": int(now), "why": "전송 중 중단·도착 여부 불명(재전송 안 함, 허브에서 직접 확인)"}
            ent = st[rid]
            if ent["st"] != ST_FAILED:
                continue
            if not dry and not ent.get("ledgered"):
                mark_failed(conf, rid, hub, ent["why"], now)
                ent["ledgered"] = True
            out["failed"].append(rid)
            text = f"[접수 #{rid} 하달 실패 → 메인이 처리] {hub} 허브: {ent['why']}\n{line_for(rid, e.get('text') or '')}"
            res = "failed" if dry else tell_main(conf, run, text)
            if res != "failed":  # unconfirmed 도 재전송하지 않음(중복 방지). 장부 보류 기록은 남아 있음
                ent["st"] = ST_NOTIFIED
                out["notified"].append(rid)
        if not dry:
            save_sent(conf.sent, st)
    return out


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"오류: {message} — --help 참고\n")
        sys.exit(EXIT_CONF)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="intake.py", formatter_class=argparse.RawDescriptionHelpFormatter,
               description="inbox 의 새 사용자 명령에 번호를 매겨 장부에 접수하고 머리말로 라우팅 대상을 기록. 해석·실행은 하지 않음.",
               epilog="예:\n  intake.py --once            # 새 명령 접수\n  intake.py --once --dry-run  # 쓰지 않고 접수될 번호·대상만 출력\n  intake.py --once --json\n"
                      "환경: LRM_HUB_DIR, LRM_LEDGER, LRM_INTAKE_ROUTES\n종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 설정·인자 오류, 3 시간 예산·잠금 대기 초과(다음 회차 재시도)")
    p.add_argument("--once", action="store_true", required=True, help="한 번 접수 후 종료(필수)")
    p.add_argument("--dry-run", action="store_true", help="장부·상태·라우팅 기록을 쓰지 않고 결과만 출력")
    p.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로 출력")
    return p


def over_budget(*_):
    raise Busy("접수 시간 예산 초과")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    conf = Conf()
    # 장부 잠금(ledger.py)처럼 대기 상한이 없는 호출도 끊도록 알람으로 단계 전체를 제한
    signal.signal(signal.SIGALRM, over_budget)
    signal.setitimer(signal.ITIMER_REAL, conf.budget_s + 1)
    try:
        now = time.time()
        res = intake(conf, now, args.dry_run)
        signal.setitimer(signal.ITIMER_REAL, conf.deliver_s)  # 접수 예산과 따로 하달 예산
        res["deliver"] = deliver(conf, now, dry=args.dry_run)
    except Busy as err:
        sys.stderr.write(f"오류: {err} — 다음 회차에 재시도\n")
        return EXIT_BUSY
    except ledger.LedgerError as err:
        sys.stderr.write(f"오류: {err}\n")
        return err.code
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for n in res["new"]:
            print(f"{'[dry-run] ' if args.dry_run else ''}접수 {n['id']} → {n['hub']} (inbox {n['line']}줄)")
        dv = res["deliver"]
        print(f"{'[dry-run] 하달 예정 ' + str(len(dv['planned'])) + '건 · ' if args.dry_run else ''}하달 {len(dv['sent'])}건 · 실패 {len(dv['failed'])}건(메인 알림 {len(dv['notified'])}건)")
        print(f"새 접수 {len(res['new'])}건 · 이미 접수 {res['seen']}건 · 제외 {res['skipped']}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
