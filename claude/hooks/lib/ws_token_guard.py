#!/usr/bin/env python3
"""작업 공간 직접 생성·봇 토큰 접근 판정(PreToolUse Bash|Read|Grep 훅). stdin: hook JSON. 출력: 거부면 hook JSON 한 줄, 통과면 빈 출력.
- git worktree add: 모든 세션 거부. 작업 공간은 create_worker_ws.sh·Orca worker-start 로만 만듦(스크립트 안의 git 은 훅이 보지 않음).
- 봇 토큰 파일(slack-bot.env) 내용 읽기: 모든 세션 거부. 이름 언급(검색 패턴·커밋 메시지·ls·test -f)은 통과, 파일을 여는 명령의 인자일 때만 거부.
- slack_notify.sh 실행: 작업자 세션만 거부, 보고는 hub_report.py. 읽기·편집·git add·bash -n·--help 는 통과.
Bash 는 줄 이음·heredoc 본문을 지운 뒤 main_send_guard.segments 로 단계를 나눠, 단계의 첫 실행 낱말과 인자를 봄(따옴표·$( )·bash -c 안도 단계로 풀림).
한계(범위 밖): 변수로 만든 명령·경로(w=worktree; git $w add, cat "$F"), 설정 폴더 상위(~·~/.config)를 통째로 검색하는 경우.
작업자 판별: ORCA_ROLE 이 있으면 worker 만 작업자. 없으면 메인이 아니고, 연결 작업 공간(git-dir ≠ common-dir)이며, 폴더 이름이 허브(coord-*)가 아닌 세션.
  허브 아닌 연결 작업 공간은 위치와 무관하게 전부 작업자로 봄(~/orca/claude-config-wt/* 같은 수동 작업 공간 포함). Orca agent launch 는 환경변수를 못 넘겨 경로로 판별함(main_role.py 와 같은 사정)."""
import fnmatch
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import main_role  # noqa: E402
import main_send_guard  # noqa: E402

# ---- 설정 ----
TOKEN_NAME = "slack-bot.env"
TOKEN_RE = re.compile(r"slack-bot\.env(?!\.example)")  # 값 없는 양식 파일(.example)은 읽어도 됨
NOTIFY_SCRIPT = "slack_notify.sh"
HUB_DIR_NAME = "lrm-hubs"
HUB_DIR_RE = re.compile(r"^coord-.+")
FILE_TOOLS = {"Read", "Grep"}
WRAPPERS = {"sudo", "env", "exec", "command", "nohup", "time", "nice", "builtin", "caffeinate", "timeout"}
RUNNERS = {"source", "."} | main_send_guard.SHELLS
INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "php"}
# 내용을 출력·전달하지 않는 명령: 이름이 인자로 와도 통과
CONTENT_SAFE = {"ls", "stat", "test", "[", "[[", "file", "echo", "printf", "git", "bd", "gh", "orca", "wc", "touch", "mkdir", "rm", "chmod", "chown", "basename", "dirname", "realpath", "readlink", "open"}
COPIERS = {"cp", "mv", "install", "rsync", "scp", "ditto"}  # 마지막 인자는 목적지라 이름이어도 통과, 원본 인자만 검사
PATTERN_FIRST = {"grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "gawk"}  # 첫 위치 인자는 검색 패턴·프로그램
SEARCHERS = {"grep", "egrep", "fgrep", "rg"}
# 값 없는 grep 짧은 옵션 묶음 중 l(이름만)·L(안 맞는 이름만)을 포함한 것. 값을 받는 옵션(-e·-f·-m·-A 등)이 섞이면 판정하지 않음
GREP_NAME_FLAGS = re.compile(r"-[rRiIhHnsqwxvcEFoaz]*[lL][rRiIhHnsqwxvcEFoazlL]*")
VALUE_OPTS ={"-A", "-B", "-C", "-m", "-g", "-t", "-T", "-d", "-D", "-j", "--glob", "--type", "--max-count", "--include", "--exclude", "--exclude-dir", "--context"}
SAFE_NOTIFY_ARGS = {"--help", "-h", "--dry-run"}
DURATION_RE = re.compile(r"^\d+(\.\d+)?[smhd]?$")
WT_ADD_WORDS = ("worktree", "add")
REASON_WORKTREE = ("작업 공간을 직접 만들지 않음(git worktree add 거부) — 하위 작업 공간은 create_worker_ws.sh, 작업자 배정은 orca orchestration worker-start 로 만들어 부모 허브에 연결함. "
                   "Orca 밖 저장소는 orca worktree create 를 쓰거나 사용자가 직접 실행")
REASON_TOKEN = "봇 토큰 파일(slack-bot.env) 내용은 세션에서 읽지 않음 — 전송은 report_sender 등 전송 프로세스가 맡음. 파일 존재 확인은 test -f·ls 로"
REASON_NOTIFY = "작업자는 slack_notify.sh 를 직접 실행하지 않음 — 보고는 hub_report.py 로 outbox 에 남기면 전송기가 올림"


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}, ensure_ascii=False))


