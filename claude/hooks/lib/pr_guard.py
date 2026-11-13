"""gh 명령 해석 + PR draft 규칙 판정(pr-draft-guard.sh 가 부름). 인자: 시작 cwd. stdin: 원본 명령.
규칙(11-13 D-PRDRAFT): 새 PR 은 draft 로만 / draft 해제(ready)는 그 PR 에 대한 사용자 승인 기록이 있을 때만.
출력: 판정 있으면 hook JSON 한 줄(deny|ask), 통과면 빈 출력. 따옴표 안·heredoc 본문의 글자는 명령이 아니라 무시하되
bash -c·eval 문자열은 다시 해석함. 판정 불가(PR 번호·저장소 해석 실패, 기록 읽기 실패)는 deny 가 아니라 ask."""
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import push_targets as pt  # noqa: E402  따옴표·heredoc·cd 해석 재사용

HOME = os.path.expanduser("~")
WRAPPERS = {"env", "command", "nohup", "time", "exec", "builtin"}
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{"}
SHELLS = {"bash", "sh", "zsh", "dash"}
CREATE_REASON = "새 PR 은 draft 로만(사용자 11-13 D-PRDRAFT). --draft 를 붙여 다시 실행"
READY_REASON = "이 PR 을 여는 건 사용자 승인 뒤에만 — 결정 카드(번호 D-RDY{num})로 승인 요청"
BOOL_SHORT = re.compile(r"^-[fwed]*d[fwed]*$")  # -d 단독·불리언 짧은 옵션 묶음
PR_URL = re.compile(r"^https?://[^/]+/([\w.\-]+/[\w.\-]+)/pull/(\d+)")
API_PULL = re.compile(r"repos/([\w.\-]+/[\w.\-]+)/pulls(?:/(\d+))?/?$")
REPO_RE = re.compile(r"[\w.\-]+/[\w.\-]+")
CARD_REPO_RE = re.compile(r"([\w.\-]+/[\w.\-]+)#\d+")
RDY_PREFIX = "D-RDY"


class Unknown(Exception):
    """판정 불가 사유. ask 로 돌림"""


def hub_dir():
    return os.environ.get("LRM_HUB_DIR") or os.path.join(HOME, ".config", "lrm-hubs")


def store_module_dir():
    """answer_store.py 위치. 이 저장소의 hubs/scripts → 설치된 ~/.config/lrm-hubs 순"""
    for d in (os.environ.get("LRM_HUB_SCRIPTS"), os.path.join(HERE, "..", "..", "..", "hubs", "scripts"), os.path.join(HOME, ".config", "lrm-hubs")):
        if d and os.path.isfile(os.path.join(d, "answer_store.py")):
            return os.path.realpath(d)
    return ""


def origin_repo(d):
    try:
        url = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.match(r"^(?:git@[^:]+:|https?://[^/]+/|ssh://[^/]+/)([\w.\-]+/[\w.\-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else ""


def approved(repo, num):
    """승인 기록 조회(읽기 전용, 유일한 입구). True|False. 기록을 못 읽으면 Unknown.
    ① 문서 사이트 PR 병합 승인(pr:저장소#번호) ② 결정 카드 D-RDY<번호>: 가장 최근 답이 approve 또는 pick 1"""
    mod = store_module_dir()
    if not mod:
        raise Unknown("승인 기록 모듈(answer_store.py)을 찾지 못함")
    d = hub_dir()
    if not os.path.isdir(d):
        raise Unknown(f"승인 기록 폴더 없음: {d}")
    for name in ("slack-inbox.jsonl", "pr-approve.jsonl"):
        p = os.path.join(d, name)
        if os.path.exists(p) and not (os.path.isfile(p) and os.access(p, os.R_OK)):
            raise Unknown(f"승인 기록을 읽지 못함: {p}")
    sys.path.insert(0, mod)
    try:
        import answer_store
        store = answer_store.AnswerStore(d)
        if f"pr:{repo}#{num}" in store.approvals():
            return True
        did = f"{RDY_PREFIX}{num}"
        recs = [r for r in store.read_inbox() if r.get("decision_id") == did and r.get("kind") == "decide" and r.get("parent_ts")]
        done, last = store.read_done(), None
        for ts in dict.fromkeys(r["parent_ts"] for r in recs):  # 올라온 순서 유지
            _, rec = store.judge(store.read_inbox(), done, did, ts)
            if not rec:
                continue
            card = store.find_card(did, ts) or {}
            named = CARD_REPO_RE.findall(f"{card.get('title', '')} {rec.get('title', '')}")
            if named and repo not in named:
                continue  # 다른 저장소의 같은 번호 카드
            last = rec
        return bool(last) and (last.get("action") == "approve" or str(last.get("pick")) == "1")
    except Unknown:
        raise
    except Exception as e:  # noqa: BLE001 기록 형식·모듈 오류는 판정 불가
        raise Unknown(f"승인 기록 확인 실패: {e}")


def parse_gh(toks):
    """gh 토큰 → (하위 명령 목록, 플래그, 위치 인자). -R/--repo 값은 repo 로 뺌"""
    flags, pos, repo, k = [], [], "", 0
    while k < len(toks):
        t = toks[k]
        if t == "--":
            pos += toks[k + 1:]; break
        if t in ("-R", "--repo") and k + 1 < len(toks):
            repo = toks[k + 1]; k += 2; continue
        if t.startswith("--repo="):
            repo = t.split("=", 1)[1]
        elif t.startswith("-") and t != "-":
            flags.append(t)
        else:
            pos.append(t)
        k += 1
    return flags, pos, repo


def has_draft(flags):
    for f in flags:
        if f in ("--draft", "--draft=true") or BOOL_SHORT.match(f):
            return True
    return False


def resolve_pr(arg, repo, here):
    """(저장소, 번호). 번호·URL 만 해석. 브랜치·빈 값은 Unknown"""
    m = PR_URL.match(arg or "")
    if m:
        return m.group(1), m.group(2)
    if re.fullmatch(r"#?\d+", arg or ""):
        repo = repo or os.environ.get("GH_REPO", "") or origin_repo(here)
        if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo):  # HOST/OWNER/REPO 형태도 해석 불가로 둠
            raise Unknown("저장소를 알 수 없음(-R 로 지정)")
        return repo, arg.lstrip("#")
    raise Unknown(f"PR 번호를 해석하지 못함({arg or '인자 없음'}) — 번호나 URL 로 지정")


def check_ready(arg, repo, here):
    r, n = resolve_pr(arg, repo, here)
    if not approved(r, n):
        return "deny", READY_REASON.format(num=n) + f" [{r}#{n}]"
    return None


def api_checks(toks, here):
    """gh api 우회: PR 생성(draft 아님)·draft 해제(PATCH draft=false)·GraphQL 변이"""
    flags, pos, repo = parse_gh(toks)
    body = " ".join(toks)
    if "markPullRequestReadyForReview" in body:
        raise Unknown("GraphQL markPullRequestReadyForReview 는 PR 번호를 알 수 없음 — 승인 기록 확인 필요")
    if "createPullRequest" in body:
        return None if re.search(r"draft\W{0,3}true", body) else ("deny", CREATE_REASON)
    path = next((p for p in pos[1:] if "repos/" in p), "")
    m = API_PULL.search(path.split("?")[0])
    if not m:
        return None
    method = "GET"
    for i, t in enumerate(toks):
        if t in ("-X", "--method") and i + 1 < len(toks):
            method = toks[i + 1].upper()
        elif t.startswith("--method="):
            method = t.split("=", 1)[1].upper()
    kv = [toks[i + 1] for i, t in enumerate(toks[:-1]) if t in ("-f", "-F", "--field", "--raw-field")]
    if not m.group(2):  # repos/o/r/pulls : 생성
        if method == "POST" or (method == "GET" and kv):
            return None if "draft=true" in kv else ("deny", CREATE_REASON)
        return None
    if method in ("PATCH", "PUT") and "draft=false" in kv:
        if not approved(m.group(1), m.group(2)):
            return "deny", READY_REASON.format(num=m.group(2)) + f" [{m.group(1)}#{m.group(2)}]"
    return None


def check_segment(toks, here, depth=0):
    while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in WRAPPERS or toks[0] in KEYWORDS):
        toks = toks[1:]
    if not toks:
        return None
    exe = os.path.basename(toks[0])
    if exe in SHELLS | {"eval"} and depth < 3:  # 문자열을 다시 실행하는 형태는 안을 해석
        inner = None
        if exe == "eval":
            inner = " ".join(toks[1:])
        else:
            for i, t in enumerate(toks[1:], 1):
                if re.fullmatch(r"-[a-zA-Z]*c", t) and i + 1 < len(toks):
                    inner = toks[i + 1]; break
        return check_cmd(inner, here, depth + 1) if inner else None
    if exe != "gh":
        return None
    rest = toks[1:]
    flags, pos, repo = parse_gh(rest)
    if pos[:1] == ["api"]:
        return api_checks(rest, here)
    if pos[:2] == ["pr", "create"]:
        return None if has_draft(flags) else ("deny", CREATE_REASON)
    if pos[:2] == ["pr", "ready"]:
        if "--undo" in flags:
            return None  # draft 로 되돌림
        return check_ready(pos[2] if len(pos) > 2 else "", repo, here)
    return None


def check_cmd(cmd, here, depth=0):
    for toks in pt.segments(pt.drop_heredoc(cmd)):
        if toks and toks[0] == "cd":
            args = [t for t in toks[1:] if t not in ("-P", "-L", "--")]
            r = pt.resolve(here, args[0]) if args else HOME
            if r is None:
                raise Unknown("cd 경로에 변수·치환 — 저장소를 알 수 없음")
            here = r
            continue
        res = check_segment(toks, here, depth)
        if res:
            return res
    return None


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.getcwd()
    try:
        res = check_cmd(sys.stdin.read(), cwd)
    except Unknown as e:
        res = ("ask", f"PR draft 규칙 판정 불가: {e}. 이 PR 을 여는 게 사용자 승인된 것인지 확인 필요")
    if res:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": res[0], "permissionDecisionReason": res[1]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
