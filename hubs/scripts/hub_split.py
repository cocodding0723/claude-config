#!/usr/bin/env python3
"""허브 분할·병합 도구. 측정·판정(measure)은 읽기 전용, 제안·실행은 사용자 승인 결정 번호가 있어야 함.

설계 원본: hubs/docs/hub-split.md. 분할 신호는 작업 공간 수와 주제 갈림 두 가지뿐(워커 상한은 신호 아님).
주제 갈림은 스크립트가 판단하지 못하므로 메인이 --topic <slug>=<떼어 낼 작업 수> 로 넘김.
허브 목록 표(hubs.tsv) 여섯째 칸이 부모 허브 slug. 하위 허브는 한 층만 허용.

환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs) · LRM_HUBS_TSV 허브 목록 표 · LRM_INTAKE_ROUTES 접수 경로 표
      LRM_HUB_SPLIT_STATE 연속 판정·냉각 기록 · LRM_WS_ROOT 허브 기본 위치 · LRM_WS_GC 작업 공간 정리기 · LRM_ORCA orca 경로
종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자 오류, 3 환경 오류(orca 응답 없음 등 판정 불가)
"""
import argparse
import contextlib
import fcntl
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


# ── 명령줄 ──────────────────────────────────────────────────────────────────────────────
EPILOG = """예:
  hub_split.py measure                           # 읽기 전용 판정(정리기 시험 실행 포함)
  hub_split.py measure --topic ui-layer=6 --json # 메인의 주제 갈림 판단을 넣어 판정
  hub_split.py measure --record                  # 주기 점검: 연속 횟수 기록
종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자 오류, 3 환경 오류(orca 응답 없음 등)
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
