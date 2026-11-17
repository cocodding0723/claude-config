"""작업판(bd) 사람 손 카드 도우미. 진입점은 board-ask·board-answer(같은 폴더).
패널(lrm-board)은 kind:decision·kind:user-todo 열린 카드를 결정 대기·할 일 칸으로 그림.
승인 성격(PR 게시·병합 허락 등)은 따로 종류를 두지 않고 결정 카드에 label gate:approval 을 붙여 배지로만 구분(11-17, kind:approval 폐지).
환경: BOARD_DIR(기본 ~/orca/board), BOARD_BD(기본: PATH 의 bd, 없으면 /opt/homebrew/bin·/usr/local/bin).
결정 카드는 '돌려 보면 알 수 있나'(--runnable) 칸이 필수. yes 면 카드를 만들지 않음 — 동작·시간·배치·출력처럼 실행해 보면 아는 사실은
사람에게 묻지 않고 시제품·실측으로 판정함(제품 방향·취향만 묻는다). 승인 성격(--gate approval)은 사실 질문이 아니라 제외.
결정 카드는 Slack 번호 카드로 그대로 보낼 수 있는 칸(배경·용어·추천·선택지 2~4개와 각 결과)과 HTML 설명서(--doc)가 필수. 없으면 종료 2 —
결정의 원천을 작업판 한 곳으로 두려는 것이라, 칸이 비면 뒤에 Slack 으로 못 보내고 사람이 카드를 다시 손봐야 함.
종료 코드: 0 정상(이미 있음·이미 닫힘 포함), 2 인자 오류, 3 bd 실패, 4 카드 없음, 5 돌려 보면 아는 질문이라 거부"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # -I 실행에서도 같은 폴더의 board_slack 을 찾음
import decide_card  # noqa: E402

KINDS = ("decision", "user-todo")
BD_CANDIDATES = ("/opt/homebrew/bin/bd", "/usr/local/bin/bd")  # launchd 기본 PATH(/usr/bin:/bin)에는 bd 가 없어 버튼 답이 카드로 못 갔음(11-17)
LEGACY_APPROVAL = "approval"  # 옛 --kind approval. decision + gate:approval 로 바꿔 받음
GATES = ("approval",)
APPROVAL_OPTIONS = ("승인 => 이대로 진행", "보류 => 진행하지 않고 카드를 열어 둠")
REASON_MAX = 80
OPTION_MAX = 4  # Slack 번호 카드 선택지 상한(board_slack 도 이 값을 씀)
RESULT_SEP = "=>"  # 선택지 '문구 => 고르면 달라지는 점'
TITLE_MAX, title_len = decide_card.TITLE_MAX, decide_card.title_len  # 제목 기준·세는 방식은 알림 스크립트와 공유하는 decide_card 한 곳
# 결정 카드 HTML 설명서 검사 기준(검사는 doc_gaps 한 함수). board-ask 와 board_slack 전송 직전이 같이 씀
DOC_LINE = "설명서"  # 카드 설명의 '설명서: <html 절대 경로>' 줄 머리
DOC_EXT = ".html"
DOC_BG_TITLES, DOC_BG_MIN = ("배경",), 80  # 배경(상세) 절 제목 낱말·본문 최소 글자(공백 제외)
DOC_OPT_TITLES = ("선택지", "결과")  # 선택지별 결과 절. 선택지 수 이상의 항목(li·tr·data-check=opt)이 있어야 함
DOC_WHY_TITLES, DOC_WHY_MIN = ("추천 이유",), 20  # 추천 이유 절(또는 data-check=why-rec)
DOC_SVG_MIN_SHAPES = 3  # 도식으로 인정하는 인라인 SVG 최소 도형 수(doc-kit check.py 와 같음). 아이콘은 도식이 아님
DOC_SVG_SHAPES = {"rect", "path", "line", "circle", "ellipse", "polygon", "polyline", "text"}
DOC_IMG_EXT = (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif")  # PUML 로 렌더한 옆 파일 이미지도 도식
DOC_DATA_IMG_MIN = 300  # data URI 이미지 최소 길이(자리표시 이미지 배제)
DOC_HEADINGS = ("h1", "h2", "h3", "h4")
# 가독성(사용자 11-17: 개행으로 읽기 좋게): 문단·목록 항목 한 줄에 사실은 하나. 코드·경로·URL 안의 '/' 는 보지 않음
DOC_BLOCKS = ("p", "li")  # 검사할 본문 단위
DOC_NO_READ = ("code", "pre", "script", "style", "svg")  # 이 태그 안 글은 가독성 검사에서 제외
DOC_SLASH_JOIN = re.compile(r"\s/\s")  # ' / ' 로 사실을 이은 줄
DOC_DOT_JOIN = " · "  # 가운뎃점 나열 구분자(공백 포함)
DOC_DOT_MAX = 3  # 한 줄의 가운뎃점 상한. 넘으면 사실 나열로 봄('구독·취소' 같은 낱말 묶음은 통과)
DOC_DOT_LINE_MIN = 40  # 가운뎃점은 이 글자 수 이상의 줄만 위반으로 봄(날짜·종류 머리 줄 같은 짧은 줄 통과)
DOC_READ_SKIP = ("meta", "term", "cap", "answer", "tag")  # 이 class 의 문단·항목은 가독성 검사 제외(머리·용어·도식 설명·답 줄)
DOC_VOID = ("meta", "link", "br", "hr", "img", "input", "source")  # 닫는 태그가 없는 태그
DOC_READ_REPORT = 3  # 오류 문구에 보여 줄 위반 줄 수
DOC_ITEMS = ("li", "tr")
RUNNABLE = ("yes", "no")
EXIT_RUNNABLE = 5
RUNNABLE_REFUSE = ("돌려 보면 알 수 있는 질문은 결정 카드로 묻지 않음. "
                   "조치: 시제품·실측(실행·촬영·시험)으로 정하고 결과와 근거를 작업 카드 코멘트·decisions.tsv 에 남김. "
                   "제품 방향·취향이라 실행으로 못 정하면 --runnable no")

ASK_HELP = """결정·할 일 카드를 작업판에 만든다. 같은 종류·같은 제목의 열린 카드가 있으면 새로 만들지 않고 그 id 를 낸다.
승인 성격은 --gate approval(결정 카드 + label gate:approval, 선택지를 안 주면 승인|보류).
Slack 자동 보내기 스위치(~/.config/lrm-hubs/.board-slack.on)가 켜져 있으면 새 결정 카드를 바로 Slack 번호 카드로 보냄(board_slack.py).

