#!/usr/bin/env python3
"""Codex 훅 → Claude 훅 변환기. Claude 훅 목록(claude/settings.base.json)을 그대로 읽어 해당하는 훅을 모두 실행함.
훅을 추가·삭제하면 Codex 에도 바로 반영되도록 목록을 따로 두지 않음.
Codex 와 다른 점만 여기서 맞춤:
- ask 는 Codex 가 무시하고 명령을 실행하므로 deny 로 바꿈(야간 결재함 기록은 night-ask.sh 가 함)
- apply_patch 는 파일마다 Write/Edit 로, spawn_agent 는 Agent 로 바꿔 전달
- 출력 형식을 어기면 Codex 가 훅 실패로 보고 명령을 실행하므로 허용된 칸만 내보냄
PreToolUse 는 훅이 실패하면 막고(종료 2), 다른 사건은 경고만 남김(종료 1). Stop 의 종료 2 는 대화를 다시 이어 가게 함.
stdin: Codex 훅 JSON. 종료 코드: 0 정상, 1 경고, 2 차단."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BASE = Path(os.environ.get("CODEX_COMPAT_SETTINGS") or Path(__file__).resolve().parents[2] / "claude/settings.base.json")
STATE = Path(os.environ.get("CLAUDE_HOOKS_STATE") or Path.home() / ".claude/hooks/state")
# hooks.base.json 의 timeout 보다 짧게. Codex 가 먼저 끊으면 차단 결과를 못 돌려줌
DEADLINE = {"PreToolUse": 33, "Stop": 145, "SessionEnd": 2}
DEADLINE_DEFAULT = 18
CONTEXT_EVENTS = {"PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart"}  # additionalContext 를 받는 사건
PATCH_BEGIN = "*** Begin Patch"
PATCH_FILE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+?)\s*$")


class HookError(Exception):
    pass


def patch_files(patch, cwd):
    """패치 → [(도구 이름, 입력)]. 새 파일은 Write(내용 포함), 나머지는 Edit"""
    out, body = [], None
    for line in patch.splitlines():
        m = PATCH_FILE.match(line)
        if m:
            path = str((Path(cwd) / m.group(2)).resolve())
            body = [] if m.group(1) == "Add File" else None
            out.append(("Write", {"file_path": path, "content": body}) if body is not None else ("Edit", {"file_path": path}))
        elif line.startswith("*** "):
            body = None
        elif body is not None and line.startswith("+"):
            body.append(line[1:])
    return [(t, {**i, "content": "\n".join(i["content"]) + "\n"} if "content" in i else i) for t, i in out]


def views(payload):
    """Claude 도구 이름·입력으로 바꾼 목록. 첫 항목은 사건마다 한 번 도는 훅(matcher 없음)에 씀"""
    tool = payload.get("tool_name")
    if not tool:
        return [payload]
    inp = payload.get("tool_input") or {}
    if isinstance(inp, str):
        inp = {"command": inp}
    if not isinstance(inp, dict):
        inp = {"input": inp}
    inp = dict(inp)
    cmd = inp.get("command") or inp.get("cmd") or ""
    cwd = payload.get("cwd") or "."
    if tool == "spawn_agent":
        return [{**payload, "tool_name": "Agent", "tool_input": {**inp, "subagent_type": inp.get("agent_type") or "general-purpose"}}]
    files = [{**payload, "tool_name": t, "tool_input": i} for t, i in patch_files(cmd, cwd)] if isinstance(cmd, str) and PATCH_BEGIN in cmd else []
    if tool == "apply_patch":
        if not files and payload.get("hook_event_name") == "PreToolUse":
            raise HookError("apply_patch 에서 파일 경로를 찾지 못함")
        return files or [payload]
    if tool == "Bash":
        return [{**payload, "tool_input": {**inp, "command": cmd}}] + files  # 셸로 부른 apply_patch 도 파일 검사
    return [{**payload, "tool_input": inp}]


def matches(matcher, view):
    """Claude 규칙: 빈 값·* 는 전부, 낱말 목록(A|B)은 정확히, 그 밖은 정규식"""
    if matcher in ("", "*"):
        return True
    subject = str(view.get("tool_name") or view.get("source") or view.get("trigger") or "")
    if re.fullmatch(r"[\w|]+", matcher):
        return subject in matcher.split("|")
    return re.search(matcher, subject) is not None


def jobs(event, payload):
    """[(명령, 제한 시간, 입력 목록)]. 같은 명령은 한 번"""
    groups = json.loads(BASE.read_text(encoding="utf-8")).get("hooks", {}).get(event, [])
    items, out, seen = views(payload), [], set()
    for group in groups:
        matcher = group.get("matcher") or ""
        targets = items[:1] if matcher in ("", "*") else [v for v in items if matches(matcher, v)]
        for hook in group.get("hooks", []):
            cmd = hook.get("command")
            if not targets or hook.get("type") != "command" or not cmd or cmd in seen:
                continue
            seen.add(cmd)
            out.append((cmd, hook.get("timeout") or 60, targets))
    return out


def skip(cmd, payload):
    """코드를 고치지 않은 세션에서 기존 미커밋 코드의 테스트가 도는 것 방지"""
    return "scoped-test.sh" in cmd and not (STATE / "sessions" / f"{payload['session_id']}.files").is_file()


def run(cmd, timeout, targets, end):
    """입력마다 차례로 실행(훅의 상태 파일이 겹치지 않게)"""
    outs = []
    for view in targets:
        left = min(timeout, end - time.monotonic())
        if left <= 0:
            raise HookError(f"{cmd}: 시간 초과")
        try:
            r = subprocess.run(["bash", "-c", cmd], input=json.dumps(view), text=True, capture_output=True, timeout=left)
        except subprocess.TimeoutExpired:
            raise HookError(f"{cmd}: 시간 초과") from None
        if r.returncode == 2:
            outs.append({"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": r.stderr.strip() or cmd}})
        elif r.returncode:
            raise HookError(f"{cmd}: 종료 {r.returncode} {r.stderr.strip()[:300]}")
        elif r.stdout.strip():
            try:
                out = json.loads(r.stdout)
            except ValueError:
                raise HookError(f"{cmd}: JSON 이 아닌 출력") from None
            if isinstance(out, dict):
                outs.append(out)
    return outs


def merge(event, outs):
    """훅 결과 합치기. 거부가 하나라도 있으면 거부"""
    ctx, msgs, denies, update = [], [], [], None
    for out in outs:
        spec = out.get("hookSpecificOutput") or {}
        if spec.get("additionalContext"):
            ctx.append(spec["additionalContext"])
        if out.get("systemMessage"):
            msgs.append(out["systemMessage"])
        why, decision = spec.get("permissionDecisionReason") or "", spec.get("permissionDecision")
        if decision == "ask":
            denies.append(why + " (Codex 는 확인 창을 못 띄워 거부함. 우회하지 말고 사용자에게 직접 실행을 요청)")
        elif decision == "deny":
            denies.append(why)
        elif out.get("decision") == "block":
            denies.append(out.get("reason") or "")
        elif decision == "allow" and spec.get("updatedInput"):
            update = spec["updatedInput"]
    res, spec = {}, {}
    if msgs:
        res["systemMessage"] = "\n".join(msgs)
    if ctx and event in CONTEXT_EVENTS:
        spec["additionalContext"] = "\n".join(ctx)
    if event == "PreToolUse":
        if denies:
            spec.update(permissionDecision="deny", permissionDecisionReason="\n".join(d for d in denies if d) or "훅이 거부함")
        elif update and isinstance(update.get("command"), str):
            spec.update(permissionDecision="allow", updatedInput=update)
    elif denies and event in ("PostToolUse", "UserPromptSubmit", "Stop"):
        res.update(decision="block", reason="\n".join(d for d in denies if d) or "훅이 거부함")
    if spec:
        res["hookSpecificOutput"] = {"hookEventName": event, **spec}
    return res


def main():
    event = ""
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name") or ""
        end = time.monotonic() + DEADLINE.get(event, DEADLINE_DEFAULT)
        sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or ""))
        payload["session_id"] = "codex-" + sid if sid else ""  # Claude 세션 상태 파일과 겹치지 않게
        todo = [j for j in jobs(event, payload) if not skip(j[0], payload)]
        if not todo:
            return 0
        outs, errors = [], []
        with ThreadPoolExecutor(len(todo)) as pool:
            for f in [pool.submit(run, *j, end) for j in todo]:
                try:
                    outs += f.result()
                except (HookError, OSError) as e:
                    errors.append(str(e))
        res = merge(event, outs)
        denied = res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        if errors and event == "PreToolUse" and not denied:
            raise HookError("; ".join(errors))
        if res:
            print(json.dumps(res, ensure_ascii=False))
        elif errors:
            raise HookError("; ".join(errors))
        return 0
    except Exception as e:  # 예상 밖 오류로 보호 훅을 건너뛰지 않게 함
        print(f"Claude 호환 훅 실패: {e}", file=sys.stderr)
        return 2 if event in ("", "PreToolUse") else 1


if __name__ == "__main__":
    sys.exit(main())
