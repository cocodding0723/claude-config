#!/usr/bin/env python3
"""메인 직접 작업 차단(PreToolUse 훅). stdin: hook JSON. 출력: 거부·확인이면 hook JSON 한 줄, 통과면 빈 출력.
메인 세션(main_role.is_main)은 계획 카드를 적고 feature_open.sh 로 기능 코디네이터를 띄우는 창구라 직접 구현·조사하지 않음.
대상 세션은 메인뿐. 허브·작업자·그 밖 세션은 즉시 통과.
- 거부: Agent·Task 도구, 게임 저장소 Write·Edit, 게임 저장소를 고치는 Bash(리다이렉트·rm·mv·sed -i·git 쓰기·빌드), orca worker-start
- 통과: 읽기 도구, 읽기·현황 명령, bd·board-ask·board-answer·feature_open.sh, 게임 저장소 밖 쓰기(메모리·문서·승인 기록)
- 확인(ask): 허용·거부 표에 없는 Bash, 쓰기 대상을 못 푸는 명령($VAR·$( )). 오탐이 가장 큰 비용이라 판단 불가는 거부하지 않음
- 임시 허용: 사용자가 만든 허용 파일(ALLOW_FILE)이 TTL 안이면 전부 통과. 모델이 그 파일을 쓰려 하면 상시 거부
- 훅이 안 보는 경로: 변수로 만든 경로·스크립트 안의 쓰기·하위 에이전트 안의 도구 호출. 실수 방지 장치라 범위 밖
종료 코드: 항상 0(판정은 stdout JSON). 훅 내부 오류는 통과 + stderr 한 줄."""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import main_role  # noqa: E402
import main_send_guard  # noqa: E402
import ws_token_guard as wtg  # noqa: E402

# ---- 설정 ----
ALLOW_FILE = "~/.config/lrm-hubs/main-work.allow"  # 사용자가 직접 만듦. env LRM_MAIN_ALLOW 로 바꿈
ALLOW_ENV = "LRM_MAIN_ALLOW"
ALLOW_TTL = 3600  # 초. 파일 수정 시각 기준
GAME_ROOTS = ["~/orca/LoveRevolutionMaker", "~/orca/workspaces/LoveRevolutionMaker"]  # env LRM_GAME_ROOTS(: 구분)로 바꿈
GAME_ROOTS_ENV = "LRM_GAME_ROOTS"
AGENT_TOOLS = {"Agent", "Task"}
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
FILE_KEYS = ("file_path", "notebook_path", "path")
OPEN_CMD = "bash ~/.config/lrm-hubs/feature_open.sh --ticket LRM-n --name \"<기능 이름>\" --base dev --dry-run"
REASON_TAIL = (f"메인은 계획 카드만 적음 — 게임 작업은 기능 코디네이터로 넘김: {OPEN_CMD} (먼저 --dry-run). "
               f"급히 직접 해야 하면 사용자가 `! touch {ALLOW_FILE}` 로 1시간 허용")
REASON_AGENT = "메인은 하위 에이전트(Agent·Task)로 조사·구현하지 않음. " + REASON_TAIL
REASON_FILE = "메인은 게임 저장소 파일을 직접 고치지 않음({p}). " + REASON_TAIL
REASON_BASH = "메인은 게임 저장소를 직접 수정·빌드하지 않음({c}). " + REASON_TAIL
REASON_WORKER = "메인은 워커를 직접 기동하지 않음(worker-start) — 워커 배정은 기능 코디네이터 몫. " + REASON_TAIL
REASON_ALLOW = f"임시 허용 파일({ALLOW_FILE})은 사용자만 만들고 지움 — 모델은 쓰지 않음"
REASON_ASK = "메인 직접 작업 차단 훅이 판단하지 못한 명령({c}) — 읽기·작업판 조회가 아니면 기능 코디네이터로 넘길 것. 괜찮으면 승인"

