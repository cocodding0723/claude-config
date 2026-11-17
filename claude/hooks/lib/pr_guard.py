"""gh 명령 해석 + PR 규칙 판정(pr-draft-guard.sh 가 부름). 인자: 시작 cwd. stdin: 원본 명령.
규칙: 새 PR 은 draft 로만(11-13 D-PRDRAFT) + 생성 자체도 사용자 승인 기록 D-PR<해시> 뒤에만(11-16) /
draft 해제(ready)는 그 PR 에 대한 사용자 승인 기록이 있을 때만.
출력: 판정 있으면 hook JSON 한 줄(deny|ask), 통과면 빈 출력. 따옴표 안·heredoc 본문의 글자는 명령이 아니라 무시하되
bash -c·eval 문자열은 다시 해석함. 판정 불가(PR 번호·저장소 해석 실패, 기록 읽기 실패)는 deny 가 아니라 ask."""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import push_targets as pt  # noqa: E402  따옴표·heredoc·cd 해석 재사용
import plan_approval as pa  # noqa: E402

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
PR_PREFIX = "D-PR"
API_CREATE_REASON = "gh api 로 새 PR 생성 — 승인 기록을 확인할 수 없음. gh pr create --draft 로 다시 실행(D-PR 승인 검사)하거나 사용자 승인 확인"
CREATE_APPROVAL_REASON = ("새 PR 생성은 사용자 승인 뒤에만(사용자 11-16) — [{repo} {branch}] 결정 카드 {did} 의 1번 답 필요. 예: "
                          "slack_notify.sh --kind decide --decision-id {did} --options '1=PR 올리기 => draft PR 생성,2=보류 => 로컬 유지' "
                          "--recommend 1 --no-doc $'{repo} {branch} PR 생성 승인\\n배경: …\\n용어: 없음'. 또는 계획서에 이 PR 을 추가한 뒤 컨펌 "
                          "→ plan-approvals/<plan-id>.json 의 prs 에 {{repo, branch}} 기록. Slack 봇이 꺼져 있으면 사용자에게 프롬프트에서 "
                          "'! ~/orca/board/bin/board-approve {repo} {branch} \"<원문>\"' 한 줄을 직접 치도록 안내(모델은 실행하지 않음)")


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


def open_store():
    """승인 기록 저장소(읽기 전용). 모듈·폴더·파일을 못 읽으면 Unknown"""
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
    import answer_store
    return answer_store.AnswerStore(d)


def card_ok(store, did, repo=""):
    """결정 카드 did 의 가장 최근 답이 approve 또는 pick 1 이면 True. repo 를 주면 제목에 다른 저장소#번호만 적힌 카드는 무시"""
    recs = [r for r in store.read_inbox() if r.get("decision_id") == did and r.get("kind") == "decide" and r.get("parent_ts")]
    done, last = store.read_done(), None
    for ts in dict.fromkeys(r["parent_ts"] for r in recs):  # 올라온 순서 유지
        _, rec = store.judge(store.read_inbox(), done, did, ts)
        if not rec:
            continue
        card = store.find_card(did, ts) or {}
        named = CARD_REPO_RE.findall(f"{card.get('title', '')} {rec.get('title', '')}")
        if repo and named and repo not in named:
            continue  # 다른 저장소의 같은 번호 카드
        last = rec
    return bool(last) and (last.get("action") == "approve" or str(last.get("pick")) == "1")


def plan_ok(find, *args):
    """계획 승인 기록 조회(새 PR·Jira 발급·상태 전환 전용). 읽기 실패는 Unknown"""
    try:
        return bool(find(hub_dir(), *args))
    except OSError as e:
        raise Unknown(f"계획 승인 기록을 읽지 못함: {e}")


def decided(did, repo=""):
    """card_ok 의 안전한 입구. 기록 형식·모듈 오류는 Unknown"""
    try:
        return card_ok(open_store(), did, repo)
    except Unknown:
        raise
    except Exception as e:  # noqa: BLE001
        raise Unknown(f"승인 기록 확인 실패: {e}")


