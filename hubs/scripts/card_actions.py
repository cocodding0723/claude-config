#!/usr/bin/env python3
"""결정 카드 버튼에 매단 후속 동작 등록부. 감시기가 카드를 보낼 때 등록하고, 버튼 답이 오면 slack_socket.py 가 꺼내 실행함.

파일: $LRM_HUB_DIR(기본 ~/.config/lrm-hubs)/card-actions.json  {결정번호: {kind, args, createdAt}}
규칙: 선택지 1번이 '실행', 그 밖의 답(보류·직접 입력)은 동작 없이 항목만 지움. 항목은 꺼낼 때 지워 같은 카드를 두 번 눌러도 한 번만 실행함.
"""
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hub_dups  # noqa: E402
import main_terminal as mt  # noqa: E402

DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
STORE = os.path.join(DIR, "card-actions.json")
TTL_S = 7 * 24 * 3600  # 답 없는 카드의 등록 보존 기간
RUN_PICK = 1  # 실행을 뜻하는 선택지 번호
ORCA = mt.find_orca()  # 수신기(launchd)의 짧은 PATH 에서도 찾음
ORCA_TIMEOUT_S = 30
DELIVER_DEADLINE_S = 90  # 허브 한 곳 전송 시도 전체의 마감. 남은 시간이 ORCA_TIMEOUT_S 보다 작으면 남은 후보를 생략하고 메인 대리로
EXEC_TIMEOUT_S = 600  # 허브 여러 개를 순서대로 띄우는 시간까지 감안한 상한

EPILOG = """종료 코드: 0 성공(등록 없음 포함), 1 후속 동작 실패, 2 인자 오류
환경: LRM_HUB_DIR 작업 폴더, LRM_RESUME_ALL resume_all.sh 경로
예:
  card_actions.py handle --did D-RS0123456789 --pick 1
  card_actions.py list
"""


