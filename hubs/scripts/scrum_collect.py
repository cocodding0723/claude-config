#!/usr/bin/env python3
"""스크럼 초안 재료 수집. gh PR·워크스페이스 커밋·허브 보고·HANDOFF·Slack 지시를 JSON 한 덩이로 출력함(읽기 전용).
Jira·Confluence 는 여기서 안 읽음: 초안을 쓰는 claude 가 Atlassian MCP 로 직접 읽음(인증이 MCP 쪽에 있음).
사용: scrum_collect.py --date YYYY-MM-DD [--since 'YYYY-MM-DD HH:MM'] [--out 파일]
  --date   초안 날짜(오늘). --since 를 안 주면 직전 평일 10:00 부터 모음(월요일은 금요일부터)
종료 코드: 0 정상(개별 출처 실패는 JSON 의 errors 에 기록. 커밋은 워크트리별 실패도 기록: commits=전부 실패, commits_partial=일부 실패), 2 인자 오류
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

DIR = Path(os.environ.get("LRM_HUB_DIR", Path.home() / ".config/lrm-hubs"))
REPO = os.environ.get("LRM_CI_REPO", "Basakansoft/LoveRevolutionMaker")
GH = os.environ.get("LRM_GH", "gh")
GIT = os.environ.get("LRM_GIT", "git")
WS_ROOT = Path(os.environ.get("LRM_SCRUM_WS_ROOT", Path.home() / "orca/workspaces/LoveRevolutionMaker"))
HOLIDAYS = DIR / "scrum-holidays.txt"  # 한 줄에 YYYY-MM-DD, # 주석. 없으면 공휴일 없음으로 침
CUTOFF_HOUR = 10  # 하루의 경계 시각(스크럼 시각)
HANDOFF_MAX_CHARS = 2000
HANDOFF_MAX_FILES = 3
INBOX_HOURS = 24
CMD_TIMEOUT = 60
COMMIT_MAX = 150  # 프롬프트 크기 상한. 최신순으로 자름


def holidays():
    try:
        lines = HOLIDAYS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {ln.split("#")[0].strip() for ln in lines if ln.split("#")[0].strip()}


def is_workday(d, hol=None):
    return d.weekday() < 5 and d.isoformat() not in (holidays() if hol is None else hol)


def prev_workday(d):
    """d 직전 평일(주말·공휴일 건너뜀)"""
    hol = holidays()
    p = d - dt.timedelta(days=1)
    while not is_workday(p, hol):
        p -= dt.timedelta(days=1)
    return p


def run(cmd, cwd=None):
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=CMD_TIMEOUT, cwd=cwd)
    if out.returncode != 0:
        raise RuntimeError("%s 종료 코드 %d: %s" % (cmd[0], out.returncode, out.stderr.strip()[:200]))
    return out.stdout


def collect_prs(since):
    out = run([GH, "pr", "list", "-R", REPO, "--author", "@me", "--state", "all",
               "--search", "updated:>=%s" % since.date().isoformat(), "--limit", "50",
               "--json", "number,title,state,mergedAt,isDraft,headRefName,url,updatedAt"])
    return [p for p in json.loads(out or "[]") if p.get("updatedAt", "") >= since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")]


def collect_commits(since):
    """(커밋 목록, 실패한 워크트리별 '<폴더>: 사유' 목록, 조사한 워크트리 수). 한 워크트리가 실패해도 나머지는 계속 모음"""
    author = run([GIT, "config", "--global", "user.name"]).strip()
    seen, rows, failed, scanned = set(), [], [], 0
    for wt in sorted(WS_ROOT.glob("*")) if WS_ROOT.is_dir() else []:
        if not (wt / ".git").exists():
            continue
        scanned += 1
        try:
            log = run([GIT, "-C", str(wt), "log", "--since=%s" % since.isoformat(), "--author=%s" % author,
                       "--format=%h\t%cI\t%s"])
            branch = run([GIT, "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            failed.append("%s: %s" % (wt.name, e))
            continue
        for ln in log.splitlines():
            h, at, subj = (ln.split("\t", 2) + ["", ""])[:3]
            if h and h not in seen:
                seen.add(h)
                rows.append({"hash": h, "at": at, "subject": subj, "branch": branch})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:COMMIT_MAX], failed, scanned


def jsonl(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for ln in lines:
        try:
            rows.append(json.loads(ln))
        except ValueError:
            pass  # 깨진 줄은 건너뜀
    return rows


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def collect_hub_reports(since):
    ts = since.timestamp()
    return [{"title": r.get("title", ""), "kind": r.get("form_kind") or r.get("kind", ""), "tag": r.get("tag", "")}
            for r in jsonl(DIR / "slack-sent.jsonl") if num(r.get("at")) >= ts and r.get("tag") != "scrum"]


def collect_handoffs(since):
    ts = since.timestamp()
    found = []
    for p in WS_ROOT.parent.glob("*/*/HANDOFF.md") if WS_ROOT.parent.is_dir() else []:
        if p.stat().st_mtime >= ts:
            found.append((p.stat().st_mtime, p))
    found.sort(reverse=True)
    return [{"path": str(p), "text": p.read_text(encoding="utf-8", errors="replace")[:HANDOFF_MAX_CHARS]}
            for _, p in found[:HANDOFF_MAX_FILES]]


def collect_instructions(now):
    ts = now.timestamp() - INBOX_HOURS * 3600
    return [r.get("text", "") for r in jsonl(DIR / "slack-inbox.jsonl")
            if num(r.get("received_at")) >= ts and r.get("text")]


def collect(date, since):
    data = {"date": date.isoformat(), "since": since.isoformat(), "errors": []}
    now = dt.datetime.combine(date, dt.time(CUTOFF_HOUR)).astimezone()
    try:
        data["commits"], failed, scanned = collect_commits(since)
        if failed and len(failed) == scanned:  # 전부 실패: 'commits' 출처 자체가 빠짐(필수 출처 판정 대상)
            data["errors"].append("commits: 워크트리 %d개 전부 git log 실패 — %s" % (scanned, "; ".join(failed)[:300]))
        elif failed:  # 일부만 실패: 모인 커밋은 쓰되 누락 가능성을 알림
            data["errors"].append("commits_partial: 워크트리 %d/%d개 git log 실패 — %s" % (len(failed), scanned, "; ".join(failed)[:300]))
    except (RuntimeError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as e:
        data["commits"] = []
        data["errors"].append("commits: %s" % e)
    for key, fn, arg in (("prs", collect_prs, since),
                         ("hub_reports", collect_hub_reports, since), ("handoffs", collect_handoffs, since),
                         ("instructions", collect_instructions, now)):
        try:
            data[key] = fn(arg)
        except (RuntimeError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as e:
            data[key] = []
            data["errors"].append("%s: %s" % (key, e))
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="초안 날짜 YYYY-MM-DD")
    ap.add_argument("--since", help="수집 시작 'YYYY-MM-DD HH:MM'(기본: 직전 평일 10:00)")
    ap.add_argument("--out", help="JSON 저장 파일(기본: 표준 출력)")
    a = ap.parse_args()
    try:
        date = dt.date.fromisoformat(a.date)
        since = (dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M") if a.since
                 else dt.datetime.combine(prev_workday(date), dt.time(CUTOFF_HOUR))).astimezone()
    except ValueError as e:
        print("날짜 형식 오류: %s — YYYY-MM-DD, 'YYYY-MM-DD HH:MM' 로 지정" % e, file=sys.stderr)
        return 2
    text = json.dumps(collect(date, since), ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
