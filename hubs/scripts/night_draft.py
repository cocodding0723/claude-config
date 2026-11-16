#!/usr/bin/env python3
"""야간 작업 15시 초안 묶음: '야간:' 표시 항목 중 미승인 건을 지시별 결정 카드로 보내고, 카드 답을 읽어 승인을 장부에 기록함.

흐름(한 번 실행 = 한 회차, 기본 dry-run):
  1. 승인 수확: 이전에 보낸 초안 카드(night-drafts.jsonl)의 답을 읽어 '오늘 밤 진행 승인'(1번)이면 task_ledger.approve_night 로 기록. 보류·무응답은 기록 없음.
  2. 초안 전송: 아직 승인 안 된 야간 항목마다 카드 1장(= 지시 1건, 같은 날 같은 항목은 1번). 승인하면 위 초안·추천안대로 진행, 추천안 자동 채택은 없음(설계 결정4 C).
     안 본 항목은 다음 날 새 결정 번호로 다시 감. 카드 전송은 hub_report.py add --kind decide 에 맡김(양식 A·비밀 값 검사·중복 id 제거를 그대로 씀).
  3. 첨부: 초안 묶음 html 1개를 이번 회차 첫 카드에만 붙임(나머지는 --no-doc, 카드 본문만으로 판단 가능). --no-doc 이면 html 도 안 만듦.
dry-run 은 hub_report --dry-run 으로 카드 양식 검사까지만 하고 outbox·기록·장부를 쓰지 않음. --send 일 때만 기록함(Slack 전송은 report_sender 의 몫).
결정 번호: D-NQ<작업 번호 숫자><MMDD>. 같은 항목·같은 날 재실행은 기록(night-drafts.jsonl)으로 건너뜀.

환경: LRM_TASKS 장부 · LRM_HUB_DIR 작업 폴더(기록·초안 html·답 기록) · LRM_REPORT_OUTBOX(hub_report 와 같음) · LRM_NIGHT_NOW 현재 시각 epoch 초(시험용)
종료 코드: 0 성공(보낼 것 없음 포함), 1 파일 읽기·쓰기·카드 기록 실패, 2 인자 오류
"""
import argparse
import datetime
import html
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 링크·복사 설치에서도 같은 폴더 모듈을 찾음
import answer_store  # noqa: E402
import hub_status  # noqa: E402
import task_ledger as tl  # noqa: E402
from ledger import LedgerError  # noqa: E402

# ---------- 카드 문구·값(이 블록 한 곳) ----------
DID_PREFIX = "D-NQ"
TITLE_MAX = 40  # hub_report.MAX_TITLE
FALLBACK_HUB = "main"  # 장부 담당이 사용자·미정이면 이 허브 이름으로 올림
APPROVE, HOLD = 1, 2  # 카드 선택지 번호
APPROVE_LABEL, HOLD_LABEL = "오늘 밤 진행 승인", "보류"
APPROVE_RESULT = "오늘 23시 이후 담당 허브가 위 초안·추천안대로 진행"
HOLD_RESULT = "오늘 밤 진행 안 함. 내일 15시 묶음에 다시 올림"
REVERSIBLE = "쉬움"  # 승인·보류 모두 되돌리기 쉬움(승인은 장부 한 칸, 밤 전달은 night.mode 로 멈춤)
TERMS = "용어: 야간 진행 — 23~07시 사이 승인된 일만 구현·draft PR·CI·문서까지 처리"
QUESTIONS_SHOWN = 3
UNKNOWN = "미확인"
LINE_MAX = 200  # decide_card.LINE_MAX
LOG_NAME = "night-drafts.jsonl"
DOC_DIR = "night-drafts"
HUB_REPORT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "hub_report.py")
OPEN_STATES = tuple(s for s in tl.STATES if s not in (tl.DONE, tl.DROPPED))


def hub_dir() -> str:
    return os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")


def now_ts() -> float:
    return float(os.environ.get("LRM_NIGHT_NOW") or time.time())


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---------- 초안 판정 ----------

def draft_rows(rows: list) -> list:
    """초안 대상: 야간 표시 + 미승인 + 끝나지 않은 행"""
    return [r for r in rows if tl.is_night(r) and not tl.night_approved(r) and r.get("state") in OPEN_STATES]


def missing_parts(row: dict) -> list:
    """초안이 덜 채워진 칸. 하나라도 있으면 카드가 보류를 추천함"""
    out = []
    if not row.get("hub") or row["hub"] in tl.RESERVED_HUBS:
        out.append("담당 허브")
    if not row.get("goal"):
        out.append("목표")
    if not row.get("done_when"):
        out.append("완료 기준")
    return out


