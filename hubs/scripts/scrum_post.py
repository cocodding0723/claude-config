#!/usr/bin/env python3
"""평일 스크럼 본인 칸 초안을 claude 로 쓰고 Slack 봇 DM 1건으로 보냄. launchd(ai.lrm.scrum)가 scrum_run.sh 로 호출.
흐름: 평일·날짜 잠금 확인 → scrum_collect.py 재료 수집 → claude -p 를 모델 순서대로 시도(양식 검증 통과분 채택)
      → 초안 파일 저장 → slack_notify.sh --kind doc 1회(초안 파일 첨부). 모델이 전부 실패하면 --kind warn 1건.
Confluence·Jira 는 읽기만(초안 작성 claude 에 읽기 도구만 허용). 쓰기 없음.
사용: scrum_post.py [--dry-run] [--date YYYY-MM-DD] [--force] [--out 파일]
  --dry-run  Slack·상태 파일 없이 초안을 파일(<draft-dir>/<날짜>.dry.md)과 표준 출력에 냄
  --date     초안 날짜(기본 오늘). 시험·재현용
  --force    평일·'오늘 이미 보냄' 검사를 건너뜀(수동 재실행)
  --out      초안 저장 경로 덮어쓰기
종료 코드: 0 발송·건너뜀·dry-run 성공, 1 초안 작성 또는 발송 실패(warn·로컬 알림 시도함), 2 인자 오류
"""
import argparse
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import scrum_collect as collect

# ── 설정(값은 이 블록 한 곳. LRM_SCRUM_* 환경 변수로 덮어씀) ─────────────────────────────────
HERE = Path(__file__).resolve().parent
DIR = collect.DIR
STATE = DIR / "scrum.state.json"
LOCK = DIR / "scrum.lock"
LOCK_STALE_SEC = 1800  # 이보다 오래된 잠금은 죽은 실행의 잔재로 보고 지움
DRAFT_DIR = Path(os.environ.get("LRM_SCRUM_DRAFT_DIR", Path.home() / "Documents/90_정리기록/scrum-draft"))
LOG = Path(os.environ.get("LRM_SCRUM_LOG", Path.home() / "orca/archive/scrum_post.log"))
ENV_FILE = DIR / "scrum.env"  # SCRUM_NAME=<회의록 본인 칸 제목>. 예시는 hubs/scrum.env.example


def load_name():
    """본인 칸 제목. 환경 변수 LRM_SCRUM_NAME > scrum.env 의 SCRUM_NAME. 없으면 빈 문자열"""
    if os.environ.get("LRM_SCRUM_NAME"):
        return os.environ["LRM_SCRUM_NAME"]
    try:
        for ln in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if ln.strip().startswith("SCRUM_NAME="):
                return ln.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


NAME = load_name()
MODELS = [m for m in os.environ.get("LRM_SCRUM_MODELS", "claude-fable-5-1,claude-fable-5-2,claude-opus-5-5").split(",") if m]
CLAUDE = os.environ.get("LRM_CLAUDE", "claude")
NOTIFY = os.environ.get("LRM_NOTIFY", str(DIR / "slack_notify.sh"))
OSA = os.environ.get("LRM_SCRUM_OSA", "osascript")
COLLECT = os.environ.get("LRM_SCRUM_COLLECT", str(HERE / "scrum_collect.py"))
PROMPT = Path(os.environ.get("LRM_SCRUM_PROMPT", HERE / "scrum-prompt.md"))
# 초안 작성 claude 도구 제한. --tools "" 로 내장 도구(Bash·Read·Edit·WebFetch 등)를 모두 끄고,
# --allowedTools 는 자동 승인 목록일 뿐이라 dontAsk 로 목록 밖 호출을 거부하게 함. 기존 설정에 허용된 권한이 남지 않게 함.
READ_TOOLS = ("mcp__atlassian__getAccessibleAtlassianResources", "mcp__atlassian__atlassianUserInfo",
              "mcp__atlassian__searchJiraIssuesUsingJql", "mcp__atlassian__getJiraIssue",
              "mcp__atlassian__searchConfluence", "mcp__atlassian__getConfluenceContent")
