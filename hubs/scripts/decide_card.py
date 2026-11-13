"""결정 카드 필수 항목 규칙. 접수(hub_report.py add)와 직접 호출(slack_notify.sh)이 이 모듈 하나를 공유함.

필수 3가지: 본문 '배경:' 줄 · 본문 '용어:' 줄(낯선 말이 없으면 '없음') · 선택지마다 결과.
결과는 --options 값에 '번호=문구 => 고르면 달라지는 점' 으로 씀. 결과 칸이 없는 옛 형식은 거부함(요구가 바뀐 것이라 호환 모드를 두지 않음).
선택 항목: 본문 '이유:' 줄 = 추천 이유. 카드에서는 추천 줄 뒤에 붙음.
카드 렌더 순서: 제목 → 배경 → 선택지(각 결과) → 추천(+이유) → 용어 → 답 줄.
"""
import json
import re
import unicodedata

RESULT_SEP = "=>"  # --options 의 문구와 결과 사이 구분
BACKGROUND, TERMS, REASON = "배경", "용어", "이유"
BACKGROUND_MIN = 10  # '배경: 없음' 같은 빈 껍데기 거부
LINE_MAX = 200  # 배경·용어 한 줄 상한
RESULT_MAX = 100  # 선택지 결과 상한
MAX_LINES, MAX_CHARS = 8, 1000  # decide 본문 상한(제목·배경·용어·이유·참고 포함). 카드 전체는 Slack 블록 3000자 안
NO_TERMS = "없음"

OPT_SPLIT = re.compile(r",[ \t]*(?=[0-9]+[ \t]*=)")  # 문구 안 쉼표는 뒤에 '숫자=' 가 올 때만 구분자. 두 자리 번호도 구분자로 봐 범위 오류로 거부
OPT_ITEM = re.compile(r"[ \t]*([0-9]+)[ \t]*=(.*)", re.S)
RESULT_SPLIT = re.compile(r"[ \t]*=>[ \t]*")

TITLE_SHOW = 40  # 거부 안내에 보이는 옛 제목 길이
_TITLE_HEAD = re.compile(r"^(?:\s*:[^\s:]+:)+\s*|^[^\s:]*선택해주세요\s*:\s*")  # 슬랙 이모지 코드(:비서-선택해주세요:)와 옛 머리말


def title_key(title: str) -> str:
    """제목 비교용 정규화: 이모지 코드·머리말·이모지·공백 차이를 지움. 같은 질문의 재전송·재질문은 같은 값"""
    t = _TITLE_HEAD.sub("", unicodedata.normalize("NFC", str(title or "")).replace("*", ""), count=1)
    t = "".join(c for c in t if unicodedata.category(c) not in ("So", "Sk", "Cf"))
    return re.sub(r"\s+", "", t).casefold()


def first_line(text: str) -> str:
    return str(text or "").split("\n", 1)[0]


def reuse_problem(did: str, title: str, sources: list) -> tuple:
    """이미 다른 질문에 쓰인 결정 번호인가. 반환 (거부 사유, 경고) — 둘 다 빈 문자열이면 통과.
    sources: [(경로, 제목 꺼내는 함수)] — 카드를 올린 기록(허브 보고 outbox·보낸 카드 기록) 전부. 번호가 같고 정규화한 제목이 하나도 같지 않으면 거부.
    같은 질문 재전송·충돌 재질문(같은 번호·같은 질문)은 통과. 기록을 못 읽으면 경고만 하고 통과(차단으로 결정을 막지 않음)"""
    seen, warn = [], ""
    for path, get in sources:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("decision_id") == did and r.get("kind", "decide") == "decide":
                        seen.append(str(get(r) or ""))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            warn = f"경고: 결정 번호 {did} 재사용 검사 못 함(기록 읽기 실패) — 그대로 진행함"
    seen = [t for t in seen if title_key(t)]
    if not seen or title_key(title) in {title_key(t) for t in seen}:
        return "", warn
    return (f"이 결정 번호는 다른 질문에 이미 쓰임({seen[-1][:TITLE_SHOW]}) — 새 번호 쓰기(예: {did}2)", warn)