예:
  board-ask "패널 정본을 작업판으로 옮길까?" --runnable no --parent lrm-q5o \\
    --options "옮김 => 손 칸을 없애 카드 하나로 관리|유지 => 지금처럼 손 칸을 같이 둠" --recommend 옮김 \\
    --background "패널 칸이 손 입력과 카드 두 곳이라 어긋남" --terms "패널 정본: 패널이 읽는 원본 데이터" --doc ~/Documents/90_정리기록/panel-source.html
  board-ask "PR #812 게시" --gate approval --recommend 승인 --background "draft PR 을 올려 리뷰를 받음" --terms 없음 --doc pr812.html
  board-ask "릴리스 키스토어 만들기" --kind user-todo --why "출시 전 Google 로그인 필요"

결정 카드 필수 칸: --options(2~4개, 각 '문구 => 결과') · --recommend · --background · --terms(없으면 '없음') · --doc(HTML 설명서) · --runnable no
결정 카드 제목은 @TITLE_MAX@자 이내(이모지·공백 제외). 넘으면 종료 2. 티켓·커밋 번호는 보낼 때 맨 아래 '참고:' 줄로 자동 이동

출력: 카드 id 한 줄(--json 이면 {"id","created","kind"})"""

ANSWER_HELP = """사람 답 원문을 카드 코멘트로 남기고 카드를 닫는다. 이미 닫힌 카드면 아무것도 하지 않는다.

