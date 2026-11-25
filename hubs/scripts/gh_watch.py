#!/usr/bin/env python3
"""GitHub PR·CI 폴링 감시기. 새 PR·병합·내 PR CI 완료를 이벤트로 남기고 현재 요약을 파일로 씀.

REST 두 개만 씀: pulls(updated 내림차순)·actions/runs(event=pull_request). 둘 다 ETag 조건부 요청이라
변경 없으면 304(호출 한도 차감 없음). 웹훅·GraphQL·검색·events·notifications API 는 쓰지 않음.
출력 계약은 hubs/docs/gh-watch.md.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 설치 링크로 불려도 같은 폴더 모듈을 찾게
import gh_watch_sinks as sinks  # noqa: E402

DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
GH = os.environ.get("LRM_GH", "gh")
EVENTS = os.path.join(DIR, "gh-events.jsonl")
SUMMARY = os.path.join(DIR, "gh-state.json")
STATE = os.path.join(DIR, "gh-watch.state.json")
LOCK = os.path.join(DIR, "gh-watch.lock")
PIDFILE = os.path.join(DIR, "gh-watch.pid")
LOG = os.path.join(DIR, "gh-watch.log")
CONF = os.path.join(DIR, "gh-watch.env")
PANEL_CONF = os.path.join(DIR, "panel.env")
ACTIONS = os.path.join(DIR, "gh-watch-actions.json")

# 조절값은 여기 한 곳. gh-watch.env(KEY=값)·같은 이름 환경 변수로 덮어씀
DEFAULTS = {
    "GH_WATCH_IDLE": 60,           # 평소 간격 초
    "GH_WATCH_BUSY": 20,           # 내 PR 실행이 진행 중일 때 간격 초
    "GH_WATCH_ERROR_MAX": 600,     # 연속 오류 때 간격 상한 초(지수 증가)
    "GH_WATCH_LOW_RATE": 200,      # 남은 호출이 이 아래면 평소 간격의 5배
    "GH_WATCH_PAGES": 5,           # 따라잡기 때 목록당 최대 쪽 수
    "GH_WATCH_PULLS_PER_PAGE": 20,
    "GH_WATCH_RUNS_PER_PAGE": 30,
    "GH_WATCH_PENDING_FETCH": 10,  # 목록에서 빠진 진행 중 실행을 개별 조회할 최대 수
    "GH_WATCH_RECENT_MERGES": 10,
    "GH_WATCH_NOTIFY_PER_HOUR": 6,
    "GH_WATCH_ACTION_TIMEOUT": 60,
    "GH_WATCH_BOARD_RETRY": 60,        # 작업판 기록 재시도 첫 간격 초(두 배씩 증가)
    "GH_WATCH_BOARD_RETRY_MAX": 600,   # 작업판 기록 재시도 간격 상한 초
    "GH_WATCH_BOARD_KEEP": 259200,     # 작업판 기록 대기 보관 초. 넘으면 drop 로 남기고 버림
    "GH_WATCH_SEEN_MAX": 3000,     # 처리한 이벤트 id 보관 수
    "GH_WATCH_LOG_MAX": 262144,    # 로그 돌림 크기(바이트)
    "GH_WATCH_EVENTS_MAX": 2097152,
    "GH_WATCH_HTTP_TIMEOUT": 30,
}
DEFAULT_REPO = "Basakansoft/LoveRevolutionMaker"
PENDING = {"queued", "in_progress", "waiting", "requested", "pending"}
RESULT = {"success": "success", "failure": "failure", "timed_out": "failure", "startup_failure": "failure",
          "cancelled": "cancelled"}  # skipped·neutral·action_required 는 이벤트로 내지 않음
TITLE_MAX = 40


def read_kv(path):
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def load_conf():
    """파일은 실행하지 않고 KEY=값만 읽음. 환경 변수가 우선"""
    raw = read_kv(CONF)
    conf = {}
    for k, d in DEFAULTS.items():
        v = os.environ.get(k) or raw.get(k)
        try:
            conf[k] = max(1, int(v)) if v else d
        except ValueError:
            conf[k] = d
    repos = os.environ.get("PANEL_GH_REPOS")
    if repos is None:
        repos = read_kv(PANEL_CONF).get("PANEL_GH_REPOS", "")
    conf["repos"] = [r for r in re.split(r"[\s,]+", repos) if re.fullmatch(r"[\w.-]+/[\w.-]+", r)] or [DEFAULT_REPO]
    return conf


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path, data):
    """임시 파일에 쓴 뒤 원자 교체. 읽는 쪽이 반쯤 쓴 파일을 보지 않게 함"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-gh-watch-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rotate(path, limit):
    try:
        if os.path.getsize(path) > limit:
            os.replace(path, path + ".1")
    except OSError:
        pass


