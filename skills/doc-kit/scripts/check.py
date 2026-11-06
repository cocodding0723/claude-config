#!/usr/bin/env python3
"""doc-kit 구조안·보고서 HTML 검사. 실패 항목이 있으면 종료 코드 1.

사용: check.py [--report] [--strict] [--project 이름] [--audience 기획자|개발자] 문서.html
--strict: 결정 카드 길이 초과 경고도 실패로 처리.
--project·설정 파일: doc_config.py 규칙. 설정의 ticket_pattern 이 있으면 본문 티켓 키를 검사하고
audience 가 기획자일 때만 본문 고유명사를 검사한다."""
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_config


CAUSAL_RE = re.compile(r"(때문|그래서|그러면|하면|면 |므로|탓에|덕분)")
REVIEW_RE = re.compile(r"(검수.{0,6}(기록|결과|반영|내역|지적)|[12]차.{0,3}검수)")
SUMMARY_MAX = 180
DCARD_MAX = 450


class Doc(HTMLParser):
    def __init__(self):
        super().__init__()
        self.checks = {}
        self.svg = 0
        self.external = []
        self.ba = []          # (before 코드 있음, after 코드 있음, why 있음)
        self._stack = []
        self._skip = 0        # code/pre/details.ref 안 깊이
        self.body_text = []
        self._cur_ba = None
        self._in = set()
        self._caps = []       # 열린 영역 [이름, 스택 깊이, 글 조각]
        self.regions = []     # 닫힌 영역 (이름, 글)
        self._details = 0
        self._head = None     # 열린 제목 [태그, 글, details 안 여부]
        self.review_outside = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        chk = a.get("data-check")
        cls = a.get("class", "")
        if chk:
            self.checks[chk] = self.checks.get(chk, 0) + 1
        if tag == "svg":
            self.svg += 1
        if tag == "script" and a.get("src"):
            self.external.append(a["src"])
        if tag == "link" and a.get("rel") == "stylesheet":
            self.external.append(a.get("href", ""))
        if chk == "ba":
            self._cur_ba = [False, False, False]
            self.ba.append(self._cur_ba)
        if self._cur_ba is not None:
            if "before" in cls.split():
                self._in.add("before")
            if "after" in cls.split():
                self._in.add("after")
            if chk == "why":
                self._cur_ba[2] = True
        if chk in ("summary", "dcard"):
            self._caps.append([chk, len(self._stack), []])
        if tag == "details":
            self._details += 1
        if tag in ("h2", "h3", "h4", "summary", "b", "strong"):
            self._head = [tag, [], self._details > 0]
        skip = tag in ("code", "pre", "svg", "style", "script") or (tag == "details" and "ref" in cls)
        self._stack.append((tag, skip))
        if skip:
            self._skip += 1
        if tag == "code" and self._cur_ba is not None:
            if "before" in self._in:
                self._cur_ba[0] = True
            if "after" in self._in:
                self._cur_ba[1] = True

    def handle_endtag(self, tag):
        while self._stack:
            t, skip = self._stack.pop()
            if skip:
                self._skip -= 1
            if t == tag:
                break
        while self._caps and len(self._stack) <= self._caps[-1][1]:
            name, _, parts = self._caps.pop()
            self.regions.append((name, re.sub(r"\s+", " ", "".join(parts)).strip()))
        if tag == "details" and self._details:
            self._details -= 1
        if self._head and tag == self._head[0]:
            text = "".join(self._head[1])
            if not self._head[2] and REVIEW_RE.search(text):
                self.review_outside.append(text.strip()[:30])
            self._head = None
        if tag == "section":
            self._cur_ba = None
            self._in.clear()
        if tag == "div":
            self._in.discard("before") if "after" in self._in else None

    def handle_data(self, data):
        if self._head:
            self._head[1].append(data)
        if self._skip == 0:
            self.body_text.append(data)
        if not any(t in ("style", "script", "svg") for t, _ in self._stack):
            for c in self._caps:
                c[2].append(data)


