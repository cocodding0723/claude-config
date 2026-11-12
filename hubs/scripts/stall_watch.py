#!/usr/bin/env python3
"""멈춘 워커 감시 카드. 읽기 전용으로 모든 Run 의 워커를 조회해 응답 없는 워커를 허브별로 묶은 Slack 카드 1장을 보냄.

대상: (a) 워커 stopped + 터미널 retained(= 종료 보고 worker_done 없이 멈춤) 이고 작업이 아직 열려 있음
      (b) 작업이 진행 중인데 워커 관찰(lastObservedAt)이 SILENCE_MIN 분 넘게 갱신 없음
침묵·시간 초과는 워커 사망의 증거가 아니므로 문구는 '응답 없음'. 워커를 멈추거나 재시작하지 않고, 🅰️ 선택 시 허브 코디네이터에 한 줄 알림만 보냄(card_actions.py).
중복 방지: 같은 dispatch 는 DEDUPE_H 시간 안에 다시 알리지 않음(stall-watch.json). 첫 실행은 이미 멈춰 있던 옛 워커를 기준선으로만 기록(카드 없음).
호출은 slack_socket.py 의 감시 루프가 주기적으로 함.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import card_actions  # noqa: E402
import main_terminal as mt  # noqa: E402
import restart_watch as rw  # noqa: E402  (허브 목록·문구 정리·상태 파일 저장 재사용)


@dataclass(frozen=True)
class Limits:
    """감시 기준 한 곳 모음. 환경 변수로만 바꿈"""
    silence_min: float  # 진행 중 워커의 관찰이 이 시간(분) 넘게 갱신 없으면 응답 없음
    dedupe_h: float  # 같은 dispatch 를 다시 알리지 않는 시간
    pages: int  # worker-list 쪽 수 상한(한 쪽 100건, 최신순)

    @classmethod
    def from_env(cls) -> "Limits":
        def num(name, default):
            try:
                v = float(os.environ.get(name, ""))
                return v if v > 0 else default
            except ValueError:
                return default
        return cls(num("LRM_STALL_SILENCE_MIN", 30.0), num("LRM_STALL_DEDUPE_H", 6.0), int(num("LRM_STALL_PAGES", 5)))


DIR = card_actions.DIR
STATE = os.path.join(DIR, "stall-watch.json")
ORCA, NOTIFY = rw.ORCA, rw.NOTIFY
ORCA_TIMEOUT_S = 30
DID_PREFIX = "D-ST"
CLOSED_TASK = frozenset({"completed", "failed", "cancelled", "canceled", "done"})  # 이미 끝난 작업의 멈춘 워커는 대상 아님
RUNNING_TASK = frozenset({"dispatched", "running", "in_progress"})
HUB_RE = re.compile(r"/coord-([a-z0-9-]+)-hub(?:@@|$)")
LINE_MAX = 60

EPILOG = """종료 코드: 0 정상(감지 없음·기준선 기록 포함), 1 카드 전송 실패·조회 실패, 2 인자 오류
환경: LRM_HUB_DIR, LRM_ORCA, LRM_NOTIFY, HUBS_TSV,
      LRM_STALL_SILENCE_MIN 응답 없음 기준(분, 기본 30), LRM_STALL_DEDUPE_H 재알림 간격(시간, 기본 6), LRM_STALL_PAGES 조회 쪽 수(기본 5)
예:
  stall_watch.py check --dry-run          # 감지 결과·카드 문구만 출력
  stall_watch.py check --report-existing  # 첫 실행에도 이미 멈춘 워커를 카드로 보냄
