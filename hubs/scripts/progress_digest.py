#!/usr/bin/env python3
"""진행 목록: 진행 스냅샷(progress.json, progress_collect.py 가 90초마다 갱신)을 읽어 Slack 목록을 갱신하고 DM 을 보냄.

스냅샷이 없거나 10분 넘게 낡으면(패널 수집기 멈춤) 그 자리에서 수집해 씀(파일은 쓰지 않음). 장부(feedback-ledger.tsv)가 정본. 이 도구가 만든 행만 상태 파일에 기억해 고치고 완료 처리하며, 손으로 넣은 행은 건드리지 않음.
직전 전송 내용의 해시가 같으면 아무것도 보내지 않음. 목록 갱신·DM 중 하나라도 실패하면 해시를 저장하지 않아 다음 회차에 재시도함
(목록 갱신은 상태 파일 기준 차이만 보내 반복해도 안전, DM 은 성공한 내용의 해시를 따로 저장해 중복 전송 안 함).
PR 조회가 실패하면 'PR 조회 실패' 한 줄만 남기고 계속함. 수집기가 직전 PR 값을 유지하므로 PR 행을 완료 처리하지 않음.

환경: LRM_HUB_DIR 작업 폴더 · LRM_PROGRESS 스냅샷 경로(수집 환경 변수는 progress_collect.py 참고) · LRM_DIGEST_STATE 상태 파일
      LRM_DIGEST_NOTIFY DM 전송 스크립트
      LRM_DIGEST_LIST_ID·LRM_DIGEST_LIST_URL Slack 목록 · SLACK_BOT_TOKEN 목록 갱신용(환경 변수로만 받음)
      SLACK_API_BASE API 주소(기본 https://slack.com/api) · LRM_DIGEST_LOCAL_ONLY=1 이면 로컬 주소 외에는 요청 전에 실패(시험용)
종료 코드: 0 성공(변화 없음·PR 조회 실패 포함), 1 목록 갱신 또는 DM 실패(재시도 대상), 2 설정·입력 오류
"""
import argparse
import hashlib
import ipaddress
import json
import os
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hub_report  # noqa: E402
import progress_collect as pc  # noqa: E402
import tls_ctx  # noqa: E402

HUB_DIR = os.path.expanduser(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs")
DEFAULT_LIST_ID = "F0CH72MJKBN"
DEFAULT_LIST_URL = "https://w1709475506-et9433868.slack.com/lists/T06M7SZGSP9/F0CH72MJKBN"
DEFAULT_API = "https://slack.com/api"
# 목록 열 ID: 할 일(text)·완료(checkbox). 종류 열은 선택지 ID 가 필요해 쓰지 않음
COL_TITLE, COL_DONE, COL_LINK = "Col0CHXNEMP1N", "Col0CH58810LR", "Col0CGN0B5WT1"
API_TIMEOUT_S = 15
LIST_FATAL = ("list_not_found", "not_in_channel", "missing_scope", "invalid_auth", "token_revoked")  # 목록 접근 자체가 막힌 오류
GH_TIMEOUT_S = 30
NAMES_MAX = 3
WS_NAMES_MAX = 5
NAME_MAX = 30  # DM 줄에 넣는 항목 이름 길이
TITLE_MAX = 80

DOING, WAIT, BLOCK, BUCKETS, LABEL, ICON = pc.DOING, pc.WAIT, pc.BLOCK, pc.BUCKETS, pc.LABEL, pc.ICON
SNAP_MAX_AGE_S = 600  # 수집기가 이만큼 멈췄으면 스냅샷을 믿지 않고 직접 수집
PR_FAIL_NOTE = "PR 조회 실패"


class Fail(Exception):
    def __init__(self, msg, code=2, api_error=""):
        super().__init__(msg)
        self.code = code
        self.api_error = api_error  # Slack 응답의 error 값(예 list_not_found)


@dataclass
class Item:
    key: str
    bucket: str
    title: str
    link: str = ""
    ref: dict = None  # 원본 메시지 위치. 링크 해석 전 값
    quote: str = ""  # 원본 첫 줄(DM 에 보이는 항목만 조회)

    def wants_link(self):
        """원본 메시지가 있어야 하는 항목(장부·허브·결정). PR 은 제목에 번호가 있어 제외"""
        return self.key.startswith(("ledger:", "hub:", "decision:"))

    def line(self):
        return f"{ICON[self.bucket]} {LABEL[self.bucket]} · {self.title}"


@dataclass
class Conf:
    state: str = ""
    notify: str = ""
    list_id: str = ""
    list_url: str = ""
    api: str = ""
    token: str = ""
    local_only: bool = False

    def __post_init__(self):
        env = os.environ.get
        self.state = self.state or env("LRM_DIGEST_STATE") or os.path.join(pc.HUB_DIR, "progress-digest.state.json")
        self.notify = self.notify or env("LRM_DIGEST_NOTIFY") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "slack_notify.sh")
        self.list_id = self.list_id or env("LRM_DIGEST_LIST_ID") or DEFAULT_LIST_ID
        self.list_url = self.list_url or env("LRM_DIGEST_LIST_URL") or DEFAULT_LIST_URL
        self.api = (self.api or env("SLACK_API_BASE") or DEFAULT_API).rstrip("/")
        self.token = self.token or env("SLACK_BOT_TOKEN") or ""
        self.local_only = self.local_only or env("LRM_DIGEST_LOCAL_ONLY") == "1"


