#!/usr/bin/env python3
"""사용자 확인 목록 원천: 문서 사이트 첫 화면·Slack 할 일 목록·옆 패널이 같은 항목 집합을 쓰게 하는 한 곳.

항목 = 종류 + 상태(todo|done) + 근거. 종류 6개: 결정 카드 · PR 병합 · 검수 자료 · 직접 실행 명령 · 시트/콘솔 작업 · 창 승인.
행 규칙·근거 조회는 todo_items·todo_facts 를 그대로 씀(복사 없음). 이 모듈이 더하는 것은 직접 입력 3분류, 확인 기록, 사라진 항목의 완료 판정.
근거(상태가 done 인 이유):
  결정  카드 닫힘(answer_store 기록: 문서 사이트·Slack 어느 쪽 답인지 표기)
  PR    gh 병합·닫힘(저장소별 키 pr:owner/name#N)
  그 밖  확인 기록(confirm-log.jsonl, 사용자가 '했음'을 누름) 또는 허브 상태에서 사라짐(허브 보고)
사라진 항목을 기억하는 명부(confirm-seen.json)가 있어야 병합된 PR·끝난 허브 항목을 '함' 으로 보여 줌. 근거 없이 사라진 결정·PR 은 목록에서 뺌.
기록 파일은 모두 flock 아래 쓰고 명부는 임시 파일 → rename 으로 교체. 환경: LRM_HUB_DIR 작업 폴더
"""
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field, fields

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import answer_store  # noqa: E402
import card_actions as actions  # noqa: E402
import todo_facts  # noqa: E402
import todo_items  # noqa: E402

DECISION, MERGE, REVIEW, COMMAND, SHEET, WINDOW = "decision", "merge", "review", "command", "sheet", "window"
KINDS = ((DECISION, "🙋 결정 카드"), (MERGE, "🔀 PR 병합"), (REVIEW, "👀 검수 자료"), (COMMAND, "⌨️ 직접 실행 명령"), (SHEET, "📋 시트·콘솔 작업"), (WINDOW, "🪟 창 승인"))
KIND_LABEL = dict(KINDS)
KIND_ORDER = tuple(k for k, _ in KINDS)
TODO, DONE = "todo", "done"
CONFIRMABLE = (REVIEW, COMMAND, SHEET, WINDOW)  # '했음' 버튼으로 확인 기록을 남길 수 있는 종류. 결정·병합은 근거가 따로 있음
LOG_NAME, SEEN_NAME, LOCK_NAME = "confirm-log.jsonl", "confirm-seen.json", "confirm-items.lock"
DONE_KEEP_S = 7 * 24 * 3600  # 완료 항목을 목록에 남기는 기간
KEY_RE = re.compile(r"[\w:/#.\-가-힣]{1,200}")

_COMMAND = re.compile(r"`([^`]{2,200})`")
_COMMAND_WORD = re.compile(r"직접 실행|명령|터미널|\bnpm\b|\bgit\b|\bgh\b|\./|\.sh\b")
_SHEET = re.compile(r"시트|콘솔|스프레드|Console|Sheet|Firebase|Play ?Console|App ?Store")
_PR_KEY = re.compile(r"^pr:([^#]+)#(\d+)$")
_DECISION_KEY = re.compile(r"^decision:(D-[0-9A-Za-z]{1,16})$")


@dataclass
class Item:
    key: str
    kind: str
    state: str
    title: str
    hub: str
    urgency: int
    since: float
    how: str  # 하는 법 한 줄
    targets: tuple = ()
    command: str = ""  # 직접 실행 명령 본문(제목에서 백틱으로 감싼 부분)
    url: str = ""  # PR 주소
    did: str = ""  # 결정 번호
    basis: str = ""  # done 일 때 근거 문장
    done_at: float = 0.0
    row: object = field(default=None, repr=False, compare=False)  # 원본 todo_items.Row. 목록 화면(digest)이 씀. 저장·응답에는 넣지 않음

    @property
    def done(self) -> bool:
        return self.state == DONE

    def public(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "row"}
        d["kind_label"] = KIND_LABEL[self.kind]
        d["confirmable"] = self.kind in CONFIRMABLE
        return d


def kind_of(row, raw_title: str) -> str:
    """todo_items 종류 → 확인 종류. 직접 입력은 명령·시트·창으로 가름(명령 > 시트 > 창 승인)"""
    if row.kind == todo_items.DECISION:
        return DECISION
    if row.kind == todo_items.MERGE:
        return MERGE
    if row.kind == todo_items.REVIEW:
        return REVIEW
    if _COMMAND.search(raw_title) or _COMMAND_WORD.search(raw_title):
        return COMMAND
    return SHEET if _SHEET.search(raw_title) else WINDOW


