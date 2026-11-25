#!/usr/bin/env python3
"""멈춘 기능 코디네이터 깨우기. 열린 기능 카드마다 코디네이터 작업 공간을 보고, 이어갈 일이 있는데 쉬는 창에 한 줄을 보냄.

왜: 코디네이터는 사용자 글·Slack 답·작업자 알림이 있어야 움직임. 알림을 놓치거나 '다음에는 …하겠습니다'로 끝내면 아무도 깨우지 않음
(11-25: 기능 21개 중 14개가 이렇게 멈춤). stall_watch 는 워커만 봄.
깨우지 않는 경우: 세션이 작업 중·백그라운드 명령 실행 중, 살아 있는 작업자 있음, 답을 기다리는 결정 카드·전송 대기 결정 있음, 검수·병합 대기, 창 없음, 너무 오래 쉼.
같은 기능 창이 둘 이상이면 깨우지 않고 경고만 올림(터미널 출력 시각으로는 맡은 창을 못 고름).
한 번 쉴 때마다 WAKE_MAX 회까지 깨우고, 그래도 멈추면 기능 스레드에 경고 1건을 올린 뒤 그 쉼이 끝날 때까지 조용함. 쉼이 끝났는지는 깨움 줄이 아닌 새 입력으로 판정.
쉰 시각은 세션 기록의 대화 시각으로 봄. 터미널 lastOutputAt 은 화면 갱신만으로 바뀌어 못 씀(5시간 쉰 창이 5분 전으로 나옴).
깨움 줄은 board_said 가 사용자 글로 세지 않음(WAKE_HEAD). 승인·병합·PR 은 이 줄로 풀리지 않음. 호출은 slack_socket.py 의 감시 루프.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import answer_store  # noqa: E402
import card_actions  # noqa: E402
import pending_judge  # noqa: E402
import restart_watch as rw  # noqa: E402  (상태 파일 저장 재사용)
import stall_watch as sw  # noqa: E402  (워커 목록·조용한 시간 재사용)
import work_remind  # noqa: E402  (작업판 조회 재사용)

flow = work_remind.flow_lib()
import board_said  # noqa: E402  (flow_lib 가 작업판 lib 경로를 넣음)

DIR = card_actions.DIR
STATE, LOCK = os.path.join(DIR, "coord-watch.json"), os.path.join(DIR, ".coord-watch.lock")
REPORT = os.environ.get("LRM_HUB_REPORT") or os.path.join(DIR, "hub_report.py")
REPORT_TIMEOUT_S = 30
OPEN_STAGES = ("understand", "design", "build", "verify")  # 접수(사용자 답 대기)와 검수·반영(카드가 닫힘)은 사용자 차례
USER_TURN_LABELS = ("review:batch", "review:batched")  # 작은 기능은 카드가 열린 채 모아 검수·병합을 기다림
WORKING = ("busy", "shell")  # 세션 등록부의 상태. shell 은 백그라운드 명령 실행 중
TAIL_BYTES = 400_000  # 세션 기록 끝에서 읽는 양. 긴 기록도 마지막 대화만 있으면 됨
WAKE_TEXT = ("코디네이터 이어가기: {mins}분째 멈춰 있음. ~/orca/board/bin/board-stage {epic} 로 다음 할 일을 보고 이어서 진행. 이 줄은 승인이 아님. "
             "사용자 답이 필요하면 창에만 두지 말고 hub_report.py decide 카드로 올리고, 검수·작업 창을 기다리면 그 창 화면에서 실제로 시작됐는지 확인. 외부 일을 기다리면 무엇인지 epic 코멘트에 남길 것")
WARN_TEXT = "코디네이터 멈춤: {name}\n- 무엇: {what}\n- 조치: 그 기능 창을 열어 이어서 진행하거나 닫기"
WARN_STUCK, WARN_DUP = "{n}번 깨웠지만 {mins}분째 진행이 없음", "같은 기능 창이 {n}개라 어느 창을 깨울지 모름. {mins}분째 멈춤"


@dataclass(frozen=True)
class Limits:
    """감시 기준 한 곳 모음. 환경 변수로만 바꿈"""
    idle_min: float = 20.0  # 이 시간(분) 넘게 대화가 없으면 멈춤
    gap_min: float = 20.0  # 깨움 사이 최소 간격
    max_age_min: float = 1440.0  # 이보다 오래 쉬면 깨우지 않음(끝난 창). 남은 작업은 work_remind 가 알림
    wake_max: int = 2
    per_run: int = 3  # 한 번에 깨우는 창 수. 한꺼번에 깨어나 한도를 쓰는 것을 막음
    quiet: tuple = ()

    @classmethod
    def from_env(cls) -> "Limits":
        def num(name, default):
            try:
                v = float(os.environ.get(name, ""))
                return v if v > 0 else default
            except ValueError:
                return default
        return cls(num("LRM_COORD_IDLE_MIN", cls.idle_min), num("LRM_COORD_GAP_MIN", cls.gap_min), num("LRM_COORD_MAX_AGE_MIN", cls.max_age_min),
                   int(num("LRM_COORD_WAKE_MAX", cls.wake_max)), int(num("LRM_COORD_PER_RUN", cls.per_run)),
                   sw.quiet_hours(os.environ.get("LRM_COORD_QUIET", sw.QUIET_DEFAULT)))


@dataclass
class Seen:
    """한 작업 공간의 세션들. last 마지막 대화 시각, said 깨움 줄이 아닌 마지막 입력 시각, working 작업 중인 세션이 있음"""
    last: float = 0.0
    said: float = 0.0
    working: bool = False


# ── 판정 (부수효과 없음) ──
def judge(e: dict, seen: Seen, waiting: bool, worker: bool, rec: dict, limits: Limits, now: float) -> tuple:
    """(할 일, 이유). 할 일: wake | warn | skip"""
    labels = e.get("labels") or []
    if e.get("status") == "closed" or not any(f"stage:{s}" in labels for s in OPEN_STAGES) or any(x in labels for x in USER_TURN_LABELS):
        return "skip", "사용자 차례 단계"
    if seen.working:
        return "skip", "작업 중"
    if not seen.last:
        return "skip", "대화 기록 없음"
    idle = (now - seen.last) / 60
    if idle < limits.idle_min:
        return "skip", "방금 대화함"
    if idle > limits.max_age_min:
        return "skip", "오래 쉼"
    if waiting:
        return "skip", "사용자 답 대기"
    if worker:
        return "skip", "작업자 진행 중"
    same = rec.get("said") == seen.said  # 깨움 뒤 새 입력이 없으면 같은 쉼
    n = rec.get("wakes", 0) if same else 0
    if n >= limits.wake_max:
        return ("skip", "이미 경고함") if rec.get("warned") else ("warn", f"{n}번 깨웠지만 멈춤")
    if same and now - rec.get("at", 0) < limits.gap_min * 60:
        return "skip", "깨운 지 얼마 안 됨"
    return "wake", f"{int(idle)}분째 멈춤"


# ── 원료 ──
def last_talk(path: str) -> tuple:
    """(마지막 대화 시각, 깨움 줄이 아닌 마지막 입력 시각). 압축 요약·시스템 줄은 대화로 안 셈"""
    last = said = 0.0
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("isMeta") or r.get("isSidechain") or r.get("type") not in ("user", "assistant"):
            continue
        at, body = board_said.stamp(r.get("timestamp")), (r.get("message") or {}).get("content")
        text = body if isinstance(body, str) else " ".join(str(b.get("text") or "") for b in body or [] if isinstance(b, dict))
        if text.startswith(board_said.NOISE_HEADS) and not text.startswith("<task-notification>"):
            continue
        last = max(last, at)
        # 자기 백그라운드 명령의 끝 알림은 새 입력이 아님: 세면 깨울 때마다 쉼이 새로 시작돼 끝없이 깨움
        if r["type"] == "user" and text.strip() and not text.startswith((board_said.COORD_WAKE_HEAD, "<task-notification>")):
            said = max(said, at)
    return last, said


def live_status() -> dict:
    """{세션 id: 상태} 살아 있는 세션만. 등록부를 못 읽으면 빈 dict"""
    out = {}
    for d in board_said.config_dirs():
        for p in glob.glob(os.path.join(glob.escape(d), "sessions", "*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    s = json.load(f)
                os.kill(int(s["pid"]), 0)
                out[s["sessionId"]] = s.get("status")
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return out


def seen_of(ws: str, status: dict) -> Seen:
    out = Seen()
    for p in board_said.transcripts(ws):
        sid = os.path.basename(p)[:-len(".jsonl")]
        if sid not in status:
            continue
        out.working = out.working or status[sid] in WORKING
        try:
            last, said = last_talk(p)
        except OSError:
            continue
        out.last, out.said = max(out.last, last), max(out.said, said)
    return out


def waiting_hubs(now: float) -> set:
    """답을 기다리는 결정(보낸 카드)과 아직 못 보낸 결정 보고가 있는 허브 이름·기능 카드 id"""
    rules = pending_judge.load_rules([DIR, os.path.join(HERE, "..")], warn=lambda m: None)
    store = answer_store.AnswerStore(DIR)
    all_cards = store.cards()
    sent = {c.get("decision_id") for c in all_cards}
    reports = flow.read_jsonl(os.environ.get("LRM_REPORT_OUTBOX") or os.path.join(DIR, flow.OUTBOX_NAME))
    by_did, out = {}, set()
    for r in reports:
        if r.get("decision_id"):
            by_did[r["decision_id"]] = r
            if r["decision_id"] not in sent and isinstance(r.get("at"), (int, float)) and now - r["at"] < 3600:
                out |= {r.get("hub"), r.get("card")}
    for c in work_remind.waiting(all_cards, rules, now):
        r = by_did.get(c["decision_id"]) or {}
        out |= {c.get("tag"), r.get("hub"), r.get("card")}
    return {x for x in out if x}


def busy_hubs(now: float) -> "set | None":
    """살아 있는 작업자를 둔 허브 이름. 조회 실패는 None(모르면 깨우지 않음)"""
    rows = sw.list_workers("active", 5)
    if rows is None:
        return None
    silent = sw.Limits.from_env().silence_min * 60
    out = set()
    tasks = {}
    for w in rows:
        at = ((w.get("projection") or {}).get("evidence") or {}).get("lastObservedAt")
        if w.get("workerState") != "ready" or not isinstance(at, (int, float)) or now - at / 1000 > silent:
            continue
        run = w.get("runId")
        if run not in tasks:
            tasks[run] = sw.run_tasks(run) or {}
        t = tasks[run].get(w.get("taskId"))
        if t and t["slug"]:
            out.add(t["slug"])
    return out


# ── 실행 ──
def warn(e: dict, ws: str, what: str) -> bool:
    cmd = ["python3", REPORT, "add", "--hub", flow.feature_hub(e), "--task", "없음", "--kind", "warn", "--card", e["id"], "--ws", ws,
           "--text", WARN_TEXT.format(name=flow.feature_name(e), what=what)]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=REPORT_TIMEOUT_S, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check(limits: Limits, dry: bool, now: "float | None" = None) -> tuple:
    with card_actions.flock(LOCK, wait=False) as got:
        return _check(limits, dry, time.time() if now is None else now) if got else (0, {"status": "busy", "rows": []})


def _check(limits: Limits, dry: bool, now: float) -> tuple:
    if sw.is_quiet(limits.quiet, now) and not dry:
        return 0, {"status": "quiet", "rows": []}
    try:
        epics = [c for c in work_remind.board_cards_all() if c.get("issue_type") == "epic" and c.get("status") != "closed"
                 and flow.FLOW_LABEL in (c.get("labels") or [])]
        workers = busy_hubs(now)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as err:
        return 1, {"status": f"unavailable {type(err).__name__}", "rows": []}
    if workers is None:
        return 1, {"status": "unavailable worker-list", "rows": []}
    waiting, status, st = waiting_hubs(now), live_status(), rw.load_state_at(STATE)
    rows, sent = [], 0
    for e in epics:
        ws, hub = os.path.expanduser(flow.coordinator_ws(e)), flow.feature_hub(e)
        if not ws or not os.path.isdir(ws):
            continue
        seen, rec = seen_of(ws, status), st.get(e["id"]) or {}
        act, why = judge(e, seen, e["id"] in waiting or hub in waiting, hub in workers, rec, limits, now)
        if act == "wake" and sent >= limits.per_run:
            act, why = "skip", "이번 회차 상한"
        rows.append({"epic": e["id"], "hub": hub, "act": act, "why": why, "mins": max(0, int((now - seen.last) // 60)) if seen.last else 0})
        if act == "skip" or dry:
            sent += act == "wake"
            continue
        mins, terms = rows[-1]["mins"], card_actions.ws_terminals(ws, writable_only=True) if act == "wake" else []
        if terms and len(terms) > 1:
            rows[-1].update(act="warn", why="창이 여럿")
            # 깨움을 다 쓴 것으로 적어 같은 쉼에는 다시 알리지 않음
            rec = {"said": seen.said, "wakes": limits.wake_max, "at": now, "warned": warn(e, ws, WARN_DUP.format(n=len(terms), mins=mins))}
        elif act == "warn":
            rec["warned"] = warn(e, ws, WARN_STUCK.format(n=rec.get("wakes", 0), mins=mins))
        else:
            res = card_actions.deliver_ws(ws, WAKE_TEXT.format(mins=mins, epic=e["id"]))
            rows[-1]["sent"] = res[0]
            sent += 1
            if res[0] not in (card_actions.SENT, card_actions.UNKNOWN):  # 막힘·창 없음은 횟수에 넣지 않고 다음 회차에 다시
                continue
            same = rec.get("said") == seen.said
            rec = {"said": seen.said, "wakes": (rec.get("wakes", 0) if same else 0) + 1, "at": now}
        st[e["id"]] = rec
        rw.save_state_at(STATE, st)
    return 0, {"status": "ok", "rows": rows}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="coord_watch.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="종료 코드: 0 정상, 1 작업판·작업자 조회 실패, 2 인자 오류\n예: coord_watch.py check --dry-run   # 보내지 않고 판정만 출력")
    c = p.add_subparsers(dest="cmd", required=True, metavar="{check}").add_parser("check", help="멈춘 코디네이터를 찾아 깨움")
    c.add_argument("--dry-run", action="store_true", help="보내지 않고 판정만 출력(조용한 시간도 무시)")
    c.add_argument("--json", action="store_true", help="결과를 JSON 으로")
    a = p.parse_args(argv)
    rc, res = check(Limits.from_env(), a.dry_run)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return rc
    acts = [r for r in res["rows"] if r["act"] != "skip"]
    print(f"coord_watch: {res['status']} 깨움 {sum(r['act'] == 'wake' for r in acts)} 경고 {sum(r['act'] == 'warn' for r in acts)}")
    for r in res["rows"] if a.dry_run else acts:
        print(f"  {r['act']:5}{r['hub']:38}{r['mins']:>5}분  {r['why']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
