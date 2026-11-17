"""Jira 쓰기 승인 판정(jira-approval-guard.sh 가 부름). stdin: hook JSON.
규칙(사용자 11-16): 이슈 생성·상태 변경(transition, edit 의 status 필드)은 사용자 승인 기록 뒤에만.
승인 기록 = 결정 카드의 가장 최근 답이 1번 또는 approve. 생성 D-JIRA<요약 sha1 8자>, 상태 변경 D-JST<이슈 키 영숫자>.
이슈 생성만 메인 대화의 사용자 직접 지시도 인정(11-17, prompt_approval). 상태 변경은 카드·계획 기록만.
출력: deny|ask hook JSON 한 줄, 통과면 빈 출력. 기록을 못 읽으면 ask."""
import hashlib
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import pr_guard as pg  # noqa: E402  승인 기록 조회 재사용
import plan_approval as pa  # noqa: E402

TOOL = "mcp__atlassian__"
CARD = ("slack_notify.sh --kind decide --decision-id {did} --options '1=승인 => {what},2=보류 => 하지 않음' "
        "--recommend 1 --no-doc $'{title}\\n배경: …\\n용어: 없음'")
STATUS_FIELDS = {"status", "resolution"}


def create_id(summary):
    """요약 앞뒤 공백·연속 공백을 정리한 뒤 sha1 앞 8자. 같은 요약이면 같은 번호"""
    norm = re.sub(r"\s+", " ", summary or "").strip()
    return "D-JIRA" + hashlib.sha1(norm.encode()).hexdigest()[:8]


def status_id(key):
    return "D-JST" + re.sub(r"[^A-Za-z0-9]", "", key or "").upper()[:11]


def target(name, inp):
    """(결정 번호, 하는 일, 카드 제목, 계획 조회) 또는 None(검사 대상 아님)"""
    if name == "createJiraIssue":
        s = inp.get("summary", "")
        if not s:
            raise pg.Unknown("이슈 요약(summary)이 비어 있음")
        return create_id(s), "이슈 발급", f"Jira 이슈 발급 승인: {s}", (pa.find_ticket, s), "tickets 에 {summary}"
    key = inp.get("issueIdOrKey") or inp.get("issueKey") or ""
    if name == "transitionJiraIssue" or (name == "editJiraIssue" and STATUS_FIELDS & set((inp.get("fields") or {}).keys())):
        if not key:
            raise pg.Unknown("이슈 키를 알 수 없음")
        to = ((inp.get("transition") or {}).get("name") or "") if name == "transitionJiraIssue" else ""
        return status_id(key), "상태 변경", f"Jira {key} 상태 변경 승인", (pa.find_transition, key, to), "jira_transitions 에 {key, to}"
    return None


def main():
    try:
        h = json.load(sys.stdin)
    except ValueError:
        return
    tool = h.get("tool_name", "")
    inp = h.get("tool_input") or {}
    if not tool.startswith(TOOL):
        return
    name = tool[len(TOOL):]
    if name in ("executeWrite", "executeDestructive"):  # discover 경유 우회
        name, inp = inp.get("name", ""), inp.get("inputs") or {}
    try:
        t = target(name, inp)
        if not t:
            return
        did, what, title, (find, *args), field = t
        if pg.plan_ok(find, *args) or pg.decided(did):
            return
        if what == "이슈 발급":
            pg.EV.update(transcript_path=h.get("transcript_path") or "", session_id=h.get("session_id") or "")
            if pg.prompt_ok("jira"):
                return
        res = ("deny", f"Jira {what}는 사용자 승인 뒤에만(사용자 11-16) — 계획서에 추가한 뒤 컨펌(plan-approvals/<plan-id>.json 의 {field} 기록) "
               f"또는 결정 카드 {did} 의 1번 답 필요" + (" 또는 메인 세션이면 사용자가 메인 대화에서 직접 '티켓·에픽 만들어' 처럼 지시한 뒤 60분 안" if what == "이슈 발급" else "") + ". 예: " + CARD.format(did=did, what=what, title=title))
    except pg.Unknown as e:
        res = ("ask", f"Jira 승인 판정 불가: {e}. 사용자 승인된 작업인지 확인 필요")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": res[0], "permissionDecisionReason": res[1]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
