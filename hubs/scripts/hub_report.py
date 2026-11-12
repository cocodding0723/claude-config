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
    "warn": ("⚠️", "경고", "warn"),
}
THREAD_KINDS = ("warn", "done")  # 기존 스레드에 답글로 올릴 수 있는 종류. decide 버튼 카드는 스레드 답글이 안 됨
DECISION_ID_RE = re.compile(r"D-[0-9A-Za-z]{1,16}", re.A)  # slack_notify.sh 의 decision_id 규칙과 같은 정의. 접수·전송 양쪽이 이걸 씀
DECISION_ID_ERR = "--decision-id 형식 오류: D-영문·숫자 1~16자 — 예 D-0001"
TS_RE = re.compile(r"^\d{1,12}\.\d{1,9}$")
MAX_LINES, MAX_CHARS, MAX_TITLE = 3, 400, 40  # 본문 3줄 이내. decide 첫 줄은 양식 A 제목 상한
MAX_FILES = 5
MAX_FILE_BYTES = 50 * 1024 * 1024  # 첨부 1건 크기 상한. 넘으면 전송기가 첨부 실패로 처리
# 문서·이미지·영상 확장자. 본문 경로 판정(local_paths)과 같은 정의를 접수·전송 양쪽이 씀
DOC_EXTS = ("html", "htm", "md", "pdf", "txt", "csv", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "mov", "webm", "mkv")
# 로컬 경로 후보: 절대(/…)·홈(~/…)·상대(./ ../ 포함, 폴더 구분 있거나 파일명만) + 위 확장자. URL 중간(앞이 : / 글자)은 제외
LOCAL_PATH_RE = re.compile(r"(?<![\w:/.~-])(?:~/|/|\.{1,2}/)?(?:[\w.\-가-힣]+/)*[\w\-가-힣][\w.\-가-힣]*\.(?:" + "|".join(DOC_EXTS) + r")(?![A-Za-z0-9_/])", re.I)
EXIT_IO, EXIT_USAGE = 1, 2


class ReportError(Exception):
    """원인 — 조치 문장과 종료 코드를 함께 나르는 오류"""

    def __init__(self, msg: str, code: int = EXIT_USAGE) -> None:
        super().__init__(msg)
        self.code = code


def outbox_path() -> str:
    return os.environ.get("LRM_REPORT_OUTBOX") or os.path.join(HUB_DIR, OUTBOX_NAME)


def check_decision_id(did) -> None:
    """형식이 틀리면 ReportError(종료 코드 2). 비밀 검사 뒤에 호출해 입력 값을 메시지에 되비추지 않음"""
    if not DECISION_ID_RE.fullmatch(did or ""):
        raise ReportError(DECISION_ID_ERR)


def local_paths(text: str) -> list:
    """본문에 적힌 로컬 파일 경로 후보(문서·이미지·영상 확장자). 접수 경고와 전송기 로그 경고가 이 판정 하나를 씀"""
    return [m.group(0) for m in LOCAL_PATH_RE.finditer(text or "")]


def unattached_paths(text: str, files) -> list:
    """--file 첨부 없이 본문에만 적힌 경로. 첨부가 하나라도 있으면 빈 목록"""
    return [] if files else local_paths(text)


def file_problem(path: str) -> str:
    """첨부할 수 없는 사유(없음·폴더·크기 초과). 올릴 수 있으면 빈 문자열. 입력 문제라 재시도해도 같은 결과"""
    p = os.path.expanduser(path)
    try:
        st = os.stat(p)
    except OSError:
        return "파일 없음"
    if not os.path.isfile(p):
        return "파일이 아님"
    if st.st_size > MAX_FILE_BYTES:
        return f"크기 초과({st.st_size // (1024 * 1024)}MB > {MAX_FILE_BYTES // (1024 * 1024)}MB)"
    return ""


def resolve_file(path: str) -> str:
    """접수 시점 경로 정규화. 존재하는 경로만 ~·상대를 절대 경로로(전송기는 다른 작업 폴더에서 돎). 없는 경로는 그대로 둠"""
    p = os.path.abspath(os.path.expanduser(path))
    return p if os.path.exists(p) else path


def has_secret(*texts: str) -> bool:
    return any(hub_status.SECRET_RE.search(t) for t in texts)


