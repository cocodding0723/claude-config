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
KIND_ORDER = (DECISION, MERGE, REVIEW, INPUT)  # 정렬 순서. 목록 표시 순서(KINDS)와 별개
UNKNOWN_HUB = "미상"  # 허브 근거를 못 찾은 행

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
_PREFIX = re.compile(r"^(?:\[(?:낡음|근거 없음·오래됨)\] )?([^:\s]+): ")
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
    targets: tuple = ()  # 대상 키("pr:746"·"decision:D-X"·"ledger:F1"). 같은 대상이면 한 행으로 합치고, 대상이 끝나면 완료 처리
    since: float = 0.0  # 기다리기 시작한 시각(epoch). 오래된 순 정렬용


def hub_of(key, title=""):
    """항목 키·제목에서 허브 이름. 허브 항목이 아니면 빈 값"""
    if key.startswith("hub:"):
        return key.split(":", 2)[1]
    m = _PREFIX.match(title or "")
    return m.group(1) if m and key.startswith("decision:") is False else ""


def _body(key, title):
    """제목에서 허브 접두사·낡음 표시 제거"""
    t = re.sub(r"^(?:\[(?:낡음|근거 없음·오래됨)\] )?", "", title or "")
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


def pr_numbers(text):
    return list(dict.fromkeys(_PR_NUM.findall(text)))


def targets_of(key, kind, title):
    """행이 가리키는 대상 키. PR 머지 행은 PR 번호, 결정 행은 결정 번호, 장부 행은 장부 번호.
    그 밖(직접 입력·검수)은 한 행에 여러 일이 섞여 대상으로 완료를 판정할 수 없어 빈 값"""
    if key.startswith("pr:"):
        return (f"pr:{key.rsplit('#', 1)[-1]}",)
    if key.startswith("decision:"):
        return (key,)
    if key.startswith("ledger:"):
        return (key,)
    body = _body(key, title)
    if kind == MERGE:
        return tuple(f"pr:{n}" for n in pr_numbers(body))
    if kind == DECISION and not _COUNT.search(body):
        return tuple(f"decision:{c}" for c in dict.fromkeys(_CODE.findall(body)))
    return ()


def retarget(row, targets):
    """대상이 바뀐 행의 제목·답하는 법을 다시 씀(합침·일부 완료 뒤). 대상 종류가 없으면 그대로"""
    row.targets = tuple(targets)
    if row.kind == MERGE and targets and all(t.startswith("pr:") for t in targets):
        nums = [t[3:] for t in targets]
        row.title = "·".join(f"#{n}" for n in nums) + " 머지"
        row.answer = ", ".join(f"#{n}" for n in nums) + " 머지 버튼"
    elif row.kind == DECISION and not row.key.startswith("decision:") and targets and all(t.startswith("decision:") for t in targets):
        row.title = f"{targets[0][len('decision:'):]} 결정"
        row.answer = f"결정 {targets[0][len('decision:'):]} A 식으로 카드에 답"
    return row


def sort_key(row):
    """목록 정렬 키: 급함 높은 순 → 종류(결정·머지·검수·직접 입력) → 오래된 순 → 제목"""
    kind = KIND_ORDER.index(row.kind) if row.kind in KIND_ORDER else len(KIND_ORDER)
    return (URGENCY_MAX - row.urgency, kind, row.since or float("inf"), row.title)


ORDER_URGENCY, ORDER_KIND = 100_000_000_000, 10_000_000_000  # 정렬 열 숫자의 자릿수(epoch 는 10자리)


def order_value(row):
    """정렬 키를 숫자 하나로 만든 값. 작을수록 위. 목록 보기에서 이 열 오름차순으로 정렬하면 sort_key 순서.
    행 자신의 값만 쓰므로 다른 행이 생기거나 사라져도 바뀌지 않음(순위 번호를 쓰면 매번 전 행을 고쳐야 함)"""
    urg, kind, since, _ = sort_key(row)
    return urg * ORDER_URGENCY + kind * ORDER_KIND + (int(since) if since != float("inf") else ORDER_KIND - 1)


def urgency(bucket, waited_s):
    """급함 1~3. 대기 시간 구간 + 막힘이면 한 단계 위"""
    level = next((lv for sec, lv in URGENCY_STEPS if waited_s >= sec), URGENCY_MIN)
    if bucket == "block":
        level += BLOCKED_BOOST
    return max(URGENCY_MIN, min(URGENCY_MAX, level))


def build_rows(items, now, first_seen, same_title=None):
    """(key, bucket, title, since) 목록 → 할 일 행. 같은 제목은 한 행(대상 없는 행만. 버린 항목의 키는 same_title 목록에 담음).
    first_seen: {key: epoch} 처음 본 시각(허브 항목의 대기 시간 기준)"""
    out, seen = [], set()
    for key, bucket, title, since in items:
        kind = classify(key, bucket, title)
        if not kind:
            continue
        hub = hub_of(key, title)
        t = make_title(key, kind, title, hub)
        targets = targets_of(key, kind, title)
        if t in seen and not targets:  # 대상이 있는 행은 todo_sync.settle 이 합침(기존 행을 남기려고)
            if same_title is not None:
                same_title.append(key)
            continue
        seen.add(t)
        start = since if key.startswith("decision:") and since else first_seen.get(key, now)
        out.append(Row(key, kind, t, urgency(bucket, max(0, now - start)), hub, answer_how(key, kind, title, hub), targets, start))
    return out
