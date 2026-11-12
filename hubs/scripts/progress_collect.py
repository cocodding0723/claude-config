#!/usr/bin/env python3
"""진행 수집기: 장부·허브 상태 파일·내 PR·결정 대기·워커 수를 모아 스냅샷 1개(progress.json)로 만듦.

스냅샷은 '진행 중 / 답 대기 / 막힘' 항목 목록. 옆 패널(panel_collect 가 90초마다 호출)과 Slack 알림(progress_digest.py)이 같은 파일을 읽음.
쓰는 쪽은 panel_collect 회차(또는 --once) 하나. 임시 파일 → os.replace 로 원자 교체. 읽기 전용 원료만 쓰며 Slack·토큰은 건드리지 않음.
원료별로 ok·err·at·tried·stale·value 를 둠(panel-auto.json 형식). 원료 하나가 실패하면 그 원료만 이전 값 + stale:true 로 두고 나머지는 그대로.
PR(gh)은 PR_INTERVAL_S 초에 한 번만 조회하고 그 사이는 직전 값을 씀.

환경: LRM_HUB_DIR 작업 폴더 · LRM_PROGRESS 스냅샷 경로 · LRM_LEDGER 장부 · LRM_HUB_STATUS_DIR 허브 상태 폴더
      LRM_DIGEST_GH gh 명령(없으면 PATH → /opt/homebrew/bin·/usr/local/bin) · LRM_DIGEST_REPOS PR 조회 저장소(쉼표, 없으면 현재 저장소) · LRM_HUB_STATUS_STALE_HOURS 낡음 기준(기본 24)
      LRM_PROGRESS_PR_INTERVAL PR 조회 간격 초(기본 300) · LRM_PROGRESS_PR_BUDGET PR 조회 전체 시간 상한 초(기본 30)
종료 코드: 0 성공(원료 일부 실패 포함), 2 설정·입력 오류
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hub_status  # noqa: E402
import ledger  # noqa: E402

VERSION = 1
HUB_DIR = os.path.expanduser(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs")
GH_TIMEOUT_S = 30
PR_BUDGET_S = 30  # PR 조회 전체 시간 상한. panel_collect 회차(90초) 안에 끝나야 함
GH_CANDIDATES = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")  # launchd 최소 PATH 대비
ORCA_TIMEOUT_S = 15
CHILD_WARN = 10  # 허브 하위 작업 공간이 이 수를 넘으면 경고
PR_INTERVAL_S = 300  # LRM_PROGRESS_PR_INTERVAL 로 바꿈(시험용, 0 이면 매번 조회)
TITLE_MAX = 80

DOING, WAIT, BLOCK = "doing", "wait", "block"
BUCKETS = (
    (DOING, "진행 중", "🔄"),
    (WAIT, "답 대기", "⏳"),
    (BLOCK, "막힘", "🚧"),
)
LABEL = {b: name for b, name, _ in BUCKETS}
ICON = {b: icon for b, _, icon in BUCKETS}
# 사용자가 움직여야 풀리는 문구. 장부 진행 칸·허브 줄이 포함하면 '답 대기'. "승인 요청" 같은 앞으로의 일은 넓게 잡으면 오분류라 제외
WAIT_WORDS = ("병합 대기", "승인 대기", "답 대기", "결정 대기", "사용자 실행 대기", "사용자 답", "사용자 확인")
STALE_TAG = "낡음"
PR_PREFIX = "pr:"
LEDGER_OPEN = ("접수", "진행")
LEDGER_BLOCK = "보류"
FAIL_NOTE = {"ledger": "장부 수집 실패", "hubs": "허브 상태 수집 실패", "prs": "PR 조회 실패", "decisions": "결정 대기 수집 실패"}
SOURCES = ("ledger", "hubs", "prs", "decisions", "workers")
PR_FIELDS = "number,title,url,isDraft,reviewDecision,mergeable"


class SourceError(Exception):
    """원료 하나의 실패. 원인 — 조치 문장"""


def one_line(text, limit=TITLE_MAX):
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def short_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def needs_user(text):
    return any(w in text for w in WAIT_WORDS)


def item(key, bucket, title, group, link=""):
    return {"key": key, "bucket": bucket, "title": one_line(title), "group": group, "link": link}


def pr_interval():
    try:
        return float(os.environ.get("LRM_PROGRESS_PR_INTERVAL", PR_INTERVAL_S))
    except ValueError:
        return PR_INTERVAL_S


def pr_budget():
    try:
        return float(os.environ.get("LRM_PROGRESS_PR_BUDGET", PR_BUDGET_S))
    except ValueError:
        return PR_BUDGET_S


def snapshot_path():
    return os.path.expanduser(os.environ.get("LRM_PROGRESS") or os.path.join(HUB_DIR, "progress.json"))


# ---------- 원료 ----------

def collect_ledger(path):
    """장부 → 항목. 접수·진행은 진행 중, 보류는 막힘. 완료는 빠져 목록에서 완료 처리 대상이 됨"""
    try:
        rows = ledger.read_rows(path)
    except ledger.LedgerError as e:
        raise SourceError(f"장부 읽기 실패 — {e}")
    out = []
    for r in rows:
        if r["state"] in LEDGER_OPEN:
            bucket = WAIT if needs_user(r["note"]) else DOING
        elif r["state"] == LEDGER_BLOCK:
            bucket = BLOCK
        else:
            continue
        out.append(item(f"ledger:{r['id']}", bucket, f"{r['id']} {r['request']}", "장부"))
    return out


def collect_hubs(status_dir, old_items=()):
    """허브 상태 파일 전부 → (항목, 알림, 읽기 실패 허브). 낡은 허브는 제목에 '낡음'.
    손상 파일은 그 허브의 이전 항목(old_items)을 그대로 두어 목록에서 완료 처리되지 않게 함"""
    out, notes, failed = [], [], []
    try:
        names = sorted(n for n in os.listdir(status_dir) if n.endswith(".json"))
    except FileNotFoundError:
        return out, notes, failed
    limit_s = hub_status.stale_hours() * 3600
    for name in names:
        slug = name[: -len(".json")]
        try:
            with open(os.path.join(status_dir, name), encoding="utf-8") as f:
                data = json.load(f)
            bad = hub_status.schema_error(data) if isinstance(data, dict) else "객체 아님"
        except (OSError, ValueError) as e:
            bad = str(e)
        if bad:
            kept = [i for i in old_items if isinstance(i, dict) and i.get("group") == slug]
            out += kept
            failed.append(slug)
            notes.append(f"허브 상태 {slug} 읽기 실패({one_line(bad, 40)}) — 이전 항목 {len(kept)}개 유지")
            continue
        at = hub_status.parse_iso(data.get("updatedAt", ""))
        stale = at is None or (time.time() - at.timestamp()) > limit_s
        tag = f"[{STALE_TAG}] " if stale else ""
        for task in data.get("tasks", []):
            out.append(item(f"hub:{slug}:t:{short_hash(task)}", WAIT if needs_user(task) else DOING, f"{tag}{slug}: {task}", slug))
        for blocker in data.get("blockers", []):
            out.append(item(f"hub:{slug}:b:{short_hash(blocker)}", WAIT if needs_user(blocker) else BLOCK, f"{tag}{slug}: {blocker}", slug))
    return out, notes, failed


def gh_bin():
    """gh 실행 파일. LRM_DIGEST_GH → PATH → 알려진 설치 위치 순(launchd 는 PATH 가 짧음)"""
    return os.environ.get("LRM_DIGEST_GH") or shutil.which("gh") or next((c for c in GH_CANDIDATES if os.path.exists(c)), "gh")


def collect_prs(gh, repos):
    """내 열린 PR → 항목. 승인된 PR 은 병합 결정 대기, 변경 요청·충돌은 막힘. 실패는 SourceError"""
    out, deadline = [], time.monotonic() + pr_budget()
    for repo in repos or [None]:
        left = deadline - time.monotonic()
        if left <= 1:
            raise SourceError(f"PR 조회 시간 예산({pr_budget():g}초) 초과 — 저장소 수를 줄이거나 간격을 늘림")
        cmd = [gh, "pr", "list", "--author", "@me", "--state", "open", "--json", PR_FIELDS]
        if repo:
            cmd += ["--repo", repo]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=min(GH_TIMEOUT_S, left), stdin=subprocess.DEVNULL)
            if p.returncode != 0:
                raise SourceError(one_line(p.stderr or f"gh 종료 코드 {p.returncode}", 60))
            prs = json.loads(p.stdout or "[]")
        except subprocess.TimeoutExpired:
            raise SourceError("gh 시간 초과")
        except (OSError, ValueError) as e:
            raise SourceError(one_line(str(e), 60))
        for pr in prs:
            if pr.get("reviewDecision") == "CHANGES_REQUESTED" or pr.get("mergeable") == "CONFLICTING":
                bucket = BLOCK
            elif pr.get("reviewDecision") == "APPROVED":
                bucket = WAIT
            else:
                bucket = DOING
            scope = f"{repo.split('/')[-1]}#" if repo else "#"
            out.append(item(f"{PR_PREFIX}{repo or ''}#{pr['number']}", bucket, f"PR {scope}{pr['number']} {pr.get('title', '')}", "PR", pr.get("url", "")))
    return out


def query_pending(now):
    """결정 대기를 panel_collect 와 같은 규칙으로 직접 조회(panel_collect 를 거치지 않고 실행될 때)"""
    import panel_collect  # 지연 가져오기: 작업 폴더 경로가 환경 변수로 정해진 뒤
    try:
        return panel_collect.collect_pending(now)
    except panel_collect.SourceError as e:
        raise SourceError(f"결정 대기 읽기 실패({e.code})")


def decision_items(pending):
    """panel_collect 의 pending 값({count, items}) → 답 대기 항목"""
    out = []
    for p in (pending or {}).get("items", []):
        ident = p.get("id") or short_hash(str(p.get("title", "")))
        out.append(item(f"decision:{ident}", WAIT, f"결정 {p.get('title', '')}", "결정"))
    return out


# ---------- 조립 ----------

def run_source(fn, prev, now):
    try:
        return {"ok": True, "err": None, "at": now, "tried": now, "stale": False, "value": fn()}
    except SourceError as e:
        err = str(e)
    except Exception as e:  # noqa: BLE001 — 원료 하나의 예상 밖 오류는 그 원료만 실패로 둠
        err = f"internal:{type(e).__name__}"
    old = prev if isinstance(prev, dict) else {}
    return {"ok": False, "err": err, "at": old.get("at"), "tried": now, "stale": True, "value": old.get("value")}


def conf_from_env():
    env = os.environ.get
    return {
        "ledger": env("LRM_LEDGER") or os.path.join(HUB_DIR, "feedback-ledger.tsv"),
        "status_dir": os.path.expanduser(env("LRM_HUB_STATUS_DIR") or os.path.join(HUB_DIR, "hub-status")),  # 작업 폴더 밑이 기본(시험이 격리됨)
        "gh": gh_bin(),
        "repos": [r.strip() for r in (env("LRM_DIGEST_REPOS") or "").split(",") if r.strip()],
    }


def build(conf, prev, now, pending=None, workers=None, use_pr=True):
    """스냅샷 dict. prev 는 직전 스냅샷(없으면 {}). pending·workers 는 panel_collect 가 이미 모은 값.
    pending 이 없으면 결정 대기를 직접 조회함(결정 원료를 끄면 미해결 결정이 목록에서 완료 처리되므로 끄지 않음)"""
    old = prev.get("sources", {}) if isinstance(prev, dict) else {}
    hub_notes, hub_failed = [], []

    def hubs():
        old_value = (old.get("hubs") or {}).get("value") or []
        found, hub_notes[:], hub_failed[:] = collect_hubs(conf["status_dir"], old_value)
        return found

    src = {
        "ledger": run_source(lambda: collect_ledger(conf["ledger"]), old.get("ledger"), now),
        "hubs": run_source(hubs, old.get("hubs"), now),
    }
    if hub_failed and src["hubs"]["ok"]:  # 일부 허브만 실패: 나머지는 새 값, 실패한 허브는 이전 항목 + 표시
        src["hubs"].update(ok=False, err="허브 상태 읽기 실패: " + ", ".join(hub_failed), stale=True)
    prs_old = old.get("prs")
    fresh = isinstance(prs_old, dict) and prs_old.get("tried") is not None and now - prs_old["tried"] < pr_interval() and prs_old.get("ok")
    if not use_pr:
        src["prs"] = {"ok": True, "err": None, "at": now, "tried": now, "stale": False, "value": None, "off": True}
    elif fresh:
        src["prs"] = prs_old  # 간격 안: gh 호출 없이 직전 결과
    else:
        src["prs"] = run_source(lambda: collect_prs(conf["gh"], conf["repos"]), prs_old, now)
    src["decisions"] = run_source(lambda: decision_items(pending if pending is not None else query_pending(now)), old.get("decisions"), now)
    src["workers"] = {"ok": workers is not None, "err": None if workers is not None else "n/a", "at": now if workers is not None else None,
                      "tried": now, "stale": workers is None, "value": workers, "off": workers is None}
    items, seen = [], set()
    for name in SOURCES:
        for it in (src[name].get("value") or []) if name != "workers" else []:
            if it["key"] not in seen:
                seen.add(it["key"])
                items.append(it)
    notes = hub_notes + [f"{FAIL_NOTE[name]} — {s['err']}" + ("(이전 값 유지)" if s.get("value") is not None else "") for name, s in src.items() if not s["ok"] and name != "workers"]
    return {"version": VERSION, "updatedAt": now, "updatedAtText": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "counts": {b: sum(1 for i in items if i["bucket"] == b) for b, _, _ in BUCKETS},
            "items": items, "notes": notes, "sources": src}


def valid_snapshot(d):
    """읽는 쪽이 의존하는 모양만 확인: items 는 {key,bucket,title} 문자열을 가진 객체 목록, sources 는 객체의 객체"""
    if not isinstance(d, dict) or not isinstance(d.get("items"), list) or not isinstance(d.get("sources", {}), dict):
        return False
    if not all(isinstance(v, dict) for v in d.get("sources", {}).values()):
        return False
    known = {b for b, _, _ in BUCKETS}
    ok = lambda i: isinstance(i, dict) and isinstance(i.get("key"), str) and i.get("bucket") in known and isinstance(i.get("title"), str)  # noqa: E731
    if not all(ok(i) for i in d["items"]):
        return False
    # 이전 값을 재사용하는 원료의 value 도 같은 모양이어야 함(손상 값이 항목 합치기에서 터지지 않게)
    return all(v.get("value") is None or (isinstance(v["value"], list) and all(ok(i) for i in v["value"]))
               for name, v in d.get("sources", {}).items() if name in ("ledger", "hubs", "prs", "decisions"))


def load_snapshot(path):
    """스냅샷 → dict. 없으면 None. 손상은 SourceError(다음 쓰기가 복구)"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise SourceError(f"스냅샷 읽기 실패({one_line(str(e), 40)}) — {path}")
    if not valid_snapshot(d):
        raise SourceError(f"스냅샷 형식 오류 — {path}")
    return d