# dontAsk 에 더하는 2중 방어: Atlassian 쓰기·범용 실행 도구 명시 거부
DENY_TOOLS = ("Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch", "Task",
              "mcp__atlassian__createJiraIssue", "mcp__atlassian__editJiraIssue",
              "mcp__atlassian__transitionJiraIssue", "mcp__atlassian__addOrEditJiraIssueComment",
              "mcp__atlassian__createConfluenceContent", "mcp__atlassian__updateConfluenceContent",
              "mcp__atlassian__addGraphContext", "mcp__atlassian__executeWrite", "mcp__atlassian__executeDestructive")
CLAUDE_TIMEOUT = int(os.environ.get("LRM_SCRUM_CLAUDE_TIMEOUT", "900"))
MAX_LINES = 30  # 칸 길이 상한(11-12 실측 25줄 안팎에 여유)
RETRY_WAITS = [int(x) for x in os.environ.get("LRM_SCRUM_RETRY_WAITS", "30,90").split(",") if x]  # 발송 API 실패(종료 코드 1)만 재시도
NET_HOST = os.environ.get("LRM_SCRUM_NET_HOST", "api.anthropic.com")
NET_WAIT_SEC = int(os.environ.get("LRM_SCRUM_NET_WAIT", "120"))  # 깨어난 직후 네트워크가 붙기를 기다리는 최대 시간
SLACK_TAG = "scrum"
NO_ATLASSIAN = "@@atlassian-unavailable@@"  # 초안 작성 claude 가 Jira·Confluence 를 못 읽었을 때 맨 끝에 붙이는 표시
NOTIFY_EXIT_API = 1  # slack_notify.sh: 1 API 실패, 2 설정·양식 오류
# ───────────────────────────────────────────────────────────────────────────────────────────


def log(msg):
    line = "%s %s" % (dt.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, file=sys.stderr)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state():
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s.setdefault("sent", {})
    s.setdefault("warned", {})
    return s


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def take_lock():
    """동시 실행 방지. 잡으면 True"""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - LOCK.stat().st_mtime < LOCK_STALE_SEC:
                    return False
                LOCK.unlink()
            except OSError:
                return False
    return False


def wait_network():
    deadline = time.time() + NET_WAIT_SEC
    while NET_WAIT_SEC > 0:
        try:
            socket.create_connection((NET_HOST, 443), timeout=5).close()
            return True
        except OSError:
            if time.time() >= deadline:
                log("네트워크 대기 %d초 초과 — 그대로 진행" % NET_WAIT_SEC)
                return False
            time.sleep(5)
    return True


def build_prompt(date, since, material):
    text = PROMPT.read_text(encoding="utf-8")
    prev = collect.prev_workday(date)
    for key, val in (("NAME", NAME), ("DATE", date.isoformat()), ("PREV_DATE", prev.isoformat()),
                     ("SINCE", since.strftime("%Y-%m-%d %H:%M")), ("SINCE_DATE", since.date().isoformat()),
                     ("MAX_LINES", str(MAX_LINES)), ("NO_ATLASSIAN", NO_ATLASSIAN), ("INPUT", material)):
        text = text.replace("{{%s}}" % key, val)
    return text


def validate(raw):
    """(칸 본문, Atlassian 못 읽음 여부, 사유). 본문이 None 이면 사유를 남기고 다음 모델로 넘어감"""
    no_atl = NO_ATLASSIAN in raw
    lines = [ln.rstrip() for ln in raw.replace(NO_ATLASSIAN, "").splitlines() if not ln.strip().startswith("```")]
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "# %s" % NAME), None)
    if start is None:
        return None, no_atl, "'# %s' 제목 없음" % NAME
    body = lines[start:]
    nxt = next((i for i, ln in enumerate(body[1:], 1) if ln.startswith("# ")), None)  # 다른 사람 칸이 딸려 오면 잘라냄
    body = body[:nxt] if nxt else body
    body = [ln for ln in body if not ln.strip() or ln.startswith(("#", "-", " "))]  # 끝에 붙은 설명 문장 제거
    while body and not body[-1].strip():
        body.pop()
    if not any(ln.lstrip().startswith("- ") for ln in body):
        return None, no_atl, "불릿 없음"
    if len(body) > MAX_LINES:
        return None, no_atl, "%d줄(상한 %d)" % (len(body), MAX_LINES)
    return "\n".join(body) + "\n", no_atl, ""