def log(conf, msg):
    rotate(LOG, conf["GH_WATCH_LOG_MAX"])
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (now_iso(), msg))


class GhError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class Api:
    """gh api 호출기. 호출 수·남은 한도를 셈. 응답 헤더는 상태·etag·한도만 꺼내고 저장하지 않음"""

    def __init__(self, conf):
        self.conf = conf
        self.calls = 0
        self.not_modified = 0
        self.rate = {}

    def get(self, path, etag=None):
        """(status, body, etag). 304 는 gh 가 종료 코드 1 로 끝나므로 상태 줄로 판정"""
        cmd = [GH, "api", "-i", path]
        if etag:
            cmd[3:3] = ["-H", "If-None-Match: " + etag]
        self.calls += 1
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=self.conf["GH_WATCH_HTTP_TIMEOUT"])
        except subprocess.TimeoutExpired:
            raise GhError("timeout", "gh api 시간 초과: " + path.split("?")[0])
        except OSError as e:
            raise GhError("no-gh", "gh 실행 불가: %s" % e)
        parts = re.split(r"\r?\n\r?\n", p.stdout, maxsplit=1)
        m = re.match(r"HTTP/[\d.]+ (\d{3})", parts[0])
        if not m:
            raise GhError("net", (p.stderr.strip() or "응답 없음").splitlines()[-1][:160])
        status = int(m.group(1))
        heads = {}
        for ln in parts[0].splitlines()[1:]:
            k, _, v = ln.partition(":")
            heads[k.strip().lower()] = v.strip()
        for k in ("remaining", "used", "limit", "reset"):
            if "x-ratelimit-" + k in heads:
                self.rate[k] = int(heads["x-ratelimit-" + k])
        if status == 304:
            self.not_modified += 1
            return 304, None, etag
        if status >= 400:
            raise GhError("http-%d" % status, "HTTP %d %s" % (status, path.split("?")[0]))
        try:
            body = json.loads(parts[1]) if len(parts) > 1 and parts[1].strip() else None
        except ValueError:
            raise GhError("json", "응답 JSON 오류: " + path.split("?")[0])
        return status, body, heads.get("etag")


def short(t):
    t = (t or "").strip()
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1] + "…"


def fresh_repo_state():
    return {"init": False, "pulls_etag": None, "runs_etag": None, "pulls_cursor": "", "runs_cursor": 0,
            "prs": {}, "runs": {}, "page1_runs": [], "merges": []}


