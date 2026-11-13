#!/usr/bin/env python3
"""평일 스크럼 본인 칸 초안을 claude 로 쓰고 Slack 봇 DM 1건으로 보냄. launchd(ai.lrm.scrum)가 scrum_run.sh 로 호출.
흐름: 평일·날짜 잠금 확인 → scrum_collect.py 재료 수집 → claude -p 를 모델 순서대로 시도(양식 검증 통과분 채택)
      → 초안 파일 저장 → slack_notify.sh --kind doc 1회(초안 파일 첨부). 모델이 전부 실패하면 --kind warn 1건.
      오늘·어제 만든 이미지·영상이 있으면(최대 5개) 요약 글 1건을 먼저 올리고 초안 파일과 첨부를 그 스레드에 답글로 올림.
Confluence·Jira 는 읽기만(초안 작성 claude 에 읽기 도구만 허용). 쓰기 없음.
사용: scrum_post.py [--dry-run] [--date YYYY-MM-DD] [--force] [--out 파일]
  --dry-run  Slack·상태 파일 없이 초안을 파일(<draft-dir>/<날짜>.dry.md)과 표준 출력에 냄
  --date     초안 날짜(기본 오늘). 시험·재현용
  --force    평일·'오늘 이미 보냄' 검사를 건너뜀(수동 재실행)
  --out      초안 저장 경로 덮어쓰기
  --preflight  발송·초안 작성 없이 gh 인증·claude 로그인·Atlassian MCP 연결·Slack 봇 스크립트 존재만 점검(토큰 값은 출력 안 함)
종료 코드: 0 발송·건너뜀·dry-run·preflight 성공, 1 초안 작성 또는 발송 실패(warn·로컬 알림 시도함), 2 인자 오류
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

# 설정: 값은 이 블록 한 곳, LRM_SCRUM_* 환경 변수로 덮어씀
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
# 초안 작성 claude 도구 제한. --tools "" 로 내장 도구 제거. --allowedTools 는 자동 승인 목록일 뿐이라 dontAsk 와 함께 써야 목록 밖이 거부됨
READ_TOOLS = ("mcp__atlassian__getAccessibleAtlassianResources", "mcp__atlassian__atlassianUserInfo",
              "mcp__atlassian__searchJiraIssuesUsingJql", "mcp__atlassian__getJiraIssue",
              "mcp__atlassian__searchConfluence", "mcp__atlassian__getConfluenceContent")
# dontAsk 와 별개로 쓰기·실행 도구를 명시 거부
DENY_TOOLS = ("Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch", "Task",
              "mcp__atlassian__createJiraIssue", "mcp__atlassian__editJiraIssue",
              "mcp__atlassian__transitionJiraIssue", "mcp__atlassian__addOrEditJiraIssueComment",
              "mcp__atlassian__createConfluenceContent", "mcp__atlassian__updateConfluenceContent",
              "mcp__atlassian__addGraphContext", "mcp__atlassian__executeWrite", "mcp__atlassian__executeDestructive")
# 초안 claude 전용 MCP 설정. --strict-mcp-config 와 함께 써서 사용자·프로젝트 MCP 서버를 전부 배제함(인증은 키체인의 같은 서버 이름·URL 항목을 씀)
ATLASSIAN_MCP = {"mcpServers": {"atlassian": {"type": "http", "url": "https://mcp.atlassian.com/v2/mcp"}}}
MCP_CONFIG = os.environ.get("LRM_SCRUM_MCP_CONFIG") or json.dumps(ATLASSIAN_MCP)  # JSON 문자열 또는 파일 경로(시험에서 가짜 MCP 로 교체)
CLAUDE_TIMEOUT = int(os.environ.get("LRM_SCRUM_CLAUDE_TIMEOUT", "900"))
MAX_LINES = 45  # 칸 길이 상한(11-12 실측 25줄 + 진행 상세·PR·막힘 줄)
RETRY_WAITS = [int(x) for x in os.environ.get("LRM_SCRUM_RETRY_WAITS", "30,90").split(",") if x]  # Slack 이 거절한 발송만 재시도
NET_HOST = os.environ.get("LRM_SCRUM_NET_HOST", "api.anthropic.com")
NET_WAIT_SEC = int(os.environ.get("LRM_SCRUM_NET_WAIT", "120"))  # 깨어난 직후 네트워크가 붙기를 기다리는 최대 시간
SOURCES = ("prs", "commits", "hub_reports", "handoffs", "instructions")  # scrum_collect.collect 의 출처 키. hub_progress 는 보강 출처라 '전 출처 빔' 판정에서 뺌
LOST_LABEL = {"commits_partial": "commits 일부 워크트리"}  # 경고 줄에 쓸 이름
REQUIRED_SOURCES = ("prs", "commits")  # 둘 다 못 모이면 초안이 근거 없음
# Slack 본문·경고·로그에 나가면 안 되는 문자열. 첨부 초안은 비밀만, 본문 주제는 내부 번호도 거름
SECRET_RE = re.compile(r"(?i)\b(?:token|secret|passw(?:or)?d|api[_-]?key|authorization|credential)s?\b\s*[:=]"
                       r"|bearer\s+[A-Za-z0-9._~+/=-]{8,}|\bxox[abprs]-|\bsk-[A-Za-z0-9]{8,}|\bgh[pousr]_[A-Za-z0-9]{10,}"
                       r"|\bAKIA[0-9A-Z]{12,}|-----BEGIN|\beyJ[A-Za-z0-9_-]{15,}\.")
INTERNAL_ID_RE = re.compile(r"\b(?:LRM-\d+|D-[A-Za-z0-9]{1,16}|T-[A-Za-z0-9]{1,16}|ctx_[0-9a-f]+|task_[0-9a-f]+|term_[0-9a-f-]+)\b")
SLACK_TAG = "scrum"
NO_ATLASSIAN = "@@atlassian-unavailable@@"  # 초안 작성 claude 가 Jira·Confluence 를 못 읽었을 때 맨 끝에 붙이는 표시
PREFLIGHT_TIMEOUT = int(os.environ.get("LRM_SCRUM_PREFLIGHT_TIMEOUT", "60"))
NOTIFY_TIMEOUT = int(os.environ.get("LRM_SCRUM_NOTIFY_TIMEOUT", "120"))
NOTIFY_RC_TIMEOUT = -1  # 시간 초과: 올라갔는지 모름
NOTIFY_RC_OS = -2  # 실행 불가: 미전송
NOTIFY_RC_UNKNOWN = -3  # Slack 오류 코드 없이 실패, 발송 기록도 없음: 올라갔는지 모름
SENT_LOG = DIR / "slack-sent.jsonl"  # slack_notify.sh 가 Slack API ok 응답 뒤에만 적는 기록(file_id 포함)
NOTIFY_EXIT_API = 1  # slack_notify.sh: 1 API 실패, 2 설정·양식 오류


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
    s.setdefault("pending", {})  # 전송 의도 기록: 날짜 → {at, path}. 결과 확인 전까지 남김
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
    # --setting-sources "" : 사용자·프로젝트 settings 의 permissions.allow·훅을 물려받지 않게 함(dontAsk 가 allow 목록의 쓰기 MCP 도구를 실행한 사례)
    cmd = [CLAUDE, "-p", "--model", model, "--output-format", "text", "--tools", "",
           "--setting-sources", "", "--strict-mcp-config", "--mcp-config", MCP_CONFIG,
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


def pick_media(text):
    """수집기 JSON 의 첨부 후보 중 지금도 올릴 수 있는 이미지·영상 경로(비밀 파일·크기 초과·없는 파일은 hub_report 규칙으로 다시 걸러냄)"""
    try:
        files = json.loads(text).get("attachments", {}).get("files", [])
    except (ValueError, AttributeError):
        return []
    paths = [f.get("path") for f in files if isinstance(f, dict)] if isinstance(files, list) else []
    return [p for p in paths if isinstance(p, str) and collect.media_kind(p) and not collect.hub_report.file_problem(p)][:collect.MEDIA_MAX_FILES]


def posted_ts(text):
    """slack_notify.sh 가 낸 'ok <ts>' 줄의 메시지 ts. 없으면 빈 문자열"""
    m = re.search(r"^ok (\d{1,12}\.\d{1,9})\s*$", text or "", re.M)
    return m.group(1) if m else ""


def delivered_top(intent):
    """전송 의도 이후 올라간 요약 글(스레드 첨부 방식)의 발송 기록. 없으면 None"""
    for r in collect.jsonl(SENT_LOG):
        if r.get("kind") == "text" and r.get("tag") == SLACK_TAG and r.get("form_kind") == "doc" and r.get("ts") and collect.num(r.get("at")) >= intent["at"]:
            return r
    return None


def delivered(intent):
    """전송 의도 이후 같은 첨부 파일의 발송 기록(slack-sent.jsonl)이 있으면 그 기록, 없으면 None"""
    for r in collect.jsonl(SENT_LOG):
        if r.get("kind") == "file" and r.get("path") == intent["path"] and collect.num(r.get("at")) >= intent["at"] and r.get("file_id"):
            return r
    return None


def notify(args, retry=True, confirm=None):
    """slack_notify.sh 호출. (종료 코드, 출력)
    재시도는 Slack 이 오류 코드로 거절한 경우(미게시 확실)만. 시간 초과·원인 불명의 종료 코드 1 은 올라갔을 수 있으므로
    confirm() 으로 발송 기록을 확인하고, 확인 못 하면 재시도 없이 NOTIFY_RC_UNKNOWN 반환(중복 게시 방지)"""
    waits = [0] + (RETRY_WAITS if retry else [])
    rc, text = 0, ""
    for i, w in enumerate(waits):
        time.sleep(w)
        try:
            out = subprocess.run([NOTIFY, *args], capture_output=True, text=True, timeout=NOTIFY_TIMEOUT)
        except subprocess.TimeoutExpired:
            rc, text = NOTIFY_RC_TIMEOUT, "notify %d초 시간 초과" % NOTIFY_TIMEOUT
        except OSError as e:
            return NOTIFY_RC_OS, "notify 실행 불가(%s)" % e
        else:
            rc, text = out.returncode, (out.stdout + out.stderr).strip()
        refused = rc == NOTIFY_EXIT_API and re.fullmatch(r"[a-z][a-z0-9_]*", text.splitlines()[-1] if text else "")
        if rc == NOTIFY_RC_TIMEOUT or (rc == NOTIFY_EXIT_API and not refused):
            if confirm and confirm():
                return 0, "발송 기록 확인"
            return NOTIFY_RC_UNKNOWN, text
        if rc != NOTIFY_EXIT_API:
            break
        if i + 1 < len(waits):
            log("Slack 이 거절(%s) — %d초 뒤 재시도" % (text, waits[i + 1]))
    return rc, text


def local_alert(msg):
    try:
        subprocess.run([OSA, "-e", 'display notification "%s" with title "스크럼 초안"' % msg.replace('"', "'")],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def summary_message(date, since, draft, model, missed, no_atl, lost=(), media=0):
    ts = topics(draft)
    n = sum(1 for ln in draft.splitlines() if ln.startswith("    - "))
    lines = ["%s 스크럼 초안 도착" % date.strftime("%m-%d"),
             "• 주제 %d개 · 세부 %d줄. 전체는 첨부 파일" % (len(ts), n),
             "• 주제: %s" % " · ".join(ts)]
    if media:
        lines.append("• 첨부 %d개(오늘·어제 만든 화면·영상). 초안 파일과 함께 이 글의 스레드에 있음" % media)
    if missed:
        lines.append("• 경고: %s 초안이 안 나갔음(그날 Mac 이 자고 있었을 수 있음)" % missed)
    if no_atl:
        lines.append("• 경고: Jira·Confluence 를 못 읽어 PR·커밋·허브 보고만으로 씀(Atlassian MCP 인증 필요)")
    if lost:
        names = [LOST_LABEL.get(k, k) for k in lost]
        lines.append("• 경고: 재료 수집 누락(%s) — 해당 출처 내용이 초안에 빠졌을 수 있음" % ", ".join(names))
    lines += ["", "참고: 모델 %s · 범위 %s 이후" % (model, since.strftime("%m-%d %H:%M"))]
    return "\n".join(lines)


def send_threaded(args, path, media, intent):
    """요약 글 → 스레드에 초안 파일 → 첨부 순서로 올림. (종료 코드, 출력). 초안 파일까지 올라가야 0.
    첨부 개별 실패는 로그와 스레드 경고 1건으로 알리고 전체 성공으로 침(초안이 본체)"""
    rc, text = notify(args, confirm=lambda: delivered_top(intent))
    if rc != 0:
        return rc, text
    ts = posted_ts(text) or (delivered_top(intent) or {}).get("ts", "")
    if not ts:
        return NOTIFY_RC_UNKNOWN, "요약 글 ts 확인 불가"
    thread = ["--kind", "doc", "--tag", SLACK_TAG, "--thread", ts]
    rc, text = notify([*thread, "초안 전체", str(path)], confirm=lambda: delivered(intent))
    if rc != 0:
        return rc, text
    failed = 0
    for i, m in enumerate(media, 1):
        r, t = notify([*thread, "첨부 %d/%d" % (i, len(media)), m], retry=False)
        if r != 0:
            failed += 1
            log("첨부 %d/%d 실패 rc=%d %s" % (i, len(media), r, redact(t[:120])))
    if failed:
        notify(["--kind", "warn", "--tag", SLACK_TAG, "--thread", ts,
                "첨부 일부 못 올림\n• 못 올린 첨부 %d개 (크기·권한·네트워크 중 하나)\n• 원본은 로컬에 있음. 로그 %s" % (failed, LOG)], retry=False)
    return 0, text


def failure_message(date, fails):
    why = redact(" / ".join(fails)[:300]) or "collect_failed"
    return "\n".join(["스크럼 초안 작성 실패",
                      "• 무엇: %s 스크럼 초안을 못 만들었음" % date.strftime("%m-%d"),
                      "• 왜: %s" % why,
                      "• 결과: 초안·발송 없음. 오늘 칸은 직접 작성 필요",
                      "👉 답: 원인 확인 뒤 수동 재실행(scrum_run.sh --force)할지 알려 주세요",
                      "참고: 로그 %s" % LOG])


def probe(cmd, ok_re=None):
    """점검용 하위 명령. (성공 여부, 사유 코드). 출력 원문은 버림: 토큰·계정 정보가 화면·로그에 안 남게"""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=PREFLIGHT_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError:
        return False, "command_not_found"
    if out.returncode != 0:
        return False, "exit_%d" % out.returncode
    if ok_re and not re.search(ok_re, out.stdout):
        return False, "unexpected_output"
    return True, ""


def preflight():
    """발송 없이 첫 자동 실행 전제만 점검. 종료 코드: 0 전부 통과, 1 하나 이상 실패"""
    notify = Path(NOTIFY)
    checks = [
        ("본인 칸 제목", (True, "") if NAME else (False, "SCRUM_NAME 없음"),
         "%s 에 SCRUM_NAME 을 쓰거나 LRM_SCRUM_NAME 지정" % ENV_FILE),
        ("gh 인증", probe([collect.GH, "auth", "status"]), "gh auth login"),
        ("claude 로그인", probe([CLAUDE, "auth", "status"], r'"loggedIn":\s*true'), "claude auth login"),
        ("Atlassian MCP 연결", probe([CLAUDE, "mcp", "get", "atlassian"], r"Status:.*Connected"),
         "대화형 claude 에서 /mcp → atlassian 인증"),
        ("Slack 봇 스크립트", (notify.is_file() and os.access(notify, os.X_OK), "없음·실행 권한 없음"),
         "%s 확인(install.sh 재실행)" % notify),
    ]
    bad = 0
    for label, (ok, why), hint in checks:
        print("%s  %s%s" % ("PASS" if ok else "FAIL", label, "" if ok else " — %s. 조치: %s" % (why, hint)))
        bad += not ok
    print("---- preflight %s (발송·상태 기록 없음)" % ("통과" if not bad else "실패 %d건" % bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Slack·상태 없이 초안만 파일·표준 출력")
    ap.add_argument("--date", help="초안 날짜 YYYY-MM-DD(기본 오늘)")
    ap.add_argument("--force", action="store_true", help="평일·오늘 이미 보냄 검사 생략")
    ap.add_argument("--out", help="초안 저장 경로")
    ap.add_argument("--preflight", action="store_true", help="발송 없이 gh·claude·Atlassian MCP·Slack 봇 스크립트만 점검")
    a = ap.parse_args()
    if a.preflight:
        return preflight()
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
        pend = state["pending"].get(date.isoformat())
        if pend and not a.force and not a.dry_run:
            return resolve_pending(date, state, pend)
        return run(a, date, state)
    finally:
        if fd is not None:
            release_lock(fd)


def mark_sent(state, date, model, rec):
    state["sent"][date.isoformat()] = {"at": int(time.time()), "model": model, "file_id": (rec or {}).get("file_id", "")}
    state["pending"].pop(date.isoformat(), None)
    save_state(state)


def resolve_pending(date, state, pend):
    """이전 실행이 결과 불명으로 끝난 날. 발송 기록이 있으면 보낸 것으로 확정, 없으면 중복 방지로 자동 재전송 안 함"""
    rec = delivered(pend)
    if rec:
        mark_sent(state, date, pend.get("model", ""), rec)
        log("%s 이전 전송이 실제로 올라가 있었음 — 보냄으로 확정(file_id %s)" % (date, rec.get("file_id")))
        return 0
    log("%s 이전 전송 결과 불명 — 중복 방지로 건너뜀. Slack DM 에서 초안 첨부를 확인하고, 없으면 scrum_run.sh --force" % date)
    local_alert("스크럼 초안 전송 결과 불명 — Slack 확인 필요")
    return 1


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
    intent = {"at": int(time.time()), "path": str(path), "model": model}
    state["pending"][date.isoformat()] = intent  # 전송 전에 의도를 남겨 중간에 죽어도 재실행이 중복 게시하지 않게 함
    save_state(state)
    media = pick_media(mat.stdout)
    msg = summary_message(date, since, draft, model, missed, no_atl, lost, len(media))
    if media:
        rc, text = send_threaded(["--kind", "doc", "--tag", SLACK_TAG, msg], path, media, intent)
    else:
        rc, text = notify(["--kind", "doc", "--tag", SLACK_TAG, msg, str(path)], confirm=lambda: delivered(intent))
    if rc != 0:
        unknown = rc in (NOTIFY_RC_TIMEOUT, NOTIFY_RC_UNKNOWN)
        if not unknown:
            state["pending"].pop(date.isoformat(), None)
            save_state(state)
        log("Slack 발송 %s rc=%d %s — 초안 파일 %s 남김" % ("결과 불명" if unknown else "실패", rc, redact(text[:160]), path))
        local_alert("Slack 전송 결과 불명 — DM 확인. 초안: %s" % path if unknown else "Slack 발송 실패. 초안: %s" % path)
        return 1
    mark_sent(state, date, model, delivered(intent))
    log("발송 완료 — 모델 %s, 초안 %s" % (model, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