one_line = pc.one_line


def load_items(use_pr):
    """스냅샷 → (항목, 알림, 허브별 하위 작업 공간 수). 없거나 낡으면 직접 수집(파일 쓰기 없음)"""
    note = []
    try:
        snap = pc.load_snapshot(pc.snapshot_path())
    except pc.SourceError as e:
        snap, note = None, [str(e)]
    if snap is None or time.time() - snap.get("updatedAt", 0) > SNAP_MAX_AGE_S:
        note.append("스냅샷 없음·낡음 — 직접 수집")
        snap = pc.refresh(write=False, use_pr=use_pr)
    items = [Item(i["key"], i["bucket"], i["title"], i.get("link", ""), i.get("ref") if isinstance(i.get("ref"), dict) else None) for i in snap["items"]]
    groups = snap.get("groups") if isinstance(snap.get("groups"), dict) else {}
    return items, note + list(snap.get("notes", [])), groups


# ---------- 요약 ----------

def digest_hash(items, groups=None):
    """전송 여부 판단용 해시. 하위 작업 공간은 경고 대상(10개 초과) 허브만 넣어 수가 오르내려도 DM 이 가지 않게 함"""
    warn = sorted(k for k, g in (groups or {}).items() if isinstance(g, dict) and g.get("warn"))
    body = json.dumps([sorted([i.key, i.bucket, i.title, i.link, i.quote] for i in items), warn], ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


QUOTE_MAX = 40


def clean_quote(text):
    """원본 첫 줄 → DM 인용. 링크 문법·서식 문자 제거, 길이 제한. 비밀·로컬 경로가 있으면 빈 값"""
    t = " ".join(str(text or "").split("\n", 1)[0].split())
    t = "".join(c for c in t if c not in "<>|*`\"")
    if hub_report.has_secret(t) or hub_report.local_paths(t):
        return ""
    return one_line(t, QUOTE_MAX)


def card_name(i):
    """'이름 · "원본 첫 줄" <링크|원본>'. 원본이 필요한 항목에 링크가 없으면 '링크 없음'. 링크 문법을 깨는 문자(<>|)는 이름에서 뺌"""
    out = one_line(i.title, NAME_MAX).translate(str.maketrans("", "", "<>|"))
    if i.quote:
        out += f' · "{i.quote}"'
    if not i.wants_link():
        return out
    return f"{out} <{i.link}|원본>" if i.link else f"{out} · 링크 없음"


def name_lines(items, limit=NAMES_MAX):
    """항목마다 '- 이름' 한 줄. limit 초과분은 '- 외 n건' 한 줄"""
    out = [f"- {card_name(i)}" for i in items[:limit]]
    extra = len(items) - limit
    return out + ([f"- 외 {extra}건"] if extra > 0 else [])


def ws_lines(groups):
    """허브별 '- 이름 하위 n개(활동 m)' 한 줄씩, 10개 넘으면 ⚠️. 하위가 없는 허브는 생략"""
    rows = [(k, g) for k, g in sorted(groups.items()) if isinstance(g, dict) and g.get("children")]
    out = [f"- {k} 하위 {g['children']}개(활동 {g.get('active', 0)})" + (" ⚠️" if g.get("warn") else "") for k, g in rows[:WS_NAMES_MAX]]
    extra = len(rows) - WS_NAMES_MAX
    return out + ([f"- 외 {extra}곳"] if extra > 0 else [])


def dm_text(items, list_url, groups=None):
    """첫 줄은 제목(양식 A), 개수·항목은 한 줄에 하나씩 '- ' 로 이음"""
    by = {b: [i for i in items if i.bucket == b] for b, _, _ in BUCKETS}
    lines = ["진행 목록", ""] + [f"- {LABEL[b]} {len(by[b])}" for b, _, _ in BUCKETS] + [f"<{list_url}|목록 열기>"]
    for b in (WAIT, BLOCK):
        if by[b]:
            lines += ["", f"{ICON[b]} {LABEL[b]}"] + name_lines(by[b])
    ws = ws_lines(groups or {})
    if ws:
        lines += ["", "🗂️ 허브 하위 작업 공간"] + ws
    return "\n".join(lines)


def render(items, notes):
    out = []
    for b, name, icon in BUCKETS:
        group = [i for i in items if i.bucket == b]
        out.append(f"{icon} {name} {len(group)}")
        out += [f"  - {i.title}" for i in group]
    out += [f"※ {n}" for n in notes]
    return "\n".join(out)


# ---------- 상태 파일 ----------

def sync_list_id(state, list_id):
    """상태의 행 매핑은 기록한 목록의 행 ID 라 목록이 바뀌면 쓸 수 없음. 목록 ID 가 다르거나(옛 상태는 기록 없음) 행이 있는데 ID 가 없으면 행 매핑·해시를 비우고 새로 시작. 비웠으면 True"""
    reset = bool(state["items"]) and state.get("list_id") != list_id
    if reset:
        state["items"], state["hash"] = {}, ""
    state["list_id"] = list_id
    return reset


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return {"hash": "", "dm_hash": "", "items": {}}
    except (OSError, ValueError) as e:
        raise Fail(f"상태 파일 읽기 실패({e}) — {path} 확인. 지우면 처음부터 다시 만들며 목록에 같은 행이 한 번 더 생길 수 있음")
    if not isinstance(st, dict) or not isinstance(st.get("items", {}), dict):
        raise Fail(f"상태 파일 형식 오류 — {path} 확인")
    st.setdefault("hash", "")
    st.setdefault("dm_hash", "")
    st.setdefault("items", {})
    return st


def save_state(path, st):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".digest-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------- Slack ----------

def check_api_base(conf):
    """시험 중 실제 Slack 으로 요청이 새지 않게 로컬 주소만 허용"""
    if not conf.local_only:
        return
    host = urllib.parse.urlparse(conf.api).hostname or ""
    try:
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise Fail(f"LRM_DIGEST_LOCAL_ONLY=1 인데 API 주소가 로컬이 아님({conf.api}) — SLACK_API_BASE 를 127.0.0.1 로 지정")


def rich(text):
    return [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}]}]


