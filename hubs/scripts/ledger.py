#!/usr/bin/env python3
"""피드백 장부(TSV) 도구: 항목 추가·상태 변경·목록.

장부 열: id · 접수 · 요청 · 상태 · 담당 · 진행·증거 · 갱신. 증거는 진행·증거 열 끝에 ' · 증거: …' 로 붙임.
쓰기는 같은 폴더 임시 파일 → os.replace 로 원자적이고, flock 으로 동시 쓰기를 직렬화함.
완료는 증거 없이 못 바꿈(증거 없는 완료 금지).

환경: LRM_LEDGER 장부 경로(기본 ~/.config/lrm-hubs/feedback-ledger.tsv)
종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(증거 없는 완료 포함), 3 대상 없음·id 중복
"""
import argparse
import contextlib
import csv
import fcntl
import io
import json
import os
import sys
import tempfile
import time

DEFAULT_PATH = os.path.expanduser("~/.config/lrm-hubs/feedback-ledger.tsv")
HEADER = ["id", "접수", "요청(원문 요약)", "상태", "담당", "진행·증거", "갱신"]
STATES = ("접수", "진행", "완료", "보류")
DONE = "완료"
EMOJI = {"접수": "📥", "진행": "🔄", "완료": "✅", "보류": "⏸️"}
EVIDENCE_SEP = " · 증거: "
STAMP = "%Y-%m-%d %H:%M"
DATE = "%m-%d"
EXIT_IO, EXIT_USAGE, EXIT_MISSING = 1, 2, 3


class LedgerError(Exception):
    """원인 — 조치 문장과 종료 코드를 함께 나르는 오류"""

    def __init__(self, msg: str, code: int = EXIT_USAGE) -> None:
        super().__init__(msg)
        self.code = code


def ledger_path() -> str:
    return os.environ.get("LRM_LEDGER") or DEFAULT_PATH


def clean(text: str) -> str:
    """TSV 한 칸에 들어가도록 탭·줄바꿈을 공백으로 바꾸고 다듬음"""
    return " ".join(str(text).split())


def split_progress(text: str) -> tuple:
    """진행·증거 열 → (진행 설명, 증거). 증거 표식이 없으면 증거는 빈 문자열"""
    head, sep, tail = text.rpartition(EVIDENCE_SEP)
    if sep:
        return head, tail
    bare = EVIDENCE_SEP.lstrip(" ·")
    return ("", text[len(bare):]) if text.startswith(bare) else (text, "")


def join_progress(note: str, evidence: str) -> str:
    """split_progress 의 역. 설명이 없으면 '증거: …' 만 남김"""
    if not evidence:
        return note
    return note + EVIDENCE_SEP + evidence if note else EVIDENCE_SEP.lstrip(" ·") + evidence


COLS = ("id", "received", "request", "state", "owner", "progress", "updated")


def parse(text: str) -> list:
    """TSV → 칸 목록. 인용된 칸의 탭·줄바꿈은 한 칸으로 읽음(스프레드시트 내보내기 대응)"""
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter="\t", quotechar='"', strict=True))
    except csv.Error as err:
        raise LedgerError(f"장부 인용 부호 오류({err}) — 따옴표로 시작하는 칸은 닫는 따옴표 필요, 해당 행 수정 후 재시도")


def read_rows(path: str) -> list:
    """장부 → 항목 dict 목록(헤더 제외). 파일이 없으면 빈 목록. 열이 모자란 줄은 빈 칸으로 채우고,
    7열을 넘는 줄은 쓰면 데이터가 잘리므로 거부"""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            records = parse(f.read())
    except FileNotFoundError:
        return []
    except OSError as err:
        raise LedgerError(f"장부 읽기 실패({err.strerror}) — LRM_LEDGER 경로·권한 확인", EXIT_IO)
    rows = []
    for n, cells in enumerate(records[1:], 1):
        if not any(cells):
            continue
        if len(cells) > len(HEADER):
            raise LedgerError(f"장부 {n}번째 항목이 {len(cells)}열 — {len(HEADER)}열만 지원. 칸 안의 탭은 따옴표로 묶거나 제거")
        row = dict(zip(COLS, (cells + [""] * len(HEADER))[: len(HEADER)]))
        row["note"], row["evidence"] = split_progress(row["progress"])
        rows.append(row)
    return rows


