"""저장소별 작업판 설정. <BOARD_DIR>/repos.json = {"<저장소 id 또는 경로>": {"compile": "<명령>", "merge_verify": "<명령>", "evidence_ignore": ["<경로 패턴>"]}}.
evidence_ignore: 증거 tree 와 브랜치 끝을 견줄 때 잡파일로 볼 경로(시험 중 도구가 고치는 추적 파일). 판정은 board_tree.
키는 board_scope.repo_id 값(remote:github.com/org/repo 처럼 'remote:'·'git:' 를 뗀 것도 됨) 또는 저장소 경로.
게이트 컴파일(board-done)·시험 병합 검증(board-merge)을 저장소마다 켜는 곳. 없으면 호출하는 쪽이 경고만 냄.
CLI: python3 board_config.py get <저장소 경로> <키>  → 값 출력(없으면 빈 출력, 종료 0)"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import board_scope as bs  # noqa: E402


def path(board=None):
    board = board or os.environ.get("BOARD_DIR", os.path.expanduser("~/orca/board"))
    return os.path.join(board, "repos.json")


def load(board=None):
    try:
        with open(path(board), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def raw(repo, key, board=None):
    """그 저장소의 설정 값 그대로. 없으면 None"""
    conf = load(board)
    rid = bs.repo_id(repo)
    top = bs.toplevel(repo) if repo else ""
    for k in (rid, rid.split(":", 1)[-1] if rid else "", top, os.path.expanduser(repo or "")):
        if k and isinstance(conf.get(k), dict) and conf[k].get(key):
            return conf[k][key]
    for k, v in conf.items():
        kp = os.path.expanduser(k)
        if isinstance(v, dict) and v.get(key) and top and os.path.isabs(kp) and bs.toplevel(kp) == top:
            return v[key]
    return None


def get(repo, key, board=None):
    v = raw(repo, key, board)
    return "" if v is None else str(v)


def get_list(repo, key, board=None):
    """목록 값. 설정에는 JSON 배열로 적음(문자열 하나면 한 칸짜리 목록). 없으면 []"""
    v = raw(repo, key, board)
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v] if isinstance(v, list) else []


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "get":
        print("사용: board_config.py get <저장소 경로> <키>", file=sys.stderr)
        sys.exit(2)
    print(get(sys.argv[2], sys.argv[3]))