def make_report(hub, task, kind, text, files, options=None, recommend=None, decision_id=None, reversible=None,
                thread=None) -> dict:
    """입력 검증 → 보고 dict(id·at 제외). 잘못되면 ReportError.
    비밀 검사는 모든 입력 필드(hub·task·kind 포함)에 가장 먼저 함. 형식 오류 메시지가 입력을 되비추기 전에 걸러야 비밀이 출력·로그로 새지 않음"""
    if has_secret(*[str(v) for v in (hub, task, kind, text, options, recommend, decision_id, reversible, thread, *(files or [])) if v]):
        raise ReportError("비밀 값(토큰·키·비밀번호)으로 보이는 내용 포함 — 기록 안 함. 값을 빼고 다시 기록")
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
    rep = {"hub": hub, "task": task, "kind": kind, "text": "\n".join(lines), "files": files}
    if kind == "decide":
        if not (options and recommend and decision_id):
            raise ReportError("decide 는 --options '1=문구,2=문구' --recommend N --decision-id D-xxxx 필요 — 버튼 결정 카드 양식")
        if len(lines[0]) > MAX_TITLE:
            raise ReportError(f"decide 첫 줄(결정 제목)이 {len(lines[0])}자 — {MAX_TITLE}자 이내로, 배경은 둘째 줄부터")
        check_decision_id(decision_id)
        rep.update({"options": options, "recommend": recommend, "decision_id": decision_id})
        if reversible:
            rep["reversible"] = reversible
    elif any(extra):
        raise ReportError("--options·--recommend·--decision-id·--reversible 는 --kind decide 에서만 사용")
    if thread:
        if kind not in THREAD_KINDS:
            raise ReportError(f"--thread 는 --kind {'|'.join(THREAD_KINDS)} 에서만 사용")
        if not TS_RE.match(thread):
            raise ReportError("--thread 형식 오류 — Slack 메시지 ts(숫자.숫자, 예 1730790000.123456)")
        rep["thread"] = thread
    return rep


