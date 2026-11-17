"""gh_watch 이벤트 처리기: 작업판 카드 코멘트·후속 명령·데스크톱 알림. 출처(폴링·다른 수집기)와 무관하게 이벤트 목록만 받음.

각 처리기는 자기 실패를 삼키고 결과 코드만 돌려줌(한 처리기 실패가 다른 처리기·감시 루프를 멈추지 않게).
"""
import json
import os
import re
import subprocess
import time

BD = os.environ.get("LRM_BD", "bd")
OSASCRIPT = os.environ.get("LRM_OSASCRIPT", "osascript")
BOARD_KINDS = {("pr_opened", "opened"), ("pr_merged", "merged"), ("ci_done", "failure"), ("ci_done", "cancelled")}


def board_text(ev):
    pr = "PR #%s" % ev["pr"] if ev.get("pr") else "PR 없음"
    if ev["kind"] == "pr_opened":
        what = "열림(작성 %s)" % ev.get("author")
    elif ev["kind"] == "pr_merged":
        what = "병합됨(병합 %s)" % (ev.get("by") or "확인 못 함")
    else:
        what = "CI %s: %s" % ("실패" if ev["result"] == "failure" else "취소", ev.get("workflow"))
        if ev.get("jobs"):
            what += " — 잡 " + ", ".join(ev["jobs"][:5])
    return "[gh-watch] %s %s · %s %s" % (pr, what, ev.get("branch"), ev.get("url"))


def cards_for(cards, branch):
    """label branch:<브랜치> 또는 본문의 'branch: <브랜치>'·'브랜치: <브랜치>' 가 정확히 맞는 카드"""
    pat = re.compile(r"(?:branch|브랜치)\s*[:：]\s*`?%s`?(?![\w./-])" % re.escape(branch), re.I)
    out = []
    for c in cards:
        text = "\n".join(str(c.get(k) or "") for k in ("description", "notes", "design"))
        if "branch:" + branch in (c.get("labels") or []) or pat.search(text):
            out.append(c["id"])
    return out


def to_board(events, log):
    evs = [e for e in events if (e["kind"], e.get("result")) in BOARD_KINDS and e.get("branch")]
    if not evs:
        return
    board = os.environ.get("BOARD_DIR") or os.path.expanduser("~/orca/board")
    if not os.path.isdir(board):
        log("board skip=no-board")
        return
    env = dict(os.environ, BD_ACTOR="gh-watch")
    try:
        p = subprocess.run([BD, "list", "--all", "--json", "--limit", "0"], cwd=board, env=env,
                           capture_output=True, text=True, timeout=30)
        cards = json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        cards = None
    if cards is None:
        log("board error=bd-list")
        return
    sent = skip = fail = 0
    for ev in evs:
        ids = cards_for(cards, ev["branch"])
        if not ids:
            skip += 1
        for cid in ids:
            try:
                r = subprocess.run([BD, "comment", cid, board_text(ev)], cwd=board, env=env,
                                   capture_output=True, text=True, timeout=30)
                ok = r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            sent, fail = sent + ok, fail + (not ok)
    log("board sent=%d skip=%d fail=%d" % (sent, skip, fail))


def load_actions(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return []
    except ValueError:
        return None
    return [a for a in data.get("actions", []) if a.get("enabled") is True and isinstance(a.get("cmd"), list)]


def matches(rule, ev):
    on = rule.get("on", "")
    if on not in (ev["kind"], "%s:%s" % (ev["kind"], ev.get("result"))):
        return False
    for k in ("mine", "by_me", "repo"):
        if k in rule and rule[k] != ev.get(k):
            return False
    return True


def run_actions(events, path, timeout, log):
    """설정 파일의 켜진 규칙만 실행. 셸을 거치지 않고 argv 그대로, 이벤트는 환경 변수로 넘김"""
    rules = load_actions(path)
    if rules is None:
        log("action error=bad-json")
        return
    for ev in events:
        for rule in rules:
            if not matches(rule, ev):
                continue
            env = dict(os.environ, GH_WATCH_EVENT=json.dumps(ev, ensure_ascii=False), GH_WATCH_KIND=ev["kind"],
                       GH_WATCH_REPO=ev["repo"], GH_WATCH_PR=str(ev.get("pr") or ""),
                       GH_WATCH_BRANCH=ev.get("branch") or "", GH_WATCH_RESULT=ev.get("result") or "",
                       GH_WATCH_URL=ev.get("url") or "")
            try:
                rc = subprocess.run([str(x) for x in rule["cmd"]], env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                rc = "timeout"
            except OSError:
                rc = "no-cmd"
            log("action on=%s pr=%s rc=%s" % (rule.get("on"), ev.get("pr"), rc))


def wants_notice(ev):
    if ev["kind"] == "ci_done":
        return ev.get("result") == "failure" and ev.get("mine")
    return ev["kind"] == "pr_merged" and not ev.get("by_me")


def notice_text(ev):
    if ev["kind"] == "ci_done":
        jobs = ", ".join(ev.get("jobs") or []) or ev.get("workflow")
        return "CI 실패 PR #%s" % ev.get("pr"), "%s · %s" % (ev.get("title"), jobs)
    return "PR #%s 병합됨" % ev.get("pr"), "%s · 병합 %s" % (ev.get("title"), ev.get("by"))


def notify(events, state, per_hour, log):
    """시간당 상한. 보낸 시각은 state['notified'] 에 남겨 재시작해도 상한 유지. open·activate 는 쓰지 않음(포커스 뺏기 금지)"""
    now = time.time()
    sent_at = [t for t in state.get("notified", []) if t > now - 3600]
    sent = capped = fail = 0
    for ev in events:
        if not wants_notice(ev):
            continue
        if len(sent_at) >= per_hour:
            capped += 1
            continue
        title, body = notice_text(ev)
        try:
            rc = subprocess.run([OSASCRIPT, "-e", "on run argv", "-e",
                                 "display notification (item 2 of argv) with title (item 1 of argv)",
                                 "-e", "end run", title, body], capture_output=True, timeout=10).returncode
        except (OSError, subprocess.TimeoutExpired):
            rc = 1
        if rc == 0:
            sent += 1
            sent_at.append(now)
        else:
            fail += 1
    state["notified"] = sent_at
    if sent or capped or fail:
        log("notify sent=%d capped=%d fail=%d" % (sent, capped, fail))


def dispatch(events, state, conf, actions_path, log):
    if not events:
        return
    for step in (lambda: to_board(events, log),
                 lambda: run_actions(events, actions_path, conf["GH_WATCH_ACTION_TIMEOUT"], log),
                 lambda: notify(events, state, conf["GH_WATCH_NOTIFY_PER_HOUR"], log)):
        try:
            step()
        except Exception as e:  # 처리기 하나의 예기치 않은 오류가 감시 루프를 멈추지 않게
            log("sink error=%s" % type(e).__name__)