def check_rows(rows: list) -> None:
    """수동 편집으로 생긴 잘못된 행 거부(set 의 검사는 손으로 고친 장부에 닿지 않음). 증거 없는 완료·알 수 없는 상태·id 비움·중복"""
    seen, bad = set(), []
    for r in rows:
        why = ("id 비어 있음" if not r["id"] else "id 중복" if r["id"] in seen else
               f"알 수 없는 상태 '{r['state']}'" if r["state"] not in STATES else
               "증거 없는 완료" if r["state"] == DONE and not r["evidence"].strip() else "")
        seen.add(r["id"])
        if why:
            bad.append(f"{r['id'] or '(id 없음)'}: {why}")
    if bad:
        raise LedgerError("장부 잘못된 행 — " + " · ".join(bad) + ". 완료는 ledger.py set <id> --state 완료 --evidence … 로, 그 밖은 파일에서 고침")


def render(rows: list) -> str:
    """항목 → TSV. 탭·줄바꿈·따옴표가 든 칸만 인용해 parse 와 왕복이 맞음"""
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", quotechar='"', lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r[c] for c in COLS])
    return out.getvalue()


def write_atomic(path: str, text: str) -> None:
    """같은 폴더 임시 파일에 쓰고 fsync 뒤 교체. 도중에 죽어도 옛 파일 또는 새 파일 하나만 남음"""
    d = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            with contextlib.suppress(OSError):
                os.chmod(tmp, os.stat(path).st_mode & 0o777)  # 기존 권한 유지
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as err:
        raise LedgerError(f"장부 쓰기 실패({err.strerror}) — 폴더 권한·디스크 확인", EXIT_IO)


