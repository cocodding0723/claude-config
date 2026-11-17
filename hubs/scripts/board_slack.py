#!/usr/bin/env python3
"""작업판 결정 카드 ↔ Slack 결정 카드 연결. Slack 봇을 다시 켤 때 결정의 원천을 작업판 하나로 두기 위한 연결 코드(11-17).

보내기: 열린 kind:decision 카드 → slack_notify.sh --kind decide 번호 카드. 결정 번호는 카드 id 에서 만들고(D-B + 영문·숫자),
       보낸 뒤 카드에 label slack:<결정 번호> 를 붙여 다시 보내지 않음.
설명서: 카드 설명의 '설명서: <html>' 줄을 보내기 직전에 다시 검사해(board_cards.doc_gaps) 카드 스레드에 첨부하고, 모바일 미리보기 PNG 도 붙임(실패해도 카드는 유지).
답 받기: 버튼 답(slack_socket.py) → 결정 번호로 카드를 찾아 board-answer 와 같은 처리(원문 코멘트 + 닫기).
       직접 입력이 질문이면 코멘트만 남기고 카드는 열어 둠. 작업판 카드가 아닌 결정 번호는 손대지 않음(옛 허브·메인 전달 그대로).
자동 보내기 스위치: <LRM_HUB_DIR>/.board-slack.on 파일이 있을 때만 board-ask 가 새 결정 카드를 바로 보냄. 기본 꺼짐(봇 꺼짐).

양식 검사: 알림 스크립트(slack_notify.sh)에 건조 경로가 없어, 같은 폴더 slack_notify.sh 안의 양식 A 검사 코드(FORM_PY: 양식 A·선택지 80자·이모지 이름)와
       hub_report.py check-text(본문 로컬 경로·비밀 첨부)를 꺼내 그대로 돌림. push --dry-run 도 이 검사를 거치므로 건조 실행 통과 = 실제 전송 통과. 본문 티켓 번호(LRM-n)·커밋 해시는 맨 아래 '참고:' 줄로 자동 이동하고,
       제목(첫 줄)이 40자를 넘으면 카드에 '보강 필요: 제목 40자' 코멘트를 남기고 거부. 전송 때 --card 로 slack-sent.jsonl 에 카드 id 를 남김.

사용: board_slack.py push <카드 id> [--dry-run] [--json]
      board_slack.py answer <결정 번호> <답 원문> [--json]      (시험·수동용. 평소엔 slack_socket.py 가 부름)
환경: LRM_HUB_DIR(기본 ~/.config/lrm-hubs) · LRM_NOTIFY(기본 <LRM_HUB_DIR>/slack_notify.sh) · BOARD_DIR · BOARD_BD
종료 코드: 0 성공(이미 보냄·작업판 카드 아님 포함), 2 인자·카드 내용 오류(원인·조치 출력), 3 bd·전송 실패, 4 카드 없음"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import board_cards as cards  # noqa: E402
import decide_card  # noqa: E402

HUB_DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
NOTIFY = os.environ.get("LRM_NOTIFY") or os.path.join(HUB_DIR, "slack_notify.sh")
SWITCH = os.path.join(HUB_DIR, ".board-slack.on")
LABEL = "slack:"
ID_HEAD, ID_MAX = "D-B", 16  # slack_notify 결정 번호 규칙: D- 뒤 영문·숫자 1~16자
SEND_TIMEOUT = 90
PREVIEW = os.environ.get("LRM_PREVIEW") or os.path.join(HUB_DIR, "html_preview.sh")  # 390폭 모바일 미리보기 PNG
PREVIEW_TIMEOUT = 60
NOTIFY_SRC = os.environ.get("LRM_NOTIFY_SRC") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "slack_notify.sh")  # 양식 검사 코드가 든 파일. NOTIFY 는 시험에서 가짜로 바뀌므로 따로 둠
FORM_OPEN, FORM_CLOSE = "read -r -d '' FORM_PY <<'PYEOF' || true\n", "\nPYEOF\n"  # slack_notify.sh 의 파이썬 본체를 감싼 heredoc 표식
HUB_REPORT = os.environ.get("LRM_HUB_REPORT") or os.path.join(os.path.dirname(NOTIFY_SRC), "hub_report.py")  # 경로·비밀 파일 검사. 알림 스크립트와 같은 폴더·같은 환경 변수
FORM_FAIL_LEVELS = ("reject", "hard")  # 실제 전송이 막힐 수 있는 수준(strict 기준). warn 은 통과
CARD_ID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}-[a-z0-9]{1,12}(\.[0-9]{1,3})*$")  # slack_notify --card 형식. 틀리면 전송이 거부돼 이 경우엔 --card 를 생략
REF_HEAD = "참고:"
REF_SEP = " · "
TICKET_RX = re.compile(r"(?<![0-9A-Za-z_])LRM-\d+(?![0-9A-Za-z_])")  # 알림 스크립트 RX ticket·hex 와 같은 규칙. 어긋나면 위 검사가 잡음
HEX_RX = re.compile(r"(?<![0-9A-Za-z_])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9A-Za-z_])", re.I)
REF_WORDS = ((TICKET_RX, "관련 티켓"), (HEX_RX, "관련 커밋"))  # 본문에서는 쉬운 말로 바꾸고 번호는 참고 줄로
TITLE_NEED = f"보강 필요: 제목 {cards.TITLE_MAX}자"
LOCK_DIR = os.path.join(HUB_DIR, ".board-slack-locks")  # 카드별 전송 잠금 파일 폴더
LOCK_WAIT, LOCK_POLL = SEND_TIMEOUT * 2, 0.2  # 앞 전송이 끝나길 기다리는 최대 초(전송+미리보기 시간 여유)·확인 간격
ATTACH_FAIL = "파일 첨부 실패"  # slack_notify 가 카드는 보내고 파일만 못 붙였을 때 stderr 에 남기는 문구


def decision_id(card_id):
    body = re.sub(r"[^A-Za-z0-9]", "", card_id)
    return (ID_HEAD + body)[: 2 + ID_MAX]


def parse_desc(desc):
    """board-ask 가 쓴 설명(선택지:·추천:·이유:·배경:·용어:) → dict"""
    out = {"options": [], "recommend": None, "why": None, "background": None, "terms": None, "doc": None}
    in_opts = False
    for raw in (desc or "").splitlines():
        ln = raw.strip()
        if ln == "선택지:":
            in_opts = True
            continue
        if in_opts and ln.startswith("- "):
            out["options"].append(ln[2:].strip())
            continue
        in_opts = False
        for key, head in (("recommend", "추천:"), ("why", "이유:"), ("background", "배경:"), ("terms", "용어:"), ("doc", cards.DOC_LINE + ":")):
            if ln.startswith(head):
                out[key] = ln[len(head):].strip()
    return out


def show(card_id):
    rows = cards.bd_json("list", "--all", "--id", card_id, "--limit", "0") or []
    card = next((r for r in rows if r.get("id") == card_id), None)
    if card is None:
        raise cards.Fail(4, f"카드 {card_id} 없음. 조치: bd list --label kind:decision 로 id 확인")
    return card


def move_refs(lines):
    """본문 줄의 티켓 번호·커밋 해시를 쉬운 말로 바꾸고 맨 아래 '참고:' 줄로 모음. 번호가 없으면 그대로"""
    refs = []
    out = []
    for ln in lines:
        for rx, word in REF_WORDS:
            refs += rx.findall(ln)
            ln = rx.sub(word, ln)
        out.append(ln)
    refs = list(dict.fromkeys(refs))
    return out + ([REF_HEAD + " " + REF_SEP.join(refs)] if refs else [])


def form_code():
    """slack_notify.sh 안의 양식 검사 코드(FORM_PY) 본문. 못 꺼내면 Fail(3)"""
    try:
        with open(NOTIFY_SRC, encoding="utf-8") as f:
            src = f.read()
        return src.split(FORM_OPEN, 1)[1].split(FORM_CLOSE, 1)[0]
    except (OSError, IndexError):
        raise cards.Fail(3, f"양식 검사 코드를 못 찾음({NOTIFY_SRC}). 조치: slack_notify.sh 가 같은 폴더에 있고 FORM_PY 구간이 그대로인지 확인")


def run_form(args, text):
    """FORM_PY 를 알림 스크립트와 같은 방식(-I, LRM_SCRIPT_DIR)으로 돌려 CompletedProcess 반환. 실행 자체가 실패하면 Fail(3)"""
    env = dict(os.environ, LRM_SCRIPT_DIR=os.path.dirname(NOTIFY_SRC))
    try:
        return subprocess.run([sys.executable, "-I", "-c", form_code(), *args], input=text, capture_output=True, text=True,
                              timeout=SEND_TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise cards.Fail(3, f"양식 검사 실행 실패({type(e).__name__}). 조치: python3 동작 확인")


def form_problems(text):
    """알림 스크립트와 같은 양식 A 검사(decide·답 줄 안내 있음)를 돌려 막힐 위반 문구 목록을 돌려줌. 검사 코드를 못 꺼내면 Fail(3)"""
    p = run_form(["check", "decide", "1"], text)
    if p.returncode != 0:
        raise cards.Fail(3, f"양식 검사 실패: {p.stderr.strip()[:200]}. 조치: slack_notify.sh 양식 검사 코드 확인")
    rows = [ln.split("\t", 2) for ln in p.stdout.splitlines() if ln.count("\t") >= 2]
    return [r[2] for r in rows if r[0] in FORM_FAIL_LEVELS]


def option_problem(opts, rec, did):
    """선택지 문구 검사(80자·이모지 이름·제어 문자 등). 알림 스크립트가 FORM_PY opts 로 막는 것과 같은 코드. 문제 없으면 빈 문자열"""
    p = run_form(["opts", f"={rec}", "", f"={did}", "", ""], opts)
    return p.stderr.strip() if p.returncode == 2 else ""


def path_problem(text, doc):
    """본문에 첨부 아닌 로컬 문서 경로·비밀 파일 첨부가 있는지. 알림 스크립트가 hub_report.py check-text 로 막는 것과 같은 검사. 문제 없으면 빈 문자열"""
    if not os.path.isfile(HUB_REPORT):
        raise cards.Fail(3, f"경로 검사 도구를 못 찾음({HUB_REPORT}). 조치: hub_report.py 가 slack_notify.sh 와 같은 폴더에 있는지 확인")
    try:
        p = subprocess.run([sys.executable, "-I", HUB_REPORT, "check-text", "--kind", "decide", "--hub-dir", HUB_DIR, "--attached", doc],
                           input=text, capture_output=True, text=True, timeout=SEND_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise cards.Fail(3, f"경로 검사 실행 실패({type(e).__name__}). 조치: python3 동작 확인")
    return (p.stderr or p.stdout).strip().removeprefix("오류: ") if p.returncode != 0 else ""


def need_title(card_id, n, dry_run):
    """제목 초과 카드: 보강 필요 코멘트(이미 있으면 생략)를 남기고 거부"""
    msg = f"{card_id} 제목이 {n}자(최대 {cards.TITLE_MAX}). 조치: 뜻을 지켜 줄이고 원래 제목은 설명 첫 줄 '원래 제목: …' 으로 보존(bd update)"
    if not dry_run:
        rows = cards.bd_json("comments", card_id) or []
        if not any(TITLE_NEED in (r.get("text") or "") for r in rows if isinstance(r, dict)):
            cards.bd("comments", "add", card_id, f"{TITLE_NEED} — 현재 {n}자. Slack 첫 줄 규칙(이모지·공백 제외 {cards.TITLE_MAX}자)에 걸려 보내지 못함")
    raise cards.Fail(2, msg)


def notify_args(card):
    """slack_notify.sh 인자. 번호 카드 필수 칸(배경·용어·선택지 결과)·HTML 설명서가 없으면 Fail(2)"""
    if "kind:decision" not in (card.get("labels") or []):
        raise cards.Fail(2, f"{card['id']} 는 결정 카드가 아님. 조치: kind:decision 카드만 보냄")
    d = parse_desc(card.get("description"))
    miss = cards.card_gaps(d["options"], d["background"], d["terms"])
    if miss:
        raise cards.Fail(2, f"{card['id']} Slack 카드 필수 칸 없음: {', '.join(miss)}. "
                            "조치: board-ask 를 --background·--terms·--options \"A => 결과|B => 결과\" 로 다시 만들거나 bd update -d 로 설명 보강")
    doc, doc_card = cards.split_doc(d["doc"])  # '경로#카드id' 면 여러 카드 설명서의 그 카드 절만 검사·첨부
    doc_miss = cards.doc_gaps(d["doc"], len(d["options"]))  # 카드가 나중에 바뀌었어도 보내기 직전에 다시 검사
    if doc_card and card["id"] != "new" and doc_card != card["id"]:
        doc_miss.append(f"설명서 절이 다른 카드({doc_card})용")
    if doc_miss:
        raise cards.Fail(2, f"{card['id']} 설명서 부족: {', '.join(doc_miss)}. "
                            f"조치: HTML 설명서를 보강하고 카드 설명에 '{cards.DOC_LINE}: <html 절대 경로>' 줄을 둠(bd update -d)")
    names = [cards.option_name(o) for o in d["options"]]
    opts = ",".join(f"{i}={o.split('=>', 1)[0].strip()} => {o.split('=>', 1)[1].strip()}" for i, o in enumerate(d["options"], 1))
    body = [card.get("title", ""), f"배경: {d['background']}", f"용어: {d['terms']}"]
    if d["why"]:
        body.append(f"이유: {d['why']}")
    body = move_refs(body)
    args = ["--kind", "decide", "--tag", "board", "--options", opts, "--decision-id", decision_id(card["id"])]
    if CARD_ID_RE.match(card["id"]):
        args += ["--card", card["id"]]
    if d["recommend"] in names:
        rec_no = names.index(d["recommend"]) + 1
        args += ["--recommend", str(rec_no)]
    else:
        raise cards.Fail(2, f"{card['id']} 추천 없음. 조치: board-ask --recommend <선택지> 로 다시 만들기")
    text = "\n".join(body)
    bad = form_problems(text)  # 건조 실행도 실제 전송과 같은 검사를 거치게 함
    if bad:
        raise cards.Fail(2, f"{card['id']} Slack 양식 A 위반: {' / '.join(bad)}")
    for check in (lambda: option_problem(opts, rec_no, decision_id(card["id"])), lambda: path_problem(text, doc)):  # 알림 스크립트가 따로 막는 나머지 검사
        why = check()
        if why:
            raise cards.Fail(2, f"{card['id']} Slack 전송 검사 위반: {why}")
    why = decide_card.problem(text, opts, decision_id(card["id"]))  # 실제 전송 때 막힐 규칙(글자 수 상한 등)을 보내기 전·건조 실행에서 미리 확인
    if why:
        raise cards.Fail(2, f"{card['id']} Slack 카드 규칙 위반: {why}")
    return args + [text, doc]  # 마지막 인자 파일은 slack_notify 가 카드 스레드에 첨부함


def push(card_id, dry_run=False):
    card = show(card_id)
    if card.get("status") == "closed":
        return {"id": card_id, "sent": False, "why": "closed"}
    did = decision_id(card_id)
    have = [x for x in card.get("labels") or [] if x.startswith(LABEL)]
    if have:
        return {"id": card_id, "sent": False, "why": "already", "decisionId": have[0][len(LABEL):]}
    n = cards.title_len(card.get("title", ""))
    if n > cards.TITLE_MAX:
        need_title(card_id, n, dry_run)
    args = notify_args(card)
    clash = [r["id"] for r in cards.bd_json("list", "--all", "--label", LABEL + did, "--limit", "0") or [] if r.get("id") != card_id]
    if clash:
        raise cards.Fail(2, f"결정 번호 {did} 를 {clash[0]} 가 이미 씀. 조치: 카드 id 를 확인하고 수동으로 slack_notify.sh 사용")
    if dry_run:
        return {"id": card_id, "sent": False, "why": "dry-run", "decisionId": did, "args": args}
    with card_lock(card_id):  # 메인·허브가 동시에 push 해 같은 카드가 두 번 가던 문제(11-17). 잠금 안에서 label 을 다시 확인
        have = [x for x in show(card_id).get("labels") or [] if x.startswith(LABEL)]
        if have:
            return {"id": card_id, "sent": False, "why": "already", "decisionId": have[0][len(LABEL):]}
        return send(card_id, did, args)


@contextlib.contextmanager
def card_lock(card_id):
    """카드 하나당 파일 잠금(fcntl.flock). 프로세스가 죽으면 OS 가 풀어 줘서 낡은 잠금이 남지 않음. 못 얻으면 Fail(3)"""
    os.makedirs(LOCK_DIR, exist_ok=True)
    fd = os.open(os.path.join(LOCK_DIR, re.sub(r"[^A-Za-z0-9._-]", "_", card_id) + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > end:
                    raise cards.Fail(3, f"{card_id} 다른 전송이 {LOCK_WAIT}초째 진행 중. 조치: 잠시 뒤 다시 시도(잠금 파일 {LOCK_DIR} 은 지우지 않아도 됨)")
                time.sleep(LOCK_POLL)
        yield
    finally:
        os.close(fd)


def send(card_id, did, args):
    try:
        p = subprocess.run([NOTIFY, *args], capture_output=True, text=True, timeout=SEND_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise cards.Fail(3, f"slack_notify 실행 실패({type(e).__name__}). 조치: LRM_NOTIFY 경로·봇 설정 확인")
    if p.returncode != 0:
        raise cards.Fail(3 if p.returncode == 1 else 2, f"slack_notify 종료 {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
    cards.bd("label", "add", card_id, LABEL + did)
    doc_state = "failed" if ATTACH_FAIL in (p.stderr or "") else "attached"  # slack_notify 는 첨부가 실패해도 종료 0 + stderr 경고만 냄
    if doc_state == "failed":
        cards.bd("comments", "add", card_id, "Slack 카드는 보냈지만 설명서 첨부에 실패함. 조치: 설명서 파일을 카드 스레드에 따로 보냄")
    ts = next((ln[3:].strip() for ln in p.stdout.splitlines() if ln.startswith("ok ")), "")
    return {"id": card_id, "sent": True, "decisionId": did, "doc": doc_state, "preview": attach_preview(args[-1], ts)}


def attach_preview(doc, ts):
    """설명서 모바일 미리보기 PNG 를 카드 스레드에 붙임(slack_notify --thread 재사용). 실패해도 카드 전송은 유지하고 이유만 돌려줌"""
    if not ts:
        return "카드 ts 없음"
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "preview.png")
        try:
            r = subprocess.run([PREVIEW, doc, png], capture_output=True, text=True, timeout=PREVIEW_TIMEOUT, stdin=subprocess.DEVNULL)
            if r.returncode != 0 or not os.path.isfile(png):
                return f"미리보기 생성 실패({(r.stderr or r.stdout).strip()[:80]})"
            s = subprocess.run([NOTIFY, "--thread", ts, "--tag", "board", "설명서 모바일 미리보기(390폭)", png],
                               capture_output=True, text=True, timeout=SEND_TIMEOUT, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"미리보기 실패({type(e).__name__})"
        return "attached" if s.returncode == 0 else f"미리보기 전송 실패({(s.stderr or s.stdout).strip()[:80]})"


def card_for(did):
    """결정 번호 → 열린·닫힌 작업판 카드 id. 작업판 카드가 아니거나 조회 실패면 None"""
    if not isinstance(did, str) or not did.startswith(ID_HEAD):
        return None
    try:
        rows = cards.bd_json("list", "--all", "--label", LABEL + did, "--limit", "0") or []
    except cards.Fail as e:  # 조회 실패를 '작업판 카드 아님'으로 조용히 넘기면 버튼 답이 메인으로 샘. 이유를 로그에 남김
        print(f"board_slack: 카드 조회 실패 — {e}", file=sys.stderr)
        return None
    ids = [r["id"] for r in rows if isinstance(r, dict) and r.get("id")]
    return ids[0] if len(ids) == 1 else None


def answer(did, text, keep_open=False):
    """버튼·직접 입력 답 → 카드 코멘트(+닫기). 작업판 카드가 아니면 None"""
    cid = card_for(did)
    if cid is None:
        return None
    ns = argparse.Namespace(id=cid, text=text, keep_open=keep_open, dry_run=False)
    return cards.answer(ns)


def auto_push(card_id):
    """board-ask 가 새 결정 카드를 만든 직후 부름. 스위치 파일이 없으면 아무것도 안 함. 실패는 카드 생성을 막지 않음"""
    if not os.path.exists(SWITCH):
        return None
    try:
        return push(card_id)
    except cards.Fail as e:
        print(f"board-ask: Slack 카드 못 보냄 — {e}", file=sys.stderr)
        return None


def main(argv):
    ap = argparse.ArgumentParser(prog="board_slack.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push", help="결정 카드를 Slack 번호 카드로 보냄(이미 보냈으면 건너뜀)")
    p.add_argument("id")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    q = sub.add_parser("answer", help="결정 번호의 답 원문을 카드에 기록")
    q.add_argument("decision_id")
    q.add_argument("text")
    q.add_argument("--keep-open", action="store_true")
    q.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    try:
        out = push(a.id, a.dry_run) if a.cmd == "push" else answer(a.decision_id, a.text.strip(), a.keep_open)
    except cards.Fail as e:
        print(f"board_slack: {e}", file=sys.stderr)
        return e.code
    if out is None:
        out = {"decisionId": a.decision_id, "board": False}
    print(json.dumps(out, ensure_ascii=False) if a.json else
          (f"{a.id}: {'보냄 ' + out['decisionId'] if out.get('sent') else out.get('why')}" if a.cmd == "push" else
           f"{a.decision_id}: {'작업판 카드 아님' if out.get('board') is False else ('닫음' if out.get('closed') else '코멘트만')}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
