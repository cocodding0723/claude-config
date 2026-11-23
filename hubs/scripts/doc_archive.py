#!/usr/bin/env python3
"""문서 보관소: 보낸 문서를 정리기록 폴더에 복사하고 색인 한 줄을 남김. 표준 라이브러리만 사용, 네트워크 없음.

설정(이 파일 한 곳): LRM_ARCHIVE_ROOT(보관 뿌리, 기본 ~/Documents/90_정리기록), LRM_TICKET_URL_BASE(티켓 주소 앞부분, 없으면 주소 칸 비움).
폴더: <뿌리>/<날짜>-<작업 이름>/, 색인: <뿌리>/archive-index.jsonl (한 줄 = 한 보관, 키는 폴더 + 파일 이름·해시 묶음).
이름 겹침: 같은 이름·같은 내용은 건너뜀, 같은 이름·다른 내용은 '<이름>-<해시 앞 8자><확장자>' 로 둠(기존 파일은 덮어쓰지 않음).
문서 모아보기(doc_index.py)는 뿌리 아래 *.html 을 이미 수집하므로 보관한 HTML 은 별도 연결 없이 목록에 나옴.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

ROOT_ENV, URL_ENV = "LRM_ARCHIVE_ROOT", "LRM_TICKET_URL_BASE"
DEFAULT_ROOT = "~/Documents/90_정리기록"
INDEX_NAME = "archive-index.jsonl"
HTML_EXTS = (".html", ".htm")
NAME_MAX = 80
HASH_LEN = 8
UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
EXIT_OK, EXIT_INPUT, EXIT_IO = 0, 3, 4   # 2 는 argparse 사용법 오류


class ArchiveError(Exception):
    def __init__(self, msg, code=EXIT_INPUT):
        super().__init__(msg)
        self.code = code


def root() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get(ROOT_ENV) or DEFAULT_ROOT))


def ticket_url(ticket: str) -> str:
    base = os.environ.get(URL_ENV, "")
    return base + ticket if base and ticket else ""


def parse_date(s: str) -> str:
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        raise ArchiveError(f"날짜 형식 오류: {s!r} — YYYY-MM-DD 로 적으세요")


def folder_name(date: str, title: str) -> str:
    safe = re.sub(r"\s+", " ", UNSAFE_RE.sub("-", title)).strip().lstrip(".")[:NAME_MAX].strip()
    if not safe:
        raise ArchiveError("작업 이름이 비었음 — --title 에 작업 이름을 적으세요")
    return f"{date}-{safe}"


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_sources(files: list[str]) -> list[str]:
    """입력 검사 후 복사 대상(옆 PDF 포함). 하나라도 없으면 아무것도 하지 않고 실패"""
    missing = [f for f in files if not os.path.isfile(os.path.expanduser(f))]
    if missing:
        raise ArchiveError(f"파일 없음: {', '.join(missing)} — 경로를 확인하고 다시 실행하세요(조치: 없는 파일은 빼거나 경로 수정)")
    out = []
    for f in files:
        p = os.path.abspath(os.path.expanduser(f))
        for q in [p] + ([os.path.splitext(p)[0] + ".pdf"] if p.lower().endswith(HTML_EXTS) else []):
            if os.path.isfile(q) and q not in out:
                out.append(q)
    return out


def plan_copy(dest_dir: str, src: str, planned: dict[str, str]) -> tuple[str, str]:
    """(대상 경로, 동작: copy|same). planned 는 이번 실행에서 이미 계획한 {대상: 해시}.
    디스크에 있거나 이미 계획된 이름이 같으면 겹침: 내용이 같으면 same, 다르면 해시 이름으로 비켜 감"""
    name = os.path.basename(src)
    digest = sha(src)
    stem, ext = os.path.splitext(name)
    for dst in (os.path.join(dest_dir, name), os.path.join(dest_dir, f"{stem}-{digest[:HASH_LEN]}{ext}")):
        have = planned.get(dst) or (sha(dst) if os.path.exists(dst) else None)
        if have is None:
            planned[dst] = digest
            return dst, "copy"
        if have == digest:
            return dst, "same"
    raise ArchiveError(f"이름 겹침을 풀지 못함: {name} — 보관 폴더의 같은 해시 이름 파일을 확인하세요", EXIT_IO)


def plan_all(dest_dir: str, sources: list[str]) -> list[tuple[str, str, str]]:
    """[(원본, 대상, 동작)]. 같은 대상이 두 번 나오면(같은 내용) 한 번만"""
    planned, steps, seen = {}, [], set()
    for src in sources:
        dst, action = plan_copy(dest_dir, src, planned)
        if dst not in seen:
            seen.add(dst)
            steps.append((src, dst, action))
    return steps


def read_index(path: str) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    rows.append(d)
    except OSError:
        pass
    return rows


def write_index(path: str, rows: list[dict]) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".archive-index.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def add(args) -> dict:
    date = parse_date(args.date) if args.date else datetime.date.today().isoformat()
    sources = collect_sources(args.files)
    base = root()
    dest_dir = os.path.join(base, folder_name(date, args.title))
    key = lambda r: (r.get("dir"), tuple(sorted((e["name"], e["sha256"]) for e in r.get("files", []))))

    def build():
        steps = plan_all(dest_dir, sources)
        row = {"date": date, "title": args.title, "card": args.card, "ticket": args.ticket, "ticket_url": ticket_url(args.ticket),
               "kind": args.kind, "thread": args.thread, "dir": dest_dir,
               "files": [{"name": os.path.basename(dst), "sha256": sha(src)} for src, dst, _ in steps]}
        return steps, row

    def result(steps, row):
        return {"dir": dest_dir, "copied": sum(a == "copy" for *_, a in steps), "skipped": sum(a == "same" for *_, a in steps),
                "files": [dst for _, dst, _ in steps], "row": row, "dry_run": args.dry_run}

    if args.dry_run:
        return result(*build())
    try:
        os.makedirs(dest_dir, exist_ok=True)
        # 계획부터 복사·색인 쓰기까지 한 잠금 안: 같은 폴더로 동시에 보관해도 서로 덮어쓰지 않음
        with open(os.path.join(base, ".archive.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            steps, row = build()
            for src, dst, action in steps:
                if action == "copy":
                    shutil.copyfile(src, dst)
            index = os.path.join(base, INDEX_NAME)
            rows = read_index(index)
            same = [i for i, r in enumerate(rows) if key(r) == key(row)]
            if same:
                rows[same[0]] = {**row, "archived_at": rows[same[0]].get("archived_at")}
                rows = [r for i, r in enumerate(rows) if i not in same[1:]]
            else:
                rows.append({**row, "archived_at": datetime.datetime.now().isoformat(timespec="seconds")})
            write_index(index, rows)
    except OSError as e:
        raise ArchiveError(f"보관 실패: {e.strerror} ({e.filename}) — 보관 뿌리 권한과 남은 용량을 확인하세요", EXIT_IO)
    return result(steps, row)


def list_rows(args) -> list[dict]:
    since = parse_date(args.since) if args.since else ""
    rows = [r for r in read_index(os.path.join(root(), INDEX_NAME))
            if (not args.card or r.get("card") == args.card) and (not args.ticket or r.get("ticket") == args.ticket)
            and (not since or r.get("date", "") >= since)]
    return sorted(rows, key=lambda r: (r.get("date", ""), r.get("archived_at", "")), reverse=True)


def build_parser():
    p = argparse.ArgumentParser(
        description="보낸 문서를 정리기록 폴더에 보관하고 색인 한 줄을 남김",
        epilog="예시:\n"
               "  doc_archive.py add --file docs/out/plan.html --title '우편함 개편' --card lrm-12 --ticket LRM-612 --kind plan --thread 1700000000.0001\n"
               "  doc_archive.py add --file a.html --title 시험 --dry-run      # 쓰지 않고 계획만 출력\n"
               "  doc_archive.py list --card lrm-12 --json\n"
               "  doc_archive.py list --since 2026-11-01\n"
               f"종료 코드: 0 성공, 2 사용법 오류, 3 입력 오류(없는 파일·날짜 형식), 4 쓰기 실패.\n"
               f"설정: {ROOT_ENV}(보관 뿌리, 기본 {DEFAULT_ROOT}), {URL_ENV}(티켓 주소 앞부분).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="문서 보관 + 색인 한 줄")
    a.add_argument("--file", action="append", required=True, dest="files", help="보관할 파일(반복 가능). HTML 은 옆 같은 이름 PDF 도 함께")
    a.add_argument("--title", required=True, help="작업 이름(폴더 이름에 쓰임)")
    a.add_argument("--card", default="", help="작업 카드 번호")
    a.add_argument("--ticket", default="", help="티켓 번호")
    a.add_argument("--kind", default="", help="문서 종류")
    a.add_argument("--thread", default="", help="Slack 스레드")
    a.add_argument("--date", help="YYYY-MM-DD, 기본 오늘")
    a.add_argument("--dry-run", action="store_true", help="복사·색인 쓰기 없이 계획만 출력")
    a.add_argument("--json", action="store_true", help="JSON 출력")
    ls = sub.add_parser("list", help="색인 조회(최신순)")
    ls.add_argument("--card")
    ls.add_argument("--ticket")
    ls.add_argument("--since", help="YYYY-MM-DD 이후")
    ls.add_argument("--json", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "add":
            res = add(args)
            if args.json:
                print(json.dumps(res, ensure_ascii=False))
            else:
                print(("[dry-run] " if res["dry_run"] else "") + f"보관: {res['dir']} (복사 {res['copied']}, 건너뜀 {res['skipped']})")
                for f in res["files"]:
                    print(f"  {f}")
                if res["dry_run"]:
                    print("  색인 줄: " + json.dumps(res["row"], ensure_ascii=False))
        else:
            rows = list_rows(args)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False))
            else:
                for r in rows:
                    print("\t".join([r.get("date", ""), r.get("title", ""), r.get("card", ""), r.get("ticket", ""), r.get("kind", ""), r.get("dir", "")]))
        return EXIT_OK
    except ArchiveError as e:
        sys.stderr.write(f"오류: {e}\n")
        return e.code


if __name__ == "__main__":
    sys.exit(main())
