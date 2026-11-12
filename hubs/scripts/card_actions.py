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
import main_terminal as mt  # noqa: E402

DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
STORE = os.path.join(DIR, "card-actions.json")
TTL_S = 7 * 24 * 3600  # 답 없는 카드의 등록 보존 기간
RUN_PICK = 1  # 실행을 뜻하는 선택지 번호
ORCA = os.environ.get("LRM_ORCA") or "orca"
ORCA_TIMEOUT_S = 30
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


def send_line(handle: str, text: str) -> bool:
    rc, _ = mt.run_orca(ORCA, ["terminal", "send", "--terminal", handle, "--text", text, "--enter", "--json"], ORCA_TIMEOUT_S)
    return rc == 0


def hub_terminals(slug: str) -> list:
    """허브 폴더(coord-<slug>-hub)에 연결된 claude 터미널 핸들. 조회 실패는 None"""
    res = orca_json(["terminal", "list", "--json", "--limit", "1000"])
    if res is None or not isinstance(res.get("terminals"), list):
        return None
    return [t.get("handle") for t in res["terminals"] if isinstance(t, dict) and str(t.get("worktreePath", "")).endswith(f"/coord-{slug}-hub")
            and t.get("agentIdentity") == "claude" and t.get("connected") is True and t.get("handle")]


def run_notify(args: dict) -> tuple[int, str]:
    """멈춘 워커 알림: 허브 코디네이터 터미널에 한 줄만 전송. 워커·창은 건드리지 않음.
    코디네이터가 2개 이상이면 어느 쪽인지 알 수 없어 허브에는 보내지 않고 메인에 중복 사실만 알림"""
    sent, notes = 0, []
    for hub in args.get("hubs", []):
        slug, lines = hub.get("slug", ""), hub.get("lines", [])
        handles = hub_terminals(slug) if slug else []
        if not handles:
            notes.append(f"{slug or '허브 미확인'}: 코디네이터 터미널 없음")
        elif len(handles) > 1:
            notes.append(f"{slug}: 코디네이터 {len(handles)}개라 보내지 않음")
        elif send_line(handles[0], "[멈춘 워커 감시] 응답 없는 워커: " + " / ".join(lines)[:600] + " — 상태 확인 후 조치 판단(확인 창은 사용자만 누름)"):
            sent += 1
        else:
            notes.append(f"{slug}: 전송 실패")
    dups = args.get("dups", [])
    main_handle = mt.read_handle(os.environ.get("LRM_MAIN_ENV") or os.path.join(DIR, "main.env")) if dups else None
    if dups:
        text = "[멈춘 워커 감시] 중복 코디네이터: " + "; ".join(f"{d['slug']} ({' / '.join(d['titles'])})" for d in dups) + " — 닫지 않았음, 정리 여부 판단 필요"
        if main_handle and send_line(main_handle, text[:600]):
            notes.append("중복 코디네이터를 메인에 알림")
        else:
            notes.append("중복 코디네이터 메인 알림 실패")
    bad = [n for n in notes if "없음" in n or "실패" in n or "않음" in n]
    return (1 if bad and not sent else 0), f"허브 {sent}곳 알림" + (f" · {'; '.join(notes)}" if notes else "")


KINDS = {"resume": run_resume, "notify": run_notify}


def execute(entry: dict, pick) -> tuple[int, str]:
    """답에 따라 실행. 1번이 아니면 동작 없음"""
    if pick != RUN_PICK:
        return 0, "보류 — 동작 없음"
    fn = KINDS.get(entry.get("kind"))
    if fn is None:
        return 1, f"알 수 없는 동작 종류: {entry.get('kind')}"
    return fn(entry.get("args") or {})


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
