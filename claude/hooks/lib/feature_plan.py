"""기능의 계획 승인을 PR·Jira 훅의 승인으로 인정(사용자 11-20 "승인은 계획 승인 한 번에 묶는다").
작업판 기능 카드(epic)가 구현 단계에 들어갈 수 있는 상태면(board_flow.stage_block: 계획 승인 카드의 승인 답·코디네이터 창의 직접 승인·
작은 크기·계획 건너뛰기) 그 기능의 통합 브랜치 draft PR 생성과 그 기능 티켓의 상태 전환을 건별 카드 없이 통과시킴.
전에는 훅이 plan-approvals/<plan-id>.json 기록만 봤는데 그 기록은 메인이 손으로 써야 해서 쓰인 적이 없고, 같은 승인을 건별 카드로 다시 받았음.
대조 기준은 feature_open.sh 가 기능 카드에 남기는 값: label branch:<통합 브랜치>·repo:<저장소 경로>, external_ref jira-<티켓>.
승인 근거(Slack 답 원장·세션 기록)는 세션이 쓸 수 없는 파일이라 훅의 기존 신뢰 범위와 같음. 작업판 조회가 실패하면 승인으로 치지 않음."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
BRANCH_HEAD, REPO_HEAD, TICKET_HEAD = "branch:", "repo:", "jira-"
BD_TIMEOUT = 15


def board_dir():
    return os.environ.get("BOARD_DIR") or os.path.expanduser("~/orca/board")


def flow_module():
    """board_flow. 훅과 같은 저장소의 board/lib → 설치된 작업판 순. 없으면 None"""
    for d in (os.path.join(HERE, "..", "..", "..", "board", "lib"), os.path.join(board_dir(), "lib")):
        if os.path.isfile(os.path.join(d, "board_flow.py")):
            if d not in sys.path:
                sys.path.insert(0, os.path.realpath(d))
            import board_flow
            return board_flow
    return None


def open_epics():
    """열린 기능 카드 목록. 작업판이 없거나 조회가 실패하면 []"""
    try:
        r = subprocess.run(["bd", "list", "--type", "epic", "--limit", "0", "--json"], cwd=board_dir(), capture_output=True, text=True, timeout=BD_TIMEOUT)
        rows = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [e for e in rows if isinstance(e, dict) and e.get("status") != "closed"]


def label_value(epic, head):
    return next((x[len(head):] for x in (epic.get("labels") or []) if isinstance(x, str) and x.startswith(head)), "")


def build_ready(hub, epic):
    """그 기능이 구현 단계로 갈 수 있는가(= 계획 승인이 끝났거나 필요 없는 크기). 새 흐름 기능(flow:v1)만 인정"""
    bf = flow_module()
    if bf is None or bf.FLOW_LABEL not in bf.labels(epic):
        return False
    return bf.stage_block(epic, bf.BUILD, bf.approval(hub, epic)) == ""


def find_pr(hub, repo, branch, origin_repo):
    """저장소·브랜치가 계획 승인된 기능의 통합 브랜치면 그 기능 카드 id, 아니면 ''. origin_repo(경로) → 'Org/Repo'"""
    for e in open_epics():
        if label_value(e, BRANCH_HEAD) != branch:
            continue
        path = label_value(e, REPO_HEAD)
        if path and origin_repo(path).lower() == (repo or "").lower() and build_ready(hub, e):
            return str(e.get("id") or "")
    return ""


def find_ticket(hub, key):
    """Jira 티켓이 계획 승인된 기능의 티켓이면 그 기능 카드 id, 아니면 ''"""
    want = (TICKET_HEAD + (key or "")).lower()
    for e in open_epics():
        if str(e.get("external_ref") or "").lower() == want and build_ready(hub, e):
            return str(e.get("id") or "")
    return ""
