"""CI AI 리뷰 지적 수집·메인 전달. ci_watch.py 가 불러 씀.

리뷰 코멘트: github-actions 봇의 이슈 코멘트 '<!-- lrm-ai-review:역할 -->'. 지적 = '아쉬운 점' 의 **무엇:** 항목 + '반드시 수정' 내용.
처리한 지적은 해시로 상태에 남겨 다시 보내지 않음. 메인 경유 원칙: 허브에 직접 보내지 않고 메인 터미널에만 알림.
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

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


GENERIC = re.compile(r"(?i)exit(ed)?( with)? (code|status) \d+|^(##\[error\])?\s*(error)?\.?\s*$")  # 종료 코드만 있는 요약은 원인 식별자가 아님


def specific(cause):
    """구체 원인 식별자(실패 시험 이름·assert 문구·오류 첫 줄)인지. 빈 값·일반 종료 요약은 아님"""
    return bool(cause) and not GENERIC.search(cause)


def common_cause(causes, own):
    """다른 열린 PR 에도 같은 검사가 같은 구체 원인으로 실패했는지. causes: {PR 번호: {head, fails:[{key, cause}], hub}}, own: 이 PR 번호.
    구체 식별자가 없으면 공통으로 보지 않음. 번호 순 정렬이라 처리 순서와 무관"""
    def ids(e):
        return {(c["key"], c.get("cause", "")) for c in (e or {}).get("fails", []) if specific(c.get("cause", ""))}

    mine = ids(causes.get(own))
    return sorted((n for n, e in causes.items() if n != own and mine & ids(e)), key=lambda n: (len(n), n))


def prune_causes(causes, open_heads):
    """열린 PR 의 현재 head 에 묶인 원인만 남김. open_heads: {PR 번호: head}"""
    for n in [n for n, e in causes.items() if open_heads.get(n) != e.get("head")]:
        del causes[n]


def hub_for(branch, title, table):
    """표만 쓰는 담당 허브. 표(정규식\t허브) 위에서부터 첫 일치, 없으면 '미배정'"""
    return assign_hub(branch, title, table)[0]


def assign_hub(branch, title, table, worktrees=None, names=None, common=(), qa_hub="", repo=""):
    """담당 허브와 근거 (허브, 근거 문구). 1순위 같은 원인의 PR 이 여럿이면 qa_hub(소유 허브보다 먼저),
    2순위 head 브랜치 작업 공간의 허브, 3순위 표(정규식\t허브) 첫 일치, 모두 실패하면 미배정"""
    if common and qa_hub:
        return qa_hub, f"근거: 같은 원인이 PR {', '.join('#' + str(n) for n in common)} 에도 있음 → 테스트·QA 허브"
    hub = parent_hub(branch, worktrees or [], names or {}, repo)
    if hub:
        return hub, "근거: PR 브랜치의 작업 공간 허브"
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
