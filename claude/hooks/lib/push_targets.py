"""명령 문자열에서 git push(인자 2 로 다른 하위 명령 지정 가능) 대상(작업 디렉터리·원격·refspec)을 해석해 JSON 한 줄씩 출력.
cfg = git -c·--config-env 로 준 설정('키=값'), env = 앞서 지정한 환경 변수('이름=값'). 따옴표를 푼 값이라 우회 검사에 씀.
인자: 시작 cwd. stdin: 원본 명령. 따옴표 안 문구·heredoc 본문의 push 글자는 무시.
cd <dir>·git -C <dir> 로 바뀐 디렉터리를 따라감. 같은 명령 안 'W=리터럴' 대입은 $W·${W} 로 풀어 씀.
해석 불가(미정 변수·치환 포함 경로 등)는 {"err": 사유}. 리디렉션(2>&1·>/dev/null)과 그 대상은 인자가 아니므로 버림."""
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
VAL_FLAGS = {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}
SEP = set(";&|\n()")


def drop_heredoc(cmd):
    out, end = [], None
    for line in cmd.split("\n"):
        if end is not None:
            if (line.strip() if end[1] else line) == end[0]:
                end = None
            continue
        out.append(line)
        m = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
        if m and "<<<" not in line:
            end = (m.group(3), m.group(1) == "-")
    return "\n".join(out)


def segments(cmd):
    """따옴표를 존중해 구분자(; & | 줄바꿈 괄호)로 나눈 토큰 목록들."""
    segs, toks, cur, has, i, n = [], [], [], False, 0, len(cmd)

    def flush_tok():
        nonlocal cur, has
        if has:
            toks.append("".join(cur))
        cur, has = [], False

    def flush_seg():
        nonlocal toks
        flush_tok()
        if toks:
            segs.append(toks)
        toks = []

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            cur.append(cmd[i + 1]); has = True; i += 2; continue
        if c in "'\"":
            j = i + 1
            while j < n and cmd[j] != c:
                j += 2 if (c == '"' and cmd[j] == "\\") else 1
            body = cmd[i + 1:j]
            if c == '"':
                body = re.sub(r"\\(.)", r"\1", body)
            cur.append(body); has = True; i = j + 1; continue
        if c in "<>" or (c == "&" and cmd[i + 1:i + 2] == ">"):
            # 리디렉션: 앞 fd 숫자(2>)는 버리고 대상 단어까지 소비. 'foo>x' 의 foo 는 유지
            if has and "".join(cur).isdigit():
                cur, has = [], False
            else:
                flush_tok()
            j = i
            while j < n and cmd[j] in "&<>":
                j += 1
            while j < n and cmd[j] in " \t":
                j += 1
            while j < n and cmd[j] not in " \t\n;|&()<>":
                if cmd[j] in "'\"":
                    k = cmd.find(cmd[j], j + 1)
                    j = n if k < 0 else k
                j += 1
            i = j
            continue
        if c in SEP:
            flush_seg(); i += 1; continue
        if c in " \t":
            flush_tok(); i += 1; continue
        if c == "#" and not has:
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        cur.append(c); has = True; i += 1
    flush_seg()
    return segs


VAR_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def expand(path, env=None):
    """~·$HOME·같은 명령 안에서 리터럴로 대입한 변수를 풀어 씀. 미정 변수·치환($( )·백틱)은 None."""
    if path == "~" or path.startswith("~/"):
        path = HOME + path[1:]
    known = dict(env or {}, HOME=HOME)
    miss = []

    def sub(m):
        v = known.get(m.group(1) or m.group(2))
        if v is None:
            miss.append(1)
            return ""
        return v

    path = VAR_RE.sub(sub, path)
    if miss or "$" in path or "`" in path:
        return None
    return path


def resolve(base, path, env=None):
    if base is None:
        return None
    p = expand(path, env)
    if p is None:
        return None
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p))


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


SHELLS = {"bash", "sh", "zsh"}
WRAPPERS = {"export", "env", "command", "exec", "nohup", "time"}


def shell_payload(toks):
    """bash -c '<명령>' 의 명령 문자열. -c 없으면 None"""
    for i, t in enumerate(toks[1:], 1):
        if t.startswith("-") and not t.startswith("--") and "c" in t and i + 1 < len(toks):
            return toks[i + 1]
    return None


def scan(cmd, here, envs, svars, sub="push"):
    """cmd 한 덩어리를 훑어 push 대상 출력. 해석 불가면 err 출력 후 False. bash -c '…' 는 하위 셸이라 cd 가 밖으로 안 번짐"""
    for toks in segments(drop_heredoc(cmd)):
        assigns = []
        while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in WRAPPERS):
            if "=" in toks[0]:
                envs.append(toks[0])
                assigns.append(toks[0])
            toks = toks[1:]
        if not toks:
            for a in assigns:   # 대입만 있는 조각은 이후 조각에 남는 셸 변수
                k, v = a.split("=", 1)
                v = expand(v, svars)
                if v is None:
                    svars.pop(k, None)
                else:
                    svars[k] = v
            continue
        if toks[0] == "cd":
            args = [t for t in toks[1:] if t not in ("-P", "-L", "--")]
            r = resolve(here, args[0], svars) if args else HOME
            if r is None:
                emit({"err": "cd 경로에 변수·치환: " + args[0]}); return False
            here = r
            continue
        if toks[0] in SHELLS:
            inner = shell_payload(toks)
            if inner is not None and not scan(inner, here, envs, dict(svars), sub):
                return False
            continue
        if os.path.basename(toks[0]) != "git":
            continue
        d, k, cfg = here, 1, []
        while k < len(toks) and toks[k].startswith("-"):
            t = toks[k]
            if t == "-C" and k + 1 < len(toks):
                r = resolve(d, toks[k + 1], svars)
                if r is None:
                    emit({"err": "-C 경로에 변수·치환: " + toks[k + 1]}); return False
                d, k = r, k + 2
            elif t in ("-c", "--config-env") and k + 1 < len(toks):
                cfg.append(toks[k + 1]); k += 2
            elif t.startswith("--config-env=") or (t.startswith("-c") and not t.startswith("--") and len(t) > 2):
                cfg.append(t.split("=", 1)[1] if t.startswith("--") else t[2:]); k += 1
            elif t in ("--exec-path", "--namespace", "--git-dir", "--work-tree") and k + 1 < len(toks):
                k += 2
            else:
                k += 1
        if k >= len(toks) or toks[k] != sub:
            continue
        flags, pos, k = [], [], k + 1
        while k < len(toks):
            t = toks[k]
            if t == "--":
                pos += toks[k + 1:]; break
            if t.startswith("-") and t != "-":
                flags.append(t)
                if t in VAL_FLAGS and k + 1 < len(toks):
                    k += 1
            else:
                pos.append(t)
            k += 1
        emit({"dir": d, "flags": flags, "remote": pos[0] if pos else "", "refs": pos[1:], "cfg": cfg, "env": envs})
    return True


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.getcwd()
    scan(sys.stdin.read(), cwd, [], {}, sys.argv[2] if len(sys.argv) > 2 else "push")


if __name__ == "__main__":
    main()
