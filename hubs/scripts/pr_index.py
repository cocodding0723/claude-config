"""같은 저장소의 worktree 간 PR 조회 결과 공유."""
import json
import os
import re
import subprocess

TICKET = re.compile(r"\b[A-Z]{2,}-\d+\b")
BRANCH_HEAD, REPO_HEAD = "branch:", "repo:"
PR_FIELDS = "number,state,title,headRefName"


def _label(card, head):
    return next((x[len(head):] for x in (card.get("labels") or []) if x.startswith(head)), "")


class PrIndex:
    def __init__(self, gh_bin, run=subprocess.run, *, limit, timeout):
        self._gh = gh_bin
        self._run = run
        self._limit = limit
        self._timeout = timeout
        self._repos = {}
        self._rows = {}

    def load(self, repo):
        repo = os.path.realpath(os.path.expanduser(repo)) if repo else ""
        if self._gh == "off" or not repo or not os.path.isdir(repo):
            return None
        if repo not in self._repos:
            self._repos[repo] = None
            try:
                p = self._call(["git", "rev-parse", "--git-common-dir"], repo)
                if p.returncode == 0 and p.stdout.strip():
                    self._repos[repo] = os.path.realpath(os.path.join(repo, p.stdout.strip()))
            except (OSError, subprocess.TimeoutExpired):
                pass
        common = self._repos[repo]
        if common is None:
            return None
        if common not in self._rows:
            self._rows[common] = None
            try:
                p = self._call([self._gh, "pr", "list", "--state", "all", "--limit", str(self._limit), "--json", PR_FIELDS], repo)
                if p.returncode == 0:
                    rows = json.loads(p.stdout)
                    if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
                        self._rows[common] = rows
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        return self._rows[common]

    def repo_for_card(self, card):
        for label in card.get("labels") or []:
            if label.startswith(REPO_HEAD):
                repo = label[len(REPO_HEAD):]
                if self.load(repo) is not None:
                    return repo
        return ""

    def for_card(self, card):
        rows = self.load(self.repo_for_card(card))
        if rows is None:
            return None
        ticket = TICKET.search(str(card.get("title") or ""))
        found = []
        if ticket:
            # 옛 PR은 기능 브랜치와 이름이 달라 제목의 티켓 번호를 우선함.
            pattern = rf"(?<![A-Za-z0-9]){re.escape(ticket.group(0))}(?![0-9])"
            found = [r for r in rows if re.search(pattern, str(r.get("title") or ""))]
        branch = _label(card, BRANCH_HEAD)
        if not found and branch:
            found = [r for r in rows if r.get("headRefName") == branch]
        return [(int(r["number"]), str(r.get("state"))) for r in found if str(r.get("number", "")).isdigit()]

    def _call(self, cmd, repo):
        return self._run(cmd, cwd=repo, capture_output=True, text=True, timeout=self._timeout, stdin=subprocess.DEVNULL)
