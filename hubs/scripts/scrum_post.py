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
import fcntl
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
SOURCES = ("prs", "commits", "hub_reports", "handoffs", "instructions")  # scrum_collect.collect 의 출처 키
REQUIRED_SOURCES = ("prs", "commits")  # 둘 다 못 모이면 초안이 근거 없음
# Slack 본문·경고·로그에 나가면 안 되는 문자열. 첨부 초안은 비밀만, 본문 주제는 내부 번호도 거름
SECRET_RE = re.compile(r"(?i)\b(?:token|secret|passw(?:or)?d|api[_-]?key|authorization|credential)s?\b\s*[:=]"
                       r"|bearer\s+[A-Za-z0-9._~+/=-]{8,}|\bxox[abprs]-|\bsk-[A-Za-z0-9]{8,}|\bgh[pousr]_[A-Za-z0-9]{10,}"
                       r"|\bAKIA[0-9A-Z]{12,}|-----BEGIN|\beyJ[A-Za-z0-9_-]{15,}\.")
INTERNAL_ID_RE = re.compile(r"\b(?:LRM-\d+|D-[A-Za-z0-9]{1,16}|T-[A-Za-z0-9]{1,16}|ctx_[0-9a-f]+|task_[0-9a-f]+|term_[0-9a-f-]+)\b")
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
    """동시 실행 방지. OS 파일 잠금(flock)이라 프로세스가 죽으면 커널이 풀어 줌. 잡으면 fd, 못 잡으면 None
    파일 존재·mtime 으로 판정하지 않음: 살아 있는 장기 실행의 잠금을 시간 경과로 오판해 지우는 경합 방지"""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, ("%d\n" % os.getpid()).encode())
    return fd


def release_lock(fd):
    """파일은 지우지 않음. 지우면 대기하던 쪽이 지워진 inode 에 잠금을 잡는 경합이 생김"""
    os.close(fd)


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


def redact(text):
    """로그용: 비밀 패턴이 있으면 그 줄 전체를 가림"""
    return "[민감 문자열 가림]" if SECRET_RE.search(text) else text


def parse_material(text):
    """수집기 JSON → (누락 출처 키 목록, 실패 사유 또는 ''). errors 항목은 '<출처>: <원문>' 이며 원문은 Slack 으로 내보내지 않음.
    필수 입력(prs·commits)이 둘 다 못 모였거나 전 출처가 비면서 오류가 있으면 실패: 인증 실패를 '조용한 날'로 오판하지 않기 위함"""
    try:
        data = json.loads(text)
    except ValueError:
        return [], "collect_bad_json"
    missing = []
    for e in data.get("errors") or []:
        key = str(e).split(":", 1)[0].strip()
        if key and key not in missing:
            missing.append(key)
    if all(k in missing for k in REQUIRED_SOURCES):
        return missing, "collect_required_missing"
    if missing and not any(data.get(k) for k in SOURCES):
        return missing, "collect_all_empty"
    return missing, ""


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
        return None, no_atl, "heading_missing"
    body = lines[start:]
    nxt = next((i for i, ln in enumerate(body[1:], 1) if ln.startswith("# ")), None)  # 다른 사람 칸이 딸려 오면 잘라냄
    body = body[:nxt] if nxt else body
    body = [ln for ln in body if not ln.strip() or ln.startswith(("#", "-", " "))]  # 끝에 붙은 설명 문장 제거
    while body and not body[-1].strip():
        body.pop()
    if not any(ln.lstrip().startswith("- ") for ln in body):
        return None, no_atl, "no_bullet"
    if len(body) > MAX_LINES:
        return None, no_atl, "too_long(%d>%d)" % (len(body), MAX_LINES)
    text = "\n".join(body) + "\n"
    if SECRET_RE.search(text):
        return None, no_atl, "sensitive_text"  # 초안은 첨부 파일로 나가므로 비밀 패턴이 있으면 다음 모델로
    return text, no_atl, ""