class Watcher:
    def __init__(self, conf, state, api):
        self.conf, self.state, self.api = conf, state, api
        self.events = []

    def me(self):
        """로그인 이름은 하루 한 번만 다시 확인"""
        st = self.state
        if not st.get("me") or time.time() - st.get("me_at", 0) > 86400:
            _, body, _ = self.api.get("user")
            st["me"], st["me_at"] = body["login"], time.time()
        return st["me"]

    def mark(self, ev_id):
        """처리한 이벤트 id 기록. 이미 있으면 False"""
        seen = self.state.setdefault("seen", [])
        if ev_id in seen:
            return False
        seen.append(ev_id)
        del seen[:-self.conf["GH_WATCH_SEEN_MAX"]]
        return True

    def emit(self, ev):
        if self.mark(ev["id"]):
            self.events.append(ev)

    def pages(self, path, etag, per_page, older_than_cursor, items_of, init):
        """1쪽은 ETag 조건부. 쪽의 마지막 항목이 커서보다 새로우면 다음 쪽까지(잠·끊김 따라잡기).
        반환 (항목|None(304), 새 etag). etag 는 호출자가 처리를 끝낸 뒤 저장(중간 실패 때 다음 회차가 다시 받게)"""
        status, body, etag = self.api.get("%s&per_page=%d" % (path, per_page), etag)
        if status == 304:
            return None, etag
        items = list(items_of(body))
        page, last = 1, items
        while init and len(last) == per_page and page < self.conf["GH_WATCH_PAGES"] \
                and not older_than_cursor(last[-1]):
            page += 1
            _, body, _ = self.api.get("%s&per_page=%d&page=%d" % (path, per_page, page))
            last = list(items_of(body))
            items += last
        return items, etag

    def poll_pulls(self, repo, rs, me):
        cursor = rs["pulls_cursor"]
        items, etag = self.pages("repos/%s/pulls?state=all&sort=updated&direction=desc" % repo, rs["pulls_etag"],
                                 self.conf["GH_WATCH_PULLS_PER_PAGE"], lambda p: p["updated_at"] < cursor,
                                 lambda b: b, rs["init"])
        if items is None:
            return
        for p in sorted(items, key=lambda x: x["updated_at"]):
            key = str(p["number"])
            old = rs["prs"].get(key)
            rec = {"number": p["number"], "title": p["title"], "user": p["user"]["login"], "state": p["state"],
                   "draft": bool(p.get("draft")), "branch": p["head"]["ref"], "sha": p["head"]["sha"],
                   "head_repo": ((p["head"].get("repo") or {}).get("full_name")), "url": p["html_url"],
                   "updated_at": p["updated_at"], "merged_at": p.get("merged_at"),
                   "merged_by": (old or {}).get("merged_by")}
            base = {"repo": repo, "pr": p["number"], "title": short(p["title"]), "branch": rec["branch"],
                    "url": rec["url"], "author": rec["user"], "mine": rec["user"] == me}
            if not rs["init"]:  # 첫 실행에 이미 있던 PR·병합은 처리한 것으로 기록
                self.mark("opened:%s#%s" % (repo, key))
                if rec["merged_at"]:
                    self.mark("merged:%s#%s" % (repo, key))
            elif old is None and p["created_at"] >= cursor:
                self.emit(dict(base, id="opened:%s#%s" % (repo, key), kind="pr_opened", result="opened"))
            if rec["merged_at"] and not (old or {}).get("merged_at"):
                if rs["init"] and (old is not None or rec["merged_at"] >= cursor):
                    _, full, _ = self.api.get("repos/%s/pulls/%s" % (repo, key))
                    rec["merged_by"] = ((full or {}).get("merged_by") or {}).get("login")
                    self.emit(dict(base, id="merged:%s#%s" % (repo, key), kind="pr_merged", result="merged",
                                   by=rec["merged_by"], by_me=rec["merged_by"] == me))
                rs["merges"] = [m for m in rs["merges"] if m["number"] != p["number"]] + [{
                    "repo": repo, "number": p["number"], "title": short(p["title"]), "branch": rec["branch"],
                    "merged_at": rec["merged_at"], "merged_by": rec["merged_by"], "author": rec["user"],
                    "mine": rec["user"] == me, "url": rec["url"]}]
            rs["prs"][key] = rec  # 병합자 조회까지 끝난 뒤 반영. 중간에 실패하면 다음 회차가 같은 병합을 다시 봄
            cursor = max(cursor, p["updated_at"])
        rs["merges"] = sorted(rs["merges"], key=lambda m: m["merged_at"])[-self.conf["GH_WATCH_RECENT_MERGES"]:]
        if not rs["init"]:  # 첫 실행은 이벤트 없이 기준만. 최근 병합 칸의 병합자만 채움
            for m in rs["merges"]:
                _, full, _ = self.api.get("repos/%s/pulls/%s" % (repo, m["number"]))
                m["merged_by"] = ((full or {}).get("merged_by") or {}).get("login")
                rs["prs"][str(m["number"])]["merged_by"] = m["merged_by"]
        rs["pulls_cursor"], rs["pulls_etag"] = cursor, etag
        # 닫힌 PR 은 30일 지나면 잊음(재개봉돼도 created_at 이 옛날이라 새 PR 로 치지 않음)
        cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rs["prs"] = {k: v for k, v in rs["prs"].items() if v["state"] == "open" or v["updated_at"] >= cut}

    def pr_of_run(self, rs, run):
        for p in run.get("pull_requests") or []:
            if str(p["number"]) in rs["prs"]:
                return rs["prs"][str(p["number"])]
        head_repo = (run.get("head_repository") or {}).get("full_name")
        cands = [p for p in rs["prs"].values() if p["branch"] == run.get("head_branch")
                 and (not head_repo or not p.get("head_repo") or p["head_repo"] == head_repo)]
        cands.sort(key=lambda p: (p["sha"] == run.get("head_sha"), p["state"] == "open", p["updated_at"]))
        return cands[-1] if cands else None

    def failed_jobs(self, repo, run_id):
        _, body, _ = self.api.get("repos/%s/actions/runs/%s/jobs?filter=latest&per_page=50" % (repo, run_id))
        return [j["name"] for j in (body or {}).get("jobs", []) if RESULT.get(j.get("conclusion")) == "failure"]

    def take_run(self, repo, rs, run, me):
        """내 PR 의 실행만 보관. PR 을 못 찾으면 실행을 일으킨 사람으로 판정"""
        pr = self.pr_of_run(rs, run)
        mine = (pr["user"] == me) if pr else (run.get("actor") or {}).get("login") == me
        if not mine:
            return
        rid = str(run["id"])
        rec = {"id": run["id"], "name": run.get("name"), "status": run.get("status"),
               "conclusion": run.get("conclusion"), "sha": run.get("head_sha"), "branch": run.get("head_branch"),
               "pr": pr["number"] if pr else None, "mine": True, "url": run.get("html_url"),
               "attempt": run.get("run_attempt") or 1, "updated_at": run.get("updated_at")}
        result = RESULT.get(run.get("conclusion")) if run.get("status") == "completed" else None
        if rs["init"] and result:
            ev_id = "ci:%s:%s:%s" % (repo, rid, rec["attempt"])
            if ev_id not in self.state.get("seen", []):
                jobs = self.failed_jobs(repo, rid) if result == "failure" else []
                rec["failed_jobs"] = jobs
                self.emit({"id": ev_id, "kind": "ci_done", "repo": repo, "pr": rec["pr"],
                           "title": short(pr["title"] if pr else run.get("display_title")),
                           "branch": rec["branch"], "result": result, "workflow": rec["name"],
                           "jobs": jobs, "url": rec["url"], "mine": True})
        elif result:
            self.mark("ci:%s:%s:%s" % (repo, rid, rec["attempt"]))
        rec.setdefault("failed_jobs", (rs["runs"].get(rid) or {}).get("failed_jobs", []))
        rs["runs"][rid] = rec

    def poll_runs(self, repo, rs, me):
        cursor = rs["runs_cursor"]
        items, etag = self.pages("repos/%s/actions/runs?event=pull_request" % repo, rs["runs_etag"],
                                 self.conf["GH_WATCH_RUNS_PER_PAGE"], lambda r: r["id"] <= cursor,
                                 lambda b: (b or {}).get("workflow_runs", []), rs["init"])
        got = set()
        if items is not None:
            rs["page1_runs"] = [r["id"] for r in items[:self.conf["GH_WATCH_RUNS_PER_PAGE"]]]
            for r in sorted(items, key=lambda x: x["id"]):
                self.take_run(repo, rs, r, me)
                got.add(r["id"])
                cursor = max(cursor, r["id"])
            rs["runs_cursor"], rs["runs_etag"] = cursor, etag
        # 1쪽 밖으로 밀려난 진행 중 실행은 개별 조회. 1쪽이 304 면 그 안의 실행은 바뀌지 않은 것
        lost = [v["id"] for v in rs["runs"].values() if v["status"] in PENDING
                and v["id"] not in got and v["id"] not in rs["page1_runs"]]
        for rid in sorted(lost)[-self.conf["GH_WATCH_PENDING_FETCH"]:]:
            _, run, _ = self.api.get("repos/%s/actions/runs/%s" % (repo, rid))
            if run:
                self.take_run(repo, rs, run, me)
        # 완료는 14일, 진행 중인데 3일 넘게 안 바뀐 것(중단된 실행)은 버림
        now = time.time()
        rs["runs"] = {k: v for k, v in rs["runs"].items()
                      if _ts(v.get("updated_at")) >= now - (3 if v["status"] in PENDING else 14) * 86400}

    def cycle(self):
        me = self.me()
        repos = self.state.setdefault("repos", {})
        for repo in self.conf["repos"]:
            rs = repos.setdefault(repo, fresh_repo_state())
            self.poll_pulls(repo, rs, me)
            self.poll_runs(repo, rs, me)
            rs["init"] = True
        return me


