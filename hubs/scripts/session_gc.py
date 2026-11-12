#!/usr/bin/env python3
"""주기 세션 정리. 끝난 워커 창을 찾아 판정 근거를 출력·기록함. 기본은 시험 모드(조회만), --apply 일 때만 동작.

자동 동작은 한 가지: Orca 감독 워커(dispatch 있음)이고 작업이 completed/failed 로 끝났고(worker_done 이후), 터미널을 Orca 가 소유하며(release 가능),
입력 칸이 비어 있고(승인 대기·쓰던 글 없음) idle IDLE_MIN 분 이상, 작업 공간 미커밋·미추적 0, 대화 기록 보관이 확인된 창 → orchestration worker-release.
실제 release 직전에 그 한 건만 다시 조회해 하나라도 달라졌으면 건너뜀.
소유권 불명·사용자 인수·유지 표식 창은 '사람 확인' 으로 목록에만 올리고, 중복 코디네이터도 목록·로그에만 올림(전송·닫기 없음).
조회 실패·조회 불완전(쪽 상한에 닿고 nextCursor 남음)·판정 불가는 이번 회차 무동작. 감지·화면 판정은 stall_watch·panel_collect 를 import 해 재사용함.
"""
import sys

if sys.version_info < (3, 10):  # card_actions.py 가 3.10 문법(float | None)을 씀. launchd 가 시스템 python(3.9)을 잡으면 TypeError 대신 안내
    print(f"ERROR: Python 3.10 이상 필요(현재 {sys.version_info.major}.{sys.version_info.minor}). plist 의 python 경로를 3.10 이상 절대 경로로 바꾸고 install.sh 를 다시 실행", file=sys.stderr)
    sys.exit(2)

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import card_actions  # noqa: E402
import main_terminal as mt  # noqa: E402
import restart_watch as rw  # noqa: E402
import stall_watch as sw  # noqa: E402


class Unavailable(Exception):
    """조회 실패·불완전. 이번 회차 무동작 사유"""


@dataclass(frozen=True)
class Limits:
    """정리 기준 한 곳 모음. 환경 변수로만 바꿈"""
    idle_min: float  # 끝난 워커 창이 이 시간(분) 이상 출력 없어야 대상
    pages: int  # 조회 쪽 수 상한. 닿고도 nextCursor 가 남으면 조회 불완전

    @classmethod
    def from_env(cls) -> "Limits":
        try:
            v = float(os.environ.get("LRM_GC_IDLE_MIN", ""))
            idle = v if v > 0 else 30.0
        except ValueError:
            idle = 30.0
        return cls(idle, sw.Limits.from_env().pages)


DIR = card_actions.DIR
LOG = os.environ.get("LRM_GC_LOG") or os.path.join(DIR, "session-gc.log")  # 로그 경로는 여기 한 곳
STATE = os.path.join(DIR, "session-gc.json")
LOCK = os.path.join(DIR, ".session-gc.lock")
MAIN_ENV = os.environ.get("LRM_MAIN_ENV") or os.path.join(DIR, "main.env")
GONE = "창 없음"
ORCA = sw.ORCA
ORCA_TIMEOUT_S = sw.ORCA_TIMEOUT_S
GIT_TIMEOUT_S = 20
TERM_LIMIT = 1000
DONE_WORKER = frozenset({"succeeded", "failed"})  # worker_done 으로 끝난 상태. stopped·abandoned 는 종료 보고 없이 멈춘 것이라 제외
DONE_DISPATCH = frozenset({"completed", "failed"})
DONE_TASK = frozenset({"completed", "failed"})
OWNED = "owned"
FREE = "not_requested"  # release 를 아직 요청하지 않은 상태

