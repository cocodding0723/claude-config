#!/usr/bin/env python3
"""옆 패널 자동 칸 수집기. 진입은 panel_collect.sh.

읽기 전용 원료(orca terminal list·worker-list·task-list, Slack 기록 파일, slack-push.log, gh)를 모아
panel-auto.json 한 파일만 원자 교체로 씀. 메인이 손으로 쓰는 panel.json 은 열지도 쓰지도 않음.
화면(preview)·메시지 본문·토큰은 파일·로그에 남기지 않음. slack-bot.env·main.env 는 읽지 않음(Slack API 호출 없음).
"""
import calendar
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
SELF_DIR = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
OUT = os.path.join(DIR, "panel-auto.json")
LOCK = os.path.join(DIR, ".panel-collect.lock")
LOG = os.path.join(DIR, "panel-collect.log")
GH_STAMP = os.path.join(DIR, ".panel-gh.stamp")
DU_STAMP = os.path.join(DIR, ".panel-du.stamp")
CONF = os.path.join(DIR, "panel.env")
SENT, INBOX, DONE = (os.path.join(DIR, n) for n in ("slack-sent.jsonl", "slack-inbox.jsonl", "slack-done.txt"))
PUSH_LOG = os.path.join(DIR, "slack-push.log")

VERSION = 1
LOCK_STALE = 600                 # 10분 넘은 잠금은 죽은 실행의 잔재로 보고 회수(주기 90초의 6배 이상)
ORCA_TIMEOUT, GH_TIMEOUT = 15, 20
TITLE_MAX = 40                   # 패널에 싣는 제목 길이. 우리가 보낸 카드·작업 제목이며 사용자 원문 아님
PUSH_LINES, PUSH_TAIL = 5, 64 * 1024
RECENT_RUNS = 3                  # 최근 기록 제목을 얻으려고 task-list 를 부르는 Run 수 상한
LOG_MAX = 1 << 20
CONF_KEYS = ("PANEL_GH_REPOS", "PANEL_GH_INTERVAL", "PANEL_HUB_ROOT", "PANEL_RECENT", "PANEL_DISK_MIN_GB", "PANEL_WS_DIR", "PANEL_DU_INTERVAL")
# 쉬는 자원 칸. 목록·숫자만 만들고 정리(삭제·종료)는 사람이 함
IDLE_LIST_MAX = 20
ENDED = ("succeeded", "failed", "stopped")
LEFT_TERMINAL = frozenset({"active", "retained", "reclaimable", "release_pending", "release_unknown"})  # 종료 뒤에도 터미널이 남은 상태
GIT_TIMEOUT, DF_TIMEOUT, DU_TIMEOUT = 5, 10, 120
WORKTREE_LIMIT = "1000"          # 기본 200개에서 잘림(실측 226개)

# 허브 화면 분류. preview 는 Orca 가 주는 화면 끝 300자 안팎이라 위쪽 질문은 못 봄
SCREEN_TAIL = 8
BUSY_MARK = "esc to interrupt"
# Claude Code 작업 표시 줄 = 회전 기호 + '…'(예: ✻ Thinking…). 끝난 턴 요약 줄도 같은 기호라 '…' 까지 봄
SPINNER = frozenset("·✢✳✶✻✽")
QUIET_IDLE_MIN = 2               # 작업 표식·입력 칸이 안 보여도 이만큼 출력이 없으면 입력 대기로 봄(preview 가 입력 칸을 자르는 경우가 많음)
# patrol.sh 의 막힘 키워드. 'allow'·'승인' 은 평범한 출력에도 나와 오탐이 잦음 → 화면이 작업 중·입력 대기로 판별되지 않을 때만 씀
PATROL_KEYS = ("do you want", "allow", "승인", "(y/n)", "❯ 1.")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
PUSH_RE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d):\d\d push ([a-z_]+)\b(.*)$")
REASON_RE = re.compile(r"\breason=([a-z_]+)")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CI_FAIL = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"})

try:  # 입력 칸·대화상자 판별은 slack_push.py 의 것을 그대로 씀(같은 화면 규칙 한 곳). 없으면 대기 판별만 포기
    sys.path.insert(0, SELF_DIR)
    from slack_push import screen_state  # noqa: E402
