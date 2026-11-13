#!/usr/bin/env python3
"""jira-issue 발급 전후 검사. MCP 호출은 에이전트가 하고 이 스크립트는 입력·결과만 검사함.

pre  <입력.json>   발급 직전 입력 검사. 오류 있으면 종료 1, 경고만이면 0 (--strict 는 경고도 1)
post <결과.json>   발급 직후 다시 읽은 티켓들의 점검 표 출력. 칸이 비면 종료 1

입력(pre): {"type","summary","parent":{"key","reason"} 또는 "new_epic":{"description","children":[]},
  "assignee","due","due_reason","status","evidence":{"branch","commits":[],"pr"}}
결과(post): [{"key","type","parent","status","due","assignee","evidence","description","children"}]
"""
import json
import re
import sys

KEY_RE = re.compile(r"^LRM-\d+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SUMMARY_RE = re.compile(r"^\[[^\]]+\] .+")
SUMMARY_MAX = 40
EPIC = "에픽"
# 근거 단계별 허용 상태(LRM 실제 이름, 11-13 조회). 완료는 병합 뒤 사람 확인 후 전환이라 병합 직후엔 확인 대기도 허용
STAGE_STATUS = {
    "todo": {"해야 할 일"},
    "doing": {"진행 중"},
    "review": {"확인 대기"},
    "done": {"완료", "확인 대기"},
}
# 사람이 정하는 상태. 어느 단계에서도 불일치로 보지 않음
HOLD_STATUS = "보류"
FIELDS = ("key", "parent", "status", "due", "assignee", "evidence")
LABELS = {"key": "키", "parent": "상위", "status": "상태", "due": "기한", "assignee": "담당", "evidence": "근거"}


def stage_of(ev):
    """근거로 실제 단계 판정. PR 병합 > PR > 브랜치·커밋 > 착수 전."""
    ev = ev or {}
    if ev.get("merged"):
        return "done"
    if ev.get("pr"):
        return "review"
    if ev.get("commits") or ev.get("branch"):
        return "doing"
    return "todo"


def check_pre(d):
    errs, warns = [], []
    summary = d.get("summary") or ""
    if not SUMMARY_RE.match(summary) or len(summary) > SUMMARY_MAX:
        errs.append(f"제목은 '[영역] 현상' {SUMMARY_MAX}자 이내")
    parent, epic = d.get("parent"), d.get("new_epic")
    if parent:
        if not KEY_RE.match(parent.get("key") or ""):
            errs.append("상위 키 형식 오류 (LRM-숫자)")
        if not (parent.get("reason") or "").strip():
            errs.append("상위를 고른 근거 한 줄 없음")
    elif epic:
        if not (epic.get("description") or "").strip():
            errs.append("새 에픽 설명(목표·범위·완료 기준) 없음")
        if not epic.get("children"):
            errs.append("새 에픽 하위 목록 없음")
    elif d.get("type") != EPIC:
        errs.append("상위 없음: parent 또는 new_epic 필수")
    if not (d.get("assignee") or "").strip():
        errs.append("담당자 accountId 없음")
    if not DATE_RE.match(d.get("due") or ""):
        errs.append("기한 YYYY-MM-DD 없음")
    if not (d.get("due_reason") or "").strip():
        errs.append("기한 근거 한 줄 없음")
    status = d.get("status") or ""
    if not status:
        errs.append("상태 없음")
    else:
        stage = stage_of(d.get("evidence"))
        if status != HOLD_STATUS and status not in STAGE_STATUS[stage]:
            warns.append(f"상태 불일치: 근거는 '{stage}' 단계, 입력 상태 '{status}' → 발급 직후 {sorted(STAGE_STATUS[stage])[0]} 로 전환")
    return errs, warns


def check_post(items):
    """티켓별 (행, 빈 칸 목록). 에픽은 설명·하위도 검사."""
    rows = []
    for it in items:
        miss = [f for f in FIELDS if not it.get(f) and not (f == "parent" and it.get("type") == EPIC)]
        if it.get("type") == EPIC:
            for f, label in (("description", "설명"), ("children", "하위")):
                if not it.get(f):
                    miss.append(label)
        if it.get("status") and it.get("evidence") is not None:
            stage = stage_of(it["evidence"])
            if it["status"] != HOLD_STATUS and it["status"] not in STAGE_STATUS[stage]:
                miss.append(f"상태({stage} 단계와 불일치)")
        rows.append((it, miss))
    return rows


def render(rows):
    out = ["| " + " | ".join(LABELS[f] for f in FIELDS) + " | 점검 |", "|" + "---|" * (len(FIELDS) + 1)]
    for it, miss in rows:
        ev = it.get("evidence")
        ev_txt = ", ".join(f"{k}={v}" for k, v in ev.items() if v) if isinstance(ev, dict) else (ev or "")
        cells = [str(it.get(f) or "비어 있음") for f in FIELDS[:-1]] + [ev_txt or "비어 있음"]
        out.append("| " + " | ".join(cells) + " | " + ("통과" if not miss else "고칠 것: " + ", ".join(miss)) + " |")
    return "\n".join(out)


def main(argv):
    strict = "--strict" in argv
    args = [a for a in argv[1:] if a != "--strict"]
    if len(args) != 2 or args[0] not in ("pre", "post"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(args[1], encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"입력 읽기 실패: {e}", file=sys.stderr)
        return 2
    if args[0] == "pre":
        errs, warns = check_pre(data)
        for e in errs:
            print("오류:", e)
        for w in warns:
            print("경고:", w)
        if not errs and not warns:
            print("통과")
        return 1 if errs or (strict and warns) else 0
    rows = check_post(data if isinstance(data, list) else [data])
    print(render(rows))
    return 1 if any(m for _, m in rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
