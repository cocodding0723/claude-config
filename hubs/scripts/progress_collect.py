#!/usr/bin/env python3
"""진행 수집기: 장부·허브 상태 파일·내 PR·결정 대기·워커 수를 모아 스냅샷 1개(progress.json)로 만듦.

스냅샷은 '진행 중 / 답 대기 / 막힘' 항목 목록. 옆 패널(panel_collect 가 90초마다 호출)과 Slack 알림(progress_digest.py)이 같은 파일을 읽음.
쓰는 쪽은 panel_collect 회차(또는 --once) 하나. 임시 파일 → os.replace 로 원자 교체. 읽기 전용 원료만 쓰며 Slack·토큰은 건드리지 않음.
원료별로 ok·err·at·tried·stale·value 를 둠(panel-auto.json 형식). 원료 하나가 실패하면 그 원료만 이전 값 + stale:true 로 두고 나머지는 그대로.
PR(gh)은 PR_INTERVAL_S 초에 한 번만 조회하고 그 사이는 직전 값을 씀.

환경: LRM_HUB_DIR 작업 폴더 · LRM_PROGRESS 스냅샷 경로 · LRM_LEDGER 장부 · LRM_HUB_STATUS_DIR 허브 상태 폴더
      LRM_DIGEST_GH gh 명령(없으면 PATH → /opt/homebrew/bin·/usr/local/bin) · LRM_DIGEST_REPOS PR 조회 저장소(쉼표, 없으면 PANEL_GH_REPOS 환경 변수 → 작업 폴더 panel.env → 현재 저장소) · LRM_HUB_STATUS_STALE_HOURS 낡음 기준(기본 24)
      LRM_ORCA orca 명령(없으면 PATH → 알려진 설치 위치) · LRM_PROGRESS_PR_INTERVAL PR 조회 간격 초(기본 300) · LRM_PROGRESS_PR_BUDGET PR 조회 전체 시간 상한 초(기본 30)
종료 코드: 0 성공(원료 일부 실패 포함), 2 설정·입력 오류
"""
import argparse
import hashlib
import json
import os
import re
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
NO_EVIDENCE_TAG = "근거 없음·오래됨"
UNMERGED_TAG = "PR 닫힘·미병합"
SEEN_KEEP_S = 7 * 86400  # 사라진 막힘의 최초 관측 기록 보관 기간
EVIDENCE_CLOSED = "closed"  # blocker_evidence.CLOSED·UNMERGED 와 같은 값(순환 가져오기 회피)
EVIDENCE_UNMERGED = "unmerged"
PR_PREFIX = "pr:"
LEDGER_OPEN = ("접수", "진행")
LEDGER_BLOCK = "보류"
FAIL_NOTE = {"ledger": "장부 수집 실패", "hubs": "허브 상태 수집 실패", "prs": "PR 조회 실패", "decisions": "결정 대기 수집 실패", "workspaces": "하위 작업 공간 수 수집 실패"}
SOURCES = ("ledger", "hubs", "prs", "decisions", "workers")
LIST_LIMIT = "1000"  # orca worktree·terminal list 기본 개수에서 잘림(panel_collect 와 같은 값)
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


def received_epoch(text, now=None):
    """장부 접수일('MM-DD') → 그날 0시 epoch. 해석 못 하면 0(정렬에서 맨 뒤). 미래 날짜면 작년으로 봄"""
    now = time.time() if now is None else now
    try:
        t = time.strptime(f"{time.localtime(now).tm_year}-{str(text).strip()}", "%Y-%m-%d")
    except ValueError:
        return 0.0
    at = time.mktime(t)
    return at if at <= now + 86400 else time.mktime(time.strptime(f"{t.tm_year - 1}-{t.tm_mon}-{t.tm_mday}", "%Y-%m-%d"))


