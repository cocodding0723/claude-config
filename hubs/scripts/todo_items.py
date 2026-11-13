#!/usr/bin/env python3
"""할 일 목록 행 규칙: 진행 스냅샷 항목 중 사용자가 손댈 것만 골라 종류·급함·제목·답하는 법을 정함. 순수 함수만 있고 Slack·파일은 건드리지 않음.

종류 4개: 결정(카드·선택지 답) · 머지(사용자 병합) · 직접 입력(허브 창 승인·직접 명령) · 검수.
허브 진행(🔄)·PR 상태는 대상이 아님. 진행 목록 DM 에만 나감.
급함 기준(URGENCY_STEPS·BLOCKED_BOOST)은 이 모듈 한 곳에서만 정의함.
"""
import re
from dataclasses import dataclass

DECISION, MERGE, INPUT, REVIEW = "decision", "merge", "input", "review"
KINDS = ((DECISION, "🙋 결정"), (MERGE, "🔀 머지"), (INPUT, "✍️ 직접 입력"), (REVIEW, "👀 검수"))
KIND_LABEL = dict(KINDS)

# 급함 1~3: 기다린 시간이 길수록 높고, 막힘(다른 일이 이 답을 기다림)이면 한 단계 올림
URGENCY_MIN, URGENCY_MAX = 1, 3
URGENCY_STEPS = ((24 * 3600, 3), (4 * 3600, 2))  # (대기 초 이상, 급함). 위에서부터 먼저 맞는 값
BLOCKED_BOOST = 1

TITLE_MAX = 60
# 문구로 종류를 가름. 앞 규칙이 우선(결정 → 검수 → 머지 → 직접 입력)
_RULES = (
    (DECISION, re.compile(r"결정|\bD-[A-Z0-9]{3,}|\(A/B\)|추천 [A-Z]\b|사용자 답|답 대기")),
    (REVIEW, re.compile(r"검수|사용자 확인")),
    (MERGE, re.compile(r"병합|머지")),
    (INPUT, re.compile(r"직접 (?:승인|입력|실행)|승인|\(사용자|사용자 실행|리베이스")),
)
_PREFIX = re.compile(r"^(?:\[낡음\] )?([^:\s]+): ")
_NOISE = re.compile(r"ctx_[0-9a-f]+|\b[0-9a-f]{7,}\b|\([^)]*\)|\[[^\]]*\]")
_CARD_HEAD = re.compile(r"^(?::[^\s:]+:\s*)+|^[^\s:]*선택해주세요:\s*")  # 슬랙 이모지 코드(:이름:)와 옛 머리말
_LEDGER_ID = re.compile(r"^[A-Z]+\d+ ")
_PR_NUM = re.compile(r"#(\d+)")
_CODE = re.compile(r"\bD-[A-Z0-9]{3,}")
_COUNT = re.compile(r"결정(?: 대기)? ?(\d+)개")
_FILLER = re.compile(r"\s*(?:결정 대기|결정|병합 대기|승인 대기|답 대기|대기|사용자 답|사용자 확인|사용자|검수)\s*")
_FUTURE = re.compile(r"예정")  # 앞으로의 일은 지금 손댈 것이 아님


@dataclass
class Row:
    key: str
    kind: str
    title: str
    urgency: int
    hub: str
    answer: str


def hub_of(key, title=""):
    """항목 키·제목에서 허브 이름. 허브 항목이 아니면 빈 값"""
    if key.startswith("hub:"):
        return key.split(":", 2)[1]
    m = _PREFIX.match(title or "")
    return m.group(1) if m and key.startswith("decision:") is False else ""


def _body(key, title):
    """제목에서 허브 접두사·낡음 표시 제거"""
    t = re.sub(r"^(?:\[낡음\] )?", "", title or "")
    if key.startswith("hub:"):
        t = _PREFIX.sub("", t, count=1)
    if key.startswith("decision:"):
        t = _CARD_HEAD.sub("", t.removeprefix("결정 "), count=1).rstrip("…")  # 카드 머리말(이모지 코드)·잘림 표시 제거
    if key.startswith("ledger:"):
        t = _LEDGER_ID.sub("", t, count=1)  # 장부 번호(F11 등)는 내부 기호
    return t.strip()


