#!/usr/bin/env python3
"""스크럼 초안 재료 수집. gh PR·워크스페이스 커밋·허브 보고·HANDOFF·Slack 지시·허브별 진행 상세(상태 파일·열린 PR 검사·결정 기록)를 JSON 한 덩이로 출력함(읽기 전용).
Jira·Confluence 는 여기서 안 읽음: 초안을 쓰는 claude 가 Atlassian MCP 로 직접 읽음(인증이 MCP 쪽에 있음).
사용: scrum_collect.py --date YYYY-MM-DD [--since 'YYYY-MM-DD HH:MM'] [--out 파일]
  --date   초안 날짜(오늘). --since 를 안 주면 직전 평일 10:00 부터 모음(월요일은 금요일부터)
첨부: 오늘·어제 만든 이미지·영상을 허브 보고 첨부와 정리 기록 폴더에서 골라 attachments 에 담음(비밀 파일·50MB 초과 제외, 개수 상한).
종료 코드: 0 정상(개별 출처 실패는 JSON 의 errors 에 기록. 커밋은 워크트리별 실패도 기록: commits=전부 실패, commits_partial=일부 실패), 2 인자 오류
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 심볼릭 링크 설치에서도 같은 폴더의 hub_report 를 찾음
import hub_report  # 첨부 규칙(비밀 파일 차단·크기 상한)을 허브 보고와 같은 정의로 씀

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
HUB_STATUS_DIR = DIR / "hub-status"  # <허브>.json: tasks·branches·next·blockers
PROGRESS = DIR / "progress.json"  # items[]: bucket(doing|wait|block)·title·group
HUB_ITEM_GROUPS_SKIP = ("PR", "장부", "결정")  # progress.json 에서 허브별 진행이 아닌 묶음
DECISION_MAX = 5  # 허브당 최근 결정 기록 상한
FIELD_MAX = 160  # 상태 파일·결정 기록 한 칸 길이 상한(프롬프트 크기)
FAIL_NAMES_MAX = 3
REPORTS = DIR / "hub-reports.jsonl"  # 허브 보고 기록. files(원본 경로)·staged(접수 때 복사본)
MEDIA_DIR = Path(os.environ.get("LRM_SCRUM_MEDIA_DIR", Path.home() / "Documents/90_정리기록"))
MEDIA_EXTS = {"image": ("png", "jpg", "jpeg", "gif", "webp"), "video": ("mp4", "mov", "webm", "mkv")}
SOURCE_REPORT, SOURCE_FOLDER = "허브 보고", "정리 기록"
MEDIA_DAYS = 2  # 오늘·어제
MEDIA_MAX_FILES = 5  # 초안 스레드에 올릴 첨부 개수 상한(허브 보고 --file 상한과 같음)
MEDIA_WALK_DEPTH = 3
MEDIA_WALK_MAX = 20000  # 폴더 순회 파일 수 상한(큰 정리 폴더에서 오래 걸리지 않게)
CHECK_FAIL = ("FAILURE", "TIMED_OUT", "ERROR", "ACTION_REQUIRED", "STARTUP_FAILURE")
CHECK_OK = ("SUCCESS", "NEUTRAL", "SKIPPED")


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


def clip(v, n=FIELD_MAX):
    t = " ".join(str(v or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summarize_checks(rollup):
    """statusCheckRollup → {pass, fail, pending, failed[이름]}. 건너뜀·중립은 통과로 셈"""
    out = {"pass": 0, "fail": 0, "pending": 0, "failed": []}
    for c in rollup or []:
        verdict = (c.get("conclusion") or c.get("state") or "").upper()
        name = c.get("name") or c.get("context") or ""
        if verdict in CHECK_FAIL:
            out["fail"] += 1
            if len(out["failed"]) < FAIL_NAMES_MAX:
                out["failed"].append(clip(name, 60))
        elif verdict in CHECK_OK and (c.get("status") in (None, "COMPLETED")):
            out["pass"] += 1
        else:
            out["pending"] += 1
    return out


def collect_open_prs():
    """내 열린 PR 번호·상태·검사 요약. 허브 매칭용 branch 포함"""
    out = run([GH, "pr", "list", "-R", REPO, "--author", "@me", "--state", "open", "--limit", "50",
               "--json", "number,title,isDraft,headRefName,baseRefName,url,reviewDecision,statusCheckRollup"])
    rows = []
    for p in json.loads(out or "[]"):
        rows.append({"number": p["number"], "title": clip(p.get("title"), 90), "url": p.get("url", ""),
                     "draft": bool(p.get("isDraft")), "branch": p.get("headRefName", ""), "base": p.get("baseRefName", ""),
                     "review": p.get("reviewDecision") or "", "checks": summarize_checks(p.get("statusCheckRollup"))})
    return rows


def collect_decisions(since):
    """허브별 decisions.tsv 의 since 이후 기록(최근 DECISION_MAX 개). 열 순서: 시각·무엇·왜·근거·결과·되돌리기"""
    found = {}
    for p in sorted(WS_ROOT.glob("coord-*-hub/decisions.tsv")) if WS_ROOT.is_dir() else []:
        hub = p.parent.name[len("coord-"):-len("-hub")]
        try:
            lines = p.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        rows = []
        for ln in lines:
            c = (ln.split("\t") + [""] * 6)[:6]
            if c[0] >= since.strftime("%Y-%m-%d %H:%M:%S"):
                rows.append({"at": c[0], "what": clip(c[1]), "why": clip(c[2]), "result": clip(c[4])})
        if rows:
            found[hub] = rows[-DECISION_MAX:]
    return found


def collect_hub_progress(since):
    """허브별 진행 상세: 상태 파일(작업·다음·막힘) + 대기·막힘 항목 + 브랜치가 맞는 열린 PR + 최근 결정.
    출처 일부가 없어도 나머지로 만들고, 전부 없으면 빈 목록. PR 조회 실패는 예외로 올려 errors 에 기록됨"""
    prs = collect_open_prs()
    items = (read_json(PROGRESS) or {}).get("items") or []
    decisions = collect_decisions(since)
    hubs = {}
    for p in sorted(HUB_STATUS_DIR.glob("*.json")) if HUB_STATUS_DIR.is_dir() else []:
        d = read_json(p)
        if not isinstance(d, dict) or not d.get("slug"):
            continue
        hubs[d["slug"]] = {"hub": d["slug"], "updated": d.get("updatedAt", ""),
                           "tasks": [clip(t) for t in d.get("tasks") or []][:6], "next": clip(d.get("next")),
                           "blockers": [clip(b) for b in d.get("blockers") or []][:6],
                           "waiting": [], "prs": [], "decisions": decisions.get(d["slug"], [])}
        branches = set(d.get("branches") or [])
        hubs[d["slug"]]["prs"] = [p for p in prs if p["branch"] in branches]
    for it in items:
        g = it.get("group")
        if g in hubs and it.get("bucket") == "wait":
            hubs[g]["waiting"].append(clip(it.get("title", "").split(": ", 1)[-1]))
    matched = {p["number"] for h in hubs.values() for p in h["prs"]}
    return {"hubs": list(hubs.values()), "other_prs": [p for p in prs if p["number"] not in matched]}


def media_kind(path):
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return next((k for k, exts in MEDIA_EXTS.items() if ext in exts), "")


def report_media(cutoff):
    """허브 보고 첨부(files) 중 cutoff 이후 보고의 이미지·영상. 원본이 없으면 접수 때 복사본(staged) 사용"""
    for r in jsonl(REPORTS):
        if num(r.get("at")) < cutoff or not isinstance(r.get("files"), list):
            continue
        staged = r.get("staged") if isinstance(r.get("staged"), dict) else {}
        for f in r["files"]:
            if isinstance(f, str) and media_kind(f):
                yield (f if os.path.isfile(f) else staged.get(f, f)), SOURCE_REPORT


def folder_media(cutoff):
    """정리 기록 폴더(깊이 MEDIA_WALK_DEPTH)에서 cutoff 이후 수정된 이미지·영상"""
    seen = 0
    root_depth = len(MEDIA_DIR.parts)
    for base, dirs, names in os.walk(MEDIA_DIR):
        if len(Path(base).parts) - root_depth >= MEDIA_WALK_DEPTH:
            dirs[:] = []
        for n in names:
            seen += 1
            if seen > MEDIA_WALK_MAX:
                return
            full = os.path.join(base, n)
            try:
                if media_kind(n) and os.stat(full).st_mtime >= cutoff:
                    yield full, SOURCE_FOLDER
            except OSError:
                continue


def collect_attachments(date):
    """{files: [{path,name,size,kind,source}], skipped: [{name,why}]}. 최신순 MEDIA_MAX_FILES 개.
    비밀 파일·크기 초과·없는 파일은 hub_report.file_problem 규칙으로 걸러 skipped 에 사유만 남김(경로·내용은 안 담음)"""
    cutoff = dt.datetime.combine(date - dt.timedelta(days=MEDIA_DAYS - 1), dt.time.min).astimezone().timestamp()
    cands, skipped, seen = [], [], set()
    for path, source in list(report_media(cutoff)) + (list(folder_media(cutoff)) if MEDIA_DIR.is_dir() else []):
        path = os.path.abspath(os.path.expanduser(path))
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        if why := hub_report.file_problem(path):
            skipped.append({"name": os.path.basename(path), "why": why.split("(")[0]})
            continue
        st = os.stat(path)
        cands.append({"path": path, "name": os.path.basename(path), "size": st.st_size, "kind": media_kind(path),
                      "source": source, "mtime": st.st_mtime})
    cands.sort(key=lambda c: (c["source"] != SOURCE_REPORT, -c["mtime"]))  # 허브 보고 첨부가 먼저(팀 일과 가까움), 그 안에서 최신순
    uniq, keys = [], set()
    for c in cands:  # 같은 이름·크기(복사본)는 하나만
        if (c["name"], c["size"]) not in keys:
            keys.add((c["name"], c["size"]))
            uniq.append(c)
    for c in uniq:
        del c["mtime"]
    skipped = skipped[:10]
    if len(uniq) > MEDIA_MAX_FILES:
        skipped.append({"name": "", "why": "개수 상한 초과 %d개" % (len(uniq) - MEDIA_MAX_FILES)})
    return {"files": uniq[:MEDIA_MAX_FILES], "skipped": skipped}


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
    for key, fn, arg in (("attachments", collect_attachments, date), ("prs", collect_prs, since),
                         ("hub_reports", collect_hub_reports, since), ("handoffs", collect_handoffs, since),
                         ("hub_progress", collect_hub_progress, since),
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
