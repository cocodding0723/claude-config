#!/usr/bin/env python3
"""작업 공간 직접 생성·봇 토큰 접근 판정(PreToolUse Bash|Read|Grep 훅). stdin: hook JSON. 출력: 거부면 hook JSON 한 줄, 통과면 빈 출력.
- git worktree add: 모든 세션 거부. 작업 공간은 create_worker_ws.sh·Orca worker-start 로만 만듦(스크립트 안의 git 은 훅이 보지 않음).
- slack-bot.env: Read·Grep 경로, Bash 명령 어디에든 나오면 모든 세션 거부(토큰 노출 차단).
- slack_notify.sh 실행: 작업자 세션만 거부, 보고는 hub_report.py. 읽기·편집·git add 같은 비실행 언급은 통과.
작업자 판별: ORCA_ROLE 이 있으면 worker 만 작업자. 없으면 메인이 아니고, 연결 작업 공간(git-dir ≠ common-dir)이며, 폴더 이름이 허브(coord-*)가 아닌 세션.
  Orca agent launch 는 환경변수를 못 넘겨 경로로 판별함(main_role.py 와 같은 사정).
Bash 의 worktree·slack_notify.sh 판정은 따옴표·heredoc 안 문구를 지운 명령으로(커밋 메시지 속 단어에 속지 않게), slack-bot.env 는 원문으로(따옴표 경로 우회 방지)."""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import main_role  # noqa: E402
import main_send_guard  # noqa: E402

# ---- 설정 ----
TOKEN_RE = re.compile(r"slack-bot\.env(?!\.example)")  # 값 없는 양식 파일(.example)은 읽어도 됨
NOTIFY_SCRIPT = "slack_notify.sh"
HUB_DIR_RE = re.compile(r"^coord-.+")
FILE_TOOLS = {"Read": ("file_path",), "Grep": ("path", "glob")}
WRAPPERS = {"sudo", "env", "exec", "command", "nohup", "time", "nice", "builtin", "caffeinate"}
RUNNERS = {"source", "."} | main_send_guard.SHELLS
WT_ADD = re.compile(r"(^|[\s;&|(`\"'])git\s+((-C|-c)\s+(\"[^\"]*\"|'[^']*'|\S+)\s+|-\S+\s+)*worktree\s+add\b")
REASON_WORKTREE = ("작업 공간을 직접 만들지 않음(git worktree add 거부) — 하위 작업 공간은 create_worker_ws.sh, "
                   "작업자 배정은 orca orchestration worker-start 로 만들어 부모 허브에 연결함")
REASON_TOKEN = "봇 토큰 파일(slack-bot.env)은 세션에서 읽지 않음 — 전송은 report_sender 등 전송 프로세스가 맡음"
REASON_NOTIFY = "작업자는 slack_notify.sh 를 직접 실행하지 않음 — 보고는 hub_report.py 로 outbox 에 남기면 전송기가 올림"


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}, ensure_ascii=False))


def stripped(cmd):
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "strip_text.py")], input=cmd, capture_output=True, text=True, timeout=5)
        return r.stdout if r.returncode == 0 and r.stdout.strip() else cmd
    except (OSError, subprocess.SubprocessError):
        return cmd


def git_dirs(cwd):
    out = []
    for flag in ("--git-dir", "--git-common-dir"):
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", flag], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        out.append(os.path.realpath(r.stdout.strip()))
    return out


def is_worker(ev):
    role = os.environ.get("ORCA_ROLE", "")
    if role:
        return role == "worker"
    cwd = ev.get("cwd") or ""
    if main_role.is_main(ev) or not os.path.isdir(cwd):
        return False
    dirs = git_dirs(cwd)
    if not dirs or dirs[0] == dirs[1]:
        return False  # 메인 체크아웃·저장소 밖
    top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5).stdout.strip()
    return not HUB_DIR_RE.match(os.path.basename(top))


def runs_notify(cmd):
    """단계 첫 낱말(래퍼·셸·source 뒤 첫 인자 포함)이 slack_notify.sh 인지"""
    for seg in main_send_guard.segments(cmd):
        i = 0
        while i < len(seg) and (re.fullmatch(r"[A-Za-z_]\w*=.*", seg[i]) or seg[i].rsplit("/", 1)[-1] in WRAPPERS):
            i += 1
        if i < len(seg) and seg[i].rsplit("/", 1)[-1] in RUNNERS:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 1
        if i < len(seg) and seg[i].rsplit("/", 1)[-1] == NOTIFY_SCRIPT:
            return True
    return False


def judge(ev):
    tool = ev.get("tool_name") or ""
    ti = ev.get("tool_input") or {}
    if tool in FILE_TOOLS:
        paths = [str(ti.get(k) or "") for k in FILE_TOOLS[tool]]
        paths += [os.path.realpath(p) for p in paths if p and not p.startswith("*")]
        return REASON_TOKEN if any(TOKEN_RE.search(p) for p in paths) else None
    cmd = ti.get("command") or ""
    if TOKEN_RE.search(cmd):
        return REASON_TOKEN
    clean = stripped(cmd)
    if WT_ADD.search(clean):
        return REASON_WORKTREE
    if NOTIFY_SCRIPT in clean and runs_notify(clean) and is_worker(ev):
        return REASON_NOTIFY
    return None


def main():
    try:
        ev = json.load(sys.stdin)
        reason = judge(ev)
    except Exception as e:  # 훅 오류가 작업을 막지 않게 통과 처리
        print(f"ws-token-guard: 판정 실패 — 통과 처리 ({e})", file=sys.stderr)
        return
    if reason:
        deny(reason)


if __name__ == "__main__":
    main()