def ask_claude(model, prompt):
    """(출력, 사유). 실패면 출력 None"""
    cmd = [CLAUDE, "-p", "--model", model, "--output-format", "text", "--tools", "",
           "--permission-mode", "dontAsk", "--allowedTools", ",".join(READ_TOOLS),
           "--disallowedTools", ",".join(DENY_TOOLS)]
    try:
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
                             cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return None, "%d초 시간 초과" % CLAUDE_TIMEOUT
    except OSError as e:
        return None, "claude 실행 불가(%s) — PATH 확인" % e
    if out.returncode != 0:
        tail = (out.stderr.strip() or out.stdout.strip())[-160:].replace("\n", " ")
        why = "사용 한도" if re.search(r"(?i)limit|quota|한도", tail) else "종료 코드 %d" % out.returncode
        return None, "%s: %s" % (why, tail)
    if not out.stdout.strip():
        return None, "출력 비어 있음"
    return out.stdout, ""


def write_draft(text, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_draft(date, since, material):
    """모델을 순서대로 시도. (초안, 쓴 모델, 실패 사유 목록, Atlassian 못 읽음 여부)"""
    prompt = build_prompt(date, since, material)
    fails = []
    for m in MODELS:
        raw, why = ask_claude(m, prompt)
        body, no_atl = None, False
        if raw is not None:
            body, no_atl, why = validate(raw)
        if body:
            return body, m, fails, no_atl
        fails.append("%s: %s" % (m, why))
        log("모델 실패 — %s" % fails[-1])
    return None, "", fails, False


def topics(draft):
    out = []
    for ln in draft.splitlines():
        m = re.match(r"- \*\*(.+?)\*\*", ln)
        if m:
            out.append(m.group(1))
    return out


def notify(args, retry=True):
    """slack_notify.sh 호출. API 실패(1)만 대기 후 재시도. (종료 코드, 출력)"""
    waits = [0] + (RETRY_WAITS if retry else [])
    rc, text = 0, ""
    for i, w in enumerate(waits):
        time.sleep(w)
        out = subprocess.run([NOTIFY, *args], capture_output=True, text=True, timeout=120)
        rc, text = out.returncode, (out.stdout + out.stderr).strip()
        if rc != NOTIFY_EXIT_API:
            break
        if i + 1 < len(waits):
            log("Slack API 실패 — %d초 뒤 재시도" % waits[i + 1])
    return rc, text


def local_alert(msg):
    try:
        subprocess.run([OSA, "-e", 'display notification "%s" with title "스크럼 초안"' % msg.replace('"', "'")],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def summary_message(date, since, draft, model, missed, no_atl):
    ts = topics(draft)
    n = sum(1 for ln in draft.splitlines() if ln.startswith("    - "))
    lines = ["%s 스크럼 초안 도착" % date.strftime("%m-%d"),
             "• 주제 %d개 · 세부 %d줄. 전체는 첨부 파일" % (len(ts), n),
             "• 주제: %s" % " · ".join(ts)]
    if missed:
        lines.append("• 경고: %s 초안이 안 나갔음(그날 Mac 이 자고 있었을 수 있음)" % missed)
    if no_atl:
        lines.append("• 경고: Jira·Confluence 를 못 읽어 PR·커밋·허브 보고만으로 씀(Atlassian MCP 인증 필요)")
    lines += ["", "참고: 모델 %s · 범위 %s 이후" % (model, since.strftime("%m-%d %H:%M"))]
    return "\n".join(lines)


def failure_message(date, fails):
    why = " / ".join(fails)[:300] or "재료 수집 실패"
    return "\n".join(["스크럼 초안 작성 실패",
                      "• 무엇: %s 스크럼 초안을 못 만들었음" % date.strftime("%m-%d"),
                      "• 왜: %s" % why,
                      "• 결과: 초안·발송 없음. 오늘 칸은 직접 작성 필요",
                      "👉 답: 원인 확인 뒤 수동 재실행(scrum_run.sh --force)할지 알려 주세요",
                      "참고: 로그 %s" % LOG])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Slack·상태 없이 초안만 파일·표준 출력")
    ap.add_argument("--date", help="초안 날짜 YYYY-MM-DD(기본 오늘)")
    ap.add_argument("--force", action="store_true", help="평일·오늘 이미 보냄 검사 생략")
    ap.add_argument("--out", help="초안 저장 경로")
    a = ap.parse_args()
    if not NAME:
        print("본인 칸 제목 없음 — %s 에 SCRUM_NAME=<회의록 본인 칸 제목> 을 쓰거나 LRM_SCRUM_NAME 을 지정" % ENV_FILE, file=sys.stderr)
        return 2
    try:
        date = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    except ValueError:
        print("날짜 형식 오류: %s — YYYY-MM-DD 로 지정" % a.date, file=sys.stderr)
        return 2

    if not a.force and not collect.is_workday(date):
        log("%s 는 주말·공휴일 — 건너뜀" % date)
        return 0
    state = load_state()
    if not a.force and not a.dry_run and date.isoformat() in state["sent"]:
        log("%s 초안은 이미 보냄 — 건너뜀" % date)
        return 0
    if not a.dry_run and not take_lock():
        log("다른 실행이 진행 중 — 건너뜀")
        return 0
    try:
        return run(a, date, state)
    finally:
        if not a.dry_run:
            try:
                LOCK.unlink()
            except OSError:
                pass


def run(a, date, state):
    prev = collect.prev_workday(date)
    since = dt.datetime.combine(prev, dt.time(collect.CUTOFF_HOUR)).astimezone()
    missed = prev.strftime("%m-%d") if state["sent"] and prev.isoformat() not in state["sent"] else ""
    t0 = time.time()
    wait_network()
    t1 = time.time()
    mat = subprocess.run([sys.executable, COLLECT, "--date", date.isoformat()], capture_output=True, text=True)
    log("네트워크 대기 %d초 · 재료 수집 %d초" % (t1 - t0, time.time() - t1))
    fails = []
    draft, model, no_atl = None, "", False
    if mat.returncode != 0:
        fails.append("재료 수집 실패: %s" % (mat.stderr.strip()[-160:] or mat.returncode))
    else:
        draft, model, fails, no_atl = make_draft(date, since, mat.stdout)
    if draft is None:
        log("초안 실패 — %s" % "; ".join(fails))
        if a.dry_run:
            print("초안 실패: %s" % "; ".join(fails), file=sys.stderr)
            return 1
        if date.isoformat() not in state["warned"]:  # 같은 날 경고는 1건
            rc, text = notify(["--kind", "warn", "--tag", SLACK_TAG, failure_message(date, fails)])
            if rc == 0:
                state["warned"][date.isoformat()] = int(time.time())
                save_state(state)
            else:
                log("warn 발송 실패 rc=%d %s" % (rc, text[:120]))
                local_alert("초안 작성 실패 — 로그 확인")
        return 1

    path = Path(a.out) if a.out else DRAFT_DIR / ("%s.%smd" % (date, "dry." if a.dry_run else ""))
    write_draft(draft, path)
    if a.dry_run:
        print(draft, end="")
        log("dry-run 완료 — 모델 %s, 초안 %s%s" % (model, path, " (Jira·Confluence 못 읽음)" if no_atl else ""))
        return 0
    rc, text = notify(["--kind", "doc", "--tag", SLACK_TAG, summary_message(date, since, draft, model, missed, no_atl), str(path)])
    if rc != 0:
        log("Slack 발송 실패 rc=%d %s — 초안 파일 %s 남김" % (rc, text[:160], path))
        local_alert("Slack 발송 실패. 초안: %s" % path)
        return 1
    state["sent"][date.isoformat()] = {"at": int(time.time()), "model": model}
    save_state(state)
    log("발송 완료 — 모델 %s, 초안 %s" % (model, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