def item(key, bucket, title, group, link="", ref=None, since=0.0):
    """ref: 원본 Slack 메시지 위치({kind: topic|card, channel, ts} 또는 {kind: file, file}). 링크는 토큰이 있는 digest 가 chat.getPermalink 로 만듦(수집기는 주소를 만들지 않음)"""
    return {"key": key, "bucket": bucket, "title": one_line(title), "group": group, "link": link, "ref": ref, "since": since}


class ReportRefs:
    """보고 전송기 상태(report-sender.state.json)에서 항목별 원본 메시지 위치를 찾음. 읽기 전용, 못 읽으면 빈 값(링크 없음)"""

    def __init__(self, path=""):
        self.topics, self.files = {}, {}
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(st, dict):
            return
        topics, reports = st.get("topics"), st.get("reports")
        for key, t in (topics.items() if isinstance(topics, dict) else ()):
            if isinstance(t, dict) and isinstance(t.get("ts"), str) and t["ts"] and isinstance(t.get("channel"), str) and t["channel"]:
                self.topics[key] = {"kind": "topic", "channel": t["channel"], "ts": t["ts"]}
        newest = {}  # 작업별로 첨부가 올라간 가장 최근 보고의 파일 ID
        for r in (reports.values() if isinstance(reports, dict) else ()):
            if not isinstance(r, dict) or not isinstance(r.get("task"), str) or not isinstance(r.get("att_done"), dict):
                continue
            sent = r.get("sent") if isinstance(r.get("sent"), (int, float)) else 0
            ids = [v for v in r["att_done"].values() if isinstance(v, str) and v not in ("", "uploaded")]
            if ids and sent >= newest.get(r["task"], -1):
                newest[r["task"]] = sent
                self.files[r["task"]] = {"kind": "file", "file": ids[0]}

    def for_task(self, task):
        """장부 작업 번호 → 첨부 메시지(있으면) 아니면 주제 스레드"""
        return self.files.get(task) or self.topics.get(task)

    def for_hub(self, slug):
        return self.topics.get(f"hub:{slug}")


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

def collect_ledger(path, refs=None):
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
        out.append(item(f"ledger:{r['id']}", bucket, f"{r['id']} {r['request']}", "장부", ref=refs.for_task(r["id"]) if refs else None, since=received_epoch(r["received"])))
    return out


def blocker_id(text):
    """막힘 문구 정규화 해시: 공백·대소문자 차이는 같은 막힘. 허브가 상태 파일을 다시 써도(updatedAt 갱신) 같은 문구면 같은 id"""
    return short_hash(" ".join(str(text).split()).casefold())


def collect_hubs(status_dir, old_items=(), refs=None, resolver=None, resolved=None, seen=None, now=None):
    """허브 상태 파일 전부 → (항목, 알림, 읽기 실패 허브). 낡은 허브는 제목에 '낡음'.
    막힘에 근거(refs)가 있고 resolver 가 '닫힘'으로 판정하면 막힘에서 빼고 resolved 목록에만 기록. 근거 없는 막힘이 낡았으면 제목에 '근거 없음·오래됨'(삭제는 안 함).
    '오래됨' 기준은 막힘별 최초 관측 시각(seen: "슬러그:문구해시" → {first, last}, 수집기 소유). 상태 파일 updatedAt 은 허브가 다시 쓸 때마다 갱신돼 근거가 못 됨.
    사라진 막힘 기록은 SEEN_KEEP_S 뒤 정리. 손상 파일은 그 허브의 이전 항목(old_items)을 그대로 두어 목록에서 완료 처리되지 않게 함"""
    out, notes, failed = [], [], []
    seen = {} if seen is None else seen
    now = time.time() if now is None else now
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
        ref = refs.for_hub(slug) if refs else None
        since = at.timestamp() if at else 0.0
        for task in data.get("tasks", []):
            out.append(item(f"hub:{slug}:t:{short_hash(task)}", WAIT if needs_user(task) else DOING, f"{tag}{slug}: {task}", slug, ref=ref, since=since))
        for b in data.get("blockers", []):
            blocker, brefs = hub_status.blocker_text(b), hub_status.blocker_refs(b)
            state = resolver(brefs) if resolver and brefs else None
            if state == EVIDENCE_CLOSED:
                if resolved is not None:
                    resolved.append({"hub": slug, "text": one_line(blocker), "refs": brefs})
                continue
            mark = seen.setdefault(f"{slug}:{blocker_id(blocker)}", {"first": now})
            mark["last"] = now
            btag = f"[{NO_EVIDENCE_TAG}] " if not brefs and now - mark["first"] > limit_s else tag
            if state == EVIDENCE_UNMERGED:
                btag += f"[{UNMERGED_TAG}] "
            it = item(f"hub:{slug}:b:{short_hash(blocker)}", WAIT if needs_user(blocker) else BLOCK, f"{btag}{slug}: {blocker}", slug, ref=ref, since=since)
            if state:
                it["evidence"] = state  # open·unknown(확인 불가 — 막힘 유지)
            out.append(it)
    for k in [k for k, v in seen.items() if not isinstance(v, dict) or now - v.get("last", v.get("first", 0)) > SEEN_KEEP_S]:
        del seen[k]
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


