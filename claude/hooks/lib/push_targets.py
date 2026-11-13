"""명령 문자열에서 git push 대상(작업 디렉터리·원격·refspec)을 해석해 JSON 한 줄씩 출력.
cfg = git -c·--config-env 로 준 설정('키=값'), env = 앞서 지정한 환경 변수('이름=값'). 따옴표를 푼 값이라 우회 검사에 씀.
인자: 시작 cwd. stdin: 원본 명령. 따옴표 안 문구·heredoc 본문의 push 글자는 무시.
cd <dir>·git -C <dir> 로 바뀐 디렉터리를 따라감. 해석 불가(변수·치환 포함 경로 등)는 {"err": 사유}."""
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


def expand(path):
    if path == "~" or path.startswith("~/"):
        path = HOME + path[1:]
    path = path.replace("${HOME}", HOME).replace("$HOME", HOME)
    if "$" in path or "`" in path:
        return None
    return path


def resolve(base, path):
    p = expand(path)
    if p is None:
        return None
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p))


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.getcwd()
    here, envs = cwd, []
    for toks in segments(drop_heredoc(sys.stdin.read())):
        while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in ("export", "env")):
            if "=" in toks[0]:
                envs.append(toks[0])
            toks = toks[1:]
        if not toks:
            continue
        if toks[0] == "cd":
            args = [t for t in toks[1:] if t not in ("-P", "-L", "--")]
            r = resolve(here, args[0]) if args else HOME
            if r is None:
                emit({"err": "cd 경로에 변수·치환: " + args[0]}); return
            here = r
            continue
        if toks[0] != "git":
            continue
        d, k, cfg = here, 1, []
        while k < len(toks) and toks[k].startswith("-"):
            t = toks[k]
            if t == "-C" and k + 1 < len(toks):
                r = resolve(d, toks[k + 1])
                if r is None:
                    emit({"err": "-C 경로에 변수·치환: " + toks[k + 1]}); return
                d, k = r, k + 2
            elif t in ("-c", "--config-env") and k + 1 < len(toks):
                cfg.append(toks[k + 1]); k += 2
            elif t.startswith("--config-env=") or (t.startswith("-c") and not t.startswith("--") and len(t) > 2):
                cfg.append(t.split("=", 1)[1] if t.startswith("--") else t[2:]); k += 1
            elif t in ("--exec-path", "--namespace") and k + 1 < len(toks):
                k += 2
            else:
                k += 1
        if k >= len(toks) or toks[k] != "push":
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


if __name__ == "__main__":
    main()
