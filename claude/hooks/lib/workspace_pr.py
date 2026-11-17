"""Orca 하위 작업 공간의 draft PR 상시 허용(사용자 11-17 "하위 워크스페이스에서 바로 올리면 되잖아").
조건 전부: gh 를 실행하는 폴더가 Orca 작업 공간 + 부모(parentWorktreeId)가 있음 + 메인 체크아웃 아님
+ PR head 가 그 공간의 현재 브랜치 + 브랜치가 작업 브랜치 규칙에 맞고 보호 브랜치가 아님 + -R 저장소가 그 공간의 origin.
draft 여부는 pr_guard 가 먼저 검사함. ready·병합은 이 모듈이 다루지 않음.
Orca 조회 실패(명령 없음·시간 초과·공간 아님)는 허용하지 않고 다른 승인 경로로 넘김."""
import json
import os
import re
import shutil
import subprocess

WORK_BRANCH = re.compile(r"^(sprint-\d{8}/[\w.\-/]+|(feature|feat|fix|hotfix|refactor|chore|docs|test)/[\w.\-/]+)$")
PROTECTED = re.compile(r"^(main|master|dev|develop|release/.*|asset-patch/.*)$")
TIMEOUT_S = 5


def orca_bin():
    return os.environ.get("LRM_ORCA_BIN") or shutil.which("orca") or ""


def toplevel(d):
    try:
        return subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def worktree(path):
    """orca worktree show 결과의 worktree 객체. 실패는 None"""
    exe = orca_bin()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "worktree", "show", "--worktree", f"path:{path}", "--json"],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
        d = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    w = ((d.get("result") or {}).get("worktree") if isinstance(d, dict) and d.get("ok") else None)
    return w if isinstance(w, dict) else None


def git_branch(d):
    try:
        return subprocess.run(["git", "-C", d, "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def branch_ok(branch):
    return bool(WORK_BRANCH.match(branch)) and not PROTECTED.match(branch)


def allowed(here, repo, head, origin_repo):
    """하위 작업 공간 조건을 모두 만족하면 '공간 경로', 아니면 ''. origin_repo(d) 는 폴더의 origin 저장소 이름"""
    if here is None or not branch_ok(head):
        return ""
    top = toplevel(here)
    if not top or git_branch(top) != head or origin_repo(top).lower() != (repo or "").lower():
        return ""
    w = worktree(top)
    if not w or w.get("isMainWorktree") or not w.get("parentWorktreeId"):
        return ""
    if os.path.realpath(str(w.get("path") or "")) != os.path.realpath(top):
        return ""
    if str(w.get("branch") or "") != f"refs/heads/{head}":
        return ""
    return top