def hub_slug(value):
    """폴더 이름 또는 hubs.tsv slug → 전달용 slug(hub_dups.in_hub 가 찾는 /coord-<slug>-hub 의 <slug>).
    'coord-' 로 시작하면 폴더 이름으로 보고 앞 coord- 한 번·끝 -hub 한 번만 제거(coord-automa-hub-hub → automa-hub, coord-server-hub → server).
    아니면 이미 slug 라 그대로(automa-hub·ui-layer). 끝 -hub 를 반복 제거하면 automa-hub 가 automa 로 깨짐"""
    if not value.startswith("coord-"):
        return value
    return re.sub(r"-hub$", "", value[len("coord-"):]) or value


def workspace_counts(worktrees, terminals):
    """허브(폴더 이름 coord-*)별 하위 작업 공간 수 → {slug: {children, active}}.
    하위 = 허브 워크스페이스의 childWorktreeIds(보관된 것 제외), 활동 = 그 하위에 열린 터미널이 있음"""
    if not isinstance(worktrees, list) or not isinstance(terminals, list):
        raise SourceError("orca 목록 형식 오류 — worktree·terminal list 응답 확인")
    rows = {w["id"]: w for w in worktrees if isinstance(w, dict) and isinstance(w.get("id"), str)}
    used = {t.get("worktreeId") for t in terminals if isinstance(t, dict) and t.get("worktreeId")}
    out = {}
    for wid, w in rows.items():
        base = os.path.basename(str(w.get("path") or "").rstrip("/"))
        if not base.startswith("coord-"):
            continue
        kids = {c for c in w.get("childWorktreeIds") or [] if isinstance(c, str)}
        kids |= {i for i, x in rows.items() if x.get("parentWorktreeId") == wid}
        kids = {k for k in kids if not (rows.get(k) or {}).get("isArchived")}
        slot = out.setdefault(hub_slug(base), {"children": 0, "active": 0})
        slot["children"] += len(kids)
        slot["active"] += sum(1 for k in kids if k in used)
    return out


def orca_json(*args):
    """orca 읽기 명령의 result. 실패는 SourceError"""
    try:
        p = subprocess.run([orca_bin(), *args, "--json"], capture_output=True, text=True, timeout=ORCA_TIMEOUT_S, stdin=subprocess.DEVNULL)
        d = json.loads(p.stdout) if p.returncode == 0 else None
    except subprocess.TimeoutExpired:
        raise SourceError("orca 시간 초과")
    except (OSError, ValueError):
        raise SourceError("orca 실행 실패")
    if not isinstance(d, dict) or not d.get("ok") or not isinstance(d.get("result"), dict):
        raise SourceError("orca 응답 오류")
    return d["result"]


