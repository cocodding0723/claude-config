from collections import Counter
from datetime import datetime
import math
import re
import unicodedata


PHASES = {
    "impl": "구현", "review": "독립 리뷰", "qa": "동작·화면 QA",
    "commit": "커밋 확인", "research": "조사·기획",
}
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_CONTROL = re.compile(
    r"(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c|$)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|\x1b[@-_]", re.DOTALL
)
_COLORS = {"heading": "1;97;44", "error": "1;97;41", "warn": "1;93",
           "active": "1;96", "section": "1;97", "quiet": "37"}
_STATES = {"dispatched": "진행", "blocked": "차단", "failed": "실패",
           "ready": "대기", "pending": "대기", "completed": "종료"}


def clean(value):
    value = _CONTROL.sub("", str(value if value is not None else "?"))
    return " ".join("".join(c if not unicodedata.category(c).startswith("C") else " "
                            for c in value).split())


def cell_width(value):
    return sum(0 if unicodedata.category(c).startswith("M") else
               2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in _SGR.sub("", value))


def fit(value, width):
    value = clean(value)
    if cell_width(value) <= width:
        return value
    suffix = "… 더보기" if width >= 10 else "…"
    budget = max(0, width - cell_width(suffix))
    result, size = [], 0
    for c in value:
        size += cell_width(c)
        if size > budget:
            break
        result.append(c)
    return "".join(result) + (suffix if width > 0 else "")


def use_color(mode, tty, no_color):
    return mode == "always" or (mode == "auto" and tty and not no_color)


def observation(task, workers, available, now):
    dispatch = task.get("dispatch_id")
    if not dispatch and task.get("status") in ("pending", "ready"):
        return "배정 대기", False
    current = [w for w in workers if dispatch and w.get("dispatchId") == dispatch
               and w.get("taskId") == task.get("id")]
    if not available or not current:
        return "모델 미확인 · 현재 워커 관찰 없음", True
    projection = current[-1].get("projection") or {}
    provider = projection.get("provider") or {}
    model = fit(provider.get("model") or "모델 미확인", 22)
    liveness = projection.get("liveness") or {}
    observed = liveness.get("observedAt")
    valid = (isinstance(observed, (int, float)) and not isinstance(observed, bool)
             and math.isfinite(observed) and observed / 1000 <= now)
    age = max(0, int(now - observed / 1000)) if valid else None
    stale = age is not None and age > 60
    unknown = liveness.get("verdict") != "live" or age is None or stale
    status = "관찰 오래됨" if stale else "관찰 확인 불가" if unknown else "관찰 정상"
    age_text = f"{age}초 전" if age is not None else "시각 없음"
    return f"{model} · {status} · {age_text}", unknown


def render_compact(snapshot, errors, now, width=80, height=24, color=False):
    run = snapshot.get("run", {}).get("run") or {}
    tasks = snapshot.get("tasks", {}).get("tasks", [])
    workers = snapshot.get("workers", {}).get("workers", [])
    gates = snapshot.get("gates", {}).get("gates", [])
    known = "tasks" in snapshot
    counts = Counter(t.get("status", "unknown") for t in tasks)
    done = counts["completed"]
    unknown = sum(n for status, n in counts.items() if status not in _STATES)
    active = [t for t in tasks if t.get("status") != "completed"]
    active.sort(key=lambda t: {"blocked": 0, "failed": 1, "dispatched": 2}.get(t.get("status"), 3))
    unseen = sum(observation(t, workers, "workers" in snapshot, now)[1]
                 for t in active if t.get("status") == "dispatched")
    state = ("확인 불가" if not known or errors else "차단" if counts["blocked"] else
             "실패" if counts["failed"] else "진행" if counts["dispatched"] else
             "대기" if counts["ready"] + counts["pending"] else
             "확인 불가" if unknown else "유휴")
    lines = []

    def add(text, tone="section"):
        lines.append((text, tone))

    add(f" ORCA HUD · {state} | 조회 {datetime.fromtimestamp(now).astimezone():%H:%M:%S}", "heading")
    add("목표 " + clean(run.get("objective") or "조회 불가"))
    add((f"진행 {counts['dispatched']} · 차단 {counts['blocked']} · 대기 "
         f"{counts['ready'] + counts['pending']} · 실패 {counts['failed']} · 불명 {unknown}")
        if known else "작업 수 확인 불가", "active" if state == "진행" else "warn")
    add(f"워커 현재 관찰: 확인 불가 {unseen}건" if "workers" in snapshot else
        "워커 조회 불가 · 종료 증거 아님", "warn" if unseen or "workers" not in snapshot else "quiet")
    fill = int(10 * done / len(tasks)) if tasks else 0
    add(f"Task 종료 [{'#' * fill}{'.' * (10 - fill)}] {done}/{len(tasks)}" if known
        else "Task 종료: 확인 불가", "section")
    add("기능 수용·QA 통과 별도 확인", "quiet")
    if errors:
        add(f"조회 오류 {len(errors)}건 · {clean(errors[0])}", "error")
    pending = [g for g in gates if g.get("status") not in ("resolved", "cancelled")]
    add(f"결정 Gate {len(pending)}건" if "gates" in snapshot else "결정 Gate 확인 불가",
        "warn" if pending or "gates" not in snapshot else "quiet")
    if pending:
        add("질문 " + clean(pending[0].get("question") or pending[0].get("title") or "내용 확인 불가"), "warn")
    registered, absent = [], []
    for phase in PHASES:
        members = [t for t in tasks if str(t.get("task_title") or t.get("display_name", "")).startswith(f"[{phase}]")]
        if members:
            registered.append(f"{PHASES[phase]} {sum(t.get('status') == 'completed' for t in members)}/{len(members)}")
        else:
            absent.append(PHASES[phase])
    add("단계 Task 종료: " + (" · ".join(registered) if known else "조회 불가"))
    add(f"미등록 {len(absent)}: " + (", ".join(absent) or "없음") if known else "단계 등록 여부: 확인 불가", "quiet")
    add("현재 작업 · 모델 / 워커 관찰", "active")
    footer = f"종료 {done}건" if known else "종료 수 확인 불가"
    completed = [clean(t.get("task_title") or t.get("display_name") or "제목 없음")
                 for t in tasks if t.get("status") == "completed"]
    reclaim = sum(w.get("terminalState") == "reclaimable" for w in workers)
    if reclaim:
        footer += f" · 자원 회수 대기 {reclaim}건"
    if completed:
        footer += " · " + " / ".join(completed)
    limit = max(1, (height or 24) - 1)
    slots = max(0, (limit - len(lines) - 2) // 2)
    for task in active[:slots]:
        status = task.get("status")
        tone = "error" if status == "failed" else "warn" if status == "blocked" else "active"
        add(f"[{_STATES.get(status, '불명')}] " + clean(task.get("task_title") or task.get("display_name") or "제목 없음"), tone)
        detail, uncertain = observation(task, workers, "workers" in snapshot, now)
        add("  " + detail, "warn" if uncertain else "quiet")
    if len(active) > slots:
        add(f"… 현재 작업 {len(active) - slots}건 더보기: --details", "warn")
    elif not active:
        add("현재 작업 없음" if known else "현재 작업 확인 불가", "quiet")
    add(footer, "quiet")
    if len(lines) > limit:
        lines = lines[:max(0, limit - 1)] + [("… 더보기: --details", "warn")]
    output = []
    for text, tone in lines:
        text = fit(text, max(0, width - 1))
        output.append(f"\x1b[{_COLORS[tone]}m{text}\x1b[0m" if color else text)
    return "\n".join(output)