def hub_dir():
    return os.path.realpath(os.path.expanduser(os.environ.get("LRM_HUB_DIR") or f"~/.config/{HUB_DIR_NAME}"))


def expand(p):
    home = os.path.expanduser("~")
    return os.path.expanduser(p.replace("${HOME}", home).replace("$HOME", home))


def names_token(arg):
    """인자가 토큰 파일(와일드카드로 풀리는 경우 포함)을 가리키는지"""
    if TOKEN_RE.search(arg):
        return True
    d, b = os.path.split(expand(arg))
    return bool(d) and any(c in b for c in "*?[") and fnmatch.fnmatch(TOKEN_NAME, b) and os.path.realpath(d) == hub_dir()


def is_hub_dir(arg):
    return os.path.realpath(expand(arg)) == hub_dir() if arg and not arg.startswith("-") else False


def drop_heredocs(cmd):
    """줄 이음을 합치고 heredoc 본문을 지움. 본문 속 문구가 명령 단계로 읽히는 것을 막음"""
    out, end = [], None
    for line in cmd.replace("\\\n", " ").split("\n"):
        if end is not None:
            if (line.strip() if end[1] else line) == end[0]:
                end = None
            continue
        out.append(line)
        m = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
        if m and "<<<" not in line:
            end = (m.group(3), m.group(1) == "-")
    return "\n".join(out)


def git_dirs(cwd):
    out = []
    for flag in ("--git-dir", "--git-common-dir"):
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", flag], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        out.append(os.path.realpath(r.stdout.strip()))
    return out


def is_worker(ev):
    role = os.environ.get("ORCA_ROLE", "")
    if role:
        return role == "worker"
    cwd = ev.get("cwd") or ""
    if main_role.is_main(ev) or not os.path.isdir(cwd):
        return False
    dirs = git_dirs(cwd)
    if not dirs or dirs[0] == dirs[1]:
        return False  # 메인 체크아웃·저장소 밖
    top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
    return not HUB_DIR_RE.match(os.path.basename(top))


def base(w):
    return w.rsplit("/", 1)[-1]


def head(seg):
    """환경변수 대입·래퍼(timeout 의 시간 인자 포함)를 건너뛴 첫 실행 낱말 위치"""
    i = 0
    while i < len(seg) and (re.fullmatch(r"[A-Za-z_]\w*=.*", seg[i]) or base(seg[i]) in WRAPPERS):
        wrapped = base(seg[i]) == "timeout"
        i += 1
        while i < len(seg) and seg[i].startswith("-"):
            i += 1
        if wrapped and i < len(seg) and DURATION_RE.match(seg[i]):
            i += 1
    return i


def is_worktree_add(seg, i):
    if base(seg[i]) != "git":
        return False
    j = i + 1
    while j < len(seg) and seg[j].startswith("-"):
        j += 2 if seg[j] in ("-C", "-c") else 1
    return tuple(seg[j:j + 2]) == WT_ADD_WORDS


def is_notify_run(seg, i):
    shell_opts = []
    if base(seg[i]) in RUNNERS:
        i += 1
        while i < len(seg) and seg[i].startswith("-"):
            shell_opts.append(seg[i])
            i += 1
    if i >= len(seg) or base(seg[i]) != NOTIFY_SCRIPT:
        return False
    if any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", o) for o in shell_opts):
        return False  # bash -n 문법 검사는 실행이 아님
    args = seg[i + 1:]
    return not (args and all(a in SAFE_NOTIFY_ARGS for a in args))


