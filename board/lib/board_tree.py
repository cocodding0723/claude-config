"""증거를 낸 작업 트리와 브랜치 끝이 같은 내용인지 판정. board-run(증거를 카드에 달지)·board-done(증거를 인정할지)이 같이 씀.

board-run 은 커밋 안 된 새 파일까지 넣어 tree 를 만든다. 그래서 편집기가 만든 해법 파일(.slnx)이나 작업자가 모아 둔
evidence/ 폴더만 있어도 tree 가 커밋과 달라져, 코드가 같은데도 '다른 내용에서 돌린 결과'로 거부됐다(11-20 오후 대조한 3건 모두 코드 차이 0).
잡파일만 다르면 같은 내용으로 본다. 코드가 한 줄이라도 다르면 다르다.

잡파일 기준(두 가지):
- ONE_SIDE: 한쪽에만 있는 파일일 때만 잡파일(커밋에 없는 새 파일, 또는 나중 커밋이 더한 파일). 추적 중인 파일의 수정은 해당 없음
- 저장소별 설정 <BOARD_DIR>/repos.json 의 "evidence_ignore"(목록): 시험 중 도구가 고치는 추적 파일처럼 그 저장소만의 것. 수정도 잡파일로 봄

CLI: python3 board_tree.py same <저장소> <브랜치 끝 tree> <증거 tree>  → 같으면 종료 0, 다르면 1(달라진 파일을 출력), 비교 불가 2
"""
import fnmatch
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import board_config  # noqa: E402

ONE_SIDE = ("*.slnx", "evidence/*")
CONF_KEY = "evidence_ignore"


def matches(path: str, patterns) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def differing(repo: str, tip_tree: str, ev_tree: str):
    """[(상태 한 글자, 경로)] — 브랜치 끝에서 증거 tree 로 가는 차이. A=증거 쪽에만 있음, D=브랜치 끝에만 있음, M=내용 다름. 비교 불가면 None"""
    r = subprocess.run(["git", "-C", repo, "-c", "core.quotePath=false", "diff", "--name-status", "--no-renames", tip_tree, ev_tree],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    rows = []
    for ln in r.stdout.splitlines():
        st, _, path = ln.partition("\t")
        if path:
            rows.append((st.strip()[:1], path))
    return rows


def real_changes(repo: str, tip_tree: str, ev_tree: str, board=None):
    """잡파일을 뺀 달라진 경로들. tree 를 못 읽으면 None(같다고 보지 않음)"""
    rows = differing(repo, tip_tree, ev_tree)
    if rows is None:
        return None
    conf = board_config.get_list(repo, CONF_KEY, board)
    return [p for st, p in rows if not (matches(p, conf) or (st in ("A", "D") and matches(p, ONE_SIDE)))]


def same(repo: str, tip_tree: str, ev_tree: str, board=None) -> bool:
    if tip_tree == ev_tree:
        return True
    return real_changes(repo, tip_tree, ev_tree, board) == []


def main(argv) -> int:
    if len(argv) != 4 or argv[0] != "same":
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    _, repo, tip_tree, ev_tree = argv
    if tip_tree == ev_tree:
        return 0
    rest = real_changes(repo, tip_tree, ev_tree)
    if rest is None:
        print("tree 를 읽을 수 없음", file=sys.stderr)
        return 2
    print("\n".join(rest))
    return 1 if rest else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
