#!/bin/bash
# PreToolUse(Bash): 슬랙 전송 스크립트 호출을 양식 A 로 강제. 실제 실행 형태만 검사함.
#   실행 형태 = 명령 첫 토큰(앞의 VAR=값·env·time 등 건너뜀), bash/sh/zsh [-옵션] 경로, source/. 경로, bash -c '…' 안쪽, $(…)·`…` 안쪽
#   grep·cat·heredoc·인용 문자열·커밋 메시지·에이전트 지시문에 이름이 들어간 것은 실행이 아니라 통과
# 차단: --kind 누락, --no-check, LRM_FORMAT=off|warn, --kind decide 에 --choices·--options 둘 다 없음. --help·-h 와 bash -n 은 통과
# 11-09 확정 양식을 세션마다 잊는 문제를 메모 대신 훅으로 막음.
cmd=$(jq -r '.tool_input.command // ""' 2>/dev/null)
case "$cmd" in *slack_notify.sh*) ;; *) exit 0 ;; esac

guide="양식 A(~/.config/lrm-hubs/docs/slack-format.md): 첫 줄 주제 → 빈 줄 → • 무엇/• 왜/• 결과 → 👉 답: → 참고: 줄."
reason=$(printf '%s' "$cmd" | GUIDE="$guide" python3 -c '
import os, re, sys

SCRIPT = "slack_notify.sh"
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
SKIP = {"env", "command", "exec", "nohup", "time", "builtin", "{", "!", "if", "then", "else", "elif", "do", "while", "until"}
ASSIGN = re.compile(r"[A-Za-z_]\w*=")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(?:\x27([^\x27\n]+)\x27|\"([^\"\n]+)\"|\\?([A-Za-z_]\w*))")
MAX_DEPTH = 3
guide = os.environ["GUIDE"]

def strip_heredocs(s):
    out, ends = [], []
    for ln in s.split("\n"):
        if ends:
            if ln.strip() == ends[0]:
                ends.pop(0)
            continue
        out.append(ln)
        ends += [m.group(1) or m.group(2) or m.group(3) for m in HEREDOC.finditer(ln)]
    return "\n".join(out)

def split_cmds(s):
    """따옴표·$(…)·백틱을 해석해 단순 명령(단어 목록)별로 나눔. 인용 문자열은 한 단어로 유지"""
    cmds, words, cur, stack, mode, i, n = [], [], None, [], "n", 0, len(s)
    def end_word():
        nonlocal cur
        if cur is not None:
            words.append(cur)
            cur = None
    def end_cmd():
        nonlocal words
        end_word()
        if words:
            cmds.append(words)
            words = []
    while i < n:
        c = s[i]
        if mode == "s":
            if c == "\x27":
                mode = "n"
            else:
                cur += c
        elif mode == "d":
            if c == "\\" and i + 1 < n:
                cur += s[i + 1]; i += 1
            elif c == "\"":
                mode = "n"
            elif c == "$" and s[i + 1:i + 2] == "(":
                end_cmd(); stack.append(("d", ")")); mode = "n"; i += 1
            elif c == "`":
                end_cmd(); stack.append(("d", "`")); mode = "n"
            else:
                cur += c
        else:
            if c == "\\" and i + 1 < n:
                if s[i + 1] == "\n":
                    end_word()
                else:
                    cur = (cur or "") + s[i + 1]
                i += 1
            elif c == "\x27":
                cur = cur or ""; mode = "s"
            elif c == "\"":
                cur = cur or ""; mode = "d"
            elif c in " \t":
                end_word()
            elif c == "#" and cur is None:
                while i < n and s[i] != "\n":
                    i += 1
                continue
            elif c in ";|&\n(":
                end_cmd()
            elif c == "$" and s[i + 1:i + 2] == "(":
                end_cmd(); stack.append(("n", ")")); i += 1
            elif c == ")":
                end_cmd()
                if stack and stack[-1][1] == ")":
                    mode = stack.pop()[0]
            elif c == "`":
                end_cmd()
                if stack and stack[-1][1] == "`":
                    mode = stack.pop()[0]
                else:
                    stack.append(("n", "`"))
            else:
                cur = (cur or "") + c
        i += 1
    end_cmd()
    return cmds

def is_script(w):
    return os.path.basename(w) == SCRIPT

def invocations(s, depth=0):
    """(인자 목록, 앞에 붙은 VAR=값 목록) 들. 전송 스크립트를 실제로 실행하는 명령만"""
    found = []
    if depth > MAX_DEPTH:
        return found
    for words in split_cmds(strip_heredocs(s)):
        i, assigns = 0, []
        while i < len(words) and (ASSIGN.match(words[i]) or words[i] in SKIP):
            if ASSIGN.match(words[i]):
                assigns.append(words[i])
            i += 1
        if i >= len(words):
            continue
        w, rest = words[i], words[i + 1:]
        if is_script(w):
            found.append((rest, assigns))
        elif os.path.basename(w) in SHELLS or w in (".", "source"):
            j = 0
            while j < len(rest) and rest[j].startswith("-") and rest[j] != "--":
                if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", rest[j]) and j + 1 < len(rest):
                    found += invocations(rest[j + 1], depth + 1)
                    j = len(rest)
                    break
                if rest[j] == "-n":
                    j = len(rest)
                    break
                j += 1
            if j < len(rest) and is_script(rest[j]):
                found.append((rest[j + 1:], assigns))
    return found

def check(args, assigns, all_words):
    if "--help" in args or "-h" in args:
        return ""
    if "--kind" not in args:
        return SCRIPT + " 에 --kind <decide|todo|done|progress|warn|doc> 필요. " + guide
    if "--no-check" in args:
        return "--no-check 금지. 양식을 고쳐서 다시 보낼 것. " + guide
    if any(re.fullmatch(r"LRM_FORMAT=(off|warn)", w) for w in assigns + all_words):
        return "LRM_FORMAT 은 strict 만 허용. " + guide
    k = args.index("--kind")
    if k + 1 < len(args) and args[k + 1] == "decide" and "--choices" not in args and "--options" not in args:
        return "--kind decide 는 이모지 선택지를 위해 --choices 2~4 또는 --options 필요. 결정 1건 = 메시지 1개. " + guide
    return ""

src = sys.stdin.read()
inv = invocations(src)
if inv:
    all_words = [w for ws in split_cmds(strip_heredocs(src)) for w in ws]
    for args, assigns in inv:
        r = check(args, assigns, all_words)
        if r:
            print(r)
            break
' 2>/dev/null)

[ -n "$reason" ] || exit 0
jq -n --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