except Exception:  # noqa: BLE001 — 가져오기 실패가 수집 전체를 막지 않게 함
    screen_state = None


class SourceError(Exception):
    """원료 하나의 실패. code 는 오류 종류만(timeout·missing·exit·parse·io·auth)"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def usage():
    return "\n".join([
        "사용: panel_collect.sh --once | --print | --help",
        "  --once   1회 수집해 panel-auto.json 을 원자 교체(launchd 가 90초마다 호출)",
        "  --print  1회 수집해 표준출력으로만 냄. 파일·로그·gh 간격 표식은 쓰지 않음",
        "원료: orca terminal list(허브 상태 분류만 저장, 화면 본문 없음), slack-sent/inbox/done(결정 대기),",
        "      slack-push.log(최근 결과 코드), orca orchestration worker-list·task-list(최근 종료 작업 제목·상태·시각),",
        "      gh pr list(PANEL_GH_REPOS 가 있을 때만, PANEL_GH_INTERVAL 초마다)",
        "설정: 환경 변수 또는 작업 폴더의 panel.env(KEY=값, 아래 키만 읽음. 양식 hubs/panel.env.example)",
        "  PANEL_GH_REPOS 공백·쉼표로 구분한 owner/repo 목록(비면 gh 건너뜀), PANEL_GH_INTERVAL gh 간격 초(기본 300, 60~86400)",
        "  PANEL_HUB_ROOT 허브 폴더 상위 경로(비면 폴더 이름 coord-* 만 봄), PANEL_RECENT 최근 기록 건수(기본 5, 1~20)",
        "  PANEL_DISK_MIN_GB 홈 볼륨 여유 경고 기준(기본 50), PANEL_WS_DIR 크기를 잴 워크스페이스 폴더(기본 ~/orca/workspaces),",
        "  PANEL_DU_INTERVAL 폴더 크기 측정 간격 초(기본 1800, 300~86400)",
        "쉬는 자원(idle): 워커 상태별 건수·종료 뒤 남은 터미널, 터미널 없는 워크스페이스(최대 20개), 디스크 여유. 목록만 만들고 정리하지 않음",
        "환경 변수: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs), PANEL_ORCA·PANEL_GH·PANEL_GIT·PANEL_DF·PANEL_DU 실행 파일(시험용)",
        "출력: 작업 폴더의 panel-auto.json. 원료마다 ok·err·at(마지막 성공)·tried·stale·value. 실패한 원료는 이전 값 유지 + stale:true",
        "로그: 작업 폴더의 panel-collect.log(원료별 결과 코드만)",
        "종료 코드: 0 정상(일부 원료 실패 포함, 잠금으로 건너뜀 포함), 2 인자·설정 오류",
    ])


# ---- 설정 -------------------------------------------------------------------------------------
def load_conf():
    """환경 변수가 우선. panel.env 는 정해진 키만 KEY=값 으로 읽고 실행하지 않음"""
    conf = {}
    try:
        with open(CONF, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k in CONF_KEYS:
                    conf[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in CONF_KEYS:
        if k in os.environ:
            conf[k] = os.environ[k]
    return conf


def conf_int(conf, key, default, lo, hi):
    raw = (conf.get(key) or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not lo <= int(raw) <= hi:
        raise ValueError(f"{key} 는 {lo}~{hi} 정수여야 함")
    return int(raw)


def gh_repos(conf):
    repos = [r for r in re.split(r"[\s,]+", conf.get("PANEL_GH_REPOS") or "") if r]
    bad = [r for r in repos if not REPO_RE.match(r)]
    if bad:
        raise ValueError(f"PANEL_GH_REPOS 형식 오류 {len(bad)}건(owner/repo 여야 함)")
    return repos


# ---- 외부 명령 --------------------------------------------------------------------------------
def run_json(argv, timeout):
    """명령 JSON 출력. 실패는 SourceError(종류만). 표준오류·출력 원문은 어디에도 남기지 않음"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise SourceError("timeout")
    except OSError:
        raise SourceError("missing")
    if p.returncode != 0:
        low = (p.stderr or "").lower()
        raise SourceError("auth" if "auth login" in low or "not logged" in low or "authentication" in low else "exit")
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise SourceError("parse")


