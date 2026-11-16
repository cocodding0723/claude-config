#!/usr/bin/env python3
"""작업 장부(JSONL) 도구: 사용자 지시 한 건 = 한 줄. 상태·전제조건·완료 기준·증거를 한 파일에서 추적함.

행 칸: id · text(원문) · received_at · hub(담당 허브 slug) · state · needs(전제조건 id 목록) · done_when · evidence(목록)
      · source({kind, ref, context}) · updated_at · history(상태 변경 기록, 최근 20건).
상태: 미분류 → 배정 → 진행 ↔ 막힘 → 완료. 완료는 증거 없이 못 바꿈. needs 는 장부에 있는 id 만, 순환 금지.
같은 source(kind+ref)는 한 번만 반입(멱등). 쓰기는 ledger.py 의 flock 잠금·원자적 교체를 그대로 씀.
피드백 장부(feedback-ledger.tsv)·ledger_sync·intake 는 건드리지 않음(Slack 진행 스레드가 읽음).

환경: LRM_TASKS 장부 경로(기본 ~/.config/lrm-hubs/tasks.jsonl) · LRM_HUB_DIR 작업 폴더 · LRM_INBOX · LRM_LEDGER 피드백 장부 · LRM_HUBS_TABLE hubs.tsv
종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(증거 없는 완료·없는 id·순환 포함), 3 대상 없음·중복
"""
import argparse
import hashlib
import html.parser
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 링크·복사 설치에서도 같은 폴더의 ledger 를 찾음
import ledger  # noqa: E402
from ledger import LedgerError, EXIT_IO, EXIT_MISSING, EXIT_USAGE  # noqa: E402

HUB_DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
DEFAULT_PATH = os.path.join(HUB_DIR, "tasks.jsonl")
STATES = ("미분류", "배정", "진행", "막힘", "완료")
UNSORTED, ASSIGNED, DOING, BLOCKED, DONE = STATES
EMOJI = {UNSORTED: "📥", ASSIGNED: "📌", DOING: "🔄", BLOCKED: "⛔", DONE: "✅"}
SRC_KINDS = ("inbox", "feedback", "todo-html", "manual")
RESERVED_HUBS = ("사용자", "main")  # hubs.tsv 에 없는 담당. 사용자 직접 일·메인 코디네이터
ID_PREFIX, ID_WIDTH = "T", 4
HISTORY_MAX = 20
STAMP = "%Y-%m-%d %H:%M"
# 피드백 장부 상태 → 작업 장부 상태
FEEDBACK_STATE = {"접수": UNSORTED, "진행": DOING, "완료": DONE, "보류": BLOCKED}
# 할일 html 묶음(div class) → (상태, 담당). 담당 빈 값은 허브 미확인
TODO_GROUPS = {"me": (ASSIGNED, "사용자"), "hub": (DOING, ""), "q": (BLOCKED, ""), "done": (DONE, "")}
PR_NUM = re.compile(r"#(\d+)")


def tasks_path() -> str:
    return os.environ.get("LRM_TASKS") or DEFAULT_PATH


def stamp(now: float) -> str:
    return time.strftime(STAMP, time.localtime(now))


def split_ids(value) -> list:
    """'T1,T2' 또는 목록 → 중복 없는 id 목록"""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = []
    for it in items:
        it = ledger.clean(it)
        if it and it not in out:
            out.append(it)
    return out


# ---------- 파일 입출력 ----------

