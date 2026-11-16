"""작업판 카드 label 해석 공용. board-claim·board-merge-check·board-merge·board-done 이 같이 씀.
file: 범위는 표기(~/·상대·./·끝 /)가 섞여 들어와도 같은 경로로 비교하고, 접두 비교는 '/' 경계에서만 함
(file:src/a 가 src/ab 를 덮지 않게)."""
import os


def labels(card, key):
    p = key + ":"
    return [l[len(p):] for l in card.get("labels") or [] if l.startswith(p)]


def label(card, key):
    v = labels(card, key)
    return v[-1] if v else ""


def norm(path, repo=""):
    """~ 확장, repo 기준 상대 경로를 절대 경로로, 끝 / 제거. repo 가 없으면 상대 경로는 ./ 만 정리"""
    p = os.path.expanduser(path.strip())
    if not os.path.isabs(p) and repo:
        p = os.path.join(repo, p)
    p = os.path.normpath(p)
    return "" if p == "." else p


def covers(prefix, path):
    """prefix 가 path 와 같거나 path 의 상위 폴더면 참. 둘 다 norm 된 값"""
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def overlap(a, b):
    return covers(a, b) or covers(b, a)
