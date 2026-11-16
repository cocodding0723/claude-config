"""계획 승인 기록(~/.config/lrm-hubs/plan-approvals/*.json)·Slack 답 원장(slack-inbox.jsonl) 쓰기 판정.
승인 기록은 메인만 씀. 원장은 Slack 수신기(launchd)만 쓰므로 모든 Claude 세션에서 거부(승인 기록의 인용 대조 근거라서).
메인 판별: 환경변수 ORCA_ROLE=main(메인 세션 시작 때 설정). 다른 세션의 Write·Edit·Bash 쓰기는 거부.
Bash 는 명령에 plan-approvals 와 쓰기 표시(리다이렉트·cp·mv·rm·sed -i·python 등)가 함께 있으면 쓰기로 봄. 변수로 경로를 숨기면 못 잡음.
stdin: hook JSON. 출력: 거부면 hook JSON 한 줄, 통과면 빈 출력."""
import json
import os
import re
import sys

DIR = "plan-approvals"
INBOX = "slack-inbox.jsonl"
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WRITE_MARK = re.compile(r"(?<![0-9&])>|\b(tee|cp|mv|rm|ln|touch|mkdir|install|dd|chmod|truncate|python3?|perl|ruby|node)\b|\bsed\s+(-[a-zA-Z]*i|--in-place)")
INBOX_REASON = "slack-inbox.jsonl 은 Slack 수신기만 씀 — 계획 승인 인용 대조의 근거라 세션에서 고치지 않음. 시험은 임시 HOME 에서"
REASON = ("계획 승인 기록은 메인 코디네이터만 씀 — 사용자 컨펌 원문을 메인에 전달하면 메인이 "
          "~/.config/lrm-hubs/plan-approvals/<plan-id>.json 에 기록함. 메인 세션이면 ORCA_ROLE=main 으로 시작할 것")


def targets(tool, inp, name):
    if tool in FILE_TOOLS:
        p = (inp.get("file_path") or inp.get("notebook_path") or "").replace("\\", "/")
        return name in p.split("/") if name == DIR else os.path.basename(p) == name
    if tool == "Bash":
        c = inp.get("command") or ""
        return name in c and bool(WRITE_MARK.search(c))
    return False


def main():
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return
    tool, inp = ev.get("tool_name", ""), ev.get("tool_input") or {}
    reason = ""
    if targets(tool, inp, INBOX):
        reason = INBOX_REASON
    elif os.environ.get("ORCA_ROLE") != "main" and targets(tool, inp, DIR):
        reason = REASON
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
