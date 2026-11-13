#!/usr/bin/env python3
"""시험 보조: 결정 카드 필수 항목(배경·용어·선택지 결과)이 없는 옛 시험 호출에 자리표시 값을 채워 slack_notify.sh 에 넘김.
양식 외 동작을 보는 시험이 필수 항목 때문에 막히지 않게 함. DECIDE_RAW=1 이면 인자를 그대로 넘김(필수 항목 거부 시험용)."""
import os
import re
import subprocess
import sys

WITH_VALUE = {"--tag", "--title", "--thread", "--kind", "--choices", "--emoji-set", "--options", "--recommend", "--reversible",
              "--decision-id", "--seq", "--todo-id", "--attached", "--link-to", "--quote", "--to-user"}
FILL = "\n배경: 시험용 배경 설명 문장입니다\n용어: 없음"


def main(argv: list) -> int:
    script, args = argv[0], argv[1:]
    if os.environ.get("DECIDE_RAW") != "1" and "--kind" in args and args[args.index("--kind") + 1:][:1] == ["decide"]:
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--options" and i + 1 < len(args) and "=>" not in args[i + 1]:
                args[i + 1] = re.sub(r"(,[ \t]*(?=[0-9]+[ \t]*=)|$)", " => 시험 결과\\1", args[i + 1]) if args[i + 1] else args[i + 1]
            if a in WITH_VALUE:
                i += 2
            elif a.startswith("-") and a != "--":
                i += 1
            else:
                if "배경:" not in args[i]:
                    first, sep, rest = args[i].partition("\n")
                    args[i] = first + FILL + (sep + rest if sep else "")
                break
    return subprocess.call(["bash", script, *args])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
