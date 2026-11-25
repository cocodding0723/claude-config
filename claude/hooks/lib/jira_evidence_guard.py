"""Jira 댓글의 자료 검사(PreToolUse). stdin: hook JSON. 거부·안내면 hook JSON 한 줄, 통과면 빈 출력.
완료·녹화·구조안 결과를 적은 댓글에 열어 볼 자료가 없으면 막아, 자료가 로컬에만 남는 일을 줄임(사용자 11-25).
통과 조건: 본문에 넣은 그림·영상, 문서 링크, '자료:' 줄의 첨부 파일 이름, '자료 없음: 이유' 중 하나.
티켓에 그 파일이 실제로 있는지는 조회하지 않음. 글 품질 검사라 실행 오류는 통과로 둠."""
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
CLAIM = re.compile(r"(수정|구현|병합|구조안|설계|문서|도식|시안|녹화|촬영|캡처)[^\n.]{0,12}(완료|마쳤|끝냈|확정|했다|했습니다)"
                   r"|(녹화|영상|캡처|스크린샷)[^\n.]{0,20}(확인했|확인함|확인됐|확인된|보였)")
NOT_YET = re.compile(r"남은 일|아직|예정|대기 중|진행 중이|못 ?했|못했|하지 않|않았|없었|→")
EMBED = re.compile(r"data-type=[\"']media[\"']")
MEDIA_URL = re.compile(r"https?://[^\s<>\"')\]]+")
NOT_EVIDENCE = re.compile(r"/browse/[A-Z]|github\.com/[^/]+/[^/]+/(pull|commit)/")  # 티켓·PR·커밋 링크는 화면·도식을 대신하지 못함
FILE = re.compile(r"[^\s/\\]+\.(png|jpe?g|gif|webp|svg|mp4|mov|webm|mp3|wav|pdf|html|puml)\b", re.I)
MATERIAL = re.compile(r"^\W*자료\W*[:：]\W*(.+)$")
NO_MATERIAL = re.compile(r"^\W*자료 없음\W*[:：]\W*(\S.{4,})$")
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


def has_evidence(body, lines, inp):
    if inp.get("inlineFileId") or EMBED.search(body):
        return True
    if any(not NOT_EVIDENCE.search(u) for u in MEDIA_URL.findall(body)):
        return True
    for ln in lines:
        m = MATERIAL.match(ln)
        if NO_MATERIAL.match(ln) or (m and FILE.search(m.group(1))):
            return True
    return False


def problem(inp):
    """고칠 것 한 줄. 문제없으면 ''"""
    body = inp.get("commentBody") or ""
    lines = plain_lines(body)
    if any(LOCAL.search(ln) for ln in lines):
        return "로컬 경로는 팀원이 열 수 없음 — 파일을 티켓에 첨부하고 파일 이름으로 바꿈"
    claim = next((ln for ln in lines if CLAIM.search(ln) and not NOT_YET.search(ln)), "")
    if not claim or has_evidence(body, lines, inp):
        return ""
    return (f"'{claim[:40]}' 에 열어 볼 자료가 없음 — 화면·영상·도식을 본문에 넣거나, 문서 링크를 걸거나, 티켓에 첨부한 뒤 "
            "'자료: 파일이름.mp4(17초, 볼 곳)' 을 적음. 보일 것이 없으면 '자료 없음: 이유'")


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
        out.update(permissionDecision="deny", permissionDecisionReason="Jira 자료 검사: " + why)
    else:
        out["additionalContext"] = "Jira 자료 검사(안내): " + why
    print(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, AttributeError, TypeError):
        pass