# 읽기·현황 명령(어디서든 통과). 인자 검사 없음
READ_CMDS = {"ls", "cat", "head", "tail", "wc", "rg", "grep", "egrep", "fgrep", "jq", "awk", "gawk", "echo", "printf", "pwd", "date", "stat", "file",
             "test", "[", "[[", "which", "basename", "dirname", "realpath", "readlink", "true", "false", "sort", "uniq", "cut", "tr", "diff", "cmp",
             "du", "df", "ps", "uname", "whoami", "id", "env", "printenv", "tree", "column", "nl", "less", "more", "type", "command", "export",
             "set", "unset", "sleep", "sed", "find", "ag", "lsof", "pgrep", "md5", "shasum", "uptime", "hostname", "git"}
# 작업판·메인 도구(인자 없이 통과). 스크립트는 `bash <이름>` 으로 불러도 같은 이름이면 통과
BOARD_CMDS = {"bd", "board-ask", "board-answer", "feature_open.sh", "patrol.sh", "board_notify.py", "main_inbox.py", "plan_check.py", "board-run"}
SHELL_RUNNERS = main_send_guard.SHELLS | {"python", "python3"}
# 쓰기 명령: 모든 위치 인자가 대상 / 마지막 인자만 대상
WRITE_ALL = {"rm", "rmdir", "mv", "touch", "mkdir", "tee", "chmod", "chown", "truncate", "unlink", "shred", "patch", "sponge"}
WRITE_LAST = {"cp", "ln", "install", "rsync", "scp", "ditto"}
BUILD_CMDS = {"unity", "Unity", "dotnet", "xcodebuild", "gradle", "gradlew", "make", "npm", "npx", "yarn", "pnpm", "flutter", "msbuild", "fastlane", "pod"}
# git: 읽기는 어디서든 통과, 쓰기는 게임 저장소에서만 거부(밖은 통과), 모르는 서브명령은 확인
GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "ls-remote", "ls-tree", "blame", "grep", "describe", "shortlog", "cat-file", "reflog",
            "merge-base", "for-each-ref", "rev-list", "show-ref", "name-rev", "diff-tree", "check-ignore", "whatchanged", "fetch", "help", "version",
            "count-objects", "var", "verify-commit", "range-diff", "cherry", "show-branch", "diff-index", "diff-files", "ls-remote"}
GIT_WRITE = {"commit", "add", "rm", "mv", "push", "pull", "checkout", "switch", "merge", "rebase", "reset", "cherry-pick", "restore", "apply", "clean",
             "am", "revert", "init", "clone", "gc", "prune", "update-ref", "symbolic-ref", "submodule", "bisect", "format-patch"}
GIT_MIXED = {"branch", "tag", "stash", "worktree", "remote", "config", "notes"}  # 읽기·쓰기 겸용: 읽기 형태만 통과
GIT_MIXED_READ = {"branch": {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--contains", "--merged", "--no-merged", "-l", "--all"},
                  "tag": {"-l", "--list", "--contains", "-n"}, "stash": {"list", "show"}, "worktree": {"list"}, "remote": {"-v", "show", "get-url"},
                  "config": {"--get", "--get-all", "--list", "-l", "--get-regexp"}, "notes": {"list", "show"}}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
# orca: 읽기·메시지만 통과. worker-start 는 거부, 그 밖은 확인
ORCA_PASS = {("orchestration", "check"), ("orchestration", "send"), ("orchestration", "ask"), ("orchestration", "reply"), ("orchestration", "inbox"),
             ("terminal", "list"), ("terminal", "read"), ("terminal", "show"), ("status",), ("worktree", "list"), ("worktree", "ps")}
GH_READ = {("pr", "view"), ("pr", "list"), ("pr", "status"), ("pr", "checks"), ("pr", "diff"), ("run", "list"), ("run", "view"), ("issue", "view"),
           ("issue", "list"), ("repo", "view"), ("auth", "status")}
REDIR = re.compile(r"^(\d*|&)>>?(.*)$")


def decide(kind, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind, "permissionDecisionReason": reason}}, ensure_ascii=False))


