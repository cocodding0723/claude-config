#!/usr/bin/env python3
"""메인 코디네이터 터미널 판정·재탐색. slack_push·slack_socket·set_main_terminal.sh --auto 가 함께 씀.

Orca 재시작으로 터미널 핸들이 바뀌면 main.env 의 MAIN_TERMINAL 이 낡아 전달이 멈춤. 사람이 고쳐야 풀리는 사유(REDISCOVER)일 때
orca terminal list 에서 메인 후보를 찾고, 정확히 1개면 MAIN_TERMINAL 줄만 원자적으로 교체함. 0개·2개 이상이면 건드리지 않고 후보 수만 돌려줌.
핸들 값은 출력하지 않음(호출자가 로그에 앞 글자만 남김). Orca 호출은 run(args, timeout) -> (rc, JSON|None) 으로 주입받음.

CLI: main_terminal.py [--dry-run] [--env 경로]   종료 코드 0 갱신(또는 dry-run 에서 1개 발견), 2 오류, 3 후보 0개·2개 이상
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass

HANDLE_RE = re.compile(r"term_[0-9a-f-]+")  # fullmatch 로만 씀. $ 는 끝 개행을 통과시킴
MAIN_KEY = "MAIN_TERMINAL="
TITLE_KEY = "MAIN-HANDOFF"  # 메인 터미널 제목에 들어가는 표식
# Orca CLI 가 터미널 핸들 무효로 취급하는 오류 코드(번들 handlers/orchestration/terminal-identity.js). 그 밖(runtime_unavailable·시간 초과 등)은 일시 오류
HANDLE_GONE_CODES = frozenset({"terminal_handle_stale", "terminal_gone", "terminal_not_found"})
# main.env 를 사람이 고쳐야 풀리는 사유. 재탐색 대상. not_writable(일시 상태)·show_fail(조회 실패)·화면 상태는 제외
REDISCOVER = frozenset({"no_main_handle", "handle_gone", "handle_dead", "wrong_worktree", "not_claude", "pane_title_mismatch"})
LIST_ARGS = ["terminal", "list", "--limit", "1000", "--json"]
LIST_TIMEOUT = 20
EXIT_NONE = 3


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
    """재탐색 결과. status: updated | would_update(dry-run) | none | many | fail. count 는 후보 수(fail 이면 0)"""
    status: str
    count: int = 0
    handle: str = ""


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
    """terminal list 의 행 목록|None(조회 실패·목록 잘림). 잘린 목록은 개수·부재를 믿을 수 없음"""
    rc, d = run(LIST_ARGS, LIST_TIMEOUT)
    res = (d or {}).get("result") if isinstance(d, dict) else None
    rows = res.get("terminals") if isinstance(res, dict) else None
    if rc != 0 or not isinstance(rows, list) or res.get("truncated"):
        return None
    return [t for t in rows if isinstance(t, dict)]


def candidates(run, spec):
    """메인 후보 핸들 목록|None(조회 실패·목록 잘림). 제목에 TITLE_KEY 포함 + target_why 통과(메인 체크아웃·claude·연결됨·쓰기 가능)"""
    rows = list_rows(run)
    if rows is None:
        return None
    out = []
    for t in rows:
        h = t.get("handle")
        if isinstance(h, str) and HANDLE_RE.fullmatch(h) and TITLE_KEY in str(t.get("title") or "") and not target_why(t, spec) and h not in out:
            out.append(h)
    return out


def write_handle(path, handle):
    """main.env 의 MAIN_TERMINAL 줄만 교체(없으면 추가). 다른 줄은 원형 보존, 권한 600, 임시 파일 뒤 원자적 교체.
    path 가 링크면 링크는 두고 최종 대상을 교체. set_main_terminal.sh 와 같은 규칙"""
    path = os.path.realpath(path)
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    keep = []
    try:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            keep = [ln for ln in f if not ln.startswith(MAIN_KEY)]
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".main.env.", dir=d)  # mkstemp 는 600 으로 만듦
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.writelines(keep)
            if keep and not keep[-1].endswith("\n"):
                f.write("\n")
            f.write(f"{MAIN_KEY}{handle}\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def rediscover(run, spec, env_path, dry_run=False):
    """후보가 정확히 1개면 main.env 를 갱신. 쓰기 실패는 status=fail"""
    found = candidates(run, spec)
    if found is None:
        return Found("fail")
    if len(found) != 1:
        return Found("none" if not found else "many", len(found))
    if dry_run:
        return Found("would_update", 1, found[0])
    try:
        write_handle(env_path, found[0])
    except OSError:
        return Found("fail", 1)
    return Found("updated", 1, found[0])


def main(argv):
    args = argv[1:]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    env = os.environ.get("LRM_MAIN_ENV") or os.path.expanduser("~/.config/lrm-hubs/main.env")
    if args[:1] == ["--env"] and len(args) == 2:
        env = args[1]
    elif args:
        print("사용: main_terminal.py [--dry-run] [--env 경로]", file=sys.stderr)
        return 2
    orca = os.environ.get("LRM_PUSH_ORCA", "orca")
    f = rediscover(lambda a, t: run_orca(orca, a, t), Spec.from_env(), env, dry)
    if f.status in ("updated", "would_update"):
        verb = "갱신함" if f.status == "updated" else "발견함(dry-run, main.env 변경 없음)"
        print(f"메인 터미널 후보 1개 {verb} — {f.handle[:8]}…")
        return 0
    if f.status == "fail":
        print("메인 터미널 재탐색 실패 — orca terminal list 응답·main.env 쓰기 권한 확인", file=sys.stderr)
        return 2
    print(f"메인 터미널 후보 {f.count}개 — "
          + ("제목에 MAIN-HANDOFF 가 있는 메인 체크아웃 claude 터미널이 없음. 메인을 띄운 뒤 다시 실행" if f.count == 0
             else "하나로 못 정함. orca terminal list 에서 골라 set_main_terminal.sh <핸들> 로 지정"), file=sys.stderr)
    return EXIT_NONE


if __name__ == "__main__":
    sys.exit(main(sys.argv))