def read_rows(path: str) -> list:
    """JSONL → 행 목록. 파일이 없으면 빈 목록. 깨진 줄은 데이터를 잃지 않도록 오류로 멈춤"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as err:
        raise LedgerError(f"작업 장부 읽기 실패({err.strerror}) — LRM_TASKS 경로·권한 확인", EXIT_IO)
    rows = []
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            raise LedgerError(f"작업 장부 {n}번째 줄 JSON 오류 — 해당 줄을 고치거나 지우고 재시도", EXIT_IO)
        if not isinstance(row, dict) or not row.get("id"):
            raise LedgerError(f"작업 장부 {n}번째 줄에 id 없음 — 해당 줄 수정", EXIT_IO)
        rows.append(row)
    return rows


def render(rows: list) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)


def write_rows(path: str, rows: list) -> None:
    check_rows(rows)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except OSError as err:
        raise LedgerError(f"작업 장부 폴더 생성 실패({err.strerror}) — LRM_TASKS 경로 확인", EXIT_IO)
    ledger.write_atomic(path, render(rows))


# ---------- 검사 ----------

def find_cycle(rows: list):
    """needs 그래프의 순환 하나를 id 경로로. 없으면 None"""
    needs = {r["id"]: split_ids(r.get("needs")) for r in rows}
    color, stack = {}, []

    def visit(i):
        color[i] = 1
        stack.append(i)
        for j in needs.get(i, ()):
            if color.get(j) == 1:
                return stack[stack.index(j):] + [j]
            if j in needs and color.get(j) is None:
                found = visit(j)
                if found:
                    return found
        stack.pop()
        color[i] = 2
        return None

    for i in needs:
        if color.get(i) is None:
            found = visit(i)
            if found:
                return found
    return None


def check_rows(rows: list) -> None:
    """쓰기 직전 불변식: id 유일·상태 값·증거 없는 완료 금지·needs 는 있는 id·순환 금지"""
    ids, bad = set(), []
    for r in rows:
        rid = r["id"]
        why = ("id 중복" if rid in ids else f"알 수 없는 상태 '{r.get('state')}'" if r.get("state") not in STATES else
               "증거 없는 완료" if r["state"] == DONE and not [e for e in r.get("evidence", []) if str(e).strip()] else "")
        ids.add(rid)
        if why:
            ref = r.get("source", {}).get("ref")
            bad.append(f"{rid}{f'({ref})' if ref else ''}: {why}")
    for r in rows:
        for n in split_ids(r.get("needs")):
            if n not in ids:
                bad.append(f"{r['id']}: needs 의 {n} 가 장부에 없음")
            elif n == r["id"]:
                bad.append(f"{r['id']}: needs 에 자기 자신")
    if bad:
        raise LedgerError("작업 장부 검사 실패 — " + " · ".join(bad) + ". 완료는 --evidence 필요, needs 는 list 로 확인한 id 만")
    cyc = find_cycle(rows)
    if cyc:
        raise LedgerError("needs 순환 — " + " → ".join(cyc) + ". 순환 고리 중 한 줄의 --needs 를 풀기")


def load_slugs() -> dict:
    """hubs.tsv → {slug: 표시 이름}. 못 읽으면 빈 표(허브 이름 검사 생략)"""
    import ci_review  # 지연 가져오기: 허브 표가 필요한 명령에서만
    return ci_review.load_hub_names(os.environ.get("LRM_HUBS_TABLE") or os.path.join(HUB_DIR, "hubs.tsv"))


def norm_hub(value, names: dict) -> str:
    """허브 값 → hubs.tsv slug(progress_collect.hub_slug 규칙). 표에 없으면 오류. 표를 못 읽었으면 그대로"""
    value = ledger.clean(value or "")
    if not value or value in RESERVED_HUBS or not names:
        return value
    import progress_collect as pc  # 지연 가져오기
    slug = pc.hub_slug(value, set(names))
    if slug not in names:
        raise LedgerError(f"허브 '{value}' 가 hubs.tsv 에 없음 — slug 확인({', '.join(sorted(names)[:6])}…) 또는 사용자·main", EXIT_USAGE)
    return slug


def hub_by_label(label: str, names: dict) -> str:
    """피드백 장부 담당 칸(slug 또는 표시 이름, 공백 차이 허용) → slug. 못 맞추면 빈 값"""
    label = ledger.clean(label)
    if label in RESERVED_HUBS or label in names:
        return label
    flat = label.replace(" ", "")
    return next((s for s, n in names.items() if n.replace(" ", "") == flat), "")


# ---------- 행 만들기·바꾸기 ----------

def next_id(rows: list) -> str:
    top = max((int(m.group(1)) for r in rows if (m := re.fullmatch(ID_PREFIX + r"(\d+)", r["id"]))), default=0)
    return f"{ID_PREFIX}{top + 1:0{ID_WIDTH}d}"


def push_history(row: dict, now: float, state: str, note: str = "") -> None:
    entry = {"at": stamp(now), "state": state}
    if note:
        entry["note"] = ledger.clean(note)[:80]
    row["history"] = (row.get("history", []) + [entry])[-HISTORY_MAX:]


def new_row(rows: list, text: str, now: float, **kw) -> dict:
    """새 행. 값 검사는 write_rows 의 check_rows 가 함"""
    text = ledger.clean(text)
    if not text:
        raise LedgerError("text 는 비울 수 없음 — 원문을 지정", EXIT_USAGE)
    kind = kw.get("source_kind") or "manual"
    if kind not in SRC_KINDS:
        raise LedgerError(f"source 종류 오류: {kind} — {'|'.join(SRC_KINDS)} 중 하나", EXIT_USAGE)
    src = {"kind": kind, "ref": ledger.clean(kw.get("source_ref") or "")}
    if kw.get("context"):
        src["context"] = ledger.clean(kw["context"])
    row = {"id": next_id(rows), "text": text, "received_at": kw.get("received_at") or stamp(now), "hub": kw.get("hub", ""),
           "state": kw.get("state") or UNSORTED, "needs": split_ids(kw.get("needs")), "done_when": ledger.clean(kw.get("done_when") or ""),
           "evidence": split_ids_keep(kw.get("evidence")), "source": src, "updated_at": stamp(now), "history": []}
    push_history(row, now, row["state"], kw.get("note", ""))
    return row


def split_ids_keep(value) -> list:
    """증거 목록: 쉼표를 나누지 않음(PR 제목·경로에 쉼표가 들 수 있음). 중복 제거"""
    items = [] if value is None else ([value] if isinstance(value, str) else list(value))
    out = []
    for it in items:
        it = ledger.clean(it)
        if it and it not in out:
            out.append(it)
    return out


def has_source(rows: list, kind: str, ref: str) -> bool:
    return bool(ref) and any(r.get("source", {}).get("kind") == kind and r.get("source", {}).get("ref") == ref for r in rows)


def add_row(path: str, text: str, now: float, dry: bool, hub="", **kw) -> dict:
    names = load_slugs() if hub else {}
    with ledger.locked(path):
        rows = read_rows(path)
        if has_source(rows, kw.get("source_kind") or "manual", kw.get("source_ref") or ""):
            raise LedgerError(f"이미 반입된 source: {kw.get('source_kind')}/{kw.get('source_ref')} — list 로 확인", EXIT_MISSING)
        row = new_row(rows, text, now, hub=norm_hub(hub, names), **kw)
        if not dry:
            write_rows(path, rows + [row])
    return row


def set_row(path: str, rid: str, now: float, dry: bool, state=None, hub=None, needs=None, done_when=None, evidence=None, note=None) -> dict:
    if state is not None and state not in STATES:
        raise LedgerError(f"상태 값 오류: {state} — {'|'.join(STATES)} 중 하나", EXIT_USAGE)
    names = load_slugs() if hub else {}
    with ledger.locked(path):
        rows = read_rows(path)
        row = next((r for r in rows if r["id"] == rid), None)
        if row is None:
            raise LedgerError(f"id 없음: {rid} — list 로 id 확인, 새 일은 add", EXIT_MISSING)
        if state is not None:
            if state != row["state"] or note:
                push_history(row, now, state, note or "")
            row["state"] = state
        elif note:
            push_history(row, now, row["state"], note)
        if hub is not None:
            row["hub"] = norm_hub(hub, names)
        if needs is not None:
            row["needs"] = split_ids(needs)
        if done_when is not None:
            row["done_when"] = ledger.clean(done_when)
        if evidence:
            row["evidence"] = split_ids_keep(row.get("evidence", []) + split_ids_keep(evidence))
        row["updated_at"] = stamp(now)
        if row["state"] == DONE and not row["evidence"]:
            raise LedgerError(f"증거 없는 완료 불가 — set {rid} --state 완료 --evidence <PR 번호·커밋 해시> 로 지정", EXIT_USAGE)
        check_rows(rows)  # dry-run 도 같은 검사로 거부 여부를 보여줌
        if not dry:
            write_rows(path, rows)
    return row


# ---------- 반입 ----------

def read_jsonl_loose(path: str) -> list:
    """inbox 같은 외부 JSONL. 파일이 없으면 오류, 깨진 줄은 건너뜀"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().splitlines()
    except OSError as err:
        raise LedgerError(f"읽기 실패({path}: {err.strerror}) — 경로·권한 확인", EXIT_IO)
    out = []
    for ln in raw:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def parent_titles(items: list) -> dict:
    """parent_ts → 부모 제목. 같은 parent_ts 를 가진 줄 중 제목이 있는 첫 값"""
    out = {}
    for it in items:
        if it.get("parent_ts") and it.get("title"):
            out.setdefault(str(it["parent_ts"]), ledger.clean(it["title"]))
    return out


