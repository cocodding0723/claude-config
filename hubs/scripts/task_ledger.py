#!/usr/bin/env python3
"""작업 장부(JSONL) 도구: 사용자 지시 한 건 = 한 줄. 상태·전제조건·완료 기준·증거를 한 파일에서 추적함.

행 칸: id · text(원문) · received_at · hub(담당 허브 slug) · state · needs(전제조건 id 목록) · done_when · evidence(목록)
      · sources([{kind, ref, context?}]) · updated_at · history(상태 변경 기록, 최근 20건)
      · assigned_at · started_at · done_at(상태에 처음 들어간 시각, 없으면 "") · blocked_reason({kind, note, since}, 막힘일 때만)
      · dropped_reason(버림일 때만) · order(수동 순서, 선택) · outbox_id(Slack 전송 대기열 연결, 선택) · hint(미분류 보조 표시, 선택).
야간 큐 칸(전부 선택, docs/night-queue-design.html): night({tries, sent_at, approved_at, approved_by_card} — 있으면 야간 대상, 머리말 '야간:' 접수에서 만들어짐)
      · gate(auto|morning|user) · after([{kind: pr|decision|task, ref}] 풀려야 시작) · questions([{q, rec, revert, answer}]) · goal(한 줄 목표) · uses(자원 표시 목록, 예 unity).
      야간 진행은 night.approved_at(사용자가 15시 초안 카드에서 승인한 시각)이 있어야 함. 값 검사는 row_problem 한 곳.
시각: ISO 8601 초 단위+시간대(예 2026-11-16T10:02:06+09:00). history 도 같음.
상태: 미분류 → 배정 → 진행 ↔ 막힘 → 완료 | 버림. 완료는 증거(PR #n·owner/repo#n·커밋 해시 7자 이상·URL), 버림은 사유(--reason) 없이 못 바꿈.
needs 는 장부에 있는 id 만, 순환 금지. 같은 source(kind+ref)는 한 번만 반입(멱등). 완료 PR 은 pr:<번호> 전역 키로 중복 제거.
쓰기는 ledger.py 의 flock 잠금·원자적 교체를 그대로 씀. 읽기는 깨진 줄을 경고 후 건너뛰고, 쓰기는 깨진 줄을 .bad 로 옮김.
피드백 장부(feedback-ledger.tsv)·ledger_sync·intake 는 건드리지 않음(Slack 진행 스레드가 읽음).

환경: LRM_TASKS 장부 경로(기본 ~/.config/lrm-hubs/tasks.jsonl) · LRM_HUB_DIR 작업 폴더 · LRM_INBOX · LRM_LEDGER 피드백 장부 · LRM_HUBS_TABLE hubs.tsv
종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(증거 없는 완료·없는 id·순환 포함), 3 대상 없음·중복
"""
import argparse
import contextlib
import datetime
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
STATES = ("미분류", "배정", "진행", "막힘", "완료", "버림")
UNSORTED, ASSIGNED, DOING, BLOCKED, DONE, DROPPED = STATES
EMOJI = {UNSORTED: "📥", ASSIGNED: "📌", DOING: "🔄", BLOCKED: "⛔", DONE: "✅", DROPPED: "🗑️"}
SRC_KINDS = ("inbox", "feedback", "todo-html", "manual")
RESERVED_HUBS = ("사용자", "main")  # hubs.tsv 에 없는 담당. 사용자 직접 일·메인 코디네이터
BLOCK_KINDS = ("확인창", "전제조건", "사용자답", "기타")
# ---------- 야간 큐 칸(값은 이 블록 한 곳) ----------
NIGHT_HEADER = "야간"  # 접수 머리말. hubs/intake-routes.tsv 의 같은 이름 행과 짝
NIGHT_HEADER_RE = re.compile(r"^\s*" + NIGHT_HEADER + r"\s*[:：]")
GATES = ("auto", "morning", "user")
AFTER_KINDS = ("pr", "decision", "task")
AFTER_REF = {"pr": re.compile(r"^(?:[\w.-]+/[\w.-]+)?#\d+$"), "decision": re.compile(r"^D-[A-Za-z0-9][A-Za-z0-9-]*$"), "task": re.compile(r"^T\d{4,}$")}  # hub_status.REF_ID 와 같은 형식
REVERT_LABELS = ("쉬움", "보통", "어려움")
NIGHT_TEXT_KEYS = ("sent_at", "approved_at", "approved_by_card")
GOAL_MAX = 200
USES_VALUES = ("unity",)  # 디스패처가 자원 조건을 따로 보는 표시. 늘리면 night_policy 도 같이
ID_PREFIX, ID_WIDTH = "T", 4
HISTORY_MAX = 20
# 피드백 장부 상태 → 작업 장부 상태
FEEDBACK_STATE = {"접수": UNSORTED, "진행": DOING, "완료": DONE, "보류": BLOCKED}
# 할일 html 묶음(div class) → (상태, 담당, 막힘 종류). 담당 빈 값은 허브 미확인. q 는 답 못 받은 질문
TODO_GROUPS = {"me": (ASSIGNED, "사용자", ""), "hub": (DOING, "", ""), "q": (BLOCKED, "", "사용자답"), "done": (DONE, "", "")}
PR_NUM = re.compile(r"#(\d+)")

# ---------- 반입·증거 규칙(한 곳) ----------
# Slack 이 봇·앱 중계 글에 붙이는 문구. inbox 에 bot_id 가 있으면 그것이 우선, user 가 있으면 사람 글로 보고 문구 검사 생략
BOT_PHRASES = ("다음을 사용하여 보냄", "Sent using")
LOOSE_MIN = 4  # 중복 후보 비교에 필요한 최소 글자 수(공백·숫자·기호 뺀 뒤)
QUESTION_TAIL = re.compile(r"(?:[?？]|까요|인가요|나요|할까|인지)\s*$")
ACK_ONLY = re.compile(r"^(?:ㅇㅋ|ㅇㅇ|네+|넵|넹|예|응|좋아요?|감사(?:합니다)?|ㄱㅅ|ok(?:ay)?|확인|ㅋ+|ㅎ+)[\s.!~]*$", re.I)
HINT_QUESTION, HINT_DISCARD = "질문", "버림후보"
# 증거로 인정하는 형식. 번호·해시 없는 서술은 증거가 아님
EVIDENCE_FORMS = (r"PR #\d+", r"[\w.-]+/[\w.-]+#\d+", r"[0-9a-fA-F]{7,40}", r"https?://\S+")
EVIDENCE_OK = re.compile("(?:" + "|".join(EVIDENCE_FORMS) + ")")
EVIDENCE_FIND = re.compile(r"PR #\d+|[\w.-]+/[\w.-]+#\d+|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b|https?://[^\s)\]>,]+")
EVIDENCE_HELP = "PR #123 · owner/repo#123 · 커밋 해시(7자 이상) · URL"
AUTO_DONE = re.compile(r"^PR #\d+ 병합 완료$")  # import-todo 가 만든 자동 문장. 피드백 원문이 오면 대체됨


