"""대기 목록 판정(한 곳): 어떤 보낸 글이 사람이 정할 결정인가, 자동 알림인가, 다시 알리기엔 오래됐는가.
2시간 재알림·'대기' 명령(slack_pending.sh)과 옆 패널(panel_collect.py)이 같이 씀. 두 곳이 판정을 따로 들고 있으면 목록이 서로 달라짐.
답했는지 판정은 여기가 아니라 answer_store.judge.

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