def api(conf, method, body):
    check_api_base(conf)
    if not conf.token:
        raise Fail("SLACK_BOT_TOKEN 없음 — slack-bot.env 를 읽은 환경에서 실행하거나 --no-list 사용", 2)
    req = urllib.request.Request(
        f"{conf.api}/{method}", data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + conf.token, "Content-Type": "application/json; charset=utf-8"})
    opener = urllib.request.build_opener(  # 환경 프록시로 우회 금지
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls_ctx.ssl_context()))
    try:
        with opener.open(req, timeout=API_TIMEOUT_S) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Fail(f"{method} 호출 실패({one_line(str(e), 50)})", 1)
    if not data.get("ok"):
        raise Fail(f"{method} 오류({data.get('error', '알 수 없음')})", 1, data.get("error", ""))
    return data


def api_get(conf, method, params):
    """읽기 전용 Slack 조회(GET). 실패는 Fail(코드 1)"""
    check_api_base(conf)
    if not conf.token:
        raise Fail(f"{method} 조회 불가(SLACK_BOT_TOKEN 없음)", 1)
    req = urllib.request.Request(f"{conf.api}/{method}?" + urllib.parse.urlencode(params), headers={"Authorization": "Bearer " + conf.token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls_ctx.ssl_context()))
    try:
        with opener.open(req, timeout=API_TIMEOUT_S) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Fail(f"{method} 호출 실패({one_line(str(e), 50)})", 1)
    if not data.get("ok"):
        raise Fail(f"{method} 오류({data.get('error', '알 수 없음')})", 1, data.get("error", ""))
    return data


