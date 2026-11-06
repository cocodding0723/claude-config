from collections import Counter
from datetime import datetime

from hud_view import clean, fit, observation


def render(snapshot, now, width=110, height=40, color=False, details=False):
    entries = snapshot["entries"]
    selected = [e for e in entries if e["scope"]["included"]]
    kinds = Counter(e["scope"]["kind"] for e in entries)
    counts = Counter(t.get("status", "unknown") for e in selected for t in e["tasks"])
    gate_records = {e["id"]: e["records"].get("gates", {}) for e in selected}
    pending_gates = {run_id: [g for g in record.get("data", {}).get("gates", [])
                              if g.get("status") not in ("resolved", "cancelled")]
                     for run_id, record in gate_records.items()}
    pending = [(run_id, gate) for run_id, gates in pending_gates.items() for gate in gates]
    unknown_gates = sum(not record.get("at") or bool(record.get("errors"))
                        for record in gate_records.values())
    lines = []

    def add(text, tone="37"):
        lines.append((text, tone))

    add(f" ORCA PROJECT HUD · 읽기 전용 | 조회 {datetime.fromtimestamp(now).astimezone():%H:%M:%S}", "1;97;44")
    success = snapshot.get("discovered_at")
    age = f"{int(max(0, now - success))}초 전" if success else "없음"
    add(snapshot["project"] + f" · 소속 마지막 성공 {age}"
        + (" · 최근 스캔 실패" if snapshot.get("discovery_failed") else ""), "1;97")
    add(f"로컬 Run {len(entries)} · 프로젝트 {len(selected)} · 외부 {kinds['external']} · "
        f"혼합 {kinds['mixed']} · 미분류 {kinds['unknown']} · 충돌 {kinds['conflict']}")
    if details:
        add(f"소속 일부 미분류 Run {sum(e['scope']['uncertain'] for e in entries)}건")
        attempt = snapshot.get("discovery_attempted_at")
        add("최근 소속 스캔 " + (f"{int(max(0, now - attempt))}초 전" if attempt else "없음"))
    add(f"확인된 Task 진행 {counts['dispatched']} · 차단 {counts['blocked']} · 실패 {counts['failed']} · "
        f"대기 {counts['ready'] + counts['pending']} · 종료 {counts['completed']}", "1;96")
    add("Task·Run 종료 ≠ 기능 수용 · QA/커밋 별도 확인")
    add(f"결정 Gate 확인 불가 {unknown_gates} Run · 대기 {len(pending)}건(캐시 포함)" if unknown_gates
        else f"결정 대기 Gate {len(pending)}건", "1;93" if pending or unknown_gates else "37")
    if pending:
        run_id, gate = pending[0]
        add(f"출처Run {run_id} · 질문 " + clean(gate.get("question") or gate.get("title") or gate.get("id")), "1;93")
    warnings = snapshot["warnings"] + [f"{e['id']}: " + ", ".join(e["errors"]) for e in entries if e["errors"]]
    add(f"조회불가·범위 경고 {len(warnings)}건" + (" · " + warnings[0] if warnings else ""), "1;93" if warnings else "37")
    if details:
        for warning in warnings[1:]:
            add(warning, "1;93")

    def rank(entry):
        statuses = {t.get("status") for t in entry["tasks"]}
        return min(({"dispatched": 0, "blocked": 1, "failed": 2, "ready": 3,
                     "pending": 3, "completed": 5}.get(s, 4) for s in statuses), default=4)

    ordered = sorted(selected, key=rank)
    historical = [e for e in ordered if rank(e) == 5 and not e["errors"]
                  and gate_records[e["id"]].get("at") and not pending_gates[e["id"]]]
    add(f"과거 요약 Run {len(historical)} · 귀속 불명 제외 Task {sum(e['excluded'] for e in selected)}")
    task_budget = 1 if len(ordered) - len(historical) > 1 else 3
    for entry in ordered:
        if entry in historical and not details:
            continue
        records, scope = entry["records"], entry["scope"]
        gate_record, gates = gate_records[entry["id"]], pending_gates[entry["id"]]
        gate_text = f"Gate {len(gates)}" if gate_record.get("at") and not gate_record.get("errors") else "Gate 확인 불가"
        tasks = sorted(entry["tasks"], key=lambda t: {"dispatched": 0, "blocked": 1, "failed": 2}.get(t.get("status"), 3))
        current = tasks if details else [t for t in tasks if t.get("status") != "completed"]
        omitted = max(0, len(current) - task_budget) if not details else 0
        label = {"project": "프로젝트", "mixed": "혼합", "conflict": "충돌"}.get(scope["kind"], "미분류")
        add(f"Run {entry['id']} [{label}{'·일부 미분류' if scope['uncertain'] else ''}] "
            + gate_text + (f" · Task 더보기 {omitted}건" if omitted else "") + " · "
            + clean(entry["run"].get("objective", "목표 조회 불가")), "1;97")
        if "tasks" not in records or not records["tasks"]["at"]:
            add("Task 수 확인 불가·일부 결과", "1;93")
        if entry["errors"]:
            stamps = [r["at"] for r in records.values() if r["at"]]
            age = f"{int(max(0, now - min(stamps)))}초 전" if stamps else "없음"
            add(f"stale/cache · 마지막 성공 {age} · 현재 상태 확인 불가", "1;93")
        if details:
            add(gate_text)
        for gate in gates[:1 if not details else len(gates)]:
            add("  " + clean(gate.get("question") or gate.get("title") or gate.get("id")), "1;93")
        workers = records["workers"]["data"].get("workers", [])
        for task in current if details else current[:task_budget]:
            add(f"  출처Run {entry['id']} · [{task.get('status', 'unknown')}] "
                + clean(task.get('task_title') or task.get('display_name') or task.get('id')), "1;96")
            detail, unknown = observation(task, workers, bool(records["workers"]["at"]), now)
            add("    관측 모델 " + detail, "1;93" if unknown else "37")
            if task.get("status") in ("failed", "blocked"):
                add("    원인 " + clean(task.get("result") or "미기록"), "1;93")
            if details:
                add(f"    Task {task.get('id')} · Dispatch {task.get('dispatch_id')} · 제외 Task {entry['excluded']}")
    if details:
        for entry in entries:
            if not entry["scope"]["included"] and (entry["scope"]["kind"] != "external" or entry["scope"]["uncertain"]):
                add(f"범위 {entry['id']} · {entry['scope']['kind']}"
                    f"{'·일부 미분류' if entry['scope']['uncertain'] else ''} · 조회오류 {len(entry['errors'])}", "1;93")
    if height and len(lines) >= height:
        lines = lines[:max(0, height - 2)] + [("… 더보기: --details · 터미널 높이 확대", "1;93")]
    output = []
    for text, tone in lines:
        text = fit(text, max(0, width - 1))
        output.append(f"\x1b[{tone}m{text}\x1b[0m" if color else text)
    return "\n".join(output)