예:
  board-answer lrm-abc "옮김. 단 Slack 칸은 숨겨"
  board-answer lrm-abc "내일 다시 볼게" --keep-open

출력: 처리 결과 한 줄(--json 이면 {"id","closed","commented"})"""


class Fail(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def find_bd():
    """bd 실행 파일. BOARD_BD → PATH → 알려진 설치 위치 순. 못 찾으면 맨 'bd'"""
    return os.environ.get("BOARD_BD") or shutil.which("bd") or next((p for p in BD_CANDIDATES if os.path.exists(p)), "bd")


def bd(*args):
    cmd = [find_bd(), *args]
    try:
        p = subprocess.run(cmd, cwd=os.environ.get("BOARD_DIR") or os.path.expanduser("~/orca/board"),
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(3, f"bd 실행 실패({type(e).__name__}). 조치: bd 설치·BOARD_DIR 확인")
    if p.returncode != 0:
        raise Fail(3, f"bd {args[0]} 실패: {(p.stderr or p.stdout).strip()[:200]}. 조치: BOARD_DIR 가 작업판인지 확인")
    return p.stdout


def bd_json(*args):
    try:
        return json.loads(bd(*args, "--json") or "null")
    except ValueError:
        raise Fail(3, f"bd {args[0]} 출력이 JSON 아님. 조치: bd 버전 확인")


def option_name(opt):
    """'문구 => 결과' 의 문구. 결과가 없으면 그대로"""
    return opt.split(RESULT_SEP, 1)[0].strip()


class DocScan(HTMLParser):
    """설명서 HTML 을 구역(h2 단위)·결정 카드 절(data-check=dcard)·제목 절·도식으로 훑음.
    표준 형식(20261117-답할것/index.html): 구역마다 div.bg 배경 + 도식, 카드마다 section[data-check=dcard] 의 선택지 tr[data-check=opt]·why-rec"""

    def __init__(self):
        super().__init__()
        self.sections = []  # 제목 절 {"title", "chars", "items"} — 카드 절이 없는 단일 결정 문서용
        self.groups = [{"bg": 0, "diagrams": 0, "dcards": [], "blocks": []}]  # h2 로 나뉘는 구역
        self.diagrams = 0
        self.why_mark = False
        self._head = None  # 열린 제목 태그
        self._skip = 0  # script·style 깊이
        self._svg = []  # 열린 svg 별 도형 수
        self._buf = []  # 열린 문단·항목 [글 조각, 제외 여부]
        self._noread = 0  # code·pre 같은 제외 태그 깊이
        self._stack = []  # 열린 태그 [태그, 배경 구역, 카드 절]
        self._dc = None  # 열린 카드 절

    @property
    def group(self):
        return self.groups[-1]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag not in DOC_VOID:
            self._stack.append([tag, "bg" in cls or a.get("data-check") == "bg", a.get("data-check") == "dcard" or "decision-card" in cls])
            if self._stack[-1][2]:
                self._dc = {"id": "", "opts": 0, "why": False}
                self.group["dcards"].append(self._dc)
        if tag in DOC_NO_READ:
            self._noread += 1
        if tag in DOC_BLOCKS:
            self._buf.append([[], bool(set(cls) & set(DOC_READ_SKIP))])
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in DOC_HEADINGS:
            self._head = tag
            if tag in ("h1", "h2"):
                self.groups.append({"bg": 0, "diagrams": 0, "dcards": [], "blocks": []})
                self._dc = None
            self.sections.append({"title": "", "chars": 0, "items": 0})
        elif tag == "svg":
            self._svg.append(0)
        elif self._svg and tag in DOC_SVG_SHAPES:
            self._svg[-1] += 1
        elif tag == "img":
            src = a.get("src") or ""
            if (src.startswith("data:image") and len(src) >= DOC_DATA_IMG_MIN) or src.split("?")[0].lower().endswith(DOC_IMG_EXT):
                self.diagrams += 1
                self.group["diagrams"] += 1
        if tag in DOC_ITEMS and self.sections:
            self.sections[-1]["items"] += 1
        if a.get("data-check") == "opt" and self._dc is not None:
            self._dc["opts"] += 1
        if a.get("data-check") == "why-rec":
            self.why_mark = True
            if self._dc is not None:
                self._dc["why"] = True

    def handle_endtag(self, tag):
        if tag in DOC_NO_READ:
            self._noread = max(0, self._noread - 1)
        if tag in DOC_BLOCKS and self._buf:
            parts, skip = self._buf.pop()
            if not skip:
                self.group["blocks"].append("".join(parts))
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == self._head:
            self._head = None
        elif tag == "svg" and self._svg:
            if self._svg.pop() >= DOC_SVG_MIN_SHAPES:
                self.diagrams += 1
                self.group["diagrams"] += 1
        for i in range(len(self._stack) - 1, -1, -1):  # 닫는 태그와 짝인 열린 태그까지 정리(닫는 태그 생략된 html 허용)
            if self._stack[i][0] == tag:
                if any(x[2] for x in self._stack[i:]):
                    self._dc = None
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._buf and not self._noread:
            self._buf[-1][0].append(data)
        if self._skip:
            return
        in_bg = any(x[1] for x in self._stack)
        n = len(re.sub(r"\s", "", data))
        if in_bg and not self._svg:
            self.group["bg"] += n
        if self._dc is not None and self._head == "h3" and not self._dc["id"]:
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", data)
            self._dc["id"] = m.group(1) if m else ""
        if not self.sections:
            return
        if self._head and not self._svg:
            self.sections[-1]["title"] += data.strip()
        elif not self._svg:
            self.sections[-1]["chars"] += n


def line_gaps(text):
    """본문 한 줄(문단·항목)의 가독성 위반 이유 목록(없으면 빈 목록). 순수 함수 — 슬랙 본문 검사도 재사용할 수 있게 입력은 글 한 줄뿐.
    가운뎃점은 줄이 DOC_DOT_LINE_MIN 자 이상일 때만 봄(짧은 머리 줄·낱말 묶음은 통과)"""
    text = " ".join((text or "").split())
    why = []
    if DOC_SLASH_JOIN.search(text):
        why.append("' / ' 로 사실을 이음")
    if len(text) >= DOC_DOT_LINE_MIN and (DOC_DOT_JOIN in text or text.count("·") > DOC_DOT_MAX):
        why.append("가운뎃점으로 사실을 나열함")
    return why


def split_doc(spec):
    """'경로#카드id' → (경로, 카드 id|''). 카드 한 장이 한 문서를 쓰면 #id 는 생략"""
    path, _, card_id = (spec or "").partition("#")
    return path, card_id