def inbox_candidates(items: list, is_reply) -> list:
    """사용자 자유 문장 → [(ts, text, context, received)]. 봇 즉답 명령·결정 카드 답·버튼·반응·빈 글 제외"""
    titles = parent_titles(items)
    out = []
    for it in items:
        kind = it.get("type")
        text = (it.get("text") or "").strip()
        if kind not in ("note", "thread_reply") or not text or not it.get("ts") or is_reply(text):
            continue
        if kind == "thread_reply" and (it.get("kind") == "decide" or it.get("decision_id")):
            continue
        ctx = ledger.clean(it.get("title") or titles.get(str(it.get("parent_ts") or ""), "")) if kind == "thread_reply" else ""
        try:
            recv = float(it["ts"])
        except ValueError:
            recv = float(it.get("received_at") or 0)
        out.append((str(it["ts"]), text, ctx, recv))
    return out


def routes_reply(path: str):
    """intake 의 즉답 명령 표를 그대로 씀(대기·PR 등). 표를 못 읽으면 오류"""
    import intake  # 지연 가져오기: slack_push 등을 끌고 오므로 반입 때만
    return intake.load_routes(path).is_reply


def default_routes() -> str:
    return os.environ.get("LRM_INTAKE_ROUTES") or os.path.join(HUB_DIR, "intake-routes.tsv")


