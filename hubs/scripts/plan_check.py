#!/usr/bin/env python3
"""계획서 필수 칸 검사. 양식은 board/templates/plan.md(설치 위치 ~/orca/board/templates/plan.md).

검사: 공통 결정 5칸(이름·재사용 부품·색·글꼴·데이터 키) · 병렬 점검 4칸(먼저 막는 것·독립·공유 상태·최소 분해) ·
카드 표의 '순서' 열(독립 | 순차(<앞 카드>)), 카드 1장 이상.
칸 값이 비었거나 양식 자리표시(<…>)·'-'·'미정'·'TODO' 이면 실패. 해당 없음은 'n/a: <이유>' 로만(이유 필수).
Markdown·HTML 둘 다 읽음(HTML 은 태그를 걷어 같은 줄 모양으로 바꿔 봄).

사용: plan_check.py <계획서.md|.html> [--json]
종료 코드: 0 통과, 1 빠진 칸 있음(원인·조치 출력), 2 인자·파일 오류"""
import argparse
import html
import json
import re
import sys

COMMON = ("이름", "재사용 부품", "색", "글꼴", "데이터 키")
PARALLEL = ("먼저 막는 것", "독립", "공유 상태", "최소 분해")
ORDER_HEAD = "순서"
ORDER_OK = re.compile(r"^(독립|순차\s*\(\s*[^()\s][^()]*\))$")
EMPTY = {"", "-", "미정", "todo", "tbd"}
PLACEHOLDER = re.compile(r"<[^<>]+>")
NA = re.compile(r"^n/?a\s*[:：]\s*(.*)$", re.I)
HELP = "양식: ~/orca/board/templates/plan.md"


def to_lines(text, is_html):
    if not is_html:
        return text.splitlines()
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", text)
    t = re.sub(r"(?i)</t[dh]>\s*<t[dh][^>]*>", " | ", t)
    t = re.sub(r"(?i)<tr[^>]*>\s*<t[dh][^>]*>", "\n| ", t)
    t = re.sub(r"(?i)</t[dh]>\s*</tr>", " |\n", t)
    t = re.sub(r"(?i)<br\s*/?>|</(p|li|h[1-6]|div)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).splitlines()


def field(lines, label):
    """'라벨:' 뒤 값. 굵게·글머리표는 무시. 없으면 None"""
    pat = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?" + re.escape(label) + r"(?:\*\*)?\s*[:：](?:\*\*)?\s*(.*)$")
    for ln in lines:
        m = pat.match(ln)
        if m:
            return m.group(1).strip()
    return None


def bad_value(v):
    """칸 값 문제 한 줄, 문제 없으면 None"""
    if v is None:
        return "칸 없음"
    na = NA.match(v)
    if na:
        return None if na.group(1).strip() and not PLACEHOLDER.search(na.group(1)) else "n/a 이유 없음"
    if v.strip().lower() in EMPTY:
        return "비었음"
    if PLACEHOLDER.search(v):
        return "양식 자리표시 그대로"
    return None


def cells(ln):
    s = ln.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return None
    return [c.strip() for c in s.strip("|").split("|")]


def card_rows(lines):
    """카드 표의 (제목, 순서) 목록. 표가 없으면 None"""
    for i, ln in enumerate(lines):
        head = cells(ln)
        if head is None or ORDER_HEAD not in head:
            continue
        col, out = head.index(ORDER_HEAD), []
        for nxt in lines[i + 1:]:
            row = cells(nxt)
            if row is None:
                if nxt.strip():
                    break
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in row if c):
                continue
            out.append((row[0] if row else "", row[col] if col < len(row) else ""))
        return out
    return None


def check(text, is_html):
    lines = to_lines(text, is_html)
    probs = []
    for group, labels in (("공통 결정", COMMON), ("병렬 점검", PARALLEL)):
        for label in labels:
            why = bad_value(field(lines, label))
            if why:
                probs.append(f"{group} '{label}': {why}")
    rows = card_rows(lines)
    if rows is None:
        probs.append(f"카드 표 없음('{ORDER_HEAD}' 열이 있는 표)")
    else:
        real = [(t, o) for t, o in rows if not PLACEHOLDER.search(t)]
        if not real:
            probs.append("카드 표에 카드 없음(자리표시 행만)")
        for title, order in real:
            if not ORDER_OK.match(order):
                probs.append(f"카드 '{title[:40]}' 순서 '{order}': 독립 또는 순차(<앞 카드>) 로 적음")
    return probs


def main(argv):
    ap = argparse.ArgumentParser(prog="plan_check.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="계획서 파일(.md 또는 .html)")
    ap.add_argument("--json", action="store_true", help='{"ok","problems"} 한 줄 출력')
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    try:
        with open(a.path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"plan_check: {a.path} 읽기 실패({type(e).__name__}). 조치: 경로·인코딩(UTF-8) 확인", file=sys.stderr)
        return 2
    probs = check(text, a.path.lower().endswith((".html", ".htm")) or "<table" in text.lower())
    if a.json:
        print(json.dumps({"ok": not probs, "problems": probs}, ensure_ascii=False))
    elif probs:
        print("계획서 빠진 칸:\n" + "\n".join(f"- {p}" for p in probs) + f"\n조치: 칸을 채우거나 'n/a: 이유'. {HELP}", file=sys.stderr)
    else:
        print("계획서 필수 칸 통과")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
