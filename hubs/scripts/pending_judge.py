"""대기 목록 판정(한 곳): 어떤 보낸 글이 사람이 정할 결정인가, 자동 알림인가, 다시 알리기엔 오래됐는가.
2시간 재알림·'대기' 명령(slack_pending.sh)과 옆 패널(panel_collect.py)이 같이 씀. 두 곳이 판정을 따로 들고 있으면 목록이 서로 달라짐.
답했는지 판정은 여기가 아니라 answer_store.judge. 작업 스레드에 글로 쓴 답을 알아보는 것(written_answer)은 여기.

설정 표 pending_rules.tsv(탭 구분, 값은 표 한 곳):
  max_age_hours<TAB>시간                   이 시간이 넘은 결정은 재알림에서 뺌
  exclude_pattern<TAB>결정 번호 정규식<TAB>설명   번호 전체가 맞는 카드는 자동 알림. 머리말만 보면 사람 결정(D-STORE…)까지 빠지므로 전체 일치로 봄
"""
import os
import re
import sys
from dataclasses import dataclass

RULES_NAME = "pending_rules.tsv"
DECIDE = "decide"


@dataclass(frozen=True)
class Rules:
    max_age_h: "float | None"  # None 이면 나이 제한 없음
    auto: tuple  # 자동 알림 결정 번호 정규식(컴파일된 것)들


def compile_auto(patterns) -> tuple:
    return tuple(re.compile(p) for p in patterns)


def to_stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_rules(dirs, warn=to_stderr) -> Rules:
    """dirs 를 앞에서부터 찾아 처음 있는 설정 표를 읽음. 표가 없거나 줄이 틀리면 그 줄만 건너뛰고 경고(목록 만들기를 멈추지 않음)"""
    path = next((p for p in (os.path.join(d, RULES_NAME) for d in dirs) if os.path.isfile(p)), "")
    if not path:
        warn(f"{RULES_NAME} 없음 — 나이 제한·자동 알림 제외 없이 목록을 만듦")
        return Rules(None, ())
    age, auto = None, []
    with open(path, encoding="utf-8") as f:
        for n, ln in enumerate(f, 1):
            c = ln.rstrip("\r\n").split("\t")
            if ln.startswith("#") or len(c) < 2:
                continue
            try:
                if c[0] == "max_age_hours":
                    age = float(c[1])
                elif c[0] == "exclude_pattern":
                    auto.append(re.compile(c[1]))
            except (ValueError, re.error):
                warn(f"{RULES_NAME} {n}번째 줄 값 오류({c[0]}) — 그 줄은 건너뜀")
    return Rules(age, tuple(auto))


def is_decision(s: dict) -> bool:
    """결정 = 선택지를 달아 결정 카드로 보낸 글. 맨 위 글이든 작업 스레드 안이든 같음(작업 하나 = 스레드 하나, 결정 카드도 그 안).
    제목의 '결정' 낱말이나 선택지 없는 옛 글은 세지 않음"""
    return bool(s.get("ts")) and s.get("form_kind") == DECIDE and "options" in s


# 번호 앞에 올 수 있는 것: 인용 표시(>), 봇을 부른 말(<@U…>), '결정' 낱말(카드 아래 참고 줄이 '결정 D-…' 꼴, 붙여 쓰기도 함)
ID_LEAD = re.compile(r"\s*(?:>\s*)?(?:<@[A-Z0-9]+>\s*)?(?:결정\s*)?")


def written_answer(card: dict, rec: dict) -> bool:
    """작업 스레드 안의 결정 카드에 글로 쓴 답인가: 그 스레드의 답글이고 카드의 결정 번호로 시작함.
    스레드에는 다른 얘기도 오가므로 번호로 시작하는 글만 답으로 봄(글 중간의 번호는 언급일 뿐). 글 답은 카드를 닫지 않음(담당이 확인하고 닫음)"""
    did, thread = card.get("decision_id"), card.get("thread_ts")
    if not (thread and isinstance(did, str) and rec.get("type") == "thread_reply" and str(rec.get("parent_ts") or "") == thread):
        return False
    text = str(rec.get("text") or "")
    rest = text[ID_LEAD.match(text).end():]
    return rest[:len(did)].lower() == did.lower() and not re.match(r"[0-9A-Za-z]", rest[len(did):])  # 소문자로 쓴 번호도 같은 번호


def is_auto(s: dict, rules: Rules) -> bool:
    """감시기 카드처럼 버튼이 동작을 실행하는 자동 알림. 카드는 그대로 두고 사람 결정 목록에서만 뺌"""
    did = s.get("decision_id")
    return isinstance(did, str) and any(p.fullmatch(did) for p in rules.auto)


def is_old(s: dict, rules: Rules, now: float) -> bool:
    """보낸 시각을 모르면 오래됐다고 단정하지 않음(조용히 빠지는 것보다 한 번 더 뜨는 쪽이 안전)"""
    at = s.get("at")
    if rules.max_age_h is None or isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    return now - at > rules.max_age_h * 3600