def allow_path():
    return os.path.realpath(wtg.expand(os.environ.get(ALLOW_ENV) or ALLOW_FILE))


def allow_active():
    try:
        return time.time() - os.stat(allow_path()).st_mtime < ALLOW_TTL
    except OSError:
        return False


def game_roots():
    raw = os.environ.get(GAME_ROOTS_ENV)
    roots = raw.split(":") if raw else GAME_ROOTS
    return [os.path.realpath(wtg.expand(r)) for r in roots if r]


def in_game(path):
    return any(path == r or path.startswith(r + os.sep) for r in game_roots())


def resolve(arg, cwd):
    """리터럴 경로 → 실제 경로. $VAR·$( )·백틱·와일드카드 폴더가 남으면 None(판단 불가)"""
    a = wtg.expand(arg)
    if "$" in a or "`" in a:
        return None
    if not os.path.isabs(a):
        a = os.path.join(cwd, a)
    return os.path.realpath(a)


def seg_cwd(seg, i, cwd):
    """cd 단계면 새 cwd(풀 수 없으면 None), 아니면 그대로"""
    if len(seg) > i + 1:
        return resolve(seg[i + 1], cwd)
    return cwd


def positional(args):
    return [a for a in args if not a.startswith("-") and not REDIR.match(a)]


def redirect_targets(seg):
    out = []
    for k, t in enumerate(seg):
        m = REDIR.match(t)
        if not m:
            continue
        tgt = m.group(2) or (seg[k + 1] if k + 1 < len(seg) else "")
        if tgt and not tgt.startswith("&"):
            out.append(tgt)
    return out


def judge_git(seg, i, cwd):
    """git 단계 → pass|deny|ask"""
    k, gdir = i + 1, cwd
    while k < len(seg) and seg[k].startswith("-"):
        if seg[k] == "-C" and k + 1 < len(seg):
            gdir = resolve(seg[k + 1], gdir) if gdir else None
        k += 2 if seg[k] in GIT_VALUE_OPTS else 1
    if k >= len(seg):
        return "pass"
    sub, rest = seg[k], seg[k + 1:]
    if sub in GIT_READ:
        return "pass"
    if sub in GIT_MIXED:
        wanted = GIT_MIXED_READ[sub]
        pos = positional(rest)
        reads = any(a in wanted for a in rest) or (not rest and sub != "stash") or (sub == "tag" and not pos)
        if sub == "stash" and not rest:
            reads = False
        if reads and not (sub in ("branch", "tag") and pos and not any(a in wanted for a in rest)):
            return "pass"
    elif sub not in GIT_WRITE:
        return "ask"
    if gdir is None:
        return "ask"
    return "deny" if in_game(gdir) else "pass"


