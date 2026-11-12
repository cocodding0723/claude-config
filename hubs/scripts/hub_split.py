#!/usr/bin/env python3
"""허브 분할·병합 도구. 측정·판정(measure)은 읽기 전용, 제안·실행은 사용자 승인 결정 번호가 있어야 함.

설계 원본: hubs/docs/hub-split.md. 분할 신호는 작업 공간 수와 주제 갈림 두 가지뿐(워커 상한은 신호 아님).
주제 갈림은 스크립트가 판단하지 못하므로 메인이 --topic <slug>=<떼어 낼 작업 수> 로 넘김.
허브 목록 표(hubs.tsv) 여섯째 칸이 부모 허브 slug. 하위 허브는 한 층만 허용.

환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs) · LRM_HUBS_TSV 허브 목록 표 · LRM_INTAKE_ROUTES 접수 경로 표
      LRM_HUB_SPLIT_STATE 연속 판정·냉각 기록 · LRM_WS_ROOT 허브 기본 위치 · LRM_WS_GC 작업 공간 정리기 · LRM_ORCA orca 경로
종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자 오류, 3 환경 오류(orca 응답 없음 등 판정 불가), 4 안전 조건 미충족
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 복사 설치에서도 같은 폴더 모듈을 찾음
import main_terminal as mt  # noqa: E402

EXIT_IO, EXIT_USAGE, EXIT_ENV = 1, 2, 3
DAY = 86400


@dataclass(frozen=True)
class Limits:
    """판정 문턱. 운영 기록 없이 정한 추정값이라 이 블록 한 곳에서만 바꿈"""
    split_min_ws: int = 9          # 정리 후보를 뺀 하위 작업 공간 수. 상한 10 전에 제안하려고 1개 여유
    split_min_tasks: int = 5       # 떼어 낼 쪽 작업 수. 생기자마자 병합 조건에 걸리지 않게
    split_streak: int = 2          # 30분 간격 점검에서 연속 횟수
    split_gap_s: int = 3600        # 이 간격 안의 다음 점검만 연속으로 침(30분 점검 1회 누락 허용)
    merge_max_ws: int = 5          # 부모+하위 작업 공간 합. 합친 뒤 바로 분할 문턱에 닿지 않게
    merge_idle_days: int = 7       # 하위 허브 새 접수 0건 기간
    merge_streak: int = 3          # 하루 1회 점검에서 연속 일수
    merge_gap_s: int = 2 * DAY     # 다음 날 점검까지 허용 간격
    merge_min_step_s: int = 20 * 3600  # 같은 날 여러 번 점검해도 하루 1회로 침
    cooldown_days: int = 7         # 분할·병합 뒤 반대 제안 금지 기간


LIMITS = Limits()


class SplitError(Exception):
    """원인 — 조치 문장과 종료 코드"""

    def __init__(self, msg: str, code: int = EXIT_USAGE) -> None:
        super().__init__(msg)
        self.code = code


# ── 경로 ────────────────────────────────────────────────────────────────────────────────
def hub_dir() -> str:
    return os.path.expanduser(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs")


def env_path(var: str, name: str) -> str:
    return os.path.expanduser(os.environ.get(var) or os.path.join(hub_dir(), name))


def hubs_tsv() -> str:
    return env_path("LRM_HUBS_TSV", "hubs.tsv")


def routes_tsv() -> str:
    return env_path("LRM_INTAKE_ROUTES", "intake-routes.tsv")


def routes_log() -> str:
    return env_path("LRM_INTAKE_LOG", "intake-routes.jsonl")


def state_path() -> str:
    return env_path("LRM_HUB_SPLIT_STATE", "hub-split.state.json")


def ws_root() -> str:
    return os.path.expanduser(os.environ.get("LRM_WS_ROOT") or "~/orca/workspaces/LoveRevolutionMaker")


def expand(v: str) -> str:
    return os.path.expanduser(v) if v.startswith("~") else v


# ── 허브 목록 표 ────────────────────────────────────────────────────────────────────────
@dataclass
class Hub:
    slug: str
    name: str
    role: str
    ws_root: str
    repo: str
    parent: str  # 부모 slug, 없으면 ""

    @property
    def path(self) -> str:
        root = expand(self.ws_root) if self.ws_root not in ("", "-") else ws_root()
        return os.path.join(root, f"coord-{self.slug}-hub")

    @property
    def intake_name(self) -> str:
        """접수 경로 표의 대상 이름. intake.hub_path 와 같은 규칙(role-<이름>.txt)"""
        base = os.path.basename(self.role)
        return base[5:-4] if base.startswith("role-") and base.endswith(".txt") else ""


def read_hubs(path: str = "") -> list:
    path = path or hubs_tsv()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise SplitError(f"허브 목록 표 읽기 실패({path}: {e.strerror}) — LRM_HUBS_TSV 확인", EXIT_IO)
    hubs = []
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        c = [x.strip() for x in ln.split("\t")] + ["-"] * 6
        hubs.append(Hub(c[0], c[1], c[2], c[3], c[4], "" if c[5] in ("", "-") else c[5]))
    return hubs


# ── 기록(연속 판정·냉각) ─────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def locked(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield


def load_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise SplitError(f"기록 파일 손상({state_path()}: {e}) — 파일을 지우면 연속 횟수만 초기화됨", EXIT_IO)


def save_state(d: dict) -> None:
    path = state_path()
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def next_streak(prev: dict, hit: bool, now: float, gap: int, min_step: int = 0) -> dict:
    """연속 판정. prev={n, at}. 조건 불충족이면 0, 간격 안이면 +1, 간격 밖이면 1. min_step 안의 재점검은 횟수 유지"""
    if not hit:
        return {"n": 0, "at": now}
    n, at = int(prev.get("n") or 0), float(prev.get("at") or 0)
    if n and now - at < min_step:
        return {"n": n, "at": at}
    return {"n": n + 1 if n and now - at <= gap else 1, "at": now}


# ── 측정 ────────────────────────────────────────────────────────────────────────────────
def orca_json(run, args: list, what: str) -> dict:
    rc, d = run(args, 30)
    if rc != 0 or not isinstance(d, dict) or not d.get("ok", True):
        raise SplitError(f"{what} 실패 — Orca 실행 여부 확인(orca status). 판정하지 않음", EXIT_ENV)
    return d


def worktree_kids(run) -> dict:
    """{허브 경로: [하위 작업 공간 경로]}. 등록이 지워진 하위 id 는 뺌(ws_gc·create_worker_ws 와 같은 셈)"""
    wts = orca_json(run, ["worktree", "list", "--limit", "1000", "--json"], "orca worktree list").get("result", {}).get("worktrees", [])
    by_id = {w.get("id"): w.get("path") for w in wts}
    return {w.get("path"): [by_id[c] for c in (w.get("childWorktreeIds") or []) if c in by_id] for w in wts}


def gc_candidates(gc_json: str, no_gc: bool) -> set:
    """ws_gc --dry-run 이 지울 수 있다고 본 경로. 끝난 작업 공간을 치운 뒤의 수로 재려고 뺌"""
    if no_gc:
        return set()
    if gc_json:
        try:
            with open(gc_json, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            raise SplitError(f"--gc-json 읽기 실패({e}) — ws_gc.sh --dry-run --json 출력 파일 지정", EXIT_IO)
    else:
        gc = env_path("LRM_WS_GC", "ws_gc.sh")
        try:
            p = subprocess.run([gc, "--dry-run", "--json", "--no-slack", "--quiet"], capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
            d = json.loads(p.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            raise SplitError(f"정리기 시험 실행 실패({gc}: {e}) — --no-gc 로 정리 전 수를 쓰거나 LRM_WS_GC 확인", EXIT_ENV)
    return {i.get("path") for i in d.get("items", []) if i.get("verdict") == "candidate"}


def intake_counts(now: float, days: int) -> dict:
    """{접수 대상 이름: 최근 days 일 접수 수}. 접수 기록이 없으면 빈 dict"""
    out = {}
    try:
        with open(routes_log(), encoding="utf-8") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict) and float(d.get("at") or 0) >= now - days * DAY:
                    out[d.get("hub")] = out.get(d.get("hub"), 0) + 1
    except OSError:
        pass
    return out


def parse_topics(items: list) -> dict:
    out = {}
    for it in items or []:
        slug, sep, n = it.partition("=")
        if not sep or not n.isdigit():
            raise SplitError(f"--topic 형식 오류: {it} — <slug>=<떼어 낼 작업 수>, 예 ui-layer=6")
        out[slug] = int(n)
    return out


def judge(hubs: list, kids: dict, gc: set, intake: dict, topics: dict, state: dict, now: float, lim: Limits = LIMITS) -> list:
    """허브별 판정. state 의 streak 는 이번 점검을 반영한 값으로 갱신(저장은 호출 쪽)"""
    by_slug = {h.slug: h for h in hubs}
    eff = {h.slug: len([k for k in kids.get(h.path, []) if k not in gc]) for h in hubs}
    streaks = state.setdefault("streaks", {})
    events = state.get("events", {})
    rows = []
    for h in hubs:
        all_kids = kids.get(h.path)
        r = {"slug": h.slug, "name": h.name, "parent": h.parent or None, "registered": all_kids is not None,
             "workspaces": len(all_kids or []), "gc_candidates": len([k for k in all_kids or [] if k in gc]),
             "effective": eff[h.slug], "intake_7d": intake.get(h.intake_name, 0) if h.intake_name else None,
             "topic_tasks": topics.get(h.slug), "verdict": "none", "reasons": []}
        ev = events.get(h.slug, {})
        last = max(float(ev.get("split_at") or 0), float(ev.get("merge_at") or 0))
        cool = last and now - last < lim.cooldown_days * DAY
        st = streaks.setdefault(h.slug, {})
        # 분할: 하위 허브는 다시 나누지 않음(한 층)
        ws_hit = eff[h.slug] >= lim.split_min_ws
        topic_hit = (topics.get(h.slug) or 0) >= lim.split_min_tasks
        split_ok = ws_hit and topic_hit and not h.parent
        st["split"] = next_streak(st.get("split", {}), split_ok, now, lim.split_gap_s)
        if ws_hit:
            r["reasons"].append(f"정리 후 작업 공간 {eff[h.slug]}개(문턱 {lim.split_min_ws})")
            if h.parent:
                r["reasons"].append("하위 허브라 분할 안 함(한 층 제한) — 형제 허브 추가를 검토")
            elif not topic_hit:
                r["verdict"] = "watch"
                r["reasons"].append(f"주제 갈림 미판단 — 메인이 떼어 낼 작업 수를 --topic {h.slug}=N 으로 넘김(문턱 {lim.split_min_tasks})")
            else:
                r["reasons"].append(f"주제 갈림: 떼어 낼 작업 {topics[h.slug]}건(문턱 {lim.split_min_tasks})")
                if cool:
                    r["verdict"] = "cooldown"
                    r["reasons"].append(f"최근 분할·병합 뒤 {lim.cooldown_days}일 안 — 제안 보류")
                elif st["split"]["n"] < lim.split_streak:
                    r["verdict"] = "watch"
                    r["reasons"].append(f"연속 {st['split']['n']}/{lim.split_streak}회 — 다음 점검에서도 맞으면 제안")
                else:
                    r["verdict"] = "split"
        # 병합: 하위 허브만. 부모와 합한 수로 잼
        if h.parent:
            par = by_slug.get(h.parent)
            total = eff[h.slug] + (eff.get(h.parent, 0) if par else 0)
            idle = r["intake_7d"] == 0
            merge_ok = par is not None and total <= lim.merge_max_ws and idle
            st["merge"] = next_streak(st.get("merge", {}), merge_ok, now, lim.merge_gap_s, lim.merge_min_step_s)
            if par is None:
                r["reasons"].append(f"부모 허브 {h.parent} 가 목록에 없음 — 허브 목록 표 확인")
            elif merge_ok:
                r["reasons"].append(f"부모와 합한 작업 공간 {total}개(문턱 {lim.merge_max_ws} 이하), 최근 {lim.merge_idle_days}일 접수 0건")
                if cool:
                    r["verdict"] = "cooldown"
                    r["reasons"].append(f"최근 분할·병합 뒤 {lim.cooldown_days}일 안 — 제안 보류")
                elif st["merge"]["n"] < lim.merge_streak:
                    r["verdict"] = "watch"
                    r["reasons"].append(f"연속 {st['merge']['n']}/{lim.merge_streak}일 — 조건이 이어지면 제안")
                else:
                    r["verdict"] = "merge"
        else:
            st.pop("merge", None)
        if all_kids is None:
            r["reasons"].append("Orca 에 허브 작업 공간이 등록돼 있지 않음 — 수를 0 으로 침")
        r["streak"] = {k: v.get("n", 0) for k, v in st.items()}
        rows.append(r)
    return rows


def cmd_measure(a, run) -> int:
    now = a.now or time.time()
    hubs = read_hubs()
    kids = worktree_kids(run)
    gc = gc_candidates(a.gc_json, a.no_gc)
    intake = intake_counts(now, LIMITS.merge_idle_days)
    topics = parse_topics(a.topic)
    unknown = [s for s in topics if s not in {h.slug for h in hubs}]
    if unknown:
        raise SplitError(f"--topic 의 허브 없음: {', '.join(unknown)} — 허브 목록 표 첫 칸 중 하나")
    with locked(state_path()):
        state = load_state()
        rows = judge(hubs, kids, gc, intake, topics, state, now)
        if a.record:
            save_state(state)
    out = {"mode": "record" if a.record else "read-only", "at": int(now), "limits": asdict(LIMITS), "hubs": rows,
           "split": [r["slug"] for r in rows if r["verdict"] == "split"], "merge": [r["slug"] for r in rows if r["verdict"] == "merge"]}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            mark = {"split": "✂️ 분할 후보", "merge": "🔗 병합 후보", "watch": "👀 관찰", "cooldown": "⏸️ 냉각"}.get(r["verdict"], "· 없음")
            print(f"{mark}\t{r['slug']}\t작업 공간 {r['workspaces']}(정리 후 {r['effective']})\t접수 7일 {r['intake_7d'] if r['intake_7d'] is not None else '-'}"
                  + (f"\t{'; '.join(r['reasons'])}" if r["reasons"] else ""))
        if not a.record:
            print("(읽기 전용: 연속 횟수는 이번 점검을 넣은 가정값, 기록하지 않음. 주기 점검은 --record)")
    return 0


# ── 제안(결정 카드) ─────────────────────────────────────────────────────────────────────
SLUG_OK = "abcdefghijklmnopqrstuvwxyz0123456789-"
HUB_TAG = "main"  # 카드·보고는 메인 이름으로 남김(대상 허브는 본문에 씀)


def tool(var: str, name: str) -> str:
    """같은 폴더의 기존 도구 경로. 환경 변수로 바꿔 끼움(시험의 가짜 도구)"""
    return os.path.expanduser(os.environ.get(var) or os.path.join(os.path.dirname(os.path.realpath(__file__)), name))


def hub_report(args: list, dry: bool) -> dict:
    """hub_report.py add 호출. dry 면 형식 검사만(outbox 에 안 씀). 실패는 SplitError"""
    cmd = [sys.executable, tool("LRM_HUB_REPORT", "hub_report.py"), "add", "--hub", HUB_TAG, "--task", "없음", *args, "--json"]
    if dry:
        cmd.append("--dry-run")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise SplitError(f"보고 기록 실패: {p.stderr.strip()} — hub_report.py 오류 문구대로 고침", p.returncode if p.returncode in (1, 2) else EXIT_IO)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"raw": p.stdout.strip()}


def read_routes() -> list:
    """접수 경로 표 행 [(머리말, 대상)]. 주석·빈 줄 제외"""
    try:
        with open(routes_tsv(), encoding="utf-8") as f:
            return [tuple((ln.rstrip("\n").split("\t") + [""])[:2]) for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError as e:
        raise SplitError(f"접수 경로 표 읽기 실패({routes_tsv()}: {e.strerror}) — LRM_INTAKE_ROUTES 확인", EXIT_IO)


@dataclass
class Plan:
    """분할·병합 한 건. 제안 카드와 실행기가 같은 값을 씀"""
    kind: str
    parent: str
    child: str
    child_name: str = ""
    prefix: str = ""


def check_plan(pl: Plan, hubs: list) -> None:
    by = {h.slug: h for h in hubs}
    if pl.kind not in ("split", "merge"):
        raise SplitError("--kind 는 split|merge")
    if pl.kind == "split":
        if pl.parent not in by:
            raise SplitError(f"부모 허브 없음: {pl.parent} — 허브 목록 표 첫 칸 중 하나")
        if by[pl.parent].parent:
            raise SplitError(f"{pl.parent} 는 이미 하위 허브 — 한 층만 허용, 형제 허브로 나눔")
        if not pl.child or set(pl.child) - set(SLUG_OK) or pl.child in by:
            raise SplitError(f"--child 형식 오류·중복: {pl.child} — 소문자·숫자·하이픈, 목록에 없는 새 slug")
        if not pl.child_name or not pl.prefix:
            raise SplitError("분할은 --child-name 표시 이름·--prefix 새 접수 머리말 필요")
        if any(r[0] == pl.prefix for r in read_routes()):
            raise SplitError(f"머리말 '{pl.prefix}' 는 이미 접수 경로 표에 있음 — 새 머리말만 추가(기존 머리말은 부모에 둠)")
    else:
        h = by.get(pl.child)
        if not h or not h.parent:
            raise SplitError(f"병합 대상 {pl.child} 가 하위 허브가 아님 — 허브 목록 표 여섯째 칸 확인")
        pl.parent = h.parent


def card_text(pl: Plan, reasons: list) -> str:
    if pl.kind == "split":
        title = f"허브 분할 승인: {pl.parent} → {pl.child}"
        what = f"새 하위 허브 '{pl.child_name}', 머리말 '{pl.prefix}' 추가. 미착수 작업만 이관, 진행 작업은 끝까지 부모"
    else:
        title = f"허브 병합 승인: {pl.child} → {pl.parent}"
        what = "하위 허브를 부모로 합침: 장부 담당·접수 경로·작업 공간 부모를 되돌리고 하위 허브 종료"
    return "\n".join([title[:40], what, ("근거: " + "; ".join(reasons))[:200] if reasons else "근거: hub_split.py measure 결과"])


def cmd_propose(a, run) -> int:
    pl = Plan(a.kind, a.hub if a.kind == "split" else "", a.child, a.child_name, a.prefix)
    check_plan(pl, read_hubs())
    if not a.file and not a.no_doc:
        raise SplitError("결정 카드는 근거 문서 --file 필요(없으면 --no-doc 명시)")
    args = ["--kind", "decide", "--text", card_text(pl, a.reason), "--options", "1=승인,2=보류", "--recommend", "1",
            "--decision-id", a.decision_id, "--reversible", "보통"]
    args += [x for f in a.file for x in ("--file", f)] + (["--no-doc"] if a.no_doc else [])
    res = hub_report(args, dry=not a.record)
    if a.record:
        with locked(state_path()):
            st = load_state()
            st.setdefault("proposals", {})[a.decision_id] = {**asdict(pl), "hash": digest(asdict(pl)), "at": int(time.time())}
            save_state(st)
    out = {"mode": "record" if a.record else "dry-run", "decision_id": a.decision_id, "plan": asdict(pl), "report": res}
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else
          ("기록" if a.record else "[시험] 카드 형식 확인만, outbox 에 안 씀") + f": {a.decision_id} {card_text(pl, a.reason).splitlines()[0]}")
    return 0


# ── 실행기 ──────────────────────────────────────────────────────────────────────────────
EXIT_REFUSED = 4  # 안전 조건 미충족(승인 없음·진행 워커 남음·하위 허브 기동 실패 등). 더 진행하지 않음
APPROVE_PICK = 1  # 결정 카드 '1=승인'
NOTE_PARENT = ("[메인→허브] 허브 {kind}: {moved} 작업은 {child} 로 넘어감. 작업 목록에서 빼고 "
               "hub_status.py write 로 상태 파일을 다시 기록해 주세요. 진행 중 워커는 그대로 이 허브에서 마무리")
# 실패 단계 → 보상 순서. 보상 뒤 done 에서 빼는 단계(다시 실행하면 처음부터 다시 함)
UNDO = {
    "split": {"create_child": (["hubs"], ["create_child"]),
              "ledger_owner": (["ledger", "close", "hubs"], ["create_child", "redeliver", "ledger_owner"]),
              "routes": (["routes"], ["routes"])},
    "merge": {"ledger_owner": (["ledger"], ["ledger_owner"]),
              "routes": (["routes"], ["routes"]),
              "remove_row": (["hubs"], ["remove_row"])},
}
RERUN = {"idle_check"}  # 재실행 때도 건너뛰지 않는 안전 검사


def run_dir(did: str) -> str:
    return os.path.join(hub_dir(), "hub-split-runs", did)


def slack_inbox() -> str:
    return env_path("LRM_SLACK_INBOX", "slack-inbox.jsonl")


def write_text(path: str, text: str) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def intake_text(rid: str) -> str:
    """접수 기록의 원문. 재하달은 원문 그대로 보냄"""
    try:
        with open(routes_log(), encoding="utf-8") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("id") == rid:
                    return str(d.get("text") or "")
    except OSError:
        pass
    raise SplitError(f"접수 기록에 {rid} 없음 — 부모 허브가 보고한 미착수 작업 번호(C0012 형식)인지 확인", EXIT_USAGE)


def py_tool(var: str, name: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, tool(var, name), *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def card_answer(did: str, since: float):
    """결정 카드 버튼 답 중 제안 뒤 마지막 것의 선택 번호. 답 없으면 None"""
    pick = None
    try:
        with open(slack_inbox(), encoding="utf-8") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if (isinstance(d, dict) and d.get("decision_id") == did and d.get("type") == "button"
                        and float(d.get("received_at") or 0) >= since):
                    pick = d.get("pick")
    except OSError:
        pass
    return pick


def check_approved(did: str, prop: dict) -> None:
    pick = card_answer(did, float(prop.get("at") or 0))
    if pick != APPROVE_PICK:
        why = "답 없음" if pick is None else f"선택 {pick}(승인 아님)"
        raise SplitError(f"결정 {did} 승인 확인 실패: {why} — 사용자가 카드에서 '승인'을 고른 뒤 실행", EXIT_REFUSED)


def hub_live(run, path: str):
    """작업 공간에 살아 있는 터미널·워커가 있으면 True, 없으면 False, 조회 실패면 None(판정 불가)"""
    rc, d = run(["terminal", "list", "--limit", "1000", "--json"], 30)
    if rc != 0 or not isinstance(d, dict):
        return None
    terms = (d.get("result") or {}).get("terminals")
    if not isinstance(terms, list):
        return None
    if any(str(t.get("worktreePath") or "").rstrip("/") == path.rstrip("/") and t.get("connected") is True for t in terms):
        return True
    rc, d = run(["orchestration", "worker-list", "--full", "--limit", "100", "--json"], 30)
    if rc != 0 or not isinstance(d, dict) or not isinstance((d.get("result") or {}).get("workers"), list):
        return None
    for w in d["result"]["workers"]:
        wt = str(((w.get("resource") or {}).get("worktreeId")) or "").split("::")[-1]
        if wt.rstrip("/") == path.rstrip("/") and w.get("terminalState") not in ("released", "reclaimable"):
            return True
    return False


class Runner:
    """단계 목록을 순서대로 실행. apply 가 아니면 할 일만 적음.
    apply 는 run.json(불변 계획)·progress.json(끝난 단계·전송·장부 이관 기록)을 남겨 재실행 시 이어서 함"""

    def __init__(self, spec: dict, did: str, apply: bool, run):
        self.spec, self.did, self.apply, self.run = spec, did, apply, run
        self.pl = Plan(**spec["plan"])
        self.moves, self.files, self.role_file = spec["moves"], spec["files"], spec["role_file"]
        self.hubs = {h["slug"]: Hub(**h) for h in spec["hubs"]}  # 시작 시점 허브 표. 표에서 행이 빠져도 경로를 앎
        self.prog_path = os.path.join(run_dir(did), "progress.json")
        self.prog = {"done": [], "sent": {}, "ledger": []}
        if apply and os.path.exists(self.prog_path):
            self.prog.update(json.loads(read_text(self.prog_path)))
        self.log = []

    def save(self):
        if self.apply:
            os.makedirs(run_dir(self.did), exist_ok=True)
            write_text(self.prog_path, json.dumps(self.prog, ensure_ascii=False, indent=2))

    def hub(self, slug: str) -> Hub:
        if slug in self.hubs:
            return self.hubs[slug]
        par = self.hubs[self.pl.parent]
        return Hub(slug, self.pl.child_name, self.role_file, par.ws_root, par.repo, self.pl.parent)

    def backup_path(self, src: str) -> str:
        return os.path.join(run_dir(self.did), "backup", os.path.basename(src))

    def terminal(self, slug: str) -> str:
        import intake  # 슬랙 전송 모듈을 끌어오므로 실제 하달 단계에서만 불러옴
        h, why = intake.find_hub_terminal(self.run, self.hub(slug).path)
        if not h:
            raise SplitError(f"{slug} 허브 터미널 확인 실패({why}) — 허브 상태 확인 뒤 같은 명령으로 다시 실행(끝난 단계는 건너뜀)", EXIT_REFUSED)
        return h

    def send(self, slug: str, text: str) -> str:
        """'sent'(제출 확인) | 'rejected'(입력 전 거절) | 'unconfirmed'·'unknown'(들어갔을 수 있음). 화면 확인은 send_confirmed 가 함"""
        import intake
        return intake.send_confirmed(self.run, self.terminal(slug), text, intake.hub_target)

    def report(self, when: str) -> str:
        verb = {"split": "분할", "merge": "병합"}[self.pl.kind]
        text = f"{'' if self.apply else '(시험) '}허브 {verb} {when}: {self.pl.parent} ↔ {self.pl.child} ({self.did})"
        hub_report(["--kind", "info", "--text", text] + [x for f in self.files for x in ("--file", f)], dry=not self.apply)
        return text

    # 단계: 설명 문자열 반환. apply 가 아니면 바꾸지 않음
    def backup(self):
        d = os.path.join(run_dir(self.did), "backup")
        if self.apply:
            os.makedirs(d, exist_ok=True)
            for src in (hubs_tsv(), routes_tsv()):
                if not os.path.exists(self.backup_path(src)):  # 재실행 때 첫 백업을 덮지 않음
                    write_text(self.backup_path(src), read_text(src))
            if len(os.listdir(d)) < 2:
                raise SplitError(f"백업 2개 확인 실패: {d}", EXIT_IO)
        return f"허브 목록 표·접수 경로 표 백업 → {d}"

    def report_before(self):
        return "보고: " + self.report("시작")

    def report_after(self):
        return "보고: " + self.report("완료")

    def create_child(self):
        pl, par = self.pl, self.hubs[self.pl.parent]
        row = "\t".join([pl.child, pl.child_name, self.role_file, par.ws_root or "-", par.repo or "-", pl.parent])
        if not self.apply:
            return f"허브 목록 표에 행 추가({row.replace(chr(9), ' | ')}) → create_hub.sh 로 하위 허브 기동 → 터미널 확인"
        if not os.path.exists(expand(self.role_file)):
            raise SplitError(f"역할 파일 없음: {self.role_file} — 하위 허브 역할 파일을 먼저 만들고 다시 실행", EXIT_REFUSED)
        text = read_text(hubs_tsv())
        if not any(ln.split("\t")[0] == pl.child for ln in text.splitlines()):
            write_text(hubs_tsv(), text + ("" if text.endswith("\n") else "\n") + row + "\n")
        env = dict(os.environ)
        if par.ws_root not in ("", "-"):
            env["WS_ROOT"] = expand(par.ws_root)
        if par.repo not in ("", "-"):
            env["REPO"] = expand(par.repo)
        create = os.path.expanduser(os.environ.get("LRM_CREATE_HUB") or "~/.claude/skills/orca-hub-create/scripts/create_hub.sh")
        p = subprocess.run(["bash", create, "--slug", pl.child, "--name", pl.child_name, "--role-file", expand(self.role_file)],
                           capture_output=True, text=True, timeout=600, env=env, stdin=subprocess.DEVNULL)
        if p.returncode not in (0, 3):  # 3 = 이미 살아 있음(재실행)
            raise SplitError(f"하위 허브 생성 실패(rc={p.returncode}): {p.stderr.strip()[-200:]}", EXIT_REFUSED)
        return f"하위 허브 기동, 터미널 {self.terminal(pl.child)}"

    def pending(self):
        texts = {rid: intake_text(rid) for rid in self.moves}
        return f"미착수 {len(texts)}건: {', '.join(texts) or '없음'}"

    def redeliver(self):
        """작업 번호별 전송 기록. 확인된 건은 다시 보내지 않고, 들어갔는지 모르는 건은 사람 확인 전까지 멈춤"""
        sent = self.prog["sent"]
        for rid in self.moves:
            text = intake_text(rid)
            if not self.apply or sent.get(rid) == "sent":
                continue
            if sent.get(rid) == "uncertain":
                raise SplitError(f"{rid} 전송 결과 불확실 — 하위 허브 화면을 보고 들어갔으면 --mark-sent {rid}, 아니면 --resend {rid} 로 다시 실행", EXIT_REFUSED)
            res = self.send(self.pl.child, f"[{rid}] {text}")
            if res == "sent":
                sent[rid] = "sent"
            elif res != "rejected":
                sent[rid] = "uncertain"
            self.save()
            if res != "sent":
                raise SplitError(f"{rid} 하위 허브 전송 결과 {res} — " + ("보내지 않았으니 다시 실행" if res == "rejected" else f"화면 확인 뒤 --mark-sent {rid} 또는 --resend {rid}"), EXIT_REFUSED)
        return f"하위 허브로 원문 재하달 {len(self.moves)}건" + ("" if self.apply else "(시험)")

    def ledger_rows(self) -> list:
        p = py_tool("LRM_LEDGER_PY", "ledger.py", "list", "--json")
        if p.returncode != 0:
            raise SplitError(f"장부 읽기 실패: {p.stderr.strip()}", EXIT_IO)
        return json.loads(p.stdout or "[]")

    def ledger_owner(self):
        src, dst = (self.pl.parent, self.pl.child) if self.pl.kind == "split" else (self.pl.child, self.pl.parent)
        rows = {r["id"]: r for r in self.ledger_rows()}
        if self.pl.kind == "split":
            ids = list(self.moves)
        else:
            names = {src, self.hub(src).intake_name}
            ids = [i for i, r in rows.items() if r.get("owner") in names and r.get("state") != "완료"]
        moved = {m["id"] for m in self.prog["ledger"]}
        for rid in ids:
            if rid in moved:
                continue
            if rid not in rows:
                raise SplitError(f"장부에 {rid} 없음", EXIT_REFUSED)
            if self.apply:
                p = py_tool("LRM_LEDGER_PY", "ledger.py", "owner", rid, "--to", dst)
                if p.returncode != 0:
                    raise SplitError(f"장부 담당 변경 실패({rid}): {p.stderr.strip()}", EXIT_IO)
                self.prog["ledger"].append({"id": rid, "from": rows[rid]["owner"], "to": dst})
                self.save()
        return f"장부 담당 {src} → {dst}: {', '.join(ids) or '없음'}"

    def in_progress(self):
        return "진행 중 작업·작업 공간은 부모에 둠(완료 보고 경로 유지). PR 닫힌 뒤 relink 로 재연결"

    def routes(self):
        rows = read_text(routes_tsv()).splitlines()
        child_in, par_in = self.hub(self.pl.child).intake_name, self.hub(self.pl.parent).intake_name
        if self.pl.kind == "split":
            if any(ln.split("\t")[0] == self.pl.prefix for ln in rows):
                return f"접수 경로: 머리말 {self.pl.prefix} 이미 있음(재실행)"
            idx = next((i for i, ln in enumerate(rows) if ln.startswith("*\t")), len(rows))  # 기본 행('*') 앞에 둠
            rows.insert(idx, f"{self.pl.prefix}\t{child_in}")
            desc = f"접수 경로 추가: {self.pl.prefix} → {child_in}"
        else:
            hit = [i for i, ln in enumerate(rows) if not ln.startswith("#") and ln.split("\t")[1:2] == [child_in]]
            for i in hit:
                rows[i] = rows[i].split("\t")[0] + "\t" + par_in
            desc = f"접수 경로 {len(hit)}줄: {child_in} → {par_in}"
        if self.apply:
            write_text(routes_tsv(), "\n".join(rows) + "\n")
        return desc

    def idle_check(self):
        """상태 파일 workers 칸이 있고 낡음 기준 이내일 때만 진행 워커 0 으로 인정"""
        import hub_status
        data, path, err = hub_status.load(self.pl.child)
        if err or data is None or not isinstance(data.get("workers"), list):
            raise SplitError(f"{self.pl.child} 상태 파일 없음·손상·workers 칸 없음({path}) — 진행 워커 0 을 확인할 수 없어 병합 안 함", EXIT_REFUSED)
        at = hub_status.parse_iso(data.get("updatedAt"))
        if at is None or time.time() - at.timestamp() > hub_status.stale_hours() * 3600:
            raise SplitError(f"{self.pl.child} 상태 파일이 낡음({data.get('updatedAt')}) — 허브에 hub_status.py write 를 요청한 뒤 다시 실행", EXIT_REFUSED)
        if data["workers"]:
            raise SplitError(f"{self.pl.child} 진행 워커 {len(data['workers'])}명 — 끝난 뒤 다시 실행", EXIT_REFUSED)
        return f"{self.pl.child} 진행 워커 0(상태 파일 {data.get('updatedAt')})"

    def relink_all(self):
        child, to = self.hub(self.pl.child).path, self.hub(self.pl.parent).path
        kids = worktree_kids(self.run).get(child, [])
        if self.apply:
            for k in kids:
                rc, _ = self.run(["worktree", "set", "--worktree", f"path:{k}", "--parent-worktree", f"path:{to}", "--json"], 30)
                if rc != 0:
                    raise SplitError(f"작업 공간 부모 변경 실패: {k} — Orca 확인 뒤 다시 실행", EXIT_ENV)
            left = worktree_kids(self.run).get(child, [])
            if left:
                raise SplitError(f"재연결 뒤에도 하위 허브 아래 {len(left)}곳 남음 — orca worktree show 로 확인", EXIT_ENV)
        return f"하위 허브 작업 공간 {len(kids)}곳(열린 PR 포함) → 부모 허브 아래로 재연결"

    def remove_row(self):
        rows = read_text(hubs_tsv()).splitlines()
        keep = [ln for ln in rows if ln.split("\t")[0] != self.pl.child]
        if self.apply and len(keep) != len(rows):
            write_text(hubs_tsv(), "\n".join(keep) + "\n")
        return f"허브 목록 표에서 {self.pl.child} 행 삭제({len(rows) - len(keep)}줄)"

    def close_child(self):
        if not self.apply:
            return f"{self.pl.child} 코디네이터 터미널 종료(작업 공간 폴더는 지우지 않음)"
        if self.pl.kind == "merge":
            self.idle_check()  # 종료 직전 재확인
        import intake
        h, _ = intake.find_hub_terminal(self.run, self.hub(self.pl.child).path)
        if h:
            rc, _ = self.run(["terminal", "close", "--terminal", h], 30)
            if rc != 0:
                raise SplitError(f"{self.pl.child} 터미널 종료 실패({h}) — Orca 확인 뒤 다시 실행", EXIT_ENV)
        return f"{self.pl.child} 터미널 종료({h or '이미 없음'})"

    def parent_status(self):
        moved = ", ".join(self.moves) if self.pl.kind == "split" else "하위 허브의"
        text = NOTE_PARENT.format(kind={"split": "분할", "merge": "병합"}[self.pl.kind], moved=moved or "없음",
                                  child=self.pl.child if self.pl.kind == "split" else self.pl.parent)
        if self.apply:
            res = self.send(self.pl.parent, text)
            if res != "sent":
                raise SplitError(f"부모 허브 전송 결과 {res} — 화면 확인 뒤 다시 실행", EXIT_REFUSED)
        return f"부모 허브에 상태 파일 재기록 요청: {text[:60]}…"

    def steps(self):
        if self.pl.kind == "split":
            return ["backup", "report_before", "create_child", "pending", "redeliver", "ledger_owner", "in_progress", "routes", "parent_status", "report_after"]
        return ["backup", "report_before", "idle_check", "ledger_owner", "routes", "relink_all", "remove_row", "close_child", "parent_status", "report_after"]

    # 보상: 설계의 '실패·되돌리기' 표. 결과를 검증해 문자열로 돌려줌
    def undo(self, what: str) -> str:
        if what in ("hubs", "routes"):
            src = hubs_tsv() if what == "hubs" else routes_tsv()
            bak = self.backup_path(src)
            if not os.path.exists(bak):
                return f"{what}: 백업 없음, 되돌리지 못함"
            write_text(src, read_text(bak))
            return f"{what}: 백업으로 되돌림" + ("(대조 일치)" if read_text(src) == read_text(bak) else "(대조 불일치!)")
        if what == "ledger":
            bad = []
            for m in reversed(self.prog["ledger"]):
                p = py_tool("LRM_LEDGER_PY", "ledger.py", "owner", m["id"], "--to", m["from"])
                if p.returncode != 0:
                    bad.append(m["id"])
            rows = {r["id"]: r for r in self.ledger_rows()}
            bad += [m["id"] for m in self.prog["ledger"] if rows.get(m["id"], {}).get("owner") != m["from"] and m["id"] not in bad]
            n = len(self.prog["ledger"])
            if not bad:
                self.prog["ledger"] = []
            return f"장부 담당 {n}건 되돌림" + (f", 실패 {','.join(bad)}" if bad else "(대조 일치)")
        if what == "close":
            import intake
            h, _ = intake.find_hub_terminal(self.run, self.hub(self.pl.child).path)
            if h:
                rc, _ = self.run(["terminal", "close", "--terminal", h], 30)
                return f"하위 허브 터미널 종료({h}, rc={rc}). 작업 공간 폴더는 남김"
            return "하위 허브 터미널 없음"
        return ""

    def compensate(self, step: str) -> list:
        undos, reset = UNDO[self.pl.kind].get(step, ([], []))
        out = [self.undo(w) for w in undos]
        self.prog["done"] = [d for d in self.prog["done"] if d not in reset]
        if "redeliver" in reset:
            self.prog["sent"] = {}  # 새 하위 허브 세션은 이전 전송을 모름
        self.save()
        return out

    def go(self) -> dict:
        done = self.prog["done"]
        for i, name in enumerate(self.steps(), 1):
            if name in done and name not in RERUN:
                self.log.append({"step": i, "name": name, "status": "skip", "detail": "이전 실행에서 끝남"})
                continue
            try:
                detail = getattr(self, name)()
            except (SplitError, OSError) as e:
                if not isinstance(e, SplitError):
                    e = SplitError(f"{name} 단계 파일 입출력 실패({e})", EXIT_IO)
                comp = self.compensate(name) if self.apply else []
                self.log.append({"step": i, "name": name, "status": "failed", "detail": str(e), "compensation": comp})
                raise SplitError(str(e) + (f" / 보상: {'; '.join(comp)}" if comp else ""), e.code)
            self.log.append({"step": i, "name": name, "status": "done" if self.apply else "plan", "detail": detail})
            if self.apply and name not in done:
                done.append(name)
                self.save()
        if self.apply:
            with locked(state_path()):
                st = load_state()
                for slug in (self.pl.parent, self.pl.child):
                    st.setdefault("events", {}).setdefault(slug, {})[f"{self.pl.kind}_at"] = int(time.time())
                st.get("streaks", {}).pop(self.pl.child if self.pl.kind == "merge" else self.pl.parent, None)
                save_state(st)
        return {"mode": "apply" if self.apply else "dry-run", "decision_id": self.did, "plan": asdict(self.pl), "steps": self.log}


def make_spec(a, pl: Plan) -> dict:
    """실행 입력 전체. 해시가 dry-run 기록·재실행 입력 대조의 기준"""
    role = a.role_file or f"~/.config/lrm-hubs/roles/role-{pl.child}.txt"
    spec = {"plan": asdict(pl), "moves": list(a.move), "files": list(a.file), "role_file": role,
            "hubs": [asdict(h) for h in read_hubs()]}
    spec["hash"] = digest({k: spec[k] for k in ("plan", "moves", "files", "role_file")})
    return spec


def cmd_run(a, run) -> int:
    d = run_dir(a.decision_id)
    run_json, dry_json = os.path.join(d, "run.json"), os.path.join(d, "dryrun.json")
    prop = load_state().get("proposals", {}).get(a.decision_id)
    if a.apply and os.path.exists(run_json):
        spec = json.loads(read_text(run_json))  # 불변 계획으로 재개. 표가 바뀌었어도 계획 검사는 다시 하지 않음
        given = digest({"plan": spec["plan"], "moves": list(a.move), "files": list(a.file), "role_file": a.role_file or spec["role_file"]})
        if given != spec["hash"]:
            raise SplitError(f"재실행 입력이 첫 실행과 다름(--move·--file·--role-file) — {run_json} 의 값으로 다시 실행", EXIT_REFUSED)
        if not prop or prop.get("hash") != digest(spec["plan"]):
            raise SplitError(f"제안 기록이 실행 계획과 다름: {a.decision_id}", EXIT_REFUSED)
        check_approved(a.decision_id, prop)
    elif a.apply:
        if not prop:
            raise SplitError(f"기록된 제안 없음: {a.decision_id} — propose --record 로 카드를 남기고 사용자 승인 뒤 실행", EXIT_REFUSED)
        pl = Plan(prop["kind"], prop["parent"], prop["child"], prop.get("child_name", ""), prop.get("prefix", ""))
        if prop.get("hash") != digest(asdict(pl)):
            raise SplitError(f"제안 계획 해시 불일치: {a.decision_id} — 제안 뒤 계획이 바뀜, 다시 propose", EXIT_REFUSED)
        check_plan(pl, read_hubs())
        check_approved(a.decision_id, prop)
        spec = make_spec(a, pl)
        dry = json.loads(read_text(dry_json)) if os.path.exists(dry_json) else {}
        if dry.get("hash") != spec["hash"]:
            raise SplitError(f"같은 입력의 시험 실행 기록 없음 — --apply 를 빼고 같은 인자로 먼저 실행해 단계 확인", EXIT_REFUSED)
        os.makedirs(d, exist_ok=True)
        write_text(run_json, json.dumps(spec, ensure_ascii=False, indent=2))
    else:
        if prop:
            pl = Plan(prop["kind"], prop["parent"], prop["child"], prop.get("child_name", ""), prop.get("prefix", ""))
        elif a.kind and a.child:
            pl = Plan(a.kind, a.hub, a.child, a.child_name, a.prefix)
        else:
            raise SplitError("시험 실행은 기록된 제안의 --decision-id 또는 --kind·--hub·--child(·--child-name·--prefix) 필요")
        check_plan(pl, read_hubs())
        spec = make_spec(a, pl)
    r = Runner(spec, a.decision_id, a.apply, run)
    if a.apply:
        for rid in a.mark_sent:
            r.prog["sent"][rid] = "sent"
        for rid in a.resend:
            r.prog["sent"].pop(rid, None)
        r.save()
    try:
        out = r.go()
    except SplitError as e:
        out = {"mode": "apply" if a.apply else "dry-run", "decision_id": a.decision_id, "plan": spec["plan"], "steps": r.log, "error": str(e)}
        print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else render_steps(out))
        raise
    if not a.apply and prop:
        os.makedirs(d, exist_ok=True)  # 첫 회 시험 근거. 이 기록과 같은 입력만 --apply 허용
        write_text(dry_json, json.dumps({"hash": spec["hash"], "at": int(time.time())}, ensure_ascii=False))
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else render_steps(out))
    return 0


def render_steps(out: dict) -> str:
    mark = {"plan": "·", "done": "✅", "skip": "↷", "failed": "❌"}
    lines = [f"[{'실행' if out['mode'] == 'apply' else '시험: 바꾸지 않음'}] {out['plan']['kind']} {out['plan']['parent']} ↔ {out['plan']['child']} ({out['decision_id']})"]
    lines += [f"{mark[s['status']]} {s['step']:>2}. {s['name']}: {s['detail']}" for s in out["steps"]]
    return "\n".join(lines)


def cmd_relink(a, run) -> int:
    """분할 후속: PR 닫힘·진행 작업자 없음이 확인된 작업 공간만 하위 허브 아래로. 판정 불가면 부모에 둠"""
    prop = load_state().get("proposals", {}).get(a.decision_id)
    if not prop or prop["kind"] != "split":
        raise SplitError(f"분할 제안 기록 없음: {a.decision_id} — 분할 실행 뒤에만 재연결", EXIT_REFUSED)
    hubs = {h.slug: h for h in read_hubs()}
    if prop["child"] not in hubs:
        raise SplitError(f"하위 허브 {prop['child']} 가 허브 목록 표에 없음 — 분할 실행이 끝났는지 확인", EXIT_REFUSED)
    par, child = hubs[prop["parent"]].path, hubs[prop["child"]].path
    kids = worktree_kids(run).get(par, [])
    rows = []
    for ws in a.ws:
        if ws not in kids:
            rows.append({"ws": ws, "action": "skip", "why": "부모 허브 아래 작업 공간 아님"})
            continue
        live = hub_live(run, ws)
        if live is not False:
            rows.append({"ws": ws, "action": "keep", "why": "살아 있는 터미널·워커 있음" if live else "터미널·워커 조회 실패 — 판정 불가라 부모에 둠"})
            continue
        br = subprocess.run(["git", "-C", ws, "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
        p = subprocess.run(["gh", "pr", "list", "--head", br, "--state", "open", "--json", "number"], cwd=ws, capture_output=True, text=True) if br else None
        if p is None or p.returncode != 0:
            rows.append({"ws": ws, "action": "keep", "why": "브랜치·PR 조회 실패 — 판정 불가라 부모에 둠"})
            continue
        if json.loads(p.stdout or "[]"):
            rows.append({"ws": ws, "action": "keep", "why": "열린 PR 있음 — 닫힌 뒤 다시 실행"})
            continue
        if a.apply:
            rc, _ = run(["worktree", "set", "--worktree", f"path:{ws}", "--parent-worktree", f"path:{child}", "--json"], 30)
            ok = rc == 0 and ws in worktree_kids(run).get(child, [])
            rows.append({"ws": ws, "action": "moved" if ok else "failed", "why": "" if ok else "부모 경로 대조 실패"})
        else:
            rows.append({"ws": ws, "action": "would_move", "why": "PR 없음·닫힘, 진행 작업자 없음"})
    print(json.dumps({"mode": "apply" if a.apply else "dry-run", "to": child, "items": rows}, ensure_ascii=False, indent=2))
    return EXIT_IO if any(r["action"] == "failed" for r in rows) else 0


# ── 명령줄 ──────────────────────────────────────────────────────────────────────────────
EPILOG = """예:
  hub_split.py measure                           # 읽기 전용 판정(정리기 시험 실행 포함)
  hub_split.py measure --topic ui-layer=6 --json # 메인의 주제 갈림 판단을 넣어 판정
  hub_split.py measure --record                  # 주기 점검: 연속 횟수 기록
  hub_split.py propose --kind split --hub ui-layer --child ui-popup --child-name 'UI 팝업' --prefix ui팝업 \\
      --decision-id D-SPLIT2 --reason '작업 공간 11개' --file docs/split.html --record
  hub_split.py run --decision-id D-SPLIT2 --move C0012 --move C0015    # 시험(기본): 단계만 출력
  hub_split.py run --decision-id D-SPLIT2 --move C0012 --apply          # 승인 뒤 실행
  hub_split.py relink --decision-id D-SPLIT2 --ws ~/orca/workspaces/X/LRM-1 --apply
