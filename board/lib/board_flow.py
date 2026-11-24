"""작업 흐름 규칙(한 곳): 단계 이름, 작업 크기, 계획 승인 판정, 구현 카드 집기 조건.
board-claim(집기)과 board-stage(단계 옮기기)가 같이 씀. 규칙을 글로만 두면 건너뛰므로 집기 도구가 막음.

흐름: 0 접수 → 1 이해 → 2 설계(계획 승인) → 3 구현 → 4 검증 → 5 검수 → 6 반영.
크기: 작음은 설계를 건너뛰고(0 → 3) 보통·큼은 계획 승인 뒤에만 구현 카드를 집음. 작음은 다른 모델 검수·녹화 없이 반영으로 가고, 사용자 검수는 반영 뒤 모아 받음(board-done --review-batch).
적용 대상: 기능 카드(epic)에 label flow:v1 이 있는 기능만. 옛 방식으로 떠 있는 기능은 막지 않음.

계획 승인 = 그 기능의 계획 승인 카드(결정 번호 plan_id(기능 카드 id), 1번 선택지가 '승인')에 사용자가 Slack·문서 사이트에서 1번이나 추천대로를 고른 것,
또는 사용자가 그 기능 코디네이터 창에 '승인'·'스킵'으로 시작하는 글을 직접 친 것(세션 기록에서 읽음, board_said).
터미널에서 남긴 답(decide-answer)은 세지 않음: 코디네이터가 스스로 승인할 수 있게 되기 때문.

건너뛰기(11-19 사용자 결정: 준비만): 사용자가 급한 일에서 이해(1)·계획(2)을 건너뛰게 한 기능은 label skip:understand·skip:plan 을 가짐.
skip:plan 이 있으면 계획 승인 없이 구현 카드를 집음(작은 일과 같은 길). 검증·검수·병합 승인은 건너뛰지 못함.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass

import board_said

FLOW_LABEL = "flow:v1"
SIZE_HEAD = "size:"
STAGE_HEAD = "stage:"
SMALL, NORMAL, LARGE = "small", "normal", "large"
SIZES = {SMALL: "작음", NORMAL: "보통", LARGE: "큼"}
PLAN_HEAD = "D-PLAN"
ID_BODY_MAX = 16 - len("PLAN")  # 결정 번호는 D- 뒤 16자까지(hub_report.DECISION_ID_RE)
APPROVE_WORD = "승인"            # 계획 승인 카드의 1번 선택지는 이 말로 시작
REVIEW_HEAD = "D-REVW"           # 검수 결정 번호 머리글자. D-PLAN 과 겹치지 않음. 몸통 길이는 PLAN 과 같아 ID_BODY_MAX 공용
PASS_WORD = "통과"               # 검수 결정 카드의 1번 선택지는 이 말로 시작
REVIEW_LABEL = "review:v1"       # 검수 단계로 넘어갈 때(board-done --epic) 붙음. 이 표시가 있는 기능만 통과 답을 Slack 기록과 대조
USER_SOURCES = ("slack", "docsite")  # 사용자가 직접 누른 답만. 터미널 기록은 코디네이터가 쓸 수 있음
POLLED_TYPES = ("reaction", "thread_reply")  # 폴러가 Slack 에서 읽어 온 사용자 반응·카드 스레드 글(출처 칸 없이 기록됨)
SENT_NAME, INBOX_NAME = "slack-sent.jsonl", "slack-inbox.jsonl"

# 검수 카드 올리기(ask_review): 보고 도구(hub_report.py add --kind decide)를 하위 프로세스로 불러 결정 카드를 대기열에 넣음
REPORT_ENV, REPORT_NAME = "LRM_HUB_REPORT", "hub_report.py"  # 보고 도구 경로 덮어쓰기(feature_open.sh 와 같은 이름). 기본은 허브 폴더 아래
OUTBOX_NAME = "hub-reports.jsonl"  # 보고 대기열. 보고 도구의 LRM_REPORT_OUTBOX 가 있으면 그 경로
FEATURE_LABEL_HEAD = "feature:"
HUB_PREFIX = "feat-"             # feature_open.sh 가 기능 허브 이름을 feat-<slug> 로 지음
REVIEW_TASK = "없음"             # 보고 도구의 작업 번호 칸. 기능 카드 단위 보고라 번호 없음
TITLE_MAX = 40                   # decide 첫 줄 상한(hub_report.MAX_TITLE)
TITLE_TAIL = " 검수"
TITLE_CUT = "…"
REVIEW_OPTIONS = (f"1={PASS_WORD} => 반영 단계로 넘어가 PR·병합 승인을 요청한다,"
                  "2=고칠 점 => 답글로 적어 준 내용을 고친 뒤 다시 검수를 요청한다")
REVIEW_RECOMMEND, REVIEW_REVERSIBLE = "1", "쉬움"
REVIEW_BACKGROUND = "구현·검증이 끝나 사용자 검수만 남았다. 결과 상세는 첨부한 완료 보고서에 있다."
REVIEW_BACKGROUND_NODOC = "구현·검증이 끝나 사용자 검수만 남았다. 완료 보고서는 찾지 못했다."
# 완료 보고서: 기능 카드의 "기능 완료" 코멘트에 적힌 html 경로. 검수 카드·작은 기능 완료 보고에 첨부
# (11-24 사용자: 완료 때 draft PR 만 오고 보고서가 안 옴)
DONE_NOTE = "기능 완료"
DOC_PATH_RE = re.compile(r"(?:~|/)[^\s`'\"]+\.html")
DONE_KIND = "done"
DONE_TAIL = " 완료"
REVIEW_TERMS = "없음"
REVIEW_NOTE_HEAD = "확인할 것"
# 작은 기능 빠른 길: 다른 모델 검수·녹화 없이 닫고 반영 단계로 감. 사용자 검수는 반영 뒤 하루치를 한 장으로 모아 받음
SMALL_SKIP = ("review", "recording")  # 작은 기능이 board-done --epic 에서 요구받지 않는 증거
BATCH_LABEL, BATCHED_LABEL = "review:batch", "review:batched"  # 모아 받기 대기 / 모아 올림 끝
BATCH_NOTE = "모아 받기 카드:"   # 모아 올릴 때 기능 카드에 남기는 머리. 결정 번호가 날짜·묶음 지문이라 나중에 다시 계산할 수 없음
BATCH_HEAD = "D-BTCH"            # 모아 받기 결정 번호 머리글자. 몸통 길이는 PLAN 과 같아 ID_BODY_MAX 공용
BATCH_TITLE = "작은 기능 {n}건 모아 검수"
BATCH_OPTIONS = (f"1={PASS_WORD} => 모두 그대로 둔다,"
                 "2=고칠 점 => 답글로 기능 이름과 고칠 점을 적으면 수정 카드로 처리한다")
BATCH_BACKGROUND = "작은 기능은 다른 모델 검수·녹화 없이 반영 단계로 넘겼다. 하루치를 모아 한 번에 확인받는다."
BATCH_LIST_HEAD = "기능"
BATCH_MAX = 4  # 카드 한 장에 싣는 기능 수. 결정 카드 본문 8줄 상한 - 머리 4줄(제목·배경·용어·목록 머리)
LABEL_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")  # 카드 제목 앞 '[LRM-782]' 같은 티켓 머리표
SENT_TITLE_HEAD = re.compile(r"^\s*:[^\s:]+:\s*")  # 보낸 카드 기록 제목 앞 슬랙 이모지 코드
# 코디네이터 창에 직접 친 승인·건너뛰기: 글이 이 말로 시작할 때만("승인", "계획 승인한다", "스킵하고 진행", "전부 추천", "ㅇㅇ 추천대로 가자").
# "진행해"·"ㄱㄱ" 같은 말은 세지 않음: 다른 얘기 끝에 붙는 말이라 뜻하지 않은 승인이 됨
# 앞의 맞장구("ㅇㅇ"·"네")는 허용. 11-19~20 실제 입력에서 "전부 추천"·"추천대로"가 승인으로 안 읽혀 Slack 카드를 다시 기다렸음
YES_HEAD = r"(?:(?:ㅇㅇ|ㅇㅋ|응|네|넵|예|오케이|ok|okay)[\s,.!~]*)?"
TYPED_OK = re.compile(r"^\s*" + YES_HEAD + r"(?:계획\s*)?(?:승인|스킵|skip|건너뛰|전부\s*추천|추천\s*대로)", re.I)
# 검수 통과로 친 글: "통과", "검수 통과", "통과, PR 올려", "LGTM"
REVIEW_OK = re.compile(r"^\s*" + YES_HEAD + r"(?:검수\s*)?(?:통과|lgtm)", re.I)
# 첫 문장에 이 말이 있으면 승인·통과가 아님: "승인 전에 하나만", "통과 못 시킴", "승인?", "통과는 보류"
TYPED_NO = re.compile(r"[?？]|말고|말아|마라|지\s*마|지\s*말|지\s*않|안\s*(?:함|해|할|돼|됨|된)|못\s*(?:함|해|하|시)|보류|취소|전에|아니|아님|불가|기다려|나중에")
FIRST_SENTENCE = re.compile(r"^[^\n.!?？。]*[?？]?")
# 건너뛸 수 있는 준비 항목: 이름 → (사용자에게 보일 이름, 이 단계에 들어서면 이미 지난 항목)
SKIP_HEAD = "skip:"
SKIP_ITEMS = {"understand": ("이해", "design"), "plan": ("계획", "build")}
SKIP_BUNDLES = {"급함": ("understand", "plan")}  # 묶음 이름 → 한 번에 거는 항목
# 건너뛸 수 없는 것의 이름. '스킵 다른모델검수'를 계획 승인으로 읽지 않고 거절 안내를 하려고 둠
SKIP_REFUSED = ("다른모델검수", "모델검수", "검수", "사용자검수", "내검수", "검증", "녹화", "캡처", "촬영", "컴파일", "시험", "병합", "병합승인")
# 코디네이터 창에 친 건너뛰기 글. 글 전체가 이 꼴일 때만: "급함 아님"처럼 다른 말이 붙은 글을 건너뛰기로 읽지 않게
URGENT_TEXT = re.compile(r"^\s*급함(?:으로)?(?:\s*(?:진행|가자|ㄱㄱ|해줘|처리))?[\s.!~]*$")
SKIP_TEXT = re.compile(r"^\s*(?:스킵|skip|건너뛰기?)\s+(.+?)[\s.!~]*$", re.I)
HUB_LINE = re.compile(r"^[ \t>*-]*기능 코디네이터 허브[ \t]*:[ \t]*(\S+)", re.M)  # feature_open.sh 가 기능 카드 설명에 적는 줄(feedback_route.HUB_LINE_RE 와 같은 꼴)


@dataclass(frozen=True)
class Stage:
    key: str
    name: str

    @property
    def label(self) -> str:
        return STAGE_HEAD + self.key


# 순서가 곧 단계 번호. review 는 기존 label stage:review(사용자 검수 대기, 상한 검사가 셈)와 같은 이름
STAGES = (Stage("intake", "접수"), Stage("understand", "이해"), Stage("design", "설계"), Stage("build", "구현"),
          Stage("verify", "검증"), Stage("review", "검수"), Stage("ship", "반영"))
BUILD = "build"


HAND_OFF = ("review", "ship")  # 완료 검사(board-done --epic)와 사용자 검수 답이 옮기는 단계. 손으로 옮기지 않음
INTAKE_FIELDS = ("목표", "확인 방법")  # 접수에서 반드시 정할 두 문장. 이후 보고·계획서의 첫 줄이 됨


def stage(key: str) -> "Stage | None":
    """단계 찾기. 영문 이름·한글 이름·번호(0~6) 모두 받음"""
    k = str(key or "").strip()
    if k.isdigit() and int(k) < len(STAGES):
        return STAGES[int(k)]
    return next((s for s in STAGES if k in (s.key, s.name)), None)


def order(st: Stage) -> int:
    return STAGES.index(st)


def intake_gaps(desc: str) -> list:
    """기능 카드 설명에서 비어 있는 접수 칸 이름들('- 목표: …' 꼴의 줄, 내용이 있어야 채운 것).
    흔한 표기는 같은 것으로 봄: 굵게 쓴 이름(**목표**:), 전각 쌍점(：), 낱말 사이를 붙여 쓴 이름(확인방법)"""
    def filled(name: str) -> bool:
        words = r"[ \t]*".join(re.escape(w) for w in name.split())
        return bool(re.search(rf"^[ \t>*-]*{words}\**[ \t]*[:：][ \t]*\S", desc or "", re.M))
    return [f for f in INTAKE_FIELDS if not filled(f)]


def stage_block(epic: dict, target: str, approved) -> str:
    """기능을 이 단계로 옮기면 안 되는 이유(원인 — 조치). 옮겨도 되면 ''. 옛 방식 기능(flow:v1 없음)은 표시만 하고 막지 않음"""
    st = stage(target)
    if st is None:
        return f"모르는 단계 '{target}' — {' · '.join(f'{i} {x.name}({x.key})' for i, x in enumerate(STAGES))} 중 하나"
    if st.key in HAND_OFF:
        return f"{st.name} 단계는 손으로 옮기지 않음 — 완료 검사 board-done --epic {epic.get('id')} <이름> 이 검수로 옮기고, 사용자 검수 답(--user-ok)이 그다음으로 옮김"
    if FLOW_LABEL not in feature_labels(epic) or st.key == STAGES[0].key:
        return ""
    gaps = intake_gaps(epic.get("description") or "")
    if gaps:
        return (f"접수 칸이 비어 있음: {', '.join(gaps)} — 기능 카드 설명에 '- 목표: <만들 것 한 문장>' 과 "
                f"'- 확인 방법: <끝났는지 확인할 방법 한 문장>' 을 사용자 원문대로 적은 뒤 다시 실행(bd update {epic.get('id')} -d …)")
    if not size_of(epic):
        return f"크기를 정하지 않음 — --size {SMALL}|{NORMAL}|{LARGE} 를 함께 주거나 bd label add {epic.get('id')} {SIZE_HEAD}<크기>"
    if order(st) >= order(stage(BUILD)):
        return claim_block({"id": "", "issue_type": "task", "status": "open"}, epic, approved)
    return ""


def labels(card: "dict | None") -> list:
    return list((card or {}).get("labels") or [])


def is_feature(card: "dict | None") -> bool:
    """기능 카드(epic)인가. 상위 카드가 있거나 epic 이 아닌 카드는 기능 표시가 붙어 있어도 기능으로 보지 않음"""
    card = card or {}
    return not card.get("parent") and card.get("issue_type") in (None, "epic")


def feature_labels(card: "dict | None") -> list:
    """기능 판정에 쓰는 label. 하위 카드에 복사된 단계·크기 표시로 재검수 순환이 생겨(lrm-7cai.4~.9) 기능 카드 것만 셈"""
    return labels(card) if is_feature(card) else []


def stray_marks(card: "dict | None") -> list:
    """하위 카드에 잘못 붙은 기능 표시(단계·크기·흐름·검수 대조)"""
    if is_feature(card):
        return []
    return [x for x in labels(card) if x.startswith((STAGE_HEAD, SIZE_HEAD)) or x in (FLOW_LABEL, REVIEW_LABEL)]


def current(epic: "dict | None") -> "Stage | None":
    """카드에 적힌 단계(없으면 None). 여러 개가 붙어 있으면 가장 뒤 단계"""
    have = [s for s in STAGES if s.label in feature_labels(epic)]
    return have[-1] if have else None


def skips(epic: "dict | None") -> tuple:
    """이 기능에서 사용자가 건너뛰기로 한 항목들(SKIP_ITEMS 순서)"""
    have = {x[len(SKIP_HEAD):] for x in feature_labels(epic) if x.startswith(SKIP_HEAD)}
    return tuple(k for k in SKIP_ITEMS if k in have)


def skip_keys(words) -> tuple:
    """항목 이름들(영문 이름·한글 이름·묶음 이름) → (항목들, 모르는 낱말들). 항목은 SKIP_ITEMS 순서, 겹치면 한 번"""
    by_name = {v[0]: k for k, v in SKIP_ITEMS.items()}
    got, unknown = set(), []
    for w in words:
        w = str(w).strip()
        if w in SKIP_BUNDLES:
            got.update(SKIP_BUNDLES[w])
        elif w in SKIP_ITEMS:
            got.add(w)
        elif w in by_name:
            got.add(by_name[w])
        elif w:
            unknown.append(w)
    return tuple(k for k in SKIP_ITEMS if k in got), tuple(unknown)


def typed_skips(text: str) -> tuple:
    """코디네이터 창에 친 글이 건너뛰기면 그 항목들, 아니면 (). '급함'(묶음) 또는 '스킵 <항목…>' 꼴이고 항목 이름만 있을 때"""
    if URGENT_TEXT.match(text or ""):
        return SKIP_BUNDLES["급함"]
    m = SKIP_TEXT.match(text or "")
    if not m:
        return ()
    keys, unknown = skip_keys(re.split(r"[\s,·]+", m.group(1)))
    return () if unknown else keys


def skip_past(epic: "dict | None", item: str) -> bool:
    """그 항목의 단계를 이미 지났는가(지난 항목은 건너뛰기를 걸거나 풀어도 달라질 것이 없음)"""
    st = current(epic)
    return st is not None and order(st) >= order(stage(SKIP_ITEMS[item][1]))


def says(pattern, text: str) -> bool:
    """글이 그 말로 시작하고, 첫 문장에 부정·보류·질문이 없는가"""
    text = text or ""
    return bool(pattern.match(text)) and not TYPED_NO.search(FIRST_SENTENCE.match(text.strip()).group(0))


def typed_plan_ok(text: str) -> bool:
    """창에 친 글이 계획 승인인가. 항목을 붙인 건너뛰기('스킵 이해')는 그 항목만 건너뛰고 계획 승인으로 세지 않음"""
    items = typed_skips(text)
    if items:
        return "plan" in items
    return says(TYPED_OK, text) and not refused_skips(text)


def typed_review_ok(text: str) -> bool:
    """창에 친 글이 검수 통과인가"""
    return says(REVIEW_OK, text)


def refused_skips(text: str) -> tuple:
    """'스킵 <낱말…>' 꼴 글에 든, 건너뛸 수 없는 것의 이름들. 그런 글은 계획 승인도 건너뛰기도 아님"""
    m = SKIP_TEXT.match(text or "")
    return tuple(w for w in re.split(r"[\s,·]+", m.group(1)) if w in SKIP_REFUSED) if m else ()


def is_control(text: str) -> bool:
    """승인·건너뛰기로 친 글인가(지시 장부에 지시로 적지 않는 글)"""
    return bool(says(TYPED_OK, text) or says(REVIEW_OK, text) or typed_skips(text))


def size_of(epic: "dict | None") -> str:
    """기능 카드의 크기(small|normal|large). 안 정했거나 모르는 값이면 ''"""
    v = next((x[len(SIZE_HEAD):] for x in feature_labels(epic) if x.startswith(SIZE_HEAD)), "")
    return v if v in SIZES else ""


def size_reason(epic: "dict | None") -> str:
    """크기를 정한 근거. 확장 지점: 크기 자동 판정 도구(board_size)를 나중에 여기 연결함. 지금은 늘 ''"""
    return ""


def skip_evidence(epic: "dict | None") -> tuple:
    """board-done --epic 이 이 기능에서 요구하지 않는 증거 종류"""
    return SMALL_SKIP if size_of(epic) == SMALL else ()


def batch_id(day: str, ids) -> str:
    """날짜 + 기능 id 목록 → 모아 받기 결정 번호. 같은 날 다른 묶음과 겹치지 않게 id 목록까지 넣음"""
    return decision_id(BATCH_HEAD, f"{day}-{','.join(ids)}")


def decision_id(head: str, card_id: str) -> str:
    """머리글자 + 기능 카드 id → 결정 번호. 길어서 자르면 다른 기능과 겹칠 수 있어 앞 6자 + id 지문 6자로 만듦"""
    body = re.sub(r"[^A-Za-z0-9]", "", card_id or "").upper()
    if len(body) > ID_BODY_MAX:
        body = body[:6] + hashlib.sha1(card_id.encode()).hexdigest()[:6].upper()
    return head + body


def plan_id(card_id: str) -> str:
    """기능 카드 id → 그 기능의 계획 승인 결정 번호. 번호를 손으로 정하지 않게 해 다른 결정의 승인을 끌어다 쓰지 못하게 함"""
    return decision_id(PLAN_HEAD, card_id)


def review_id(card_id: str) -> str:
    """기능 카드 id → 그 기능의 검수 결정 번호. 계획 승인 번호와 머리글자가 달라 서로의 답을 쓸 수 없음"""
    return decision_id(REVIEW_HEAD, card_id)


def read_jsonl(path: str) -> list:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def plan_approved(hub_dir: str, did: str) -> bool:
    """계획 승인 카드에 사용자가 승인으로 답했는가. 기록을 못 읽으면 승인 아님.
    답은 가장 최근에 올린 카드(메시지)에 달린 것만 봄: 계획을 고쳐 같은 번호로 다시 올리면 옛 카드의 승인은 새 계획의 승인이 아님.
    그 카드의 답이 여럿이면 가장 최근 것이 기준(승인 뒤 수정 요청으로 바꾸면 승인이 풀림)"""
    return first_option_picked(hub_dir, did, APPROVE_WORD)


def review_passed(hub_dir: str, did: str) -> bool:
    """검수 결정 카드에 사용자가 통과로 답했는가. 판정 규칙은 계획 승인과 같음(최근 카드의 최근 사용자 답이 1번 '통과')"""
    return first_option_picked(hub_dir, did, PASS_WORD)


def first_option_picked(hub_dir: str, did: str, word: str) -> bool:
    """그 번호의 가장 최근 카드 1번 선택지가 word 로 시작하고, 그 카드에 사용자가 낸 가장 최근 답이 1번인가"""
    card = last_card(hub_dir, did)
    if card is None or not card.get("ts") or not str(card["options"].get("1", "")).strip().startswith(word):
        return False
    last = last_answer(hub_dir, did, card)
    # 추천대로(approve)도 pick 에 추천 번호가 들어 있음. 1번일 때만 인정
    return last is not None and last["action"] in ("pick", "approve") and last.get("pick") == 1


def review_block(epic: "dict | None", hub_dir: str) -> str:
    """검수 통과 답(--user-ok)을 받아 주면 안 되는 이유(원인 — 조치). 받아도 되면 ''.
    검수 단계로 넘어갈 때 REVIEW_LABEL 이 붙은 기능만 대조함. 표시 없는 옛 기능은 이미 검수 카드를 번호 없이 올렸을 수 있어 막지 않음(경고는 호출자)"""
    if REVIEW_LABEL not in labels(epic):
        return ""
    did = review_id(str((epic or {}).get("id") or ""))
    if review_passed(hub_dir, did):
        return ""
    return (f"기능 {epic['id']} 의 검수 결정 카드 {did} 에 사용자의 통과 답(1번)이 Slack 기록에 없음 — 검수 결정 카드를 번호 {did}, "
            f"1번 선택지 '{PASS_WORD}'(2번 '고칠 점 있음')으로 올리고 사용자가 1번을 누른 뒤 다시 실행. "
            f"번호는 board-stage {epic['id']} 가 보여 줌. 사용자가 코디네이터 창에 '통과'라고 직접 쳤으면 --user-ok-typed")


def last_card(hub_dir: str, did: str) -> "dict | None":
    """그 결정 번호로 가장 최근에 올린 결정 카드 기록"""
    return next((s for s in reversed(read_jsonl(os.path.join(hub_dir, SENT_NAME)))
                 if s.get("decision_id") == did and s.get("form_kind") == "decide" and isinstance(s.get("options"), dict)), None)


def last_answer(hub_dir: str, did: str, card: dict) -> "dict | None":
    """그 카드에 사용자가 낸 가장 최근 답"""
    return next((r for r in reversed(read_jsonl(os.path.join(hub_dir, INBOX_NAME)))
                 if r.get("decision_id") == did and r.get("parent_ts") == card.get("ts") and by_user(r)
                 and r.get("action") in ("pick", "approve", "custom", "hold")), None)


def coordinator_ws(epic: "dict | None") -> str:
    """기능 카드 설명에 적힌 코디네이터 작업 공간 경로. 없으면 ''"""
    m = HUB_LINE.search(str((epic or {}).get("description") or ""))
    return m.group(1) if m else ""


def feature_hub(epic: "dict | None") -> str:
    """기능 카드 label feature:<slug> → 허브 이름 feat-<slug>. 없으면 ''"""
    slug = next((l[len(FEATURE_LABEL_HEAD):] for l in reversed(labels(epic)) if l.startswith(FEATURE_LABEL_HEAD)), "")
    return HUB_PREFIX + slug if slug else ""


def feature_name(epic: "dict | None") -> str:
    """카드 제목에서 티켓 머리표를 뗀 기능 이름. 비면 id"""
    return LABEL_PREFIX.sub("", str((epic or {}).get("title") or "")).strip() or str((epic or {}).get("id") or "")


def review_title(hub_dir: str, did: str, epic: "dict | None") -> str:
    """검수 카드 제목(본문 첫 줄). 그 번호로 앞서 올린 카드가 있으면 그 제목 그대로: 보고 도구가 같은 번호의 제목이 달라지면 거부함.
    앞 카드가 없으면 '<기능 이름> 검수'를 TITLE_MAX 안으로 맞춤"""
    outbox = os.environ.get("LRM_REPORT_OUTBOX") or os.path.join(hub_dir, OUTBOX_NAME)
    for r in reversed(read_jsonl(outbox)):
        first = str(r.get("text") or "").split("\n", 1)[0].strip()
        if r.get("decision_id") == did and r.get("kind") == "decide" and first:
            return first
    for r in reversed(read_jsonl(os.path.join(hub_dir, SENT_NAME))):
        first = SENT_TITLE_HEAD.sub("", str(r.get("title") or "")).strip()
        if r.get("decision_id") == did and r.get("form_kind") == "decide" and first:
            return first
    name = feature_name(epic)
    room = TITLE_MAX - len(TITLE_TAIL)
    if len(name) > room:
        name = name[:room - len(TITLE_CUT)].rstrip() + TITLE_CUT
    return name + TITLE_TAIL


def num(v) -> float:
    """기록의 시각 칸(숫자나 숫자 글)을 실수로. 못 읽으면 0"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def review_pending(hub_dir: str, did: str) -> bool:
    """그 번호의 검수 카드가 답을 기다리는가: 보낸 카드에 사용자 답이 아직 없거나, 보낸 뒤 대기열에 새로 들어가 아직 안 나간 카드가 있음"""
    card = last_card(hub_dir, did)
    if card is not None and last_answer(hub_dir, did, card) is None:
        return True
    since = num(card.get("at")) if card is not None else 0.0
    outbox = os.environ.get("LRM_REPORT_OUTBOX") or os.path.join(hub_dir, OUTBOX_NAME)
    return any(r.get("decision_id") == did and r.get("kind") == "decide" and num(r.get("at")) > since
               for r in read_jsonl(outbox))


