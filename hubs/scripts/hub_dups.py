#!/usr/bin/env python3
"""허브 코디네이터 터미널의 생존 판정·중복 목록·옛 세션 정리. restart_hub.sh·resume_all.sh 가 함께 씀.

코디네이터 = 허브 폴더(coord-<slug>-hub)의 claude 터미널 중 orchestration 워커(active·retained Dispatch)가 아닌 것.
살아 있음 = 연결됨·쓰기 가능·고아 아님·마지막 출력이 --max-idle-min 이내.
정리(clean)는 유지할 새 핸들이 같은 조건으로 살아 있을 때만 하고, 닫기 전에 각 터미널 마지막 화면을 보관함. 강제 종료는 쓰지 않음.

CLI: hub_dups.py alive (--slug S|--path P) [--terms 파일]   '살아 있음 수 전체 수 유지후보' 출력. 종료 코드 0 살아 있음, 1 없음, 2 판정 실패
     hub_dups.py list [--slug S|--path P] [--terms 파일] [--json]   코디네이터가 2개 이상인 허브 표
     hub_dups.py ready --handle H [--timeout 초]              새 세션 첫 출력 대기. 0 준비됨, 1 시간 초과, 2 조회 실패
     hub_dups.py clean (--slug S|--path P) --keep H [--also H]... [--dry-run]   종료 코드 0 완료, 1 일부 실패, 2 조회·인자 오류
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import main_terminal as mt  # noqa: E402

HUB_RE = re.compile(r"/coord-([a-z0-9-]+)-hub$")
HANDLE_RE = re.compile(r"term_[A-Za-z0-9_-]+")
IDLE_MIN_DEFAULT = 240  # 사람 결정을 기다리며 조용한 허브가 죽은 것으로 오판되지 않게 넉넉히
TAIL_LINES = 30
INBOX_DEFAULT = "~/.config/lrm-hubs/hub-inbox"
CALL_TIMEOUT = 30
WORKER_STATES = ("active", "retained")
WORKER_PAGES = 5


class Unavailable(Exception):
    """Orca 조회 실패·불완전. 닫을 대상을 확정할 수 없으니 무동작 사유"""


def orca(args, timeout=CALL_TIMEOUT):
    """(returncode, 파싱한 result|None). 응답에 result 가 없으면 None"""
    rc, d = mt.run_orca(mt.find_orca(), args, timeout)
    res = d.get("result") if isinstance(d, dict) else None
    return rc, (res if isinstance(res, dict) else None)


def list_terminals():
    rc, res = orca(["terminal", "list", "--limit", "1000", "--json"])
    if rc != 0 or res is None or not isinstance(res.get("terminals"), list):
        raise Unavailable("orca terminal list 조회 실패")
    return [t for t in res["terminals"] if isinstance(t, dict) and isinstance(t.get("handle"), str)]


def load_terminals(path):
    if path:
        try:
            ts = json.load(open(path))["result"]["terminals"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise Unavailable(f"터미널 목록 파일 해석 실패({type(e).__name__})")
        return [t for t in ts if isinstance(t, dict)]
    return list_terminals()


def worker_handles():
    """살아 있는 Dispatch 터미널 핸들 집합. 쪽 상한에 닿고도 더 남으면 불완전로 실패"""
    out = set()
    for state in WORKER_STATES:
        cursor = None
        for _ in range(WORKER_PAGES):
            rc, res = orca(["orchestration", "worker-list", "--json", "--full", "--terminal-state", state, "--limit", "100"] + (["--cursor", cursor] if cursor else []))
            if rc != 0 or res is None or not isinstance(res.get("workers"), list):
                raise Unavailable(f"worker-list({state}) 조회 실패")
            out |= {h for w in res["workers"] if isinstance(w, dict) for h in (w.get("agentTerminalHandle"), w.get("terminal")) if h}
            cursor = res.get("nextCursor")
            if not cursor:
                break
        else:
            raise Unavailable(f"worker-list({state}) 조회 불완전(쪽 상한 도달)")
    return out


def in_hub(t, slug=None, path=None):
    p = str(t.get("worktreePath") or "").rstrip("/")
    if path is not None:
        return p == path.rstrip("/")
    return p.endswith(f"/coord-{slug}-hub")


def is_coord(t, workers):
    return t.get("agentIdentity") == "claude" and t.get("handle") not in workers


def is_alive(t, now_ms, max_idle_min):
    last = t.get("lastOutputAt")
    return (t.get("connected") is True and t.get("writable") is True and t.get("orphaned") is False
            and isinstance(last, (int, float)) and now_ms - last <= max_idle_min * 60000)


def coords(terms, workers, **where):
    return [t for t in terms if in_hub(t, **where) and is_coord(t, workers)]


def newest(ts):
    return max(ts, key=lambda t: t.get("lastOutputAt") or 0, default=None)


def fmt_time(ms):
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ms / 1000)) if isinstance(ms, (int, float)) else "-"


def one_line(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def idle_arg(a):
    return float(a.max_idle_min if a.max_idle_min is not None else os.environ.get("HUB_ALIVE_MIN", IDLE_MIN_DEFAULT))


def where_of(a):
    return {"path": a.path} if a.path else {"slug": a.slug}


# ---- 하위 명령 -----------------------------------------------------------------------------
def cmd_alive(a):
    """'살아 있음 수 전체 수 유지후보' 한 줄 출력. 워커 조회가 실패하면 워커를 빼지 않고 판정(보수적: 중복 기동보다 건너뜀)"""
    try:
        terms = load_terminals(a.terms)
    except Unavailable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        workers = worker_handles()
    except Unavailable:
        workers = set()
    now = time.time() * 1000
    cs = coords(terms, workers, **where_of(a))
    live = [t for t in cs if is_alive(t, now, idle_arg(a))]
    print(len(live), len(cs), (newest(live) or {}).get("handle", "-"))  # 살아 있음 수, 코디네이터 전체 수, 유지 후보 핸들
    return 0 if live else 1


def hub_groups(terms, workers):
    """{허브 경로: 코디네이터 터미널 목록}. 허브 폴더 이름 규칙(coord-<slug>-hub)에 맞는 경로만"""
    g = {}
    for t in terms:
        p = str(t.get("worktreePath") or "").rstrip("/")
        if HUB_RE.search(p) and is_coord(t, workers):
            g.setdefault(p, []).append(t)
    return g


def cmd_list(a):
    try:
        terms = load_terminals(a.terms)
        workers = worker_handles()
    except Unavailable as e:
        print(f"ERROR: {e} — Orca 실행 상태 확인 후 재시도", file=sys.stderr)
        return 2
    now = time.time() * 1000
    rows = []
    for path, ts in sorted(hub_groups(terms, workers).items()):
        if len(ts) < 2 or (a.slug and not in_hub(ts[0], slug=a.slug)) or (a.path and path != a.path.rstrip("/")):
            continue
        keep = newest([t for t in ts if is_alive(t, now, idle_arg(a))]) or newest(ts)
        for t in sorted(ts, key=lambda x: x.get("lastOutputAt") or 0, reverse=True):
            rows.append({"hub": HUB_RE.search(path).group(1), "path": path, "handle": t["handle"], "title": one_line(t.get("title")),
                         "lastOutputAt": t.get("lastOutputAt"), "alive": is_alive(t, now, idle_arg(a)), "keep": t is keep})
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("중복 코디네이터 없음")
        return 0
    print("허브\t핸들\t제목\t마지막 출력\t상태")
    for r in rows:
        print(f"{r['hub']}\t{r['handle']}\t{r['title']}\t{fmt_time(r['lastOutputAt'])}\t{'유지 후보' if r['keep'] else ('살아 있음' if r['alive'] else '죽음·고아')}")
    return 0


def cmd_ready(a):
    """새 세션이 연결돼 첫 출력을 냈는지 폴링. 출력 시각이 비어 있으면 아직 안 뜬 것"""
    deadline = time.time() + a.timeout
    while True:
        try:
            t = next((x for x in list_terminals() if x["handle"] == a.handle), None)
        except Unavailable as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if t and t.get("connected") is True and t.get("lastOutputAt"):
            return 0
        if time.time() >= deadline:
            return 1
        time.sleep(a.interval)


def archive(t, inbox_dir, slug_dir):
    """옛 터미널 마지막 화면 몇 줄을 inbox 에 보관. 성공하면 파일 경로, 실패하면 None"""
    rc, res = orca(["terminal", "read", "--terminal", t["handle"], "--screen", "--limit", str(TAIL_LINES), "--json"])
    term = (res or {}).get("terminal") if res else None
    tail = term.get("tail") if isinstance(term, dict) else None
    if rc != 0 or not isinstance(tail, list):
        return None
    d = os.path.join(os.path.expanduser(inbox_dir), slug_dir)
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, f"{time.strftime('%Y%m%d-%H%M%S')}-{t['handle']}.txt")
    with open(f, "w") as fh:
        fh.write(f"# 정리 전 마지막 화면\nhandle: {t['handle']}\ntitle: {one_line(t.get('title'))}\nlastOutputAt: {fmt_time(t.get('lastOutputAt'))}\n\n")
        fh.write("\n".join(str(x) for x in tail[-TAIL_LINES:]) + "\n")
    return f


def cmd_clean(a):
    try:
        terms = load_terminals(None)
        workers = worker_handles()
    except Unavailable as e:
        print(f"ERROR: {e} — 정리하지 않음(옛 세션 유지)", file=sys.stderr)
        return 2
    now = time.time() * 1000
    by = {t["handle"]: t for t in terms}
    keep = by.get(a.keep)
    if not (keep and keep.get("agentIdentity") == "claude" and is_alive(keep, now, idle_arg(a))):
        print(f"ERROR: 유지할 세션 {a.keep} 이 살아 있지 않음 — 정리하지 않음(옛 세션 유지)", file=sys.stderr)
        return 2
    targets = coords(terms, workers, **where_of(a))
    for h in a.also:
        t = by.get(h)
        if t and is_coord(t, workers) and t not in targets:
            targets.append(t)
    targets = [t for t in targets if t["handle"] != a.keep]
    keep_path = str(keep.get("worktreePath") or "").rstrip("/")
    slug_dir = HUB_RE.search(keep_path).group(1) if HUB_RE.search(keep_path) else os.path.basename(keep_path) or "unknown"
    inbox = a.inbox or os.environ.get("LRM_HUB_INBOX") or INBOX_DEFAULT
    if not targets:
        print("정리할 옛 코디네이터 없음")
        return 0
    failed = 0
    for t in targets:
        label = f"{t['handle']} · {one_line(t.get('title')) or '제목 없음'} · 마지막 출력 {fmt_time(t.get('lastOutputAt'))}"
        if a.dry_run:
            print(f"[dry-run] 닫을 대상: {label}")
            continue
        saved = archive(t, inbox, slug_dir)
        if not saved:
            failed += 1
            print(f"ERROR: 마지막 화면 보관 실패, 닫지 않음: {label}", file=sys.stderr)
            continue
        rc, _ = orca(["terminal", "close", "--terminal", t["handle"], "--json"])
        if rc == 0:
            print(f"닫음: {label} → 보관 {saved}")
        else:
            failed += 1
            print(f"ERROR: 닫기 실패(rc={rc}): {label}", file=sys.stderr)
    return 1 if failed else 0


def parser():
    p = argparse.ArgumentParser(prog="hub_dups.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(s, hub=False):
        g = s.add_mutually_exclusive_group(required=hub)
        g.add_argument("--slug", help="허브 slug(폴더 coord-<slug>-hub)")
        g.add_argument("--path", help="허브 폴더 절대 경로")
        s.add_argument("--max-idle-min", type=float, help=f"살아 있음 기준 마지막 출력 경과 분(기본 {IDLE_MIN_DEFAULT}, 환경 HUB_ALIVE_MIN)")

    s = sub.add_parser("alive", help="허브에 살아 있는 코디네이터가 있는지")
    common(s, hub=True)
    s.add_argument("--terms", help="orca terminal list --json 저장 파일(없으면 직접 조회)")
    s.set_defaults(fn=cmd_alive)
    s = sub.add_parser("list", help="코디네이터 2개 이상인 허브 목록")
    common(s)
    s.add_argument("--terms")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("ready", help="새 세션 첫 출력 대기")
    s.add_argument("--handle", required=True)
    s.add_argument("--timeout", type=float, default=60)
    s.add_argument("--interval", type=float, default=2)
    s.set_defaults(fn=cmd_ready)
    s = sub.add_parser("clean", help="유지할 세션 외 같은 허브 코디네이터를 보관 후 닫기")
    common(s, hub=True)
    s.add_argument("--keep", required=True, help="유지할 새 세션 핸들")
    s.add_argument("--also", action="append", default=[], help="허브 폴더 밖이어도 함께 닫을 옛 핸들(워커면 제외)")
    s.add_argument("--inbox", help=f"보관 폴더(기본 {INBOX_DEFAULT}, 환경 LRM_HUB_INBOX)")
    s.add_argument("--dry-run", action="store_true", help="닫지 않고 대상만 출력")
    s.set_defaults(fn=cmd_clean)
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    if a.cmd == "clean" and not HANDLE_RE.fullmatch(a.keep):
        print("ERROR: --keep 형식 오류(term_...)", file=sys.stderr)
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
