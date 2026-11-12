#!/usr/bin/env python3
"""주기 세션 정리. 중복 코디네이터와 끝난 워커 창을 찾아 판정 근거를 출력·기록함. 기본은 시험 모드(조회만), --apply 일 때만 동작.

대상: (1) 같은 허브 폴더(coord-*-hub)의 코디네이터 2개 이상 → 최근 출력이 있는 쪽 유지, 나머지는 'HANDOFF 작성 후 종료 요청' 한 줄 전송(직접 닫지 않음)
      (2) 끝난 워커(succeeded) 창 → 입력 대기·idle IDLE_MIN 분 이상·작업 공간 미커밋 0·대화 기록 보관 확인 뒤 worker-release 또는 terminal close
제외: 승인 대기 창(멈춘 워커 카드 경로 담당), 사용자 소유·유지 표식 창, 코디네이터·메인 창, 같은 창을 쓰는 진행 워커.
조회 실패·판정 불가는 아무것도 하지 않음. 감지·화면 판정은 stall_watch·panel_collect 를 import 해 재사용함.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import card_actions  # noqa: E402
import main_terminal as mt  # noqa: E402
import restart_watch as rw  # noqa: E402
import stall_watch as sw  # noqa: E402


@dataclass(frozen=True)
class Limits:
    """정리 기준 한 곳 모음. 환경 변수로만 바꿈"""
    idle_min: float  # 끝난 워커 창이 이 시간(분) 이상 출력 없어야 대상
    handoff_h: float  # 같은 창에 종료 요청을 다시 보내지 않는 시간
    pages: int

    @classmethod
    def from_env(cls) -> "Limits":
        def num(name, default):
            try:
                v = float(os.environ.get(name, ""))
                return v if v > 0 else default
            except ValueError:
                return default
        return cls(num("LRM_GC_IDLE_MIN", 30.0), num("LRM_GC_HANDOFF_H", 6.0), sw.Limits.from_env().pages)


DIR = card_actions.DIR
LOG = os.environ.get("LRM_GC_LOG") or os.path.join(DIR, "session-gc.log")  # 로그 경로는 여기 한 곳
STATE = os.path.join(DIR, "session-gc.json")
LOCK = os.path.join(DIR, ".session-gc.lock")
MAIN_ENV = os.environ.get("LRM_MAIN_ENV") or os.path.join(DIR, "main.env")
GONE = "창 없음"
ORCA = sw.ORCA
ORCA_TIMEOUT_S = sw.ORCA_TIMEOUT_S
GIT_TIMEOUT_S = 20
LIST_ARGS = ["terminal", "list", "--json", "--limit", "1000"]
HANDOFF_TEXT = "[세션 정리] 같은 허브에 코디네이터 창이 2개라 이 창을 종료 대상으로 표시함. HANDOFF 를 작성한 뒤 이 창을 종료해 주세요. 계속 쓸 창이면 무시해도 됨."

EPILOG = """종료 코드: 0 정상(후보 없음·시험 모드 포함), 1 조회 실패(아무것도 안 함), 2 인자 오류
환경: LRM_GC_IDLE_MIN 끝난 워커 idle 기준(분, 기본 30), LRM_GC_HANDOFF_H 종료 요청 재전송 간격(시간, 기본 6),
      LRM_GC_LOG 로그 파일(기본 $LRM_HUB_DIR/session-gc.log), LRM_HUB_DIR, LRM_ORCA, LRM_MAIN_ENV, HUBS_TSV
예:
  session_gc.py                 # 시험 모드: 후보·판정 근거만 출력(orca 변경 명령 0회)
  session_gc.py --json          # 같은 내용을 JSON 으로
  session_gc.py --apply         # 실제 동작. 첫 --apply 는 목록만 보여주고 다음 실행부터 동작
