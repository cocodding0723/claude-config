"""작업판 카드 label 해석 공용. board-claim·board-merge-check·board-merge·board-done 이 같이 씀.
file: 범위는 표기(~/·상대·./·끝 /)가 섞여 들어와도 같은 경로로 비교하고, 접두 비교는 '/' 경계에서만 함
(file:src/a 가 src/ab 를 덮지 않게).
겹침은 (저장소 id, 저장소 안 경로) 쌍으로 비교함. 작업자마다 repo: 가 다른 작업 공간이어도 같은 저장소면 같은 파일로 봄."""
import os
import re
import subprocess


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


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _remote_id(url):
    """git@host:a/b.git · https://u@host/a/b · ssh://git@host/a/b.git → host/a/b"""
    u = re.sub(r"^[a-z+]+://", "", url.strip())
    u = re.sub(r"^[^@/]+@", "", u)
    u = re.sub(r"^([^/:]+):(?!\d)", r"\1/", u)
    return re.sub(r"(\.git)?/*$", "", u).lower()


_TOP, _ID = {}, {}


def toplevel(path):
    """path(없으면 가장 가까운 있는 상위 폴더)가 속한 git 작업 트리 최상위. 없으면 ''"""
    d = os.path.abspath(os.path.expanduser(path))
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent
    if d not in _TOP:
        top = _git(d, "rev-parse", "--show-toplevel")
        _TOP[d] = os.path.realpath(top) if top else ""
    return _TOP[d]


def repo_id(repo):
    """저장소 정체. origin 원격 주소 → 공통 git 폴더(같은 저장소의 worktree 끼리 같음). git 저장소가 아니면 ''"""
    top = toplevel(repo) if repo else ""
    if not top:
        return ""
    if top not in _ID:
        url = _git(top, "config", "--get", "remote.origin.url")
        if url:
            _ID[top] = "remote:" + _remote_id(url)
        else:
            common = _git(top, "rev-parse", "--path-format=absolute", "--git-common-dir")
            _ID[top] = "git:" + os.path.realpath(common or top)
    return _ID[top]


def locate(path, repo=""):
    """file: 값 → (저장소 id, 저장소 안 경로). 상대 경로는 repo 기준, 절대 경로는 그 경로가 속한 저장소 기준.
    저장소를 모르면 id '' 와 정리된 경로(절대 경로는 실제 경로)"""
    p = os.path.expanduser(path.strip())
    if not os.path.isabs(p):
        rid = repo_id(repo)
        if rid:
            rel = os.path.normpath(p)
            return (rid, "" if rel == "." else rel)
        return ("", norm(p, repo))
    top = toplevel(p)
    if top:
        real = os.path.realpath(p)
        if covers(top, real):
            return (repo_id(top), "" if real == top else os.path.relpath(real, top))
    return ("", norm(p))


def loc_covers(a, b):
    """a 범위가 b 를 덮으면 참. 저장소 id 가 같아야 하고, 저장소 안 경로 '' 는 저장소 전체"""
    return a[0] == b[0] and (a[1] == "" or covers(a[1], b[1]))


def loc_overlap(a, b):
    return loc_covers(a, b) or loc_covers(b, a)


def loc_text(loc):
    rid, rel = loc
    return f"{rid.split(':', 1)[-1]}:{rel or '.'}" if rid else rel


def too_broad(loc):
    """저장소 전체(file:.)·파일 시스템 최상위처럼 아무 변경이나 통과시키는 범위"""
    rid, rel = loc
    home = os.path.expanduser("~")
    return (bool(rid) and rel == "") or rel in ("", ".") or (not rid and os.path.isabs(rel) and (rel.count("/") <= 1 or rel in (home, os.path.dirname(home))))


DIRECTIVE_HEAD = re.compile(r"^#{1,6}\s*지시\b.*$", re.M)
DIRECTIVE_INLINE = re.compile(r"사용자[^\n:：]{0,16}원문\s*[:：]\s*(\S.*)")


def directive(card):
    """카드의 '지시(사용자 원문 + 뒤 지시)' 칸 내용. 양식: '## 지시 …' 제목 아래 본문, 또는 '사용자 … 원문: …' 한 줄. 없거나 자리표시뿐이면 ''"""
    text = card.get("description") or ""
    m = DIRECTIVE_HEAD.search(text)
    if m:
        body = text[m.end():]
        nxt = re.search(r"^#{1,6}\s", body, re.M)
        body = body[:nxt.start()] if nxt else body
    else:
        m = DIRECTIVE_INLINE.search(text)
        body = m.group(1) if m else ""
    body = re.sub(r"<[^>\n]*>", "", body)
    body = re.sub(r"[-*\s:：\"'“”()（）]|원문|뒤\s*지시", "", body)
    return body


def card_repo(card, fallback=""):
    """카드 label repo: 가 읽을 수 있는 git 저장소면 그 경로, 아니면(반납된 작업 공간 등) fallback"""
    r = os.path.expanduser(label(card, "repo"))
    return r if r and repo_id(r) else fallback
