#!/usr/bin/env python3
"""명령 접수기: slack-inbox.jsonl 의 새 사용자 명령에 번호를 매기고 장부에 '접수' 행을 남기며 머리말로 라우팅 대상을 기록.

번호·장부·라우팅만 함. 명령 해석·실행·허브 전달은 하지 않음(전달은 routes 기록을 읽는 쪽이 맡음).
멱등: 메시지 ts 로 접수 여부를 판정하므로 같은 줄을 다시 읽어도 번호·장부 행이 늘지 않음.
동시 실행(폴러·소켓): 상태 파일 옆 .lock 에 flock 을 잡고 한 번에 하나만 처리함.
대상: type 이 note·command 인 최상위 사용자 메시지. 반응·스레드 답글·봇 즉답 명령(대기·PR)은 제외.
첫 설치(상태 파일 없음)에서만 접수 기한(기본 24시간)보다 묵은 줄을 접수 없이 '본 것'으로 표시함. 운영 중 쌓인 미처리 명령은 나이와 무관하게 접수함.

환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs) · LRM_LEDGER 장부 · LRM_INTAKE_ROUTES 머리말 표 · LRM_INTAKE_BUDGET_S 시간 예산
시간 예산: 접수 단계 전체(잠금 대기 포함)에 LRM_INTAKE_BUDGET_S(기본 10초) 상한. 넘기면 하던 일을 멈추고 종료 코드 3 — 폴러 뒤 메인 전달을 막지 않고 다음 회차가 이어받음.
종료 코드: 0 성공(접수 0건 포함), 1 파일 읽기·쓰기 실패, 2 설정 오류(머리말 표 형식 등), 3 시간 예산 초과·잠금 대기 초과(다음 회차 재시도)
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 복사·링크 설치에서도 같은 폴더의 ledger 를 찾음
import ledger  # noqa: E402

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

    def __post_init__(self) -> None:
        join = lambda n: os.path.join(HUB_DIR, n)  # noqa: E731
        with contextlib.suppress(ValueError):
            self.budget_s = float(os.environ.get("LRM_INTAKE_BUDGET_S", self.budget_s))
        self.inbox = self.inbox or join("slack-inbox.jsonl")
        self.state = self.state or join("intake-state.json")
        self.routes_log = self.routes_log or join("intake-routes.jsonl")
        self.ledger = self.ledger or os.environ.get("LRM_LEDGER") or join("feedback-ledger.tsv")  # 라이브 장부 기본 경로를 쓰지 않음
        self.table = self.table or os.environ.get("LRM_INTAKE_ROUTES") or join("intake-routes.tsv")


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
        res = intake(conf, time.time(), args.dry_run)
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
        print(f"새 접수 {len(res['new'])}건 · 이미 접수 {res['seen']}건 · 제외 {res['skipped']}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
