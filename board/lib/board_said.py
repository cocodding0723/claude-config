"""사용자가 코디네이터 창에 직접 친 글 읽기(한 곳). 지시 장부(board-said)와 계획 승인 판정(board_flow)이 같이 씀.

원료는 그 작업 공간의 Claude Code 세션 기록(<설정 폴더>/projects/<작업 공간 경로를 - 로 바꾼 이름>/*.jsonl).
세션 기록은 세션이 고쳐 쓸 수 없는 파일이라(쓰기는 훅이 막음) 코디네이터가 적어 주는 원문보다 믿을 수 있음.
사용자 글로 치는 줄: type=user 이고 본문이 글(도구 결과 아님)인 줄, 작업 도중에 친 글(attachment queued_command).
빼는 줄: 시스템이 넣은 줄(<…> 꼴·요약 이어 붙임), 스크립트가 입력창에 넣는 알림(작업자 메시지 도착·Slack 답 도착).
한계: 입력창에 글을 넣는 명령(orca terminal send)으로 넣은 줄은 사용자 글과 구분하지 못함. 일부러 규칙을 피하는 경우라 막지 않음.
"""
import datetime
import glob
import json
import os
import re

PROFILE_GLOB = "~/Library/Application Support/orca/claude-profiles/*/home"  # Orca 가 계정마다 두는 설정 폴더
NOISE_HEADS = ("<", "[SYSTEM", "Caveat:", "This session is being continued", "[Request interrupted")
NOISE_LINES = (re.compile(r"^You have \d+ orchestration messages?\b"),   # 작업자 메시지 도착 알림
               re.compile(r"^Slack 답 도착:"))                             # 답 전달기의 입력창 알림(feedback_route.WAKE_TEXT)
SAME_S = 120  # 같은 글이 이 초 안에 두 번 보이면 한 번으로 침(대기열에 들어갔다 다시 기록된 글)


def config_dirs(env=None) -> list:
    """세션 기록을 찾을 설정 폴더들. BOARD_CLAUDE_DIRS(쌍점 구분)가 있으면 그것만(시험용)"""
    env = os.environ if env is None else env
    if env.get("BOARD_CLAUDE_DIRS"):
        return [d for d in env["BOARD_CLAUDE_DIRS"].split(":") if d]
    out = [os.path.expanduser("~/.claude")]
    if env.get("CLAUDE_CONFIG_DIR"):
        out.append(env["CLAUDE_CONFIG_DIR"])
    return out + sorted(glob.glob(os.path.expanduser(PROFILE_GLOB)))


def transcripts(ws: str, dirs=None) -> list:
    """작업 공간 ws 의 세션 기록 파일들(같은 파일을 가리키는 링크는 한 번만)"""
    name = re.sub(r"[^A-Za-z0-9]", "-", os.path.normpath(ws))
    seen, out = set(), []
    for d in (config_dirs() if dirs is None else dirs):
        for p in glob.glob(os.path.join(glob.escape(d), "projects", name, "*.jsonl")):
            real = os.path.realpath(p)
            if real not in seen:
                seen.add(real)
                out.append(p)
    return out


def stamp(value) -> float:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def text_of(rec: dict) -> str:
    """기록 한 줄에서 사용자가 친 글. 사용자 글이 아니면 ''"""
    if rec.get("isMeta") or rec.get("isSidechain"):
        return ""
    if rec.get("type") == "attachment":
        att = rec.get("attachment") or {}
        return str(att.get("prompt") or "") if att.get("type") == "queued_command" else ""
    if rec.get("type") != "user":
        return ""
    body = (rec.get("message") or {}).get("content")
    if isinstance(body, str):
        return body
    if isinstance(body, list) and all(isinstance(b, dict) and b.get("type") == "text" for b in body):
        return "\n".join(str(b.get("text") or "") for b in body)
    return ""


def is_said(text: str) -> bool:
    t = text.strip()
    return bool(t) and not t.startswith(NOISE_HEADS) and not any(p.match(t) for p in NOISE_LINES)


def typed(ws: str, since: float = 0.0, dirs=None) -> list:
    """작업 공간 ws 의 세션에서 사용자가 since(epoch) 뒤에 직접 친 글 [(epoch, 글)], 오래된 순. 기록을 못 찾으면 []"""
    rows = []
    for path in transcripts(ws, dirs):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    if '"type":"user"' not in ln and '"type": "user"' not in ln and "queued_command" not in ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    text = text_of(rec) if isinstance(rec, dict) else ""
                    at = stamp(rec.get("timestamp")) if text else 0.0
                    if text and at > since and is_said(text):
                        rows.append((at, text.strip()))
        except OSError:
            continue
    rows.sort()
    out = []
    for at, text in rows:
        if any(t == text and at - a <= SAME_S for a, t in out[-5:]):
            continue
        out.append((at, text))
    return out


# ── 지시 장부: 사용자가 직접 친 글을 기능 카드 코멘트에 번호와 함께 적고, 글마다 처리를 적음 ──
SAID_RE = re.compile(r"^지시 #(\d+) \([^)]*\): (.*) \[said:(\d+)\]\s*$", re.S)
DONE_RE = re.compile(r"^지시 처리 ((?:#\d+[, ]*)+): (\S+) — (.+)$", re.S)
STATES = ("반영", "미반영", "답함", "되물음", "해당없음")  # 답함: 질문에 답함. 되물음: 사용자에게 다시 물어 답을 기다림(완료 검사는 통과 못 함)
WAITING = "되물음"


def said_line(n: int, at: float, text: str) -> str:
    when = datetime.datetime.fromtimestamp(at).strftime("%m-%d %H:%M")
    return f"지시 #{n} ({when}): {text} [said:{int(at)}]"


def done_line(numbers: list, state: str, why: str) -> str:
    return f"지시 처리 {','.join(f'#{n}' for n in numbers)}: {state} — {why}"


def ledger(comments: list) -> dict:
    """카드 코멘트(글 목록, 오래된 순) → {번호: {"at": 친 시각, "text": 원문, "state": 처리('' 면 아직), "why": 근거}}.
    같은 지시의 처리를 여러 번 적으면 마지막 것이 기준"""
    out = {}
    for c in comments:
        m = SAID_RE.match(c.strip())
        if m:
            out.setdefault(int(m.group(1)), {"at": int(m.group(3)), "text": m.group(2), "state": "", "why": ""})
            continue
        m = DONE_RE.match(c.strip())
        if m and m.group(2) in STATES:
            for n in re.findall(r"#(\d+)", m.group(1)):
                if int(n) in out:
                    out[int(n)].update(state=m.group(2), why=m.group(3).strip())
    return out


def unrecorded(rows: list, book: dict, skip=None) -> list:
    """세션 기록의 사용자 글 [(epoch, 글)] 중 장부에 아직 없는 것. skip(글) 이 참인 글은 지시로 치지 않음(승인·건너뛰기 같은 입력)"""
    have = {v["at"] for v in book.values()}
    return [(at, text) for at, text in rows if int(at) not in have and not (skip and skip(text))]


def unsettled(book: dict) -> list:
    """처리가 안 적혔거나 사용자 답을 기다리는(되물음) 지시 번호"""
    return sorted(n for n, v in book.items() if v["state"] in ("", WAITING))