@contextlib.contextmanager
def locked(path: str):
    """읽기-수정-쓰기 구간을 서로 막는 배타 잠금. 잠금 파일은 장부 옆 .lock"""
    try:
        with open(path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
    except OSError as err:
        raise LedgerError(f"잠금 실패({err.strerror}) — 장부 폴더 권한 확인", EXIT_IO)


def add(path: str, rid: str, req: str, owner: str, now: float, dry: bool) -> dict:
    rid, req, owner = clean(rid), clean(req), clean(owner)
    if not rid or not req:
        raise LedgerError("--id·--req 는 비울 수 없음 — 값을 지정")
    with locked(path):
        rows = read_rows(path)
        if any(r["id"] == rid for r in rows):
            raise LedgerError(f"id 중복: {rid} — 상태 변경은 set {rid} --state …", EXIT_MISSING)
        t = time.localtime(now)
        row = {"id": rid, "received": time.strftime(DATE, t), "request": req, "state": "접수", "owner": owner,
               "progress": "", "updated": time.strftime(STAMP, t), "note": "", "evidence": ""}
        if not dry:
            write_atomic(path, render(rows + [row]))
    return row


def set_state(path: str, rid: str, state: str, evidence, note, now: float, dry: bool) -> dict:
    if state not in STATES:
        raise LedgerError(f"상태 값 오류: {state} — {'|'.join(STATES)} 중 하나")
    evidence = clean(evidence) if evidence is not None else None
    if state == DONE and not evidence:
        raise LedgerError(f"증거 없는 완료 불가 — set {rid} --state 완료 --evidence <경로·커밋 해시> 로 지정")
    with locked(path):
        rows = read_rows(path)
        row = next((r for r in rows if r["id"] == rid), None)
        if row is None:
            raise LedgerError(f"id 없음: {rid} — list 로 id 확인, 새 항목은 add", EXIT_MISSING)
        row["state"] = state
        if note is not None:
            row["note"] = clean(note)
        if evidence is not None:
            row["evidence"] = evidence
        row["progress"] = join_progress(row["note"], row["evidence"])
        row["updated"] = time.strftime(STAMP, time.localtime(now))
        if not dry:
            write_atomic(path, render(rows))
    return row


def set_owner(path: str, rid: str, owner: str, now: float, dry: bool) -> dict:
    """담당만 바꿈(허브 분할·병합 이관). 상태·진행은 유지"""
    owner = clean(owner)
    if not owner:
        raise LedgerError("--to 는 비울 수 없음 — 새 담당 지정")
    with locked(path):
        rows = read_rows(path)
        row = next((r for r in rows if r["id"] == rid), None)
        if row is None:
            raise LedgerError(f"id 없음: {rid} — list 로 id 확인", EXIT_MISSING)
        row["owner"] = owner
        row["updated"] = time.strftime(STAMP, time.localtime(now))
        if not dry:
            write_atomic(path, render(rows))
    return row


def public(row: dict) -> dict:
    return {"id": row["id"], "received": row["received"], "request": row["request"], "state": row["state"],
            "owner": row["owner"], "note": row["note"], "evidence": row["evidence"], "updated": row["updated"]}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"오류: {message} — --help 참고\n")
        sys.exit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(
        prog="ledger.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="피드백 장부 도구. 장부 경로는 환경 변수 LRM_LEDGER(기본 ~/.config/lrm-hubs/feedback-ledger.tsv).",
        epilog="예:\n"
               "  ledger.py add --id F19 --req '알림이 늦게 옴' --owner 개인설정\n"
               "  ledger.py set F19 --state 진행 --note '원인 분석 중'\n"
               "  ledger.py set F19 --state 완료 --evidence 3f2a9c1\n"
               "  ledger.py owner F19 --to ui-popup\n"
               "  ledger.py list --open --json\n"
               "종료 코드: 0 성공, 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(증거 없는 완료 포함), 3 대상 없음·id 중복")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    a = sub.add_parser("add", help="새 항목 추가(상태 접수)", description="새 피드백 항목을 접수 상태로 추가. 같은 id 는 거부(exit 3).")
    a.add_argument("--id", required=True, help="항목 번호(예 F19)")
    a.add_argument("--req", required=True, help="요청 원문 요약 한 줄")
    a.add_argument("--owner", required=True, help="담당(예 개인설정)")
    a.add_argument("--dry-run", action="store_true", help="쓰지 않고 추가될 항목만 출력")
    s = sub.add_parser("set", help="상태·진행·증거 변경", description="상태 변경. 완료는 --evidence 필수. 갱신 시각은 자동.")
    s.add_argument("id", help="항목 번호")
    s.add_argument("--state", required=True, choices=STATES, metavar="|".join(STATES), help="새 상태")
    s.add_argument("--evidence", help="증거(경로·커밋 해시). 완료 때 필수")
    s.add_argument("--note", help="진행 설명(생략하면 기존 유지)")
    s.add_argument("--dry-run", action="store_true", help="쓰지 않고 바뀔 항목만 출력")
    o = sub.add_parser("owner", help="담당 변경", description="담당 칸만 바꿈. 상태·진행은 그대로(허브 이관용).")
    o.add_argument("id", help="항목 번호")
    o.add_argument("--to", required=True, help="새 담당(허브 slug)")
    o.add_argument("--dry-run", action="store_true", help="쓰지 않고 바뀔 항목만 출력")
    ls = sub.add_parser("list", help="항목 목록", description="항목 목록. --open 은 완료 제외.")
    ls.add_argument("--open", action="store_true", help="완료가 아닌 항목만")
    ls.add_argument("--json", action="store_true", help="JSON 배열로 출력")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    path = ledger_path()
    try:
        if args.cmd == "add":
            row = add(path, args.id, args.req, args.owner, time.time(), args.dry_run)
            print(("[dry-run] " if args.dry_run else "") + f"추가: {row['id']} {row['state']} · {row['request']}")
        elif args.cmd == "set":
            row = set_state(path, args.id, args.state, args.evidence, args.note, time.time(), args.dry_run)
            print(("[dry-run] " if args.dry_run else "") + f"변경: {row['id']} → {row['state']} ({row['updated']})")
        elif args.cmd == "owner":
            row = set_owner(path, args.id, args.to, time.time(), args.dry_run)
            print(("[dry-run] " if args.dry_run else "") + f"담당: {row['id']} → {row['owner']} ({row['updated']})")
        else:
            rows = [r for r in read_rows(path) if not (args.open and r["state"] == DONE)]
            if args.json:
                print(json.dumps([public(r) for r in rows], ensure_ascii=False, indent=2))
            else:
                for r in rows:
                    print(f"{EMOJI.get(r['state'], '·')} {r['id']}\t{r['state']}\t{r['owner']}\t{r['request']}")
    except LedgerError as err:
        sys.stderr.write(f"오류: {err}\n")
        return err.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
