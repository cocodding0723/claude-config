#!/usr/bin/env python3
"""gh_watch.py 시험. 가짜 gh(픽스처·304·오류)와 임시 폴더만 씀. 네트워크·실제 작업판·알림 없음."""
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("GH_WATCH_SCRIPT") or os.path.join(HERE, "..", "..", "hubs", "scripts", "gh_watch.py")
REPO = "o/r"
ME = "me"
PASS = FAIL = 0


def ok(cond, name, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  " + name)
    else:
        FAIL += 1
        print("FAIL  %s — %s" % (name, detail))


def iso(minutes_ago):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Env:
    def __init__(self, **extra):
        self.d = tempfile.mkdtemp(prefix="cc-ghwatch.")
        self.fake = os.path.join(self.d, "fake")
        os.makedirs(os.path.join(self.fake, "api"))
        self.hub = os.path.join(self.d, "hub")
        os.makedirs(self.hub)
        self.env = dict(os.environ, LRM_HUB_DIR=self.hub, FAKE_DIR=self.fake,
                        LRM_GH=os.path.join(HERE, "fake_gh.py"), PANEL_GH_REPOS=REPO,
                        LRM_OSASCRIPT=os.path.join(HERE, "fake_osascript.sh"), LRM_BD=os.path.join(HERE, "fake_bd.py"),
                        BOARD_DIR=os.path.join(self.d, "board"))
        self.env.update({k: str(v) for k, v in extra.items()})
        self.prs, self.runs, self.details, self.jobs = {}, {}, {}, {}
        self.put("user", {"login": ME})
        self.flush()

    def put(self, path, body):
        key = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
        with open(os.path.join(self.fake, "api", key + ".json"), "w") as f:
            json.dump(body, f)

    def pr(self, n, user=ME, state="open", created=30, updated=None, merged=None, merged_by=None, branch=None,
           sha=None, title=None):
        self.prs[n] = {"number": n, "title": title or "PR 제목 %d" % n, "user": {"login": user}, "state": state,
                       "draft": False, "head": {"ref": branch or "feat/b%d" % n, "sha": sha or "sha%d" % n,
                                                "repo": {"full_name": REPO}},
                       "html_url": "https://github.com/o/r/pull/%d" % n, "created_at": iso(created),
                       "updated_at": iso(updated if updated is not None else created),
                       "merged_at": iso(merged) if merged is not None else None}
        if merged is not None:
            self.details[n] = {"number": n, "merged_by": {"login": merged_by or "other"}}

    def run(self, rid, pr_n, status="completed", conclusion="success", name="CI", updated=1, attempt=1, jobs=None):
        p = self.prs[pr_n]
        self.runs[rid] = {"id": rid, "name": name, "status": status, "conclusion": conclusion if status == "completed" else None,
                          "head_sha": p["head"]["sha"], "head_branch": p["head"]["ref"],
                          "head_repository": {"full_name": REPO}, "pull_requests": [], "actor": {"login": "x"},
                          "html_url": "https://github.com/o/r/actions/runs/%d" % rid, "run_attempt": attempt,
                          "updated_at": iso(updated), "display_title": p["title"]}
        if jobs is not None:
            self.jobs[rid] = jobs

    def flush(self, pulls_pp=20, runs_pp=30):
        prs = sorted(self.prs.values(), key=lambda p: p["updated_at"], reverse=True)
        runs = sorted(self.runs.values(), key=lambda r: r["id"], reverse=True)
        base = "repos/%s/pulls?state=all&sort=updated&direction=desc&per_page=%d" % (REPO, pulls_pp)
        for i in range(max(1, (len(prs) + pulls_pp - 1) // pulls_pp)):
            self.put(base + ("&page=%d" % (i + 1) if i else ""), prs[i * pulls_pp:(i + 1) * pulls_pp])
        base = "repos/%s/actions/runs?event=pull_request&per_page=%d" % (REPO, runs_pp)
        for i in range(max(1, (len(runs) + runs_pp - 1) // runs_pp)):
            self.put(base + ("&page=%d" % (i + 1) if i else ""),
                     {"total_count": len(runs), "workflow_runs": runs[i * runs_pp:(i + 1) * runs_pp]})
        for n, d in self.details.items():
            self.put("repos/%s/pulls/%d" % (REPO, n), d)
        for rid, r in self.runs.items():
            self.put("repos/%s/actions/runs/%d" % (REPO, rid), r)
        for rid, js in self.jobs.items():
            self.put("repos/%s/actions/runs/%d/jobs?filter=latest&per_page=50" % (REPO, rid),
                     {"jobs": [{"name": n, "conclusion": c} for n, c in js]})

    def calls(self):
        try:
            return open(os.path.join(self.fake, "calls.log")).read().splitlines()
        except OSError:
            return []

    def reset_calls(self):
        open(os.path.join(self.fake, "calls.log"), "w").close()

    def cli(self, *args):
        p = subprocess.run([sys.executable, SCRIPT] + list(args), env=self.env, capture_output=True, text=True,
                           timeout=60)
        try:
            out = json.loads(p.stdout)
        except ValueError:
            out = {"raw": p.stdout, "err": p.stderr}
        return p.returncode, out

    def events(self):
        try:
            return [json.loads(x) for x in open(os.path.join(self.hub, "gh-events.jsonl"))]
        except OSError:
            return []

    def state(self):
        return json.load(open(os.path.join(self.hub, "gh-state.json")))

    def fail(self, rule):
        path = os.path.join(self.fake, "fail")
        if rule:
            open(path, "w").write(rule)
        elif os.path.exists(path):
            os.unlink(path)

    def close(self):
        shutil.rmtree(self.d)


def kinds(evs):
    return [(e["kind"], e.get("pr"), e.get("result")) for e in evs]


# ---- 첫 실행·변경 없음 ----
e = Env()
e.pr(1, ME, updated=20)
e.pr(2, "other", state="closed", created=50, updated=40, merged=40, merged_by="boss")
e.run(100, 1, conclusion="failure", jobs=[("build", "failure")])
e.flush()
rc, out = e.cli("once")
st = e.state()
ok(rc == 0 and out["events"] == 0 and e.events() == [], "첫 실행: 기준만 기록, 이벤트 0", out)
ok(st["me"] == ME and [p["number"] for p in st["my_open_prs"]] == [1], "첫 실행: gh-state.json 에 내 열린 PR", st)
ok(st["recent_merges"] and st["recent_merges"][0]["merged_by"] == "boss", "첫 실행: 최근 병합 병합자 채움", st["recent_merges"])
ok(st["my_open_prs"][0]["ci"]["state"] == "failure", "첫 실행: 기존 실행으로 CI 상태 요약", st["my_open_prs"][0]["ci"])
e.reset_calls()
rc, out = e.cli("once")
c = e.calls()
ok(rc == 0 and out["events"] == 0 and len(c) == 2 and all(x.endswith("[etag]") for x in c) and out["not_modified"] == 2,
   "변경 없음: 조건부 요청 2회 모두 304, 이벤트 0", (out, c))

# ---- 새 PR(내 것·남의 것) ----
e.pr(3, "other", created=0)
e.pr(4, ME, created=0)
e.flush()
rc, out = e.cli("once")
evs = e.events()
op = {ev["pr"]: ev for ev in evs if ev["kind"] == "pr_opened"}
ok(set(op) == {3, 4} and op[3]["mine"] is False and op[4]["mine"] is True and op[4]["author"] == ME,
   "새 PR: 내 것·남의 것 구분", kinds(evs))
ev = op[3]
ok(all(k in ev for k in ("ts", "kind", "repo", "pr", "title", "branch", "result", "url")) and ev["repo"] == REPO,
   "이벤트 줄: 시각·종류·저장소·번호·제목·브랜치·결과·링크", ev)

# ---- 병합(남이·내가) ----
n0 = len(e.events())
e.pr(3, "other", state="closed", created=5, updated=0, merged=0, merged_by="boss")
e.pr(4, ME, state="closed", created=5, updated=0, merged=0, merged_by=ME)
e.flush()
e.cli("once")
mg = {ev["pr"]: ev for ev in e.events()[n0:] if ev["kind"] == "pr_merged"}
ok(set(mg) == {3, 4} and mg[3]["by"] == "boss" and mg[3]["by_me"] is False and mg[4]["by_me"] is True,
   "병합: 병합자 포함, 내가 병합한 것 구분", mg)
st = e.state()
ok(st["recent_merges"][0]["number"] in (3, 4) and len(st["recent_merges"]) == 3, "요약: 최근 병합 갱신", st["recent_merges"])

# ---- CI 진행 중 → 간격 단축 → 실패·성공·취소 ----
n0 = len(e.events())
e.pr(1, ME, created=60, updated=0, sha="sha1b")
e.run(101, 1, status="in_progress", name="CI")
e.run(102, 1, status="queued", name="Lint")
e.run(103, 1, status="in_progress", name="Docs")
e.flush()
rc, out = e.cli("once")
ok(out["interval"] == 20 and e.state()["busy"] and e.state()["my_open_prs"][0]["ci"]["state"] == "pending",
   "진행 중 내 PR 실행 → 간격 20초", out)
e.run(101, 1, conclusion="failure", name="CI", jobs=[("unity / compile", "failure"), ("lint", "success")])
e.run(102, 1, conclusion="success", name="Lint")
e.run(103, 1, conclusion="cancelled", name="Docs")
e.flush()
rc, out = e.cli("once")
ci = {ev["workflow"]: ev for ev in e.events()[n0:] if ev["kind"] == "ci_done"}
ok(ci.get("CI", {}).get("result") == "failure" and ci["CI"]["jobs"] == ["unity / compile"], "CI 실패: 실패한 잡 이름", ci)
ok(ci.get("Lint", {}).get("result") == "success" and ci.get("Docs", {}).get("result") == "cancelled", "CI 성공·취소", ci)
ok(out["interval"] == 60, "진행 중 없음 → 간격 60초 복귀", out)
s1 = e.state()["my_open_prs"][0]["ci"]
ok(s1["state"] == "failure" and s1["failed_jobs"] == ["unity / compile"] and len(s1["runs"]) == 3,
   "요약: 머리 커밋 기준 CI 상태(옛 커밋 실행 제외)", s1)
ok(not any(ev["kind"] == "ci_done" and ev["pr"] in (2, 3) for ev in e.events()), "남의 PR 실행은 이벤트 없음")

# ---- 중복 방지 ----
n0 = len(e.events())
e.cli("once")
e.run(105, 2, conclusion="failure", name="CI")  # 남의 PR 실행이 늘어 목록이 다시 200
e.flush()
e.reset_calls()
e.cli("once")
ok(len(e.events()) == n0 and not any(c.startswith("repos/o/r/actions/runs/") for c in e.calls()),
   "중복 방지: 목록이 다시 200 이어도 같은 이벤트·잡 조회 없음", (kinds(e.events()[n0:]), e.calls()))
sp = os.path.join(e.hub, "gh-watch.state.json")
raw = json.load(open(sp))
raw["repos"][REPO]["prs"].pop("1")  # 커서와 같은 초의 PR 을 다시 처음 보는 경우(>= 경계)
raw["repos"][REPO]["pulls_cursor"] = e.prs[1]["created_at"]
json.dump(raw, open(sp, "w"))
e.pr(2, "other", state="closed", created=50, updated=0, merged=40, merged_by="boss")
e.flush()
e.cli("once")
ok(len(e.events()) == n0, "중복 방지: 처리한 이벤트 id 기록으로 경계 재처리도 1회", kinds(e.events()[n0:]))
e.run(101, 1, conclusion="success", name="CI", attempt=2)
e.flush()
e.cli("once")
ok([(x["workflow"], x["result"]) for x in e.events()[n0:]] == [("CI", "success")], "재실행(attempt 2)은 새 이벤트", kinds(e.events()[n0:]))

# ---- 오류: 중간 실패 뒤 재시도 1회만 ----
n0 = len(e.events())
e.run(104, 1, conclusion="failure", name="Asset", jobs=[("asset", "failure")])
e.flush()
e.fail(r"/jobs")
rc, out = e.cli("once")
ok(rc == 1 and e.state()["ok"] is False and e.state()["error"] == "http-502" and out["interval"] == 120,
   "오류: ok=false·원인 코드·간격 늘림", out)
e.fail("")
e.cli("once")
asset = [x for x in e.events()[n0:] if x.get("workflow") == "Asset"]
ok(len(asset) == 1 and asset[0]["jobs"] == ["asset"], "중간 실패한 이벤트는 다음 회차에 1회만", asset)
e.fail("net")
rc, out = e.cli("once")
ok(rc == 1 and e.state()["error"] == "net", "네트워크 끊김: 원인 코드 net", e.state()["error"])
e.fail("")
e.close()

# ---- 잠 뒤 따라잡기(여러 쪽) ----
e = Env(GH_WATCH_PULLS_PER_PAGE=2, GH_WATCH_RUNS_PER_PAGE=2)
e.pr(1, ME, created=100, updated=100)
e.flush(2, 2)
e.cli("once")
for n in range(10, 16):  # 자는 동안 6개 PR·5개 실행
    e.pr(n, "other" if n % 2 else ME, created=10 - (n - 10))
e.pr(1, ME, created=100, updated=0)
for i, rid in enumerate(range(200, 205)):
    e.run(rid, 1, conclusion="success", name="W%d" % i)
e.flush(2, 2)
e.reset_calls()
e.cli("once")
evs = e.events()
ok(sorted(x["pr"] for x in evs if x["kind"] == "pr_opened") == list(range(10, 16)), "따라잡기: 여러 쪽 PR 전부", kinds(evs))
ok(len([x for x in evs if x["kind"] == "ci_done"]) == 5, "따라잡기: 여러 쪽 실행 전부", kinds(evs))
ok(any("&page=3" in c for c in e.calls()), "따라잡기: 다음 쪽 요청", e.calls())
e.cli("once")
ok(len(e.events()) == len(evs), "따라잡기 뒤 재실행은 이벤트 0")
e.close()

# ---- kick·잠금 ----
e = Env(GH_WATCH_IDLE=600)
e.pr(1, ME)
e.flush()
rc, out = e.cli("kick")
ok(rc == 0 and out["mode"] == "once", "kick: 상주 없으면 직접 한 회차", out)
d = subprocess.Popen([sys.executable, SCRIPT, "run"], env=e.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
log = os.path.join(e.hub, "gh-watch.log")


def cycles():
    try:
        return sum(1 for ln in open(log) if " cycle " in ln)
    except OSError:
        return 0


for _ in range(50):
    if os.path.exists(os.path.join(e.hub, "gh-watch.pid")) and cycles() >= 2:
        break
    time.sleep(0.2)
before = cycles()
rc, out = e.cli("once")
ok(rc == 4, "상주가 잠금 보유 중이면 once 는 종료 코드 4", (rc, out))
rc, out = e.cli("kick")
for _ in range(30):
    if cycles() > before:
        break
    time.sleep(0.2)
ok(rc == 0 and out["mode"] == "signal" and cycles() == before + 1, "kick: 상주를 신호로 즉시 깨움", (out, before, cycles()))
d.send_signal(signal.SIGTERM)
d.wait(10)
ok(not os.path.exists(os.path.join(e.hub, "gh-watch.pid")), "상주 종료 때 pid 파일 정리")
with open(os.path.join(e.hub, "gh-watch.pid"), "w") as f:
    f.write("1")
fd = os.open(os.path.join(e.hub, "gh-watch.lock"), os.O_RDWR)
import fcntl  # noqa: E402

fcntl.flock(fd, fcntl.LOCK_EX)
rc, out = e.cli("kick")
ok(rc == 1 and "감시기가 아님" in out.get("error", ""), "낡은 pid 파일이면 신호 안 보냄", out)
os.close(fd)
log_text = open(log).read()
ok("Bearer" not in log_text and "token" not in log_text.lower() and "If-None-Match" not in log_text,
   "로그에 토큰·헤더 없음(결과 코드만)")
e.close()

# ---- 처리기: 작업판 카드·알림 상한·후속 명령 ----
e = Env(GH_WATCH_NOTIFY_PER_HOUR=2)
os.makedirs(e.env["BOARD_DIR"])
json.dump([{"id": "c-a", "labels": ["branch:feat/b3"]},
           {"id": "c-b", "labels": [], "description": "작업 메모\n브랜치: `feat/b4`"},
           {"id": "c-c", "labels": [], "description": "branch: feat/b4-extra"},
           {"id": "c-d", "labels": ["file:x"]}], open(os.path.join(e.fake, "cards.json"), "w"))
out_file = os.path.join(e.d, "action.out")
json.dump({"actions": [
    {"on": "ci_done:failure", "mine": True, "enabled": True,
     "cmd": ["/bin/sh", "-c", 'echo "$GH_WATCH_KIND $GH_WATCH_PR $GH_WATCH_RESULT" >> "$0"', out_file]},
    {"on": "pr_merged", "cmd": ["/bin/sh", "-c", 'echo off >> "$0"', out_file]}]},
    open(os.path.join(e.hub, "gh-watch-actions.json"), "w"))
e.pr(1, ME)
e.pr(9, ME, branch="feat/none")
e.flush()
e.cli("once")
e.pr(3, "other", created=0)
e.pr(4, ME, created=0)
e.flush()
e.cli("once")
e.pr(3, "other", state="closed", created=5, updated=0, merged=0, merged_by="boss")
e.pr(4, ME, state="closed", created=5, updated=0, merged=0, merged_by=ME)
e.pr(1, ME, created=60, updated=0, sha="s1b")
e.run(301, 1, conclusion="success", name="CI")
e.run(302, 9, conclusion="failure", name="CI", jobs=[("build", "failure")])
e.run(303, 9, conclusion="failure", name="Lint", jobs=[("lint", "failure")])
e.flush()
e.cli("once")


def lines(name):
    try:
        return open(os.path.join(e.fake, name)).read().splitlines()
    except OSError:
        return []


bc = lines("bd-comments.log")
ok(sorted(x.split("\t")[0] for x in bc) == ["c-a", "c-a", "c-b", "c-b"], "카드 매칭: label·본문 브랜치 정확히, 비슷한 이름 제외", bc)
ok(any("병합됨(병합 boss)" in x for x in bc) and all(x.endswith("\tgh-watch") for x in bc), "카드 코멘트: 병합자·작성자 gh-watch", bc)
ok("board sent=" in open(os.path.join(e.hub, "gh-watch.log")).read() and "skip=2" in open(os.path.join(e.hub, "gh-watch.log")).read(),
   "맞는 카드 없는 이벤트는 건너뜀(CI 실패 2건, 브랜치 feat/none)")
nl = lines("notify.log")
ok(len(nl) == 2 and nl[0].startswith("PR #3 병합됨"), "알림: 남이 병합한 것·내 CI 실패만, 시간당 상한 2", nl)
ok("capped=1" in open(os.path.join(e.hub, "gh-watch.log")).read(), "알림 상한 넘은 건은 capped 로 기록")
e.run(304, 9, conclusion="failure", name="Docs", jobs=[("docs", "failure")])
e.flush()
e.cli("once")
ok(len(lines("notify.log")) == 2, "상한은 재시작(다음 회차)에도 유지", lines("notify.log"))
act = open(out_file).read().splitlines() if os.path.exists(out_file) else []
ok(act == ["ci_done 9 failure"] * 3, "후속 명령: 켠 규칙만, 이벤트는 환경 변수로", act)
n_bc, n_act = len(lines("bd-comments.log")), len(act)
e.cli("once")
ok(len(lines("bd-comments.log")) == n_bc and len(open(out_file).read().splitlines()) == n_act, "같은 이벤트 처리기 1회")
os.unlink(os.path.join(e.hub, "gh-watch-actions.json"))
e.close()

print("\n결과: 통과 %d, 실패 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