def tasks_path() -> str:
    return os.environ.get("LRM_TASKS") or DEFAULT_PATH


def stamp(now: float) -> str:
    """시각 → ISO 8601 초 단위+시간대"""
    return datetime.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")


def stamp_text(text: str, now: float) -> str:
    """피드백 장부의 'YYYY-MM-DD HH:MM'·'YYYY-MM-DD' → ISO. 못 읽으면 now"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt).astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    return stamp(now)


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


def split_ids_keep(value) -> list:
    """증거 목록: 쉼표를 나누지 않음(PR 제목·경로에 쉼표가 들 수 있음). 중복 제거"""
    items = [] if value is None else ([value] if isinstance(value, str) else list(value))
    out = []
    for it in items:
        it = ledger.clean(it)
        if it and it not in out:
            out.append(it)
    return out


def check_evidence(items: list) -> None:
    bad = [e for e in items if not EVIDENCE_OK.fullmatch(e)]
    if bad:
        raise LedgerError(f"증거 형식 오류: {' · '.join(bad)} — 허용 형식: {EVIDENCE_HELP}", EXIT_USAGE)


def extract_evidence(text: str) -> list:
    """자유 서술에서 증거 형식에 맞는 토큰만 추림"""
    return split_ids_keep(EVIDENCE_FIND.findall(text or ""))


def evidence_prs(evidence: list) -> list:
    """증거 목록의 'PR #n' → 전역 키 pr:n"""
    return [f"pr:{e[4:]}" for e in evidence if re.fullmatch(r"PR #\d+", e)]


def pr_keys(row: dict) -> set:
    """행이 이미 다룬 PR 의 전역 키(pr:<번호>). 증거 'PR #n' 과 source ref 'pr:n' 에서 모음"""
    keys = set(evidence_prs(row.get("evidence", [])))
    return keys | {s["ref"] for s in row.get("sources", []) if str(s.get("ref", "")).startswith("pr:")}


def text_key(text: str) -> str:
    """정규화 문장 해시: 공백을 하나로, 소문자. 파일명·묶음은 넣지 않음"""
    return hashlib.sha1(ledger.clean(text).lower().encode()).hexdigest()[:12]


def loose(text: str) -> str:
    """공백·숫자·기호를 뺀 비교 키. 이것만 같으면 중복 후보"""
    return re.sub(r"[\W\d_]+", "", text.lower())


# ---------- 파일 입출력 ----------

def load(path: str) -> tuple:
    """JSONL → (행 목록, 깨진 줄 원문 목록). 파일이 없으면 빈 목록. 깨진 줄은 멈추지 않고 따로 모음"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return [], []
    except OSError as err:
        raise LedgerError(f"작업 장부 읽기 실패({err.strerror}) — LRM_TASKS 경로·권한 확인", EXIT_IO)
    rows, bad = [], []
    for ln in lines:
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            row = None
        if isinstance(row, dict) and row.get("id"):
            rows.append(row)
        else:
            bad.append(ln)
    return rows, bad


def read_rows(path: str) -> list:
    """읽기 전용 명령용: 깨진 줄은 경고만 하고 건너뜀"""
    rows, bad = load(path)
    if bad:
        sys.stderr.write(f"경고: 깨진 줄 {len(bad)}건 건너뜀 — 다음 쓰기 때 {os.path.basename(path)}.bad 로 옮겨짐\n")
    return rows


def load_for_write(path: str, dry: bool) -> tuple:
    rows, bad = load(path)
    if bad:
        sys.stderr.write(f"경고: 깨진 줄 {len(bad)}건 — " + ("dry-run 이라 그대로 둠\n" if dry else f"{os.path.basename(path)}.bad 로 옮기고 진행\n"))
    return rows, bad


def render(rows: list) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)


def write_rows(path: str, rows: list, bad: list = ()) -> None:
    check_rows(rows)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if bad:  # 원문 그대로 보관한 뒤 장부를 교체함. 중간에 죽어도 줄은 어느 한쪽에 남음
            with open(path + ".bad", "a", encoding="utf-8") as f:
                f.write("".join(b + "\n" for b in bad))
                f.flush()
                os.fsync(f.fileno())
    except OSError as err:
        raise LedgerError(f"작업 장부 폴더·.bad 쓰기 실패({err.strerror}) — LRM_TASKS 경로·권한 확인", EXIT_IO)
    ledger.write_atomic(path, render(rows))