def report_id(rep: dict) -> str:
    """내용 해시. 시각은 넣지 않아 같은 내용이 중복으로 잡힘"""
    canon = json.dumps(rep, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


def valid_record(d) -> bool:
    """sender 가 KeyError 없이 쓸 수 있는 레코드인가: 필수 필드·타입·형식, decide 카드 필드, 비밀 값 없음"""
    if not isinstance(d, dict):
        return False
    if not all(isinstance(d.get(k), str) and d[k].strip() for k in ("id", "hub", "task", "kind", "text")):
        return False
    files = d.get("files")  # make_report 는 항상 files 를 씀. 키가 없으면 손상 레코드
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return False
    if not hub_status.SLUG_RE.match(d["hub"]) or d["kind"] not in KINDS or not (d["task"] == NO_TASK or TASK_RE.match(d["task"])):
        return False
    if d["kind"] == "decide" and not all(isinstance(d.get(k), str) and d[k] for k in ("options", "recommend", "decision_id")):
        return False
    if d.get("reversible") is not None and not isinstance(d["reversible"], str):
        return False
    if d.get("thread") is not None and not (isinstance(d["thread"], str) and TS_RE.match(d["thread"]) and d["kind"] in THREAD_KINDS):
        return False
    if "channel" in d:  # 알림 스크립트는 봇 DM 에만 게시함. 채널 지정 레코드는 엉뚱한 곳에 게시된 것으로 보이지 않게 격리
        return False
    return not has_secret(*[v for v in d.values() if isinstance(v, str)], *files)


def read_outbox(path: str, bad=None) -> list:
    """outbox → 보고 목록(기록 순서). 파일이 없으면 빈 목록. 개행으로 끝나지 않은 마지막 줄은 기록 중이거나 잘린 줄이라 읽지 않음.
    bad 가 목록이면 형식이 깨졌거나 필수 필드가 없는 줄의 원문을 모아 줌(호출자가 격리)"""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except FileNotFoundError:
        return []
    except OSError as err:
        raise ReportError(f"outbox 읽기 실패({err.strerror}) — LRM_REPORT_OUTBOX 경로·권한 확인", EXIT_IO)
    lines = raw.split("\n")[:-1]
    for ln in lines:
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            d = None
        if valid_record(d):
            out.append(d)
        elif bad is not None:
            bad.append(ln)
    return out


def repair_tail(path: str) -> None:
    """잠금 아래에서 호출. 개행 없이 끝난 마지막 줄이 있으면 줄 경계를 복구함.
    온전한 JSON 객체면 개행만 붙이고, 잘린 줄이면 <outbox>.corrupt 에 옮긴 뒤 outbox 에서 잘라냄. 복구가 안 되면 ReportError(종료 코드 1)"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return
    except OSError as err:
        raise ReportError(f"outbox 읽기 실패({err.strerror}) — LRM_REPORT_OUTBOX 경로·권한 확인", EXIT_IO)
    if not data or data.endswith(b"\n"):
        return
    cut = data.rfind(b"\n") + 1
    tail = data[cut:]
    try:
        whole = isinstance(json.loads(tail.decode("utf-8")), dict)
    except (ValueError, UnicodeDecodeError):
        whole = False
    try:
        if whole:
            with open(path, "ab") as f:
                f.write(b"\n")
                f.flush()
                os.fsync(f.fileno())
            return
        fd = os.open(path + ".corrupt", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as q:
            q.write(tail + b"\n")
            q.flush()
            os.fsync(q.fileno())
        os.truncate(path, cut)
    except OSError as err:
        raise ReportError(f"outbox 끝줄 복구 실패({err.strerror}) — 기록 안 함. {path} 끝의 잘린 줄을 직접 정리", EXIT_IO)
    sys.stderr.write(f"경고: outbox 끝의 잘린 줄 {len(tail)}바이트를 {path}.corrupt 로 옮김\n")


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
        if not dry:
            repair_tail(path)
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


def send_now() -> None:
    """기록 직후 전송기 1회. 잠금을 못 잡으면(busy) 다음 폴러 회차가 올림. 전송 실패·예외는 기록을 되돌리지 않고 안내만 출력(종료 코드 0)"""
    import report_sender  # 지연 import: report_sender 가 이 모듈을 import 함

    try:
        res = report_sender.run(report_sender.Conf())
    except Exception as err:  # noqa: BLE001 — 기록은 이미 끝났으므로 전송 오류가 호출자를 실패시키지 않음
        print(f"즉시 전송 건너뜀({type(err).__name__}) — 다음 폴러 회차에 올라감")
        return
    note = {"busy": "다른 전송기가 실행 중 — 다음 폴러 회차에 올라감", "halted": "전송 중단 — report_sender.py 오류 안내 확인"}.get(res["status"])
    print("즉시 전송: " + (note or f"전송 {res['sent']} · 실패 {res['failed']}" + (" — 실패분은 다음 회차에 재시도" if res["failed"] else "")))
    for ln in res["lines"]:
        print(ln)


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
               "  hub_report.py add --hub ci-hub --task 없음 --kind warn --thread 1730790000.123456 --text 'CI 실패 원인: 시험 X' --now\n"
               "환경: LRM_HUB_DIR, LRM_REPORT_OUTBOX\n"
               "종료 코드: 0 기록(중복 무시 포함), 1 파일 읽기·쓰기 실패, 2 인자·입력 오류(비밀 값 포함)")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    a = sub.add_parser("add", help="보고 1건 기록", description="보고 1건을 outbox 에 추가. 같은 내용은 무시(종료 코드 0).")
    a.add_argument("--hub", required=True, help="허브 slug(예 ui-layer)")
    a.add_argument("--task", required=True, help=f"작업 번호(예 C0012). 없으면 '{NO_TASK}'")
    a.add_argument("--kind", required=True, metavar="|".join(KINDS), help="보고 종류")  # 값 검사는 make_report 가 함(비밀 검사 뒤)
    a.add_argument("--text", required=True, help=f"본문 {MAX_LINES}줄·{MAX_CHARS}자 이내. decide 는 첫 줄이 결정 제목")
    a.add_argument("--file", action="append", default=[], dest="files", help="Slack 에 첨부할 문서·이미지·영상 경로(반복 가능, 최대 5개). 본문에 경로만 쓰지 않음")
    a.add_argument("--options", help="(decide) '1=문구,2=문구'")
    a.add_argument("--recommend", help="(decide) 추천 번호")
    a.add_argument("--decision-id", help="(decide) 결정 번호 D-영문·숫자")
    a.add_argument("--reversible", help="(decide) 쉬움|보통|어려움")
    a.add_argument("--thread", help="봇 DM 안의 기존 Slack 스레드 ts(숫자.숫자)에 답글로 올림(채널 지정 불가). warn·done 만. 진행 스레드 한 줄은 생략")
    a.add_argument("--now", action="store_true", help="기록 직후 전송기를 1회 실행. 다른 전송기가 돌고 있으면 다음 폴러 회차가 올림")
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
                              args.options, args.recommend, args.decision_id, args.reversible, args.thread)
            rep["files"] = [resolve_file(f) for f in rep["files"]]
            for f in rep["files"]:
                if why := file_problem(f):
                    sys.stderr.write(f"경고: --file {f} 첨부 불가({why}) — 기록은 하고 전송기가 '첨부 실패' 한 줄을 남김\n")
            if found := unattached_paths(rep["text"], rep["files"]):
                sys.stderr.write(f"경고: 본문에 로컬 경로({', '.join(found[:3])})만 있고 --file 첨부 없음 — 사용자는 경로를 열 수 없음. --file 로 첨부\n")
            rep, fresh = add(path, rep, time.time(), args.dry_run)
            if args.json:
                print(json.dumps({"id": rep["id"], "added": fresh and not args.dry_run, "duplicate": not fresh}, ensure_ascii=False))
            elif not fresh:
                print(f"중복 무시: {rep['id']} — 같은 내용이 이미 기록됨")
            else:
                print(("[dry-run] " if args.dry_run else "") + f"기록: {rep['id']} {rep['hub']} {rep['task']} {rep['kind']}")
            if args.now and not args.dry_run:
                send_now()
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