def judge_seg(seg, cwd):
    """명령 한 단계 → (pass|deny|ask, 새 cwd)"""
    i = wtg.head(seg)
    if i >= len(seg):
        return "pass", cwd  # 변수 대입만
    cmd = wtg.base(seg[i])
    args = seg[i + 1:]
    if cmd == "cd":
        return "pass", seg_cwd(seg, i, cwd) if cwd else None
    for t in redirect_targets(seg):
        p = resolve(t, cwd) if cwd else None
        if p is None:
            return "ask", cwd
        if in_game(p):
            return "deny", cwd
    if cmd in main_send_guard.SHELLS | {"python", "python3"}:
        if any(re.fullmatch(r"-[a-zA-Z]*c", a) for a in args) and cmd in main_send_guard.SHELLS:
            return "pass", cwd  # -c 문자열은 segments 가 따로 분리해 판정
        pos = positional(args)
        if cmd.startswith("python") and args[:1] == ["-m"] and args[1:2] == ["json.tool"]:
            return "pass", cwd
        return ("pass" if pos and wtg.base(pos[0]) in BOARD_CMDS else "ask"), cwd
    if cmd == "orca":
        pos = positional(args)
        if "worker-start" in pos:
            return "deny", cwd
        return ("pass" if any(tuple(pos[:len(p)]) == p for p in ORCA_PASS) else "ask"), cwd
    if cmd in BOARD_CMDS:
        return "pass", cwd
    if cmd == "git":
        return judge_git(seg, i, cwd), cwd
    if cmd == "gh":
        pos = positional(args)
        return ("pass" if any(tuple(pos[:2]) == p for p in GH_READ) else "ask"), cwd
    if cmd in BUILD_CMDS:
        return ("deny" if cwd and in_game(cwd) else "ask"), cwd
    if cmd == "sed" and any(re.fullmatch(r"-[a-zA-Z]*i[a-zA-Z]*|--in-place.*", a) for a in args):
        return judge_targets(positional(args)[1:], cwd), cwd
    if cmd in ("find", "xargs") and any(a in ("-delete", "-exec", "-execdir", "-fprint", "-ok") or cmd == "xargs" for a in args):
        return "ask", cwd
    if cmd in WRITE_ALL:
        return judge_targets(positional(args), cwd), cwd
    if cmd in WRITE_LAST:
        return judge_targets(positional(args)[-1:], cwd), cwd
    if cmd in READ_CMDS:
        return "pass", cwd
    return "ask", cwd


def judge_targets(paths, cwd):
    verdict = "pass"
    for a in paths:
        p = resolve(a, cwd) if cwd else None
        if p is None:
            verdict = "ask"
        elif in_game(p):
            return "deny"
    return verdict


def judge_bash(ev):
    cmd = (ev.get("tool_input") or {}).get("command") or ""
    name = os.path.basename(allow_path())
    if name in cmd and not all(wtg.base(s[wtg.head(s)]) in {"ls", "cat", "stat", "test", "[", "[[", "file", "head", "tail", "wc", "grep", "rg"}
                               for s in main_send_guard.segments(wtg.drop_heredocs(cmd)) if wtg.head(s) < len(s)):
        return "deny", REASON_ALLOW
    if allow_active():
        return None
    cwd = os.path.realpath(ev.get("cwd") or os.getcwd())
    asks = []
    for seg in main_send_guard.segments(wtg.drop_heredocs(cmd)):
        verdict, cwd = judge_seg(seg, cwd)
        text = " ".join(seg)[:80]
        if verdict == "deny":
            if "worker-start" in seg:
                return "deny", REASON_WORKER
            return "deny", REASON_BASH.format(c=text)
        if verdict == "ask":
            asks.append(text)
    return ("ask", REASON_ASK.format(c=asks[0])) if asks else None


def judge_file(ev):
    ti = ev.get("tool_input") or {}
    raw = next((ti[k] for k in FILE_KEYS if isinstance(ti.get(k), str)), "")
    if not raw:
        return None
    path = resolve(raw, os.path.realpath(ev.get("cwd") or os.getcwd()))
    if path is None:
        return None
    if path == allow_path():
        return "deny", REASON_ALLOW
    if not allow_active() and in_game(path):
        return "deny", REASON_FILE.format(p=raw)
    return None


def judge(ev):
    if not main_role.is_main(ev):
        return None
    tool = ev.get("tool_name") or ""
    if tool in AGENT_TOOLS:
        return None if allow_active() else ("deny", REASON_AGENT)
    if tool in FILE_TOOLS:
        return judge_file(ev)
    if tool == "Bash":
        return judge_bash(ev)
    return None


def main():
    try:
        res = judge(json.load(sys.stdin))
    except Exception as e:  # 훅 오류가 작업을 막지 않게 통과 처리
        print(f"main-work-guard: 판정 실패 — 통과 처리 ({e})", file=sys.stderr)
        return
    if res:
        decide(*res)


if __name__ == "__main__":
    main()
