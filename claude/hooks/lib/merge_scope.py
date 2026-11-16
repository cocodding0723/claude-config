"""명령 문자열에서 git 병합 조각(작업 디렉터리·대상·옵션)을 해석해 JSON 한 줄로 출력.
인자: 시작 cwd. stdin: 원본 명령. 따옴표·리디렉션·변수 해석은 push_targets 와 같은 규칙.
{"ok": 병합 외 조각이 전부 읽기 전용·이동·대입뿐인가, "pieces": [{"dir": 경로|null(외부 변수), "targets": [...], "flags": [...]}]}
ok=false 면 호출측이 허용 판정을 하지 않음. 병합 조각이 없으면 pieces=[]."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from push_targets import HOME, drop_heredoc, expand, resolve, segments  # noqa: E402

# 병합 조각과 한 명령에 둬도 되는 것: 이동·읽기 전용 git·출력 자르기. 그 밖은 호출측이 기존 판정으로 넘김
SAFE_GIT = {"status", "log", "diff", "show", "fetch", "rev-parse"}
SAFE_CMD = {"pwd", "true", "tail", "head", "cat"}


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.getcwd()
    here, svars, pieces, ok = cwd, {}, [], True
    for toks in segments(drop_heredoc(sys.stdin.read())):
        assigns = []
        while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] == "export"):
            if "=" in toks[0]:
                assigns.append(toks[0])
            toks = toks[1:]
        if not toks:
            for a in assigns:
                k, v = a.split("=", 1)
                v = expand(v, svars)
                if v is None or k.startswith("GIT_"):   # GIT_DIR 등은 대상 저장소를 바꿈
                    svars.pop(k, None)
                    ok = ok and not k.startswith("GIT_")
                else:
                    svars[k] = v
            continue
        if assigns or toks[0] == "env":
            ok = False   # 한 명령에만 붙는 환경 변수(GIT_DIR= 등)는 해석하지 않음
        if toks[0] == "cd":
            args = [t for t in toks[1:] if t not in ("-P", "-L", "--")]
            here = resolve(here, args[0], svars) if args else HOME
            continue
        if toks[0] in SAFE_CMD:
            continue
        if toks[0] != "git":
            ok = False
            continue
        d, k = here, 1
        while k < len(toks) and toks[k].startswith("-"):
            if toks[k] == "-C" and k + 1 < len(toks):
                d = resolve(d, toks[k + 1], svars)
                k += 2
            else:
                ok = False   # -c·--git-dir 등은 동작을 바꿀 수 있음
                k += 1
        sub = toks[k] if k < len(toks) else ""
        if sub in SAFE_GIT:
            continue
        if sub != "merge":
            ok = False
            continue
        flags = [t for t in toks[k + 1:] if t.startswith("-")]
        targets = [t for t in toks[k + 1:] if not t.startswith("-")]
        pieces.append({"dir": d, "targets": targets, "flags": flags})
    print(json.dumps({"ok": ok, "pieces": pieces}, ensure_ascii=False))


if __name__ == "__main__":
    main()