def ask_claude(model, prompt):
    """(출력, 사유). 실패면 출력 None"""
    cmd = [CLAUDE, "-p", "--model", model, "--output-format", "text", "--tools", "",
           "--permission-mode", "dontAsk", "--allowedTools", ",".join(READ_TOOLS),
           "--disallowedTools", ",".join(DENY_TOOLS)]
    try:
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
                             cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as e:
        log("claude 실행 불가(%s) — PATH 확인" % e)
        return None, "cli_missing"
    if out.returncode != 0:
        tail = (out.stderr.strip() or out.stdout.strip())[-160:].replace("\n", " ")
        log("claude 오류 원문(로컬 로그만): %s" % redact(tail))
        return None, "usage_limit" if re.search(r"(?i)limit|quota|한도", tail) else "exit_%d" % out.returncode
    if not out.stdout.strip():
        return None, "empty_output"
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
            out.append(" ".join(INTERNAL_ID_RE.sub("", m.group(1)).split()) or "(주제)")  # 내부 번호는 첨부에만 둠
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


def summary_message(date, since, draft, model, missed, no_atl, lost=()):
    ts = topics(draft)
    n = sum(1 for ln in draft.splitlines() if ln.startswith("    - "))
    lines = ["%s 스크럼 초안 도착" % date.strftime("%m-%d"),
             "• 주제 %d개 · 세부 %d줄. 전체는 첨부 파일" % (len(ts), n),
             "• 주제: %s" % " · ".join(ts)]
    if missed:
        lines.append("• 경고: %s 초안이 안 나갔음(그날 Mac 이 자고 있었을 수 있음)" % missed)
    if no_atl:
        lines.append("• 경고: Jira·Confluence 를 못 읽어 PR·커밋·허브 보고만으로 씀(Atlassian MCP 인증 필요)")
    if lost:
        lines.append("• 경고: 재료 수집 누락(%s) — 해당 출처 내용이 초안에 빠졌을 수 있음" % ", ".join(lost))
    lines += ["", "참고: 모델 %s · 범위 %s 이후" % (model, since.strftime("%m-%d %H:%M"))]
    return "\n".join(lines)


def failure_message(date, fails):
    why = redact(" / ".join(fails)[:300]) or "collect_failed"
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
    fd = None
    if not a.dry_run:
        fd = take_lock()
        if fd is None:
            log("다른 실행이 진행 중 — 건너뜀")
            return 0
    try:
        state = load_state()  # 잠금을 잡은 뒤에 읽음: 앞선 실행의 sent 기록을 놓치지 않음
        if not a.force and not a.dry_run and date.isoformat() in state["sent"]:
            log("%s 초안은 이미 보냄 — 건너뜀" % date)
            return 0
        return run(a, date, state)
    finally:
        if fd is not None:
            release_lock(fd)


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
    draft, model, no_atl, lost = None, "", False, []
    if mat.returncode != 0:
        fails.append("collect_failed(rc=%d)" % mat.returncode)
        log("재료 수집 실패: %s" % mat.stderr.strip()[-160:])
    else:
        lost, why = parse_material(mat.stdout)
        if lost:
            log("재료 수집 누락: %s" % ", ".join(lost))
        if why:
            fails.append("%s(%s)" % (why, ", ".join(lost)))
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
                log("warn 발송 실패 rc=%d %s" % (rc, redact(text[:120])))
                local_alert("초안 작성 실패 — 로그 확인")
        return 1

    path = Path(a.out) if a.out else DRAFT_DIR / ("%s.%smd" % (date, "dry." if a.dry_run else ""))
    write_draft(draft, path)
    if a.dry_run:
        print(draft, end="")
        log("dry-run 완료 — 모델 %s, 초안 %s%s" % (model, path, " (Jira·Confluence 못 읽음)" if no_atl else ""))
        return 0
    rc, text = notify(["--kind", "doc", "--tag", SLACK_TAG, summary_message(date, since, draft, model, missed, no_atl, lost), str(path)])
    if rc != 0:
        log("Slack 발송 실패 rc=%d %s — 초안 파일 %s 남김" % (rc, redact(text[:160]), path))
        local_alert("Slack 발송 실패. 초안: %s" % path)
        return 1
    state["sent"][date.isoformat()] = {"at": int(time.time()), "model": model}
    save_state(state)
    log("발송 완료 — 모델 %s, 초안 %s" % (model, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