EPILOG = """종료 코드: 0 정상(후보 없음·시험 모드 포함), 1 조회 실패·불완전(아무것도 안 함), 2 인자 오류·Python 3.10 미만
환경: LRM_GC_IDLE_MIN 끝난 워커 idle 기준(분, 기본 30), LRM_GC_LOG 로그 파일(기본 $LRM_HUB_DIR/session-gc.log),
      LRM_HUB_DIR, LRM_ORCA, LRM_MAIN_ENV, LRM_STALL_PAGES 조회 쪽 수(기본 5)
예:
  session_gc.py                 # 시험 모드: 후보·판정 근거만 출력(orca 변경 명령 0회)
  session_gc.py --json          # 같은 내용을 JSON 으로
  session_gc.py --apply         # 실제 worker-release. 첫 --apply 는 목록만 보여주고 다음 실행부터 동작
"""


def safe(text) -> str:
    """제목·사유를 출력·로그에 싣기 전 한 줄 정리와 비밀 값 가림"""
    return sw.mask_secrets(rw.clean(str(text or "")))


def log(line: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {line}\n")
    except OSError as e:
        print(f"WARN: 로그 기록 실패({e.strerror}). LRM_GC_LOG 경로 확인", file=sys.stderr)


def load_state() -> dict:
    return rw.load_state_at(STATE)


# ---- 조회(읽기 전용) -----------------------------------------------------------------------------
def list_workers(state: str, pages: int) -> list:
    """터미널 상태별 워커 전체. 쪽 상한에 닿고 nextCursor 가 남으면 조회 불완전"""
    rows, cursor = [], None
    for _ in range(pages):
        args = ["orchestration", "worker-list", "--json", "--full", "--terminal-state", state, "--limit", "100"] + (["--cursor", cursor] if cursor else [])
        res = sw.orca_json(args)
        if res is None or not isinstance(res.get("workers"), list):
            raise Unavailable(f"worker-list({state}) 조회 실패")
        rows += [w for w in res["workers"] if isinstance(w, dict)]
        cursor = res.get("nextCursor")
        if not cursor:
            return rows
    raise Unavailable(f"worker-list({state}) 조회 불완전(쪽 상한 {pages} 도달, 더 남음)")


def list_terminals() -> list:
    res = sw.orca_json(["terminal", "list", "--json", "--limit", str(TERM_LIMIT)])
    if res is None or not isinstance(res.get("terminals"), list):
        raise Unavailable("terminal list 조회 실패")
    if res.get("nextCursor") or len(res["terminals"]) >= TERM_LIMIT:
        raise Unavailable("terminal list 조회 불완전(상한 도달)")
    return [t for t in res["terminals"] if isinstance(t, dict) and t.get("handle")]


def task_statuses(run_id: str) -> dict:
    """{taskId: status}. 실패·불완전은 Unavailable"""
    res = sw.orca_json(["orchestration", "task-list", "--run", run_id, "--json", "--full"])
    if res is None or not isinstance(res.get("tasks"), list):
        raise Unavailable("task-list 조회 실패")
    if res.get("nextCursor"):
        raise Unavailable("task-list 조회 불완전")
    return {t["id"]: t.get("status") or "" for t in res["tasks"] if isinstance(t, dict) and t.get("id")}


def git_dirty(path: str):
    """작업 공간 미커밋·미추적 여부. 조회 실패는 None"""
    try:
        p = subprocess.run(["git", "-C", path, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(p.stdout.strip()) if p.returncode == 0 else None


def transcript_kept(dispatch: str) -> bool:
    """대화 기록 보관 확인. 읽기 명령으로 실제 읽히는지 봄"""
    return bool(sw.orca_json(["orchestration", "worker-read", "--dispatch", dispatch, "--source", "transcript", "--json", "--limit", "1"]))


def screen_verdict(t: dict):
    """(상태, 사유|None). 자동 대상은 입력 칸이 비어 있는('idle') 창뿐. 승인 대기는 멈춘 워커 카드 담당, 쓰던 글·판별 불가는 건드리지 않음"""
    pc = sw.pc
    if pc is None or getattr(pc, "screen_state", None) is None:
        return "unknown", "화면 판별 모듈 없음"
    if pc.classify(t.get("preview")) == "approval":
        return "approval", "승인 대기 창(멈춘 워커 카드 경로 담당)"
    st = pc.screen_state(t.get("preview"))
    return (st, None) if st == "idle" else (st, f"입력 칸이 비어 있지 않거나 판별 불가({st})")


# ---- 판정 ----------------------------------------------------------------------------------------
def decide(f: dict, ctx: dict):
    """한 워커 판정. f 는 조회 결과를 정리한 사실(facts), ctx 는 {busy, main, limits, now}.
    (대상 항목|None, 사유|None, 사람 확인 여부). 첫 회차 수집과 release 직전 재검증이 같은 함수를 씀"""
    t, res = f["term"], f["resource"]
    if f["worker"] not in DONE_WORKER or f["dispatch"] not in DONE_DISPATCH or f["terminal_state"] != "retained":
        return None, "끝난 워커 아님", False
    if not t:
        return None, GONE, False
    h = t["handle"]
    if h in ctx["busy"]:
        return None, "같은 창을 쓰는 진행 워커 있음", False
    if h == ctx["main"] or sw.HUB_RE.search(str(t.get("worktreePath", ""))):
        return None, "코디네이터·메인 창", False
    if f["task"] not in DONE_TASK:
        return None, f"작업 미종료({safe(f['task']) or '불명'})", False
    held = res.get("retainedReason") or (res.get("releaseState") if res.get("releaseState") != FREE else "")
    if res.get("ownershipState") != OWNED or held:
        return None, f"소유권·유지 상태 확인 필요(소유 {safe(res.get('ownershipState')) or '없음'}, 표식 {safe(held) or '없음'})", True
    _st, why = screen_verdict(t)
    if why:
        return None, why, False
    last = t.get("lastOutputAt")
    if not isinstance(last, (int, float)):
        return None, "출력 시각 불명", False
    idle = int((ctx["now"] - last / 1000) // 60)
    if idle < ctx["limits"].idle_min:
        return None, f"idle {idle}분 < {int(ctx['limits'].idle_min)}분", False
    dirty = git_dirty(t.get("worktreePath") or "")
    if dirty is None:
        return None, "작업 공간 git 조회 실패", False
    if dirty:
        return None, "작업 공간 미커밋·미추적 있음", False
    if (res.get("archive") or {}).get("status") != "captured" and not transcript_kept(f["id"]):
        return None, "대화 기록 보관 확인 안 됨", False
    return {"kind": "release", "dispatch": f["id"], "run": f["run"], "task_id": f["task_id"], "handle": h, "title": safe(t.get("title")), "idle": idle,
            "evidence": "작업 종료 · 소유 · 입력 칸 비어 있음 · 미커밋·미추적 0 · 대화 기록 보관 확인"}, None, False


def facts_from_list(w: dict, terms: dict, tasks: dict) -> dict:
    return {"id": w.get("dispatchId"), "run": w.get("runId"), "task_id": w.get("taskId"), "worker": w.get("workerState"), "dispatch": w.get("dispatchStatus"),
            "terminal_state": w.get("terminalState"), "resource": w.get("resource") or {}, "term": terms.get(w.get("agentTerminalHandle")),
            "task": tasks.get(w.get("taskId"), "")}


def facts_from_show(dispatch: str, run: str, task_id: str):
    """release 직전 한 건 재조회(worker-show·terminal show·task-list). 조회 실패는 None"""
    res = sw.orca_json(["orchestration", "worker-show", "--dispatch", dispatch, "--json", "--full"])
    if not res or not isinstance(res.get("worker"), dict):
        return None
    handle = res["worker"].get("agentTerminalHandle")
    tres = sw.orca_json(["terminal", "show", "--terminal", handle or "-", "--json"])
    term = (tres or {}).get("terminal")
    try:
        status = task_statuses(run).get(task_id, "")
    except Unavailable:
        return None
    return {"id": dispatch, "run": run, "task_id": task_id, "worker": res["worker"].get("state"), "dispatch": (res.get("dispatch") or {}).get("status"),
            "terminal_state": ((res.get("projection") or {}).get("resource") or {}).get("terminalState"),
            "resource": res.get("terminalResource") or {}, "term": term if isinstance(term, dict) and term.get("handle") == handle and term.get("connected") is True else None,
            "task": status}


def find_dups(terms: list, workers: list, main_handle):
    """(중복 코디네이터 목록, 판정 불가 허브 경로 목록). 전체 허브 경로 기준으로 묶고, 워커 창·메인 창은 코디네이터로 세지 않음"""
    worker_handles = {w.get("agentTerminalHandle") for w in workers}
    groups = {}
    for t in terms:
        path = str(t.get("worktreePath", ""))
        if sw.HUB_RE.search(path) and t.get("agentIdentity") == "claude" and t.get("connected") is True \
                and t["handle"] not in worker_handles and t["handle"] != main_handle:
            groups.setdefault(path, []).append(t)
    out, unsure = [], []
    for path, ts in sorted(groups.items()):
        if len(ts) < 2:
            continue
        ts = sorted(ts, key=lambda x: x.get("lastOutputAt") or 0, reverse=True)
        if not ts[0].get("lastOutputAt") or ts[0]["lastOutputAt"] == ts[1].get("lastOutputAt"):
            unsure.append(path)
            continue
        for t in ts[1:]:
            out.append({"kind": "dup_coord", "hub": path, "handle": t["handle"], "title": safe(t.get("title")), "keep": safe(ts[0].get("title")),
                        "state": screen_verdict(t)[0]})
    return out, unsure


def collect(limits: Limits, now: float):
    """(후보 목록, 제외 목록 [(대상, 사유)]). 조회 실패·불완전은 Unavailable"""
    terms = list_terminals()
    by_handle = {t["handle"]: t for t in terms}
    retained = list_workers("retained", limits.pages)
    active = list_workers("active", limits.pages)
    main_handle = mt.read_handle(MAIN_ENV)
    ctx = {"busy": {w.get("agentTerminalHandle") for w in active}, "main": main_handle, "limits": limits, "now": now}
    items, unsure = find_dups(terms, retained + active, main_handle)
    skips = [(f"허브 {p}", "코디네이터 최근 출력 비교 불가") for p in unsure]
    cache = {}
    gone = 0
    for w in retained:
        if w.get("workerState") not in DONE_WORKER or w.get("dispatchStatus") not in DONE_DISPATCH or w.get("terminalState") != "retained":
            continue
        run = w.get("runId")
        if run not in cache:
            cache[run] = task_statuses(run)
        it, why, human = decide(facts_from_list(w, by_handle, cache[run]), ctx)
        if it:
            items.append(it)
        elif why == GONE:
            gone += 1  # 이미 닫힌 창은 한 줄로 묶음(할 일 없음)
        elif human:
            t = by_handle.get(w.get("agentTerminalHandle")) or {}
            items.append({"kind": "human", "dispatch": w.get("dispatchId"), "handle": t.get("handle"), "title": safe(t.get("title")), "why": why})
        else:
            skips.append((w.get("dispatchId"), why))
    if gone:
        skips.append((f"창 없는 워커 {gone}건", "이미 닫힘"))
    return items, skips, ctx


# ---- 동작 ----------------------------------------------------------------------------------------
def recheck(it: dict, ctx: dict):
    """release 직전 재검증. (통과 여부, 사유). 조회 실패·달라진 점이 하나라도 있으면 통과 못함"""
    f = facts_from_show(it["dispatch"], it["run"], it["task_id"])
    if f is None:
        return False, "재조회 실패"
    new, why, _human = decide(f, {**ctx, "now": time.time()})
    if not new or new["handle"] != it["handle"]:
        return False, why or "창이 달라짐"
    return True, ""


def apply(items: list, ctx: dict) -> list:
    """release 대상만 실행. [(항목, 결과 문구)]"""
    out = []
    for it in items:
        if it["kind"] != "release":
            continue
        ok, why = recheck(it, ctx)
        if not ok:
            out.append((it, f"건너뜀: 재검증 — {why}"))
            continue
        rc, _d = mt.run_orca(ORCA, ["orchestration", "worker-release", "--dispatch", it["dispatch"], "--json"], ORCA_TIMEOUT_S)
        out.append((it, "worker-release 완료" if rc == 0 else f"worker-release 실패 rc={rc}"))
    return out


def describe(it: dict) -> str:
    if it["kind"] == "dup_coord":
        return f"[중복 코디네이터·목록만] {it['hub']} · {it['title'] or '제목 없음'} · 유지 후보: {it['keep'] or '제목 없음'}"
    if it["kind"] == "human":
        return f"[사람 확인] {it['dispatch']} · {it['title'] or '제목 없음'} · {it['why']}"
    return f"[release 대상] {it['dispatch']} · {it['title'] or '제목 없음'} · idle {it['idle']}분 · {it['evidence']}"


def run(limits: Limits, do_apply: bool, now: float | None = None):
    """조회·판정·(옵션)동작·기록 전체를 잠금으로 직렬화. 겹친 실행은 건너뜀"""
    with card_actions.flock(LOCK, wait=False) as got:
        return _run(limits, do_apply, now) if got else (0, {"status": "busy", "mode": "apply" if do_apply else "dry-run"})


def _run(limits: Limits, do_apply: bool, now: float | None):
    now = time.time() if now is None else now
    mode = "apply" if do_apply else "dry-run"
    try:
        items, skips, ctx = collect(limits, now)
    except Unavailable as e:
        log(f"{mode} unavailable {safe(e)} — 아무것도 안 함")
        return 1, {"status": "unavailable", "mode": mode, "error": safe(e)}
    st = load_state()
    first = do_apply and not st.get("applied_once")  # 첫 --apply 는 목록만. 다음 실행부터 동작
    results = apply(items, ctx) if do_apply and not first else []
    if do_apply:
        rw.save_state_at(STATE, {"applied_once": st.get("applied_once") or now})
    status = "first_apply_listing" if first else ("applied" if results else "listed")
    done = {id(i): r for i, r in results}
    count = {k: sum(i["kind"] == k for i in items) for k in ("release", "human", "dup_coord")}
    log(f"{mode} {status} release {count['release']}건 사람 확인 {count['human']}건 중복 코디네이터 {count['dup_coord']}건 제외 {len(skips)}건")
    for i in items:
        log(f"  {describe(i)}" + (f" ⇒ {done[id(i)]}" if id(i) in done else ""))
    for who, why in skips:
        log(f"  [제외] {who} — {why}")
    return 0, {"status": status, "mode": mode, "count": len(items), "items": items, "skipped": [{"target": w, "reason": r} for w, r in skips],
               "results": [{"target": i.get("dispatch"), "result": r} for i, r in results]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="session_gc.py", description="끝난 워커 창 정리. 기본은 시험 모드(조회만). 대화형 입력 없음.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="실제 동작(worker-release 만). 없으면 시험 모드")
    p.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    a = p.parse_args(argv)
    rc, res = run(Limits.from_env(), a.apply)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        label = "실행" if a.apply else "시험 모드"
        print(f"session_gc: {label} · {res['status']}" + (f" · 후보 {res['count']}건 · 제외 {len(res['skipped'])}건" if "count" in res else ""))
        for i in res.get("items", []):
            print("  " + describe(i))
        for s in res.get("skipped", []):
            print(f"  [제외] {s['target']} — {s['reason']}")
        for r in res.get("results", []):
            print(f"  결과 {r['target']}: {r['result']}")
    if rc:
        print(f"ERROR: {res.get('error')}. Orca 실행 여부 확인 뒤 다음 주기에 재시도", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