def write_snapshot(path, snap):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".progress-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def refresh(pending=None, workers=None, write=True, use_pr=True, conf=None):
    """직전 스냅샷을 읽어 새로 만들고(write 면) 원자 교체. 손상된 직전 파일은 무시하고 새로 씀. 반환: 스냅샷"""
    path = snapshot_path()
    try:
        prev = load_snapshot(path) or {}
    except SourceError:
        prev = {}
    snap = build(conf or conf_from_env(), prev, int(time.time()), pending, workers, use_pr)
    if write:
        write_snapshot(path, snap)
    return snap


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="progress_collect.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="장부·허브 상태·내 PR 을 '진행 중 / 답 대기 / 막힘' 스냅샷(progress.json)으로 모음.",
        epilog="환경:" + __doc__.split("환경:", 1)[1] + "\n예:\n  progress_collect.py --dry-run   파일 쓰기 없이 JSON 출력\n  progress_collect.py --once      스냅샷 원자 교체\n")
    p.add_argument("--once", action="store_true", help="1회 수집해 스냅샷 파일을 원자 교체(기본 동작)")
    p.add_argument("--dry-run", action="store_true", help="수집해 표준출력으로만 냄. 파일 쓰기 없음")
    p.add_argument("--no-pr", action="store_true", help="PR 조회 생략")
    p.add_argument("--repo", action="append", default=[], help="PR 조회 저장소 owner/name (여러 번 가능)")
    a = p.parse_args(argv)
    conf = conf_from_env()
    conf["repos"] = a.repo or conf["repos"]
    try:
        snap = refresh(write=not a.dry_run, use_pr=not a.no_pr, conf=conf)
    except OSError as e:
        print(f"오류: 스냅샷 쓰기 실패({e.strerror}) — 작업 폴더 권한 확인", file=sys.stderr)
        return 2
    if a.dry_run:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    else:
        print(f"스냅샷 {snapshot_path()} — " + " · ".join(f"{LABEL[b]} {snap['counts'][b]}" for b, _, _ in BUCKETS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
