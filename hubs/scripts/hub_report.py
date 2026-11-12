#!/usr/bin/env python3
"""허브 진행 보고 기록 CLI. 허브가 메인 세션을 거치지 않고 보고를 outbox 에 한 줄 남김.

Slack 을 부르지 않음. 토큰을 가진 report_sender.py 가 outbox 를 읽어 작업 번호의 주제 스레드에 올림.
outbox: 한 줄 = 보고 1건(JSON). 쓰기는 flock 아래 추가 전용이라 허브 여러 개가 동시에 기록해도 줄이 섞이지 않음.
같은 내용(허브·작업·종류·본문·증거·결정 필드)은 id 가 같아 두 번째부터 무시함. 비밀 값(hub_status 와 같은 패턴)이 있으면 거부.

환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs) · LRM_REPORT_OUTBOX outbox 경로(기본 <작업 폴더>/hub-reports.jsonl)
종료 코드: 0 기록(중복 무시 포함), 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(비밀 값 포함)
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # -I 실행·복사 설치에서도 같은 폴더의 hub_status 를 찾음
import hub_status  # noqa: E402

HUB_DIR = os.environ.get("LRM_HUB_DIR", os.path.expanduser("~/.config/lrm-hubs"))
OUTBOX_NAME = "hub-reports.jsonl"
NO_TASK = "없음"
TASK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,23}$")
# 종류 → (이모지, 라벨, slack_notify.sh --kind)
KINDS = {
    "done": ("✅", "완료", "done"),
    "block": ("⚠️", "차단", "warn"),
    "decide": ("🙋", "결정", "decide"),
    "info": ("🔄", "정보", "progress"),
}
MAX_LINES, MAX_CHARS, MAX_TITLE = 3, 400, 40  # 본문 3줄 이내. decide 첫 줄은 양식 A 제목 상한
MAX_FILES = 5
EXIT_IO, EXIT_USAGE = 1, 2


class ReportError(Exception):
    """원인 — 조치 문장과 종료 코드를 함께 나르는 오류"""

    def __init__(self, msg: str, code: int = EXIT_USAGE) -> None:
        super().__init__(msg)
        self.code = code


def outbox_path() -> str:
    return os.environ.get("LRM_REPORT_OUTBOX") or os.path.join(HUB_DIR, OUTBOX_NAME)


def has_secret(*texts: str) -> bool:
    return any(hub_status.SECRET_RE.search(t) for t in texts)


def make_report(hub, task, kind, text, files, options=None, recommend=None, decision_id=None, reversible=None) -> dict:
    """입력 검증 → 보고 dict(id·at 제외). 잘못되면 ReportError"""
    if not hub_status.SLUG_RE.match(hub or "") or len(hub) > 24:
        raise ReportError(f"--hub '{hub}' 형식 오류 — 소문자·숫자·하이픈 24자 이내(예: ui-layer)")
    if task != NO_TASK and not TASK_RE.match(task or ""):
        raise ReportError(f"--task '{task}' 형식 오류 — 작업 번호(예: C0012) 또는 '{NO_TASK}'")
    if kind not in KINDS:
        raise ReportError(f"--kind '{kind}' 오류 — {'|'.join(KINDS)} 중 하나")
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        raise ReportError("--text 비어 있음 — 보고 내용을 3줄 이내로 지정")
    if len(lines) > MAX_LINES or len("\n".join(lines)) > MAX_CHARS:
        raise ReportError(f"--text 가 {len(lines)}줄·{len(' '.join(lines))}자 — {MAX_LINES}줄·{MAX_CHARS}자 이내로 줄이고 상세는 --file 증거로")
    files = [f.strip() for f in files or []]
    if any(not f or "\n" in f for f in files) or len(files) > MAX_FILES:
        raise ReportError(f"--file 오류 — 한 줄 경로 {MAX_FILES}개 이하")
    extra = [options, recommend, decision_id, reversible]
    if has_secret(*lines, *files, *[e for e in extra if e]):
        raise ReportError("비밀 값(토큰·키·비밀번호)으로 보이는 내용 포함 — 기록 안 함. 값을 빼고 다시 기록")
    rep = {"hub": hub, "task": task, "kind": kind, "text": "\n".join(lines), "files": files}
    if kind == "decide":
        if not (options and recommend and decision_id):
            raise ReportError("decide 는 --options '1=문구,2=문구' --recommend N --decision-id D-xxxx 필요 — 버튼 결정 카드 양식")
        if len(lines[0]) > MAX_TITLE:
            raise ReportError(f"decide 첫 줄(결정 제목)이 {len(lines[0])}자 — {MAX_TITLE}자 이내로, 배경은 둘째 줄부터")
        rep.update({"options": options, "recommend": recommend, "decision_id": decision_id})
        if reversible:
            rep["reversible"] = reversible
    elif any(extra):
        raise ReportError("--options·--recommend·--decision-id·--reversible 는 --kind decide 에서만 사용")
    return rep


def report_id(rep: dict) -> str:
    """내용 해시. 시각은 넣지 않아 같은 내용이 중복으로 잡힘"""
    canon = json.dumps(rep, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


def read_outbox(path: str) -> list:
    """outbox → 보고 목록(기록 순서). 파일이 없으면 빈 목록, 깨진 줄은 건너뜀"""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("id") and d.get("hub") and d.get("kind") in KINDS:
                    out.append(d)
    except FileNotFoundError:
        return []
    except OSError as err:
        raise ReportError(f"outbox 읽기 실패({err.strerror}) — LRM_REPORT_OUTBOX 경로·권한 확인", EXIT_IO)
    return out


@contextlib.contextmanager
def locked(path: str):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path + ".lock", "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            yield
    except OSError as err:
        raise ReportError(f"outbox 잠금 실패({err.strerror}) — 작업 폴더 권한 확인", EXIT_IO)


def add(path: str, rep: dict, now: float, dry: bool) -> tuple:
    """(보고 dict, 새로 기록했나). 같은 id 가 이미 있으면 기록하지 않음"""
    rep = {"id": report_id(rep), "at": int(now), **rep}
    with locked(path):
        if any(r["id"] == rep["id"] for r in read_outbox(path)):
            return rep, False
        if not dry:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rep, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as err:
                raise ReportError(f"outbox 쓰기 실패({err.strerror}) — 폴더 권한·디스크 확인", EXIT_IO)
    return rep, True


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"오류: {message} — --help 참고\n")
        sys.exit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(
        prog="hub_report.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="허브 진행 보고를 outbox 에 한 줄 기록. Slack 호출 없음 — report_sender.py 가 주제 스레드에 올림.",
        epilog="예:\n"
               "  hub_report.py add --hub ui-layer --task C0012 --kind done --text '버튼 정렬 수정 완료' --file docs/ui.html\n"
               "  hub_report.py add --hub ui-layer --task 없음 --kind info --text '리뷰 대기 중'\n"
               "  hub_report.py add --hub ui-layer --task C0012 --kind decide --text $'알림 시각 정하기\\n지금은 시각이 제각각' \\\n"
               "      --options '1=오전 9시,2=오전 10시' --recommend 1 --decision-id D-0101\n"
               "환경: LRM_HUB_DIR, LRM_REPORT_OUTBOX\n"
               "종료 코드: 0 기록(중복 무시 포함), 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(비밀 값 포함)")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    a = sub.add_parser("add", help="보고 1건 기록", description="보고 1건을 outbox 에 추가. 같은 내용은 무시(종료 코드 0).")
    a.add_argument("--hub", required=True, help="허브 slug(예 ui-layer)")
    a.add_argument("--task", required=True, help=f"작업 번호(예 C0012). 없으면 '{NO_TASK}'")
    a.add_argument("--kind", required=True, choices=list(KINDS), metavar="|".join(KINDS), help="보고 종류")
    a.add_argument("--text", required=True, help=f"본문 {MAX_LINES}줄·{MAX_CHARS}자 이내. decide 는 첫 줄이 결정 제목")
    a.add_argument("--file", action="append", default=[], dest="files", help="증거 경로(반복 가능, 최대 5개)")
    a.add_argument("--options", help="(decide) '1=문구,2=문구'")
    a.add_argument("--recommend", help="(decide) 추천 번호")
    a.add_argument("--decision-id", help="(decide) 결정 번호 D-영문·숫자")
    a.add_argument("--reversible", help="(decide) 쉬움|보통|어려움")
    a.add_argument("--dry-run", action="store_true", help="쓰지 않고 기록될 보고만 출력")
    a.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로 출력")
    ls = sub.add_parser("list", help="outbox 목록", description="outbox 의 보고를 기록 순서로 출력.")
    ls.add_argument("--json", action="store_true", help="JSON 배열로 출력")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    path = outbox_path()
    try:
        if args.cmd == "add":
            rep = make_report(args.hub, args.task, args.kind, args.text, args.files,
                              args.options, args.recommend, args.decision_id, args.reversible)
            rep, fresh = add(path, rep, time.time(), args.dry_run)
            if args.json:
                print(json.dumps({"id": rep["id"], "added": fresh and not args.dry_run, "duplicate": not fresh}, ensure_ascii=False))
            elif not fresh:
                print(f"중복 무시: {rep['id']} — 같은 내용이 이미 기록됨")
            else:
                print(("[dry-run] " if args.dry_run else "") + f"기록: {rep['id']} {rep['hub']} {rep['task']} {rep['kind']}")
        else:
            rows = read_outbox(path)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for r in rows:
                    print(f"{r['id']}\t{r['hub']}\t{r['task']}\t{r['kind']}\t{r['text'].splitlines()[0]}")
    except ReportError as err:
        sys.stderr.write(f"오류: {err}\n")
        return err.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
