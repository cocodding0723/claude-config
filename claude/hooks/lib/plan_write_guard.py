"""계획 승인 기록(~/.config/lrm-hubs/plan-approvals/*.json) 쓰기 판정. 기록은 사용자 컨펌을 옮긴 승인 근거라 메인만 씀.
메인 판별: 환경변수 ORCA_ROLE=main(메인 세션 시작 때 설정). 다른 세션의 Write·Edit·Bash 쓰기는 거부.
Bash 는 명령에 plan-approvals 와 쓰기 표시(리다이렉트·cp·mv·rm·sed -i·python 등)가 함께 있으면 쓰기로 봄. 변수로 경로를 숨기면 못 잡음.
stdin: hook JSON. 출력: 거부면 hook JSON 한 줄, 통과면 빈 출력."""
import json
import os
import re
import sys

DIR = "plan-approvals"
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WRITE_MARK = re.compile(r"(?<![0-9&])>|\b(tee|cp|mv|rm|ln|touch|mkdir|install|dd|chmod|truncate|python3?|perl|ruby|node)\b|\bsed\s+(-[a-zA-Z]*i|--in-place)")
REASON = ("계획 승인 기록은 메인 코디네이터만 씀 — 사용자 컨펌 원문을 메인에 전달하면 메인이 "
          "~/.config/lrm-hubs/plan-approvals/<plan-id>.json 에 기록함. 메인 세션이면 ORCA_ROLE=main 으로 시작할 것")


def targets(tool, inp):
    if tool in FILE_TOOLS:
        p = inp.get("file_path") or inp.get("notebook_path") or ""
        return DIR in p.replace("\\", "/").split("/")
    if tool == "Bash":
        c = inp.get("command") or ""
        return DIR in c and bool(WRITE_MARK.search(c))
    return False


def main():
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return
    if os.environ.get("ORCA_ROLE") == "main":
        return
    if targets(ev.get("tool_name", ""), ev.get("tool_input") or {}):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": REASON}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