class Links:
    """항목의 원본 메시지 → chat.getPermalink 정식 주소·첫 줄 인용. 회차마다 새로 만들어 같은 메시지는 한 번만 조회(실패도 캐시).
    손수 만든 주소로 대체하지 않음: 조회 실패면 링크 없음으로 두고 경고"""

    def __init__(self, conf, warn=None):
        self.conf, self.warn = conf, warn or (lambda m: print(m, file=sys.stderr))
        self.where, self.perma, self.text = {}, {}, {}

    def locate(self, ref):
        """ref → (channel, ts, thread_ts) 또는 None. 파일 ref 는 files.info 의 공유 위치"""
        key = ref.get("file") or (ref.get("channel"), ref.get("ts"))
        if key not in self.where:
            self.where[key] = None
            try:
                if ref.get("kind") == "file":
                    f = api_get(self.conf, "files.info", {"file": ref["file"]}).get("file") or {}
                    shares = {**(f.get("shares") or {}).get("public", {}), **(f.get("shares") or {}).get("private", {})}
                    ch, rows = next(iter(shares.items()), (None, None))
                    row = (rows or [{}])[0]
                    if ch and row.get("ts"):
                        self.where[key] = (ch, row["ts"], row.get("thread_ts", ""))
                elif ref.get("channel") and ref.get("ts"):
                    self.where[key] = (ref["channel"], ref["ts"], "")
            except Fail as e:
                self.warn(f"원본 위치 조회 실패({e})")
        return self.where[key]

    def link(self, ref):
        loc = self.locate(ref) if ref else None
        if not loc:
            return ""
        if loc[:2] not in self.perma:
            try:
                self.perma[loc[:2]] = api_get(self.conf, "chat.getPermalink", {"channel": loc[0], "message_ts": loc[1]}).get("permalink", "")
            except Fail as e:
                self.perma[loc[:2]] = ""
                self.warn(f"링크 조회 실패({e}) — 링크 없음으로 표시")
        return self.perma[loc[:2]]

    def quote(self, ref):
        loc = self.locate(ref) if ref else None
        if not loc:
            return ""
        if loc[:2] not in self.text:
            params = {"channel": loc[0], "oldest": loc[1], "latest": loc[1], "inclusive": "true", "limit": 1}
            try:
                if loc[2]:
                    msgs = api_get(self.conf, "conversations.replies", {**params, "ts": loc[2]}).get("messages")
                    msg = next((m for m in msgs or [] if m.get("ts") == loc[1]), {})
                else:
                    msg = (api_get(self.conf, "conversations.history", params).get("messages") or [{}])[0]
                self.text[loc[:2]] = clean_quote(msg.get("text"))
            except Fail:
                self.text[loc[:2]] = ""  # 인용만 생략, 링크는 유지
        return self.text[loc[:2]]


