#!/usr/bin/env python3
"""Slack 스레드 답글 → 작업판 카드 코멘트 + 담당 세션 받은 편지함(hub-inbox/<상자>.jsonl). 메인 중계 없음(11-17).

스레드 하나 = 카드 하나. 카드 종류 두 가지:
- 기능 epic: 기능 코디네이터가 받음. 상자 feat-<slug>
- 작업 공간 카드(label ws:<작업 공간 경로>): 그 작업 공간 세션이 받음. 상자 ws-<카드 id>
스레드 등록: report_sender.py 가 카드의 첫 Slack 보고를 올린 뒤 attach 로 label slack-thread:<ts> 를 붙임(손 등록 open-thread 도 남아 있음).

전달(--once, 폴러 회차마다): slack-inbox.jsonl 의 새 thread_reply 중 parent_ts 가 등록된 스레드면
  - 열린 기능 epic 1개: 원문 그대로 epic 코멘트 + 그 기능 상자에 kind feedback 한 줄(첨부는 저장 경로 그대로). 고쳐 쓰지 않음.
  - 열린 작업 공간 카드: 원문 그대로 카드 코멘트 + 그 상자 한 줄, 이어서 세션 입력창에 'Slack 답 도착: bd show <카드>' 한 줄 알림
    (스위치 파일 <작업 폴더>/.thread-wake.on 이 있을 때만). 입력 막힘(확인 창)·입력 칸에 쓰던 글(화면 보류)·터미널 없음은 회차마다 재시도, 시간 초과는 다시 보내지 않음.
    세션에 닿지 못하면(스위치 꺼짐·재시도 WAKE_MAX_TRIES 회 실패·대기 WAKE_MAX_AGE_S 초과) 부모 기능 코디네이터 상자 feat-<slug> 에 한 줄(없거나 끝났으면 확인 카드)을 넣고 카드 코멘트에 남김.
  - 끝난 작업 공간 카드(닫힘 또는 label slack-thread-end): 그 카드의 부모 기능 epic 으로 보냄. 기능도 끝났으면 확인 카드로.
  - 애매함(같은 스레드에 카드 여럿, 닫힌 epic 만, 담당 없는 카드): 메인 카드 '📥 기능 판별 필요'(label hub:main,feedback:triage)에 원문 코멘트.
  - 등록 안 된 스레드: 손대지 않음(기존 메인 전달 그대로).
  전달한 답글은 feedback-routed.jsonl 에 남고 slack_push 가 그 답글을 메인에 다시 보내지 않음.
첫 실행은 파일 끝에서 시작(옛 답글을 몰아 보내지 않음). 읽은 위치는 feedback-route.offset.

사용: feedback_route.py open-thread --epic <id> --ts <Slack ts> [--dry-run] [--json]
      feedback_route.py attach --card <id> --ts <Slack ts> [--ws <작업 공간 경로>] [--dry-run] [--json]
      feedback_route.py end-thread --card <id> [--dry-run] [--json]
      feedback_route.py --once [--dry-run] [--json]
환경: LRM_HUB_DIR(기본 ~/.config/lrm-hubs) · BOARD_DIR · BOARD_BD
종료 코드: 0 성공(전달할 것 없음 포함), 2 인자 오류, 3 bd·파일 실패, 4 카드 없음, 5 같은 스레드가 이미 다른 카드에 붙어 있어 거부(확인 카드를 만듦)"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import board_cards as cards  # noqa: E402
import card_actions as actions  # noqa: E402
import answer_store  # noqa: E402
import main_inbox  # noqa: E402
import pending_judge  # noqa: E402

THREAD = "slack-thread:"
FEATURE = "feature:"
WS = "ws:"                      # 작업 공간 카드의 담당 작업 공간 경로
END = "slack-thread-end"        # 작업 공간 반납으로 스레드가 끝났다는 표시
BOX_HEAD = "feat-"
WS_BOX_HEAD = "ws-"
BOX_MAX = 48                    # main_inbox.BOX_RE 상한
CARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
# 세션 입력창 알림. 스위치 파일이 없으면 끔(켜기는 사용자 승인 뒤 코디네이터가 함). 알림 문구에는 카드 id 외 외부 글을 넣지 않음
WAKE_SWITCH = ".thread-wake.on"
WAKE_STATE = "thread-wake.json"
WAKE_TEXT = "Slack 답 도착: bd show {card}"
WAKE_PER_RUN = 3                # 회차당 입력창 전송 시도 상한
WAKE_BUDGET_S = 60              # 회차당 입력창 전송에 쓰는 시간 상한. 넘으면 남은 알림은 다음 회차로
WAKE_MAX_TRIES = actions.BLOCK_RETRIES  # 막힘 재시도 횟수 = 결정 답 전달 경로와 같은 값
WAKE_GAP_S = actions.BLOCK_GAP_S
WAKE_MAX_AGE_S = 3600           # 대기열 항목 나이 상한. 넘으면 세션 알림 대신 부모 상자로 넘김(스위치를 오래 꺼 뒀다 켠 경우 등)
ROUTE_FAIL_MAX = 3               # 한 답글의 전달(bd 쓰기)이 이 횟수만큼 실패하면 확인 카드로 넘기고 다음 답글로 진행
ROUTE_FAILS = "feedback-route.fails.json"
HANDOFF_TEXT = "작업 공간 카드 {card} 에 Slack 답 도착, 세션에 알리지 못함({why}). 답 첫 줄: {head}. 원문: bd show {card}"
HEAD_MAX = 80                   # 넘기는 줄에 넣는 답 첫 줄 요약 길이 상한
EMPTY_BODY = "(본문 없음)"        # 본문·첨부가 모두 빈 답글의 대체 본문(상자 쓰기가 본문 필요 오류를 내지 않게)
HANDOFF_FAIL_MAX = ROUTE_FAIL_MAX  # 부모로 넘기기가 이 횟수 실패하면 포기(대기열에서 뺌)
HANDOFF_NOTE = "세션에 알리지 못해({why}) {to} 로 넘김"
HANDOFF_TRIAGE_TITLE = "📥 작업 공간 답 전달 확인 필요: {card}"
TRIAGE_LABELS = "hub:main,feedback:triage"
TRIAGE_TITLE = "📥 기능 판별 필요: Slack 스레드 {ts}"
TS_RE = re.compile(r"^[0-9]{1,12}\.[0-9]{1,9}$")
SENT_NAME = "slack-sent.jsonl"
# 작업 스레드에 결정 번호로 시작하는 글. 상자 줄 머리는 짧게(입력창 알림 요약 HEAD_MAX 안에 원문이 남게), 닫는 법은 카드 코멘트에만
DECIDE_HEAD = "[결정 {did} {kind}] "
DECIDE_ANSWER, DECIDE_ASK = "답", "질문"
DECIDE_NOTE = {DECIDE_ANSWER: ", 결정 {did} 의 글 답. 카드는 열려 있음, 확인 뒤 decide-answer {did} 로 닫음",
               DECIDE_ASK: ", 결정 {did} 에 대한 질문. 답이 아님"}
SOURCE = "feedback_route"
EXIT_CONFLICT = 5


def hub_dir():
    return main_inbox.hub_dir()


def paths():
    d = hub_dir()
    return (os.path.join(d, "slack-inbox.jsonl"), os.path.join(d, "feedback-route.offset"), os.path.join(d, "feedback-routed.jsonl"))


def routed_ts(path=None):
    """이미 전달한 답글 ts 집합. slack_push.read_inbox 도 이걸로 메인 전달을 건너뜀"""
    path = path or paths()[2]
    out = set()
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("ts"):
                    out.add(str(r["ts"]))
    except OSError:
        pass
    return out


def thread_cards():
    """parent_ts → [카드 행]. 열린·닫힌 카드, 기능 epic·작업 공간 카드 모두(닫힌 것만 걸리면 애매로 봄)"""
    out = {}
    for r in cards.bd_json("list", "--all", "--limit", "0") or []:
        if not isinstance(r, dict):
            continue
        for lb in r.get("labels") or []:
            if lb.startswith(THREAD):
                out.setdefault(lb[len(THREAD):], []).append(r)
    # bd 는 하위 카드를 만들 때 부모 label 을 물려줌 → 기능 epic 이 스레드를 가진 뒤 만든 작업 카드에 같은 slack-thread 가 따라 붙음.
    # 부모가 같은 스레드의 주인이면 물려받은 것이라 주인에서 뺌
    for ts, hits in out.items():
        ids = {c.get("id") for c in hits}
        out[ts] = [c for c in hits if c.get("parent") not in ids]
    return out


def card_row(card):
    rows = cards.bd_json("list", "--all", "--id", card, "--limit", "0") or []
    return next((r for r in rows if isinstance(r, dict) and r.get("id") == card), None)


def label_value(card, head):
    return next((lb[len(head):] for lb in card.get("labels") or [] if lb.startswith(head)), None)


def slug_of(epic):
    return label_value(epic, FEATURE)


def ws_of(card):
    return label_value(card, WS)


def is_feature(card):
    return card.get("issue_type") == "epic" and bool(slug_of(card))


def is_open(card):
    """끝나지 않은 카드: 닫히지 않았고 반납 표시(END)도 없음"""
    return card.get("status") != "closed" and END not in (card.get("labels") or [])


def box_of(card_id):
    """작업 공간 카드 id → 받은 편지함 상자 이름(main_inbox.BOX_RE 에 맞게 소문자·숫자·- 만)"""
    core = re.sub(r"[^a-z0-9]+", "-", card_id.lower()).strip("-")
    return (WS_BOX_HEAD + core)[:BOX_MAX].rstrip("-")


def body(rec):
    """원문 + 첨부 저장 경로. 내용은 바꾸지 않음"""
    text = str(rec.get("text") or "")
    files = [str(p) for p in rec.get("files") or []]
    if not text.strip() and not files:
        text = EMPTY_BODY
    if files:
        text += "\n첨부: " + " ".join(files)
    if rec.get("files_failed"):
        text += f"\n첨부 저장 실패 {len(rec['files_failed'])}건(Slack 스레드에서 직접 확인)"
    return text


def triage_card(ts, dry_run):
    title = TRIAGE_TITLE.format(ts=ts)
    rows = cards.bd_json("list", "--label", "feedback:triage", "--limit", "0") or []
    hit = next((r["id"] for r in rows if isinstance(r, dict) and r.get("title") == title and r.get("status") != "closed"), None)
    if hit or dry_run:
        return hit or "(새 카드)"
    made = cards.bd_json("create", title, "-t", "task", "-p", "1", "-l", TRIAGE_LABELS, "-d",
                         f"Slack 스레드 {ts} 의 답글이 어느 기능 것인지 정해지지 않음. 조치: 맞는 카드에 feedback_route.py attach(기능 epic 은 open-thread)로 다시 걸고 이 카드를 닫음",)
    made = made[0] if isinstance(made, list) else made
    return made["id"]


def to_feature(epic, rec, text, dry_run, note="", head=""):
    """열린 기능 epic 으로: epic 코멘트 + 기능 코디네이터 상자. note 는 코멘트 머리 보충(끝난 작업 공간 카드에서 온 답 등)"""
    box = BOX_HEAD + slug_of(epic)
    if not dry_run:  # 상자 먼저: 중복 판정이 있어 재시도해도 줄이 겹치지 않고, 코멘트는 상자가 된 뒤에 한 번만
        main_inbox.append("feedback", head + text, SOURCE, ref=f"{epic['id']} slack:{rec.get('ts')}", box=box)
        cards.bd("comments", "add", epic["id"], f"Slack 피드백(원문{note}): {text}")
    return {"ts": str(rec.get("ts")), "parent_ts": str(rec.get("parent_ts") or ""), "to": "feature", "epic": epic["id"], "box": box}


def to_triage(rec, hits, why, text, dry_run):
    parent = str(rec.get("parent_ts") or "")
    cid = triage_card(parent, dry_run)
    if not dry_run:
        cards.bd("comments", "add", cid, f"Slack 피드백(원문, {why}: {', '.join(e['id'] for e in hits)}): {text}")
    return {"ts": str(rec.get("ts")), "parent_ts": parent, "to": "triage", "card": cid, "why": why}


def to_ws(card, rec, text, dry_run, head="", note=""):
    """열린 작업 공간 카드로: 카드 코멘트 + 세션 받은 편지함 한 줄. 입력창 알림은 호출한 쪽이 once() 끝에서 처리"""
    box = box_of(card["id"])
    if not dry_run:  # 상자 먼저(to_feature 와 같은 이유)
        main_inbox.append("feedback", head + text, SOURCE, ref=f"{card['id']} slack:{rec.get('ts')}", box=box)
        cards.bd("comments", "add", card["id"], f"Slack 피드백(원문{note}): {text}")
    return {"ts": str(rec.get("ts")), "parent_ts": str(rec.get("parent_ts") or ""), "to": "ws", "card": card["id"], "box": box}


def route_guarded(rec, by_ts, dry_run):
    """route 에 답글별 실패 횟수 제한을 건 것. bd 오류가 한 답글에서만 계속 나면 뒤 답글이 영원히 막히므로
    ROUTE_FAIL_MAX 회째에 확인 카드로 넘기고 진행(확인 카드 쓰기도 실패하면 그대로 오류). 일시 오류는 이전처럼 다음 회차에 같은 답글부터 다시"""
    try:
        return route(rec, by_ts, dry_run)
    except (cards.Fail, OSError, ValueError) as err:  # 상자 쓰기 실패(OSError)·상자가 거부한 본문(ValueError)도 bd 실패와 같이 셈
        if dry_run:
            raise
        path, ts = os.path.join(hub_dir(), ROUTE_FAILS), str(rec.get("ts"))
        try:
            with open(path, encoding="utf-8") as f:
                fails = json.load(f)
        except (OSError, ValueError):
            fails = {}
        fails[ts] = fails.get(ts, 0) + 1
        if fails[ts] < ROUTE_FAIL_MAX:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fails, f)
            raise
        got = to_triage(rec, by_ts.get(str(rec.get("parent_ts") or "")) or [], f"전달 {fails[ts]}회 실패({str(err)[:80]})", body(rec), False)
        fails.pop(ts, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fails, f)
        return got


def decision_of(rec):
    """이 답글이 그 스레드 안 결정 카드에 쓴 글 답이면 결정 번호, 아니면 ''. 판정은 pending_judge 한 곳(대기 목록과 같은 규칙)"""
    try:
        with open(os.path.join(hub_dir(), SENT_NAME), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return ""
    for ln in reversed(lines):  # 같은 번호를 다시 보낸 경우 최근 카드
        try:
            s = json.loads(ln)
        except ValueError:
            continue
        if isinstance(s, dict) and pending_judge.is_decision(s) and pending_judge.written_answer(s, rec):
            return s["decision_id"]
    return ""


def route(rec, by_ts, dry_run):
    """한 답글의 전달 결과 dict, 등록된 스레드가 아니면 None. 결정 번호로 시작하는 글은 그 결정의 답으로 표시해 같은 곳에 전달"""
    hits = by_ts.get(str(rec.get("parent_ts") or ""))
    if not hits:
        return None
    did = decision_of(rec)
    if not did:
        return place(rec, hits, body(rec), dry_run)
    # 번호로 시작해도 질문이면 답으로 표시하지 않음(담당이 질문을 답으로 보고 카드를 닫지 않게). 판정은 대기 목록과 같은 규칙
    kind = DECIDE_ASK if answer_store.is_question(rec.get("text")) else DECIDE_ANSWER
    got = place(rec, hits, body(rec), dry_run, head=DECIDE_HEAD.format(did=did, kind=kind), note=DECIDE_NOTE[kind].format(did=did))
    return dict(got, decision_id=did)


def place(rec, hits, text, dry_run, head="", note=""):
    """답글을 받을 곳(작업 공간 카드·기능 epic·확인 카드)에 넣음. head 는 상자 줄 머리, note 는 카드 코멘트의 보충(둘 다 원문은 건드리지 않음)"""
    if len(hits) == 1:
        c = hits[0]
        if ws_of(c) is not None:
            if is_open(c):
                return to_ws(c, rec, text, dry_run, head, note)
            # 끝난 작업 공간 카드: 답은 그 기능 코디네이터가 받음. 기능도 끝났으면 확인 카드
            up = card_row(c["parent"]) if c.get("parent") else None
            if up and up.get("status") != "closed" and is_feature(up):
                return to_feature(up, rec, text, dry_run, note=f", 끝난 작업 공간 카드 {c['id']} 스레드" + note, head=head)
            return to_triage(rec, hits, "끝난 작업 공간 카드(기능도 끝남)" + note, text, dry_run)
        if is_feature(c) and c.get("status") != "closed":
            return to_feature(c, rec, text, dry_run, note=note, head=head)
    opened = [e for e in hits if e.get("status") != "closed"]
    if len(hits) > 1:
        why = "epic 여럿" if all(e.get("issue_type") == "epic" for e in hits) else "카드 여럿"
    elif not opened:
        why = "닫힌 epic"
    elif hits[0].get("issue_type") == "epic":
        why = "epic 에 feature: 라벨 없음"
    else:
        why = "담당 작업 공간 없는 카드"
    return to_triage(rec, hits, why + note, text, dry_run)


def answer_to_ws(card_id, ws, ts, text, kind="answer"):
    """결정 버튼 답(kind answer)이나 직접 입력 질문(question)을 그 결정을 올린 세션에 전달. 스레드 글 답과 같은 길: 세션 상자 한 줄 → 카드 코멘트 → 입력창 알림.
    기능 카드(epic)로 올린 결정은 기능 코디네이터 상자, 그 밖은 작업 공간 카드 상자. 반환 = 상자 이름. 상자·코멘트 쓰기 실패는 예외(부르는 쪽이 메인으로 넘김).
    입력창 알림은 실패해도 대기열에 남아 폴러 회차가 다시 시도하므로 전달 실패로 치지 않음"""
    row = card_row(card_id)
    box = BOX_HEAD + slug_of(row) if row and is_feature(row) else box_of(card_id)
    main_inbox.append(kind, text, SOURCE, ref=f"{card_id} slack:{ts}", box=box)
    cards.bd("comments", "add", card_id, f"Slack 결정 {'질문' if kind == 'question' else '답'}: {text}")
    if ws:
        queue_wake(card_id, ws, ts, text)
        try:
            run_wakes()
        except (cards.Fail, OSError, ValueError):
            pass
    return box


# ---- 세션 입력창 알림 ------------------------------------------------------------------------
def wake_on():
    return os.path.exists(os.path.join(hub_dir(), WAKE_SWITCH))


def load_wake():
    try:
        with open(os.path.join(hub_dir(), WAKE_STATE), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in d.get("items", []) if isinstance(x, dict) and x.get("card") and x.get("ws")] if isinstance(d, dict) else []


def save_wake(items):
    path = os.path.join(hub_dir(), WAKE_STATE)
    tmp = path + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def queue_wake(card_id, ws, ts="", text="", now=None):
    """입력창 알림 대기열에 추가. 같은 카드의 알림이 이미 대기 중이면 한 항목으로 합침(입력창 문구는 같고 카드 코멘트에 답이 다 있음).
    항목은 묶인 답글의 (ts, 첫 줄)을 모두 가져, 부모로 넘길 때 답마다 한 줄씩 냄"""
    items = load_wake()
    reply = {"ts": str(ts), "head": head_of(text)}
    for x in items:
        if x["card"] == card_id:
            x.setdefault("replies", []).append(reply)
            save_wake(items)
            return False
    items.append({"card": card_id, "ws": ws, "tries": 0, "next_at": 0, "at": int(time.time() if now is None else now), "replies": [reply]})
    save_wake(items)
    return True


def head_of(text):
    """답 본문의 첫 줄(앞뒤 공백 제외) 요약. 비면 EMPTY_BODY"""
    first = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), EMPTY_BODY)
    return first if len(first) <= HEAD_MAX else first[: HEAD_MAX - 1] + "…"


def hand_off(card_id, why, replies=None):
    """세션에 닿지 못한 작업 공간 카드 답을 부모로 넘김: 열린 기능 epic 의 코디네이터 상자(기존 기능 스레드 경로, to_feature 와 같은 상자)에 한 줄,
    기능이 없거나 끝났으면 확인 카드. 줄은 묶인 답글마다 하나(ref 에 답글 ts·본문에 첫 줄이 있어 받은 편지함 중복 판정에 걸리지 않음). 넘긴 사실은 작업 공간 카드 코멘트 한 줄. 반환 = 넘긴 곳 이름, 실패는 None(대기열에 남겨 다음 회차 재시도)"""
    try:
        row = card_row(card_id)
        up = card_row(row["parent"]) if row and row.get("parent") else None
        items = replies or [{"ts": "", "head": EMPTY_BODY}]  # 답글 정보가 없는 옛 대기열 항목은 한 줄로
        lines = [(f"{card_id} handoff slack:{r.get('ts') or 'unknown'}", HANDOFF_TEXT.format(card=card_id, why=why, head=r.get("head") or EMPTY_BODY)) for r in items]
        if up and up.get("status") != "closed" and is_feature(up):
            box = BOX_HEAD + slug_of(up)
            for ref, line in lines:
                main_inbox.append("feedback", line, SOURCE, ref=ref, box=box)
            to = f"기능 코디네이터 상자 {box}"
        else:
            title = HANDOFF_TRIAGE_TITLE.format(card=card_id)
            hit = next((r["id"] for r in cards.bd_json("list", "--label", "feedback:triage", "--limit", "0") or []
                        if isinstance(r, dict) and r.get("title") == title and r.get("status") != "closed"), None)
            if hit:
                for _ref, line in lines:
                    cards.bd("comments", "add", hit, line)
            else:
                made = cards.bd_json("create", title, "-t", "task", "-p", "1", "-l", TRIAGE_LABELS, "-d", "\n".join(line for _ref, line in lines))
                made = made[0] if isinstance(made, list) else made
                hit = made["id"]
            to = f"확인 카드 {hit}"
        cards.bd("comments", "add", card_id, HANDOFF_NOTE.format(why=why, to=to))
        return to
    except (cards.Fail, OSError, KeyError, TypeError):
        return None


def run_wakes(now=None):
    """대기 알림을 한 번씩 처리. 결과 요약 목록.
    세션에 닿지 못하는 경우는 모두 부모 상자로 넘김(hand_off): 스위치 꺼짐, 나이 상한(WAKE_MAX_AGE_S) 초과, 막힘·화면 보류·터미널 없음이 WAKE_MAX_TRIES 회 계속됨.
    전송 결과: 성공·시간 초과(도착 불명 → 다시 보내지 않음)는 대기열에서 뺌. 막힘·막힘 아닌 실패는 WAKE_GAP_S 뒤 재시도하고 WAKE_MAX_TRIES 회째에 넘김.
    넘기기에 실패하면 항목을 남겨 다음 회차에 다시 시도(답이 사라지지 않게). 답 자체는 카드 코멘트와 작업 공간 상자에 이미 있음"""
    now = time.time() if now is None else now
    items, keep, out = load_wake(), [], []
    if not items:
        return out
    on = wake_on()
    start, sent = time.monotonic(), 0

    def give(it, row, why):
        to = hand_off(it["card"], why, it.get("replies"))
        if to is None:
            it["handoff_fails"] = it.get("handoff_fails", 0) + 1
            if it["handoff_fails"] >= HANDOFF_FAIL_MAX:  # 카드가 지워진 경우 등 끝없는 재시도 방지. 답은 카드 코멘트·ws 상자에 이미 있음
                row.update(wake="handoff_dropped", why=why)
            else:
                keep.append(it)
                row["wake"] = "handoff_failed"
        else:
            row.update(wake="handed_off", why=why, to=to)
        out.append(row)

    for it in items:
        row = {"card": it["card"]}
        if not on:
            give(it, row, "스위치 꺼짐")
            continue
        if now - it.get("at", now) > WAKE_MAX_AGE_S:
            give(it, row, f"알림이 {WAKE_MAX_AGE_S // 60}분 넘게 대기")
            continue
        if it.get("next_at", 0) > now or sent >= WAKE_PER_RUN or time.monotonic() - start > WAKE_BUDGET_S:
            keep.append(it)
            continue
        sent += 1
        status, _tries, codes, _found = actions.deliver_ws(it["ws"], WAKE_TEXT.format(card=it["card"]))
        row["wake"] = status
        if status in (actions.BLOCKED, actions.FAILED):
            it["tries"] = it.get("tries", 0) + 1
            why = ", ".join(dict.fromkeys(codes)) or "세션 터미널 없음"
            if it["tries"] >= WAKE_MAX_TRIES:
                give(it, row, f"{'입력 막힘' if status == actions.BLOCKED else '터미널 전송 실패'} {it['tries']}회({why})")
                continue
            it["next_at"] = int(now + WAKE_GAP_S)
            keep.append(it)
            row["wake"] = "blocked_retry" if status == actions.BLOCKED else "failed_retry"
            row["why"] = why
        out.append(row)
    save_wake(keep)
    return out


# ---- 한 회차 ---------------------------------------------------------------------------------
def once(dry_run=False):
    inbox, opath, rpath = paths()
    res = {"routed": [], "note": ""}
    try:
        st = os.stat(inbox)
    except FileNotFoundError:
        st = None
        res["note"] = "slack-inbox 없음"
    if st is not None:
        ino, off = main_inbox.load_offset(opath)
        if ino is None:
            off, res["note"] = st.st_size, "첫 실행: 파일 끝에서 시작"
        elif ino != st.st_ino or st.st_size < off:
            off, res["note"] = 0, "파일 교체·잘림: 처음부터"
        recs, end = main_inbox.read_from(inbox, off)
        replies = [r for r in recs if r.get("type") == "thread_reply" and r.get("parent_ts")]
        done = routed_ts(rpath)
        by_ts = thread_cards() if replies else {}
        for r in replies:
            if str(r.get("ts")) in done:
                continue
            got = route_guarded(r, by_ts, dry_run)
            if got is None:
                continue
            res["routed"].append(got)
            if not dry_run:
                line = json.dumps(dict(got, at=main_inbox.now_iso()), ensure_ascii=False) + "\n"
                fd = os.open(rpath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(fd, line.encode("utf-8"))
                finally:
                    os.close(fd)
                done.add(got["ts"])
                if got["to"] == "ws":
                    queue_wake(got["card"], ws_of(next(c for c in by_ts[got["parent_ts"]] if c["id"] == got["card"])), r.get("ts"), body(r))
        if not dry_run:
            main_inbox.save_offset(opath, st.st_ino, end)
    if not dry_run:
        wakes = run_wakes()
        if wakes:
            res["wake"] = wakes
    return res


# ---- 카드 스레드 등록·종료 -------------------------------------------------------------------
def open_thread(epic, ts, dry_run=False):
    if not TS_RE.match(ts):
        raise cards.Fail(2, "--ts 형식 오류. 조치: Slack 메시지 ts(숫자.숫자), slack_notify.sh 출력 'ok <ts>' 값")
    e = card_row(epic)
    if e is None or e.get("issue_type") != "epic":
        raise cards.Fail(4, f"epic {epic} 없음. 조치: bd list --type epic 로 id 확인")
    if not slug_of(e):
        raise cards.Fail(2, f"{epic} 에 feature:<slug> 라벨 없음. 조치: feature_open.sh 로 만든 epic 을 씀")
    label = THREAD + ts
    if label in (e.get("labels") or []):
        return {"epic": epic, "ts": ts, "added": False}
    if not dry_run:
        cards.bd("label", "add", epic, label)
    return {"epic": epic, "ts": ts, "added": not dry_run, "box": BOX_HEAD + slug_of(e)}


def attach(card, ts, ws="", dry_run=False):
    """카드에 Slack 스레드를 붙임(label slack-thread:<ts>, 작업 공간 카드는 ws:<경로> 도). 반복 실행 안전.
    같은 스레드가 이미 다른 카드에 붙어 있으면 거부(종료 5): 답이 어느 카드 것인지 가를 수 없으므로 확인 카드를 만들고 붙이지 않음"""
    if not TS_RE.match(ts):
        raise cards.Fail(2, "--ts 형식 오류. 조치: Slack 메시지 ts(숫자.숫자), slack_notify.sh 출력 'ok <ts>' 값")
    if not CARD_RE.match(card):
        raise cards.Fail(2, "--card 형식 오류. 조치: 작업판 카드 id(예 lrm-q5o.4)")
    if ws and (not os.path.isabs(ws) or "\n" in ws):
        raise cards.Fail(2, "--ws 형식 오류. 조치: 한 줄 절대 경로")
    row = card_row(card)
    if row is None:
        raise cards.Fail(4, f"카드 {card} 없음. 조치: bd list 로 id 확인")
    label, have = THREAD + ts, row.get("labels") or []
    others = [c for c in thread_cards().get(ts, []) if c.get("id") != card]
    if others:
        ids = ", ".join(c["id"] for c in others)
        cid = triage_card(ts, dry_run)
        if not dry_run:
            cards.bd("comments", "add", cid, f"스레드 {ts} 는 이미 {ids} 에 붙어 있어 {card} 에는 붙이지 않음. 조치: 어느 카드의 스레드인지 정해 같은 스레드가 한 카드에만 붙게 함")
        raise cards.Fail(EXIT_CONFLICT, f"스레드 {ts} 가 이미 {ids} 에 붙어 있음 — {card} 에 붙이지 않고 확인 카드 {cid} 에 남김")
    added = []
    if label not in have:
        added.append(label)
    if ws and (WS + ws) not in have and ws_of(row) is None:
        added.append(WS + ws)
    if not dry_run:
        for lb in added:
            cards.bd("label", "add", card, lb)
        if label in added:
            where = f" 담당 작업 공간: {ws}. 답은 이 카드 코멘트와 받은 편지함 {box_of(card)} 로 옴." if ws else f" 답은 이 카드 코멘트와 받은 편지함 {BOX_HEAD}{slug_of(row)} 로 옴." if slug_of(row) else ""
            cards.bd("comments", "add", card, f"Slack 스레드 {ts} 연결.{where}")
    return {"card": card, "ts": ts, "added": bool(added) and not dry_run, "labels": added}


def end_thread(card, dry_run=False):
    """작업 공간 반납 표시(label slack-thread-end). 이후 이 스레드의 답은 부모 기능 epic 으로 감. 반복 실행 안전"""
    if not CARD_RE.match(card):
        raise cards.Fail(2, "--card 형식 오류. 조치: 작업판 카드 id(예 lrm-q5o.4)")
    row = card_row(card)
    if row is None:
        raise cards.Fail(4, f"카드 {card} 없음. 조치: bd list 로 id 확인")
    if END in (row.get("labels") or []):
        return {"card": card, "added": False}
    if not dry_run:
        cards.bd("label", "add", card, END)
    return {"card": card, "added": not dry_run}


def main(argv):
    ap = argparse.ArgumentParser(prog="feedback_route.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=("open-thread", "attach", "end-thread"), help="기능 스레드 등록(open-thread)·카드에 스레드 붙임(attach)·작업 공간 스레드 종료 표시(end-thread). 없으면 --once")
    ap.add_argument("--epic")
    ap.add_argument("--card")
    ap.add_argument("--ts")
    ap.add_argument("--ws", default="", help="attach: 담당 작업 공간 절대 경로")
    ap.add_argument("--once", action="store_true", help="새 답글 한 번 전달")
    ap.add_argument("--dry-run", action="store_true", help="작업판·상자·기록을 바꾸지 않고 판정만")
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    try:
        if a.cmd == "open-thread":
            if not a.epic or not a.ts:
                raise cards.Fail(2, "open-thread 는 --epic·--ts 필수")
            out = open_thread(a.epic, a.ts, a.dry_run)
        elif a.cmd == "attach":
            if not a.card or not a.ts:
                raise cards.Fail(2, "attach 는 --card·--ts 필수")
            out = attach(a.card, a.ts, a.ws, a.dry_run)
        elif a.cmd == "end-thread":
            if not a.card:
                raise cards.Fail(2, "end-thread 는 --card 필수")
            out = end_thread(a.card, a.dry_run)
        elif a.once:
            out = once(a.dry_run)
        else:
            raise cards.Fail(2, "할 일 없음. 조치: --once, open-thread --epic <id> --ts <ts>, attach --card <id> --ts <ts>, end-thread --card <id> 중 하나")
    except cards.Fail as e:
        print(f"feedback_route: {e}", file=sys.stderr)
        return e.code
    except (OSError, ValueError) as e:
        print(f"feedback_route: 파일 실패({type(e).__name__}). 조치: LRM_HUB_DIR 권한 확인", file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    elif a.cmd == "open-thread":
        print(f"{out['epic']}: 스레드 {out['ts']} {'등록' if out['added'] else '이미 등록됨/건조'}")
    elif a.cmd == "attach":
        print(f"{out['card']}: 스레드 {out['ts']} {'연결' if out['added'] else '이미 연결됨/건조'}")
    elif a.cmd == "end-thread":
        print(f"{out['card']}: 스레드 종료 표시 {'함' if out['added'] else '이미 있음/건조'}")
    else:
        print(f"전달 {len(out['routed'])}건{' · ' + out['note'] if out['note'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