def reads_token(seg, i):
    cmd, args = base(seg[i]), seg[i + 1:]
    if cmd in CONTENT_SAFE:
        return False
    if cmd in COPIERS:
        files = [a for a in args if not a.startswith("-")]
        return any(names_token(a) for a in files[:-1]) if len(files) > 1 else any(names_token(a) for a in files)
    files, pat_given, k = [], cmd not in PATTERN_FIRST, 0
    while k < len(args):
        a = args[k]
        if a in ("-e", "--regexp") and cmd in PATTERN_FIRST:
            pat_given, k = True, k + 2
        elif a in VALUE_OPTS:
            k += 2
        elif a.startswith("-") and a != "-":
            k += 1
        else:
            if not pat_given:
                pat_given = True  # 검색 패턴·프로그램: 이름이 나와도 파일을 여는 것이 아님
            else:
                files.append(a)
            k += 1
    # 이름만 내거나 토큰 파일을 빼는 검색은 폴더·와일드카드 대상이어도 통과. 토큰 파일을 이름으로 직접 주면 그대로 거부
    safe = cmd in SEARCHERS and (names_only(cmd, args) or skips_token(cmd, args))
    if cmd in PATTERN_FIRST and not safe and any(is_hub_dir(a) for a in files):
        return True  # 설정 폴더 통째 검색
    return any(TOKEN_RE.search(a) or (not safe and names_token(a)) for a in files)


def names_only(cmd, args):
    """파일 이름만 출력하는 검색인지(내용이 나오지 않음). rg 의 -L 은 링크 따라가기라 제외"""
    if any(a in ("--files-with-matches", "--files-without-match") for a in args):
        return True
    if cmd == "rg":
        return "-l" in args
    return any(GREP_NAME_FLAGS.fullmatch(a) for a in args)


def search_globs(cmd, args):
    """(제외 glob, 포함 glob) 목록. grep 은 --exclude·--include, rg 는 -g/--glob(! 로 시작하면 제외)"""
    excl, incl = [], []
    for k, a in enumerate(args):
        name, _, val = a.partition("=")
        if not val and k + 1 < len(args):
            val = args[k + 1]
        if cmd == "rg" and name in ("-g", "--glob"):
            (excl if val.startswith("!") else incl).append(val.lstrip("!"))
        elif cmd != "rg" and name == "--exclude":
            excl.append(val)
        elif cmd != "rg" and name == "--include":
            incl.append(val)
    return excl, incl


def skips_token(cmd, args):
    """제외 glob 이 토큰 파일을 빼거나, 포함 glob 이 토큰 파일을 고르지 않는지"""
    excl, incl = search_globs(cmd, args)
    return any(fnmatch.fnmatch(TOKEN_NAME, g) for g in excl) or (bool(incl) and not any(fnmatch.fnmatch(TOKEN_NAME, g) for g in incl))


def judge_bash(ev, cmd):
    clean = drop_heredocs(cmd)
    segs = main_send_guard.segments(clean)
    worker = None
    interp = False
    for seg in segs:
        i = head(seg)
        if i >= len(seg):
            continue
        if is_worktree_add(seg, i):
            return REASON_WORKTREE
        if reads_token(seg, i):
            return REASON_TOKEN
        interp = interp or base(seg[i]) in INTERPRETERS or base(seg[i]) in main_send_guard.SHELLS
        if NOTIFY_SCRIPT in " ".join(seg[i:]) and is_notify_run(seg, i):
            worker = is_worker(ev) if worker is None else worker
            if worker:
                return REASON_NOTIFY
    if interp and re.search(r"[/~]\S*" + TOKEN_RE.pattern, cmd):
        return REASON_TOKEN  # 인라인 코드·heredoc 스크립트가 경로로 여는 경우
    return None


def judge_file_tool(tool, ti):
    path = str(ti.get("file_path") or ti.get("path") or "")
    real = os.path.realpath(expand(path)) if path else ""
    if TOKEN_RE.search(path) or TOKEN_RE.search(real):
        return REASON_TOKEN
    if tool == "Grep" and real == hub_dir():
        glob = str(ti.get("glob") or "")
        if not glob or fnmatch.fnmatch(TOKEN_NAME, glob):
            return REASON_TOKEN  # 설정 폴더 통째 검색은 토큰 값을 출력할 수 있음
    return None


def judge(ev):
    tool = ev.get("tool_name") or ""
    ti = ev.get("tool_input") or {}
    if tool in FILE_TOOLS:
        return judge_file_tool(tool, ti)
    return judge_bash(ev, ti.get("command") or "")


def main():
    try:
        ev = json.load(sys.stdin)
        reason = judge(ev)
    except Exception as e:  # 훅 오류가 작업을 막지 않게 통과 처리
        print(f"ws-token-guard: 판정 실패 — 통과 처리 ({e})", file=sys.stderr)
        return
    if reason:
        deny(reason)


if __name__ == "__main__":
    main()
