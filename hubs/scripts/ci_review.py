"""CI AI 리뷰 지적 수집·메인 전달. ci_watch.py 가 불러 씀.

리뷰 코멘트: github-actions 봇의 이슈 코멘트 '<!-- lrm-ai-review:역할 -->'. 지적 = '아쉬운 점' 의 **무엇:** 항목 + '반드시 수정' 내용.
처리한 지적은 해시로 상태에 남겨 다시 보내지 않음. 메인 경유 원칙: 허브에 직접 보내지 않고 메인 터미널에만 알림.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hub_status
import main_terminal as mt

MARK = re.compile(r"<!-- lrm-ai-review:(\w+) -->")
SECTION = re.compile(r"^###\s*아쉬운 점.*?$(.*?)(?=^###|^\*\*반드시|\Z)", re.M | re.S)
ITEM = re.compile(r"\*\*무엇:\*\*\s*(.+?)(?=\n\s*\*\*무엇:\*\*|\Z)", re.S)
MUST = re.compile(r"\*\*반드시 수정해야 하는 점:\*\*\s*(.+?)(?=\n\s*\*\*팀 규칙|\n\[실행 기록|\Z)", re.S)
COMMIT = re.compile(r"`([0-9a-f]{7,40})`")
TICKET = re.compile(r"LRM-(\d+)")
ERROR = re.compile(r"^##\s.*—\s*ERROR\s*$", re.M)  # 리뷰어 자체가 실패한 코멘트(지적 아님, 재실행 대상)
EMPTY = ("없음", "평가 결과 없음")
REVIEW_DONE = ("Claude Code Review",)  # 이 워크플로 검사가 전부 끝나야 수집


def review_finished(checks):
    mine = [c for c in checks if c.get("workflow") in REVIEW_DONE or c.get("name", "").startswith("AI Review Gate")]
    return bool(mine) and all(c["bucket"] in ("pass", "fail", "cancel", "skipping") for c in mine)


def parse(comment):
    """코멘트 하나 → 지적 목록 [{role, kind, text, commit}]"""
    body = comment.get("body", "")
    m = MARK.search(body)
    if not m or m.group(1) == "summary":
        return []
    role, commit = m.group(1), (COMMIT.search(body) or [None, ""])[1]
    if ERROR.search(body):
        e = {"role": role, "kind": "오류", "text": "리뷰어 오류(평가 결과 없음)", "commit": commit}
        e["id"] = hashlib.sha1(f"{comment.get('id')}|오류|{commit}".encode()).hexdigest()[:16]
        return [e]
    out = []
    sec = SECTION.search(body)
    if sec:
        for it in ITEM.finditer(sec.group(1)):
            out.append({"role": role, "kind": "권장", "text": " ".join(it.group(1).split())[:600], "commit": commit})
    must = MUST.search(body)
    if must and not must.group(1).strip().startswith(EMPTY):
        out.append({"role": role, "kind": "필수", "text": " ".join(must.group(1).split())[:600], "commit": commit})
    for f in out:
        f["id"] = hashlib.sha1(f"{comment.get('id')}|{f['kind']}|{f['text']}".encode()).hexdigest()[:16]
    return out


UNASSIGNED = "미배정(메인 판단)"
HUB_DIR = re.compile(r"^coord-(.+)-hub$")  # 허브 폴더 이름. 가운데가 hubs.tsv 의 slug


def load_hub_names(path):
    """hubs.tsv → {slug: 표시 이름}. 읽기 실패는 빈 표"""
    names = {}
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            cols = ln.split("\t")
            if ln.strip() and not ln.startswith("#") and len(cols) >= 2:
                names[cols[0].strip()] = cols[1].strip()
    except OSError:
        pass
    return names


def load_hub_aliases(path):
    """hubs.tsv 의 역할 파일 role-<별칭>.txt → {별칭: slug}. 접수 경로(intake-routes)가 허브를 별칭으로 적기 때문"""
    out = {}
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            cols = ln.split("\t")
            m = re.match(r"role-(.+)\.txt$", os.path.basename(cols[2])) if len(cols) > 2 else None
            if ln.strip() and not ln.startswith("#") and m:
                out[m.group(1)] = cols[0].strip()
    except OSError:
        pass
    return out


REPO_ID = re.compile(r"^[\w.-]+/[\w.-]+$")
UNKNOWN_REPO = "?"  # 경로 basename 만 알아 owner 를 확인할 수 없음


def load_hub_repos(path):
    """hubs.tsv → {slug: 저장소}. 5번째 칸 '-'·빈 칸은 ''(기본 게임 저장소), 'owner/name' 은 그대로, 경로(~/…·/…)는 owner 를 알 수 없어 UNKNOWN_REPO"""
    out = {}
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            cols = [c.strip() for c in ln.split("\t")]
            if ln.strip() and not ln.startswith("#") and len(cols) >= 2:
                cell = cols[4] if len(cols) > 4 else "-"
                out[cols[0]] = "" if cell in ("", "-") else cell if REPO_ID.match(cell) and not cell.startswith((".", "~")) else UNKNOWN_REPO
    except OSError:
        pass
    return out


def fresh_status(data, now=None):
    """허브 상태 파일이 낡지 않았는지. updatedAt 이 없거나 읽을 수 없거나 hub_status 의 낡음 기준(stale_hours) 초과면 False"""
    at = hub_status.parse_iso((data or {}).get("updatedAt"))
    now = now or datetime.now(timezone.utc)
    return at is not None and now - at <= timedelta(hours=hub_status.stale_hours())


def ticket_hubs(status_dir, routes_path, hubs_path, repo="", game_repo="", worktrees=None):
    """티켓 번호 → 그 티켓을 맡은 허브 표시 이름 집합. 근거는 기존 데이터: 허브 상태 파일(tasks·branches 의 LRM-n, 낡은 파일 제외)과
    접수 경로(jsonl 의 text·hub). repo: 대상 PR 저장소('owner/name'), game_repo: hubs.tsv 저장소 칸이 '-' 인 허브의 저장소.
    허브 저장소는 owner/name 전체로 비교하며 소속을 확인할 수 없으면(경로만 있음, game_repo 비어 있음) 제외. repo 가 비면 저장소 검사를 하지 않음.
    worktrees: Orca 작업 공간 목록(조회 실패·불완전이면 None → 이 근거만 건너뜀). 표시 이름·브랜치의 LRM-n 을 부모 허브 폴더(coord-<slug>-hub)로 연결.
    작업 공간 근거는 PR 과 같은 저장소(projectId)만 쓰며, 상태 파일·접수 경로에 없는 허브가 더해지면 티켓이 갈린 것으로 보아 미배정이 됨"""
    names, aliases, repos, out = load_hub_names(hubs_path), load_hub_aliases(hubs_path), load_hub_repos(hubs_path), {}
    want, home = repo.lower(), game_repo.lower()

    def ok_repo(slug):
        have = repos.get(slug, UNKNOWN_REPO)
        return not want or (have == "" and home == want) or (have not in ("", UNKNOWN_REPO) and have.lower() == want)

    try:
        files = sorted(Path(status_dir).glob("*.json"))
    except OSError:
        files = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        slug = str(d.get("slug") or f.stem)
        if not fresh_status(d) or not ok_repo(slug):
            continue
        for t in TICKET.findall(" ".join(map(str, (d.get("tasks") or []) + (d.get("branches") or [])))):
            out.setdefault(t, set()).add(slug)
    try:
        rows = Path(routes_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        rows = []
    for ln in rows:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        slug = aliases.get(str(r.get("hub")))
        for t in TICKET.findall(str(r.get("text", ""))) if slug and ok_repo(slug) else ():
            out.setdefault(t, set()).add(slug)
    for t, slugs in workspace_tickets(worktrees, names, repo).items():
        out.setdefault(t, set()).update(slugs)
    return {t: {names[s] for s in v if s in names} for t, v in out.items()}


def workspace_tickets(worktrees, names, repo=""):
    """작업 공간 목록 → {티켓 번호: {허브 slug}}. 표시 이름·브랜치의 LRM-n 이 있는 작업 공간의 부모 허브 폴더 이름(coord-<slug>-hub)에서 slug 결정.
    PR 저장소와 projectId 가 다르거나 부모가 허브가 아니면 제외"""
    by_id = {w.get("id"): w for w in worktrees or []}
    out = {}
    for w in worktrees or []:
        if not same_repo(w, repo):
            continue
        parent = by_id.get(w.get("parentWorktreeId"))
        m = HUB_DIR.match(os.path.basename((parent or {}).get("path") or str(w.get("parentWorktreeId") or "").split("::")[-1]))
        if not (m and m.group(1) in names):
            continue
        for t in TICKET.findall(f"{w.get('displayName', '')} {w.get('branch', '')}"):
            out.setdefault(t, set()).add(m.group(1))
    return out


def title_hubs(title, tmap):
    """PR 제목의 티켓들이 가리키는 (허브, 'LRM-n') 목록. 허브 이름 순"""
    return sorted({(h, "LRM-" + t) for t in TICKET.findall(title or "") for h in tmap.get(t, ())})


def hub_name(base, names):
    """폴더 이름 → 표시 이름. coord-<slug>-hub 의 slug, 또는 'coord-server-hub' 처럼 폴더 이름 자체가 slug 인 허브"""
    m = HUB_DIR.match(base)
    for slug in ([m.group(1)] if m else []) + [base]:
        if slug in names:
            return names[slug]
    return ""


def same_repo(wt, repo):
    """작업 공간이 PR 저장소('owner/name')의 것인지. projectId 가 없으면 같다고 보지 않음"""
    return bool(repo) and str(wt.get("projectId") or "").lower() == f"github:{repo}".lower()


def parent_hub(branch, worktrees, names, repo=""):
    """PR head 브랜치를 가진 작업 공간의 허브 표시 이름. 못 찾으면 빈 문자열.
    PR 과 같은 저장소의 작업 공간 중 후보가 정확히 1개일 때만 지정(이름만 같은 다른 저장소·중복은 건너뜀).
    후보가 허브 폴더(coord-*-hub) 자체면 그 허브, 아니면 부모 허브. 부모 id 는 '<repo>::<경로>' 형식"""
    if not branch:
        return ""
    found = [w for w in worktrees if w.get("branch") == f"refs/heads/{branch}" and same_repo(w, repo)]
    if len(found) != 1:
        return ""
    w = found[0]
    own = hub_name(os.path.basename(str(w.get("path") or "")), names)
    if own:
        return own
    by_id = {x.get("id"): x for x in worktrees}
    parent = by_id.get(w.get("parentWorktreeId"))
    return hub_name(os.path.basename((parent or {}).get("path") or str(w.get("parentWorktreeId") or "").split("::")[-1]), names)


def repo_path(orca, repo, timeout=20):
    """PR 저장소('owner/name') → orca 에 등록된 로컬 저장소 경로. 이름이 정확히 1개 일치할 때만, 아니면 빈 문자열"""
    rc, data = mt.run_orca(orca, ["repo", "list", "--json"], timeout)
    repos = ((data or {}).get("result") or {}).get("repos")
    if rc != 0 or not isinstance(repos, list):
        return ""
    name = repo.split("/")[-1].lower()
    hit = [r.get("path") for r in repos if str(r.get("displayName", "")).lower() == name and r.get("path")]
    return hit[0] if len(hit) == 1 else ""


def list_worktrees(orca, repo="", limit=1000, timeout=30):
    """PR 저장소 범위의 작업 공간 목록(orca worktree list, 읽기 전용). 저장소 지정 없이 부르면 전체 목록이 잘리고 원격 호스트가 빠지므로
    저장소 매핑 실패·조회 실패·잘림(truncated)·누락 호스트는 None(조회 불완전)"""
    path = repo_path(orca, repo, timeout)
    if not path:
        return None
    rc, data = mt.run_orca(orca, ["worktree", "list", "--repo", f"path:{path}", "--limit", str(limit), "--json"], timeout)
    res = (data or {}).get("result") or {}
    wts = res.get("worktrees")
    if rc != 0 or not isinstance(wts, list) or res.get("truncated") or (res.get("hostScope") or {}).get("omittedHostIds"):
        return None
    return wts


GENERIC = re.compile(r"(?i)exit(ed)?( with)? (code|status) \d+|^(##\[error\])?\s*(error)?\.?\s*$|^\d*\s*failed,\s*\d+\s*skipped\b|^(?:test run\s+)?failed:?\s*\d+\s*$")  # 종료 코드·시험 개수 요약('failed, 2 skipped', 'FAILED: 0')은 원인 식별자가 아님. 모든 PR 에 같아 공통 원인으로 오분류됨(11-13)


def specific(cause):
    """구체 원인 식별자(실패 시험 이름·assert 문구·오류 첫 줄)인지. 빈 값·일반 종료 요약은 아님"""
    return bool(cause) and not GENERIC.search(cause)


# 실패 메시지 정규화 규칙(공통 원인 비교용). 확인된 가변 필드만 자리표시자로 치환. 스테이지 이름·기댓값 같은 숫자는 보존
SIG_RULES = [(re.compile(p), r) for p, r in (
    (r"\d{4}-\d\d-\d\dT?[\d:.]*Z?|\b\d{1,2}:\d\d(:\d\d)?(\.\d+)?\b", "<t>"),  # 날짜·시각
    (r"(?:[\w.-]*/)+[\w.-]*(?::\d+)?", "<p>"),  # 경로(파일:줄 포함)
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<g>"),  # GUID
    (r"(?i)(\b(?:commit|sha\d*|hash|checksum|md5|rev)\b[\s:=#@`'\"]*)[0-9a-f]{7,64}\b", r"\1<h>"),  # 해시 필드 값
    (r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{12,64}\b", "<h>"),  # 문맥 없는 긴 해시(12자 이상 문자·숫자 혼합). 1e00000 같은 짧은 값은 의미 있는 값으로 보존
    (r"(?:PR|pr|#)\s?\d+", "<n>"),  # PR 번호
    (r"\b\d+(?:\.\d+)?\s?(?:ms|msec|sec|s|초|분|시간)(?![\w])", "<d>"),  # 소요 시간
)]


def signature(text):
    """실패 메시지 → 공통 원인 비교값. 가변 필드 제거·공백 정리한 전체 메시지(자르지 않음)"""
    for rx, rep in SIG_RULES:
        text = rx.sub(rep, text or "")
    return " ".join(text.split())


LEGACY_NAME = re.compile(r"^[\w.+()\[\],/<>-]+$")  # xml 시험 이름 꼴(공백 없는 한 토큰)
LOG_WORDS = {"error", "fail", "failed", "failure", "warning", "exception", "assert"}  # 로그 줄 머리말은 시험 이름이 아님


def fail_sigs(c):
    """실패 항목의 비교값 집합. 신형(msg 있음)은 메시지만. 구형(msg 없음)은 cause 전체, 그리고 '시험명: 메시지' 로 볼 근거가 있을 때만 시험명을 뗀 나머지.
    근거 = 접두가 공백 없는 한 토큰이고 로그 머리말(Error 등)이 아님. 'Shader error in Foo: …' 같이 판별이 불명한 값은 전체 문자열로만 비교(보수적)"""
    if c.get("msg"):
        return {signature(c["msg"])} if specific(c["msg"]) else set()
    text = c.get("cause", "")
    if not specific(text):
        return set()
    name, sep, rest = text.partition(": ")
    named = bool(sep) and LEGACY_NAME.match(name) and name.lower() not in LOG_WORDS
    return {signature(text)} | ({signature(rest)} if named and specific(rest) else set())


# 다른 검사를 감싸기만 하는 작업. 요약 줄('FAILED: 0' 등)이 모든 PR 에 같아 공통 원인으로 오분류됨(11-12~13 5회) → 비교에서 뺌
WRAPPER_CHECKS = {"Run Unity validation / Unity validation"}


def common_cause(causes, own):
    """다른 열린 PR 에도 같은 검사가 같은 실패 메시지(정규화 전체)로 실패했는지. causes: {PR 번호: {head, fails:[{key, cause, msg}], hub}}, own: 이 PR 번호.
    시험 이름만 같고 메시지가 다르면 공통이 아님. 구형 상태(msg 없음)는 fail_sigs 로 이관 없이 비교. 구체 식별자가 없으면(종료 코드 요약 등) 제외.
    번호 순 정렬이라 처리 순서와 무관"""
    def ids(e):
        return {(c["key"], g) for c in (e or {}).get("fails", []) if c["key"] not in WRAPPER_CHECKS for g in fail_sigs(c)}

    mine = ids(causes.get(own))
    return sorted((n for n, e in causes.items() if n != own and mine & ids(e)), key=lambda n: (len(n), n))


def prune_causes(causes, open_heads):
    """열린 PR 의 현재 head 에 묶인 원인만 남김. open_heads: {PR 번호: head}"""
    for n in [n for n, e in causes.items() if open_heads.get(n) != e.get("head")]:
        del causes[n]


def hub_for(branch, title, table):
    """표만 쓰는 담당 허브. 표(정규식\t허브) 위에서부터 첫 일치, 없으면 '미배정'"""
    return assign_hub(branch, title, table)[0]


OWNER_KINDS = {"lrm": re.compile(r"(?i)^LRM-"), "pr": re.compile(r"(?i)^pr:")}
RANGE_MAX = 1000  # 범위 한 줄이 이만큼 넘으면 오타로 보고 줄을 건너뜀


def parse_owner_key(key):
    """'LRM-461,464,557..567' · 'pr:717..719' → (종류, 번호 집합). 형식 오류는 ValueError.
    접두사는 맨 앞에 한 번만, 뒤는 숫자 또는 'A..B' 범위를 쉼표로 이음"""
    key = key.strip()
    kind = next((k for k, rx in OWNER_KINDS.items() if rx.match(key)), None)
    if not kind:
        raise ValueError("접두사 LRM- 또는 pr: 필요")
    nums = set()
    for tok in OWNER_KINDS[kind].sub("", key).split(","):
        m = re.fullmatch(r"\s*(\d+)(?:\.\.(\d+))?\s*", tok)
        if not m:
            raise ValueError(f"번호 형식 오류 '{tok.strip()}'")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if hi < lo or hi - lo >= RANGE_MAX:
            raise ValueError(f"범위 오류 '{tok.strip()}'")
        nums.update(str(n) for n in range(lo, hi + 1))
    return kind, nums


def load_owners(path, names=None):
    """담당 덮어쓰기 표(티켓 패턴<TAB>허브 slug<TAB>메모) → {"lrm": {번호: {slug}}, "pr": {번호: {slug}}}.
    읽기 실패·형식 오류·hubs.tsv 에 없는 slug 줄은 건너뛰고 stderr 에 경고(전체 실패 아님)"""
    out = {"lrm": {}, "pr": {}}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    except OSError as e:
        print(f"[ci] 담당 덮어쓰기 표 읽기 실패({path}): {e}", file=sys.stderr)
        return out
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        cols = ln.split("\t")
        try:
            if len(cols) < 2 or not cols[1].strip():
                raise ValueError("허브 slug 칸 없음")
            slug = cols[1].strip()
            if names is not None and slug not in names:
                raise ValueError(f"hubs.tsv 에 없는 slug '{slug}'")
            kind, nums = parse_owner_key(cols[0])
        except ValueError as e:
            print(f"[ci] 담당 덮어쓰기 표 {i}번째 줄 건너뜀: {e}", file=sys.stderr)
            continue
        for n in nums:
            out[kind].setdefault(n, set()).add(slug)
    return out


def owner_override(owners, pr_number, branch, title, names):
    """덮어쓰기 표 판정. 없으면 None, 있으면 (허브, 근거). PR 번호가 먼저, 그다음 제목·브랜치 티켓.
    표에서 서로 다른 허브로 갈리면 미배정"""
    if not owners:
        return None
    pr_hit = owners["pr"].get(str(pr_number or ""), set())
    if len(pr_hit) > 1:
        return UNASSIGNED, f"근거: 담당 덮어쓰기 표에서 PR#{pr_number} 가 여러 허브로 갈림(" + ", ".join(sorted(pr_hit)) + ")"
    if pr_hit:
        slug = next(iter(pr_hit))
        return names.get(slug, slug), f"근거: 담당 덮어쓰기 표 PR#{pr_number} → {slug}"
    pairs = [(f"LRM-{t}", sl) for t in sorted({t for t in TICKET.findall(f"{title} {branch}") if t in owners["lrm"]}, key=lambda t: (len(t), t))
             for sl in sorted(owners["lrm"][t])]
    slugs = {sl for _, sl in pairs}
    if len(slugs) == 1:
        slug = next(iter(slugs))
        return names.get(slug, slug), f"근거: 담당 덮어쓰기 표 {pairs[0][0]} → {slug}"
    if slugs:
        return UNASSIGNED, "근거: 담당 덮어쓰기 표에서 티켓이 여러 허브로 갈림(" + ", ".join(f"{t} → {sl}" for t, sl in pairs) + ")"
    return None


def assign_hub(branch, title, table, worktrees=None, names=None, common=(), qa_hub="", repo="", tmap=None, ws=None, owners=None, pr_number=""):
    """담당 허브와 근거 (허브, 근거 문구). 1순위 같은 원인의 PR 이 여럿이면 qa_hub(소유 허브보다 먼저),
    1.5순위 담당 덮어쓰기 표(owners: load_owners 결과. PR 번호 → 제목·브랜치 티켓),
    2순위 head 브랜치 작업 공간의 허브, 3순위 제목 티켓(tmap: 티켓→허브 집합)이 한 허브로 모일 때,
    4순위 표(정규식\t허브) 첫 일치. ws: 작업 공간 근거({티켓: {slug}}, workspace_tickets 결과)로, 있으면 근거 문구에 '작업 공간 [LRM-n] → slug' 를 씀. 티켓이 여러 허브로 갈리면 즉시 미배정, 모두 실패해도 미배정"""
    if common and qa_hub:
        return qa_hub, f"근거: 같은 원인이 PR {', '.join('#' + str(n) for n in common)} 에도 있음 → 테스트·QA 허브"
    forced = owner_override(owners, pr_number, branch, title, names or {})
    if forced:
        return forced
    hub = parent_hub(branch, worktrees or [], names or {}, repo)
    if hub:
        return hub, "근거: PR 브랜치의 작업 공간 허브"
    hit = title_hubs(title, tmap or {})
    if len({h for h, _ in hit}) == 1:
        slug = next((x for x in (ws or {}).get(hit[0][1][4:], ()) if (names or {}).get(x) == hit[0][0]), "")
        if slug:
            return hit[0][0], f"근거: 제목 티켓 {hit[0][1]} · 작업 공간 [{hit[0][1]}] → {slug}"
        return hit[0][0], f"근거: 제목 티켓 {hit[0][1]} → {hit[0][0]}"
    if hit:  # 티켓이 여러 허브로 갈리면 표로 넘기지 않고 메인 판단
        return UNASSIGNED, "근거: 제목 티켓이 여러 허브로 갈림(" + ", ".join(f"{t} → {h}" for h, t in hit) + ")"
    text = f"{branch} {title}"
    for pat, name in table:
        if re.search(pat, text):
            return name, "근거: 허브 표(ci-review-hubs.tsv) 일치"
    return UNASSIGNED, "근거: 작업 공간·표·공통 원인 모두 없음"


def load_table(path):
    rows = []
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            if ln.strip() and not ln.startswith("#") and "\t" in ln:
                pat, hub = ln.split("\t", 1)
                rows.append((pat.strip(), hub.strip()))
    except OSError:
        pass
    return rows


def write_summary(out_dir, pr, hub, finds):
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = finds[0]["commit"] or "unknown"
    path = out_dir / f"PR-{pr['number']}-{commit[:12]}.md"
    lines = [f"# PR #{pr['number']} AI 리뷰 지적 {len(finds)}건", "", f"- 제목: {pr.get('title', '')}", f"- 브랜치: {pr.get('headRefName', '')}",
             f"- 담당 허브: {hub}", f"- 대상 커밋: {commit}", "", "## 지적"]
    for i, f in enumerate(finds, 1):
        lines.append(f"{i}. [{f['kind']}·{f['role']}] {f['text']}")
    lines += ["", "## 처리 규칙", "- 지적마다 수정 커밋 + 일반 push. PR 에 답글(수정 = 커밋 해시, 반박 = 근거).", "- 강제 push·dev 병합 금지."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_fail(out_dir, pr, hub, sha, fails, slack_ts=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"PR-{pr['number']}-{sha[:12]}-fail.md"
    lines = [f"# PR #{pr['number']} CI 실패", "", f"- 제목: {pr.get('title', '')}", f"- 브랜치: {pr.get('headRefName', '')}",
             f"- 담당 허브: {hub}", f"- 커밋: {sha}", f"- Slack 알림 스레드: {slack_ts or '없음(전송 실패) — 새 메시지로 보냄'}", "", "## 실패 검사"]
    lines += [f"- {c['key']}: {c.get('cause') or '원인 줄 못 찾음'} ({c.get('link', '')})" for c in fails]
    thread = f" --thread {slack_ts}" if slack_ts else ""
    lines += ["", "## 처리 규칙",
              f"1. 고치기 전에 Slack 으로 설명: slack_notify.sh --kind warn{thread} — 첫 줄 주제, '• 무엇: 실패한 검사·원인 / • 왜: 코드 근거(파일:줄) / • 결과: 고칠 방법 1~2줄'",
              "2. 원인 수정 커밋 + 일반 push. 고치지 못하면 메인에 근거 보고",
              f"3. push 뒤 같은 스레드에 done 한 줄: slack_notify.sh --kind done{thread} \"수정 push 완료\"",
              "- 강제 push·dev 병합 금지"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main_handle(env_path):
    """main.env 의 MAIN_TERMINAL 값만 읽음(다른 줄은 보지 않음)"""
    try:
        for ln in Path(env_path).read_text(encoding="utf-8").splitlines():
            if ln.startswith("MAIN_TERMINAL="):
                return ln.split("=", 1)[1].strip() or None
    except OSError:
        pass
    return None


def send_main(orca, handle, text):
    try:
        p = subprocess.run([orca, "terminal", "send", "--terminal", handle, "--text", text, "--enter", "--wait-submit", "20", "--json"],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def error_text(pr, errs):
    roles = ", ".join(sorted({e["role"] for e in errs}))
    return f"[CI→메인] AI 리뷰 오류 {len(errs)}명 PR#{pr['number']} · 역할 {roles} · 허브 배정 없음, 리뷰 재실행 대상"


def relay_text(pr, hub, n, path):
    return f"[CI→메인] AI 리뷰 지적 {n}건 PR#{pr['number']} · 담당 허브 {hub} · 지적 요약 {path}"


def comments(gh, repo, n):
    raw = gh("api", f"repos/{repo}/issues/{n}/comments?per_page=100")
    return [c for c in json.loads(raw) if MARK.search(c.get("body", ""))]
