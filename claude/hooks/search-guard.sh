#!/usr/bin/env bash
# PreToolUse(Bash): 루트(/)·홈 전체를 대상으로 하는 find·bfs·fd·grep -r 차단. 저장소·작업 공간 등 하위 폴더 지정은 허용.
# 근거: 홈·루트 전수 검색은 수 분 걸리고 디스크 I/O 를 몰아 다른 작업(Unity·Orca)을 멈춘다.
# stdin: hook JSON. stdout: deny JSON (또는 없음 = 의견 없음). 파싱 실패 시 통과(검사 못 하면 막지 않음).
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null)
cwd=$(printf '%s' "$input" | jq -r '.cwd // ""' 2>/dev/null)
printf '%s' "$cmd" | grep -Eq '(^|[^[:alnum:]_.-])(find|bfs|fd|fdfind|grep|egrep|fgrep)([[:space:]]|$)' || exit 0   # 대상 명령이 없으면 즉시 통과

# 파이썬 본문은 $( ) 밖 heredoc 으로 받음(bash 3.2 가 $( ) 안 heredoc 의 백틱을 잘못 해석함)
read -r -d '' PYSRC <<'PY'
import os, shlex, sys

home = os.path.normpath(os.environ.get("HOME_DIR", "") or "/nonexistent-home")
cwd = os.environ.get("CWD", "") or os.getcwd()
cmd = os.environ["CMD"]

# 값을 받는 옵션(위치 인자와 구분용)
FD_VAL = {"-e", "--extension", "-t", "--type", "-E", "--exclude", "-d", "--max-depth", "--min-depth", "--exact-depth", "-S", "--size",
          "-c", "--color", "-j", "--threads", "-o", "--owner", "--changed-within", "--changed-before", "--ignore-file", "-g0"}
GREP_VAL = {"-e", "-f", "-m", "-A", "-B", "-C", "--include", "--exclude", "--exclude-dir", "--include-dir", "-d", "--binary-files",
            "--label", "--color", "--colour", "--max-count", "--file", "--regexp", "--context", "--after-context", "--before-context"}
FIND_FLAGS = {"-H", "-L", "-P", "-E", "-X", "-x", "-s", "-d"}   # find·bfs 앞쪽 전역 옵션(경로 아님)


def broad(path):
    """루트·홈 전체(또는 그 바로 아래 전체 글롭)를 가리키는 경로인지."""
    p = path
    if p == "~" or p.startswith("~/"):
        p = home + p[1:]
    p = p.replace("${HOME}", home).replace("$HOME", home)
    if "$" in p or "`" in p:
        return False   # 값을 알 수 없는 변수는 판정 안 함
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    for glob in ("/*", "/.*"):
        if p.endswith(glob):
            p = p[: -len(glob)] or "/"
    p = os.path.normpath(p)
    return p == "/" or p == "/Users" or (home != "" and p == home)


def segments(tokens):
    seg = []
    for t in tokens:
        if t in {";", "&&", "||", "|", "&", "(", ")", "|&", "\n"}:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)
    if seg:
        yield seg


def command_word(seg):
    i = 0
    while i < len(seg):
        t = seg[i]
        if "=" in t and not t.startswith("-") and t.split("=")[0].replace("_", "").isalnum():
            i += 1
        elif t in {"sudo", "command", "time", "nice", "exec", "env"}:
            i += 1
        else:
            break
    return (os.path.basename(seg[i]), seg[i + 1:]) if i < len(seg) else ("", [])


def find_paths(args):
    out, i = [], 0
    while i < len(args):
        t = args[i]
        if t == "-f" and i + 1 < len(args):   # BSD find -f 경로
            out.append(args[i + 1]); i += 2; continue
        if t in FIND_FLAGS:
            i += 1; continue
        if t.startswith("-") or t in {"(", "!"}:
            break
        out.append(t); i += 1
    return out


def fd_paths(args):
    pos, out, i = [], [], 0
    while i < len(args):
        t = args[i]
        if t == "--search-path" and i + 1 < len(args):
            out.append(args[i + 1]); i += 2; continue
        if t.startswith("--search-path="):
            out.append(t.split("=", 1)[1]); i += 1; continue
        if t in FD_VAL:
            i += 2; continue
        if t == "--":
            pos.extend(args[i + 1:]); break
        if t.startswith("-") and len(t) > 1:
            i += 1; continue
        pos.append(t); i += 1
    return out + pos[1:]   # 첫 위치 인자는 패턴


def grep_info(args):
    recursive, have_e, pos, i = False, False, [], 0
    while i < len(args):
        t = args[i]
        if t == "--":
            pos.extend(args[i + 1:]); break
        if t in {"--recursive", "--dereference-recursive"} or (t == "-d" and i + 1 < len(args) and args[i + 1] == "recurse"):
            recursive = True
        elif t.startswith("-") and not t.startswith("--") and len(t) > 1:
            if any(c in t[1:] for c in "rR"):
                recursive = True
        if t in {"-e", "-f", "--regexp", "--file"}:
            have_e = True
        if t in GREP_VAL:
            i += 2; continue
        if t.startswith("-") and len(t) > 1:
            i += 1; continue
        pos.append(t); i += 1
    paths = pos if have_e else pos[1:]
    return recursive, paths


try:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    tokens = list(lex)
except ValueError:
    sys.exit(0)

for seg in segments(tokens):
    name, args = command_word(seg)
    if name in {"find", "bfs"}:
        targets = find_paths(args); label = name
    elif name in {"fd", "fdfind"}:
        targets = fd_paths(args); label = name
    elif name in {"grep", "egrep", "fgrep"}:
        rec, targets = grep_info(args)
        if not rec:
            continue
        label = name + " -r"
    else:
        continue
    if not targets:
        targets = ["."]   # 경로 생략 = 현재 폴더
    for t in targets:
        if broad(t):
            print("%s 의 검색 대상 '%s' 은 루트·홈 전체입니다" % (label, t))
            sys.exit(0)
PY
reason=$(HOME_DIR="${HOME:-}" CMD="$cmd" CWD="$cwd" python3 -I -c "$PYSRC")
[ -n "$reason" ] || exit 0
jq -cn --arg r "$reason. 수 분 걸리고 디스크를 몰아쓰니 저장소·작업 공간 폴더를 지정하세요(예: find ~/orca/workspaces/<저장소> …). 파일 이름만 찾으면 mdfind, 저장소 안이면 git ls-files·git grep 사용." \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
