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


def stage(key: str) -> "Stage | None":
    return next((s for s in STAGES if s.key == key), None)


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
    같은 번호로 계획을 다시 올렸으면 가장 최근 답이 기준(고친 계획에 수정 요청이 오면 승인이 풀림)"""
    card = next((s for s in reversed(read_jsonl(os.path.join(hub_dir, SENT_NAME)))
                 if s.get("decision_id") == did and s.get("form_kind") == "decide" and isinstance(s.get("options"), dict)), None)
    if card is None or not str(card["options"].get("1", "")).strip().startswith(APPROVE_WORD):
        return False
    last = next((r for r in reversed(read_jsonl(os.path.join(hub_dir, INBOX_NAME)))
                 if r.get("decision_id") == did and r.get("source") in USER_SOURCES and r.get("action") in ("pick", "approve", "custom", "hold")), None)
    if last is None:
        return False
    return last["action"] == "approve" or (last["action"] == "pick" and last.get("pick") == 1)


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
