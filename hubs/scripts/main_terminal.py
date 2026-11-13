#!/usr/bin/env python3
"""메인 코디네이터 터미널 판정·재탐색. slack_push·slack_socket·set_main_terminal.sh --auto 가 함께 씀.

Orca 재시작으로 터미널 핸들이 바뀌면 main.env 의 MAIN_TERMINAL 이 낡아 전달이 멈춤. 사람이 고쳐야 풀리는 사유(REDISCOVER)일 때
orca terminal list 에서 메인 후보를 찾고, 정확히 1개면 MAIN_TERMINAL 줄만 원자적으로 교체함. 0개·2개 이상이면 건드리지 않고 후보 수만 돌려줌.
핸들 값은 출력하지 않음(호출자가 로그에 앞 글자만 남김). Orca 호출은 run(args, timeout) -> (rc, JSON|None) 으로 주입받음.
후보 = 제목이 TITLE_KEY 로 시작하는 메인 체크아웃의 claude 터미널(워커 제목에 표식이 끼어도 후보가 되지 않게 앞부분 일치).
main.env 갱신은 파일 잠금 아래에서 현재 핸들을 재검증한 뒤 씀(폴러·소켓·set_main_terminal.sh 가 같은 잠금 공유). 잠금 밖 직접 편집은 보호하지 않음.

CLI: main_terminal.py [--dry-run] [--env 경로]   재탐색. 종료 코드 0 갱신(또는 dry-run 에서 1개 발견), 2 오류, 3 후보 0개·2개 이상
     main_terminal.py --set <핸들> [--env 경로]   잠금 아래에서 MAIN_TERMINAL 줄 기록. 종료 코드 0, 2 형식·쓰기 오류
"""
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

HANDLE_RE = re.compile(r"term_[0-9a-f-]+")  # fullmatch 로만 씀. $ 는 끝 개행을 통과시킴
MAIN_KEY = "MAIN_TERMINAL="
TITLE_KEY = "MAIN-HANDOFF"  # 메인 터미널 제목의 시작 표식(예: "MAIN-HANDOFF.md 메인 코디네이터"). 운영 규약: 이 접두부는 메인 전용, 워커 제목은 이 접두부로 시작 금지(같은 체크아웃 claude 워커가 후보가 되면 재탐색이 오인). 중간에 낀 경우는 후보 아님
# Orca CLI 가 터미널 핸들 무효로 취급하는 오류 코드(번들 handlers/orchestration/terminal-identity.js). 그 밖(runtime_unavailable·시간 초과 등)은 일시 오류
HANDLE_GONE_CODES = frozenset({"terminal_handle_stale", "terminal_gone", "terminal_not_found"})
# main.env 를 사람이 고쳐야 풀리는 사유. 재탐색 대상. not_writable(일시 상태)·show_fail(조회 실패)·화면 상태는 제외
REDISCOVER = frozenset({"no_main_handle", "handle_gone", "handle_dead", "wrong_worktree", "not_claude", "pane_title_mismatch"})
LIST_ARGS = ["terminal", "list", "--limit", "1000", "--json"]
LIST_TIMEOUT = 20
LOCK_NAME, LOCK_WAIT = ".main-env.lock", 60  # main.env 옆 잠금 파일. 재탐색은 orca 호출 몇 번을 품으므로 넉넉히 기다림
EXIT_NONE = 3
# launchd 기본 PATH(/usr/bin:/bin)에는 orca 가 없음 → PATH 밖 설치 위치도 찾음
ORCA_CANDIDATES = ("/usr/local/bin/orca", "/Applications/Orca.app/Contents/Resources/bin/orca")