def blocking_questions(row: dict) -> list:
    """밤에 되물을 수 없어 답이 필요한 질문: 답 없음 + 되돌리기 쉬움 아님"""
    return [q for q in row.get("questions", []) if q.get("answer") is None and q.get("revert") != "쉬움"]


def recommend_for(row: dict) -> int:
    return HOLD if missing_parts(row) or blocking_questions(row) else APPROVE


def card_id(task: str, now: float) -> str:
    digits = re.sub(r"\D", "", task)
    return f"{DID_PREFIX}{digits}{datetime.datetime.fromtimestamp(now).strftime('%m%d')}"


def after_text(row: dict) -> str:
    items = [f"{a['kind']}:{a['ref']}" for a in row.get("after", [])] + [f"task:{n}" for n in tl.split_ids(row.get("needs"))]
    return ", ".join(items) or "없음"


def build_card(row: dict, now: float) -> dict:
    """결정 카드 한 장의 값. 양식 A: 제목 → 배경 → 질문 → 이유 → 용어. 본문 8줄·1000자 안(decide_card 상한)"""
    task, goal = row["id"], row.get("goal") or ""
    title = clip(f"야간 진행 승인 {task}" + (f": {goal}" if goal else ""), TITLE_MAX)
    hub = row.get("hub") if hub_status.SLUG_RE.match(row.get("hub") or "") and len(row["hub"]) <= 24 else FALLBACK_HUB
    background = clip(f"오늘 밤 담당 허브가 진행할 초안. 담당 {row.get('hub') or UNKNOWN} · 완료 기준 {row.get('done_when') or UNKNOWN} · 선행 {after_text(row)}", LINE_MAX)
    lines = [title, f"배경: {background}"]
    questions = row.get("questions", [])
    for q in questions[:QUESTIONS_SHOWN]:
        lines.append(clip(f"질문: {q['q']} — 추천 {q.get('rec') or UNKNOWN}({q['revert']})" + (f" · 답 {q['answer']}" if q.get("answer") is not None else ""), LINE_MAX))
    if len(questions) > QUESTIONS_SHOWN:
        lines.append(f"질문 외 {len(questions) - QUESTIONS_SHOWN}건은 첨부 초안 묶음 참고")
    rec = recommend_for(row)
    gaps, hard = missing_parts(row), blocking_questions(row)
    why = ("초안에 " + "·".join(gaps) + " 없음" if gaps else "") + (" · " if gaps and hard else "") + (f"답 없는 질문 {len(hard)}건이 되돌리기 쉬움 아님" if hard else "")
    lines.append(clip("이유: " + (f"{why}이라 보류 추천" if rec == HOLD else "초안이 갖춰졌고 막힐 질문이 없어 승인 추천") + ". 추천안 자동 채택은 없고 승인해야 진행", LINE_MAX))
    lines.append(TERMS)
    return {"hub": hub, "task": task, "did": card_id(task, now), "text": "\n".join(lines), "recommend": rec,
            "options": f"{APPROVE}={APPROVE_LABEL} => {APPROVE_RESULT},{HOLD}={HOLD_LABEL} => {HOLD_RESULT}"}


def render_bundle(day: str, rows: list) -> str:
    """초안 묶음 html(첨부용). 지시마다 목표·담당·완료 기준·선행·질문 표. 값은 모두 escape"""
    esc = html.escape
    parts = [f"<h1>야간 작업 초안 {esc(day)}</h1><p>승인한 항목만 오늘 밤 진행한다. 추천안은 승인 시 그대로 따른다.</p>"]
    for r in rows:
        qs = "".join(f"<tr><td>{esc(q['q'])}</td><td>{esc(q.get('rec') or UNKNOWN)}</td><td>{esc(q['revert'])}</td><td>{esc(q['answer']) if q.get('answer') is not None else '없음'}</td></tr>"
                     for q in r.get("questions", [])) or "<tr><td colspan=4>질문 없음</td></tr>"
        parts.append(f"<section><h2>{esc(r['id'])} {esc(r.get('goal') or r['text'][:60])}</h2><ul><li>담당 허브: {esc(r.get('hub') or UNKNOWN)}</li>"
                     f"<li>완료 기준: {esc(r.get('done_when') or UNKNOWN)}</li><li>선행 조건: {esc(after_text(r))}</li><li>원문: {esc(r['text'])}</li></ul>"
                     f"<table><tr><th>질문</th><th>추천안</th><th>되돌리기</th><th>답</th></tr>{qs}</table></section>")
    css = ("body{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222;background:#fff}"
           "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
           "@media(prefers-color-scheme:dark){body{color:#ddd;background:#1b1b1b}td,th{border-color:#555}}")
    return f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>야간 작업 초안 {esc(day)}</title><style>{css}</style>" + "".join(parts)


# ---------- 기록·답 읽기 ----------

def log_path() -> str:
    return os.path.join(hub_dir(), LOG_NAME)