def main(path, report=False, strict=False, project=None, audience=None):
    html = open(path, encoding="utf-8").read()
    d = Doc()
    d.feed(html)
    body = " ".join(d.body_text)
    fails = []
    if not d.checks.get("summary"):
        fails.append("3줄 요약 박스(data-check=summary) 없음")
    if not d.checks.get("decision"):
        fails.append("🙋 결정 섹션(data-check=decision) 없음")
    if not report and d.svg < 2:
        fails.append(f"내장 SVG {d.svg}개 — 지금/바뀌면 그림 2개 이상 필요")
    dc = d.checks.get("dcard", 0)
    if dc == 0 and "결정 없음" not in body:
        fails.append("결정 카드(data-check=dcard) 없음 — 결정할 게 없으면 '결정 없음'이라고 적을 것")
    if dc and d.checks.get("opt", 0) < dc * 2:
        fails.append("결정 카드마다 선택지(data-check=opt) 2개 이상 필요")
    if dc and d.checks.get("why-rec", 0) < dc:
        fails.append("결정 카드마다 추천 이유(data-check=why-rec) 필요")
    if dc and d.checks.get("blocks", 0) < dc:
        fails.append("결정 카드마다 '안 정하면 멈추는 것'(data-check=blocks) 필요")
    if dc and body.count("감수하는 단점") < dc:
        fails.append("결정 카드마다 추천 이유에 '감수하는 단점' 필요")
    if dc and not re.search(r"(쉬움|보통|어려움)", body):
        fails.append("되돌리기 라벨(쉬움/보통/어려움) 없음")
    if dc and "추천" not in body:
        fails.append("추천 표시 없음")
    if not report and not d.ba:
        fails.append("before/after 블록(data-check=ba) 없음")
    for i, (b, a, w) in enumerate(d.ba, 1):
        if not (b and a):
            fails.append(f"before/after 블록 {i}: before·after 코드 둘 다 필요")
        if not w:
            fails.append(f"before/after 블록 {i}: '왜' 한 줄(data-check=why) 없음")
    if d.external:
        fails.append("외부 리소스 참조: " + ", ".join(d.external[:3]))
    if "{{" in html:
        fails.append("채우지 않은 자리표시 {{…}} 남음")
    cfg = doc_config.resolve(project)
    audience = audience or cfg.get("audience")
    ticket = cfg.get("ticket_pattern")
    parts = []
    if ticket:
        try:
            re.compile(ticket)
        except re.error as e:
            fails.append(f"설정 ticket_pattern 정규식 오류: {e}")
        else:
            parts.append("(?:" + ticket + ")")
    if audience == "기획자":
        parts += [r"[0-9a-f]{7,40}", r"[A-Z][a-z]+(?:[A-Z][a-z0-9]+){1,}"]
        allow = {"PlantUML", "JavaScript", "TypeScript", "GitHub", "PowerPoint", "UniTask", "ZLinq"}
    elif parts:
        allow = set()
    if parts:
        pat = re.compile(r"\b(?:" + "|".join(parts) + r")\b")
        proper = sorted({m.group(0) for m in pat.finditer(body)} - allow)
        if proper:
            fails.append("본문 고유명사(코드·참고 칸으로 옮길 것): " + ", ".join(proper[:12]))
    jargon = sorted(set(re.findall(r"\b(?:P[123]|fixup|autosquash|ctx_\w+|Dispatch)\b", body)))
    if jargon:
        fails.append("내부 기호: " + ", ".join(jargon))
    warns = []
    for name, text in d.regions:
        if name == "summary":
            n = len(re.sub(r"\s", "", text.replace("3줄 요약", "", 1)))
            if n > SUMMARY_MAX:
                fails.append(f"3줄 요약 {n}자 — {SUMMARY_MAX}자 이하로 줄일 것")
    cards = [t for n, t in d.regions if n == "dcard"]
    for i, text in enumerate(cards, 1):
        if not CAUSAL_RE.search(text):
            fails.append(f"결정 카드 {i}: 인과 연결어(때문·그래서·그러면·하면·~면·므로·탓에·덕분) 없음")
        n = len(re.sub(r"\s", "", text))
        if n > DCARD_MAX:
            (fails if strict else warns).append(f"결정 카드 {i}: 본문 {n}자 — {DCARD_MAX}자 이하 권장")
    for h in d.review_outside:
        fails.append(f"검수 기록이 <details> 밖에 있음: '{h}'")
    for w in warns:
        print("⚠️ 경고:", w)
    if fails:
        print("❌ 검사 실패")
        for f in fails:
            print(" -", f)
        return 1
    print(f"✅ 통과 — SVG {d.svg}, before/after {len(d.ba)}")
    return 0


def _opt(argv, name):
    """--이름 값 옵션을 꺼내 (값, 나머지 인자) 반환."""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1], argv[:i] + argv[i + 2:]
        print(f"{name} 값 없음", file=sys.stderr)
        sys.exit(2)
    return None, argv


if __name__ == "__main__":
    argv = sys.argv[1:]
    project, argv = _opt(argv, "--project")
    audience, argv = _opt(argv, "--audience")
    args = [a for a in argv if a not in ("--report", "--strict")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], report="--report" in argv, strict="--strict" in argv, project=project, audience=audience))