def resolve_links(items, links, dm_items=()):
    """모든 항목의 link 를 정식 주소로 채우고, DM 에 보이는 항목(dm_items)에만 인용을 채움.
    결정 대기에 카드 위치가 없으면 '링크 없음'과 함께 경고"""
    for i in items:
        if not i.wants_link():
            continue
        if i.ref is None and i.key.startswith("decision:"):
            links.warn(f"결정 카드 위치(ts) 없음 — {i.key} 는 링크 없음으로 표시")
        i.link = links.link(i.ref)
        i.quote = ""
    for i in dm_items:
        if i.link:
            i.quote = links.quote(i.ref)


def dm_visible(items, limit=NAMES_MAX):
    """DM 에 이름이 나가는 항목(답 대기·막힘 각 limit 개)"""
    return [i for b in (WAIT, BLOCK) for i in [x for x in items if x.bucket == b][:limit]]


def plan_list(items, state):
    """상태 파일 기준 차이 → [('create'|'update'|'done', item, record_id)]. 손으로 넣은 행은 상태에 없어 대상 아님"""
    ops, current = [], {i.key: i for i in items}
    for it in items:
        old = state["items"].get(it.key)
        if old is None:
            ops.append(("create", it, ""))
        elif old["title"] != it.title or old["bucket"] != it.bucket or old.get("link", "") != it.link:
            ops.append(("update", it, old["record"]))
    for key, old in state["items"].items():
        if key not in current:
            ops.append(("done", Item(key, old["bucket"], old["title"]), old["record"]))
    return ops


def apply_list(conf, ops, state):
    """차이를 보내고 성공한 것만 상태에 반영. 실패하면 나머지는 다음 회차로 넘김. 반환: 오류 목록"""
    errors = []
    for op, it, rec in ops:
        try:
            if op == "create":
                fields = [{"column_id": COL_TITLE, "rich_text": rich(it.line())}]
                if it.link:
                    fields.append({"column_id": COL_LINK, "rich_text": rich(it.link)})
                d = api(conf, "slackLists.items.create", {"list_id": conf.list_id, "initial_fields": fields})
                rid = (d.get("item") or {}).get("id") or d.get("id")
                if not rid:
                    raise Fail("slackLists.items.create 응답에 행 ID 없음", 1)
                state["items"][it.key] = {"record": rid, "title": it.title, "bucket": it.bucket, "link": it.link}
            elif op == "update":
                cells = [{"row_id": rec, "column_id": COL_TITLE, "rich_text": rich(it.line())}]
                if it.link:
                    cells.append({"row_id": rec, "column_id": COL_LINK, "rich_text": rich(it.link)})
                api(conf, "slackLists.items.update", {"list_id": conf.list_id, "cells": cells})
                state["items"][it.key].update(title=it.title, bucket=it.bucket, link=it.link)
            else:
                api(conf, "slackLists.items.update", {"list_id": conf.list_id, "cells": [{"row_id": rec, "column_id": COL_DONE, "checkbox": True}]})
                del state["items"][it.key]
        except Fail as e:
            if e.code == 2:
                raise  # 설정 오류는 나머지도 같으니 중단
            if e.api_error in LIST_FATAL:  # 목록 자체를 못 쓰는 오류는 행마다 반복하지 않고 이번 회차 목록 갱신을 건너뜀(DM 은 계속)
                errors.append(f"목록 갱신 건너뜀({e}) — 봇이 목록 {conf.list_id} 에 접근할 수 있게 해야 함")
                break
            errors.append(f"목록 {op} {it.key}: {e}")
    return errors


def send_dm(conf, text):
    if not os.path.exists(conf.notify):
        raise Fail(f"DM 전송 스크립트 없음({conf.notify}) — LRM_DIGEST_NOTIFY 확인", 2)
    cmd = [conf.notify, "--kind", "progress", "--title", "진행 목록", text]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(f"DM 전송 실패({one_line(str(e), 50)})", 1)
    if p.returncode != 0:
        raise Fail(f"DM 전송 실패(종료 코드 {p.returncode}: {one_line(p.stderr, 60)})", 1)


