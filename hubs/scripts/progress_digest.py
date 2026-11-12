#!/usr/bin/env python3
"""진행 목록: 진행 스냅샷(progress.json, progress_collect.py 가 90초마다 갱신)을 읽어 Slack 목록을 갱신하고 DM 한 줄을 보냄.

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
import progress_collect as pc  # noqa: E402

HUB_DIR = os.path.expanduser(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs")
DEFAULT_LIST_ID = "F0CF54PDVFT"
DEFAULT_LIST_URL = "https://w1709475506-et9433868.slack.com/lists/T06M7SZGSP9/F0CF54PDVFT"
DEFAULT_API = "https://slack.com/api"
# 목록 열 ID: 할 일(text)·완료(checkbox). 종류 열은 선택지 ID 가 필요해 쓰지 않음
COL_TITLE, COL_DONE, COL_LINK = "Col0CEX0ZCHMH", "Col0CF6UCE0KW", "Col0CFXJT428G"
API_TIMEOUT_S = 15
GH_TIMEOUT_S = 30
NAMES_MAX = 3
NAME_MAX = 30  # DM 줄에 넣는 항목 이름 길이
TITLE_MAX = 80

DOING, WAIT, BLOCK, BUCKETS, LABEL, ICON = pc.DOING, pc.WAIT, pc.BLOCK, pc.BUCKETS, pc.LABEL, pc.ICON
SNAP_MAX_AGE_S = 600  # 수집기가 이만큼 멈췄으면 스냅샷을 믿지 않고 직접 수집
PR_FAIL_NOTE = "PR 조회 실패"


class Fail(Exception):
    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


@dataclass
class Item:
    key: str
    bucket: str
    title: str
    link: str = ""

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
    """스냅샷 → (항목, 알림). 없거나 낡으면 직접 수집(파일 쓰기 없음)"""
    note = []
    try:
        snap = pc.load_snapshot(pc.snapshot_path())
    except pc.SourceError as e:
        snap, note = None, [str(e)]
    if snap is None or time.time() - snap.get("updatedAt", 0) > SNAP_MAX_AGE_S:
        note.append("스냅샷 없음·낡음 — 직접 수집")
        snap = pc.refresh(write=False, use_pr=use_pr)
    items = [Item(i["key"], i["bucket"], i["title"], i.get("link", "")) for i in snap["items"]]
    return items, note + list(snap.get("notes", []))


# ---------- 요약 ----------

def digest_hash(items):
    body = json.dumps(sorted([i.key, i.bucket, i.title, i.link] for i in items), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def names(items, limit=NAMES_MAX):
    shown = [one_line(i.title, NAME_MAX) for i in items[:limit]]
    extra = len(items) - limit
    return ", ".join(shown) + (f" 외 {extra}" if extra > 0 else "")


def dm_text(items, list_url):
    by = {b: [i for i in items if i.bucket == b] for b, _, _ in BUCKETS}
    head = " · ".join(f"{LABEL[b]} {len(by[b])}" for b, _, _ in BUCKETS)
    lines = [f"{head} — <{list_url}|목록 열기>"]
    for b in (WAIT, BLOCK):
        if by[b]:
            lines.append(f"{ICON[b]} {LABEL[b]}: {names(by[b])}")
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
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 환경 프록시로 우회 금지
    try:
        with opener.open(req, timeout=API_TIMEOUT_S) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Fail(f"{method} 호출 실패({one_line(str(e), 50)})", 1)
    if not data.get("ok"):
        raise Fail(f"{method} 오류({data.get('error', '알 수 없음')})", 1)
    return data


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
    items, notes = load_items(use_pr=not a.no_pr)
    digest = digest_hash(items)
    ops = plan_list(items, state)
    text = dm_text(items, conf.list_url)
    # 해시가 같아도 못 보낸 목록 작업이 남았으면 동기화(--no-list 로 건너뛴 행, 링크만 바뀐 행)
    changed = a.force or digest != state["hash"] or (bool(ops) and not a.no_list)
    result = {
        "counts": {b: sum(1 for i in items if i.bucket == b) for b, _, _ in BUCKETS},
        "items": [{"key": i.key, "bucket": i.bucket, "title": i.title} for i in items],
        "notes": notes, "changed": changed, "dm": text,
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
        description="장부·허브 상태·내 PR 을 '진행 중 / 답 대기 / 막힘' 으로 묶어 Slack 목록을 갱신하고 DM 한 줄을 보냄. 변화가 없으면 아무것도 안 보냄.",
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