@contextlib.contextmanager
def flock(path: str, wait: bool = True):
    """프로세스 간 배타 잠금(fcntl.flock). wait=False 인데 이미 잡혀 있으면 False 를 내주고 즉시 반환, 잡으면 True.
    잠금 파일은 지우지 않음(지우면 서로 다른 inode 를 잡아 잠금이 무력화됨)"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    got = True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            got = False
        yield got
    finally:
        os.close(fd)  # 닫으면 잠금도 풀림


LOCK = STORE + ".lock"


def load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STORE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register(did: str, kind: str, args: dict, now: float | None = None) -> None:
    """카드 한 장의 후속 동작 등록. 보존 기간이 지난 항목은 같이 정리"""
    now = time.time() if now is None else now
    with flock(LOCK):  # 읽기-수정-저장 전체를 잠금. 안 하면 오래된 읽기본이 take 로 지운 항목을 되살림(재실행 위험)
        data = {k: v for k, v in load().items() if isinstance(v, dict) and now - v.get("createdAt", 0) < TTL_S}
        data[did] = {"kind": kind, "args": args, "createdAt": now}
        save(data)


def take(did: str) -> dict | None:
    """등록 항목을 꺼내며 지움. 등록이 없으면 None(일반 결정 카드)"""
    with flock(LOCK):
        data = load()
        entry = data.pop(did, None)
        if entry is None:
            return None
        save(data)
    return entry if isinstance(entry, dict) else None


TRY_TIMES = 4  # 비차단 시도 횟수. 총 대기 상한 ≈ (TRY_TIMES-1) * TRY_GAP_S
TRY_GAP_S = 0.05


def try_take(did: str) -> tuple[str, dict | None]:
    """수신 콜백용 비차단 take. ('ok', 항목) | ('none', None: 등록 없음 = 일반 카드) | ('busy', None: 다른 프로세스가 잠금 보유 = 처리 중).
    busy 는 '없음'이 아니므로 호출자가 일반 카드로 오인하지 말고 take() 로 이어서 처리해야 함(항목은 그대로 남아 있음)"""
    for i in range(TRY_TIMES):
        with flock(LOCK, wait=False) as got:
            if got:
                data = load()
                entry = data.pop(did, None)
                if entry is None:
                    return "none", None
                save(data)
                return "ok", entry if isinstance(entry, dict) else None
        if i < TRY_TIMES - 1:
            time.sleep(TRY_GAP_S)
    return "busy", None


def run_resume(args: dict) -> tuple[int, str]:
    """대상 허브만 resume_all.sh --only 로 재기동"""
    slugs = [s for s in args.get("slugs", []) if isinstance(s, str) and s]
    if not slugs:
        return 0, "대상 허브 없음"
    script = os.environ.get("LRM_RESUME_ALL") or os.path.join(DIR, "resume_all.sh")
    cmd = ["bash", script]
    for s in slugs:
        cmd += ["--only", s]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=EXEC_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, f"resume_all 실행 실패 {type(err).__name__}"
    return p.returncode, f"허브 {len(slugs)}개 이어받기 요청({', '.join(slugs)})" if p.returncode == 0 else f"resume_all 종료 코드 {p.returncode}"


def orca_json(args: list):
    rc, d = mt.run_orca(ORCA, args, ORCA_TIMEOUT_S)
    res = (d or {}).get("result") if rc == 0 and isinstance(d, dict) else None
    return res if isinstance(res, dict) else None


def send_line(handle: str, text: str):
    """한 줄 전송. 성공이면 None, 실패면 orca 오류 코드(없으면 rc=N·timeout)"""
    rc, d = mt.run_orca(ORCA, ["terminal", "send", "--terminal", handle, "--text", text, "--enter", "--json"], ORCA_TIMEOUT_S)
    if rc == 0:
        return None
    return mt.error_code(d) or ("timeout" if rc is None else f"rc={rc}")


def hub_terminals(slug: str, writable_only: bool = False):
    """허브 폴더(coord-<slug>-hub)의 살아 있는 claude 코디네이터 터미널 목록. 조회·워커 판별 실패는 None.
    코디네이터 = hub_dups.is_coord(워커 Dispatch 터미널 제외). 살아 있음은 hub_dups 와 같은 기준(실제 claude 프로세스),
    프로세스를 확인하지 못할 때만 Orca 의 연결됨·고아 아님 플래그로 대체. writable_only 는 전송 후보용 —
    중복 판정(stall_watch.coordinators)은 쓰기 가능 여부와 무관하게 세므로 기본은 거르지 않음"""
    res = orca_json(["terminal", "list", "--json", "--limit", "1000"])
    if res is None or not isinstance(res.get("terminals"), list):
        return None
    try:
        workers = hub_dups.worker_handles()  # 워커 터미널에 알림을 보내거나 중복으로 세지 않도록 판별 불가면 조회 실패로 취급
    except hub_dups.Unavailable:
        return None
    terms = [t for t in res["terminals"] if isinstance(t, dict) and isinstance(t.get("handle"), str) and t["handle"]
             and hub_dups.in_hub(t, slug=slug) and hub_dups.is_coord(t, workers) and (not writable_only or t.get("writable") is True)]
    try:
        live = hub_dups.live_handles()
    except hub_dups.Unavailable:
        return [t for t in terms if t.get("connected") is True and not t.get("orphaned")]
    return [t for t in terms if hub_dups.is_alive(t, live)]


def by_recent(terms: list) -> list:
    """마지막 출력(lastOutputAt)이 최근인 순. 값이 없으면 가장 오래된 것으로 봄, 같으면 목록 순서 유지"""
    return sorted(terms, key=lambda t: -(t.get("lastOutputAt") if isinstance(t.get("lastOutputAt"), (int, float)) else 0))


def pick_latest(terms: list) -> dict:
    return by_recent(terms)[0]


SENT, FAILED, UNKNOWN = "sent", "failed", "unknown"  # UNKNOWN = 시간 초과. 이미 전달됐을 수 있어 재전송 금지


def deliver(slug: str, text: str):
    """허브 코디네이터에 전송: (상태, 시도한 수, 실패 코드 목록, 후보 수).
    후보를 최근 출력 순으로 시도 → 모두 실패하면 목록을 1회 재조회해 아직 안 보낸 핸들만 시도(같은 핸들에 같은 요청 반복 금지).
    시간 초과는 UNKNOWN 으로 즉시 멈춤. 전체 마감(DELIVER_DEADLINE_S) 뒤에는 남은 호출 시간이 모자라면 후보를 생략"""
    end = time.monotonic() + DELIVER_DEADLINE_S
    tried, codes, first = set(), [], None

    def out_of_time() -> bool:
        if end - time.monotonic() >= ORCA_TIMEOUT_S:
            return False
        codes.append("deadline")
        return True

    for _rnd in range(2):
        if out_of_time():
            break
        terms = hub_terminals(slug, writable_only=True)
        if terms is None:
            codes.append("terminal_list_failed")
            break
        if first is None:
            first = len(terms)
        for t in by_recent([t for t in terms if t["handle"] not in tried]):
            if out_of_time():
                return FAILED, len(tried), codes, first or 0
            tried.add(t["handle"])
            err = send_line(t["handle"], text)
            if err is None:
                return SENT, len(tried), codes, first
            codes.append(err)
            if err == "timeout":
                return UNKNOWN, len(tried), codes, first
    return FAILED, len(tried), codes, first or 0


def str_list(v) -> list:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def run_notify(args: dict) -> tuple[int, str]:
    """멈춘 워커 알림: 허브 코디네이터 터미널 한 곳에 한 줄만 전송. 워커·창은 건드리지 않음.
    허브 전송이 끝내 실패하면 메인 터미널에 원문을 대리 전달. 시간 초과(전달 불명)는 원문을 다시 보내지 않고 메인에 '확인 필요' 한 줄만.
    메인 전달 실패·전달 불명·중복 알림 실패는 rc=1"""
    sent, notes, lost = 0, [], 0
    main_handle = mt.read_handle(os.environ.get("LRM_MAIN_ENV") or os.path.join(DIR, "main.env"))
    hubs = args.get("hubs")
    for hub in hubs if isinstance(hubs, list) else []:
        if not isinstance(hub, dict):
            continue
        slug = hub.get("slug") if isinstance(hub.get("slug"), str) else ""
        body = "응답 없는 워커: " + " / ".join(str_list(hub.get("lines")))[:600] + " — 상태 확인 후 조치 판단(확인 창은 사용자만 누름)"
        label = slug or "허브 미확인"
        status, tries, codes, found = deliver(slug, "[멈춘 워커 감시] " + body) if slug else (FAILED, 0, [], 0)
        if status == SENT:
            sent += 1
            if tries > 1:
                notes.append(f"{slug}: 앞선 {tries - 1}곳 전송 실패({', '.join(dict.fromkeys(codes))}) 뒤 {tries}번째 후보에 전송")
            elif found > 1:
                notes.append(f"{slug}: 코디네이터 {found}개 중 마지막 출력이 가장 최근인 1개에 전송")
            continue
        if status == UNKNOWN:
            lost += 1  # 확인 필요도 성공이 아님
            msg = f"[멈춘 워커 감시] {label} 허브 알림이 시간 초과로 전달 여부 불명 — 확인 필요(원문은 다시 보내지 않았음)"
            err = send_line(main_handle, msg) if main_handle else "main_handle_missing"
            notes.append(f"{label}: 전달 불명(시간 초과), 확인 필요 · " + ("메인에 확인 요청 전달" if err is None else f"메인 확인 요청도 실패({err})"))
            continue
        why = f"전송 실패({', '.join(dict.fromkeys(codes))})" if codes else "코디네이터 터미널 없음"
        proxy = f"[멈춘 워커 감시] ({label} 허브에 전달하지 못해 메인에 대리 전달) " + body
        err = send_line(main_handle, proxy) if main_handle else "main_handle_missing"
        if err is None:
            notes.append(f"{label}: {why} · 메인에 대리 전달")
        else:
            lost += 1
            notes.append(f"{label}: {why} · 메인 대리 전달도 실패({err})" + (" — 전달 불명, 확인 필요" if err == "timeout" else ""))
    dups = [d for d in args.get("dups", []) if isinstance(d, dict) and isinstance(d.get("slug"), str)] if isinstance(args.get("dups"), list) else []
    if dups:
        text = "[멈춘 워커 감시] 중복 코디네이터: " + "; ".join(f"{d['slug']} ({' / '.join(str_list(d.get('titles')))})" for d in dups) + " — 닫지 않았음, 정리 여부 판단 필요"
        if main_handle and send_line(main_handle, text[:600]) is None:
            notes.append("중복 코디네이터를 메인에 알림")
        else:
            lost += 1
            notes.append("중복 코디네이터 메인 알림 실패")
    return (1 if lost else 0), f"허브 {sent}곳 알림" + (f" · {'; '.join(notes)}" if notes else "")


KINDS = {"resume": run_resume, "notify": run_notify}


def execute(entry: dict, pick) -> tuple[int, str]:
    """답에 따라 실행. 1번이 아니면 동작 없음"""
    if pick != RUN_PICK:
        return 0, "보류 — 동작 없음"
    fn = KINDS.get(entry.get("kind"))
    if fn is None:
        return 1, f"알 수 없는 동작 종류: {entry.get('kind')}"
    args = entry.get("args")
    try:
        return fn(args if isinstance(args, dict) else {})
    except Exception as err:  # 수신 스레드가 조용히 죽지 않게 결과로 돌려줌
        return 1, f"후속 동작 예외({type(err).__name__})"


def cmd_handle(a) -> int:
    entry = take(a.did)
    if entry is None:
        print(f"등록 없음: {a.did}")
        return 0
    rc, msg = execute(entry, a.pick)
    print(msg)
    return rc


def cmd_list(_a) -> int:
    for did, v in load().items():
        print(f"{did}\t{v.get('kind')}\t{json.dumps(v.get('args'), ensure_ascii=False)}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="card_actions.py", description="결정 카드 후속 동작 등록부. 대화형 입력 없음.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{handle,list}")
    h = sub.add_parser("handle", help="버튼 답 처리(등록 항목을 꺼내 실행)")
    h.add_argument("--did", required=True, help="결정 번호(D-...)")
    h.add_argument("--pick", required=True, type=int, help="누른 선택지 번호(1 = 실행)")
    h.set_defaults(fn=cmd_handle)
    sub.add_parser("list", help="대기 중인 등록 목록").set_defaults(fn=cmd_list)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