def commit_import(path: str, now: float, dry: bool, build) -> dict:
    """잠금 안에서 build(rows) → [행] 을 만들고 한 번에 씀. 결과 요약 {added, skipped, by_state, sample, ids}"""
    with ledger.locked(path):
        rows = read_rows(path)
        fresh, skipped = build(rows)
        merged = rows + fresh
        check_rows(merged)  # 하나라도 거부면 아무것도 쓰지 않음
        if not dry and fresh:
            write_rows(path, merged)
    by_state = {}
    for r in fresh:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    return {"added": len(fresh), "skipped": skipped, "by_state": by_state,
            "sample": [{"id": r["id"], "state": r["state"], "text": r["text"][:60], "context": r["source"].get("context", "")} for r in fresh[:5]]}


def import_inbox(path: str, inbox: str, routes: str, now: float, dry: bool) -> dict:
    cands = inbox_candidates(read_jsonl_loose(inbox), routes_reply(routes))

    def build(rows):
        fresh, skipped, seen = [], 0, set()
        for ts, text, ctx, recv in cands:
            if ts in seen or has_source(rows, "inbox", ts):
                skipped += 1
                continue
            seen.add(ts)
            fresh.append(new_row(rows + fresh, text, now, source_kind="inbox", source_ref=ts, context=ctx, received_at=stamp(recv or now),
                                 note="Slack inbox 에서 반입"))
        return fresh, skipped

    return commit_import(path, now, dry, build)


