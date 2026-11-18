"""작업 흐름 규칙(한 곳): 단계 이름, 작업 크기, 계획 승인 판정, 구현 카드 집기 조건.
board-claim(집기)과 board-stage(단계 옮기기)가 같이 씀. 규칙을 글로만 두면 건너뛰므로 집기 도구가 막음.

흐름: 0 접수 → 1 이해 → 2 설계(계획 승인) → 3 구현 → 4 검증 → 5 검수 → 6 반영.
크기: 작음은 설계를 건너뛰고(0 → 3) 보통·큼은 계획 승인 뒤에만 구현 카드를 집음.
적용 대상: 기능 카드(epic)에 label flow:v1 이 있는 기능만. 옛 방식으로 떠 있는 기능은 막지 않음.

계획 승인 = 그 기능의 계획 승인 카드(결정 번호 plan_id(기능 카드 id), 1번 선택지가 '승인')에 사용자가 Slack·문서 사이트에서 1번이나 추천대로를 고른 것.
터미널에서 남긴 답(decide-answer)은 세지 않음: 코디네이터가 스스로 승인할 수 있게 되기 때문.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass

FLOW_LABEL = "flow:v1"
SIZE_HEAD = "size:"
SMALL, NORMAL, LARGE = "small", "normal", "large"
SIZES = {SMALL: "작음", NORMAL: "보통", LARGE: "큼"}
PLAN_HEAD = "D-PLAN"
ID_BODY_MAX = 16 - len("PLAN")  # 결정 번호는 D- 뒤 16자까지(hub_report.DECISION_ID_RE)
APPROVE_WORD = "승인"            # 계획 승인 카드의 1번 선택지는 이 말로 시작
USER_SOURCES = ("slack", "docsite")  # 사용자가 직접 누른 답만. 터미널 기록은 코디네이터가 쓸 수 있음
POLLED_TYPES = ("reaction", "thread_reply")  # 폴러가 Slack 에서 읽어 온 사용자 반응·카드 스레드 글(출처 칸 없이 기록됨)
SENT_NAME, INBOX_NAME = "slack-sent.jsonl", "slack-inbox.jsonl"


@dataclass(frozen=True)
class Stage:
    key: str
    name: str

    @property
    def label(self) -> str:
        return "stage:" + self.key


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
    if FLOW_LABEL not in labels(epic) or st.key == STAGES[0].key:
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


def size_of(epic: "dict | None") -> str:
    """기능 카드의 크기(small|normal|large). 안 정했거나 모르는 값이면 ''"""
    v = next((x[len(SIZE_HEAD):] for x in labels(epic) if x.startswith(SIZE_HEAD)), "")
    return v if v in SIZES else ""


def plan_id(card_id: str) -> str:
    """기능 카드 id → 그 기능의 계획 승인 결정 번호. 번호를 손으로 정하지 않게 해 다른 결정의 승인을 끌어다 쓰지 못하게 함.
    길어서 자르면 다른 기능과 겹칠 수 있어 앞 6자 + id 지문 6자로 만듦"""
    body = re.sub(r"[^A-Za-z0-9]", "", card_id or "").upper()
    if len(body) > ID_BODY_MAX:
        body = body[:6] + hashlib.sha1(card_id.encode()).hexdigest()[:6].upper()
    return PLAN_HEAD + body


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
    card = next((s for s in reversed(read_jsonl(os.path.join(hub_dir, SENT_NAME)))
                 if s.get("decision_id") == did and s.get("form_kind") == "decide" and isinstance(s.get("options"), dict)), None)
    if card is None or not card.get("ts") or not str(card["options"].get("1", "")).strip().startswith(APPROVE_WORD):
        return False
    last = next((r for r in reversed(read_jsonl(os.path.join(hub_dir, INBOX_NAME)))
                 if r.get("decision_id") == did and r.get("parent_ts") == card["ts"] and by_user(r)
                 and r.get("action") in ("pick", "approve", "custom", "hold")), None)
    # 추천대로(approve)도 pick 에 추천 번호가 들어 있음. 1번(승인)일 때만 승인
    return last is not None and last["action"] in ("pick", "approve") and last.get("pick") == 1


def by_user(rec: dict) -> bool:
    """사용자가 직접 낸 답인가. 버튼·문서 사이트 답은 출처 칸으로, 이모지 반응·카드 스레드 글은 폴러가 출처 칸 없이 쓴 기록 종류로 앎.
    둘 다 수신 프로세스만 쓰는 기록이라 세션이 만들 수 없음. 터미널에서 남긴 답(source terminal)과 출처를 모르는 버튼 기록은 세지 않음"""
    if rec.get("source") in USER_SOURCES:
        return True
    return "source" not in rec and rec.get("type") in POLLED_TYPES


def claim_block(card: dict, epic: "dict | None", approved) -> str:
    """이 카드를 지금 집으면 안 되는 이유(원인 — 조치). 집어도 되면 ''. approved(결정 번호) -> bool 는 계획 승인 조회"""
    if epic is None or FLOW_LABEL not in labels(epic):
        return ""
    if card.get("id") == epic.get("id") or card.get("issue_type") == "epic" or card.get("status") == "in_progress":
        return ""
    size = size_of(epic)
    if not size:
        return (f"기능 {epic['id']} 의 크기를 정하지 않음(접수 단계) — 목표·확인 방법을 적고 "
                f"bd label add {epic['id']} {SIZE_HEAD}{SMALL}|{NORMAL}|{LARGE} 중 하나를 붙인 뒤 다시 집음")
    if size == SMALL:
        return ""
    did = plan_id(epic["id"])
    if approved(did):
        return ""
    return (f"기능 {epic['id']}({SIZES[size]}) 의 계획 승인이 없음 — 계획서를 작업 스레드에 결정 카드 {did} 로 올리고"
            f"(1번 선택지 '{APPROVE_WORD}') 사용자가 승인한 뒤 다시 집음. 작은 일이면 {SIZE_HEAD}{SMALL} 로 바꿈")