def result_doc(comments) -> str:
    """기능 카드 코멘트 → 가장 최근 "기능 완료" 코멘트의 실제 있는 html 경로. 없으면 ''"""
    for text in reversed([str(c) for c in comments]):
        if DONE_NOTE not in text:
            continue
        for m in reversed(DOC_PATH_RE.findall(text)):
            path = os.path.expanduser(m)
            if os.path.isfile(path):
                return path
    return ""


def review_command(report: str, hub: str, ws: str, epic: dict, title: str, note: str = "", doc: str = "") -> list:
    """검수 카드를 올리는 보고 도구 명령(인자 목록). 본문 = 제목 / 배경 / 용어 / (덧붙임). doc 이 있으면 첨부"""
    lines = [title, f"배경: {REVIEW_BACKGROUND if doc else REVIEW_BACKGROUND_NODOC}", f"용어: {REVIEW_TERMS}"]
    if note.strip():
        lines.append(f"{REVIEW_NOTE_HEAD}: {note.strip()}")
    return ["python3", report, "add", "--hub", hub, "--task", REVIEW_TASK, "--kind", "decide", "--card", str(epic.get("id") or ""),
            "--ws", ws, *(["--file", doc] if doc else ["--no-doc"]), "--text", "\n".join(lines), "--options", REVIEW_OPTIONS, "--recommend", REVIEW_RECOMMEND,
            "--decision-id", review_id(str(epic.get("id") or "")), "--reversible", REVIEW_REVERSIBLE]