"""


def log(line: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {line}\n")
    except OSError as e:
        print(f"WARN: 로그 기록 실패({e.strerror}). LRM_GC_LOG 경로 확인", file=sys.stderr)


def load_state() -> dict:
    d = rw.load_state_at(STATE)
    return d if isinstance(d.get("handoff"), dict) else {"handoff": {}}


def screen_state(t: dict) -> str:
    return sw.pc.classify(t.get("preview")) if sw.pc else "unknown"


def git_dirty(path: str):
    """작업 공간 미커밋(추적·미추적 포함) 여부. 조회 실패는 None"""
    try:
        p = subprocess.run(["git", "-C", path, "status", "--porcelain"], capture_output=True, text=True, timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(p.stdout.strip()) if p.returncode == 0 else None


def transcript_kept(dispatch: str) -> bool:
    """대화 기록(transcript) 보관 확인. 읽기 명령으로 실제 읽히는지 봄"""
    res = sw.orca_json(["orchestration", "worker-read", "--dispatch", dispatch, "--source", "transcript", "--json", "--limit", "1"])
    return bool(res)


def find_dups(terms: list, main_handle):
    """(제거 후보 목록, 판정 불가 허브 목록). 허브 폴더 안 연결된 claude 창이 2개 이상이면 최근 출력 쪽 유지"""
    groups = {}
    for t in terms:
        m = sw.HUB_RE.search(str(t.get("worktreePath", "")))
        if m and t.get("agentIdentity") == "claude" and t.get("connected") is True:
            groups.setdefault(m.group(1), []).append(t)
    out, unsure = [], []
    for slug, ts in sorted(groups.items()):
        if len(ts) < 2:
            continue
        ts = sorted(ts, key=lambda x: x.get("lastOutputAt") or 0, reverse=True)
        if not ts[0].get("lastOutputAt") or ts[0]["lastOutputAt"] == ts[1].get("lastOutputAt"):
            unsure.append(slug)
            continue
        for t in ts[1:]:
            out.append({"kind": "dup_coord", "slug": slug, "handle": t["handle"], "title": rw.clean(t.get("title") or ""),
                        "keep": rw.clean(ts[0].get("title") or ""), "state": screen_state(t)})
    return out, unsure


def judge_worker(w: dict, t, busy_handles: set, main_handle, limits: Limits, now: float):
    """끝난 워커 한 건 판정. (대상 항목|None, 제외 사유|None)"""
    d = w.get("dispatchId")
    res = w.get("resource") or {}
    if not t:
        return None, GONE
    h = t["handle"]
    if h in busy_handles:
        return None, "같은 창을 쓰는 진행 워커 있음"
    if h == main_handle or sw.HUB_RE.search(str(t.get("worktreePath", ""))):
        return None, "코디네이터·메인 창"
    if res.get("releaseState") in ("release_pending", "released") or res.get("retainedReason"):
        return None, f"유지·사용자 소유 표식({res.get('retainedReason') or res.get('releaseState')})"
    state = screen_state(t)
    if state == "approval":
        return None, "승인 대기 창(멈춘 워커 카드 경로 담당)"
    if state != "idle":
        return None, f"입력 대기 아님({state})"
    last = t.get("lastOutputAt")
    if not isinstance(last, (int, float)):
        return None, "출력 시각 불명"
    idle = int((now - last / 1000) // 60)
    if idle < limits.idle_min:
        return None, f"idle {idle}분 < {int(limits.idle_min)}분"
    dirty = git_dirty(t.get("worktreePath") or "")
    if dirty is None:
        return None, "작업 공간 git 조회 실패"
    if dirty:
        return None, "작업 공간 미커밋 있음"
    kept = (res.get("archive") or {}).get("status") == "captured" or transcript_kept(d)
    if not kept:
        return None, "대화 기록 보관 확인 안 됨"
    method = "worker-release" if res.get("ownershipState") == "owned" else "terminal-close"
    return {"kind": "done_worker", "dispatch": d, "handle": h, "title": rw.clean(t.get("title") or ""), "idle": idle,
            "method": method, "evidence": "미커밋 0 · 대화 기록 보관 확인 · 승인 대기 아님"}, None


def collect(limits: Limits, now: float):
    """(후보 목록, 제외 목록 [(대상, 사유)], 오류 문구|None)"""
    res = sw.orca_json(LIST_ARGS)
    if res is None or not isinstance(res.get("terminals"), list):
        return [], [], "terminal list 조회 실패"
    terms = [t for t in res["terminals"] if isinstance(t, dict) and t.get("handle")]
    by_handle = {t["handle"]: t for t in terms}
    retained = sw.list_workers("retained", limits.pages)
    active = sw.list_workers("active", limits.pages)
    if retained is None or active is None:
        return [], [], "worker-list 조회 실패"
    main_handle = mt.read_handle(MAIN_ENV)
    busy = {w.get("agentTerminalHandle") for w in active}
    items, unsure = find_dups(terms, main_handle)
    skips = [("허브 " + slug, "코디네이터 최근 출력 비교 불가") for slug in unsure]
    for w in retained:
        if w.get("workerState") != "succeeded" or w.get("dispatchStatus") != "completed" or w.get("terminalState") != "retained":
            continue
        it, why = judge_worker(w, by_handle.get(w.get("agentTerminalHandle")), busy, main_handle, limits, now)
        if it:
            items.append(it)
        else:
            skips.append((w.get("dispatchId"), why))
    gone = [w for w, why in skips if why == GONE]  # 이미 닫힌 창은 한 줄로 묶음(할 일 없음)
    skips = [(w, why) for w, why in skips if why != GONE]
    if gone:
        skips.append((f"창 없는 워커 {len(gone)}건", "이미 닫힘"))
    return items, skips, None


def apply(items: list, st: dict, limits: Limits, now: float) -> list:
    """후보별 실제 동작. [(항목, 결과 문구)]. 종료 요청은 창마다 handoff_h 시간에 1회"""
    out = []
    for it in items:
        if it["kind"] == "dup_coord":
            if it["state"] in ("approval", "unknown"):
                out.append((it, f"건너뜀: 화면 상태 {it['state']}"))
                continue
            if now - st["handoff"].get(it["handle"], 0) < limits.handoff_h * 3600:
                out.append((it, "건너뜀: 최근 요청함"))
                continue
            rc, _d = mt.run_orca(ORCA, ["terminal", "send", "--terminal", it["handle"], "--text", HANDOFF_TEXT, "--enter", "--json"], ORCA_TIMEOUT_S)
            if rc == 0:
                st["handoff"][it["handle"]] = now
            out.append((it, "종료 요청 전송" if rc == 0 else f"실패 rc={rc}"))
        else:
            args = ["orchestration", "worker-release", "--dispatch", it["dispatch"], "--json"] if it["method"] == "worker-release" \
                else ["terminal", "close", "--terminal", it["handle"], "--json"]
            rc, _d = mt.run_orca(ORCA, args, ORCA_TIMEOUT_S)
            out.append((it, f"{it['method']} 완료" if rc == 0 else f"{it['method']} 실패 rc={rc}"))
    st["handoff"] = {h: t for h, t in st["handoff"].items() if now - t < 7 * 24 * 3600}
    return out


def describe(it: dict) -> str:
    if it["kind"] == "dup_coord":
        return f"[중복 코디네이터] {it['slug']} · {it['title'] or '제목 없음'} → 종료 요청 대상 · 유지: {it['keep'] or '제목 없음'}"
    return f"[끝난 워커] {it['dispatch']} · {it['title'] or '제목 없음'} · idle {it['idle']}분 · {it['evidence']} → {it['method']}"


def run(limits: Limits, do_apply: bool, now: float | None = None):
    """조회·판정·(옵션)동작·기록 전체를 잠금으로 직렬화. 겹친 실행은 건너뜀"""
    with card_actions.flock(LOCK, wait=False) as got:
        return _run(limits, do_apply, now) if got else (0, {"status": "busy", "mode": "apply" if do_apply else "dry-run"})


def _run(limits: Limits, do_apply: bool, now: float | None):
    now = time.time() if now is None else now
    mode = "apply" if do_apply else "dry-run"
    items, skips, err = collect(limits, now)
    if err:
        log(f"{mode} unavailable {err} — 아무것도 안 함")
        return 1, {"status": "unavailable", "mode": mode, "error": err}
    st = load_state()
    first = do_apply and not st.get("applied_once")  # 첫 --apply 는 목록만. 다음 실행부터 동작
    results = []
    if do_apply and not first and items:
        results = apply(items, st, limits, now)
    if do_apply:
        st["applied_once"] = st.get("applied_once") or now
        rw.save_state_at(STATE, st)
    status = "first_apply_listing" if first else ("applied" if results else "listed")
    done = {id(i): r for i, r in results}
    log(f"{mode} {status} 후보 {len(items)}건(중복 코디네이터 {sum(i['kind'] == 'dup_coord' for i in items)}, 끝난 워커 {sum(i['kind'] == 'done_worker' for i in items)}) 제외 {len(skips)}건")
    for i in items:
        log(f"  {describe(i)}" + (f" ⇒ {done[id(i)]}" if id(i) in done else ""))
    for who, why in skips:
        log(f"  [제외] {who} — {why}")
    return 0, {"status": status, "mode": mode, "count": len(items), "items": items, "skipped": [{"target": w, "reason": r} for w, r in skips],
               "results": [{"target": i.get("dispatch") or i.get("handle"), "result": r} for i, r in results]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="session_gc.py", description="중복 코디네이터·끝난 워커 창 정리. 기본은 시험 모드(조회만). 대화형 입력 없음.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="실제 동작(종료 요청 전송·worker-release·terminal close). 없으면 시험 모드")
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
