#!/usr/bin/env python3
"""결정 카드 직접 입력 질문 중 아직 답글이 안 달린 것의 기록(<LRM_HUB_DIR>/ask-open.jsonl).

수신기(slack_socket)가 질문을 받으면 note, 전송기(report_sender)가 그 스레드에 답글을 올릴 때 waiting 으로 찾아
'버튼으로 고르세요' 줄을 붙이고, 전송이 끝나면 close. 답하는 세션이 안내 줄을 잊어도 붙게 하려는 것.
줄: {"thread","did","card","at"} 질문 받음 · {"thread","did","done"} 안내를 붙였거나 이미 답한 카드.
"""
import json
import os
import time
from typing import Callable

NAME = "ask-open.jsonl"
TTL_S = 24 * 3600  # 이보다 오래된 질문에는 안내를 붙이지 않음. 하루 지난 답글은 그 질문의 답이라고 보기 어려움
REMIND = "답을 확인했으면 결정 {did} 카드 버튼으로 골라 주세요"


def path(hub_dir: str) -> str:
    return os.path.join(hub_dir, NAME)


def add(hub_dir: str, row: dict) -> None:
    with open(path(hub_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def note(hub_dir: str, thread: str, did: str, card: str, now: float | None = None) -> None:
    """질문 한 건 기록. thread 는 답글이 올라갈 스레드(카드가 스레드 안이면 그 스레드, 맨 위 글이면 카드 자신), card 는 카드 글 ts"""
    add(hub_dir, {"thread": thread, "did": did, "card": card, "at": int(time.time() if now is None else now)})


def rows(hub_dir: str) -> list:
    try:
        with open(path(hub_dir), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("thread"), str) and isinstance(r.get("did"), str):
            out.append(r)
    return out


def waiting(hub_dir: str, thread: str, answered: Callable[[str, str], bool] | None = None, now: float | None = None) -> list:
    """이 스레드에서 답글을 기다리는 질문의 결정 번호(받은 순서, 중복 없음).
    answered(결정 번호, 카드 ts) 가 참인 카드는 이미 버튼으로 답한 것이라 뺌 — 닫힌 카드에 '고르세요' 를 붙이지 않게"""
    now = time.time() if now is None else now
    asked: dict = {}
    for r in rows(hub_dir):
        if r["thread"] != thread:
            continue
        if "done" in r:
            asked.pop(r["did"], None)
        elif now - r.get("at", 0) <= TTL_S:
            asked[r["did"]] = str(r.get("card") or "")
    return [did for did, card in asked.items() if not (answered and answered(did, card))]


def close(hub_dir: str, thread: str, dids: list, now: float | None = None) -> None:
    for did in dids:
        add(hub_dir, {"thread": thread, "did": did, "done": int(time.time() if now is None else now)})