def doc_gaps(spec, option_count):
    """결정 설명서 HTML 의 빠진 것 목록(없으면 빈 목록): 파일·배경(상세)·도식·선택지별 결과·추천 이유·가독성.
    spec 은 '경로' 또는 '경로#카드id'. 카드 절(data-check=dcard)이 있는 표준 형식은 그 카드의 구역·절만 검사하고,
    여러 카드가 든 문서에서 #카드id 가 없으면 거부함. 카드 절이 없는 문서는 제목 낱말('배경'·'선택지별 결과'·'추천 이유')로 문서 전체를 검사함"""
    path, card_id = split_doc(spec)
    if not path:
        return ["설명서 경로"]
    if not os.path.isfile(path):
        return [f"설명서 파일 없음({path})"]
    if not path.lower().endswith(DOC_EXT):
        return [f"설명서가 {DOC_EXT} 아님({path})"]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            html = f.read()
    except OSError as e:
        return [f"설명서 읽기 실패({e.strerror})"]
    scan = DocScan()
    scan.feed(html)
    gaps = []
    dcards = [(g, dc) for g in scan.groups for dc in g["dcards"]]
    if dcards:
        mine = [(g, dc) for g, dc in dcards if card_id and dc["id"] == card_id] or (dcards if len(dcards) == 1 and not card_id else [])
        if not mine:
            return [f"설명서에 카드 {card_id} 절 없음(data-check=dcard 의 h3 첫 낱말이 카드 id)" if card_id
                    else f"여러 카드({len(dcards)}장) 설명서 — '설명서: 경로#카드id' 로 카드를 지정"]
        g, dc = mine[0]
        bg_ok, svg_ok, opt_ok, why_ok, blocks = g["bg"] >= DOC_BG_MIN, g["diagrams"] >= 1, dc["opts"] >= option_count, dc["why"], g["blocks"]
    else:
        def pick(words):
            return [x for x in scan.sections if any(w in x["title"] for w in words)]
        bg_ok = any(x["chars"] >= DOC_BG_MIN for x in pick(DOC_BG_TITLES)) or any(g["bg"] >= DOC_BG_MIN for g in scan.groups)
        svg_ok = scan.diagrams >= 1
        opt_ok = any(x["items"] >= option_count for x in pick(DOC_OPT_TITLES))
        why_ok = scan.why_mark or any(x["chars"] >= DOC_WHY_MIN for x in pick(DOC_WHY_TITLES))
        blocks = [t for g in scan.groups for t in g["blocks"]]
    if not bg_ok:
        gaps.append(f"배경(상세) 절('{DOC_BG_TITLES[0]}' 제목 또는 class=bg 구역, 본문 {DOC_BG_MIN}자 이상)")
    if not svg_ok:
        gaps.append(f"도식(도형 {DOC_SVG_MIN_SHAPES}개 이상 인라인 SVG 또는 PUML 로 만든 SVG 1개 이상)")
    if not opt_ok:
        gaps.append(f"선택지별 결과(제목 '{DOC_OPT_TITLES[0]}' 아래 또는 data-check=opt 항목 {option_count}개)")
    if not why_ok:
        gaps.append(f"추천 이유 절('{DOC_WHY_TITLES[0]}' 제목 + 본문 {DOC_WHY_MIN}자 이상 또는 data-check=why-rec)")
    bad = [(t, w) for t in blocks for w in line_gaps(t)]
    if bad:
        shown = "; ".join(f"'{' '.join(t.split())[:30]}…' {w}" for t, w in bad[:DOC_READ_REPORT])
        gaps.append(f"가독성 위반 {len(bad)}건(줄 하나에 사실 하나, 개행·목록으로 나눔): {shown}")
    return gaps