EXAMPLE = ("본문 예: '알림 시각 정하기' 다음 줄에 '배경: 알림 개편 작업 중, 시각이 화면마다 달라 하나로 맞추려 함' · "
           "'용어: 푸시 — 앱 밖 알림' / --options 예: '1=오전 9시 => 출근 전에 도착,2=오전 10시 => 업무 시작 뒤에 도착'")


def _label_re(label: str) -> "re.Pattern":
    return re.compile(r"^[ \t]*(?:[-•*][ \t]*)?" + label + r"[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)


LINE_RE = {k: _label_re(k) for k in (BACKGROUND, TERMS, REASON)}


def options(raw: str) -> list:
    """--options 값 → [(번호, 문구, 결과)] (결과 칸이 없으면 빈 문자열). 형식이 틀리면 ValueError"""
    out = []
    for part in OPT_SPLIT.split(unicodedata.normalize("NFC", raw)):
        m = OPT_ITEM.fullmatch(part)
        if not m:
            raise ValueError("--options 형식 오류 — '1=문구 => 고르면 달라지는 점,2=문구 => …' 처럼 번호=문구 를 쉼표로 이어 쓰세요")
        label, result = (s.strip(" \t") for s in (RESULT_SPLIT.split(m.group(2), maxsplit=1) + [""])[:2])
        out.append((m.group(1), label, result))
    return out


def value_of(body: str, key: str) -> str:
    """본문에서 '키: 값' 줄의 값(첫 줄). 없으면 빈 문자열"""
    m = LINE_RE[key].search(body or "")
    return m.group(1) if m else ""


def take_lines(msg: str) -> tuple:
    """본문에서 '용어:'·'이유:' 줄을 빼서 (나머지 본문, 용어 줄 원문, 이유 값). 카드 렌더가 추천 줄 뒤로 옮길 때 씀"""
    terms, why, keep = "", "", []
    for ln in msg.split("\n"):
        if LINE_RE[TERMS].match(ln) and not terms:
            terms = ln.strip()
        elif LINE_RE[REASON].match(ln) and not why:
            why = LINE_RE[REASON].match(ln).group(1)
        else:
            keep.append(ln)
    return "\n".join(keep), terms, why


def problem(body: str, options_raw: "str | None" = None) -> str:
    """필수 항목 검사. 문제가 없으면 빈 문자열, 있으면 '원인 — 조치(예시)' 한 문장.
    options_raw 가 None 이면 선택지 결과는 검사하지 않음(--choices 카드: 선택지가 글자 A·B 라 결과 칸이 없음)"""
    miss, bad = [], []
    bg = value_of(body, BACKGROUND)
    if not bg:
        miss.append("배경")
    elif len(bg) < BACKGROUND_MIN:
        bad.append(f"배경이 {len(bg)}자 — {BACKGROUND_MIN}자 이상으로 지금 하던 작업과 질문이 생긴 이유를 씀")
    elif len(bg) > LINE_MAX:
        bad.append(f"배경이 {len(bg)}자 — {LINE_MAX}자 이내 1~2줄로 줄임")
    terms = value_of(body, TERMS)
    if not terms:
        miss.append("용어")
    elif len(terms) > LINE_MAX:
        bad.append(f"용어가 {len(terms)}자 — {LINE_MAX}자 이내로 줄임")
    if options_raw is not None:
        try:
            rows = options(options_raw)
        except ValueError as err:
            return str(err)
        no_result = [n for n, _l, r in rows if not r]
        if no_result:
            miss.append("선택지 결과(" + ", ".join(f"{n}번" for n in no_result) + ")")
        for n, _l, r in rows:
            if len(r) > RESULT_MAX:
                bad.append(f"{n}번 결과가 {len(r)}자 — {RESULT_MAX}자 이내로 줄임")
    if not (miss or bad):
        return ""
    parts = []
    if miss:
        parts.append("없는 항목: " + ", ".join(miss))
    parts += bad
    return (f"결정 카드 필수 항목 부족(전송 안 함) — {'; '.join(parts)}. 조치: 본문에 '배경: …'(지금 무슨 작업 중이고 왜 이 질문이 생겼는지, 처음 보는 사람 기준 1~2줄)"
            f"·'용어: …'(낯선 말·파일·내부 이름 한 줄 풀이, 없으면 '{NO_TERMS}')를 쓰고 선택지마다 '번호=문구 => 고르면 달라지는 점'을 씀. {EXAMPLE}")