@contextlib.contextmanager
def locked(path: str):
    """잠금 파일은 장부 옆에 열리므로 폴더부터 만듦"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except OSError as err:
        raise LedgerError(f"작업 장부 폴더 생성 실패({err.strerror}) — LRM_TASKS 경로 확인", EXIT_IO)
    with ledger.locked(path):
        yield


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


def row_problem(r: dict, ids: set) -> str:
    st, ev = r.get("state"), [e for e in r.get("evidence", []) if str(e).strip()]
    br = r.get("blocked_reason")
    if st not in STATES:
        return f"알 수 없는 상태 '{st}'"
    if st == DONE and not ev:
        return "증거 없는 완료"
    if any(not EVIDENCE_OK.fullmatch(str(e)) for e in ev):
        return f"증거 형식 오류(허용: {EVIDENCE_HELP})"
    if st == DROPPED and not str(r.get("dropped_reason", "")).strip():
        return "사유 없는 버림"
    if br is not None and (st != BLOCKED or not isinstance(br, dict) or br.get("kind") not in BLOCK_KINDS):
        return f"blocked_reason 은 막힘 상태에서 kind({'|'.join(BLOCK_KINDS)})와 함께만"
    if "order" in r and (isinstance(r["order"], bool) or not isinstance(r["order"], int)):
        return "order 는 정수"
    return night_problem(r)


def night_problem(r: dict) -> str:
    """야간 큐 칸 검사. 칸이 없으면 통과(옛 행 호환)"""
    if "gate" in r and r["gate"] not in GATES:
        return f"gate 는 {'|'.join(GATES)} 중 하나"
    if "goal" in r and (not isinstance(r["goal"], str) or len(r["goal"]) > GOAL_MAX):
        return f"goal 은 {GOAL_MAX}자 이내 문자열"
    if "uses" in r and (not isinstance(r["uses"], list) or any(u not in USES_VALUES for u in r["uses"])):
        return f"uses 는 {'|'.join(USES_VALUES)} 목록"
    if "after" in r:
        a = r["after"]
        if not isinstance(a, list) or any(not isinstance(x, dict) or x.get("kind") not in AFTER_KINDS or not AFTER_REF[x["kind"]].match(str(x.get("ref", ""))) for x in a):
            return "after 는 [{kind: pr|decision|task, ref}] — ref 형식은 pr:Owner/Repo#N·decision:D-ID·task:T0123"
    if "questions" in r:
        q = r["questions"]
        if not isinstance(q, list) or any(not isinstance(x, dict) or not str(x.get("q", "")).strip() or x.get("revert") not in REVERT_LABELS
                                          or not isinstance(x.get("answer"), (str, type(None))) for x in q):
            return f"questions 는 [{{q, rec, revert({'|'.join(REVERT_LABELS)}), answer(문자열|null)}}]"
    if "night" in r:
        n = r["night"]
        if not isinstance(n, dict) or set(n) - {"tries", *NIGHT_TEXT_KEYS} or isinstance(n.get("tries", 0), bool) or not isinstance(n.get("tries", 0), int) \
                or n.get("tries", 0) < 0 or any(not isinstance(n.get(k, ""), str) for k in NIGHT_TEXT_KEYS):
            return "night 는 {tries(0 이상 정수), sent_at, approved_at, approved_by_card(문자열)}"
        if bool(n.get("approved_at")) != bool(n.get("approved_by_card")):
            return "night.approved_at 과 approved_by_card 는 같이 있어야 함"
    return ""


def check_rows(rows: list) -> None:
    """쓰기 직전 불변식: id 유일·상태 값·증거(형식)·버림 사유·막힘 사유·needs 는 있는 id·순환 금지"""
    ids, bad = set(), []
    for r in rows:
        rid = r["id"]
        why = "id 중복" if rid in ids else row_problem(r, ids)
        ids.add(rid)
        if why:
            ref = (r.get("sources") or [{}])[0].get("ref")
            bad.append(f"{rid}{f'({ref})' if ref else ''}: {why}")
    for r in rows:
        for n in split_ids(r.get("needs")):
            if n not in ids:
                bad.append(f"{r['id']}: needs 의 {n} 가 장부에 없음")
            elif n == r["id"]:
                bad.append(f"{r['id']}: needs 에 자기 자신")
    if bad:
        raise LedgerError("작업 장부 검사 실패 — " + " · ".join(bad) + ". 완료는 --evidence, 버림은 --reason 필요, needs 는 list 로 확인한 id 만")
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


def derive(rows: list) -> dict:
    """파생 칸(저장 안 함): id → (blocks_count: 이 일을 전제로 둔 행 수, blocked_by: 완료 안 된 전제조건 id)"""
    state = {r["id"]: r["state"] for r in rows}
    blocks = {}
    for r in rows:
        for n in split_ids(r.get("needs")):
            blocks[n] = blocks.get(n, 0) + 1
    return {r["id"]: (blocks.get(r["id"], 0), [n for n in split_ids(r.get("needs")) if state.get(n) != DONE]) for r in rows}


def with_derived(rows: list, universe: list) -> list:
    d = derive(universe)
    return [{**r, "blocks_count": d[r["id"]][0], "blocked_by": d[r["id"]][1]} for r in rows]


# ---------- 야간 큐 칸 다루기 ----------

def new_night() -> dict:
    return {"tries": 0, "sent_at": "", "approved_at": "", "approved_by_card": ""}


def is_night(row: dict) -> bool:
    return isinstance(row.get("night"), dict)


def night_approved(row: dict) -> bool:
    return is_night(row) and bool(row["night"].get("approved_at"))


def parse_after(value) -> list:
    """'pr:#12,decision:D-X,task:T0001' → [{kind, ref}]. 형식 오류는 check_rows 가 거부"""
    out = []
    for item in split_ids(value):
        kind, _, ref = item.partition(":")
        out.append({"kind": kind, "ref": ref})
    return out


def parse_questions(values) -> list:
    """'질문|추천안|쉬움' 반복 → questions. 추천안·되돌리기 칸은 비울 수 있으나 되돌리기는 기본 보통"""
    out = []
    for raw in values or []:
        q, rec, revert = (ledger.clean(c) for c in (raw.split("|") + ["", ""])[:3])
        out.append({"q": q, "rec": rec, "revert": revert or "보통", "answer": None})
    return out


def approve_night(path: str, rid: str, card: str, now: float, dry: bool = False) -> dict:
    """사용자 승인 기록. 이미 승인된 행은 그대로(먼저 온 승인만 인정). 야간 대상이 아닌 행은 거부"""
    with locked(path):
        rows, bad = load_for_write(path, dry)
        row = next((r for r in rows if r["id"] == rid), None)
        if row is None:
            raise LedgerError(f"id 없음: {rid} — list 로 id 확인", EXIT_MISSING)
        if not is_night(row):
            raise LedgerError(f"{rid} 는 야간 대상이 아님 — 머리말 '{NIGHT_HEADER}:' 접수 또는 set --night", EXIT_USAGE)
        if not row["night"].get("approved_at"):
            row["night"].update(approved_at=stamp(now), approved_by_card=ledger.clean(card))
            row["updated_at"] = stamp(now)
            push_history(row, row["updated_at"], row["state"], f"야간 승인 {ledger.clean(card)}")
            check_rows(rows)
            if not dry:
                write_rows(path, rows, bad)
    return row


# ---------- 행 만들기·바꾸기 ----------

def next_id(rows: list) -> str:
    top = max((int(m.group(1)) for r in rows if (m := re.fullmatch(ID_PREFIX + r"(\d+)", r["id"]))), default=0)
    return f"{ID_PREFIX}{top + 1:0{ID_WIDTH}d}"


def push_history(row: dict, at: str, state: str, note: str = "") -> None:
    entry = {"at": at, "state": state}
    if note:
        entry["note"] = ledger.clean(note)[:80]
    row["history"] = (row.get("history", []) + [entry])[-HISTORY_MAX:]


def enter_state(row: dict, state: str, at: str, kind: str = "", note: str = "", reason: str = "") -> None:
    """상태를 바꾸고 그에 딸린 칸(시각·막힘 사유·버림 사유)을 맞춤. 상태가 바뀌면 이전 상태의 사유는 지움"""
    was = row.get("state")
    row["state"] = state
    for st, key in ((ASSIGNED, "assigned_at"), (DOING, "started_at")):
        row.setdefault(key, "")
        if state == st and not row[key]:
            row[key] = at
    row["done_at"] = (at if was != DONE or not row.get("done_at") else row["done_at"]) if state == DONE else ""
    if state == BLOCKED:
        prev = row.get("blocked_reason") if was == BLOCKED else None
        if prev is None or kind or note:
            prev = prev or {}
            row["blocked_reason"] = {"kind": kind or prev.get("kind") or "기타", "note": ledger.clean(note) if note else prev.get("note", ""),
                                     "since": prev.get("since") or at}
    else:
        row.pop("blocked_reason", None)
    if state == DROPPED:
        row["dropped_reason"] = ledger.clean(reason) or row.get("dropped_reason", "")
    else:
        row.pop("dropped_reason", None)


def hint_for(text: str) -> str:
    return HINT_DISCARD if ACK_ONLY.match(text) else HINT_QUESTION if QUESTION_TAIL.search(text) else ""


def new_row(rows: list, text: str, now: float, **kw) -> dict:
    """새 행. 값 검사는 write_rows 의 check_rows 가 함. state_at 은 상태에 들어간 시각(반입 원본의 시각)"""
    text = ledger.clean(text)
    if not text:
        raise LedgerError("text 는 비울 수 없음 — 원문을 지정", EXIT_USAGE)
    kind = kw.get("source_kind") or "manual"
    if kind not in SRC_KINDS:
        raise LedgerError(f"source 종류 오류: {kind} — {'|'.join(SRC_KINDS)} 중 하나", EXIT_USAGE)
    src = {"kind": kind, "ref": ledger.clean(kw.get("source_ref") or "")}
    if kw.get("context"):
        src["context"] = ledger.clean(kw["context"])
    at = kw.get("state_at") or stamp(now)
    evidence = split_ids_keep(kw.get("evidence"))
    check_evidence(evidence)
    row = {"id": next_id(rows), "text": text, "received_at": kw.get("received_at") or stamp(now), "hub": kw.get("hub", ""),
           "state": "", "needs": split_ids(kw.get("needs")), "done_when": ledger.clean(kw.get("done_when") or ""),
           "evidence": evidence, "sources": [src], "updated_at": kw.get("updated_at") or stamp(now), "history": [],
           "assigned_at": "", "started_at": "", "done_at": ""}
    enter_state(row, kw.get("state") or UNSORTED, at, kw.get("blocked_kind", ""), kw.get("blocked_note", ""), kw.get("reason", ""))
    for key in ("order", "outbox_id", "hint", "gate", "goal"):
        if kw.get(key) not in (None, ""):
            row[key] = kw[key]
    for key in ("after", "questions", "uses"):
        if kw.get(key):
            row[key] = kw[key]
    if kw.get("night") or NIGHT_HEADER_RE.match(text):
        row["night"] = new_night()
    push_history(row, at, row["state"], kw.get("note", ""))
    return row


def source_hit(rows: list, kind: str, ref: str):
    """같은 kind+ref 를 sources 에 가진 첫 행"""
    if not ref:
        return None
    return next((r for r in rows if any(s.get("kind") == kind and s.get("ref") == ref for s in r.get("sources", []))), None)


def has_source(rows: list, kind: str, ref: str) -> bool:
    return source_hit(rows, kind, ref) is not None


def parse_order(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        raise LedgerError(f"order 값 오류: {value} — 정수(작을수록 앞). 지우려면 빈 문자열", EXIT_USAGE)


def add_row(path: str, text: str, now: float, dry: bool, hub="", link="", **kw) -> dict:
    names = load_slugs() if hub else {}
    kind, ref = kw.get("source_kind") or "manual", kw.get("source_ref") or ""
    with locked(path):
        rows, bad = load_for_write(path, dry)
        if has_source(rows, kind, ref):
            raise LedgerError(f"이미 반입된 source: {kind}/{ref} — list 로 확인", EXIT_MISSING)
        if link:  # 새 행 대신 기존 행에 출처만 추가
            target = next((r for r in rows if r["id"] == link), None)
            if target is None:
                raise LedgerError(f"--link 대상 id 없음: {link} — list 로 id 확인", EXIT_MISSING)
            if not ref:
                raise LedgerError("--link 는 --source-ref 필요 — 추가할 출처 식별값을 지정", EXIT_USAGE)
            if kind not in SRC_KINDS:
                raise LedgerError(f"source 종류 오류: {kind} — {'|'.join(SRC_KINDS)} 중 하나", EXIT_USAGE)
            add_source(target, kind, ref, kw.get("context"), stamp(now))
            row = target
        else:
            row = new_row(rows, text, now, hub=norm_hub(hub, names), order=parse_order(kw.pop("order", None)), **kw)
            rows = rows + [row]
        check_rows(rows)  # dry-run 도 같은 검사로 거부 여부를 보여줌
        if not dry:
            write_rows(path, rows, bad)
    return row


def add_source(row: dict, kind: str, ref: str, context, at: str) -> None:
    src = {"kind": kind, "ref": ledger.clean(ref)}
    if context:
        src["context"] = ledger.clean(context)
    row["sources"] = row.get("sources", []) + [src]
    row["updated_at"] = at
    push_history(row, at, row["state"], f"출처 추가 {kind}/{src['ref']}")


def set_row(path: str, rid: str, now: float, dry: bool, state=None, hub=None, needs=None, done_when=None, evidence=None, note=None,
            blocked_kind=None, blocked_note=None, reason=None, order=None, outbox_id=None,
            gate=None, goal=None, after=None, questions=None, uses=None, night=None) -> dict:
    if state is not None and state not in STATES:
        raise LedgerError(f"상태 값 오류: {state} — {'|'.join(STATES)} 중 하나", EXIT_USAGE)
    new_order = parse_order(order)
    new_evidence = split_ids_keep(evidence)
    check_evidence(new_evidence)
    names = load_slugs() if hub else {}
    with locked(path):
        rows, bad = load_for_write(path, dry)
        row = next((r for r in rows if r["id"] == rid), None)
        if row is None:
            raise LedgerError(f"id 없음: {rid} — list 로 id 확인, 새 일은 add", EXIT_MISSING)
        at, target = stamp(now), state or row["state"]
        if (blocked_kind or blocked_note) and target != BLOCKED:
            raise LedgerError(f"--blocked-kind·--blocked-note 는 막힘 상태에서만 — 현재 {target}. --state 막힘 과 함께 지정", EXIT_USAGE)
        if reason and target != DROPPED:
            raise LedgerError(f"--reason 은 버림 상태에서만 — 현재 {target}. --state 버림 과 함께 지정", EXIT_USAGE)
        if target == DROPPED and not reason and not row.get("dropped_reason"):
            raise LedgerError(f"사유 없는 버림 불가 — set {rid} --state 버림 --reason <사유> 로 지정", EXIT_USAGE)
        was = row["state"]
        if state is not None or blocked_kind or blocked_note or reason:
            enter_state(row, target, at, blocked_kind or "", blocked_note or "", reason or "")
        if (state is not None and state != was) or note:
            push_history(row, at, row["state"], note or "")
        if hub is not None:
            row["hub"] = norm_hub(hub, names)
        if needs is not None:
            row["needs"] = split_ids(needs)
        if done_when is not None:
            row["done_when"] = ledger.clean(done_when)
        if new_evidence:
            row["evidence"] = split_ids_keep(row.get("evidence", []) + new_evidence)
        if order is not None:
            row.pop("order", None) if new_order is None else row.update(order=new_order)
        if outbox_id is not None:
            row.pop("outbox_id", None) if not outbox_id.strip() else row.update(outbox_id=ledger.clean(outbox_id))
        for key, val in (("gate", gate), ("goal", goal and ledger.clean(goal)), ("after", after), ("questions", questions), ("uses", uses)):
            if val is not None:  # 빈 값(빈 문자열·빈 목록)이면 칸을 지움
                row.pop(key, None) if not val else row.update({key: val})
        if night is not None:
            if night and not is_night(row):
                row["night"] = new_night()
            elif not night:
                row.pop("night", None)
        row["updated_at"] = at
        if row["state"] == DONE and not row["evidence"]:
            raise LedgerError(f"증거 없는 완료 불가 — set {rid} --state 완료 --evidence <{EVIDENCE_HELP}> 로 지정", EXIT_USAGE)
        check_rows(rows)  # dry-run 도 같은 검사로 거부 여부를 보여줌
        if not dry:
            write_rows(path, rows, bad)
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


def is_bot(it: dict) -> bool:
    """봇·AI 중계 글. bot_id 가 있으면 봇, user 가 있으면 사람, 둘 다 없을 때만 BOT_PHRASES 로 판정"""
    if it.get("bot_id"):
        return True
    if it.get("user"):
        return False
    return any(p in (it.get("text") or "") for p in BOT_PHRASES)


def inbox_candidates(items: list, is_reply) -> list:
    """사용자 지시 → [(ts, text, context, received)]. 결정 카드 답(kind=decide)은 포함. 봇 즉답 명령·봇 중계 글·버튼·반응·빈 글 제외"""
    titles = parent_titles(items)
    out = []
    for it in items:
        kind = it.get("type")
        text = (it.get("text") or "").strip()
        if kind not in ("note", "thread_reply") or not text or not it.get("ts") or is_reply(text) or is_bot(it):
            continue
        ctx = ""
        if kind == "thread_reply":
            ctx = ledger.clean(it.get("title") or titles.get(str(it.get("parent_ts") or ""), ""))
            if it.get("kind") == "decide" or it.get("decision_id"):
                ctx = f"{ctx} · kind=decide" if ctx else "kind=decide"
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


class Pool:
    """반입 중 중복 판정용 색인: 느슨한 문장 키 → 행 id, PR 키 → 행"""

    def __init__(self, rows: list):
        self.loose, self.prs, self.info = {}, {}, {"dup_candidates": [], "linked": 0, "demoted": 0}
        for r in rows:
            self.add(r)

    def add(self, row: dict) -> None:
        key = loose(row["text"])
        if len(key) >= LOOSE_MIN and not AUTO_DONE.match(row["text"]):  # 자동 완료 문장은 번호만 달라 PR 키로 판정
            self.loose.setdefault(key, row["id"])
        for k in pr_keys(row):
            self.prs.setdefault(k, row)

    def similar(self, text: str, ref: str) -> None:
        """느슨한 키가 기존 행과 같으면 중복 후보로 기록(행은 그대로 추가)"""
        other = None if ref.startswith("pr:") else self.loose.get(loose(text))
        if other:
            self.info["dup_candidates"].append({"ref": ref, "text": text[:60], "similar_to": other})


def commit_import(path: str, now: float, dry: bool, build) -> dict:
    """잠금 안에서 build(rows, pool) → ([새 행], 건너뜀) 을 만들고 한 번에 씀. 기존 행에 출처만 붙인 경우(linked)도 씀"""
    with locked(path):
        rows, bad = load_for_write(path, dry)
        pool = Pool(rows)
        fresh, skipped = build(rows, pool)
        merged = rows + fresh
        check_rows(merged)  # 하나라도 거부면 아무것도 쓰지 않음
        if not dry and (fresh or pool.info["linked"]):
            write_rows(path, merged, bad)
    by_state = {}
    for r in fresh:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    return {"added": len(fresh), "skipped": skipped, "by_state": by_state, **pool.info,
            "discard_candidates": sum(1 for r in fresh if r.get("hint") == HINT_DISCARD),
            "questions": sum(1 for r in fresh if r.get("hint") == HINT_QUESTION),
            "sample": [{"id": r["id"], "state": r["state"], "text": r["text"][:60], "context": r["sources"][0].get("context", "")} for r in fresh[:5]]}


def import_inbox(path: str, inbox: str, routes: str, now: float, dry: bool) -> dict:
    cands = inbox_candidates(read_jsonl_loose(inbox), routes_reply(routes))

    def build(rows, pool):
        fresh, skipped, seen = [], 0, set()
        for ts, text, ctx, recv in cands:
            if ts in seen or has_source(rows, "inbox", ts):
                skipped += 1
                continue
            seen.add(ts)
            row = new_row(rows + fresh, text, now, source_kind="inbox", source_ref=ts, context=ctx, received_at=stamp(recv or now),
                          state_at=stamp(recv or now), hint=hint_for(text), note="Slack inbox 에서 반입")
            pool.similar(row["text"], ts)
            pool.add(row)
            fresh.append(row)
        return fresh, skipped

    return commit_import(path, now, dry, build)


def import_feedback(path: str, feedback: str, now: float, dry: bool) -> dict:
    src_rows = ledger.read_rows(feedback)
    names = load_slugs()

    def build(rows, pool):
        fresh, skipped = [], 0
        for r in src_rows:
            if has_source(rows + fresh, "feedback", r["id"]):
                skipped += 1
                continue
            state = FEEDBACK_STATE.get(r["state"])
            if state is None:
                raise LedgerError(f"피드백 {r['id']} 상태 '{r['state']}' 대응 없음 — 접수·진행·완료·보류 중 하나로 고친 뒤 재시도", EXIT_USAGE)
            upd = stamp_text(r["updated"], now)
            evidence, note = extract_evidence(r["evidence"]), r["note"]
            if state == DONE and not evidence:  # 증거 형식 없는 '완료'는 완료로 못 옮김. 진행으로 두고 증거 보강을 남김
                state, note = DOING, "완료 표시였으나 증거 형식 없음 — 증거 보강 뒤 완료. " + note
                pool.info["demoted"] += 1
            auto = next((pool.prs[k] for k in evidence_prs(evidence) if k in pool.prs), None)
            if state == DONE and auto and all(s["kind"] == "todo-html" for s in auto["sources"]):
                # 같은 PR 의 todo 자동 행: 새 행을 만들지 않고 피드백 출처를 붙이고 원문을 되살림
                add_source(auto, "feedback", r["id"], None, upd)
                if AUTO_DONE.match(auto["text"]):
                    auto["text"] = ledger.clean(r["request"])
                pool.info["linked"] += 1
                continue
            year = upd[:4]
            recv = stamp_text(f"{year}-{r['received']}", now) if re.fullmatch(r"\d\d-\d\d", r["received"]) else stamp(now)
            row = new_row(rows + fresh, r["request"], now, source_kind="feedback", source_ref=r["id"], received_at=recv, state=state, state_at=upd,
                          updated_at=upd, hub=hub_by_label(r["owner"], names), evidence=evidence, note=note,
                          blocked_kind="기타" if state == BLOCKED else "", blocked_note=r["note"] if state == BLOCKED else "")
            pool.similar(row["text"], r["id"])
            pool.add(row)
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
    recv = stamp_text(f"{day.group(1)}-{day.group(2)}-{day.group(3)}", now) if day else stamp(now)

    def build(rows, pool):
        fresh, skipped = [], 0
        for it in items:
            state, hub, bkind = TODO_GROUPS[it["cls"]]
            if it["cls"] == "done":  # 키에 파일명·묶음을 넣지 않음: 다른 날 목록에 다시 나와도 같은 일
                prs = list(dict.fromkeys(PR_NUM.findall(it["text"] + " " + it["note"])))
                units = [(f"pr:{n}", f"PR #{n} 병합 완료", [f"PR #{n}"]) for n in prs] or [(f"h:{text_key(it['text'])}", it["text"], [])]
            else:
                text = it["text"] + (f" — {it['note']}" if it["note"] else "")
                units = [(f"h:{text_key(it['text'])}", text, [])]
            for ref, text, evidence in units:
                if (ref in pool.prs) if ref.startswith("pr:") else has_source(rows + fresh, "todo-html", ref):
                    skipped += 1
                    continue
                row = new_row(rows + fresh, text, now, source_kind="todo-html", source_ref=ref, received_at=recv, state=state, hub=hub,
                              evidence=evidence, blocked_kind=bkind, note=f"{stem} 에서 반입")
                pool.similar(row["text"], ref)
                pool.add(row)
                fresh.append(row)
        return fresh, skipped

    return commit_import(path, now, dry, build)


# ---------- CLI ----------

JSON_MODE = False  # --json 일 때 오류도 {error, code, hint} JSON 으로 냄


def view(row: dict) -> str:
    srcs = " · ".join(f"{s.get('kind')} {s.get('ref')}" + (f" ({s['context']})" if s.get("context") else "") for s in row.get("sources", []))
    br, times = row.get("blocked_reason"), [f"{k} {row[k]}" for k in ("assigned_at", "started_at", "done_at") if row.get(k)]
    out = [f"{EMOJI.get(row['state'], '·')} {row['id']}  {row['state']}  담당 {row.get('hub') or '미정'}", f"  원문: {row['text']}",
           f"  받음: {row['received_at']}  갱신: {row['updated_at']}" + (f"  ({', '.join(times)})" if times else ""),
           f"  전제조건: {', '.join(row.get('needs', [])) or '없음'}", f"  완료 기준: {row.get('done_when') or '미확인'}",
           f"  증거: {' · '.join(row.get('evidence', [])) or '없음'}", f"  출처: {srcs or '없음'}"]
    if br:
        out.append(f"  막힘: {br['kind']} · {br.get('note') or '-'} (since {br['since']})")
    if row.get("dropped_reason"):
        out.append(f"  버림 사유: {row['dropped_reason']}")
    out += [f"  {k}: {row[k]}" for k in ("order", "outbox_id", "hint", "gate", "goal") if k in row]
    if row.get("after"):
        out.append("  after: " + " · ".join(f"{a['kind']}:{a['ref']}" for a in row["after"]))
    if row.get("uses"):
        out.append("  uses: " + ", ".join(row["uses"]))
    out += [f"  질문: {q['q']} (추천 {q.get('rec') or '-'} · 되돌리기 {q['revert']} · 답 {q['answer'] if q['answer'] is not None else '없음'})" for q in row.get("questions", [])]
    if is_night(row):
        n = row["night"]
        out.append(f"  야간: 시도 {n.get('tries', 0)} · 전달 {n.get('sent_at') or '-'} · 승인 {n.get('approved_at') or '미승인'}" + (f" ({n['approved_by_card']})" if n.get("approved_by_card") else ""))
    out += ["  기록:"] + [f"    {h['at']} {h['state']} {h.get('note', '')}".rstrip() for h in row.get("history", [])]
    return "\n".join(out)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        fail(f"{message} — --help 참고", EXIT_USAGE)


def fail(message: str, code: int) -> None:
    """'원인 — 조치' 오류를 stderr 문장 또는 --json 일 때 stdout JSON 으로 내고 종료"""
    if JSON_MODE:
        cause, _, hint = message.partition(" — ")
        print(json.dumps({"error": cause, "code": code, "hint": hint}, ensure_ascii=False))
    else:
        sys.stderr.write(f"오류: {message}\n")
    sys.exit(code)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="task_ledger.py", formatter_class=argparse.RawDescriptionHelpFormatter,
               description="작업 장부(JSONL) 도구. 경로는 환경 변수 LRM_TASKS(기본 ~/.config/lrm-hubs/tasks.jsonl).",
               epilog="예:\n"
                      "  task_ledger.py add --text '확인 창 7곳 누르기' --hub 사용자 --state 배정\n"
                      "  task_ledger.py add --text '같은 일' --link T0003 --source-kind inbox --source-ref 1793.1   # 기존 행에 출처만 추가\n"
                      "  task_ledger.py set T0003 --state 진행 --hub vfx --needs T0001,T0002\n"
                      "  task_ledger.py set T0003 --state 막힘 --blocked-kind 확인창 --blocked-note '허브 7곳 대기'\n"
                      "  task_ledger.py set T0003 --state 완료 --evidence 'PR #780'\n"
                      "  task_ledger.py set T0004 --state 버림 --reason '기획 변경으로 불필요'\n"
                      "  task_ledger.py list --state 막힘 --json\n"
                      "  task_ledger.py add --text '야간: 문서 정리' --hub docs --state 배정 --gate auto --goal '문서 이름 규칙 반영' --question '파일 이름?|A|쉬움' --uses unity\n"
                      "  task_ledger.py list --night-unapproved --json   # 15시 초안 후보\n"
                      "  task_ledger.py approve-night T0003 --card D-NQ0003-1116\n"
                      "  task_ledger.py import-inbox --dry-run --json\n"
                      "상태: " + "|".join(STATES) + "\n"
                      "증거 형식: " + EVIDENCE_HELP + "\n"
                      "--json 출력: list 는 배열(각 행에 blocks_count·blocked_by 추가), 나머지 명령은 객체. 오류도 {error, code, hint} 객체(stdout).\n"
                      "종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(증거 없는 완료·없는 id·순환 포함), 3 대상 없음·중복")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    def dry(sp):
        sp.add_argument("--dry-run", action="store_true", help="쓰지 않고 결과만 출력")

    def js(sp):
        sp.add_argument("--json", action="store_true", help="JSON 으로 출력(오류도 JSON)")

    def night_args(sp):
        sp.add_argument("--gate", help=f"야간 진행 구분 {'|'.join(GATES)}(set 에서 빈 문자열이면 지움)")
        sp.add_argument("--goal", help=f"한 줄 목표 {GOAL_MAX}자 이내(빈 문자열이면 지움)")
        sp.add_argument("--after", help="시작 전에 풀려야 할 것. 'pr:Owner/Repo#N,decision:D-ID,task:T0123' 쉼표 구분(set 에서 빈 문자열이면 지움)")
        sp.add_argument("--question", action="append", help="사전 질문 '질문|추천안|쉬움·보통·어려움' 반복 지정. set 에서는 목록 전체 교체, 빈 문자열 하나면 지움")
        sp.add_argument("--uses", help=f"자원 표시 {'|'.join(USES_VALUES)} 쉼표 구분(set 에서 빈 문자열이면 지움)")
        sp.add_argument("--night", action="store_true", help="야간 대상으로 표시(머리말 '" + NIGHT_HEADER + ":' 접수와 같음)")

    def extras(sp):
        sp.add_argument("--blocked-kind", choices=BLOCK_KINDS, metavar="|".join(BLOCK_KINDS), help="막힘 종류(막힘 상태에서만, 기본 기타)")
        sp.add_argument("--blocked-note", help="막힘 사유 한 줄(막힘 상태에서만)")
        sp.add_argument("--reason", help="버림 사유(버림 상태에서 필수)")
        sp.add_argument("--order", help="수동 순서 정수(작을수록 앞). set 에서 빈 문자열이면 지움")
        sp.add_argument("--outbox-id", help="Slack 전송 대기열 항목 id(선택). set 에서 빈 문자열이면 지움")

    a = sub.add_parser("add", help="새 일 추가", description="새 일 추가(기본 상태 미분류). 같은 source 는 거부(exit 3). --link 면 새 행 없이 기존 행에 출처만 추가.")
    a.add_argument("--text", help="원문(--link 가 아니면 필수)")
    a.add_argument("--link", metavar="T0xxx", help="새 행 대신 이 행의 sources 에 --source-kind/--source-ref 추가")
    a.add_argument("--hub", default="", help="담당 허브 slug(hubs.tsv) 또는 사용자·main")
    a.add_argument("--state", choices=STATES, metavar="|".join(STATES), help="상태(기본 미분류)")
    a.add_argument("--needs", help="전제조건 id, 쉼표로 구분")
    a.add_argument("--done-when", help="완료 기준")
    a.add_argument("--evidence", action="append", help=f"증거({EVIDENCE_HELP}). 여러 번 지정 가능")
    a.add_argument("--source-kind", choices=SRC_KINDS, default="manual", help="출처 종류")
    a.add_argument("--source-ref", default="", help="출처 식별값(ts·C0004 등). 같은 값은 한 번만 반입")
    extras(a)
    night_args(a)
    dry(a)
    js(a)
    s = sub.add_parser("set", help="상태·담당·전제조건·완료 기준·증거 변경", description="주어진 칸만 바꿈. 완료는 증거 필수(없으면 exit 2), 버림은 사유 필수, needs 는 있는 id 만·순환 금지.")
    s.add_argument("id", help="일 번호(예 T0003)")
    s.add_argument("--state", choices=STATES, metavar="|".join(STATES))
    s.add_argument("--hub", help="담당 허브 slug")
    s.add_argument("--needs", help="전제조건 id 전체를 이 값으로 교체. 쉼표 구분, 빈 문자열이면 비움")
    s.add_argument("--done-when", help="완료 기준")
    s.add_argument("--evidence", action="append", help=f"증거 추가({EVIDENCE_HELP}). 여러 번 지정 가능")
    s.add_argument("--note", help="상태 기록에 남길 짧은 말")
    extras(s)
    night_args(s)
    s.add_argument("--night-off", action="store_true", help="야간 대상 표시와 승인 기록을 지움")
    dry(s)
    js(s)
    an = sub.add_parser("approve-night", help="야간 진행 승인 기록", description="사용자가 결정 카드에서 승인한 야간 항목에 night.approved_at·approved_by_card 를 씀. 이미 승인된 행은 그대로(멱등). 야간 대상이 아니면 exit 2.")
    an.add_argument("id")
    an.add_argument("--card", required=True, help="승인한 결정 카드 번호(D-…)")
    dry(an)
    js(an)
    sh = sub.add_parser("show", help="한 건 자세히", description="한 건의 모든 칸과 상태 기록 출력. --json 은 객체(blocks_count·blocked_by 포함).")
    sh.add_argument("id")
    js(sh)
    ls = sub.add_parser("list", help="목록", description="목록. 필터는 AND. order 가 있는 행이 먼저, 나머지는 장부 순서. --json 은 배열.")
    ls.add_argument("--state", choices=STATES, metavar="|".join(STATES))
    ls.add_argument("--hub", help="담당 허브 slug(별칭도 slug 로 정규화)")
    ls.add_argument("--night", action="store_true", help="야간 대상만")
    ls.add_argument("--night-unapproved", action="store_true", help="야간 대상 중 미승인만(15시 초안 후보)")
    ls.add_argument("--gate", choices=GATES, metavar="|".join(GATES))
    js(ls)
    i = sub.add_parser("import-inbox", help="Slack inbox 사용자 글을 미분류로 반입",
                       description="slack-inbox.jsonl 의 note·thread_reply(결정 카드 답 포함, 문맥에 kind=decide 표시)를 미분류 행으로. 봇 즉답 명령·봇 중계 글·버튼·반응 제외. 스레드 답글은 부모 제목을 문맥으로 붙임. ts 로 멱등. 비슷한 문장은 dup_candidates 로 표시.")
    i.add_argument("--inbox", default=os.environ.get("LRM_INBOX") or os.path.join(HUB_DIR, "slack-inbox.jsonl"), help="inbox 경로(기본 LRM_INBOX 또는 작업 폴더)")
    i.add_argument("--routes", default=None, help="즉답 명령 표(기본 LRM_INTAKE_ROUTES 또는 작업 폴더 intake-routes.tsv)")
    dry(i)
    js(i)
    f = sub.add_parser("import-feedback", help="피드백 장부 행을 1회 반입",
                       description="feedback-ledger.tsv 행을 상태 대응(접수→미분류, 진행→진행, 완료→완료, 보류→막힘)으로 반입. 피드백 id 로 멱등. 증거 형식이 없는 완료는 진행으로 반입(demoted). 같은 PR 의 todo 자동 행은 출처만 합침.")
    f.add_argument("--feedback", default=os.environ.get("LRM_LEDGER") or ledger.DEFAULT_PATH, help="피드백 장부 경로(기본 LRM_LEDGER)")
    dry(f)
    js(f)
    t = sub.add_parser("import-todo", help="할일 html 을 행으로 반입",
                       description="할일 목록 html 의 div.c 를 행으로. me→배정·사용자, hub→진행, q→막힘(사용자답), done→완료(PR 번호마다 한 행, 증거 PR). 키는 정규화 문장 해시(파일명 제외), 완료 PR 은 pr:<번호> 전역 키.")
    t.add_argument("html", help="할일 html 경로")
    dry(t)
    js(t)
    return p


def emit(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2) if getattr(args, "json", False) else text)


def summarize(res: dict, dry_run: bool) -> str:
    lines = [("[dry-run] " if dry_run else "") + f"반입 {res['added']}건 · 이미 있음 {res['skipped']}건 · 출처 합침 {res['linked']}건 · 상태별 "
             + (", ".join(f"{k} {v}" for k, v in res["by_state"].items()) or "없음")
             + f" · 버림 후보 {res['discard_candidates']} · 질문 {res['questions']} · 증거 없어 진행으로 반입 {res['demoted']} · 중복 후보 {len(res['dup_candidates'])}"]
    lines += [f"  {s['id']} {s['state']} {s['text']}" + (f" ← {s['context']}" if s["context"] else "") for s in res["sample"]]
    lines += [f"  중복 후보 {c['ref']} ≈ {c['similar_to']}: {c['text']}  (합치려면 add --link {c['similar_to']} --source-kind … --source-ref {c['ref']})" for c in res["dup_candidates"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    global JSON_MODE
    JSON_MODE = "--json" in (sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    path, now = tasks_path(), time.time()
    try:
        if args.cmd == "add":
            if not args.text and not args.link:
                raise LedgerError("--text 필요 — 원문을 지정(기존 행에 출처만 붙이려면 --link)", EXIT_USAGE)
            row = add_row(path, args.text or "", now, args.dry_run, hub=args.hub, link=args.link or "", state=args.state, needs=args.needs,
                          done_when=args.done_when, evidence=args.evidence, source_kind=args.source_kind, source_ref=args.source_ref,
                          blocked_kind=args.blocked_kind or "", blocked_note=args.blocked_note or "", reason=args.reason or "",
                          order=args.order, outbox_id=args.outbox_id, gate=args.gate, goal=args.goal and ledger.clean(args.goal),
                          after=parse_after(args.after), questions=parse_questions(args.question), uses=split_ids(args.uses), night=args.night)
            emit(args, row, ("[dry-run] " if args.dry_run else "") + (f"출처 추가: {row['id']} ← {args.source_kind}/{args.source_ref}" if args.link else f"추가: {row['id']} {row['state']} · {row['text']}"))
        elif args.cmd == "set":
            row = set_row(path, args.id, now, args.dry_run, state=args.state, hub=args.hub, needs=args.needs, done_when=args.done_when,
                          evidence=args.evidence, note=args.note, blocked_kind=args.blocked_kind, blocked_note=args.blocked_note,
                          reason=args.reason, order=args.order, outbox_id=args.outbox_id, gate=args.gate, goal=args.goal,
                          after=None if args.after is None else parse_after(args.after),
                          questions=None if args.question is None else parse_questions([q for q in args.question if q.strip()]),
                          uses=None if args.uses is None else split_ids(args.uses), night=False if args.night_off else (True if args.night else None))
            emit(args, row, ("[dry-run] " if args.dry_run else "") + f"변경: {row['id']} → {row['state']} ({row['updated_at']})")
        elif args.cmd == "approve-night":
            row = approve_night(path, args.id, args.card, now, args.dry_run)
            emit(args, row, ("[dry-run] " if args.dry_run else "") + f"야간 승인: {row['id']} ({row['night']['approved_at']})")
        elif args.cmd == "show":
            rows = read_rows(path)
            row = next((r for r in rows if r["id"] == args.id), None)
            if row is None:
                raise LedgerError(f"id 없음: {args.id} — list 로 id 확인", EXIT_MISSING)
            emit(args, with_derived([row], rows)[0], view(row))
        elif args.cmd == "list":
            rows = read_rows(path)
            hub = norm_hub(args.hub, load_slugs()) if args.hub else ""
            picked = [r for r in rows if (not args.state or r["state"] == args.state) and (not hub or r.get("hub") == hub)
                      and (not (args.night or args.night_unapproved) or is_night(r)) and (not args.night_unapproved or not night_approved(r))
                      and (not args.gate or r.get("gate") == args.gate)]
            picked = [r for _, r in sorted(enumerate(picked), key=lambda p: (p[1].get("order", float("inf")), p[0]))]
            emit(args, with_derived(picked, rows), "\n".join(f"{EMOJI.get(r['state'], '·')} {r['id']}\t{r['state']}\t{r.get('hub') or '-'}\t{r['text'][:70]}" for r in picked))
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
        fail(str(err), err.code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