def orca(*args):
    d = run_json([os.environ.get("PANEL_ORCA", "orca"), *args, "--json"], ORCA_TIMEOUT)
    if not isinstance(d, dict) or not d.get("ok") or not isinstance(d.get("result"), dict):
        raise SourceError("exit")
    return d["result"]


class Fetch:
    """한 회차 안에서 같은 orca 조회는 한 번만(허브·쉬는 자원이 terminal list 를, 기록·쉬는 자원이 worker-list 를 같이 씀). 실패도 기억"""

    def __init__(self):
        self.memo = {}

    def __call__(self, *args):
        if args not in self.memo:
            try:
                self.memo[args] = (True, orca(*args))
            except SourceError as e:
                self.memo[args] = (False, e)
        ok, v = self.memo[args]
        if not ok:
            raise v
        return v


def run_text(argv, timeout):
    """명령 표준출력 문자열. 실패는 SourceError(종류만)"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise SourceError("timeout")
    except OSError:
        raise SourceError("missing")
    if p.returncode != 0:
        raise SourceError("exit")
    return p.stdout


# ---- 원료: 허브 상태 ---------------------------------------------------------------------------
def classify(preview, quiet_min=None):
    """화면 끝으로 본 허브 상태: busy(작업 중) | approval(승인창) | idle(입력 대기) | unknown(확인불가).
    순서: 대화상자 → 작업 표식 → 빈 입력 칸 → patrol 키워드 → 출력 멈춤 시간"""
    lines = [ln for ln in ANSI_RE.sub("", str(preview or "")).replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return "unknown"
    tail_lines = lines[-SCREEN_TAIL:]
    tail = "\n".join(tail_lines).lower()
    st = screen_state(preview) if screen_state else None
    if st == "dialog":
        return "approval"
    if BUSY_MARK in tail or any(ln.strip()[0] in SPINNER and "…" in ln for ln in tail_lines):
        return "busy"
    if st in ("idle", "draft"):
        return "idle"
    if any(k in tail for k in PATROL_KEYS):
        return "approval"
    if quiet_min is not None and quiet_min >= QUIET_IDLE_MIN:
        return "idle"
    return "unknown"


def hub_name(base):
    return re.sub(r"(-hub)+$", "", re.sub(r"^(coord-)+", "", base)) or base


def terminals(get):
    terms = get("terminal", "list").get("terminals")
    if not isinstance(terms, list):
        raise SourceError("parse")
    return [t for t in terms if isinstance(t, dict)]


def collect_hubs(conf, now, get):
    """허브 식별은 patrol.sh 규칙: 작업 폴더 이름 coord-* + 에이전트 붙은 터미널(+ PANEL_HUB_ROOT 가 있으면 그 아래)"""
    terms = terminals(get)
    if not isinstance(terms, list):
        raise SourceError("parse")
    root = (conf.get("PANEL_HUB_ROOT") or "").rstrip("/")
    rows = []
    for t in terms:
        if not isinstance(t, dict):
            continue
        path = str(t.get("worktreePath") or "")
        base = os.path.basename(path.rstrip("/"))
        if not base.startswith("coord-") or not t.get("agentIdentity"):
            continue
        if root and not path.startswith(root + "/"):
            continue
        last = t.get("lastOutputAt")
        quiet = int((now * 1000 - last) // 60000) if isinstance(last, (int, float)) and last > 0 else None
        connected = bool(t.get("connected")) and not t.get("orphaned")
        rows.append({
            "hub": base,
            "name": hub_name(base),
            "agent": str(t.get("agentIdentity"))[:20],
            "state": classify(t.get("preview"), quiet) if connected else "unknown",
            "quietMin": max(quiet, 0) if quiet is not None else None,
            "connected": connected,
        })
    seen = {}
    for r in rows:
        seen[r["hub"]] = seen.get(r["hub"], 0) + 1
    for r in rows:
        r["dup"] = seen[r["hub"]] > 1
    rows.sort(key=lambda r: (r["name"], r["quietMin"] if r["quietMin"] is not None else 1 << 30))
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("busy", "approval", "idle", "unknown")}
    return {"rows": rows, "counts": counts}


# ---- 원료: 결정 대기 ---------------------------------------------------------------------------
def jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict):
                        out.append(d)
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError):
        raise SourceError("io")
    return out


def collect_pending(now):
    """slack_pending.sh 와 같은 판정(시험이 두 규칙을 대조): 제목에 '결정'이 든 최상위 보낸 메시지 중
    반응(reaction parent_ts)도 slack-done.txt 기록도 없는 것. 같은 제목은 한 건"""
    sent, inbox = jsonl(SENT), jsonl(INBOX)
    done = {d.get("parent_ts") for d in inbox if d.get("type") == "reaction"}
    try:
        with open(DONE, encoding="utf-8") as f:
            done |= set(f.read().split())
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError):
        raise SourceError("io")
    items, seen = [], set()
    for s in sent:
        title = str(s.get("title", ""))
        if not s.get("ts") or s.get("thread_ts") or "결정" not in title or s["ts"] in done or title in seen:
            continue
        seen.add(title)
        at = s.get("at")
        items.append({"title": title.split("\n")[0][:TITLE_MAX],
                      "ageMin": int((now - at) // 60) if isinstance(at, (int, float)) else None})
    return {"count": len(items), "items": items}


# ---- 원료: 최근 기록 ---------------------------------------------------------------------------
def collect_push():
    """slack-push.log 끝 줄의 시각·결과 코드·사유 코드만"""
    try:
        with open(PUSH_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - PUSH_TAIL))
            text = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return {"lines": []}
    except OSError:
        raise SourceError("io")
    out = []
    for line in text.split("\n"):
        m = PUSH_RE.match(line.strip())
        if m:
            r = REASON_RE.search(m.group(4))
            out.append({"at": f"{m.group(1)[5:]} {m.group(2)}", "event": m.group(3), "reason": r.group(1) if r else None})
    return {"lines": out[-PUSH_LINES:]}


def utc_epoch(text):
    try:
        return calendar.timegm(time.strptime(str(text), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def workers_raw(get):
    ws = get("orchestration", "worker-list", "--full").get("workers")
    if not isinstance(ws, list):
        raise SourceError("parse")
    return [w for w in ws if isinstance(w, dict)]


def task_titles(get, runs):
    """Run 별 작업 제목. 실패한 Run 은 제목만 빠짐(상태·시각은 유지)"""
    titles = {}
    for run in list(dict.fromkeys(r for r in runs if r))[:RECENT_RUNS]:
        try:
            for t in get("orchestration", "task-list", "--run", run).get("tasks") or []:
                if isinstance(t, dict) and t.get("id"):
                    titles[t["id"]] = str(t.get("title") or "").split("\n")[0][:TITLE_MAX]
        except SourceError:
            continue
    return titles


def collect_workers(conf, get):
    """최근 종료된 워커(완료 보고)의 작업 제목·상태·시각. worker-list 는 최신이 앞. 제목은 task-list 에서만 얻음"""
    limit = conf_int(conf, "PANEL_RECENT", 5, 1, 20)
    ws = workers_raw(get)
    live, recent = 0, []
    for w in ws:
        proj = w.get("projection") if isinstance(w.get("projection"), dict) else {}
        verdict = (proj.get("liveness") or {}).get("verdict") if isinstance(proj.get("liveness"), dict) else None
        if verdict == "live":
            live += 1
        state = str(w.get("workerState") or "")
        if state in ("succeeded", "failed", "stopped") and len(recent) < limit:
            res = w.get("resource") if isinstance(w.get("resource"), dict) else {}
            recent.append({"task": str(w.get("taskId") or ""), "run": str(w.get("runId") or ""),
                           "status": state, "at": utc_epoch(res.get("releaseCompletedAt"))})
    titles = task_titles(get, [r["run"] for r in recent])
    items = [{"title": titles.get(r["task"]), "status": r["status"], "at": r["at"]} for r in recent]
    return {"live": live, "recent": items}


# ---- 원료: 쉬는 자원 --------------------------------------------------------------------------
def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def idle_workers(get):
    ws = workers_raw(get)
    counts = {"live": 0, "succeeded": 0, "failed": 0, "stopped": 0}
    left = []
    for w in ws:
        proj = w.get("projection") if isinstance(w.get("projection"), dict) else {}
        live = proj.get("liveness") if isinstance(proj.get("liveness"), dict) else {}
        if live.get("verdict") == "live":
            counts["live"] += 1
        state = str(w.get("workerState") or "")
        if state in ENDED:
            counts[state] += 1
            if str(w.get("terminalState") or "") in LEFT_TERMINAL:
                left.append(w)
    titles = task_titles(get, [str(w.get("runId") or "") for w in left[:IDLE_LIST_MAX]])
    items = [{"dispatch": str(w.get("dispatchId") or "")[:24], "title": titles.get(str(w.get("taskId") or "")),
              "status": str(w.get("workerState")), "terminal": str(w.get("terminalState"))} for w in left[:IDLE_LIST_MAX]]
    return {**counts, "leftover": len(left), "items": items, "more": max(0, len(left) - IDLE_LIST_MAX)}


def merged(path, base):
    """브랜치 HEAD 가 기준 ref 에 들어갔는지. 기준이 없거나 git 이 답하지 못하면 None"""
    if not base:
        return None
    try:
        p = subprocess.run([os.environ.get("PANEL_GIT", "git"), "-C", path, "merge-base", "--is-ancestor", "HEAD", base],
                           capture_output=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return {0: True, 1: False}.get(p.returncode)


def last_active(w):
    """마지막 활동 시각(ms). 0·없음이면 만든 시각, 그것도 없으면 None"""
    for k in ("lastActivityAt", "createdAt"):
        v = w.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return v
    return None


def idle_workspaces(get, now):
    """Orca 워크스페이스 중 열린 터미널이 0개인 것. 오래 쉰 것부터 최대 20개"""
    used = {str(t.get("worktreeId")) for t in terminals(get) if t.get("worktreeId")}
    res = get("worktree", "list", "--limit", WORKTREE_LIMIT)
    rows = res.get("worktrees")
    if not isinstance(rows, list):
        raise SourceError("parse")
    rows = [w for w in rows if isinstance(w, dict) and not w.get("isMainWorktree")]
    quiet = [w for w in rows if str(w.get("id")) not in used]
    quiet.sort(key=lambda w: last_active(w) or float("inf"))  # 시각을 모르는 것은 뒤로
    items = []
    for w in quiet[:IDLE_LIST_MAX]:
        last = last_active(w)
        path = str(w.get("path") or "")
        items.append({
            "name": str(w.get("displayName") or os.path.basename(path))[:TITLE_MAX],
            "path": tilde(path),
            "branch": re.sub(r"^refs/heads/", "", str(w.get("branch") or ""))[:80],
            "ageDays": int((now * 1000 - last) // 86400000) if last is not None else None,
            "merged": merged(path, str(w.get("baseRef") or "")) if path else None,
        })
    return {"total": len(rows), "noTerminal": len(quiet), "items": items, "more": max(0, len(quiet) - IDLE_LIST_MAX),
            "truncated": bool(res.get("truncated"))}


def idle_disk(conf):
    """홈 볼륨 여유. df -k 의 마지막 줄 4번째(가용 KB)·5번째(사용률) 칸"""
    min_gb = conf_int(conf, "PANEL_DISK_MIN_GB", 50, 1, 100000)
    lines = [ln for ln in run_text([os.environ.get("PANEL_DF", "df"), "-k", os.path.expanduser("~")], DF_TIMEOUT).splitlines() if ln.strip()]
    cols = lines[-1].split() if len(lines) >= 2 else []
    if len(cols) < 5 or not cols[3].isdigit() or not cols[4].rstrip("%").isdigit():
        raise SourceError("parse")
    free = round(int(cols[3]) / 1024 / 1024, 1)
    return {"freeGB": free, "usedPct": int(cols[4].rstrip("%")), "minGB": min_gb, "warn": free < min_gb}


def idle_wssize(conf, prev, now, mark):
    """워크스페이스 폴더 크기. du 는 느려 PANEL_DU_INTERVAL 마다만 재고, 실패·간격 안이면 직전 값"""
    interval = conf_int(conf, "PANEL_DU_INTERVAL", 1800, 300, 86400)
    folder = os.path.expanduser(conf.get("PANEL_WS_DIR") or "~/orca/workspaces")
    old = prev if isinstance(prev, dict) else {"gb": None, "at": None}
    old = {**old, "dir": tilde(folder)}
    try:
        due = now - os.path.getmtime(DU_STAMP) >= interval
    except OSError:
        due = True
    if not due or not mark or not os.path.isdir(folder):
        return old
    touch(DU_STAMP)
    try:
        kb = run_text([os.environ.get("PANEL_DU", "du"), "-sk", folder], DU_TIMEOUT).split()[0]
        return {"gb": round(int(kb) / 1024 / 1024, 1), "at": now, "dir": tilde(folder)}
    except (SourceError, IndexError, ValueError):
        return old


def collect_idle(conf, get, prev, now, mark):
    """부분마다 따로 실패. 실패한 부분은 직전 값을 두고 원료 전체는 ok:false·stale. 폴더 크기 실패는 건너뜀(경고 아님)"""
    old = prev if isinstance(prev, dict) else {}
    old_val = old.get("value") if isinstance(old.get("value"), dict) else {}
    value, errs = {}, []
    parts = (
        ("terminals", lambda: {"total": len(terminals(get)), "agents": sum(1 for t in terminals(get) if t.get("agentIdentity"))}),
        ("workers", lambda: idle_workers(get)),
        ("workspaces", lambda: idle_workspaces(get, now)),
        ("disk", lambda: idle_disk(conf)),
    )
    for name, fn in parts:
        try:
            value[name] = fn()
        except SourceError as e:
            errs.append(f"{name}:{e.code}")
            value[name] = old_val.get(name)
        except ValueError:
            errs.append(f"{name}:config")
            value[name] = old_val.get(name)
    value["wsSize"] = idle_wssize(conf, old_val.get("wsSize"), now, mark)
    if errs:
        return {"ok": False, "err": ",".join(errs), "at": old.get("at"), "tried": now, "stale": True, "value": value}
    return {"ok": True, "err": None, "at": now, "tried": now, "stale": False, "value": value}


# ---- 원료: PR·CI ------------------------------------------------------------------------------
def collect_gh(repos):
    out, total_open, total_fail = [], 0, 0
    for repo in repos:
        prs = run_json([os.environ.get("PANEL_GH", "gh"), "pr", "list", "--repo", repo, "--state", "open",
                        "--limit", "100", "--json", "number,statusCheckRollup"], GH_TIMEOUT)
        if not isinstance(prs, list):
            raise SourceError("parse")
        fail = 0
        for pr in prs:
            checks = pr.get("statusCheckRollup") if isinstance(pr, dict) else None
            if any(isinstance(c, dict) and (c.get("conclusion") in CI_FAIL or c.get("state") in CI_FAIL) for c in checks or []):
                fail += 1
        out.append({"repo": repo, "open": len(prs), "ciFail": fail})
        total_open += len(prs)
        total_fail += fail
    return {"repos": out, "open": total_open, "ciFail": total_fail}


def gh_due(interval, now):
    try:
        return now - os.path.getmtime(GH_STAMP) >= interval
    except OSError:
        return True


# ---- 조립·쓰기 --------------------------------------------------------------------------------
def load_prev():
    """이전 결과. 없거나 깨졌으면 빈 값(다음 쓰기가 복구)"""
    try:
        with open(OUT, encoding="utf-8") as f:
            d = json.load(f)
        return (d.get("sources") or {}) if isinstance(d, dict) and isinstance(d.get("sources"), dict) else {}, "ok"
    except FileNotFoundError:
        return {}, "none"
    except (OSError, ValueError, UnicodeDecodeError):
        return {}, "corrupt"


def run_source(fn, prev, now):
    try:
        return {"ok": True, "err": None, "at": now, "tried": now, "stale": False, "value": fn()}
    except SourceError as e:
        code = e.code
    except ValueError:
        code = "config"
    old = prev if isinstance(prev, dict) else {}
    return {"ok": False, "err": code, "at": old.get("at"), "tried": now, "stale": True, "value": old.get("value")}


def collect(conf, repos, interval, prev, now, mark_gh):
    """(결과, gh 처리 off|run|skip). mark_gh 가 거짓(--print)이면 gh·du 간격 표식을 쓰지 않고 du 도 재지 않음"""
    get = Fetch()
    src = {
        "hubs": run_source(lambda: collect_hubs(conf, now, get), prev.get("hubs"), now),
        "pending": run_source(lambda: collect_pending(now), prev.get("pending"), now),
        "push": run_source(collect_push, prev.get("push"), now),
        "workers": run_source(lambda: collect_workers(conf, get), prev.get("workers"), now),
        "idle": collect_idle(conf, get, prev.get("idle"), now, mark_gh),
    }
    gh = "off" if not repos else "run" if gh_due(interval, now) else "skip"
    if gh == "off":
        src["gh"] = {"ok": True, "err": None, "at": now, "tried": now, "stale": False, "value": None, "off": True}
    elif gh == "run":
        if mark_gh:
            touch(GH_STAMP)
        src["gh"] = run_source(lambda: collect_gh(repos), prev.get("gh"), now)
    else:
        old = prev.get("gh")
        if isinstance(old, dict) and not old.get("off"):
            src["gh"] = old  # 간격 안: gh 호출 없이 직전 결과 유지
        else:
            src["gh"] = {"ok": False, "err": "interval", "at": None, "tried": None, "stale": True, "value": None}
    return {"version": VERSION, "updatedAt": now, "updatedAtText": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "sources": src}, gh


def touch(path):
    try:
        with open(path, "a", encoding="utf-8"):
            pass
        os.utime(path, None)
    except OSError:
        pass


def write_atomic(data):
    """같은 폴더 임시 파일에 다 쓴 뒤 rename. 읽는 쪽(mod)은 언제나 온전한 이전 판이나 새 판만 봄"""
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".panel-auto.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def log(text):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} collect {text}\n")
    except OSError:
        pass


def summary(data, gh, prev_state):
    parts = []
    for k, v in data["sources"].items():
        if k == "gh" and gh != "run":
            parts.append(f"gh={gh}")
        else:
            parts.append(f"{k}=ok" if v.get("ok") else f"{k}=fail:{v.get('err')}")
    if prev_state == "corrupt":
        parts.append("prev=corrupt")
    return " ".join(parts)


def acquire():
    """mkdir 잠금. 오래된 잠금은 회수 후 한 번 더 시도"""
    for _ in range(2):
        try:
            os.mkdir(LOCK)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(LOCK) > LOCK_STALE:
                    os.rmdir(LOCK)
                    log("lock reclaimed")
                    continue
            except OSError:
                pass
            return False
    return False


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(usage())
        return 0
    if argv not in (["--once"], ["--print"]):
        print("인자는 --once | --print | --help 중 하나 — --help 참고", file=sys.stderr)
        return 2
    conf = load_conf()
    try:
        repos = gh_repos(conf)
        interval = conf_int(conf, "PANEL_GH_INTERVAL", 300, 60, 86400)
        conf_int(conf, "PANEL_RECENT", 5, 1, 20)
        conf_int(conf, "PANEL_DISK_MIN_GB", 50, 1, 100000)
        conf_int(conf, "PANEL_DU_INTERVAL", 1800, 300, 86400)
    except ValueError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    if argv == ["--print"]:
        prev, _ = load_prev()
        print(json.dumps(collect(conf, repos, interval, prev, int(time.time()), False)[0], ensure_ascii=False, indent=1))
        return 0
    try:
        os.makedirs(DIR, exist_ok=True)
    except OSError:
        print(f"작업 폴더를 만들 수 없음: {DIR}", file=sys.stderr)
        return 2
    if not acquire():
        log("skip reason=locked")
        return 0
    try:
        prev, prev_state = load_prev()
        data, gh = collect(conf, repos, interval, prev, int(time.time()), True)
        try:
            write_atomic(data)
        except OSError:
            log("write fail")
            return 0
        log("ok " + summary(data, gh, prev_state))
    finally:
        try:
            os.rmdir(LOCK)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