def import_feedback(path: str, feedback: str, now: float, dry: bool) -> dict:
    src_rows = ledger.read_rows(feedback)
    names = load_slugs()

    def build(rows):
        fresh, skipped = [], 0
        for r in src_rows:
            if has_source(rows, "feedback", r["id"]):
                skipped += 1
                continue
            state = FEEDBACK_STATE.get(r["state"])
            if state is None:
                raise LedgerError(f"피드백 {r['id']} 상태 '{r['state']}' 대응 없음 — 접수·진행·완료·보류 중 하나로 고친 뒤 재시도", EXIT_USAGE)
            year = (r["updated"] or stamp(now))[:4]
            recv = f"{year}-{r['received']} 00:00" if re.fullmatch(r"\d\d-\d\d", r["received"]) else stamp(now)
            row = new_row(rows + fresh, r["request"], now, source_kind="feedback", source_ref=r["id"], received_at=recv, state=state,
                          hub=hub_by_label(r["owner"], names), evidence=[r["evidence"]] if r["evidence"] else [], note=r["note"])
            row["updated_at"] = r["updated"] or row["updated_at"]
            fresh.append(row)
        return fresh, skipped

    return commit_import(path, now, dry, build)


class TodoParser(html.parser.HTMLParser):
    """할일 html → [{cls, text, note}]. div.c 하나가 한 일, 안의 small 은 부연"""

    def __init__(self):
        super().__init__()
        self.items, self._cur, self._small = [], None, False

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "").split()
        if tag == "div" and "c" in cls:
            self._cur = {"cls": next((c for c in cls if c in TODO_GROUPS), ""), "text": "", "note": ""}
        elif tag == "small" and self._cur is not None:
            self._small = True

    def handle_endtag(self, tag):
        if tag == "small":
            self._small = False
        elif tag == "div" and self._cur is not None:
            self.items.append(self._cur)
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["note" if self._small else "text"] += data