@dataclass(frozen=True)
class Spec:
    """메인 세션 판정 기준"""
    main_path: str
    agent: str = "claude"
    pane: str = "MAIN"  # show 에 paneTitle 이 있고 이 값과 다르면 불일치. "" 이면 대조 생략

    @classmethod
    def from_env(cls):
        return cls(main_path=(os.environ.get("LRM_PUSH_MAIN") or os.path.expanduser("~/orca/LoveRevolutionMaker")).rstrip("/"),
                   pane=os.environ.get("LRM_PUSH_PANE", "MAIN"))


@dataclass(frozen=True)
class Found:
    """재탐색 결과. status: updated | would_update(dry-run) | current_ok(이미 정상) | none | many | fail. count 는 후보 수(fail 이면 0)"""
    status: str
    count: int = 0
    handle: str = ""


def find_orca(env=None, path=None, candidates=ORCA_CANDIDATES):
    """orca 실행 파일 경로. LRM_ORCA → PATH(path 로 대체 가능) → candidates 순. 못 찾으면 맨 'orca'"""
    env = os.environ if env is None else env
    return env.get("LRM_ORCA") or shutil.which("orca", path=path) or next((p for p in candidates if os.path.exists(p)), "orca")


def run_orca(orca, args, timeout):
    """(returncode, 파싱한 JSON|None). 시간 초과는 (None, None), 실행 불가는 (127, None)"""
    try:
        p = subprocess.run([orca] + args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, None
    except OSError:
        return 127, None
    for blob in (p.stdout, p.stderr):
        try:
            d = json.loads(blob)
            if isinstance(d, dict):
                return p.returncode, d
        except ValueError:
            pass
    return p.returncode, None


def read_handle(path):
    """main.env 의 MAIN_TERMINAL 줄만 읽은 핸들|None. 형식이 term_<16진·하이픈> 이 아니거나 서로 다른 값이 둘 이상이면 무효"""
    vals = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.startswith(MAIN_KEY):
                    vals.add(ln[len(MAIN_KEY):].strip())
    except OSError:
        return None
    v = vals.pop() if len(vals) == 1 else None
    return v if v and HANDLE_RE.fullmatch(v) else None


def target_why(t, spec):
    """터미널 정보가 메인 세션이 아니면 사유 코드. 끊김·고아·쓰기 불가·다른 체크아웃·claude 아님·제목 불일치 순"""
    if t.get("connected") is not True or t.get("orphaned"):
        return "handle_dead"
    if t.get("writable") is not True:
        return "not_writable"
    if str(t.get("worktreePath") or "").rstrip("/") != spec.main_path:
        return "wrong_worktree"
    if t.get("agentIdentity") != spec.agent:
        return "not_claude"
    if spec.pane and "paneTitle" in t and t.get("paneTitle") != spec.pane:  # set-pane-title 이 show 에 안 보이는 경우가 있어 키가 없으면 통과
        return "pane_title_mismatch"
    return None


def error_code(d):
    err = d.get("error") if isinstance(d, dict) else None
    return err.get("code") if isinstance(err, dict) else None


def check_link(run, spec, handle):
    """핸들을 terminal show 로 확인: (터미널 dict|None, 사유 코드|None). 화면 상태는 보지 않음(호출자 몫).
    사유: handle_gone·show_fail(조회 실패) 또는 target_why 결과"""
    rc, d = run(["terminal", "show", "--terminal", handle, "--json"], LIST_TIMEOUT)
    if rc is None or rc == 127:
        return None, "show_fail"
    t = ((d or {}).get("result") or {}).get("terminal") if isinstance(d, dict) else None
    if rc != 0 or not isinstance(t, dict):
        return None, "handle_gone" if error_code(d) in HANDLE_GONE_CODES else "show_fail"
    if t.get("handle") not in (None, handle):  # 다른 터미널의 정보를 돌려받음
        return None, "handle_gone"
    return t, target_why(t, spec)


def list_rows(run):
    """terminal list 의 행 목록|None(조회 실패·목록 잘림·모양 이상). 모든 행이 term_ 핸들을 가져야 믿음:
    한 행이라도 어긋나면 그 행이 찾는 터미널일 수 있어 '목록에 없음'을 단정할 수 없음"""
    rc, d = run(LIST_ARGS, LIST_TIMEOUT)
    res = (d or {}).get("result") if isinstance(d, dict) else None
    rows = res.get("terminals") if isinstance(res, dict) else None
    if rc != 0 or not isinstance(rows, list) or res.get("truncated"):
        return None
    if not all(isinstance(t, dict) and isinstance(t.get("handle"), str) and HANDLE_RE.fullmatch(t["handle"]) for t in rows):
        return None
    return rows


def candidates(run, spec):
    """메인 후보 핸들 목록|None(조회 실패·목록 잘림·모양 이상). 제목이 TITLE_KEY 로 시작 + target_why 통과(메인 체크아웃·claude·연결됨·쓰기 가능)"""
    rows = list_rows(run)
    if rows is None:
        return None
    out = []
    for t in rows:
        h = t["handle"]
        if str(t.get("title") or "").startswith(TITLE_KEY) and not target_why(t, spec) and h not in out:
            out.append(h)
    return out


def fate(run, match):
    """match(핸들)가 참인 터미널의 생사: 'dead' | 'alive' | 'unknown'. 핸들 원문 없이 해시 등으로만 아는 터미널 확인용.
    dead 는 종료 근거가 있을 때만: 목록 전체 조회가 성공했고(잘림·행 모양 이상 없음) 그 핸들이 없음.
    목록에 있으면 연결·고아 표시로 종료를 단정할 수 없음(입력은 갔는데 통신만 끊겼을 수 있음) → 연결 정상이면 alive, 아니면 unknown.
    조회 실패도 unknown"""
    rows = list_rows(run)
    if rows is None:
        return "unknown"
    hit = [t for t in rows if match(t["handle"])]
    if not hit:
        return "dead"
    return "alive" if any(t.get("connected") is True and not t.get("orphaned") for t in hit) else "unknown"


@contextlib.contextmanager
def env_lock(path, wait=LOCK_WAIT):
    """main.env(링크면 최종 대상) 옆 잠금 파일의 배타 잠금. wait 초 안에 못 얻으면 TimeoutError"""
    d = os.path.dirname(os.path.realpath(path))
    os.makedirs(d, exist_ok=True)
    fd = os.open(os.path.join(d, LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        end = time.time() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= end:
                    raise TimeoutError("main.env 잠금 대기 초과")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # 닫으면 잠금도 풀림


def read_lines(path):
    """main.env 줄 목록(원형·줄 끝 포함)|None(파일 없음)"""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            return f.readlines()
    except FileNotFoundError:
        return None


def raw_values(lines):
    """MAIN_TERMINAL 줄 값 목록(읽은 그대로). 갱신 전후 비교용"""
    return [ln[len(MAIN_KEY):].strip() for ln in lines or [] if ln.startswith(MAIN_KEY)]


def write_handle(path, handle):
    """main.env 의 MAIN_TERMINAL 줄만 교체(없으면 추가). 다른 줄은 원형 보존, 권한 600, 임시 파일 뒤 원자적 교체.
    path 가 링크면 링크는 두고 최종 대상을 교체. 호출자가 env_lock 안에서 부름.
    보장 범위는 공용 잠금을 쓰는 writer(재탐색·--set·set_main_terminal.sh)뿐. 잠금 없이 main.env 를 편집하는 쪽과의 경합은 보호하지 않음"""
    path = os.path.realpath(path)
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    keep = [ln for ln in read_lines(path) or [] if not ln.startswith(MAIN_KEY)]
    fd, tmp = tempfile.mkstemp(prefix=".main.env.", dir=d)  # mkstemp 는 600 으로 만듦
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.writelines(keep)
            if keep and not keep[-1].endswith("\n"):
                f.write("\n")
            f.write(f"{MAIN_KEY}{handle}\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def rediscover(run, spec, env_path, dry_run=False):
    """메인 연결이 끊겼을 때 후보가 정확히 1개면 main.env 를 갱신. 잠금 아래에서 현재 핸들을 다시 확인해
    그 사이 다른 쪽이 고쳤으면(연결 정상) 쓰지 않고 status=current_ok. 쓰기·잠금 실패는 status=fail"""
    try:
        with contextlib.nullcontext() if dry_run else env_lock(env_path):
            cur = read_handle(env_path)
            if cur:
                _, why = check_link(run, spec, cur)
                if why not in REDISCOVER:
                    return Found("current_ok", 0, cur)  # 이미 고쳐졌거나 일시 오류(조회 실패·쓰기 불가)면 건드리지 않음
            found = candidates(run, spec)
            if found is None:
                return Found("fail")
            if len(found) != 1:
                return Found("none" if not found else "many", len(found))
            if dry_run:
                return Found("would_update", 1, found[0])
            write_handle(env_path, found[0])
            return Found("updated", 1, found[0])
    except (OSError, TimeoutError):
        return Found("fail")


def set_handle(path, handle):
    """수동 기록: 형식 확인 뒤 잠금 아래에서 MAIN_TERMINAL 교체"""
    if not HANDLE_RE.fullmatch(handle):
        raise ValueError("핸들 형식 오류")
    with env_lock(path):
        write_handle(path, handle)


def main(argv):
    args = argv[1:]
    if args[:1] == ["--set"]:
        env = os.environ.get("LRM_MAIN_ENV") or os.path.expanduser("~/.config/lrm-hubs/main.env")
        if len(args) == 4 and args[2] == "--env":
            env, args = args[3], args[:2]
        if len(args) != 2:
            print("사용: main_terminal.py --set <핸들> [--env 경로]", file=sys.stderr)
            return 2
        try:
            set_handle(env, args[1])
        except ValueError:
            print("핸들 형식 오류: term_ 뒤에 16진·하이픈만 허용 — orca terminal list 의 handle 값을 그대로 넣음", file=sys.stderr)
            return 2
        except (OSError, TimeoutError) as err:
            print(f"main.env 기록 실패({type(err).__name__}) — 폴더 권한·다른 갱신 작업 확인", file=sys.stderr)
            return 2
        print(f"MAIN_TERMINAL 기록함 — {os.path.basename(env)} (값은 출력하지 않음)")
        return 0
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    env = os.environ.get("LRM_MAIN_ENV") or os.path.expanduser("~/.config/lrm-hubs/main.env")
    if args[:1] == ["--env"] and len(args) == 2:
        env = args[1]
    elif args:
        print("사용: main_terminal.py [--dry-run] [--env 경로]", file=sys.stderr)
        return 2
    orca = os.environ.get("LRM_PUSH_ORCA") or find_orca()
    f = rediscover(lambda a, t: run_orca(orca, a, t), Spec.from_env(), env, dry)
    if f.status == "current_ok":
        print("메인 연결이 이미 정상 — 바꾸지 않음")
        return 0
    if f.status in ("updated", "would_update"):
        verb = "갱신함" if f.status == "updated" else "발견함(dry-run, main.env 변경 없음)"
        print(f"메인 터미널 후보 1개 {verb} — {f.handle[:8]}…")
        return 0
    if f.status == "fail":
        print("메인 터미널 재탐색 실패 — orca terminal list 응답·main.env 쓰기 권한 확인", file=sys.stderr)
        return 2
    print(f"메인 터미널 후보 {f.count}개 — "
          + ("제목이 MAIN-HANDOFF 로 시작하는 메인 체크아웃 claude 터미널이 없음. 메인을 띄운 뒤 다시 실행" if f.count == 0
             else "하나로 못 정함. orca terminal list 에서 골라 set_main_terminal.sh <핸들> 로 지정"), file=sys.stderr)
    return EXIT_NONE


if __name__ == "__main__":
    sys.exit(main(sys.argv))
