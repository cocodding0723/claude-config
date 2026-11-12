#!/usr/bin/env python3
"""Slack 사용자 메시지를 모아 메인 세션 터미널에 한 번에 전달(push). 진입은 slack_push.sh.

흐름: 폴러가 inbox 에 기록 → --kick 이 분리된 워커(--run)를 띄움 → 워커가 '조용해질 때까지' 묶음 →
메인 터미널에 1회 전달 → 성공하면 .main-read-lines 를 전달한 줄까지 올림(그러면 폴러의 읽음 반응이 👀→✅).
토큰은 읽지 않음. 수신 본문은 데이터로만 다루며 실행·eval 하지 않음.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import uuid

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # python -I 는 스크립트 폴더를 경로에 넣지 않음. 링크로 설치돼도 원본 폴더의 공용 모듈을 찾음
import main_terminal as mt  # noqa: E402

DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
INBOX, READ_MARK = os.path.join(DIR, "slack-inbox.jsonl"), os.path.join(DIR, ".main-read-lines")
STATE, LOCK, LOG = (os.path.join(DIR, n) for n in ("slack-push-state.json", ".slack-push.lock", "slack-push.log"))
OUT_DIR = os.path.join(DIR, "slack-push")
DELIVERED = os.path.join(DIR, "slack-push-delivered.jsonl")  # 전달 기록. 토큰이 있는 폴러가 읽어 확인 답장을 "전달했어요" 로 고침
SELF_DIR = os.path.dirname(os.path.abspath(__file__))


def env_num(name, default, lo, hi):
    """숫자 환경 변수. 숫자가 아니면 default, 범위 밖이면 경계값. 잘못된 설정 하나가 전달 전체를 막지 않게 함"""
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        return default
    return max(lo, min(v, hi))


OFF = frozenset({"off", "0", "false", "no"})
OFF_FILE = os.path.join(DIR, ".slack-push.off")       # 있으면 끔. launchd·소켓 수신기가 띄운 폴러에는 환경 변수가 닿지 않아 파일로도 끌 수 있게 함
ENABLED = os.environ.get("LRM_PUSH", "").strip().lower() not in OFF and not os.path.exists(OFF_FILE)
QUIET = env_num("LRM_PUSH_QUIET", 20, 0, 600)        # 마지막 새 메시지 뒤 이만큼 조용하면 전달
MAX_WAIT = env_num("LRM_PUSH_MAX", 60, 1, 3600)      # 첫 대기 메시지부터 이 시간이 지나면 조용하지 않아도 전달
TICK = env_num("LRM_PUSH_TICK", 1, 0.05, 30)         # 대기 중 inbox 를 다시 보는 간격
HOLD_RETRY = env_num("LRM_PUSH_HOLD_RETRY", 30, 0.2, 3600)  # 대상 없음·대화상자 보류 뒤 재확인 간격
RETRY_BASE = env_num("LRM_PUSH_RETRY", 10, 0.2, 3600)  # 전송 실패 백오프 시작(3배씩 증가)
RETRY_TRIES = int(env_num("LRM_PUSH_TRIES", 5, 1, 20))  # 이 횟수를 넘으면 HOLD_GAP 간격으로만 재시도
HOLD_GAP = env_num("LRM_PUSH_HOLD", 600, 0, 86400)
WAIT_SUBMIT = int(env_num("LRM_PUSH_WAIT", 10, 1, 120))  # 전송 뒤 제출·턴 시작을 관찰하는 시간(초). terminal send --wait-submit
SEND_TIMEOUT = env_num("LRM_PUSH_SEND_TIMEOUT", WAIT_SUBMIT + 25, 1, 600)  # orca 프로세스 제한. Orca 는 --wait-submit 에 10초를 더 기다림
# 접수 뒤 화면 재확인 시간(초). 0·off 면 끄고 turn_started 필수. Orca 가 Claude 의 턴 시작을 못 잡는 환경(2026-11-10 실측)의 보강 확인
CONFIRM = 0 if os.environ.get("LRM_PUSH_CONFIRM", "").strip().lower() in OFF else env_num("LRM_PUSH_CONFIRM", 20, 1, 120)
CONFIRM_GAP = env_num("LRM_PUSH_TEST_CONFIRM_GAP", 1, 0.05, 1)  # 연속 두 판정 사이 최소 간격(초). 렌더 지연 방어라 운영에선 1 고정, 시험만 낮춤
CONFIRM_TICK = env_num("LRM_PUSH_CONFIRM_TICK", 2, CONFIRM_GAP, 30)  # 화면 재확인 간격
CONFIRM_STREAK = 2  # 제출 판정이 연속 이 횟수일 때만 확정. 붙여 넣기가 그려지기 전 순간의 빈 입력 칸을 제출로 오판하지 않게 함
IDLE_CHECK = "relaxed" if os.environ.get("LRM_PUSH_IDLE_CHECK", "").strip().lower() == "relaxed" else "strict"  # 그 밖의 값은 strict
FOREVER = 10 ** 9                                     # 자동 재시도 없는 보류의 다음 시각 간격(초). 사람이 확인해야 풀림
LIFE = env_num("LRM_PUSH_LIFE", MAX_WAIT + 60, 1, 7200)  # 워커 최대 수명(초). 더 필요하면 다음 --kick 이 새로 띄움
SEND_CAP = 3                                          # 워커 1회당 전달 상한(폭주 방지)
BATCH_ITEMS, MSG_CHARS, BATCH_CHARS = 20, 400, 3000
FIRST_MAX = 20                                        # 첫 실행에서 안 읽은 줄이 이보다 많으면 과거분은 보내지 않음
KEEP_DAYS, LOCK_STALE = 30, 900
MAIN_ENV = os.environ.get("LRM_MAIN_ENV") or os.path.join(DIR, "main.env")  # 메인 터미널 핸들(MAIN_TERMINAL=…) 설정. 메인 재시작 때 set_main_terminal.sh 가 갱신
HANDLE_LOG = 13                                       # 로그에 남기는 핸들 앞 글자 수
SPEC = mt.Spec.from_env()                             # 메인 판정 기준(메인 체크아웃 LRM_PUSH_MAIN·claude·제목 보조 조건 LRM_PUSH_PANE). 판정·재탐색은 main_terminal 이 맡음
ORCA = os.environ.get("LRM_PUSH_ORCA", "orca")
NOTIFY = os.environ.get("LRM_PUSH_NOTIFY") or os.path.join(SELF_DIR, "slack_notify.sh")
ALERT = os.path.join(DIR, ".slack-push-alert.json")  # '메인 연결 끊김' 알림을 보낸 사유 목록. 연결이 확인되면 지움
# 메인 핸들이 없거나 다른 터미널을 가리키는 사유. 사람이 main.env 를 고쳐야 풀리므로 알림 대상(화면 상태·조회 실패는 제외)
LINK_LOST = frozenset({"no_main_handle", "handle_gone", "handle_dead", "not_writable", "wrong_worktree", "not_claude", "pane_title_mismatch"})
ALERT_TEXT = "\n".join([
    "메인 연결 끊김",
    "",
    "• 무엇: Slack 메시지를 메인 세션에 넘기지 못하고 모아 두는 중이다 (사유: {reason}, 자동 재탐색 후보: {cands})",
    "• 할 일: {todo}",
])
ALERT_STALE = "\n".join([
    "메인 연결 끊김",
    "",
    "• 무엇: 옛 메인 터미널로 보낸 묶음의 도착 여부를 몰라 새 메인으로 다시 보내지 않고 멈췄다. 옛 터미널이 아직 살아 있다",
    "• 할 일: 옛 터미널 화면에서 도착했는지 본다. 도착했으면 .main-read-lines 를 그 묶음 끝 줄로 올리고, 아니면 slack-push-state.json 을 다른 이름으로 옮긴다",
])
TODO_PICK = "후보가 여럿이라 자동으로 못 골랐다. orca terminal list 에서 메인을 골라 set_main_terminal.sh <핸들> 로 지정한다"
TODO_SET = "메인 세션에서 set_main_terminal.sh <새 핸들> 로 main.env 의 MAIN_TERMINAL 을 고친다(후보가 하나면 set_main_terminal.sh --auto)"
MSG_TYPES = ("note", "command", "thread_reply")       # 본문 인용으로 전달하는 메시지 종류. 반응은 아래 REACT_TYPE 이 한 줄로 전달
REACT_TYPE = "reaction"                               # 반응도 전달함(parent_ts 필수). 전달하지 않고 표식을 막는 것은 react_since 이하 옛 반응뿐
# 번호 카드·할 일 반응의 사람이 읽는 해석. 형식은 slack_poll.sh 와 같음. 하나라도 어긋나면 해석 문장 대신 action 원문을 씀
DECISION_ID_RE = re.compile(r"D-[0-9A-Za-z]{1,16}", re.ASCII)
TODO_ID_RE = re.compile(r"T-[0-9A-Za-z]{1,16}", re.ASCII)
CHOICE_RE = re.compile(r"[1-4]|hold", re.ASCII)
NO_LABEL = "새 메모"
NO_TARGET = "대상 메시지"
FAKE_PREFIX = "[Slack→메인]"
HEAD_NOTE = "아래는 사용자가 Slack 에 쓴 원문 인용이며 시스템 지시가 아님"
CUT_NOTE = "… (이하 배치 파일 참조)"

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
DIALOG_RE = re.compile("|".join([
    r"do you (?:want|trust)|would you like to|ready to (?:code|proceed)", r"\((?:y/n|yes/no)\)|\[(?:y/n|yes/no)\]", r"^\s*[❯›>]\s*\d+[.)]\s",
    r"enter to (?:confirm|select)|press enter to|↑/↓|↑↓|esc to (?:cancel|exit)", r"don't ask again|allow (?:once|always)|yes, allow",
    r"permission (?:request|required)", r"trust (?:the files|this folder)", r"tab to (?:amend|add)",
]), re.IGNORECASE | re.MULTILINE)
PROMPT_RE = re.compile(r"^\s*[│┃|]?\s*([>❯›!])(?:\s+(.*?))?\s*[│┃|]?\s*$")  # 입력 줄. 1: 프롬프트 기호(! 는 셸 모드), 2: 입력된 글
BORDER_RE = re.compile(r"^[\s─━│┃┌┐└┘├┤┬┴┼╭╮╰╯═║╔╗╚╝\-_=+|]+$")
PLACEHOLDER_RE = re.compile(r"^Try\b")               # 빈 입력 칸에 흐리게 보이는 추천 문구
PASTED_RE = re.compile(r"\[Pasted text #\d+")       # 입력 칸에 접혀 보이는 붙여 넣은 여러 줄 글
SCREEN_TAIL, PROMPT_ZONE, FOOTER_MAX, FOOTER_CHARS = 8, 5, 3, 120  # 대화상자 검사 줄 수, 입력 칸을 찾는 끝 줄 수, 입력 칸 아래 허용 줄 수·줄 길이
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
RETRY_KEYS = ("orchestrationRequestId",)              # 모호한 실패 때 Orca 가 error.data 에 담아 알리는 요청 ID(재시도 때 같은 값이어야 함)
CODE_RE = re.compile(r"[^a-z0-9_]")


def log(event, **kv):
    """사유 코드·숫자만 한 줄 기록. 메시지 본문·URL·ID 는 넣지 않음"""
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1 << 20:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} push {event} " + " ".join(f"{k}={v}" for k, v in kv.items()) + "\n")
    except OSError:
        pass


# ---- 파일 입출력 ------------------------------------------------------------------------------
def read_inbox():
    """inbox 의 모든 물리 줄을 (dict|None) 목록으로. 줄 번호 = 인덱스 + 1(폴러의 .main-read-lines 와 같은 기준)"""
    try:
        with open(INBOX, encoding="utf-8") as f:
            out = []
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    d = None
                out.append(d if isinstance(d, dict) else None)
            return out
    except OSError:
        return []


def read_mark():
    try:
        with open(READ_MARK, encoding="utf-8") as f:
            return max(0, int(f.read().strip()))
    except (OSError, ValueError):
        return 0


def write_mark(n):
    """max(기존, n) 로만 갱신. 메인이 먼저 더 앞선 값으로 넘겼으면 건드리지 않음"""
    if n <= read_mark():
        return False
    tmp = READ_MARK + ".push-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{n}\n")
    os.replace(tmp, READ_MARK)
    return True


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
        if isinstance(s, dict) and isinstance(s.get("pushed_lines"), int):
            return s
    except (OSError, ValueError):
        pass
    return None


def save_state(s):
    tmp = STATE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def handle_tag(h):
    """상태 파일에 핸들 원문 대신 남기는 식별값(SHA-256 앞 16자). 같은 터미널인지 비교하는 데만 씀"""
    return "sha256:" + hashlib.sha256(h.encode()).hexdigest()[:16]


def last_delivered_line():
    """전달 기록의 마지막 전달 줄(id 가 '{마지막 줄}-{시각}'). 기록이 없거나 읽을 수 없으면 0"""
    best = 0
    try:
        with open(DELIVERED, encoding="utf-8") as f:
            for ln in f:
                try:
                    best = max(best, int(str(json.loads(ln).get("id")).split("-")[0]))
                except (ValueError, TypeError, AttributeError):
                    pass
    except OSError:
        pass
    return best


def new_state(lines, mark):
    """상태가 없거나 손상됐을 때의 기준선 = max(읽음 표식, 전달 기록의 마지막 전달 줄). 그 뒤가 적으면 그대로 보내고, 많으면 과거분은 건너뜀.
    표식만으로는 부족함: 옛 반응 줄이 표식을 막으면 표식이 전달분보다 뒤처져 있어 상태를 잃을 때 이미 전달한 줄이 재전달됨.
    기준선 이하 반응은 전달 여부를 알 수 없어 옛 반응으로 둠(react_since)"""
    start = min(max(mark, last_delivered_line()), len(lines))
    base = start if len(lines) - start <= FIRST_MAX else len(lines)
    return {"pushed_lines": base, "skip_to": base if base > start else 0, "pending_since": None, "last_new_ts": None, "push_pending": None,
            "react_since": start}


def react_floor(state):
    """이 줄 번호 이하의 반응은 전달하지 않은 옛 반응(반응 전달 전 버전의 기록). 키가 없는 옛 상태는 전달 완료·보류 묶음 끝 줄까지를 옛 반응으로 봄.
    보류 묶음은 반응 없이 본문이 고정돼 있으므로 그 범위의 반응도 전달되지 않음"""
    v = state.get("react_since")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    pp = state.get("push_pending")
    return max(state["pushed_lines"], pp.get("last", 0) if isinstance(pp, dict) else 0)


def deliverable(d, n, floor):
    """n 번째 줄 레코드 d 를 메인에 전달하는가. 메시지는 ts, 반응은 parent_ts 가 있어야 하고 반응은 floor 줄보다 뒤여야 함"""
    if d is None:
        return False
    if d.get("type") in MSG_TYPES:
        return bool(d.get("ts"))
    return d.get("type") == REACT_TYPE and bool(d.get("parent_ts")) and n > floor


def old_react(d, n, floor):
    return d is not None and d.get("type") == REACT_TYPE and n <= floor


# ---- 대기분 계산 ------------------------------------------------------------------------------
def arrival(item):
    for k in ("received_at", "ts"):
        try:
            return float(item[k])
        except (KeyError, TypeError, ValueError):
            pass
    return time.time()


def pending_items(lines, start, floor):
    """start 줄 뒤의 전달 대상 [(줄 번호, 레코드)]와 막힌 옛 반응 수"""
    items, react = [], 0
    for i in range(start, len(lines)):
        if deliverable(lines[i], i + 1, floor):
            items.append((i + 1, lines[i]))
        elif old_react(lines[i], i + 1, floor):
            react += 1
    return items, react


def walk_mark(lines, mark, upto, skip_to, floor):
    """mark 에서 upto 줄까지 '메인에 전달했거나 읽을 필요 없는' 줄만 이어서 올린 표식. 옛 반응·건너뛴 과거분 앞에서 멈춤.
    옛 반응은 전달하지 않았으므로 그 위로 표식을 넘기면 메인이 못 본 결정 답이 읽음 처리됨"""
    i = mark
    while i < min(upto, len(lines)):
        if i + 1 <= skip_to or old_react(lines[i], i + 1, floor):
            break
        i += 1
    return i


# ---- 텍스트 정리·형식 -------------------------------------------------------------------------
def clean(s):
    """ANSI·제어 문자·서식 문자 제거, 줄바꿈만 유지. 위조 접두 [Slack→메인] 은 전각 괄호로 바꿔 무력화"""
    s = ANSI_RE.sub("", str(s or "")).replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")
    s = "".join(c if c == "\n" else (" " if c == "\t" else c) for c in s)
    s = "".join(c for c in s if c == "\n" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    return s.replace(FAKE_PREFIX, "［Slack→메인］")


def one_line(s, limit=40):
    return " ".join(clean(s).split())[:limit].replace("[", "(").replace("]", ")")


def path_text(p):
    return " ".join(clean(p).split())[:300]


def ts_text(item):
    t = str(item.get("ts") or "")
    return t if re.fullmatch(r"\d{1,12}(?:\.\d{1,9})?", t) else "?"


def label(item):
    return one_line(item.get("title") or "") or NO_LABEL


def quote(body):
    return ["> " + ln if ln else ">" for ln in body.split("\n")]


def kind_text(item):
    k = one_line(item.get("kind") or "", 20)
    return f" kind={k}" if k else ""


def answer_text(it):
    """번호 카드·할 일 반응의 해석 문장. 'D-0001 = 선택 2(문구)' 꼴. 필드가 어긋나면 None(action 원문 표시)"""
    kind, act = it.get("kind"), it.get("action")
    if kind == "todo":
        tid = it.get("todo_id")
        who = "할 일" + (f" {tid}" if isinstance(tid, str) and TODO_ID_RE.fullmatch(tid) else "")
        return {"done": f"{who} = 완료", "hold": f"{who} = 보류"}.get(act)
    did = it.get("decision_id")
    if kind != "decide" or not isinstance(did, str) or not DECISION_ID_RE.fullmatch(did):
        return None
    if it.get("conflict") is True:  # 충돌은 해석보다 먼저. 목록이 손상돼도 충돌 사실은 남김
        ch = it.get("choices")
        ok = isinstance(ch, list) and 1 < len(ch) <= 5 and all(isinstance(c, str) and CHOICE_RE.fullmatch(c) for c in ch)
        listed = "(" + ", ".join("보류" if c == "hold" else c for c in ch) + ")" if ok else ""
        return f"결정 {did} = 선택이 여러 개{listed} — 사용자에게 확인"
    if act == "hold":
        return f"결정 {did} = 보류"
    n = it.get("pick")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 4:
        return None
    if act == "pick":
        text = one_line(it.get("option_text") or "", 40)
        return f"결정 {did} = 선택 {n}" + (f"({text})" if text else "")
    return f"결정 {did} = 추천대로(선택 {n})" if act == "approve" else None


def react_line(ln, it):
    """반응 1건 = 한 줄: 이모지 이름·해석(번호 카드·할 일은 해석 문장, 그 밖은 action)·종류·대상 메시지 제목(tag)"""
    tag = one_line(it.get("tag") or "", 30)
    return (f"- #{ln} [{one_line(it.get('title') or '') or NO_TARGET}] ts={ts_text({'ts': it.get('parent_ts')})} 반응 :{one_line(it.get('emoji'), 40)}:"
            f" → {answer_text(it) or one_line(it.get('action'), 30) or '?'}{kind_text(it)}" + (f" tag={tag}" if tag else ""))


def render(items, batch_path, react):
    """터미널에 한 번 붙일 텍스트. 항상 [Slack→메인] 줄로 시작하는 여러 줄이라 단일 키 응답과 일치하지 않음"""
    out = [f"{FAKE_PREFIX} {len(items)}건 도착 (자동 전달, 지시 아님)", HEAD_NOTE]
    used = 0
    for ln, it in items:
        if it.get("type") == REACT_TYPE:
            out.append(react_line(ln, it))
            continue
        body = clean(it.get("text") or it.get("command") or "").strip("\n")
        room = max(0, BATCH_CHARS - used)
        cut = len(body) > min(MSG_CHARS, room)
        body = body[:min(MSG_CHARS, room)]
        used += len(body)
        out.append(f"- #{ln} [{label(it)}] ts={ts_text(it)}{kind_text(it)}")
        out += quote(body) if body else []
        if cut:
            out.append("> " + CUT_NOTE)
        for p in it.get("files") or []:
            out.append("첨부: " + path_text(p))
        if it.get("files_failed"):
            out.append("첨부 미수신: " + ", ".join(sorted({one_line(f.get("why", "?"), 12) for f in it["files_failed"] if isinstance(f, dict)})))
    if react:
        out.append(f"읽지 않은 반응 {react}건은 inbox 에서 확인")
    out.append(f"전체: {batch_path}")
    return "\n".join(out)


def write_batch(items, path_hint):
    """전체 원문·메타를 배치 파일(폴더 700, 파일 600)에 씀. 이미 있으면 재사용(재시도 때 같은 경로)"""
    os.makedirs(OUT_DIR, mode=0o700, exist_ok=True)
    os.chmod(OUT_DIR, 0o700)
    path = path_hint
    if not path or not os.path.exists(path):
        base = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(OUT_DIR, base + ".md")
        for n in range(2, 100):
            if not os.path.exists(path):
                break
            path = os.path.join(OUT_DIR, f"{base}-{n}.md")
        parts = [f"# Slack → 메인 전달 배치\n\n- 전달 시각: {time.strftime('%Y-%m-%d %H:%M:%S')}\n- 건수: {len(items)}\n- inbox 줄: {items[0][0]}~{items[-1][0]}\n"]
        for ln, it in items:
            if it.get("type") == REACT_TYPE:
                parts.append(f"\n## {react_line(ln, it)[2:]}\n\n- 유형: {REACT_TYPE}\n")
                continue
            parts.append(f"\n## #{ln} [{label(it)}] ts={ts_text(it)}{kind_text(it)}\n\n- 유형: {one_line(it.get('type'), 20)}\n")
            parts.append("\n".join(quote(clean(it.get("text") or it.get("command") or "").strip("\n"))) + "\n")
            parts += [f"- 첨부: {path_text(p)}\n" for p in it.get("files") or []]
            parts += [f"- 첨부 미수신: {one_line(f.get('why', '?'), 12)}\n" for f in it.get("files_failed") or [] if isinstance(f, dict)]
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(parts))
    return path


def last_seq(rows):
    """기록 줄 목록의 마지막 seq. 읽을 수 있는 줄이 없으면 0"""
    for ln in reversed(rows[-5:]):
        try:
            return max(0, int(json.loads(ln).get("seq") or 0))
        except (ValueError, TypeError, AttributeError):
            pass
    return 0


def record_delivery(items, last):
    """전달한 묶음의 스레드별 건수를 폴러가 읽도록 한 줄 기록. 스레드 = 답글이면 부모 ts, 최상위면 자기 ts(확인 답장이 달린 곳).
    seq 는 단조 증가 번호(밀리초 시각, 직전 seq 보다 작아지지 않음). 폴러는 이 값을 '처리한 위치'로 저장하고 그보다 큰 기록만 처리하므로
    기록이 1000줄을 넘어 최근 500줄로 잘려도 옛 기록을 다시 처리하지 않음"""
    roots = {}
    for _, it in items:
        if it.get("type") == REACT_TYPE:  # 확인 답장은 메시지 스레드에만 있음. 반응을 세면 '전달했어요 (n건)' 이 부풀음
            continue
        r = str(it.get("parent_ts") or it.get("ts"))
        roots[r] = roots.get(r, 0) + 1
    try:
        try:
            with open(DELIVERED, encoding="utf-8") as f:
                rows = f.readlines()
        except OSError:
            rows = []
        seq = max(int(time.time() * 1000), last_seq(rows) + 1)
        rows.append(json.dumps({"seq": seq, "id": f"{last}-{int(time.time())}", "roots": roots}, ensure_ascii=False) + "\n")
        keep = rows[-500:] if len(rows) > 1000 else None
        if keep is None:
            with open(DELIVERED, "a", encoding="utf-8") as f:
                f.write(rows[-1])
        else:
            with open(DELIVERED + ".tmp", "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(DELIVERED + ".tmp", DELIVERED)
    except OSError:
        pass


def prune_batches():
    """KEEP_DAYS 일 지난 배치 파일 삭제"""
    try:
        for n in os.listdir(OUT_DIR):
            p = os.path.join(OUT_DIR, n)
            if n.endswith(".md") and os.path.isfile(p) and time.time() - os.path.getmtime(p) > KEEP_DAYS * 86400:
                os.remove(p)
    except OSError:
        pass


# ---- 대상 터미널·전송 -------------------------------------------------------------------------
def run_orca(args, timeout):
    """(returncode, 파싱한 JSON|None). 시간 초과는 (None, None), 실행 불가는 (127, None)"""
    return mt.run_orca(ORCA, args, timeout)


def screen_state(preview):
    """화면 끝(preview 는 Orca 가 주는 끝 300자 안팎)으로 본 입력 상태: 'empty' | 'dialog' | 'draft' | 'idle' | 'unknown'.
    붙여 넣기 뒤의 Enter 는 메뉴의 강조된 선택지(보통 승인)를 확정하고, 입력 칸에 쓰다 만 글이 있으면 그 뒤에 붙어 제출되므로
    (/명령·셸 모드 !) 'idle'(빈 입력 줄이 끝에 보임)일 때만 안전함.
    입력 칸 = 끝 PROMPT_ZONE 줄 안의 마지막 프롬프트 줄로, 그 아래에는 테두리·짧은 상태줄만 FOOTER_MAX 줄까지 허용(위쪽의 지난 대화 인용 줄과 구분)"""
    lines = [ln for ln in ANSI_RE.sub("", str(preview or "")).replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return "empty"
    if DIALOG_RE.search("\n".join(lines[-SCREEN_TAIL:])):
        return "dialog"
    for i in range(len(lines) - 1, max(-1, len(lines) - 1 - PROMPT_ZONE), -1):
        m = PROMPT_RE.match(lines[i])
        if not m:
            continue
        below = lines[i + 1:]
        if len(below) > FOOTER_MAX or not all(BORDER_RE.match(b) or len(b.strip()) <= FOOTER_CHARS for b in below):
            return "unknown"
        typed = (m.group(2) or "").strip()
        return "draft" if m.group(1) == "!" or (typed and not PLACEHOLDER_RE.match(typed)) else "idle"
    return "unknown"


SCREEN_HOLD = {"empty": "empty_preview", "dialog": "dialog", "draft": "draft", "unknown": "no_idle_marker"}


def screen_hold(preview):
    """전달을 보류할 사유 코드|None. strict(기본)는 'idle' 만 통과. relaxed 는 대화상자·쓰던 글·빈 화면만 보류하고 판별 불가는 통과"""
    st = screen_state(preview)
    if st == "idle" or (st == "unknown" and IDLE_CHECK == "relaxed"):
        return None
    return SCREEN_HOLD[st]


def box_state(preview):
    """접수 뒤 화면 끝으로 본 입력 칸: ('submitted'|'draft'|'unknown', 표식 종류).
    접수 전 입력 칸이 비어 있었으므로(strict 판별 통과) 칸에 보이는 글은 우리 글로 봄. 양성 근거(빈 입력 칸)만 제출로 보고 칸을 못 찾으면 판별 불가"""
    st = screen_state(preview)
    lines = [ln for ln in ANSI_RE.sub("", str(preview or "")).replace("\r", "\n").split("\n") if ln.strip()]
    if st == "draft":
        return "draft", "pasted" if any(PASTED_RE.search(ln) for ln in lines[-PROMPT_ZONE:]) else "typed"
    if st == "idle":
        return "submitted", "idle"
    if st in ("empty", "dialog"):
        return "unknown", st
    # 입력 칸을 못 찾으면 판별 불가. 작업 중 표식(esc to interrupt)은 제출 증거가 아니므로 보지 않음(작업 중에도 입력 칸은 보임)
    return "unknown", "no_box"


def main_handle():
    """지정된 메인 핸들|None. LRM_PUSH_HANDLE(시험용) 또는 main.env 의 MAIN_TERMINAL 줄만 읽음(다른 줄은 보지도 기록하지도 않음).
    형식이 term_<16진·하이픈> 이 아니거나 서로 다른 값이 둘 이상이면 무효"""
    v = os.environ.get("LRM_PUSH_HANDLE")
    if v is None:
        return mt.read_handle(MAIN_ENV)
    return v if mt.HANDLE_RE.fullmatch(v) else None


def target_why(t):
    """show 로 받은 터미널이 메인 세션이 아니면 사유 코드(판정은 main_terminal.target_why)"""
    return mt.target_why(t, SPEC)


def find_target():
    """(handle|None, 사유 코드|None, 로그용 핸들 앞부분, 재탐색 후보 수|None). 매번 새로 확인. main.env 가 가리키는 터미널이 살아 있고 메인 체크아웃의
    claude 세션이며 입력 대기 상태일 때만 대상. 제목으로 후보를 고르지 않음(제목이 사라지면 다른 터미널이 대상이 되는 일을 막음).
    사람이 고쳐야 풀리는 사유면 relink 가 제목 후보를 재탐색해 main.env 를 갱신한 뒤 다시 확인함(후보가 정확히 1개일 때만)"""
    return relink(*probe())


def probe():
    """main.env 핸들 1회 확인: (handle|None, 사유 코드|None, 로그용 핸들 앞부분)"""
    handle = main_handle()
    if not handle:
        return None, "no_main_handle", "none"
    short = handle[:HANDLE_LOG]
    t, why = mt.check_link(run_orca, SPEC, handle)
    why = why or screen_hold(t.get("preview"))
    return (None, why, short) if why else (handle, None, short)


def relink(handle, why, short):
    """probe 결과가 REDISCOVER 사유면 재탐색해 main.env 를 갱신하고 다시 probe. 반환: (handle, 사유, 앞부분, 후보 수|None).
    후보 수는 재탐색을 했을 때만(알림 문구용). 조회 실패·LRM_PUSH_HANDLE 고정(시험)이면 재탐색하지 않음"""
    if why not in mt.REDISCOVER or os.environ.get("LRM_PUSH_HANDLE") is not None:
        return handle, why, short, None
    found = mt.rediscover(run_orca, SPEC, MAIN_ENV)
    if found.status != "updated":
        log("relink", result=found.status, cands=found.count, was=why)
        return handle, why, short, found.count if found.status in ("none", "many") else None
    log("relink", note="재탐색으로_갱신", handle=found.handle[:8], was=why)
    h2, why2, short2 = probe()
    return h2, why2, short2, 1


def screen_check(handle):
    """접수 뒤 화면 1회 관찰: (판정, 표식 종류). 판정 'submitted' | 'draft' | 'unknown' | 'changed'(보낸 터미널이 아님). 읽기만 함"""
    if main_handle() != handle:
        return "changed", "main_env"
    rc, d = run_orca(["terminal", "show", "--terminal", handle, "--json"], 20)
    if rc is None or rc == 127:
        return "unknown", "show_fail"
    t = ((d or {}).get("result") or {}).get("terminal") if isinstance(d, dict) else None
    if rc != 0 or not isinstance(t, dict):
        err = d.get("error") if isinstance(d, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        return ("changed", "handle_gone") if code in mt.HANDLE_GONE_CODES else ("unknown", "show_fail")
    if t.get("handle") not in (None, handle):
        return "changed", "handle_gone"
    why = target_why(t)
    if why == "not_writable":  # 같은 터미널의 일시 상태. 다른 터미널이라는 근거도 제출 근거도 아니므로 판별 불가로만 처리
        return "unknown", why
    if why:
        return "changed", why
    pv = t.get("preview")
    if not isinstance(pv, str):
        return "unknown", "no_preview"
    return box_state(pv)


def confirm_screen(handle):
    """turn_started 없는 접수 영수증의 보강 확인: (판정, 사유 코드, 로그 정보). 판정 'sent' | 'unsubmitted' | 'unknown'.
    CONFIRM 초 동안 CONFIRM_TICK 마다 화면을 다시 읽어 제출 판정이 연속 CONFIRM_STREAK 회면 전달 완료.
    재전송하지 않음(입력 칸에 글이 남아 있으면 중복됨). 확정 전에는 최소 CONFIRM_STREAK 회 읽음"""
    end = time.time() + CONFIRM
    reads = streak = 0
    first_ok = 0.0
    last = ("unknown", "none")
    while True:
        res, kind = screen_check(handle)
        reads += 1
        if res == "changed":
            return "unknown", "target_changed", {"confirm": "changed", "screen": kind, "reads": reads}
        if res == "submitted":
            streak += 1
            if streak == 1:
                first_ok = time.time()
            if streak >= CONFIRM_STREAK and time.time() - first_ok >= CONFIRM_GAP:
                return "sent", None, {"confirm": "screen", "screen": kind, "reads": reads}
        else:
            streak, last = 0, (res, kind)
        if reads >= CONFIRM_STREAK and time.time() + CONFIRM_TICK > end:
            break
        time.sleep(CONFIRM_TICK)
    return "unsubmitted", "unsubmitted", {"confirm": "still_draft" if last[0] == "draft" else "unknown", "screen": last[1], "reads": reads}


def alert_state():
    try:
        with open(ALERT, encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) and isinstance(s.get("reasons"), list) else {"reasons": []}
    except (OSError, ValueError):
        return None


def alert_save(s):
    tmp = ALERT + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ALERT)


def alert(reason, cands=None, text=None):
    """'메인 연결 끊김' 경고를 사유마다 한 번만 보냄. 보낸 사유는 상태 파일(600)에 남겨 연결이 확인될 때까지 다시 보내지 않음.
    알림 실패는 전달·수집을 막지 않음(로그만). 본문에 핸들 값·메시지 내용은 넣지 않음.
    cands = 재탐색이 센 후보 수(None 이면 재탐색 안 함·실패). 2개 이상이면 사람이 고르라고 안내함"""
    s = alert_state() or {"reasons": []}
    if reason in s["reasons"]:
        return
    s["reasons"].append(reason)
    s["at"] = int(time.time())
    try:
        alert_save(s)  # 보내기 전에 기록. 알림이 실패해도 30초마다 다시 보내 Slack 을 채우지 않게 함
    except OSError:
        log("alert", reason=reason, result="state_fail")
        return
    try:
        p = subprocess.run(["/bin/bash", NOTIFY, "--kind", "warn", "--tag", "slack-push", "--title", "메인 연결 끊김", text or ALERT_TEXT.format(reason=reason, cands="미확인" if cands is None else f"{cands}개", todo=TODO_PICK if cands and cands > 1 else TODO_SET)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        result = "ok" if p.returncode == 0 else f"fail_rc{p.returncode}"
    except subprocess.TimeoutExpired:
        result = "timeout"
    except OSError:
        result = "fail"
    log("alert", reason=reason, result=result)


def alert_clear():
    """메인 연결이 확인되면 알림 상태를 지워 다음 끊김 때 다시 알림"""
    if alert_state() is None:
        return
    try:
        os.remove(ALERT)
        log("link_ok")
    except OSError:
        pass


def find_key(d, keys):
    if isinstance(d, dict):
        for k, v in d.items():
            if k in keys and v is not None:
                return v
        for v in d.values():
            r = find_key(v, keys)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = find_key(v, keys)
            if r is not None:
                return r
    return None


def code_of(s):
    return CODE_RE.sub("", str(s).lower())[:40]


def classify(rc, d, request_id):
    """terminal send 응답을 (판정, 사유 코드, 영수증 정보)로. 판정:
    sent        제출·턴 시작까지 확인(stages 에 turn_started)
    unsubmitted 입력은 받았으나 턴 시작 미확인. 재전송하면 입력 칸에 이미 있는 글이 중복됨 → attempt 가 화면으로 보강 확인, 안 되면 보류
    unknown     도착 여부를 알 수 없음(deliveryOutcome unknown·retrySafe false·ID 불일치·영수증 모양 불명) → 자동 재전송 금지, 보류
    blocked     Orca 가 권한·신뢰 메뉴로 거절(입력 전)
    refused     입력을 받지 않음(accepted=false)
    retry       모호한 실패·시간 초과·일반 오류. 같은 요청 ID 로 다시 보내면 Orca 가 영수증만 돌려주고 다시 입력하지 않음"""
    if rc is None:
        return "retry", "send_timeout", {}
    if rc == 127:
        return "retry", "orca_missing", {}
    err = d.get("error") if isinstance(d, dict) else None
    if isinstance(err, dict) or (isinstance(d, dict) and d.get("ok") is False) or (d is None and rc != 0):
        err = err if isinstance(err, dict) else {}
        data = err.get("data") if isinstance(err.get("data"), dict) else {}
        code = code_of(err.get("code")) if isinstance(err.get("code"), str) and err.get("code") else "send_error"
        reported = find_key(d, RETRY_KEYS)
        if data.get("deliveryOutcome") == "unknown" or data.get("retrySafe") is False or (reported is not None and str(reported) != request_id):
            return "unknown", code or "send_error", {}
        if code == "agent_prompt_blocked":
            return "blocked", code, {}
        if code == "incompatible_runtime":  # 재전송 근거(영수증)를 남기지 못하는 호스트. 도착 여부 불명
            return "unknown", code, {}
        return "retry", code or "send_error", {}
    send_r = ((d or {}).get("result") or {}).get("send") if isinstance(d, dict) else None
    if not isinstance(send_r, dict) or not isinstance(send_r.get("accepted"), bool):
        return "unknown", "bad_receipt", {}
    if not send_r["accepted"]:
        return "refused", "not_accepted", {}
    prompt = send_r.get("prompt") if isinstance(send_r.get("prompt"), dict) else {}
    stages = [code_of(x) for x in (prompt.get("stages") if isinstance(prompt.get("stages"), list) else []) if isinstance(x, str)]
    info = {"stages": ",".join(stages) or "none", "obs": code_of(prompt.get("observation") or "none") or "none"}
    return ("sent", None, info) if "turn_started" in stages else ("unsubmitted", "unsubmitted", info)


def send(handle, text, request_id):
    """(판정, 사유 코드, 영수증 정보). request_id 는 묶음을 확정할 때 만든 UUID 로 모든 시도에 같음.
    Orca 는 요청 ID 에 본문·터미널 프로세스를 묶어 기억하므로, 응답을 못 받은 시도의 재전송이 중복 입력이 되지 않음"""
    args = ["terminal", "send", "--terminal", handle, "--text", text, "--enter", "--wait-submit", str(WAIT_SUBMIT), "--retry-request", request_id, "--json"]
    rc, d = run_orca(args, SEND_TIMEOUT)
    return classify(rc, d, request_id)


# ---- 잠금 -------------------------------------------------------------------------------------
def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def lock_held():
    try:
        age = time.time() - os.stat(LOCK).st_mtime
    except OSError:
        return False
    try:
        with open(os.path.join(LOCK, "pid"), encoding="utf-8") as f:
            return age < LOCK_STALE and pid_alive(int(f.read().strip()))
    except (OSError, ValueError):
        return age < 5  # 폴더만 만들고 pid 를 쓰기 전


def acquire():
    for _ in range(2):
        try:
            os.mkdir(LOCK)
            with open(os.path.join(LOCK, "pid"), "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            if lock_held():
                return False
            release()  # 죽은 워커의 잔재
        except OSError:
            return False
    return False


def release():
    for p in (os.path.join(LOCK, "pid"),):
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.rmdir(LOCK)
    except OSError:
        pass


# ---- 판단·전달 --------------------------------------------------------------------------------
def prepare(state, lines, mark):
    """상태를 현재 inbox·표식에 맞춤. (state, 변경 여부)"""
    changed = False
    if state["pushed_lines"] < mark:  # 메인이 먼저 읽은 줄은 보내지 않음
        state["pushed_lines"], changed = mark, True
    pp = state.get("push_pending")
    if pp and (mark >= pp["last"] or pp["last"] > len(lines)):  # 읽혔거나 inbox 가 잘린 보류분은 버림
        state["push_pending"], changed = None, True
    return changed


def needs_work(state, lines, mark):
    if state is None:
        return True
    prepare(state, lines, mark)
    pp = state.get("push_pending")
    if pp and pp.get("stop"):  # 자동 재시도 없는 보류(unknown_delivery·unsubmitted). 읽음 표식이 보류분을 지나면 prepare 가 버려 다시 일함
        return False
    start = pp["last"] if pp else state["pushed_lines"]
    items, _ = pending_items(lines, start, react_floor(state))
    return bool(pp or items or walk_mark(lines, mark, state["pushed_lines"], state.get("skip_to", 0), react_floor(state)) > mark)


def items_of(lines, pp):
    """보류 묶음의 전달 대상. 반응 기준선은 묶음을 만들 때 값(없는 옛 묶음은 반응 없이 본문이 고정돼 끝 줄까지 옛 반응)"""
    floor = pp.get("react_since", pp["last"])
    return [(n, lines[n - 1]) for n in range(pp["first"], pp["last"] + 1) if n <= len(lines) and deliverable(lines[n - 1], n, floor)]


def hold(pp, reason, next_at, lines_txt, **kv):
    """보류 사유를 상태에 남기고 사유가 바뀔 때만 로그 1줄"""
    if pp.get("reason") != reason:
        log("hold", reason=reason, lines=lines_txt, **kv)
    pp["reason"], pp["next_at"] = reason, next_at


def seal(pp, items, lines, mark):
    """보류 묶음의 본문과 요청 ID 를 고정(없을 때만). 첫 시도부터 마지막 시도까지 본문·ID 가 같아야 Orca 가 재전송을 같은 요청으로 알아봄.
    본문에 들어가는 읽지 않은 반응 수는 표식에 따라 바뀌므로 확정 시점 값으로 박음"""
    if isinstance(pp.get("text"), str) and UUID_RE.match(str(pp.get("request_id"))):
        return
    pp["batch"] = write_batch(items, pp.get("batch"))
    floor = pp.get("react_since", pp["last"])
    react = sum(1 for i in range(mark, min(pp["last"], len(lines))) if old_react(lines[i], i + 1, floor))
    pp["text"], pp["request_id"], pp["ambig"] = render(items, pp["batch"], react), str(uuid.uuid4()), None


def attempt(state, lines, mark, now):
    """보류 묶음 1회 전달 시도. 'sent' | 'fail' | 'hold'"""
    pp = state["push_pending"]
    span = f"{pp['first']}-{pp['last']}"
    items = items_of(lines, pp)
    if not items:  # 보류분이 사라짐(inbox 가 바뀜). 보낼 것 없음
        state["push_pending"] = None
        return "sent"
    handle, why, short, cands = find_target()
    if why in LINK_LOST:
        alert(why, cands)
    elif why != "show_fail":  # 핸들이 메인 세션임을 확인함(화면 상태 보류 포함). 조회 실패는 판단 보류
        alert_clear()
    if why:
        hold(pp, why, now + HOLD_RETRY, span, handle=short)
        return "hold"
    if pp.get("ambig") and pp["ambig"] not in (handle_tag(handle), handle):  # 원문은 이전 버전이 남긴 상태 호환
        # 도착 여부를 모르는 시도를 한 터미널과 다름. 요청 ID 는 터미널에 묶이므로 다른 곳에 보내면 중복될 수 있음.
        # 옛 터미널이 이미 죽었으면 사람이 못 본 것이므로 같은 묶음을 새 요청 ID 로 새 대상에 재전달. 살아 있거나 확인 못 하면 보류
        old = pp["ambig"]
        fate = mt.is_dead(run_orca, lambda h: old in (handle_tag(h), h))
        if fate != "dead":
            hold(pp, "unknown_delivery", now + FOREVER, span, why="target_changed")
            pp["stop"] = True
            if fate == "alive":
                alert("target_changed", text=ALERT_STALE)
            return "hold"
        log("redeliver", why="target_dead_redeliver", lines=span)
        pp["ambig"], pp["request_id"], pp["attempts"] = None, str(uuid.uuid4()), 0
    seal(pp, items, lines, mark)
    save_state(state)  # 보내기 전에 본문·ID 를 저장. 전송 직후 프로세스가 죽어도 다음 시도가 같은 ID 로 재전송함
    verdict, code, info = send(handle, pp["text"], pp["request_id"])
    if verdict == "sent":
        info["confirm"] = "receipt"
    elif verdict == "unsubmitted":  # 접수(input_accepted)된 영수증만 화면으로 보강 확인. 그 밖은 기존 보류
        if not CONFIRM:
            info["confirm"] = "off"
        elif "input_accepted" not in info["stages"].split(","):
            info["confirm"] = "skip"
        else:
            verdict, code, extra = confirm_screen(handle)
            info.update(extra)
    if verdict == "sent":
        state["pushed_lines"], state["push_pending"] = pp["last"], None
        state["pending_since"] = state["last_new_ts"] = None
        mark_to = walk_mark(lines, mark, state["pushed_lines"], state.get("skip_to", 0), react_floor(state))
        write_mark(mark_to)
        record_delivery(items, pp["last"])
        log("ok", n=len(items), lines=span, mark=mark_to, **info)
        return "sent"
    if verdict in ("unknown", "unsubmitted"):  # 자동 재전송 금지. 표식도 올리지 않음(올리면 메인이 못 본 채 읽음 처리됨). 사람이 터미널을 보고 풂
        hold(pp, "unknown_delivery" if verdict == "unknown" else "unsubmitted", now + FOREVER, span, why=code, **info)
        pp["stop"], pp["ambig"] = True, handle_tag(handle)
        return "hold"
    if verdict == "blocked":  # Orca 가 권한·신뢰 메뉴로 판단해 거절. 실패 횟수에 넣지 않고 보류
        hold(pp, "dialog", now + HOLD_RETRY, span)
        if not pp.get("ambig"):
            pp["request_id"] = str(uuid.uuid4())  # 입력 전 거절이라 ID 를 바꿔도 중복 없음(앞선 모호한 시도가 있으면 같은 ID 유지)
        return "hold"
    if verdict == "retry":
        pp["ambig"] = handle_tag(handle)
    elif not pp.get("ambig"):  # refused: 입력을 받지 않았으므로 새 ID
        pp["request_id"] = str(uuid.uuid4())
    pp["attempts"] = pp.get("attempts", 0) + 1
    pp["reason"] = code
    delay = RETRY_BASE * 3 ** (pp["attempts"] - 1) if pp["attempts"] < RETRY_TRIES else HOLD_GAP
    pp["next_at"] = now + delay
    log("fail", reason=code, attempts=pp["attempts"], lines=span)
    return "fail"


def freeze(state, items, now, lines, mark):
    """대기분을 한 묶음으로 확정. 이 시점에 배치 파일·본문·요청 ID(uuid4)를 만들어 상태에 저장(seal)"""
    batch = items[:BATCH_ITEMS]
    pp = {"first": batch[0][0], "last": batch[-1][0], "attempts": 0, "next_at": now, "reason": None, "batch": None,
          "text": None, "request_id": None, "ambig": None, "stop": False, "react_since": react_floor(state)}
    seal(pp, batch, lines, mark)
    state["push_pending"] = pp


def worker():
    """전달할 것이 없어질 때까지 반복. 대기가 길어지면 끝내고 다음 --kick 에 맡김"""
    prune_batches()
    t0, sent = time.time(), 0
    lines, mark = read_inbox(), read_mark()
    state = load_state()
    if state is None:
        state = new_state(lines, mark)
        if state["skip_to"]:
            log("baseline", skipped=state["skip_to"] - mark)
        save_state(state)
    state["react_since"] = react_floor(state)  # 옛 상태는 키를 처음 채울 때 기준선을 고정(뒤에 전달한 줄로 밀리지 않게)
    while True:
        lines, mark, now = read_inbox(), read_mark(), time.time()
        prepare(state, lines, mark)
        pp = state.get("push_pending")
        wait = None
        if pp:
            if now >= pp["next_at"]:
                res = attempt(state, lines, mark, now)
                sent += res == "sent"
                save_state(state)
                if res == "sent":
                    repoll()
                    continue
                wait = state["push_pending"]["next_at"] - time.time()
            else:
                wait = pp["next_at"] - now
        else:
            items, _ = pending_items(lines, state["pushed_lines"], state["react_since"])
            if items:
                arr = [min(arrival(it), now) for _, it in items]
                state["pending_since"], state["last_new_ts"] = min(arr), max(arr)
                due = min(state["last_new_ts"] + QUIET, state["pending_since"] + MAX_WAIT)
                if now >= due:
                    freeze(state, items, now, lines, mark)
                    save_state(state)
                    continue
                wait = due - now
            else:
                state["pending_since"] = state["last_new_ts"] = None
                new = walk_mark(lines, mark, state["pushed_lines"], state.get("skip_to", 0), state["react_since"])
                if new > mark:
                    write_mark(new)
                save_state(state)
                return
        save_state(state)
        if wait is None or sent >= SEND_CAP or time.time() - t0 + wait > LIFE:
            return
        time.sleep(max(0.02, min(TICK, wait)))


def repoll():
    """전달 직후 폴러를 한 번 돌려 읽음 반응(👀→✅)을 바로 반영. 다음 정기 회차를 기다리지 않게 함"""
    poll = os.path.join(SELF_DIR, "slack_poll.sh")
    if os.environ.get("LRM_PUSH_REPOLL", "").strip().lower() in OFF or not os.path.exists(poll):
        return
    try:
        subprocess.run(["/bin/bash", poll, "--once"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=200)
    except (OSError, subprocess.TimeoutExpired):
        pass


def note_off():
    """끄기 표식 파일 때문에 꺼져 있음을 로그에 한 줄만 남김(직전 줄이 이미 같은 기록이면 건너뜀. 폴러가 2분마다 불러도 로그가 늘지 않음).
    LRM_PUSH=off 환경 변수는 의도적으로 기록하지 않음"""
    if not os.path.exists(OFF_FILE):
        return
    try:
        with open(LOG, "rb") as f:
            f.seek(max(0, os.path.getsize(LOG) - 200))
            last = f.read().decode("utf-8", "replace").strip().split("\n")[-1]
    except OSError:
        last = ""
    if " push off " not in last:
        log("off", reason="off_file")


def kick():
    """보낼 것이 있고 워커가 없을 때만 분리된 워커를 띄우고 바로 반환. 폴러·소켓 수신기의 출력 파이프를 잡지 않게 모든 표준 입출력을 닫음"""
    if not ENABLED:
        note_off()
        return
    if lock_held():
        return
    lines, mark = read_inbox(), read_mark()
    if not needs_work(load_state(), lines, mark):
        return
    subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "--run"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def main(argv):
    if len(argv) != 1 or argv[0] not in ("--kick", "--run"):
        print("사용: slack_push.sh --kick|--run (--help 참고)", file=sys.stderr)
        return 2
    if argv[0] == "--kick":
        kick()
        return 0
    if not ENABLED:
        note_off()
        return 0
    os.makedirs(DIR, exist_ok=True)
    if not acquire():
        return 0
    try:
        worker()
    finally:
        release()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