def approved(repo, num):
    """draft 해제 승인 조회. ① 문서 사이트 PR 병합 승인(pr:저장소#번호) ② 결정 카드 D-RDY<번호> 1번 답. 기록을 못 읽으면 Unknown"""
    try:
        store = open_store()
        if f"pr:{repo}#{num}" in store.approvals():
            return True
        return card_ok(store, f"{RDY_PREFIX}{num}", repo)
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
        if not repo and not os.environ.get("GH_REPO") and here is None:
            raise Unknown("cd 경로에 변수·치환 — 저장소를 알 수 없음(-R 로 지정)")
        repo = repo or os.environ.get("GH_REPO", "") or origin_repo(here)
        if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo):  # HOST/OWNER/REPO 형태도 해석 불가로 둠
            raise Unknown("저장소를 알 수 없음(-R 로 지정)")
        return repo, arg.lstrip("#")
    raise Unknown(f"PR 번호를 해석하지 못함({arg or '인자 없음'}) — 번호나 URL 로 지정")


def pr_create_id(repo, branch):
    """새 PR 승인 결정 번호. D-PR + sha1("저장소:브랜치") 앞 8자(결정 번호 16자 제한이라 브랜치 이름을 그대로 못 씀)"""
    return PR_PREFIX + hashlib.sha1(f"{repo}:{branch}".encode()).hexdigest()[:8]


def current_branch(d):
    try:
        return subprocess.run(["git", "-C", d, "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check_create(toks, flags, repo, here):
    """gh pr create: --draft 필수 + D-PR<해시> 승인. 저장소·브랜치를 못 풀면 Unknown"""
    if not has_draft(flags):
        return "deny", CREATE_REASON
    head = ""
    for i, t in enumerate(toks):
        if t in ("-H", "--head") and i + 1 < len(toks):
            head = toks[i + 1]
        elif t.startswith("--head="):
            head = t.split("=", 1)[1]
    head = head.split(":", 1)[-1]  # owner:branch 형태
    if here is None and not (repo and head):
        raise Unknown("cd 경로에 변수·치환 — 저장소·브랜치를 알 수 없음(-R·--head 로 지정)")
    repo = repo or os.environ.get("GH_REPO", "") or origin_repo(here)
    head = head or current_branch(here)
    if not REPO_RE.fullmatch(repo or "") or not head:
        raise Unknown("새 PR 의 저장소·브랜치를 알 수 없음(-R·--head 로 지정)")
    if plan_ok(pa.find_pr, repo, head):
        return None
    did = pr_create_id(repo, head)
    if not decided(did, repo):
        return "deny", CREATE_APPROVAL_REASON.format(repo=repo, branch=head, did=did)
    return None


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
        return ("ask", API_CREATE_REASON) if re.search(r"draft\W{0,3}true", body) else ("deny", CREATE_REASON)
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
            return ("ask", API_CREATE_REASON) if "draft=true" in kv else ("deny", CREATE_REASON)
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
        return check_create(rest, flags, repo, here)
    if pos[:2] == ["pr", "ready"]:
        if "--undo" in flags:
            return None  # draft 로 되돌림
        return check_ready(pos[2] if len(pos) > 2 else "", repo, here)
    return None


def check_cmd(cmd, here, depth=0):
    for toks in pt.segments(pt.drop_heredoc(cmd)):
        if toks and toks[0] == "cd":
            args = [t for t in toks[1:] if t not in ("-P", "-L", "--")]
            # 경로를 못 풀어도 여기서 ask 하지 않음. gh 조각이 저장소를 필요로 할 때만 판정 불가(here=None)
            here = pt.resolve(here, args[0]) if args else HOME
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
        res = ("ask", f"PR 규칙 판정 불가: {e}. 이 PR 생성·열기가 사용자 승인된 것인지 확인 필요")
    if res:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": res[0], "permissionDecisionReason": res[1]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