def classify(key, bucket, title):
    """사용자가 손댈 종류 또는 None. 답 대기·막힘만 후보(진행 중은 대상 아님)"""
    if key.startswith("decision:"):
        return DECISION
    if key.startswith("pr:"):
        return MERGE if bucket == "wait" else None
    if bucket not in ("wait", "block"):
        return None
    if key.startswith("hub:") or key.startswith("ledger:"):
        text = _body(key, title)
        if _FUTURE.search(text):
            return None
        for kind, rx in _RULES:
            if rx.search(text):
                return kind
        return REVIEW if key.startswith("ledger:") and bucket == "wait" else None  # 허브 막힘 중 문구 없는 것은 사용자 몫이 아님
    return None


def _core(text):
    """제목 본문: 해시·ctx·괄호·대괄호·군더더기 제거"""
    t = _FILLER.sub(" ", _NOISE.sub("", text))
    return re.sub(r"\s+", " ", t.replace(" + ", " ")).strip(" :·,-—+")


def make_title(key, kind, title, hub):
    """할 일 동사형 한 줄. PR 제목·커밋 문구·내부 기호(해시·ctx)는 넣지 않음"""
    body = _body(key, title)
    if kind == MERGE:
        nums = list(dict.fromkeys(_PR_NUM.findall(title if key.startswith("pr:") else body)))
        if key.startswith("pr:"):
            nums = nums[:1]
        return ("·".join(f"#{n}" for n in nums) + " 머지") if nums else (_core(body) or "병합") + " 머지"
    if kind == DECISION:
        count = _COUNT.search(body)
        codes = list(dict.fromkeys(_CODE.findall(body)))
        if count:
            return f"결정 카드 {count.group(1)}건 답하기"
        if codes:
            return f"{codes[0]} 결정"  # 카드 번호가 있으면 그것이 사용자가 아는 이름
        core = _core(body)
        if core.endswith("?"):
            return cut(core)  # 이미 질문 형태
        return cut(f"{core} 결정" if core else "결정 답하기")
    core = _core(_CODE.sub("", body)) or "확인"
    if kind == REVIEW:
        return cut(core if core.endswith("검수") else f"{core} 검수")
    return cut(f"{core} — {hub} 허브 창" if hub else core)


def cut(text, limit=TITLE_MAX):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def answer_how(key, kind, title, hub):
    """사용자가 어디서 무엇을 하면 되는지 한 줄"""
    body = _body(key, title)
    if kind == MERGE:
        nums = list(dict.fromkeys(_PR_NUM.findall(title if key.startswith("pr:") else body)))
        return (", ".join(f"#{n}" for n in nums) + " 머지 버튼") if nums else "GitHub 에서 머지 버튼"
    if kind == DECISION:
        codes = _CODE.findall(body)
        ident = codes[0] if codes else ""
        return f"결정 {ident} A 식으로 카드에 답".replace("  ", " ") if ident else "결정 카드의 버튼 선택"
    if kind == REVIEW:
        return f"{hub} 허브에서 확인 뒤 승인·수정 요청" if hub else "확인 뒤 승인·수정 요청"
    return f"{hub} 허브 창 1. Yes" if hub else "허브 창 1. Yes"


def urgency(bucket, waited_s):
    """급함 1~3. 대기 시간 구간 + 막힘이면 한 단계 위"""
    level = next((lv for sec, lv in URGENCY_STEPS if waited_s >= sec), URGENCY_MIN)
    if bucket == "block":
        level += BLOCKED_BOOST
    return max(URGENCY_MIN, min(URGENCY_MAX, level))


def build_rows(items, now, first_seen):
    """(key, bucket, title, since) 목록 → 할 일 행. 같은 제목은 한 행. first_seen: {key: epoch} 처음 본 시각(허브 항목의 대기 시간 기준)"""
    out, seen = [], set()
    for key, bucket, title, since in items:
        kind = classify(key, bucket, title)
        if not kind:
            continue
        hub = hub_of(key, title)
        t = make_title(key, kind, title, hub)
        if t in seen:
            continue
        seen.add(t)
        start = since if key.startswith("decision:") and since else first_seen.get(key, now)
        out.append(Row(key, kind, t, urgency(bucket, max(0, now - start)), hub, answer_how(key, kind, title, hub)))
    return out