def read_log() -> list:
    out = []
    try:
        with open(log_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("task") and d.get("did"):
                    out.append(d)
    except FileNotFoundError:
        pass
    return out


def append_log(rec: dict) -> None:
    with open(log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def approved_pick(rec: dict) -> bool:
    return rec.get("action") == "pick" and str(rec.get("pick")) == str(APPROVE)


def harvest(path: str, rows: list, now: float, dry: bool) -> list:
    """보낸 초안 카드의 답 → 승인 기록. 반환: 승인한(dry 면 승인할) 작업 번호"""
    store, log, done = answer_store.AnswerStore(hub_dir()), read_log(), []
    for row in draft_rows(rows):
        for rec in (d for d in log if d["task"] == row["id"]):
            for card in store.cards(rec["did"]):
                if card["by"] and approved_pick(store.find_answer(rec["did"], card["ts"])[1]):
                    tl.approve_night(path, row["id"], rec["did"], now, dry)
                    done.append(row["id"])
                    break
            if row["id"] in done:
                break
    return done


# ---------- 전송 ----------

def hub_report_add(card: dict, doc: str, dry: bool) -> tuple:
    """hub_report.py add --kind decide 호출. 반환 (성공, 메시지). 검사 실패 사유는 hub_report 가 '원인 — 조치' 로 돌려줌"""
    cmd = [sys.executable, HUB_REPORT, "add", "--hub", card["hub"], "--task", card["task"], "--kind", "decide", "--text", card["text"],
           "--options", card["options"], "--recommend", str(card["recommend"]), "--decision-id", card["did"], "--reversible", REVERSIBLE, "--json"]
    cmd += ["--file", doc] if doc else ["--no-doc"]
    if dry:
        cmd.append("--dry-run")
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0, (res.stdout if res.returncode == 0 else res.stderr).strip()


def run(path: str, send: bool, no_doc: bool, now: float) -> dict:
    """한 회차. 반환 {approved, cards: [{task, did, ok, note}], doc, skipped}"""
    rows = tl.read_rows(path)
    day = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    approved = harvest(path, rows, now, not send)
    if approved and send:
        rows = tl.read_rows(path)  # 승인된 행은 초안 대상에서 빠짐
    pending = [r for r in draft_rows(rows) if r["id"] not in approved]
    sent_today = {d["task"] for d in read_log() if d.get("day") == day}
    todo = [r for r in pending if r["id"] not in sent_today]
    doc, cards = "", []
    if todo and not no_doc:
        doc = os.path.join(hub_dir(), DOC_DIR, f"{day}.html")
        if send:
            os.makedirs(os.path.dirname(doc), exist_ok=True)
            with open(doc, "w", encoding="utf-8") as f:
                f.write(render_bundle(day, todo))
    for i, row in enumerate(todo):
        card = build_card(row, now)
        attach = doc if i == 0 and doc and send else ""  # 묶음 html 은 첫 카드에만. dry-run 은 파일이 없으므로 --no-doc 로 양식만 검사
        ok, note = hub_report_add(card, attach, not send)
        if ok and send:
            append_log({"day": day, "task": row["id"], "did": card["did"], "at": int(now)})
        cards.append({"task": row["id"], "did": card["did"], "ok": ok, "note": note, "recommend": card["recommend"], "text": card["text"]})
    return {"approved": approved, "cards": cards, "doc": doc if send and todo else "", "skipped": sorted(sent_today & {r["id"] for r in pending})}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="night_draft.py", formatter_class=argparse.RawDescriptionHelpFormatter, description="야간 작업 15시 초안 묶음 전송·승인 수확", epilog=__doc__)
    p.add_argument("--send", action="store_true", help="실제로 outbox 에 카드를 기록하고 승인을 장부에 씀(기본은 dry-run: 양식 검사만)")
    p.add_argument("--no-doc", action="store_true", help="초안 묶음 html 을 만들지도 붙이지도 않음")
    p.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        res = run(tl.tasks_path(), args.send, args.no_doc, now_ts())
    except (LedgerError, OSError) as err:
        print(f"오류: {err} — LRM_TASKS·LRM_HUB_DIR 경로·권한 확인", file=sys.stderr)
        return 1
    failed = [c for c in res["cards"] if not c["ok"]]
    if args.json:
        print(json.dumps({"dry_run": not args.send, **res}, ensure_ascii=False, indent=2))
    else:
        tag = "" if args.send else "[dry-run] "
        print(f"{tag}승인 {len(res['approved'])}건 · 카드 {len(res['cards'])}장(실패 {len(failed)}) · 오늘 이미 보냄 {len(res['skipped'])}건")
        for c in res["cards"]:
            print(f"  {c['task']} {c['did']} {'ok' if c['ok'] else '실패: ' + c['note']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
