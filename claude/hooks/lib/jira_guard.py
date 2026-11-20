"""Jira 쓰기 판정(jira-approval-guard.sh 가 부름). stdin: hook JSON.
규칙: 이슈 상태 변경(transition, edit 의 status 필드)만 사용자 승인 기록 뒤에(사용자 11-16).
단, 되돌리기 쉬운 전환(hubs/jira_free_transitions.tsv: 진행 중·해야 할 일)은 승인 없이 통과(사용자 11-20). 일을 닫는 전환은 그대로 승인 필요.
승인 기록 = 결정 카드 D-JST<이슈 키 영숫자> 의 가장 최근 답이 1번 또는 approve, 또는 계획 승인.
이슈 발급·링크·에픽 지정은 승인 없이 통과(사용자 11-17 "jira 티켓 발급 그냥 자동화하자").
개인 업무 발급 금지는 역할 파일 문구로만 둠: 훅 입력(cwd·세션)에 개인/게임 구분 근거가 없어 코드로 거부하지 않음.
출력: deny|ask hook JSON 한 줄, 통과면 빈 출력. 기록을 못 읽으면 ask."""
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import pr_guard as pg  # noqa: E402  승인 기록 조회 재사용
import plan_approval as pa  # noqa: E402

TOOLS = ("mcp__atlassian__", "mcp__claude_ai_Atlassian_MCP__")  # 로컬 MCP · claude.ai 커넥터. 도구 이름은 같고 머리말만 다름
CARD = ("slack_notify.sh --kind decide --decision-id {did} --options '1=승인 => {what},2=보류 => 하지 않음' "
        "--recommend 1 --no-doc $'{title}\\n배경: …\\n용어: 없음'")
STATUS_FIELDS = {"status", "resolution"}
# 무승인 전환 표. 훅과 같은 저장소(메인 체크아웃)에서 읽어, 표를 바꾸려면 훅 코드처럼 병합을 거치게 함
FREE_TABLE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "hubs", "jira_free_transitions.tsv")


def free_transitions():
    """{(프로젝트 키, 전환 id)}, {(프로젝트 키, 전환 이름)}. 표를 못 읽으면 빈 집합(전부 승인 필요)"""
    ids, names = set(), set()
    try:
        with open(FREE_TABLE, encoding="utf-8") as f:
            for ln in f:
                c = [x.strip() for x in ln.split("\t")]
                if ln.lstrip().startswith("#") or len(c) < 3 or not c[0]:
                    continue
                ids.add((c[0].upper(), c[1]))
                names.add((c[0].upper(), c[2]))
    except OSError:
        pass
    return ids, names


def transition_of(inp):
    """(전환 id, 전환 이름). MCP 입력은 transitionId·transitionName(지금)과 transition:{id,name}(옛 형식) 둘 다 옴"""
    old = inp.get("transition") if isinstance(inp.get("transition"), dict) else {}
    return str(inp.get("transitionId") or old.get("id") or ""), str(inp.get("transitionName") or old.get("name") or "")


def is_free(key, inp):
    """표에 있는 전환만 참. id 와 이름을 둘 다 주면 둘 다 표에 있어야 함(이름만 맞춘 다른 전환 방지)"""
    tid, tname = transition_of(inp)
    proj = key.split("-")[0].upper()
    ids, names = free_transitions()
    if not (tid or tname):
        return False
    return (not tid or (proj, tid) in ids) and (not tname or (proj, tname.strip()) in names)


def status_id(key):
    return "D-JST" + re.sub(r"[^A-Za-z0-9]", "", key or "").upper()[:11]


def target(name, inp):
    """(결정 번호, 하는 일, 카드 제목, 계획 조회, 계획 필드) 또는 None(검사 대상 아님)"""
    key = inp.get("issueIdOrKey") or inp.get("issueKey") or ""
    if name == "transitionJiraIssue" or (name == "editJiraIssue" and STATUS_FIELDS & set((inp.get("fields") or {}).keys())):
        if not key:
            raise pg.Unknown("이슈 키를 알 수 없음")
        if name == "transitionJiraIssue" and is_free(key, inp):
            return None
        to = transition_of(inp)[1] if name == "transitionJiraIssue" else ""
        return status_id(key), "상태 변경", f"Jira {key} 상태 변경 승인", (pa.find_transition, key, to), "jira_transitions 에 {key, to}"
    return None


def main():
    try:
        h = json.load(sys.stdin)
    except ValueError:
        return
    tool = h.get("tool_name", "")
    inp = h.get("tool_input") or {}
    prefix = next((p for p in TOOLS if tool.startswith(p)), None)
    if prefix is None:
        return
    name = tool[len(prefix):]
    if name in ("executeWrite", "executeDestructive"):  # discover 경유 우회
        name, inp = inp.get("name", ""), inp.get("inputs") or {}
    try:
        t = target(name, inp)
        if not t:
            return
        did, what, title, (find, *args), field = t
        if pg.plan_ok(find, *args) or pg.decided(did):
            return
        res = ("deny", f"Jira {what}는 사용자 승인 뒤에만(사용자 11-16) — 계획서에 추가한 뒤 컨펌(plan-approvals/<plan-id>.json 의 {field} 기록) "
               f"또는 결정 카드 {did} 의 1번 답 필요. 예: " + CARD.format(did=did, what=what, title=title))
    except pg.Unknown as e:
        res = ("ask", f"Jira 승인 판정 불가: {e}. 사용자 승인된 작업인지 확인 필요")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": res[0], "permissionDecisionReason": res[1]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
