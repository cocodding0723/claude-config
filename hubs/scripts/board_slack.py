#!/usr/bin/env python3
"""작업판 결정 카드 ↔ Slack 결정 카드 연결. Slack 봇을 다시 켤 때 결정의 원천을 작업판 하나로 두기 위한 연결 코드(11-17).

보내기: 열린 kind:decision 카드 → slack_notify.sh --kind decide 번호 카드. 결정 번호는 카드 id 에서 만들고(D-B + 영문·숫자),
       보낸 뒤 카드에 label slack:<결정 번호> 를 붙여 다시 보내지 않음.
설명서: 카드 설명의 '설명서: <html>' 줄을 보내기 직전에 다시 검사해(board_cards.doc_gaps) 카드 스레드에 첨부하고, 모바일 미리보기 PNG 도 붙임(실패해도 카드는 유지).
답 받기: 버튼 답(slack_socket.py) → 결정 번호로 카드를 찾아 board-answer 와 같은 처리(원문 코멘트 + 닫기).
       직접 입력이 질문이면 코멘트만 남기고 카드는 열어 둠. 작업판 카드가 아닌 결정 번호는 손대지 않음(옛 허브·메인 전달 그대로).
자동 보내기 스위치: <LRM_HUB_DIR>/.board-slack.on 파일이 있을 때만 board-ask 가 새 결정 카드를 바로 보냄. 기본 꺼짐(봇 꺼짐).

사용: board_slack.py push <카드 id> [--dry-run] [--json]
      board_slack.py answer <결정 번호> <답 원문> [--json]      (시험·수동용. 평소엔 slack_socket.py 가 부름)
환경: LRM_HUB_DIR(기본 ~/.config/lrm-hubs) · LRM_NOTIFY(기본 <LRM_HUB_DIR>/slack_notify.sh) · BOARD_DIR · BOARD_BD
종료 코드: 0 성공(이미 보냄·작업판 카드 아님 포함), 2 인자·카드 내용 오류(원인·조치 출력), 3 bd·전송 실패, 4 카드 없음"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

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
    args = ["--kind", "decide", "--tag", "board", "--options", opts, "--decision-id", decision_id(card["id"])]
    if d["recommend"] in names:
        args += ["--recommend", str(names.index(d["recommend"]) + 1)]
    else:
        raise cards.Fail(2, f"{card['id']} 추천 없음. 조치: board-ask --recommend <선택지> 로 다시 만들기")
    text = "\n".join(body)
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
    args = notify_args(card)
    clash = [r["id"] for r in cards.bd_json("list", "--all", "--label", LABEL + did, "--limit", "0") or [] if r.get("id") != card_id]
    if clash:
        raise cards.Fail(2, f"결정 번호 {did} 를 {clash[0]} 가 이미 씀. 조치: 카드 id 를 확인하고 수동으로 slack_notify.sh 사용")
    if dry_run:
        return {"id": card_id, "sent": False, "why": "dry-run", "decisionId": did, "args": args}
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
    except cards.Fail:
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