종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자 오류, 3 환경 오류(orca 응답 없음 등), 4 안전 조건 미충족(진행 워커·제안 기록 없음 등)
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"오류: {message} — --help 참고\n")
        sys.exit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="hub_split.py", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
               description="허브 분할·병합 판정·제안·실행. 대화형 입력 없음.")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    m = sub.add_parser("measure", help="분할·병합 후보 판정(기본 읽기 전용)", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="허브별 하위 작업 공간 수(정리 후보 제외)·최근 접수 수·주제 갈림으로 분할·병합 후보를 냄. 허브·장부·경로는 바꾸지 않음.")
    m.add_argument("--topic", action="append", default=[], metavar="SLUG=N", help="메인 판단: 이 허브에서 떼어 낼 주제의 작업 수(반복)")
    m.add_argument("--gc-json", default="", metavar="FILE", help="ws_gc.sh --dry-run --json 결과 파일(없으면 직접 시험 실행)")
    m.add_argument("--no-gc", action="store_true", help="정리 후보를 빼지 않고 등록된 수 그대로 잼")
    m.add_argument("--record", action="store_true", help="연속 횟수를 기록 파일에 저장(30분 주기 점검용)")
    m.add_argument("--now", type=float, default=0, help=argparse.SUPPRESS)
    m.add_argument("--json", action="store_true", help="JSON 출력")
    m.set_defaults(fn=cmd_measure)
    pr = sub.add_parser("propose", help="분할·병합 결정 카드 기록(기본 형식 확인만)", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
                        description="사용자 승인용 결정 카드를 hub_report.py 로 outbox 에 남김. --record 없으면 형식만 확인. 기록한 제안만 run --apply 가 받음.")
    pr.add_argument("--kind", required=True, choices=("split", "merge"))
    pr.add_argument("--hub", default="", help="(split) 나눌 부모 허브 slug")
    pr.add_argument("--child", required=True, help="split: 새 하위 허브 slug / merge: 접을 하위 허브 slug")
    pr.add_argument("--child-name", default="", help="(split) 하위 허브 표시 이름")
    pr.add_argument("--prefix", default="", help="(split) 하위 허브로 보낼 새 접수 머리말")
    pr.add_argument("--decision-id", required=True, help="결정 번호 D-영문·숫자")
    pr.add_argument("--reason", action="append", default=[], help="판정 근거 한 줄(반복). measure 의 reasons")
    pr.add_argument("--file", action="append", default=[], help="근거 문서(반복)")
    pr.add_argument("--no-doc", action="store_true", help="근거 문서 없는 카드임을 명시")
    pr.add_argument("--record", action="store_true", help="outbox 에 실제 기록하고 제안을 기록 파일에 남김")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(fn=cmd_propose)
    r = sub.add_parser("run", help="분할·병합 실행(기본 --dry-run)", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="문서의 이관 순서대로 실행. 기본은 할 일만 출력. --apply 는 propose --record 로 남긴 결정 번호만 받음. "
                                   "--apply 조건: 결정 카드에 사용자가 '승인'을 고른 답(slack-inbox), 제안 뒤 계획 불변, 같은 입력의 시험 실행 기록. "
                                   "첫 --apply 가 run.json(불변 계획)을 남기고, 끝난 단계·전송·장부 이관을 progress.json 에 기록해 재실행 시 이어감. 실패 단계는 설계의 되돌리기를 실제로 수행.")
    r.add_argument("--decision-id", required=True, help="승인받은 결정 번호")
    r.add_argument("--move", action="append", default=[], metavar="ID", help="(split) 부모 허브가 보고한 미착수 작업 번호(반복)")
    r.add_argument("--file", action="append", default=[], help="전후 보고에 붙일 문서(반복)")
    r.add_argument("--role-file", default="", help="(split) 하위 허브 역할 파일(기본 ~/.config/lrm-hubs/roles/role-<child>.txt)")
    r.add_argument("--kind", choices=("split", "merge"), help="(시험, 제안 기록 없을 때) 종류")
    r.add_argument("--hub", default="", help="(시험) 부모 허브")
    r.add_argument("--child", default="", help="(시험) 하위 허브")
    r.add_argument("--child-name", default="")
    r.add_argument("--prefix", default="")
    r.add_argument("--mark-sent", action="append", default=[], metavar="ID", help="(재실행) 화면에서 도착을 확인한 작업 번호")
    r.add_argument("--resend", action="append", default=[], metavar="ID", help="(재실행) 화면에서 미도착을 확인해 다시 보낼 작업 번호")
    mode = r.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(기본) 바꾸지 않고 단계만 출력")
    mode.add_argument("--apply", action="store_true", help="실제 실행")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_run)
    rl = sub.add_parser("relink", help="분할 후속: PR 닫힌 작업 공간을 하위 허브 아래로", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    rl.add_argument("--decision-id", required=True)
    rl.add_argument("--ws", action="append", default=[], required=True, help="옮길 작업 공간 경로(반복)")
    rl.add_argument("--apply", action="store_true", help="실제 재연결(기본 시험)")
    rl.set_defaults(fn=cmd_relink)
    return p


def default_run():
    orca = mt.find_orca()
    return lambda args, timeout: mt.run_orca(orca, args, timeout)


def main(argv=None, run=None) -> int:
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:  # --help 는 0, 인자 오류는 2
        return int(e.code or 0)
    try:
        return a.fn(a, run or default_run())
    except SplitError as e:
        sys.stderr.write(f"오류: {e}\n")
        return e.code
    except OSError as e:
        sys.stderr.write(f"오류: 파일 입출력 실패({e}) — 경로·권한 확인\n")
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
