#!/usr/bin/env python3
"""할 일 행의 근거 조회: 끝난 대상(PR 병합·닫힘, 결정 카드 답, 장부 완료)과 허브(PR head 브랜치의 작업 공간 부모 허브, 결정 카드를 올린 허브).

읽기 전용. 조회가 실패하면 '모름'(끝났다고 보지 않음, 허브는 '미상')으로 두어 행을 잘못 완료·수정하지 않음.
PR 조회 결과는 cache(상태 파일의 facts 칸)에 남겨 병합·닫힘은 다시 묻지 않고, 열림은 OPEN_TTL_S 뒤에 다시 물음.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import progress_collect as pc  # noqa: E402

PR_STATE_FIELDS = "state,headRefName"
CLOSED_STATES = ("MERGED", "CLOSED")
OPEN_TTL_S = 300  # 열린 PR 상태를 다시 묻는 간격
HUB_TTL_S = 3600  # PR → 허브 판정 유효 시간(찾은 값)
HUB_MISS_TTL_S = 600  # 못 찾은 값은 짧게(작업 공간이 곧 생길 수 있음)
PR_PREFIX, DEC_PREFIX, LEDGER_PREFIX = "pr:", "decision:", "ledger:"


def closed_cards(sent, inbox, done_ts, card_state):
    """결정 번호 → 답이 끝난 카드인지(같은 번호의 카드가 여럿이면 전부 닫혀야 True). panel_collect.collect_pending 과 같은 닫힘 규칙:
    번호 카드는 card_state 가 closed 이거나 slack-done 에 ts 가 있을 때만 닫힘. 보류·충돌은 대기"""
    reactions = {}
    for d in inbox:
        if d.get("type") == "reaction" and isinstance(d.get("parent_ts"), str):
            reactions.setdefault(d["parent_ts"], []).append(d)
    state = {}
    for s in sent:
        did, ts = s.get("decision_id"), s.get("ts")
        if s.get("form_kind") != "decide" or "options" not in s or not isinstance(did, str) or not isinstance(ts, str):
            continue
        shut = ts in done_ts or card_state(s, reactions.get(ts, [])) == "closed"
        state[did] = state.get(did, True) and shut
    return state


def decision_hubs(reports, sent, known):
    """결정 번호 → 카드를 올린 허브 slug. hub-reports 기록(decision_id·hub)이 우선, 없으면 보낸 카드 기록의 tag.
    known: 허브 slug 집합. tag 는 ci-watch 같은 비허브 값도 있어 허브 이름일 때만 씀"""
    out = {}
    for s in sent:
        if isinstance(s.get("decision_id"), str) and s.get("form_kind") == "decide" and s.get("tag") in known:
            out[s["decision_id"]] = s["tag"]
    for r in reports:
        if isinstance(r.get("decision_id"), str) and r.get("hub") in known:
            out[r["decision_id"]] = r["hub"]
    return out


def ledger_done(path, read_rows, done_state):
    """장부에서 완료된 번호 집합. 읽지 못하면 빈 집합(완료로 보지 않음)"""
    try:
        return {r["id"] for r in read_rows(path) if r["state"] == done_state}
    except Exception:  # noqa: BLE001 — 장부 오류는 '모름'으로 둠
        return set()


class Facts:
    """한 회차의 근거 모음. gh·orca·파일 조회는 필요할 때 한 번씩 하고 이 객체에 기억함.
    gh: gh 실행 파일 · repos: PR 조회 저장소 목록 · worktrees: repo → 작업 공간 목록(조회 불완전이면 None) 함수 · hub_slugs: 허브 slug 집합"""

    def __init__(self, gh, repos, worktrees, hub_slugs, cache=None, now=None, budget_s=None, use_pr=True):
        self.gh, self.repos, self._worktrees, self.hub_slugs = gh, list(repos), worktrees, set(hub_slugs)  # hub_slugs 는 hubs.tsv 의 원래 slug
        self.cache = cache if cache is not None else {}
        self.now = time.time() if now is None else now
        self.deadline = time.monotonic() + (pc.pr_budget() if budget_s is None else budget_s)
        self.repo_hint = {}  # PR 번호 → 저장소(수집기가 이미 아는 열린 PR)
        self.wts = {}  # repo → 작업 공간 목록
        self.cards = {}  # 결정 번호 → 닫힘 여부
        self.dec_hubs = {}
        self.ledger = set()
        self.open_ttl = OPEN_TTL_S  # 열린 PR 재조회 간격(호출자가 바꿈)
        self.use_pr = use_pr  # False 면 gh 를 부르지 않음(--no-pr): PR 은 모두 '모름'
        self.errors = []  # 조회 실패 안내(사람이 읽는 문장)

    # ---------- PR ----------
    def _view(self, num):
        """gh pr view → {state, branch, repo} 또는 None. 저장소가 여럿이면 앞에서부터 시도"""
        if not self.use_pr:
            return None
        for repo in ([self.repo_hint[num]] if num in self.repo_hint else self.repos) or [""]:
            left = self.deadline - time.monotonic()
            if left <= 1:
                self.errors.append(f"PR 조회 시간 예산 초과 — #{num} 이후는 다음 회차")
                return None
            cmd = [self.gh, "pr", "view", num, "--json", PR_STATE_FIELDS] + (["--repo", repo] if repo else [])
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=min(pc.GH_TIMEOUT_S, left), stdin=subprocess.DEVNULL)
                d = json.loads(p.stdout) if p.returncode == 0 else None
            except (OSError, ValueError, subprocess.TimeoutExpired):
                d = None
            if isinstance(d, dict) and isinstance(d.get("state"), str):
                return {"state": d["state"], "branch": d.get("headRefName") or "", "repo": repo}
        return None

    def pr(self, num):
        """PR 번호 → {state, branch, repo, ...} 또는 None(모름). 병합·닫힘은 캐시에서 영구 재사용"""
        key = PR_PREFIX + num
        hit = self.cache.get(key)
        if isinstance(hit, dict) and (hit.get("state") in CLOSED_STATES or self.now - hit.get("at", 0) < self.open_ttl):
            return hit
        got = self._view(num)
        if got is None:
            return hit if isinstance(hit, dict) and hit.get("state") in CLOSED_STATES else None
        got["at"] = self.now
        if isinstance(hit, dict) and hit.get("branch") == got["branch"] and "hub" in hit:  # 브랜치가 같으면 허브 판정 유지
            got.update(hub=hit["hub"], hub_at=hit.get("hub_at", 0))
        self.cache[key] = got
        return got

    def pr_hub(self, num):
        """PR head 브랜치를 가진 작업 공간의 부모 허브 slug(ci_review.parent_hub 규칙). 근거가 없으면 빈 문자열"""
        info = self.pr(num)
        if not info or not info.get("branch"):
            return ""
        ttl = HUB_TTL_S if info.get("hub") else HUB_MISS_TTL_S
        if "hub" in info and self.now - info.get("hub_at", 0) < ttl:
            return info["hub"]
        import ci_review  # 지연 가져오기: orca·허브 모듈을 쓰는 곳에서만 필요
        repo = info.get("repo") or ""
        if repo not in self.wts:
            self.wts[repo] = self._worktrees(repo)
        wts = self.wts[repo]
        if wts is None:
            self.errors.append(f"작업 공간 목록 조회 불완전 — #{num} 허브 판정 보류")
            return info.get("hub", "")
        found = ci_review.parent_hub(info["branch"], wts, {s: s for s in self.hub_slugs}, repo)
        info.update(hub=pc.hub_slug(found) if found else "", hub_at=self.now)
        return info["hub"]

    # ---------- 종합 ----------
    def closed(self, targets):
        """대상 키 목록 중 끝난 것의 집합. 모르면 끝나지 않은 것으로 봄"""
        out = set()
        for t in targets:
            if t.startswith(PR_PREFIX):
                info = self.pr(t[len(PR_PREFIX):])
                if info and info["state"] in CLOSED_STATES:
                    out.add(t)
            elif t.startswith(DEC_PREFIX) and self.cards.get(t[len(DEC_PREFIX):]) is True:
                out.add(t)
            elif t.startswith(LEDGER_PREFIX) and t[len(LEDGER_PREFIX):] in self.ledger:
                out.add(t)
        return out

    def hub(self, targets):
        """행의 대상으로 판정한 허브 slug. 근거가 없으면 None(호출자가 '미상' 처리)"""
        for t in targets:
            if t.startswith(PR_PREFIX):
                h = self.pr_hub(t[len(PR_PREFIX):])
            elif t.startswith(DEC_PREFIX):
                h = self.dec_hubs.get(t[len(DEC_PREFIX):], "")
            else:
                h = ""
            if h:
                return h
        return None


def orca_worktrees(repo):
    """ci_review.list_worktrees 로 PR 저장소의 작업 공간 목록. orca 를 못 찾거나 조회가 불완전하면 None"""
    import ci_review
    try:
        return ci_review.list_worktrees(pc.orca_bin(), repo)
    except Exception:  # noqa: BLE001 — orca 실행 문제는 '모름'으로 둠
        return None


def load_local(facts, hub_dir, ledger_path):
    """로컬 기록(보낸 카드·받은 답·허브 보고·허브 표·장부)을 읽어 facts 에 채움. 못 읽은 기록은 안내만 남기고 비움(= 모름)"""
    import ci_review
    import ledger
    import panel_collect
    p = lambda name: os.path.join(hub_dir, name)  # noqa: E731
    names = ci_review.load_hub_names(p("hubs.tsv"))
    facts.hub_slugs = set(names)
    known = set(names) | {pc.hub_slug(s) for s in names}
    try:
        sent, inbox = panel_collect.jsonl(p("slack-sent.jsonl")), panel_collect.jsonl(p("slack-inbox.jsonl"))
        try:
            with open(p("slack-done.txt"), encoding="utf-8") as f:
                done = set(f.read().split())
        except FileNotFoundError:
            done = set()
        facts.cards = closed_cards(sent, inbox, done, panel_collect.card_state)
        try:
            reports = panel_collect.jsonl(p("hub-reports.jsonl"))
        except panel_collect.SourceError:
            reports = []
        facts.dec_hubs = decision_hubs(reports, sent, known)
    except (panel_collect.SourceError, OSError, UnicodeDecodeError):
        facts.errors.append("결정 카드 기록 읽기 실패 — 결정 행 완료·허브 판정 보류")
    facts.ledger = ledger_done(ledger_path, ledger.read_rows, ledger.DONE)
    return facts