def parse_todo(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            parser = TodoParser()
            parser.feed(f.read())
    except OSError as err:
        raise LedgerError(f"할일 html 읽기 실패({err.strerror}) — 경로 확인", EXIT_IO)
    return [{**it, "text": ledger.clean(it["text"]), "note": ledger.clean(it["note"])} for it in parser.items if it["cls"] and ledger.clean(it["text"])]


def import_todo(path: str, html_path: str, now: float, dry: bool) -> dict:
    items = parse_todo(html_path)
    stem = os.path.splitext(os.path.basename(html_path))[0]
    day = re.search(r"(\d{4})(\d\d)(\d\d)", stem)
    recv = f"{day.group(1)}-{day.group(2)}-{day.group(3)} 00:00" if day else stamp(now)

    def build(rows):
        fresh, skipped = [], 0
        for it in items:
            state, hub = TODO_GROUPS[it["cls"]]
            if it["cls"] == "done":
                prs = PR_NUM.findall(it["text"] + " " + it["note"])
                units = [(f"{stem}:done:{n}", f"PR #{n} 병합 완료", [f"PR #{n}"]) for n in prs] or [(f"{stem}:done:{hashlib.sha1(it['text'].encode()).hexdigest()[:8]}", it["text"], [])]
            else:
                text = it["text"] + (f" — {it['note']}" if it["note"] else "")
                units = [(f"{stem}:{it['cls']}:{hashlib.sha1(it['text'].encode()).hexdigest()[:8]}", text, [])]
            for ref, text, evidence in units:
                if has_source(rows + fresh, "todo-html", ref):
                    skipped += 1
                    continue
                fresh.append(new_row(rows + fresh, text, now, source_kind="todo-html", source_ref=ref, received_at=recv, state=state, hub=hub,
                                     evidence=evidence, note=f"{stem} 에서 반입"))
        return fresh, skipped

    return commit_import(path, now, dry, build)


# ---------- CLI ----------

def view(row: dict) -> str:
    out = [f"{EMOJI.get(row['state'], '·')} {row['id']}  {row['state']}  담당 {row.get('hub') or '미정'}", f"  원문: {row['text']}",
           f"  받음: {row['received_at']}  갱신: {row['updated_at']}", f"  전제조건: {', '.join(row.get('needs', [])) or '없음'}",
           f"  완료 기준: {row.get('done_when') or '미확인'}", f"  증거: {' · '.join(row.get('evidence', [])) or '없음'}",
           f"  출처: {row['source'].get('kind')} {row['source'].get('ref')}" + (f" ({row['source']['context']})" if row["source"].get("context") else "")]
    out += ["  기록:"] + [f"    {h['at']} {h['state']} {h.get('note', '')}".rstrip() for h in row.get("history", [])]
    return "\n".join(out)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"오류: {message} — --help 참고\n")
        sys.exit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="task_ledger.py", formatter_class=argparse.RawDescriptionHelpFormatter,
               description="작업 장부(JSONL) 도구. 경로는 환경 변수 LRM_TASKS(기본 ~/.config/lrm-hubs/tasks.jsonl).",
               epilog="예:\n"
                      "  task_ledger.py add --text '확인 창 7곳 누르기' --hub 사용자 --state 배정\n"
                      "  task_ledger.py set T0003 --state 진행 --hub vfx --needs T0001,T0002\n"
                      "  task_ledger.py set T0003 --state 완료 --evidence 'PR #780'\n"
                      "  task_ledger.py list --state 막힘 --json\n"
                      "  task_ledger.py import-inbox --dry-run --json\n"
                      "상태: " + "|".join(STATES) + "\n"
                      "종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(증거 없는 완료·없는 id·순환 포함), 3 대상 없음·중복")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    def dry(sp):
        sp.add_argument("--dry-run", action="store_true", help="쓰지 않고 결과만 출력")

    def js(sp):
        sp.add_argument("--json", action="store_true", help="JSON 으로 출력")

    a = sub.add_parser("add", help="새 일 추가", description="새 일 추가(기본 상태 미분류). 같은 source 는 거부(exit 3).")
    a.add_argument("--text", required=True, help="원문")
    a.add_argument("--hub", default="", help="담당 허브 slug(hubs.tsv) 또는 사용자·main")
    a.add_argument("--state", choices=STATES, metavar="|".join(STATES), help="상태(기본 미분류)")
    a.add_argument("--needs", help="전제조건 id, 쉼표로 구분")
    a.add_argument("--done-when", help="완료 기준")
    a.add_argument("--evidence", action="append", help="증거(PR 번호·커밋 해시). 여러 번 지정 가능")
    a.add_argument("--source-kind", choices=SRC_KINDS, default="manual", help="출처 종류")
    a.add_argument("--source-ref", default="", help="출처 식별값(ts·C0004 등). 같은 값은 한 번만 반입")
    dry(a)
    js(a)
    s = sub.add_parser("set", help="상태·담당·전제조건·완료 기준·증거 변경", description="주어진 칸만 바꿈. 완료는 증거 필수(없으면 exit 2), needs 는 있는 id 만·순환 금지.")
    s.add_argument("id", help="일 번호(예 T0003)")
    s.add_argument("--state", choices=STATES, metavar="|".join(STATES))
    s.add_argument("--hub", help="담당 허브 slug")
    s.add_argument("--needs", help="전제조건 id 전체를 이 값으로 교체. 쉼표 구분, 빈 문자열이면 비움")
    s.add_argument("--done-when", help="완료 기준")
    s.add_argument("--evidence", action="append", help="증거 추가. 여러 번 지정 가능")
    s.add_argument("--note", help="상태 기록에 남길 짧은 말")
    dry(s)
    js(s)
    sh = sub.add_parser("show", help="한 건 자세히", description="한 건의 모든 칸과 상태 기록 출력.")
    sh.add_argument("id")
    js(sh)
    ls = sub.add_parser("list", help="목록", description="목록. 필터는 AND.")
    ls.add_argument("--state", choices=STATES, metavar="|".join(STATES))
    ls.add_argument("--hub", help="담당 허브 slug")
    js(ls)
    i = sub.add_parser("import-inbox", help="Slack inbox 자유 문장을 미분류로 반입",
                       description="slack-inbox.jsonl 의 note·결정 카드 아닌 thread_reply 를 미분류 행으로. 봇 즉답 명령·버튼·반응 제외. 스레드 답글은 부모 제목을 문맥으로 붙임. ts 로 멱등.")
    i.add_argument("--inbox", default=os.environ.get("LRM_INBOX") or os.path.join(HUB_DIR, "slack-inbox.jsonl"), help="inbox 경로(기본 LRM_INBOX 또는 작업 폴더)")
    i.add_argument("--routes", default=None, help="즉답 명령 표(기본 LRM_INTAKE_ROUTES 또는 작업 폴더 intake-routes.tsv)")
    dry(i)
    js(i)
    f = sub.add_parser("import-feedback", help="피드백 장부 행을 1회 반입",
                       description="feedback-ledger.tsv 행을 상태 대응(접수→미분류, 진행→진행, 완료→완료, 보류→막힘)으로 반입. 피드백 id 로 멱등. 증거 없는 완료가 있으면 전체 중단(exit 2).")
    f.add_argument("--feedback", default=os.environ.get("LRM_LEDGER") or ledger.DEFAULT_PATH, help="피드백 장부 경로(기본 LRM_LEDGER)")
    dry(f)
    js(f)
    t = sub.add_parser("import-todo", help="할일 html 을 행으로 반입",
                       description="할일 목록 html 의 div.c 를 행으로. me→배정·사용자, hub→진행, q→막힘, done→완료(PR 번호마다 한 행, 증거 PR). 문장 해시로 멱등.")
    t.add_argument("html", help="할일 html 경로")
    dry(t)
    js(t)
    return p