# ---------- 실행 ----------

def run(conf, a):
    state = load_state(conf.state)
    if sync_list_id(state, conf.list_id):
        print(f"목록이 바뀌어 행 매핑을 새로 시작함(새 목록 {conf.list_id})", file=sys.stderr)
    items, notes, groups = load_items(use_pr=not a.no_pr)
    resolve_links(items, Links(conf), dm_visible(items))
    digest = digest_hash(items, groups)
    ops = plan_list(items, state)
    text = dm_text(items, conf.list_url, groups)
    # 해시가 같아도 못 보낸 목록 작업이 남았으면 동기화(--no-list 로 건너뛴 행, 링크만 바뀐 행)
    changed = a.force or digest != state["hash"] or (bool(ops) and not a.no_list)
    result = {
        "counts": {b: sum(1 for i in items if i.bucket == b) for b, _, _ in BUCKETS},
        "items": [{"key": i.key, "bucket": i.bucket, "title": i.title} for i in items],
        "notes": notes, "groups": groups, "changed": changed, "dm": text,
        "list_ops": [{"op": op, "key": it.key, "title": it.line()} for op, it, _ in ops],
    }
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif not (a.quiet and not changed):
        print(render(items, notes))
        print("---")
        print("변화 없음 — 전송 안 함" if not changed else f"DM: {text}")
    if a.dry_run:
        if not a.json:
            print(f"(--dry-run) 목록 변경 {len(ops)}건: " + ", ".join(f"{op} {it.key}" for op, it, _ in ops) if ops else "(--dry-run) 목록 변경 없음")
        return 0
    if not changed:
        return 0

    errors = []
    if not a.no_list:
        try:
            errors += apply_list(conf, ops, state)
        finally:
            save_state(conf.state, state)  # 성공한 행은 실패가 있어도 기억해 중복 생성 방지
    if not a.no_dm and state["dm_hash"] != digest:
        try:
            send_dm(conf, text)
            state["dm_hash"] = digest
        except Fail as e:
            errors.append(str(e))
            if e.code == 2:
                save_state(conf.state, state)
                raise
    if not errors and not a.no_list and not a.no_dm:  # 일부 단계를 건너뛴 실행은 완료로 기록하지 않음
        state["hash"] = digest
    save_state(conf.state, state)
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


def parser():
    p = argparse.ArgumentParser(
        prog="progress_digest.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="장부·허브 상태·내 PR 을 '진행 중 / 답 대기 / 막힘' 으로 묶어 Slack 목록을 갱신하고 DM 을 보냄. 변화가 없으면 아무것도 안 보냄.",
        epilog="환경:" + __doc__.split("환경:", 1)[1] + "\n예:\n  progress_digest.py --dry-run          전송 없이 결과만 출력\n  progress_digest.py --force            변화 없어도 다시 전송\n  progress_digest.py --no-pr --json     PR 조회 없이 JSON 출력\n")
    p.add_argument("--dry-run", action="store_true", help="수집·분류·계획만 출력. 전송·상태 파일 쓰기 없음")
    p.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    p.add_argument("--force", action="store_true", help="직전 전송과 같아도 다시 보냄")
    p.add_argument("--quiet", action="store_true", help="변화 없으면 출력 없음(launchd 로그용)")
    p.add_argument("--no-pr", action="store_true", help="PR 조회 생략")
    p.add_argument("--no-list", action="store_true", help="Slack 목록 갱신 생략")
    p.add_argument("--no-dm", action="store_true", help="DM 생략")
    p.add_argument("--repo", action="append", default=[], help="PR 조회 저장소 owner/name (여러 번 가능)")
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    if a.repo:
        os.environ["LRM_DIGEST_REPOS"] = ",".join(a.repo)  # 직접 수집할 때 수집기가 읽음
    try:
        return run(Conf(), a)
    except Fail as e:
        print(f"오류: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
