#!/usr/bin/env python3
"""Display an explicitly scoped Orca Run without consuming its mailbox."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unicodedata

from hud_view import PHASES, render_compact, use_color


def safe(value):
    return " ".join("".join(
        c if not unicodedata.category(c).startswith("C") else " "
        for c in str(value if value is not None else "?")
    ).split())


def crop(value, width):
    value = safe(value)
    result, size = [], 0
    for c in value:
        size += 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if size > width - 1:
            return "".join(result) + "…"
        result.append(c)
    return value


def query(executable, argv):
    completed = subprocess.run(
        [executable, "orchestration", *argv, "--json"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"{argv[0]}: JSON 응답 없음 (exit {completed.returncode})") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{argv[0]}: 응답 객체 형식 오류")
    if completed.returncode or data.get("ok") is not True:
        error = data.get("error")
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        raise RuntimeError(f"{argv[0]}: {safe(code)}")
    result = data.get("result")
    field = {"run-show": "run", "task-list": "tasks", "worker-list": "workers", "gate-list": "gates"}[argv[0]]
    expected = dict if field == "run" else list
    if not isinstance(result, dict) or not isinstance(result.get(field), expected):
        raise RuntimeError(f"{argv[0]}: {field} 응답 형식 오류")
    if expected is list and not all(isinstance(row, dict) for row in result[field]):
        raise RuntimeError(f"{argv[0]}: {field} 항목 형식 오류")
    if field == "workers" and not isinstance(result.get("page", {}), dict):
        raise RuntimeError("worker-list: page 응답 형식 오류")
    return result


def collect(executable, run_id):
    commands = {
        "run": ["run-show", "--id", run_id],
        "tasks": ["task-list", "--run", run_id, "--brief"],
        "workers": ["worker-list", "--run", run_id, "--include-remote"],
        "gates": ["gate-list", "--run", run_id],
    }
    result, errors = {}, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {key: pool.submit(query, executable, argv) for key, argv in commands.items()}
        for key, future in pending.items():
            try:
                result[key] = future.result()
            except (RuntimeError, subprocess.TimeoutExpired, OSError, KeyError) as error:
                errors.append(f"{key}: {safe(type(error).__name__)} {safe(error)[:100]}")
    workers = result.get("workers")
    if workers:
        seen = set()
        while workers.get("page", {}).get("hasMore"):
            cursor = workers["page"].get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                errors.append("workers: 페이지 조회 중단, 일부 결과만 표시")
                break
            seen.add(cursor)
            try:
                page = query(executable, ["worker-list", "--run", run_id,
                                          "--include-remote", "--cursor", cursor])
                workers.setdefault("workers", []).extend(page.get("workers", []))
                workers["page"] = page.get("page", {})
            except (RuntimeError, subprocess.TimeoutExpired, OSError, KeyError) as error:
                errors.append(f"workers: 페이지 조회 실패 {safe(type(error).__name__)}")
                break
    return result, errors


def worker_detail(worker, now):
    projection = worker.get("projection") or {}
    provider = projection.get("provider") or {}
    liveness = projection.get("liveness") or {}
    observed = liveness.get("observedAt")
    age = f"{max(0, int(now - observed / 1000))}초 전" if isinstance(observed, (int, float)) else "관찰 시각 없음"
    identity = f"{provider.get('id', '?')}/{provider.get('model') or '모델 미확인'}"
    reason = liveness.get("reason") or (projection.get("evidence") or {}).get("liveStatus", "?")
    return f"{identity} · {liveness.get('verdict', 'unverifiable')} · {age} · {reason}"


def git_metadata(worktree):
    if not worktree:
        return []
    result = []
    for title, argv in [
        ("Stage 파일 변경", ["diff", "--cached", "--name-status", "--no-renames", "--no-ext-diff", "--no-textconv"]),
        ("최근 커밋", ["log", "-3", "--format=%h %s"]),
    ]:
        try:
            process = subprocess.run(["git", "--no-optional-locks", "-C", str(worktree), *argv],
                                     capture_output=True, text=True, timeout=5)
            if process.returncode:
                result.append(f"{title}: 조회 불가")
            else:
                rows = process.stdout.splitlines()
                result.append(f"{title}: {len(rows)}건" if title == "Stage 파일 변경" else title)
                result.extend("  " + safe(row) for row in rows[:5])
                if len(rows) > 5:
                    result.append(f"  외 {len(rows) - 5}건")
        except (OSError, subprocess.TimeoutExpired):
            result.append(f"{title}: 조회 불가")
    return result


def render(snapshot, errors, run_id, now, worktree=None, width=110, height=None):
    run = snapshot.get("run", {}).get("run") or {}
    tasks = snapshot.get("tasks", {}).get("tasks", [])
    workers = snapshot.get("workers", {}).get("workers", [])
    gates = snapshot.get("gates", {}).get("gates", [])
    lines = ["ORCA COORDINATOR HUD  |  읽기 전용",
             f"Run {run_id}", run.get("objective", "목표 조회 불가"),
             f"조회 {datetime.fromtimestamp(now).astimezone().strftime('%H:%M:%S')} · Ctrl+C 닫기"]
    if errors:
        lines += ["[확인 불가] 일부 조회 실패 — 종료·수용 판정에 사용하지 않음", *errors]
    if "tasks" in snapshot:
        counts = Counter(task.get("status", "unknown") for task in tasks)
        done = counts["completed"]
        fill = int(12 * done / len(tasks)) if tasks else 0
        lines.append(f"Task 종료 [{('#' * fill).ljust(12, '.')}] {done}/{len(tasks)} "
                     f"· 진행 {counts['dispatched']} · 차단 {counts['blocked']} · 실패 {counts['failed']}")
    else:
        lines.append("Task 수·진척: 확인 불가")
    lines.append("Task 종료 ≠ 기능 수용 · QA/커밋 증거는 중앙이 확인")
    pending = [g for g in gates if g.get("status") not in ("resolved", "cancelled")]
    lines.append(f"결정 Gate: {len(pending)}건" if "gates" in snapshot else "결정 Gate: 확인 불가")
    for gate in pending[:3]:
        lines.append("  " + safe(gate.get("question") or gate.get("title") or gate.get("id")))
    phases = []
    for phase, label in PHASES.items():
        members = [t for t in tasks if str(t.get("task_title") or t.get("display_name", "")).startswith(f"[{phase}]")]
        completed = sum(t.get("status") == "completed" for t in members)
        phases.append(f"{label}: Task {completed}/{len(members)} 종료" if members else f"{label}: 단계 미등록")
    lines += [" | ".join(phases[:3]), " | ".join(phases[3:])]
    lines.append("")
    rank = {"blocked": 0, "failed": 1, "dispatched": 2, "ready": 3, "pending": 4, "completed": 5}
    ordered = sorted(tasks, key=lambda t: rank.get(t.get("status"), 2))
    lines.append("작업·모델·마지막 관찰")
    for task in ordered[:10]:
        title = task.get("task_title") or task.get("display_name") or task.get("id")
        lines.append(f"[{task.get('status', 'unknown')}] {safe(title)}")
        attempts = [w for w in workers if w.get("taskId") == task.get("id")]
        for worker in attempts:
            projection = worker.get("projection") or {}
            dispatch = worker.get("dispatchId", "?")
            current = "현재" if dispatch == task.get("dispatch_id") else "시도"
            lines.append(f"  {current} {dispatch} · Dispatch {worker.get('dispatchStatus', '?')}"
                         f"/{projection.get('outcome', '?')} · 터미널 {worker.get('terminalState', '?')}")
            lines.append("  " + worker_detail(worker, now))
            attention = projection.get("attention") or {}
            if attention.get("requiresAction"):
                lines.append("  조치 필요: " + safe(attention.get("categories", [])))
        if not attempts and task.get("dispatch_id"):
            lines.append("  워커 관찰 없음 — 종료 증거 아님")
    if len(ordered) > 10:
        lines.append(f"외 {len(ordered) - 10}개 Task — 전체 목록은 Orca task-list")
    if worktree:
        lines += ["", *git_metadata(worktree)]
    if height and len(lines) >= height:
        lines = lines[:max(1, height - 2)] + ["… 아래 항목 생략 · 창 높이를 늘리거나 --watch 없이 전체 조회"]
    return "\n".join(crop(line, width) for line in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="명시적 Orca Run ID")
    parser.add_argument("--orca", default="/usr/local/bin/orca", help="해당 Run의 Orca 실행 파일")
    parser.add_argument("--worktree", type=Path, help="Git 메타데이터를 조회할 정확한 경로")
    parser.add_argument("--watch", action="store_true", help="5초 간격으로 갱신")
    parser.add_argument("--details", action="store_true", help="Dispatch·관찰 근거·Git 상세 표시")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="기본 화면 색상: TTY 자동 감지, NO_COLOR 준수")
    args = parser.parse_args()
    if not re.fullmatch(r"run_[A-Za-z0-9]+", args.run):
        parser.error("Run ID 형식: run_<id>")
    interactive = args.watch and sys.stdout.isatty()
    if interactive:
        print("\033[?1049h", end="", flush=True)
    try:
        while True:
            snapshot, errors = collect(args.orca, args.run)
            if interactive:
                print("\033[H\033[2J", end="")
            size = shutil.get_terminal_size((110, 40))
            now = time.time()
            if args.details:
                output = render(snapshot, errors, args.run, now, args.worktree,
                                size.columns, size.lines if interactive else None)
            else:
                output = render_compact(snapshot, errors, now, size.columns, size.lines,
                                        use_color(args.color, sys.stdout.isatty(), os.environ.get("NO_COLOR")))
            print(output, flush=True)
            if not args.watch:
                return 1 if errors else 0
            time.sleep(5)
    except KeyboardInterrupt:
        return 0
    finally:
        if interactive:
            print("\033[?1049l", end="", flush=True)


if __name__ == "__main__":
    sys.exit(main())