def pr_url(key: str) -> str:
    m = _PR_KEY.match(key)
    return f"https://github.com/{m.group(1)}/pull/{m.group(2)}" if m else ""


class Store:
    """확인 기록(추가 전용)과 명부(항목·처음 본 시각). 파일 접근은 이 클래스 한 곳"""

    def __init__(self, hub_dir: str | None = None) -> None:
        self.dir = hub_dir or os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
        self.log, self.seen_path = os.path.join(self.dir, LOG_NAME), os.path.join(self.dir, SEEN_NAME)
        self.lock = os.path.join(self.dir, LOCK_NAME)

    def confirm(self, key: str, source: str, now: float | None = None) -> bool:
        """확인 기록 한 줄. 이미 있으면 False(중복 기록 없음)"""
        with actions.flock(self.lock):
            if key in self.confirmed():
                return False
            rec = {"key": key, "source": source, "at": int(time.time() if now is None else now)}
            answer_store.AnswerStore.append(self.log, json.dumps(rec, ensure_ascii=False) + "\n")
        return True

    def confirmed(self) -> dict:
        """키 → 확인 기록. 깨진 줄은 건너뜀"""
        out = {}
        try:
            with open(self.log, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and isinstance(d.get("key"), str):
                        out.setdefault(d["key"], d)
        except OSError:
            pass
        return out

    def load_seen(self) -> dict:
        try:
            with open(self.seen_path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        for k in ("entries", "first"):
            if not isinstance(d.get(k), dict):
                d[k] = {}
        return d

    def save_seen(self, seen: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".confirm-seen.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(seen, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.seen_path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def decision_basis(did: str, store_dir: str) -> str:
    """결정 카드가 닫힌 근거 문장. 답 기록이 있으면 어느 쪽 답인지, 없으면 카드 닫힘만"""
    st = answer_store.AnswerStore(store_dir)
    card = st.find_card(did)
    by = st.first_answer(did, card["ts"]) if card else ""
    return answer_store.LATE_TEXT.get(by, "") if by else "결정 카드 닫힘"


def closed_basis(targets, facts, store_dir: str) -> str:
    """끝난 대상의 근거 문장(첫 대상 기준)"""
    for t in targets:
        m = _DECISION_KEY.match(t)
        if m:
            return decision_basis(m.group(1), store_dir)
        if t.startswith(todo_facts.PR_PREFIX):
            info = facts.pr(t[len(todo_facts.PR_PREFIX):]) or {}
            return f"#{t[len(todo_facts.PR_PREFIX):]} {'병합됨' if info.get('state') == 'MERGED' else '닫힘'}"
        if t.startswith(todo_facts.LEDGER_PREFIX):
            return "장부 완료"
    return "대상 종료"


def log_basis(rec: dict) -> str:
    src = answer_store.SOURCE_NAME.get(rec.get("source"), str(rec.get("source") or ""))
    return f"사용자 확인 — {src} {time.strftime('%m-%d %H:%M', time.localtime(rec.get('at', 0)))}"


def from_row(row, raw_title: str) -> Item:
    kind = kind_of(row, raw_title)
    cmd = _COMMAND.search(raw_title)
    m = _DECISION_KEY.match(row.key)
    return Item(row.key, kind, TODO, row.title, row.hub, row.urgency, row.since, row.answer, tuple(row.targets), row=row,
                command=cmd.group(1).strip() if cmd and kind == COMMAND else "", url=pr_url(row.key),
                did=m.group(1) if m else next((t[len("decision:"):] for t in row.targets if t.startswith("decision:")), ""))


def sort_key(it: Item) -> tuple:
    """안 함 먼저 → 종류 → 급함 높은 순 → 오래된 순. 함은 최근 완료 순"""
    if it.done:
        return (1, -it.done_at, 0, "")
    return (0, KIND_ORDER.index(it.kind), -it.urgency, it.since or float("inf"), it.title)


def select(raw, now: float, first: dict, facts, store: Store, prefer=(), same_title=None):
    """세 화면(문서 사이트·Slack 할 일 목록·옆 패널)이 같은 항목 집합을 쓰게 하는 선별 한 곳.
    행 규칙·근거는 todo_items·todo_facts 그대로, 거기에 확인 기록(store)을 반영함.
    반환: (안 함 행 목록, 함 Item 목록(대상 종료·확인 기록), 버린 [(행, 'done'|'dup', 근거)]). 함 Item 의 done_at 은 아직 모름(0)"""
    raw, log = list(raw), store.confirmed()
    titles = {k: t for k, _b, t, _s in raw}
    rows = todo_items.build_rows(raw, now, first, same_title)
    kept, dropped = todo_facts.settle_rows(rows, facts, prefer)
    todo, done = [], []
    for r in kept:
        it = from_row(r, titles.get(r.key, r.title))
        if it.kind in CONFIRMABLE and it.key in log:
            it.state, it.basis, it.done_at = DONE, log_basis(log[it.key]), float(log[it.key].get("at", now))
            done.append(it)
        else:
            todo.append(r)
    for r, kind, why in dropped:
        if kind == "done":  # 수집 목록에는 남았지만 대상이 끝남
            it = from_row(r, titles.get(r.key, r.title))
            it.state, it.basis = DONE, closed_basis(r.targets, facts, store.dir)
            done.append(it)
    return todo, done, dropped


def open_rows(raw, now: float, first: dict, facts, store: Store, prefer=(), same_title=None):
    """할 일 목록(digest)·옆 패널용: 안 함 행만. 반환: (남은 행, 버린 [(행, 'done'|'dup', 근거)]) — 확인 기록으로 끝난 행은 'done' 으로 버림"""
    todo, done, dropped = select(raw, now, first, facts, store, prefer, same_title)
    return todo, [d for d in dropped if d[1] == "dup"] + [(it.row, "done", it.basis) for it in done]


def build(raw, now: float, facts, store: Store, persist: bool = True) -> list:
    """(key, bucket, title, since) 목록 → 확인 항목 목록(안 함 먼저). 근거 조회는 facts, 기록은 store.
    persist=False 면 명부를 쓰지 않음(읽기 전용 호출·시험)"""
    with actions.flock(store.lock):
        seen = store.load_seen()
        entries, first = seen["entries"], seen["first"]
        log = store.confirmed()
        raw = list(raw)
        todo, done, dropped = select(raw, now, first, facts, store)
        titles = {k: t for k, _b, t, _s in raw}
        items = [from_row(r, titles.get(r.key, r.title)) for r in todo] + done
        for it in done:
            it.done_at = it.done_at or now
        live = {it.key for it in items} | {r.key for r, _k, _w in dropped}
        for it in items:
            first.setdefault(it.key, now)
            entries[it.key] = {**it.public(), "done_at": it.done_at or (entries.get(it.key) or {}).get("done_at", 0.0)}
        items = revive(items, entries, live, log, facts, store, now)
        for k in [k for k in first if k not in live]:
            del first[k]
        for k in [k for k, e in entries.items() if e.get("state") == DONE and now - e.get("done_at", 0) > DONE_KEEP_S]:
            del entries[k]
        if persist:
            store.save_seen(seen)
    items.sort(key=sort_key)
    return [it for it in items if not it.done or now - it.done_at <= DONE_KEEP_S]


def revive(items, entries, live, log, facts, store: Store, now: float) -> list:
    """수집 목록에서 사라진 명부 항목의 완료 판정을 덧붙임. 이미 완료로 기록한 것은 그대로 보임.
    결정·병합은 끝난 근거가 있을 때만 함(없으면 명부에서도 지움). 그 밖은 확인 기록 또는 허브 상태에서 사라짐(허브 보고)을 근거로 함"""
    have = {it.key for it in items}
    for key, e in list(entries.items()):
        if key in live or key in have:
            continue
        args = {k: e[k] for k in Item.__dataclass_fields__ if k in e and k != "row"}
        args["targets"] = tuple(args.get("targets") or ())
        it = Item(**args)
        if e.get("state") != DONE:
            if it.kind in CONFIRMABLE and key in log:
                it.basis, it.done_at = log_basis(log[key]), float(log[key].get("at", now))
            elif it.targets and facts.closed(it.targets) == set(it.targets):
                it.basis, it.done_at = closed_basis(it.targets, facts, store.dir), now
            elif it.kind in CONFIRMABLE:
                it.basis, it.done_at = "허브 상태에서 사라짐(허브 보고)", now
            else:
                del entries[key]  # 근거 없이 사라진 결정·병합은 완료로 단정하지 않음
                continue
            it.state = DONE
            e.update(state=DONE, basis=it.basis, done_at=it.done_at)
        items.append(it)
    return items