def emit(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2) if getattr(args, "json", False) else text)


def summarize(res: dict, dry_run: bool) -> str:
    lines = [("[dry-run] " if dry_run else "") + f"반입 {res['added']}건 · 이미 있음 {res['skipped']}건 · 상태별 " + (", ".join(f"{k} {v}" for k, v in res["by_state"].items()) or "없음")]
    lines += [f"  {s['id']} {s['state']} {s['text']}" + (f" ← {s['context']}" if s["context"] else "") for s in res["sample"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    path, now = tasks_path(), time.time()
    try:
        if args.cmd == "add":
            row = add_row(path, args.text, now, args.dry_run, hub=args.hub, state=args.state, needs=args.needs, done_when=args.done_when,
                          evidence=args.evidence, source_kind=args.source_kind, source_ref=args.source_ref)
            emit(args, row, ("[dry-run] " if args.dry_run else "") + f"추가: {row['id']} {row['state']} · {row['text']}")
        elif args.cmd == "set":
            row = set_row(path, args.id, now, args.dry_run, state=args.state, hub=args.hub, needs=args.needs, done_when=args.done_when,
                          evidence=args.evidence, note=args.note)
            emit(args, row, ("[dry-run] " if args.dry_run else "") + f"변경: {row['id']} → {row['state']} ({row['updated_at']})")
        elif args.cmd == "show":
            row = next((r for r in read_rows(path) if r["id"] == args.id), None)
            if row is None:
                raise LedgerError(f"id 없음: {args.id} — list 로 id 확인", EXIT_MISSING)
            emit(args, row, view(row))
        elif args.cmd == "list":
            rows = [r for r in read_rows(path) if (not args.state or r["state"] == args.state) and (not args.hub or r.get("hub") == args.hub)]
            emit(args, rows, "\n".join(f"{EMOJI.get(r['state'], '·')} {r['id']}\t{r['state']}\t{r.get('hub') or '-'}\t{r['text'][:70]}" for r in rows))
        else:
            if args.cmd == "import-inbox":
                res = import_inbox(path, args.inbox, args.routes or default_routes(), now, args.dry_run)
            elif args.cmd == "import-feedback":
                res = import_feedback(path, args.feedback, now, args.dry_run)
            else:
                res = import_todo(path, args.html, now, args.dry_run)
            res["dry_run"] = args.dry_run
            emit(args, res, summarize(res, args.dry_run))
    except LedgerError as err:
        sys.stderr.write(f"오류: {err}\n")
        return err.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
