#!/usr/bin/env python3
"""Slack 기능 스레드 피드백 → 그 기능의 작업판 epic 코멘트 + 기능 코디네이터 받은 편지함(hub-inbox/feat-<slug>.jsonl). 메인 중계 없음(11-17).

기능 스레드 등록: 기능 코디네이터가 Slack 에 처음 올린 보고의 ts 를 open-thread 로 epic 에 label slack-thread:<ts> 로 붙임.
전달(--once, 폴러 회차마다): slack-inbox.jsonl 의 새 thread_reply 중 parent_ts 가 등록된 스레드면
  - 열린 epic 1개: 원문 그대로 epic 코멘트 + 그 기능 상자에 kind feedback 한 줄(첨부는 저장 경로 그대로). 고쳐 쓰지 않음.
  - 애매함(같은 스레드에 epic 여럿, 또는 닫힌 epic 만): 메인 카드 '📥 기능 판별 필요'(label hub:main,feedback:triage)에 원문 코멘트.
  - 등록 안 된 스레드: 손대지 않음(기존 메인 전달 그대로).
  전달한 답글은 feedback-routed.jsonl 에 남고 slack_push 가 그 답글을 메인에 다시 보내지 않음.
첫 실행은 파일 끝에서 시작(옛 답글을 몰아 보내지 않음). 읽은 위치는 feedback-route.offset.

사용: feedback_route.py open-thread --epic <id> --ts <Slack ts> [--dry-run] [--json]
      feedback_route.py --once [--dry-run] [--json]
환경: LRM_HUB_DIR(기본 ~/.config/lrm-hubs) · BOARD_DIR · BOARD_BD
종료 코드: 0 성공(전달할 것 없음 포함), 2 인자 오류, 3 bd·파일 실패, 4 epic 없음"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import board_cards as cards  # noqa: E402
import main_inbox  # noqa: E402

THREAD = "slack-thread:"
FEATURE = "feature:"
BOX_HEAD = "feat-"
TRIAGE_LABELS = "hub:main,feedback:triage"
TRIAGE_TITLE = "📥 기능 판별 필요: Slack 스레드 {ts}"
TS_RE = re.compile(r"^[0-9]{1,12}\.[0-9]{1,9}$")
SOURCE = "feedback_route"


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


def thread_epics():
    """parent_ts → [epic 행]. 열린·닫힌 epic 모두(닫힌 것만 걸리면 애매로 봄)"""
    out = {}
    for r in cards.bd_json("list", "--all", "--type", "epic", "--limit", "0") or []:
        if not isinstance(r, dict):
            continue
        for lb in r.get("labels") or []:
            if lb.startswith(THREAD):
                out.setdefault(lb[len(THREAD):], []).append(r)
    return out


def slug_of(epic):
    return next((lb[len(FEATURE):] for lb in epic.get("labels") or [] if lb.startswith(FEATURE)), None)


def body(rec):
    """원문 + 첨부 저장 경로. 내용은 바꾸지 않음"""
    text = str(rec.get("text") or "")
    files = [str(p) for p in rec.get("files") or []]
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
                         f"Slack 스레드 {ts} 의 답글이 어느 기능 것인지 정해지지 않음. 조치: 맞는 epic 에 feedback_route.py open-thread 로 다시 걸고 이 카드를 닫음",)
    return made["id"]


def route(rec, epics, dry_run):
    """한 답글의 전달 결과 dict, 기능 스레드가 아니면 None"""
    parent = str(rec.get("parent_ts") or "")
    hits = epics.get(parent)
    if not hits:
        return None
    opened = [e for e in hits if e.get("status") != "closed"]
    text = body(rec)
    if len(opened) == 1 and len(hits) == 1 and slug_of(opened[0]):
        e = opened[0]
        box = BOX_HEAD + slug_of(e)
        if not dry_run:
            cards.bd("comments", "add", e["id"], f"Slack 피드백(원문): {text}")
            main_inbox.append("feedback", text, SOURCE, ref=f"{e['id']} slack:{rec.get('ts')}", box=box)
        return {"ts": str(rec.get("ts")), "parent_ts": parent, "to": "feature", "epic": e["id"], "box": box}
    why = "epic 여럿" if len(hits) > 1 else ("닫힌 epic" if not opened else "epic 에 feature: 라벨 없음")
    cid = triage_card(parent, dry_run)
    if not dry_run:
        cards.bd("comments", "add", cid, f"Slack 피드백(원문, {why}: {', '.join(e['id'] for e in hits)}): {text}")
    return {"ts": str(rec.get("ts")), "parent_ts": parent, "to": "triage", "card": cid, "why": why}


def once(dry_run=False):
    inbox, opath, rpath = paths()
    try:
        st = os.stat(inbox)
    except FileNotFoundError:
        return {"routed": [], "note": "slack-inbox 없음"}
    ino, off = main_inbox.load_offset(opath)
    note = ""
    if ino is None:
        off, note = st.st_size, "첫 실행: 파일 끝에서 시작"
    elif ino != st.st_ino or st.st_size < off:
        off, note = 0, "파일 교체·잘림: 처음부터"
    recs, end = main_inbox.read_from(inbox, off)
    replies = [r for r in recs if r.get("type") == "thread_reply" and r.get("parent_ts")]
    done, out = routed_ts(rpath), []
    epics = thread_epics() if replies else {}
    for r in replies:
        if str(r.get("ts")) in done:
            continue
        res = route(r, epics, dry_run)
        if res is None:
            continue
        out.append(res)
        if not dry_run:
            line = json.dumps(dict(res, at=main_inbox.now_iso()), ensure_ascii=False) + "\n"
            fd = os.open(rpath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            done.add(res["ts"])
    if not dry_run:
        main_inbox.save_offset(opath, st.st_ino, end)
    return {"routed": out, "note": note}


def open_thread(epic, ts, dry_run=False):
    if not TS_RE.match(ts):
        raise cards.Fail(2, "--ts 형식 오류. 조치: Slack 메시지 ts(숫자.숫자), slack_notify.sh 출력 'ok <ts>' 값")
    rows = cards.bd_json("list", "--all", "--id", epic, "--limit", "0") or []
    e = next((r for r in rows if r.get("id") == epic), None)
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


def main(argv):
    ap = argparse.ArgumentParser(prog="feedback_route.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=("open-thread",), help="기능 스레드 등록. 없으면 --once")
    ap.add_argument("--epic")
    ap.add_argument("--ts")
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
        elif a.once:
            out = once(a.dry_run)
        else:
            raise cards.Fail(2, "할 일 없음. 조치: --once 또는 open-thread --epic <id> --ts <ts>")
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
    else:
        print(f"전달 {len(out['routed'])}건{' · ' + out['note'] if out['note'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