def ask_review(epic: dict, hub_dir: str, note: str = "", dry_run: bool = False, run=subprocess.run, doc: str = "") -> dict:
    """검수 결정 카드(1=통과, 2=고칠 점)를 보고 대기열에 올림. 반환:
    {id(결정 번호), title, posted, skipped(건너뛴 이유, 없으면 ''), failed(실패 사유, 없으면 ''), manual(직접 올릴 명령)}.
    skipped 는 올릴 필요가 없는 경우(답 대기 카드 있음, dry-run), failed 는 올리지 못한 경우(허브·작업 공간 모름, 보고 도구 없음·거부).
    허브 이름·작업 공간은 기능 카드에서 얻음: label feature:<slug>, 설명의 '기능 코디네이터 허브:' 줄(--shipped 와 같은 근거)"""
    did = review_id(str(epic.get("id") or ""))
    title = review_title(hub_dir, did, epic)
    hub, ws = feature_hub(epic), coordinator_ws(epic)
    report = os.environ.get(REPORT_ENV) or os.path.join(hub_dir, REPORT_NAME)
    cmd = review_command(report, hub or "<허브 이름>", ws or "<작업 공간>", epic, title, note, doc)
    out = {"id": did, "title": title, "posted": False, "skipped": "", "failed": "", "manual": shlex.join(cmd)}
    if review_pending(hub_dir, did):
        out["skipped"] = f"검수 카드 {did} 가 이미 답을 기다리는 중이라 다시 올리지 않음"
    elif not (hub and ws):
        out["failed"] = "기능 카드에서 " + ("허브 이름(label feature:<slug>)" if not hub else "작업 공간(설명의 '기능 코디네이터 허브:' 줄)") + "을 얻지 못함"
    elif dry_run:
        out["skipped"] = "dry-run: 아래 명령으로 올릴 것"
    elif not os.path.isfile(report):
        out["failed"] = f"보고 도구 {report} 없음(경로는 {REPORT_ENV} 로 지정)"
    else:
        env = dict(os.environ, LRM_HUB_DIR=hub_dir)
        try:
            r = run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            out["failed"] = f"보고 도구 실행 실패: {e}"
        else:
            if r.returncode == 0:
                out["posted"] = True
            else:
                out["failed"] = f"보고 도구 종료 {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}"
    return out