def orca_bin():
    """LRM_ORCA → PATH → 알려진 설치 위치 순(launchd 는 PATH 가 짧음). 탐색은 main_terminal.find_orca 한 곳"""
    import main_terminal  # 지연 가져오기: 쓰는 곳에서만 필요
    return main_terminal.find_orca()


def query_workspaces():
    return workspace_counts(orca_json("worktree", "list", "--limit", LIST_LIMIT).get("worktrees"),
                            orca_json("terminal", "list", "--limit", LIST_LIMIT).get("terminals"))


def query_pending(now):
    """결정 대기를 panel_collect 와 같은 규칙으로 직접 조회(panel_collect 를 거치지 않고 실행될 때)"""
    import panel_collect  # 지연 가져오기: 작업 폴더 경로가 환경 변수로 정해진 뒤
    try:
        return panel_collect.collect_pending(now)
    except panel_collect.SourceError as e:
        raise SourceError(f"결정 대기 읽기 실패({e.code})")


def ts_epoch(ts):
    """Slack ts('초.마이크로초') → epoch. 아니면 0"""
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def card_ref(p):
    """결정 카드의 원본 메시지 위치. 채널·ts 가 없으면 None(digest 가 '링크 없음'과 경고를 냄)"""
    ch, ts = p.get("channel"), p.get("ts")
    return {"kind": "card", "channel": ch, "ts": ts} if isinstance(ch, str) and ch and isinstance(ts, str) and ts else None


def decision_items(pending):
    """panel_collect 의 pending 값({count, items}) → 답 대기 항목"""
    out = []
    for p in (pending or {}).get("items", []):
        ident = p.get("id") or short_hash(str(p.get("title", "")))
        out.append(item(f"decision:{ident}", WAIT, f"결정 {p.get('title', '')}", "결정", ref=card_ref(p), since=ts_epoch(p.get("ts"))))
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


def default_repos():
    """PR 조회 저장소 기본값: 환경 변수 PANEL_GH_REPOS → 작업 폴더 panel.env 의 PANEL_GH_REPOS(옆 패널과 같은 설정). 파일은 읽기만 하고 실행하지 않음.
    launchd 는 저장소 밖에서 돌아 '현재 저장소' 폴백이 실패하므로 필요"""
    raw = os.environ.get("PANEL_GH_REPOS")
    if raw is None:
        raw = ""
        try:
            with open(os.path.join(HUB_DIR, "panel.env"), encoding="utf-8") as f:
                for line in f:
                    k, sep, v = line.strip().partition("=")
                    if sep and k == "PANEL_GH_REPOS":
                        raw = v.strip().strip('"').strip("'")
        except (OSError, UnicodeDecodeError) as e:  # 깨진 설정이 수집 전체를 멈추지 않게 파일만 무시
            if not isinstance(e, FileNotFoundError):
                print(f"경고: panel.env 읽기 실패({type(e).__name__}) — 무시하고 현재 저장소로 조회", file=sys.stderr)
            raw = ""
    return [r for r in re.split(r"[\s,]+", raw) if r]


def conf_from_env(read_panel=True):
    """read_panel=False 면 panel.env 를 읽지 않음(PR 조회를 안 하거나 저장소를 명시한 실행)"""
    env = os.environ.get
    return {
        "ledger": env("LRM_LEDGER") or os.path.join(HUB_DIR, "feedback-ledger.tsv"),
        "status_dir": os.path.expanduser(env("LRM_HUB_STATUS_DIR") or os.path.join(HUB_DIR, "hub-status")),  # 작업 폴더 밑이 기본(시험이 격리됨)
        "report_state": env("LRM_REPORT_STATE") or os.path.join(HUB_DIR, "report-sender.state.json"),
        "gh": gh_bin(),
        "repos": [r.strip() for r in (env("LRM_DIGEST_REPOS") or "").split(",") if r.strip()] or (default_repos() if read_panel else []),
    }


