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

MARK = re.compile(r"<!-- lrm-ai-review:(\w+) -->")
SECTION = re.compile(r"^###\s*아쉬운 점.*?$(.*?)(?=^###|^\*\*반드시|\Z)", re.M | re.S)
ITEM = re.compile(r"\*\*무엇:\*\*\s*(.+?)(?=\n\s*\*\*무엇:\*\*|\Z)", re.S)
MUST = re.compile(r"\*\*반드시 수정해야 하는 점:\*\*\s*(.+?)(?=\n\s*\*\*팀 규칙|\n\[실행 기록|\Z)", re.S)
COMMIT = re.compile(r"`([0-9a-f]{7,40})`")
TICKET = re.compile(r"LRM-(\d+)")
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
    out = []
    sec = SECTION.search(body)
    if sec:
        for it in ITEM.finditer(sec.group(1)):
            out.append({"role": role, "kind": "권장", "text": " ".join(it.group(1).split())[:600], "commit": commit})
    must = MUST.search(body)
    if must and not must.group(1).strip().startswith("없음"):
        out.append({"role": role, "kind": "필수", "text": " ".join(must.group(1).split())[:600], "commit": commit})
    for f in out:
        f["id"] = hashlib.sha1(f"{comment.get('id')}|{f['kind']}|{f['text']}".encode()).hexdigest()[:16]
    return out


def hub_for(branch, title, table):
    """담당 허브. 표(정규식\t허브) 위에서부터 첫 일치, 없으면 '미배정'"""
    text = f"{branch} {title}"
    for pat, hub in table:
        if re.search(pat, text):
            return hub
    return "미배정(메인 판단)"


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


def relay_text(pr, hub, n, path):
    return f"[CI→메인] AI 리뷰 지적 {n}건 PR#{pr['number']} · 담당 허브 {hub} · 지적 요약 {path}"


def comments(gh, repo, n):
    raw = gh("api", f"repos/{repo}/issues/{n}/comments?per_page=100")
    return [c for c in json.loads(raw) if MARK.search(c.get("body", ""))]