def send_done(epic: dict, hub_dir: str, doc: str, dry_run: bool = False, run=subprocess.run) -> str:
    """작은 기능 완료 보고(kind done + 보고서 첨부)를 대기열에 올림. 검수 답은 모아 받기로 따로 받음. 반환: 실패 사유, 성공·dry-run 은 ''"""
    hub, ws = feature_hub(epic), coordinator_ws(epic)
    report = os.environ.get(REPORT_ENV) or os.path.join(hub_dir, REPORT_NAME)
    if not doc:
        return f"완료 보고서 없음 — 기능 카드에 '{DONE_NOTE} <보고서.html 경로>' 코멘트를 남긴 뒤 hub_report.py add --kind done --file 로 직접 보냄"
    if not (hub and ws):
        return "기능 카드에서 허브 이름·작업 공간을 얻지 못함"
    if dry_run:
        return ""
    if not os.path.isfile(report):
        return f"보고 도구 {report} 없음(경로는 {REPORT_ENV} 로 지정)"
    name, room = feature_name(epic), TITLE_MAX - len(DONE_TAIL)
    if len(name) > room:
        name = name[:room - len(TITLE_CUT)].rstrip() + TITLE_CUT
    cmd = ["python3", report, "add", "--hub", hub, "--task", REVIEW_TASK, "--kind", DONE_KIND, "--card", str(epic.get("id") or ""),
           "--ws", ws, "--file", doc, "--text", name + DONE_TAIL + "\n검수는 모아 받기 카드로 받는다. 상세는 첨부 보고서."]
    try:
        r = run(cmd, capture_output=True, text=True, env=dict(os.environ, LRM_HUB_DIR=hub_dir), stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return f"보고 도구 실행 실패: {e}"
    return "" if r.returncode == 0 else f"보고 도구 종료 {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}"


def batch_did(comments) -> str:
    """기능 카드 코멘트 → 그 기능이 실린 가장 최근 모아 받기 결정 번호. 없으면 ''"""
    ids = [str(c).split(BATCH_NOTE, 1)[1].split()[0] for c in comments if str(c).startswith(BATCH_NOTE) and str(c).split(BATCH_NOTE, 1)[1].split()]
    return ids[-1] if ids else ""


def batch_verdict(hub_dir: str, did: str) -> tuple:
    """모아 받기 카드의 사용자 답 → ('pass'|'fix'|'', 답글 원문). 판정은 최근 카드의 최근 사용자 답(터미널 대필 제외).
    답글(custom)은 고칠 점을 적은 것으로 봄. 보류(hold)·답 없음은 ''"""
    card = last_card(hub_dir, did)
    if card is None or not card.get("ts"):
        return "", ""
    last = last_answer(hub_dir, did, card)
    if last is None:
        return "", ""
    text = next((str(r.get("text") or "").strip() for r in reversed(read_jsonl(os.path.join(hub_dir, INBOX_NAME)))
                 if r.get("decision_id") == did and r.get("parent_ts") == card.get("ts") and by_user(r)
                 and str(r.get("text") or "").strip()), "")
    if last["action"] in ("pick", "approve") and last.get("pick") == 1:
        return "pass", ""
    if (last["action"] in ("pick", "approve") and last.get("pick") == 2) or last["action"] == "custom":
        return "fix", text
    return "", ""


def batch_command(report: str, hub: str, ws: str, epics: list, did: str) -> list:
    """모아 받기 카드 명령. 본문 = 제목 / 배경 / 용어 / 기능 이름 목록"""
    # 이름은 백틱: 기능 이름에 도구 이름이 들어 있어도 쉬운 말 검사가 묶음 전체를 거부하지 않게
    names = [f"- `{feature_name(e)}` ({e.get('id')})" for e in epics]
    lines = [BATCH_TITLE.format(n=len(epics)), f"배경: {BATCH_BACKGROUND}", f"용어: {REVIEW_TERMS}", f"{BATCH_LIST_HEAD}:", *names]
    return ["python3", report, "add", "--hub", hub, "--task", REVIEW_TASK, "--kind", "decide", "--card", str(epics[0].get("id") or ""),
            "--ws", ws, "--no-doc", "--text", "\n".join(lines), "--options", BATCH_OPTIONS, "--recommend", REVIEW_RECOMMEND,
            "--decision-id", did, "--reversible", REVIEW_REVERSIBLE]


def ask_review_batch(epics: list, hub_dir: str, day: str, dry_run: bool = False, run=subprocess.run) -> dict:
    """review:batch 기능들을 검수 결정 카드 한 장으로 올림. 반환 키는 ask_review 와 같음.
    허브·작업 공간은 둘 다 얻을 수 있는 첫 기능 것을 씀(한 장이라 한 곳에만 올림)"""
    did = batch_id(day, [str(e.get("id") or "") for e in epics])
    hub, ws = next(((feature_hub(e), coordinator_ws(e)) for e in epics if feature_hub(e) and coordinator_ws(e)), ("", ""))
    report = os.environ.get(REPORT_ENV) or os.path.join(hub_dir, REPORT_NAME)
    cmd = batch_command(report, hub or "<허브 이름>", ws or "<작업 공간>", epics, did)
    out = {"id": did, "title": BATCH_TITLE.format(n=len(epics)), "posted": False, "skipped": "", "failed": "", "manual": shlex.join(cmd)}
    if not (hub and ws):
        out["failed"] = "모은 기능 어디에서도 허브 이름(label feature:<slug>)과 작업 공간(설명의 '기능 코디네이터 허브:' 줄)을 함께 얻지 못함"
    elif dry_run:
        out["skipped"] = "dry-run: 아래 명령으로 올릴 것"
    elif not os.path.isfile(report):
        out["failed"] = f"보고 도구 {report} 없음(경로는 {REPORT_ENV} 로 지정)"
    else:
        env = dict(os.environ, LRM_HUB_DIR=hub_dir)
        try:
            r = run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            out["failed"] = f"보고 도구 실행 실패: {e}"
        else:
            if r.returncode == 0:
                out["posted"] = True
            else:
                out["failed"] = f"보고 도구 종료 {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}"
    return out


def typed_approval(hub_dir: str, did: str, epic: "dict | None", typed=board_said.typed) -> bool:
    """사용자가 그 기능 코디네이터 창에 승인·건너뛰기를 직접 쳤는가(11-19: Slack 버튼을 기다리지 않고 진행하는 길).
    가장 최근 계획 카드를 올린 뒤에 친 글만 봄(카드를 다시 올리면 그 전 승인은 새 계획의 승인이 아님). 카드가 없으면 기능을 연 뒤의 글.
    그 카드에 Slack 으로 수정 요청을 했으면 그 뒤에 친 글만 봄(나중 답이 기준)"""
    ws = coordinator_ws(epic)
    if not ws:
        return False
    card = last_card(hub_dir, did)
    since = board_said.stamp((epic or {}).get("created_at"))
    if card is not None:
        if isinstance(card.get("at"), (int, float)) and not isinstance(card.get("at"), bool):
            since = max(since, float(card["at"]))
        ans = last_answer(hub_dir, did, card)
        if ans is not None and isinstance(ans.get("received_at"), (int, float)):
            since = max(since, float(ans["received_at"]))
    return any(typed_plan_ok(text) for _, text in typed(ws, since))


def typed_review(epic: "dict | None", typed=board_said.typed) -> str:
    """검수 대기 중인 기능의 코디네이터 창에 사용자가 직접 친 검수 통과 글(가장 최근 것). 없으면 ''.
    완료 검사가 기능 카드를 닫은 뒤(closed_at)에 친 글만 봄: 그 전의 '통과'는 다른 것에 대한 말일 수 있음.
    11-19: 한 코디네이터가 창의 '통과'를 두 번 거절하고 Slack 버튼을 기다렸음(터미널 답을 세지 않는 규칙을 넓게 읽음)"""
    ws = coordinator_ws(epic)
    since = board_said.stamp((epic or {}).get("closed_at"))
    if not ws or not since:
        return ""
    return next((text for _, text in reversed(typed(ws, since)) if typed_review_ok(text)), "")


def approval(hub_dir: str, epic: "dict | None"):
    """계획 승인 조회 함수(결정 번호 -> bool): Slack·문서 사이트 답, 또는 코디네이터 창에 직접 친 승인·건너뛰기"""
    return lambda did: plan_approved(hub_dir, did) or typed_approval(hub_dir, did, epic)


def by_user(rec: dict) -> bool:
    """사용자가 직접 낸 답인가. 버튼·문서 사이트 답은 출처 칸으로, 이모지 반응·카드 스레드 글은 폴러가 출처 칸 없이 쓴 기록 종류로 앎.
    둘 다 수신 프로세스만 쓰는 기록이라 세션이 만들 수 없음. 터미널에서 남긴 답(source terminal)과 출처를 모르는 버튼 기록은 세지 않음"""
    if rec.get("source") in USER_SOURCES:
        return True
    return "source" not in rec and rec.get("type") in POLLED_TYPES


def claim_block(card: dict, epic: "dict | None", approved) -> str:
    """이 카드를 지금 집으면 안 되는 이유(원인 — 조치). 집어도 되면 ''. approved(결정 번호) -> bool 는 계획 승인 조회"""
    if epic is None or FLOW_LABEL not in feature_labels(epic):
        return ""
    if card.get("id") == epic.get("id") or card.get("issue_type") == "epic" or card.get("status") == "in_progress":
        return ""
    size = size_of(epic)
    if not size:
        return (f"기능 {epic['id']} 의 크기를 정하지 않음(접수 단계) — 목표·확인 방법을 적고 "
                f"bd label add {epic['id']} {SIZE_HEAD}{SMALL}|{NORMAL}|{LARGE} 중 하나를 붙인 뒤 다시 집음")
    if size == SMALL or "plan" in skips(epic):
        return ""
    did = plan_id(epic["id"])
    if approved(did):
        return ""
    return (f"기능 {epic['id']}({SIZES[size]}) 의 계획 승인이 없음 — 계획서를 작업 스레드에 결정 카드 {did} 로 올리고"
            f"(1번 선택지 '{APPROVE_WORD}') 사용자가 승인한 뒤 다시 집음. 사용자가 코디네이터 창에 '승인'이나 '스킵'이라고 직접 쳐도 됨. "
            f"작은 일이면 {SIZE_HEAD}{SMALL} 로 바꿈. 급한 일이면 사용자가 코디네이터 창에 '급함'이라고 치거나 /orca-feature skip 으로 준비를 건너뜀(board-skip)")
