#!/usr/bin/env python3
"""메인 대상 자동 알림 → 작업판 카드 코멘트. 메인 입력창·받은 편지함에는 사용자만 쓰게 하려는 것(11-17).

켜는 법: <LRM_HUB_DIR>/main-route 파일 첫 줄이 board 이면 main_inbox.append(상자 main)가 이 모듈로 보냄.
       파일이 없거나 다른 값이면 옛 경로(hub-inbox/main.jsonl) 그대로. 작업판 쓰기가 실패해도 옛 경로로 떨어져 알림을 잃지 않음.
카드 찾기(앞에서부터): ① 결정 번호 D-… → label slack:<번호> 카드 ② PR 번호(#n·PR#n) → label pr:<n> 카드
  ③ 본문·ref 의 작업판 카드 id ④ 없으면 종류별 모음 카드 '📥 자동 알림: <종류>'(label kind:alert,alert:<종류>,hub:main, 하나만 만들고 재사용).
같은 알림(종류·ref·본문이 같음)은 board-notify.jsonl 최근 500줄 안에서 한 번만.

사용: board_notify.py post --kind K --source S [--ref R] [--dry-run] [--json] <본문|->
환경: LRM_HUB_DIR(기본 ~/.config/lrm-hubs) · BOARD_DIR · BOARD_BD · BOARD_ID_RE(카드 id 정규식, 기본 lrm-[0-9a-z]{3,8})
종료 코드: 0 기록(중복 건너뜀 포함), 2 인자 오류, 3 bd·파일 실패"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import board_cards as cards  # noqa: E402

ROUTE_FILE, LOG_FILE = "main-route", "board-notify.jsonl"
ROUTE_BOARD = "board"
DEDUP_LINES = 500
ALERT_TITLE = "📥 자동 알림: {kind}"
ALERT_LABELS = "kind:alert,alert:{kind},hub:main"
DID_RE = re.compile(r"\bD-[A-Za-z0-9]{1,16}\b")
PR_RE = re.compile(r"(?:PR\s*#?|#)([0-9]{1,6})\b")
COMMENT_MAX = 6000


def hub_dir():
    return os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")


def enabled(d=None):
    try:
        with open(os.path.join(d or hub_dir(), ROUTE_FILE), encoding="utf-8") as f:
            return f.readline().strip() == ROUTE_BOARD
    except OSError:
        return False


def card_re():
    return re.compile(r"\b(" + (os.environ.get("BOARD_ID_RE") or r"lrm-[0-9a-z]{3,8}") + r")\b")


def labeled(label):
    rows = cards.bd_json("list", "--all", "--label", label, "--limit", "0") or []
    live = [r["id"] for r in rows if isinstance(r, dict) and r.get("status") != "closed"]
    return live[0] if live else (rows[0]["id"] if rows and isinstance(rows[0], dict) else None)


def exists(cid):
    rows = cards.bd_json("list", "--all", "--id", cid, "--limit", "0") or []
    return any(isinstance(r, dict) and r.get("id") == cid for r in rows)


def alert_card(kind, dry_run):
    title = ALERT_TITLE.format(kind=kind)
    rows = cards.bd_json("list", "--label", f"alert:{kind}", "--limit", "0") or []
    hit = next((r["id"] for r in rows if isinstance(r, dict) and r.get("status") != "closed"), None)
    if hit or dry_run:
        return hit or "(새 카드)"
    made = cards.bd_json("create", title, "-t", "task", "-p", "3", "-l", ALERT_LABELS.format(kind=kind), "-d",
                         f"메인 대상 자동 알림({kind}) 중 맞는 카드를 못 찾은 것을 모음. 읽으면 코멘트를 보고 맞는 카드로 옮기거나 닫음")
    return made["id"]


def target(kind, text, ref, dry_run=False):
    """(카드 id, 찾은 방법)"""
    hay = f"{ref or ''}\n{text}"
    for did in DID_RE.findall(hay):
        cid = labeled(f"slack:{did}")
        if cid:
            return cid, "decision"
    for n in PR_RE.findall(hay):
        cid = labeled(f"pr:{n}")
        if cid:
            return cid, "pr"
    for cid in card_re().findall(hay):
        if exists(cid):
            return cid, "id"
    return alert_card(kind, dry_run), "alert"


def seen(path, key):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-DEDUP_LINES:]
    except OSError:
        return False
    for ln in lines:
        try:
            if json.loads(ln).get("id") == key:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def post(kind, text, source, ref=None, rid=None, d=None, dry_run=False):
    """카드에 코멘트 한 줄. 반환 {status: board|duplicate, card, how, id}. 실패는 cards.Fail·OSError"""
    if not text or not text.strip() or not source:
        raise ValueError("source·본문 필요")
    d = d or hub_dir()
    log = os.path.join(d, LOG_FILE)
    if rid is None:
        import main_inbox  # 같은 멱등 키 규칙(main_inbox 가 이 모듈을 부르므로 호출 때 불러옴)
        rid = main_inbox.make_id(source, kind, text, ref)
    key = rid
    if seen(log, key):
        return {"status": "duplicate", "id": key}
    cid, how = target(kind, text, ref, dry_run)
    note = f"[{source}·{kind}] {text.strip()}"
    if ref:
        note += f"\n(ref {ref})"
    if len(note) > COMMENT_MAX:
        note = note[:COMMENT_MAX - 1] + "…"
    if dry_run:
        return {"status": "dry-run", "card": cid, "how": how, "id": key, "line": note}
    cards.bd("comments", "add", cid, note)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps({"id": key, "card": cid, "how": how, "kind": kind, "source": source}, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return {"status": "board", "card": cid, "how": how, "id": key}


def main(argv):
    ap = argparse.ArgumentParser(prog="board_notify.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post", help="알림 한 건을 맞는 카드에 코멘트")
    p.add_argument("--kind", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--ref")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("text", help="본문. '-' 면 표준 입력")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    text = sys.stdin.read() if a.text == "-" else a.text
    try:
        out = post(a.kind, text, a.source, a.ref, dry_run=a.dry_run)
    except ValueError as e:
        print(f"board_notify: {e}. 조치: --help", file=sys.stderr)
        return 2
    except cards.Fail as e:
        print(f"board_notify: {e}", file=sys.stderr)
        return 3
    except OSError as e:
        print(f"board_notify: 기록 실패({type(e).__name__}). 조치: LRM_HUB_DIR 권한 확인", file=sys.stderr)
        return 3
    print(json.dumps(out, ensure_ascii=False) if a.json else
          (f"이미 보냄 {out['id']}" if out["status"] == "duplicate" else f"{out['card']} 에 기록({out['how']})"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