def card_gaps(options, background, terms):
    """Slack 번호 카드 필수 칸 중 빠진 것의 이름 목록. board-ask(만들 때)와 board_slack(보낼 때)이 같은 기준을 씀"""
    gaps = [n for n, v in (("배경", background), ("용어", terms)) if not v]
    if not 2 <= len(options) <= OPTION_MAX:
        gaps.append(f"선택지 2~{OPTION_MAX}개")
    if any(RESULT_SEP not in o or not o.split(RESULT_SEP, 1)[1].strip() for o in options):
        gaps.append("선택지 결과('문구 => 고르면 달라지는 점')")
    return gaps


def ask(a):
    title = a.question.strip()
    if not title:
        raise Fail(2, "질문이 비었음. 조치: 첫 인자에 질문 한 문장")
    if a.kind in ("decision", LEGACY_APPROVAL) and title_len(title) > TITLE_MAX:
        raise Fail(2, f"결정 카드 제목이 {title_len(title)}자(최대 {TITLE_MAX}). Slack 첫 줄 규칙에 걸려 못 보냄. "
                      f"조치: 제목을 {TITLE_MAX}자 안으로 줄이고 세부는 --background 로 옮김")
    if a.kind == LEGACY_APPROVAL:
        print("board-ask: --kind approval 은 폐지됨 → 결정 카드 + gate:approval 로 만듦. 조치: --gate approval 사용", file=sys.stderr)
        a.kind, a.gate = "decision", "approval"
    if a.gate and a.kind != "decision":
        raise Fail(2, "--gate 는 결정 카드에만. 조치: --kind 를 빼거나 decision")
    if a.kind == "decision" and not a.gate:
        if a.runnable is None:
            raise Fail(2, "결정 카드는 '돌려 보면 알 수 있나' 칸 필수. 조치: 실행·시제품으로 알 수 있으면 --runnable yes(거부됨), 방향·취향이면 --runnable no")
        if a.runnable == "yes":
            raise Fail(EXIT_RUNNABLE, RUNNABLE_REFUSE)
    opts = [o.strip() for o in (a.options or "").split("|") if o.strip()]
    if a.gate == "approval" and not opts:
        opts = list(APPROVAL_OPTIONS)
    if a.kind == "decision":
        gaps = card_gaps(opts, a.background, a.terms)
        if a.recommend is None:
            gaps.append("추천")
        if not a.doc:
            gaps.append("설명서(--doc)")
        if gaps:
            raise Fail(2, f"결정 카드 필수 칸 없음: {', '.join(gaps)}. Slack 번호 카드로 못 보내는 카드가 생김. "
                          "조치: --options \"A => 결과|B => 결과\" --recommend A --background \"…\" --terms \"…\"(없으면 없음) --doc <설명서.html> 를 채워 다시 실행")
    names = [option_name(o) for o in opts]
    if a.recommend is not None and a.recommend not in names:
        raise Fail(2, f"추천 '{a.recommend}' 이 선택지에 없음. 조치: --options 안의 값 하나")
    label = f"kind:{a.kind}"
    for r in bd_json("list", "--label", label, "--limit", "0") or []:
        if r.get("title") == title and r.get("status") != "closed":
            return {"id": r["id"], "created": False, "kind": a.kind, "gate": a.gate}
    lines = []
    if opts:
        lines.append("선택지:")
        lines += [f"- {o}" for o in opts]
    if a.recommend is not None:
        lines.append(f"추천: {a.recommend}")
    if a.why:
        lines.append(f"이유: {a.why}")
    if a.kind == "decision" and not a.gate:
        lines.append("돌려 보면 알 수 있나: 아니오")
    if a.background:
        lines.append(f"배경: {a.background}")
    if a.terms:
        lines.append(f"용어: {a.terms}")
    if a.doc:
        lines.append(f"{DOC_LINE}: {os.path.abspath(os.path.expanduser(a.doc))}")
    if a.kind == "decision":
        import board_slack  # 서로 참조라 호출 때 불러옴. 만든 뒤 Slack 에서 거부될 카드(글자 수 상한 등)를 만들기 전에 막음
        try:
            board_slack.notify_args({"id": "new", "title": title, "labels": [label], "description": "\n".join(lines)})
        except board_slack.cards.Fail as e:  # board_slack 이 따로 불러온 board_cards 의 Fail 이라 여기서 다시 감쌈
            raise Fail(e.code, re.sub(r"^new ", "", str(e)))
    labels = label + (f",gate:{a.gate}" if a.gate else "")
    args = ["create", title, "-l", labels, "-p", str(a.priority)]
    if lines:
        args += ["-d", "\n".join(lines)]
    if a.parent:
        args += ["--parent", a.parent]
    if a.dry_run:
        return {"id": None, "created": False, "kind": a.kind, "gate": a.gate, "dryRun": args}
    made = bd_json(*args)
    made = made[0] if isinstance(made, list) else made
    if not isinstance(made, dict) or "id" not in made:
        raise Fail(3, "bd create 결과에 id 없음. 조치: bd list 로 카드가 생겼는지 확인")
    if a.kind == "decision":
        import board_slack  # 서로 참조라 호출 때 불러옴
        board_slack.auto_push(made["id"])
    return {"id": made["id"], "created": True, "kind": a.kind, "gate": a.gate}