def build(conf, prev, now, pending=None, workers=None, use_pr=True, workspaces=None):
    """스냅샷 dict. prev 는 직전 스냅샷(없으면 {}). pending·workers 는 panel_collect 가 이미 모은 값, workspaces 는 {slug: {children, active}} 를 돌려주는 함수(없으면 orca 직접 조회).
    pending 이 없으면 결정 대기를 직접 조회함(결정 원료를 끄면 미해결 결정이 목록에서 완료 처리되므로 끄지 않음)"""
    old = prev.get("sources", {}) if isinstance(prev, dict) else {}
    hub_notes, hub_failed = [], []
    refs = ReportRefs(conf.get("report_state", ""))

    evidence_cache = dict(prev.get("evidence") or {}) if isinstance(prev, dict) and isinstance(prev.get("evidence"), dict) else {}
    blocker_seen = {k: dict(v) for k, v in (prev.get("blockerSeen") or {}).items() if isinstance(v, dict) and isinstance(v.get("first"), (int, float))} if isinstance(prev, dict) and isinstance(prev.get("blockerSeen"), dict) else {}
    resolved = (prev.get("resolved") or []) if isinstance(prev, dict) and isinstance(prev.get("resolved"), list) else []

    def hubs():
        import blocker_evidence  # 지연 가져오기: todo_facts 가 이 모듈을 가져옴
        old_value = (old.get("hubs") or {}).get("value") or []
        resolver = blocker_evidence.Resolver(conf, evidence_cache, now, use_pr)
        fresh_resolved = []
        found, hub_notes[:], hub_failed[:] = collect_hubs(conf["status_dir"], old_value, refs, resolver, fresh_resolved, blocker_seen, now)
        hub_notes.extend(resolver.notes())
        resolved[:] = fresh_resolved  # 이번 회차에 근거가 닫혀 빠진 막힘만(상태 파일에 남아 있는 동안 매 회차 다시 기록됨)
        return found

    src = {
        "ledger": run_source(lambda: collect_ledger(conf["ledger"], refs), old.get("ledger"), now),
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
    src["workspaces"] = run_source(workspaces or query_workspaces, old.get("workspaces"), now)
    groups = {slug: {**c, "warn": c["children"] > CHILD_WARN} for slug, c in (src["workspaces"].get("value") or {}).items()}
    items, seen = [], set()
    for name in SOURCES:
        for it in (src[name].get("value") or []) if name != "workers" else []:
            if it["key"] not in seen:
                seen.add(it["key"])
                items.append(it)
    notes = hub_notes + [f"{FAIL_NOTE[name]} — {s['err']}" + ("(이전 값 유지)" if s.get("value") is not None else "") for name, s in src.items() if not s["ok"] and name != "workers"]
    return {"version": VERSION, "updatedAt": now, "updatedAtText": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "counts": {b: sum(1 for i in items if i["bucket"] == b) for b, _, _ in BUCKETS},
            "groups": groups, "items": items, "notes": notes, "sources": src,
            "resolved": resolved, "evidence": evidence_cache, "blockerSeen": blocker_seen}


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


def refresh(pending=None, workers=None, write=True, use_pr=True, conf=None, workspaces=None):
    """직전 스냅샷을 읽어 새로 만들고(write 면) 원자 교체. 손상된 직전 파일은 무시하고 새로 씀. 반환: 스냅샷"""
    path = snapshot_path()
    try:
        prev = load_snapshot(path) or {}
    except SourceError:
        prev = {}
    snap = build(conf or conf_from_env(read_panel=use_pr), prev, int(time.time()), pending, workers, use_pr, workspaces)
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
    conf = conf_from_env(read_panel=not a.no_pr and not a.repo)
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
