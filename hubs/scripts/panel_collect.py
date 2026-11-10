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
CONF_KEYS = ("PANEL_GH_REPOS", "PANEL_GH_INTERVAL", "PANEL_HUB_ROOT", "PANEL_RECENT")

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
        "환경 변수: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs), PANEL_ORCA·PANEL_GH 실행 파일(시험용)",
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


def collect_hubs(conf, now):
    """허브 식별은 patrol.sh 규칙: 작업 폴더 이름 coord-* + 에이전트 붙은 터미널(+ PANEL_HUB_ROOT 가 있으면 그 아래)"""
    terms = orca("terminal", "list").get("terminals")
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


def collect_workers(conf):
    """최근 종료된 워커(완료 보고)의 작업 제목·상태·시각. worker-list 는 최신이 앞. 제목은 task-list 에서만 얻음"""
    limit = conf_int(conf, "PANEL_RECENT", 5, 1, 20)
    ws = orca("orchestration", "worker-list", "--full").get("workers")
    if not isinstance(ws, list):
        raise SourceError("parse")
    live, recent = 0, []
    for w in ws:
        if not isinstance(w, dict):
            continue
        proj = w.get("projection") if isinstance(w.get("projection"), dict) else {}
        verdict = (proj.get("liveness") or {}).get("verdict") if isinstance(proj.get("liveness"), dict) else None
        if verdict == "live":
            live += 1
        state = str(w.get("workerState") or "")
        if state in ("succeeded", "failed", "stopped") and len(recent) < limit:
            res = w.get("resource") if isinstance(w.get("resource"), dict) else {}
            recent.append({"task": str(w.get("taskId") or ""), "run": str(w.get("runId") or ""),
                           "status": state, "at": utc_epoch(res.get("releaseCompletedAt"))})
    titles = {}
    for run in list(dict.fromkeys(r["run"] for r in recent if r["run"]))[:RECENT_RUNS]:
        try:
            for t in orca("orchestration", "task-list", "--run", run).get("tasks") or []:
                if isinstance(t, dict) and t.get("id"):
                    titles[t["id"]] = str(t.get("title") or "").split("\n")[0][:TITLE_MAX]
        except SourceError:
            continue  # 제목만 빠짐. 상태·시각은 유지
    items = [{"title": titles.get(r["task"]), "status": r["status"], "at": r["at"]} for r in recent]
    return {"live": live, "recent": items}


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
    """(결과, gh 처리 off|run|skip)"""
    src = {
        "hubs": run_source(lambda: collect_hubs(conf, now), prev.get("hubs"), now),
        "pending": run_source(lambda: collect_pending(now), prev.get("pending"), now),
        "push": run_source(collect_push, prev.get("push"), now),
        "workers": run_source(lambda: collect_workers(conf), prev.get("workers"), now),
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