def answer(a):
    text = a.text.strip()
    if not text:
        raise Fail(2, "답 원문이 비었음. 조치: 두 번째 인자에 사람 답 그대로")
    rows = bd_json("list", "--all", "--id", a.id, "--limit", "0") or []
    card = next((r for r in rows if r.get("id") == a.id), None)
    if card is None:
        raise Fail(4, f"카드 {a.id} 없음. 조치: bd list --label kind:decision 로 id 확인")
    if card.get("status") == "closed":
        return {"id": a.id, "closed": True, "commented": False, "already": True}
    if a.dry_run:
        return {"id": a.id, "closed": False, "commented": False, "dryRun": True}
    bd("comments", "add", a.id, f"사용자 답: {text}")
    if a.keep_open:
        return {"id": a.id, "closed": False, "commented": True}
    reason = text if len(text) <= REASON_MAX else text[:REASON_MAX - 1] + "…"
    bd("close", a.id, "--reason", f"답: {reason}")
    return {"id": a.id, "closed": True, "commented": True}


def main(argv):
    if not argv or argv[0] not in ("ask", "answer"):
        print("사용: board_cards.py ask|answer ... (진입점 board-ask·board-answer)", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    fmt = argparse.RawDescriptionHelpFormatter
    if cmd == "ask":
        ap = argparse.ArgumentParser(prog="board-ask", description=ASK_HELP.replace("@TITLE_MAX@", str(TITLE_MAX)), formatter_class=fmt)
        ap.add_argument("question", help="카드 제목. 결정이면 질문 한 문장")
        ap.add_argument("--options", help='선택지 "A => 고르면 달라지는 점|B => …"(결정은 2~4개·결과 필수)')
        ap.add_argument("--recommend", help="추천 선택지 문구(선택지 중 하나, 결정은 필수)")
        ap.add_argument("--why", help="추천 이유·배경 한 줄")
        ap.add_argument("--parent", help="상위 epic id")
        ap.add_argument("--kind", choices=KINDS + (LEGACY_APPROVAL,), default="decision", help="카드 종류(기본 decision). approval 은 폐지된 옛 값")
        ap.add_argument("--gate", choices=GATES, help="승인 성격 배지(label gate:<값>). 결정 카드에만")
        ap.add_argument("--runnable", choices=RUNNABLE, help="돌려 보면 알 수 있나(결정 카드 필수, 승인 성격 제외). yes 면 거부: 시제품·실측으로 판정")
        ap.add_argument("--background", help="배경 한 줄(결정 카드 필수)")
        ap.add_argument("--terms", help="낯선 말 풀이 한 줄, 없으면 '없음'(결정 카드 필수)")
        ap.add_argument("--doc", help="HTML 설명서 경로(결정 카드 필수). 배경(상세)·도식·선택지별 결과·추천 이유 절이 있어야 함. Slack 카드 스레드에 첨부됨")
        ap.add_argument("--priority", type=int, choices=range(5), default=1, help="0 최우선~4(기본 1)")
    else:
        ap = argparse.ArgumentParser(prog="board-answer", description=ANSWER_HELP, formatter_class=fmt)
        ap.add_argument("id", help="카드 id")
        ap.add_argument("text", help="사람 답 원문(고치지 않고 그대로)")
        ap.add_argument("--keep-open", action="store_true", help="코멘트만 남기고 닫지 않음")
    ap.add_argument("--json", action="store_true", help="JSON 한 줄 출력")
    ap.add_argument("--dry-run", action="store_true", help="작업판을 바꾸지 않고 할 일만 출력")
    try:
        a = ap.parse_args(rest)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    try:
        out = ask(a) if cmd == "ask" else answer(a)
    except Fail as e:
        print(f"board-{cmd}: {e}", file=sys.stderr)
        return e.code
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    elif cmd == "ask":
        print(out["id"] if out["id"] else f"(dry-run) bd {' '.join(out['dryRun'])}")
    else:
        print(f"{a.id}: {'이미 닫힘' if out.get('already') else '닫음' if out['closed'] else '코멘트만 남김' if out['commented'] else '(dry-run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