"""


def orca_json(args: list):
    rc, d = mt.run_orca(ORCA, args, ORCA_TIMEOUT_S)
    res = (d or {}).get("result") if rc == 0 and isinstance(d, dict) else None
    return res if isinstance(res, dict) else None


def list_workers(state: str, pages: int):
    """터미널 상태별 워커 목록(최신순, 쪽 수 상한). 조회 실패는 None"""
    rows, cursor = [], None
    for _ in range(pages):
        args = ["orchestration", "worker-list", "--json", "--full", "--terminal-state", state, "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        res = orca_json(args)
        if res is None or not isinstance(res.get("workers"), list):
            return None
        rows += [w for w in res["workers"] if isinstance(w, dict)]
        cursor = res.get("nextCursor")
        if not cursor:
            break
    return rows


def run_tasks(run_id: str):
    """{taskId: {status,title,slug}}. slug 는 작업을 만든 터미널의 워크트리(coord-<slug>-hub)에서 판별"""
    res = orca_json(["orchestration", "task-list", "--run", run_id, "--json", "--full"])
    if res is None or not isinstance(res.get("tasks"), list):
        return None
    out = {}
    for t in res["tasks"]:
        if isinstance(t, dict) and t.get("id"):
            m = HUB_RE.search(str(t.get("created_by_process_incarnation") or ""))
            out[t["id"]] = {"status": t.get("status"), "title": t.get("title") or t.get("task_title") or "", "slug": m.group(1) if m else ""}
    return out


def detect(limits: Limits, now: float):
    """(감지 목록, 오류 문구|None). 항목 {dispatch,kind,mins,title,slug}"""
    cands = []
    stopped = list_workers("retained", limits.pages)
    active = list_workers("active", limits.pages)
    if stopped is None or active is None:
        return [], "worker-list 조회 실패"
    for w in stopped:
        if w.get("workerState") == "stopped":
            cands.append((w, "stopped"))
    for w in active:
        seen_ms = ((w.get("projection") or {}).get("evidence") or {}).get("lastObservedAt")
        if w.get("workerState") == "ready" and isinstance(seen_ms, (int, float)) and now - seen_ms / 1000 > limits.silence_min * 60:
            cands.append((w, "silent"))
    tasks = {}
    for run in sorted({w.get("runId") for w, _ in cands if w.get("runId")}):
        t = run_tasks(run)
        if t is None:
            return [], "task-list 조회 실패"
        tasks.update(t)
    out = []
    for w, kind in cands:
        t = tasks.get(w.get("taskId"))
        if t is None or t["status"] in CLOSED_TASK or (kind == "silent" and t["status"] not in RUNNING_TASK):
            continue
        mins = int((now - w["projection"]["evidence"]["lastObservedAt"] / 1000) // 60) if kind == "silent" else None
        out.append({"dispatch": w.get("dispatchId"), "kind": kind, "mins": mins, "title": rw.clean(t["title"]), "slug": t["slug"]})
    return out, None


def coordinators(slug: str):
    """허브 폴더에 연결된 claude 코디네이터 터미널 [(핸들, 제목)]. 조회 실패는 None"""
    res = orca_json(["terminal", "list", "--json", "--limit", "1000"])
    if res is None or not isinstance(res.get("terminals"), list):
        return None
    return [(t.get("handle"), rw.clean(t.get("title") or "")) for t in res["terminals"]
            if isinstance(t, dict) and str(t.get("worktreePath", "")).endswith(f"/coord-{slug}-hub")
            and t.get("agentIdentity") == "claude" and t.get("connected") is True]


def load_state() -> dict:
    d = rw.load_state_at(STATE)
    return d if isinstance(d.get("seen"), dict) else {"seen": {}}


def describe(item: dict) -> str:
    if item["kind"] == "silent":
        return f"{item['mins']}분째 응답 없음"
    return "종료 보고 없이 멈춤"


def build(items: list, names: dict, dups: dict) -> tuple:
    """(카드 본문, 알림 대상 {slug:[줄]}). 허브별로 묶어 한 줄씩"""
    by_hub = {}
    for it in items:
        by_hub.setdefault(it["slug"], []).append(it)
    lines = [f"멈춘 워커 감시 — 응답 없음 {len(items)}건", "",
             f"• 무엇: 종료 보고 없이 멈췄거나 응답이 없는 워커 {len(items)}건(허브 {len(by_hub)}곳)",
             "• 결과: 알리기를 누르면 담당 허브 코디네이터에 한 줄만 보냄. 워커는 건드리지 않음"]
    for slug, ts in sorted(dups.items()):
        lines.append(f"• 중복 코디네이터: {names.get(slug, slug)} · {' / '.join(t or '제목 없음' for _h, t in ts)} ({len(ts)}개)")
    lines += ["", "참고:"]
    notify = {}
    for slug, its in sorted(by_hub.items(), key=lambda kv: kv[0] or "~"):
        label = names.get(slug, slug) if slug else "허브 미확인"
        for it in its:
            text = f"{it['title'][:LINE_MAX]} — {describe(it)}"
            lines.append(f"• {label} · {text}")
            notify.setdefault(slug, []).append(text)
    return "\n".join(lines), notify


def check(limits: Limits, dry: bool, report_existing: bool, now: float | None = None):
    now = time.time() if now is None else now
    items, err = detect(limits, now)
    if err:
        return 1, {"status": "unavailable", "error": err}
    st = load_state()
    first = not os.path.exists(STATE)
    if first and not report_existing:  # 옛 워커 기준선: 이후 새로 멈춘 것만 알림
        if not dry:
            rw.save_state_at(STATE, {"seen": {i["dispatch"]: {"at": now, "baseline": True} for i in items}, "baselinedAt": now})
        return 0, {"status": "baseline", "count": len(items)}
    fresh = [i for i in items if (st["seen"].get(i["dispatch"]) or {}).get("at", 0) + limits.dedupe_h * 3600 <= now
             and not (st["seen"].get(i["dispatch"]) or {}).get("baseline")]
    if not fresh:
        return 0, {"status": "none"}
    names = dict(rw.hub_rows())
    dups = {}
    for slug in {i["slug"] for i in fresh if i["slug"]}:
        c = coordinators(slug)
        if c and len(c) >= 2:
            dups[slug] = c
    text, notify = build(fresh, names, dups)
    did = DID_PREFIX + hashlib.sha1(("|".join(sorted(i["dispatch"] for i in fresh)) + str(time.time_ns())).encode()).hexdigest()[:10]
    summary = {"did": did, "count": len(fresh), "hubs": sorted(notify), "dispatches": [i["dispatch"] for i in fresh], "card": text}
    if dry:
        return 0, {"status": "would_card", **summary}
    card_actions.register(did, "notify", {"hubs": [{"slug": s, "lines": ls} for s, ls in notify.items()],
                                          "dups": [{"slug": s, "titles": [t for _h, t in c]} for s, c in dups.items()]})
    if not rw.send_card(did, text, "1=담당 허브에 알리기,2=무시"):
        card_actions.take(did)
        return 1, {"status": "send_failed", **summary}
    for i in fresh:
        st["seen"][i["dispatch"]] = {"at": now}
    st["seen"] = {k: v for k, v in st["seen"].items() if v.get("baseline") or now - v.get("at", 0) < 7 * 24 * 3600}
    rw.save_state_at(STATE, st)
    return 0, {"status": "carded", **summary}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="stall_watch.py", description="멈춘 워커 감시 카드(읽기 전용 조회). 대화형 입력 없음.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{check}")
    c = sub.add_parser("check", help="응답 없는 워커를 찾아 카드 1장 전송", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("--dry-run", action="store_true", help="전송·저장 없이 감지 결과와 카드 문구만 출력")
    c.add_argument("--report-existing", action="store_true", help="첫 실행에도 이미 멈춘 워커를 카드로 보냄(기본은 기준선만 기록)")
    c.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    a = p.parse_args(argv)
    rc, res = check(Limits.from_env(), a.dry_run, a.report_existing)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"stall_watch: {res['status']}" + (f" {res['count']}건" if "count" in res else ""))
        if a.dry_run and res.get("card"):
            print(res["card"])
    if rc:
        print(f"ERROR: {res.get('error') or '카드 전송 실패 — slack_notify.sh 확인'}. 다음 주기에 재시도", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
