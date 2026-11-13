#!/usr/bin/env python3
"""허브 막힘 근거 판정: 막힘에 달린 근거(pr·decision)가 닫혔는지 확인함.

PR·결정 카드 판정은 todo_facts.Facts 를 그대로 씀(할 일 목록 자동 완료와 같은 규칙). 이 모듈은 근거 종류별 호출만 묶음.
결과: closed(근거가 전부 닫힘) · open(하나라도 열림) · unknown(조회 실패·기록 없음 → 막힘 유지) · None(확인할 수 있는 근거 없음).
gh 결과는 cache 에 OPEN_TTL_S 동안 보관(병합·닫힘은 영구).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import progress_collect as pc  # noqa: E402
import todo_facts  # noqa: E402

CLOSED, OPEN, UNKNOWN = "closed", "open", "unknown"
CHECKABLE = ("pr", "decision")  # ticket 은 닫힘을 판정하지 않음(표시만)
OPEN_TTL_S = 600  # 열린 PR 을 다시 묻는 간격
CACHE_KEEP_S = 86400


class Resolver:
    """refs([{kind, id}]) → 상태. Facts 는 처음 필요할 때 만들어 한 회차 안에서 재사용"""

    def __init__(self, conf, cache, now, use_pr=True):
        self.conf, self.now, self.use_pr = conf, now, use_pr
        self.cache = cache
        for k in [k for k, v in cache.items() if not isinstance(v, dict) or now - v.get("at", 0) > CACHE_KEEP_S]:
            del cache[k]
        self._facts = None
        self.errors = []

    def facts(self):
        if self._facts is None:
            f = todo_facts.Facts(self.conf["gh"], self.conf["repos"], todo_facts.orca_worktrees, set(), self.cache, self.now, use_pr=self.use_pr)
            f.open_ttl = OPEN_TTL_S
            try:
                todo_facts.load_local(f, pc.HUB_DIR, self.conf["ledger"])
            except Exception as e:  # noqa: BLE001 — 로컬 기록 오류는 결정 근거를 '모름'으로 둠
                f.cards = {}
                f.errors.append(f"결정 카드 기록 읽기 실패({type(e).__name__})")
            self._facts = f
        return self._facts

    def one(self, ref):
        kind, rid = ref["kind"], ref["id"]
        f = self.facts()
        if kind == "pr":
            repo, _, num = rid.rpartition("#")
            if repo:
                f.repo_hint[num] = repo
            info = f.pr(num)
            if info is None:
                return UNKNOWN
            return CLOSED if info["state"] in todo_facts.CLOSED_STATES else OPEN
        if kind == "decision":
            done = f.cards.get(rid)
            return UNKNOWN if done is None else (CLOSED if done else OPEN)
        return None

    def __call__(self, refs):
        states = [s for s in (self.one(r) for r in refs if r["kind"] in CHECKABLE) if s]
        if not states:
            return None
        if OPEN in states:
            return OPEN
        return UNKNOWN if UNKNOWN in states else CLOSED

    def notes(self):
        return self._facts.errors if self._facts else []