def _ts(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0


def ci_summary(rs, pr):
    """PR 머리 커밋의 워크플로별 최신 실행만 모아 한 상태로"""
    latest = {}
    for r in rs["runs"].values():
        if r["pr"] == pr["number"] and r["sha"] == pr["sha"]:
            cur = latest.get(r["name"])
            if cur is None or (r["id"], r["attempt"]) > (cur["id"], cur["attempt"]):
                latest[r["name"]] = r
    runs = sorted(latest.values(), key=lambda r: r["name"] or "")
    states = [("pending" if r["status"] in PENDING else RESULT.get(r["conclusion"], "other")) for r in runs]
    state = "none"
    for s in ("pending", "failure", "cancelled", "success"):
        if s in states:
            state = s
            break
    return {"state": state,
            "failed_jobs": sorted({j for r in runs for j in r.get("failed_jobs") or []}),
            "runs": [{"workflow": r["name"], "state": st, "url": r["url"]} for r, st in zip(runs, states)]}


def busy(state):
    return any(v["status"] in PENDING
               for rs in state.get("repos", {}).values() for v in rs["runs"].values())


def summary(conf, state, interval, err, api):
    mine, merges = [], []
    for repo in conf["repos"]:
        rs = state.get("repos", {}).get(repo)
        if not rs:
            continue
        for p in rs["prs"].values():
            if p["state"] == "open" and p["user"] == state.get("me"):
                mine.append({"repo": repo, "number": p["number"], "title": short(p["title"]), "branch": p["branch"],
                             "draft": p["draft"], "url": p["url"], "updated_at": p["updated_at"],
                             "ci": ci_summary(rs, p)})
        merges += rs["merges"]
    merges.sort(key=lambda m: m["merged_at"], reverse=True)
    return {"schema": 1, "updated_at": now_iso(), "ok": err is None, "error": err, "me": state.get("me"),
            "repos": conf["repos"], "interval": interval, "busy": busy(state),
            "next_poll_at": dt.datetime.fromtimestamp(time.time() + interval, dt.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "my_open_prs": sorted(mine, key=lambda p: p["updated_at"], reverse=True),
            "recent_merges": merges[:conf["GH_WATCH_RECENT_MERGES"]],
            "rate": {k: api.rate[k] for k in ("remaining", "used", "limit", "reset") if k in api.rate}}


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def append_events(conf, events):
    if not events:
        return
    rotate(EVENTS, conf["GH_WATCH_EVENTS_MAX"])
    with open(EVENTS, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(dict(ev, ts=now_iso()), ensure_ascii=False) + "\n")


def run_once(conf):
    """한 회차. 반환: 다음 간격 초. 이벤트는 상태 저장 전에 기록(중간에 죽으면 다음 회차가 같은 id 로 다시 냄)"""
    state = load_state()
    api = Api(conf)
    w = Watcher(conf, state, api)
    err = None
    try:
        w.cycle()
        state["errors"] = 0
    except GhError as e:
        err = e.code
        state["errors"] = state.get("errors", 0) + 1
    except (KeyError, TypeError) as e:
        err = "shape"
        state["errors"] = state.get("errors", 0) + 1
        log(conf, "cycle error=shape detail=%s" % type(e).__name__)
    append_events(conf, w.events)
    sinks.dispatch(w.events, state, conf, ACTIONS, lambda m: log(conf, m))
    if err:
        interval = min(conf["GH_WATCH_ERROR_MAX"], conf["GH_WATCH_IDLE"] * 2 ** min(state["errors"], 6))
    elif busy(state):
        interval = conf["GH_WATCH_BUSY"]
    else:
        interval = conf["GH_WATCH_IDLE"]
    if not err and api.rate.get("remaining", 10 ** 9) < conf["GH_WATCH_LOW_RATE"]:
        interval = max(interval, conf["GH_WATCH_IDLE"] * 5)
    write_json(STATE, state)
    write_json(SUMMARY, summary(conf, state, interval, err, api))
    log(conf, "cycle %s calls=%d not_modified=%d events=%d interval=%d remaining=%s" % (
        "ok" if not err else "error=" + err, api.calls, api.not_modified, len(w.events), interval,
        api.rate.get("remaining", "-")))
    return interval, w.events, api


_kicked = False


def _on_kick(signum, frame):
    global _kicked
    _kicked = True


def lock(block):
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def daemon(conf):
    global _kicked
    fd = lock(block=True)  # 다른 상주가 있으면 끝날 때까지 대기(launchd 재시작 폭주 방지)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))  # 상주만 씀. once 는 쓰지 않음(kick 이 once 를 신호로 깨우지 않게)
    signal.signal(signal.SIGUSR1, _on_kick)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log(conf, "start pid=%d repos=%d" % (os.getpid(), len(conf["repos"])))
    try:
        while True:
            _kicked = False
            interval = run_once(conf)[0]
            end = time.monotonic() + interval
            while not _kicked and time.monotonic() < end:
                time.sleep(1)
            conf = load_conf()
    finally:
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
        os.close(fd)


def is_watcher(pid):
    """pid 재사용으로 남의 프로세스에 신호를 보내지 않게 명령줄 확인"""
    p = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return p.returncode == 0 and "gh_watch.py" in p.stdout


def kick(conf):
    """상주가 잠금을 쥐고 있으면 신호로 깨우고, 없으면 직접 한 회차"""
    fd = lock(block=False)
    if fd is None:
        try:
            pid = int(open(PIDFILE).read().strip())
        except (OSError, ValueError):
            return {"ok": True, "mode": "busy", "note": "한 회차(once)가 이미 도는 중"}
        if not is_watcher(pid):
            return {"ok": False, "mode": "signal", "error": "pid %d 가 감시기가 아님(낡은 gh-watch.pid). 신호 안 보냄" % pid}
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError as e:
            return {"ok": False, "mode": "signal", "error": "상주 pid 신호 실패: %s" % e}
        return {"ok": True, "mode": "signal", "pid": pid}
    try:
        interval, events, api = run_once(conf)
        return {"ok": True, "mode": "once", "events": len(events), "calls": api.calls, "interval": interval}
    finally:
        os.close(fd)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="gh-watch", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="GitHub PR·CI 폴링 감시기. 출력: gh-events.jsonl(이벤트)·gh-state.json(요약). 계약: docs/gh-watch.md",
        epilog="명령:\n  run     상주 루프(launchd ai.lrm.ghwatch)\n  once    한 회차만(상주가 돌고 있으면 종료 코드 4)\n"
               "  kick    상주를 즉시 깨움. 상주가 없으면 직접 한 회차\n  status  gh-state.json 출력\n"
               "종료: 0 정상 / 1 gh 오류(once) / 2 사용법 / 4 다른 실행이 잠금 보유\n"
               "예: gh-watch kick   # 병합·push 직후 바로 반영")
    ap.add_argument("cmd", choices=["run", "once", "kick", "status"])
    a = ap.parse_args(argv)
    os.makedirs(DIR, exist_ok=True)
    conf = load_conf()
    if a.cmd == "run":
        daemon(conf)
    elif a.cmd == "status":
        try:
            print(open(SUMMARY, encoding="utf-8").read(), end="")
        except OSError:
            print(json.dumps({"ok": False, "error": "gh-state.json 없음. gh-watch once 로 먼저 생성"}))
            return 1
    elif a.cmd == "kick":
        out = kick(conf)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out["ok"] else 1
    else:
        fd = lock(block=False)
        if fd is None:
            print(json.dumps({"ok": False, "error": "다른 실행이 잠금 보유. gh-watch kick 사용"}, ensure_ascii=False))
            return 4
        try:
            interval, events, api = run_once(conf)
        finally:
            os.close(fd)
        ok = json.load(open(SUMMARY, encoding="utf-8"))["ok"]
        print(json.dumps({"ok": ok, "events": len(events), "calls": api.calls,
                          "not_modified": api.not_modified, "interval": interval}, ensure_ascii=False))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
