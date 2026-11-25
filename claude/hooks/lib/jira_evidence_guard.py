"""Jira 댓글의 로컬 경로 검사(PreToolUse). stdin: hook JSON. 거부·안내면 hook JSON 한 줄, 통과면 빈 출력.
자료는 Slack 으로 보내므로 첨부 여부는 보지 않음(사용자 11-25). 글 품질 검사라 실행 오류는 통과로 둠."""
import json
import os
import re
import sys
from html import unescape

TOOLS = ("mcp__atlassian__", "mcp__claude_ai_Atlassian_MCP__")  # 로컬 MCP · claude.ai 커넥터
COMMENT = "addOrEditJiraIssueComment"
ANY_PROJECT = "*"
DENY, WARN = "deny", "warn"
# 훅과 같은 저장소에서 읽어, 표를 바꾸려면 병합을 거치게 함
TABLE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "hubs", "jira_evidence.tsv")
LOCAL = re.compile(r"(?:file://|/(?:Users|home)/|/private/tmp/|~/(?:Documents|orca|Downloads|Desktop))")


def mode_of(project):
    rows = {}
    try:
        with open(TABLE, encoding="utf-8") as f:
            for ln in f:
                c = [x.strip() for x in ln.split("\t")]
                if not ln.startswith("#") and len(c) >= 2 and c[0]:
                    rows[c[0].upper()] = c[1]
    except OSError:
        pass
    return rows.get(project, rows.get(ANY_PROJECT, "off"))


def plain_lines(body):
    text = re.sub(r"</(p|li|h\d|div|tr)>|<br\s*/?>", "\n", body)
    return [ln.strip() for ln in unescape(re.sub(r"<[^>]+>", "", text)).splitlines() if ln.strip()]


def problem(inp):
    """고칠 것 한 줄. 문제없으면 ''"""
    if any(LOCAL.search(ln) for ln in plain_lines(inp.get("commentBody") or "")):
        return "로컬 경로는 팀원이 열 수 없음 — 경로를 빼고, 자료는 Slack 으로 보냄(QA 확인용 전후 화면·영상만 티켓 첨부)"
    return ""


def main():
    h = json.load(sys.stdin)
    tool, inp = h.get("tool_name", ""), h.get("tool_input") or {}
    prefix = next((p for p in TOOLS if tool.startswith(p)), None)
    if prefix is None:
        return
    name = tool[len(prefix):]
    if name == "executeWrite":  # discover 경유
        name, inp = inp.get("name", ""), inp.get("inputs") or {}
    if name != COMMENT:
        return
    m = re.search(r"([A-Z][A-Z0-9_]*)-\d+", str(inp.get("issueIdOrKey") or "").upper())
    mode = mode_of(m.group(1) if m else "")
    why = problem(inp) if mode in (DENY, WARN) else ""
    if not why:
        return
    out = {"hookEventName": "PreToolUse"}
    if mode == DENY:
        out.update(permissionDecision="deny", permissionDecisionReason="Jira 댓글 검사: " + why)
    else:
        out["additionalContext"] = "Jira 댓글 검사(안내): " + why
    print(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, AttributeError, TypeError):
        pass
