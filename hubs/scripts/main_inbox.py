#!/usr/bin/env python3
"""메인 코디네이터 받은 편지함: 자동 알림을 메인 터미널 입력 대신 파일 한 줄로 남김. 쓰는 쪽(append)과 읽는 쪽(read)이 같은 모듈.

터미널 입력창에 알림이 들어가면 사용자 입력과 섞이므로(2026-11-16 지적) 메인 대상 자동 출력은 hub-inbox/main.jsonl 로 보냄.
메인 순찰(orca-main patrol.sh --inbox)이 읽은 위치(inode+바이트 오프셋)를 main-inbox.offset 에 기록하며 새 줄만 가져감.
허브 대상 전달은 이 모듈과 무관(터미널 send 그대로).

한 줄: {at(ISO 초+시간대), source(보낸 스크립트), kind, text, ref?, id(멱등 키 source-해시), full?(4KiB 초과분 전문 파일)}
kind: answer(사용자 답) stall(멈춘 워커) ci(CI·PR) restart(재기동) intake(접수) review(AI 리뷰) slack(Slack 수신) other
규칙: flock + O_APPEND, 권한 0600, 한 줄 4KiB 상한(넘으면 전문은 main-overflow/<id>.txt, 줄에는 앞부분+full 경로),
      같은 id 는 최근 500줄 안에서 한 번만(같은 알림 재전송 금지), 1MiB 넘으면 main.jsonl.1 로 회전(읽는 쪽이 옛 파일 잔여분을 이어 읽음).

CLI: main_inbox.py append --kind K --source S [--ref R] [--dry-run] [--json] <본문|->
     main_inbox.py read --since-offset [--peek] [--json]   새 줄 출력 후 읽은 위치 기록(--peek 은 기록 안 함)
환경: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs). 받은 편지함 <폴더>/hub-inbox/main.jsonl, 읽은 위치 <폴더>/main-inbox.offset
종료 코드: 0 성공(중복 건너뜀 포함), 1 파일 읽기·쓰기 실패, 2 인자·입력 오류
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import sys

KINDS = ("answer", "stall", "ci", "restart", "intake", "review", "slack", "other")
INBOX_NAME, LOCK_NAME, OVERFLOW_DIR, OFFSET_NAME = "main.jsonl", ".main.jsonl.lock", "main-overflow", "main-inbox.offset"
LINE_MAX = 4096        # 한 줄(개행 포함) 바이트 상한. PIPE_BUF 와 같아 O_APPEND 한 번 쓰기가 끊기지 않음
DEDUP_LINES = 500
ROTATE_BYTES = 1 << 20
EXIT_IO, EXIT_USAGE = 1, 2


def hub_dir(env=None):
    env = os.environ if env is None else env
    return env.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")


def inbox_path(d=None):
    return os.path.join(d or hub_dir(), "hub-inbox", INBOX_NAME)


def offset_path(d=None):
    return os.path.join(d or hub_dir(), OFFSET_NAME)


def make_id(source, kind, text, ref=None):
    h = hashlib.sha256("\0".join((kind, ref or "", text)).encode("utf-8", "replace")).hexdigest()[:12]
    return f"{source}-{h}"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def encode(rec):
    return (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def fit(rec, spill):
    """줄이 LINE_MAX 이하가 되게 본문을 줄임. 줄이면 spill(전문) 경로를 full 에 기록. (줄 바이트, 잘렸는지)"""
    line = encode(rec)
    if len(line) <= LINE_MAX:
        return line, False
    rec = dict(rec, full=spill(rec["text"]))
    text = rec["text"]
    lo, hi = 0, len(text)
    while lo < hi:  # 들어가는 가장 긴 앞부분 이진 탐색
        mid = (lo + hi + 1) // 2
        if len(encode(dict(rec, text=text[:mid] + "…(전문은 full)"))) <= LINE_MAX:
            lo = mid
        else:
            hi = mid - 1
    return encode(dict(rec, text=text[:lo] + "…(전문은 full)")), True


def tail_ids(path, n=DEDUP_LINES):
    """파일 끝 n 줄의 id 집합. 한 줄 상한이 있으므로 끝 n*LINE_MAX 바이트만 읽으면 충분"""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - n * LINE_MAX))
            data = f.read()
    except FileNotFoundError:
        return set()
    ids = set()
    for ln in data.splitlines()[-n:]:
        try:
            ids.add(json.loads(ln).get("id"))
        except (ValueError, AttributeError):
            pass
    return ids


@contextlib.contextmanager
def locked(d):
    os.makedirs(os.path.join(d, "hub-inbox"), mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(d, "hub-inbox", LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # 닫으면 잠금도 풀림


def append(kind, text, source, ref=None, hub=None, dry_run=False):
    """한 줄 추가. 반환 {status: added|duplicate, id, truncated}. 쓰기 실패는 OSError, 인자 오류는 ValueError.
    dry_run 은 파일·폴더를 만들지 않고 같은 판정만 돌려주며 line 에 쓸 줄을 담음"""
    if kind not in KINDS:
        raise ValueError(f"kind 는 {'|'.join(KINDS)} 중 하나")
    if not source or not isinstance(text, str) or not text.strip():
        raise ValueError("source·본문 필요")
    d = hub or hub_dir()
    path = inbox_path(d)
    rec = {"at": now_iso(), "source": source, "kind": kind, "text": text}
    if ref:
        rec["ref"] = str(ref)
    rec["id"] = make_id(source, kind, text, ref)

    def spill(full):
        p = os.path.join(d, "hub-inbox", OVERFLOW_DIR, rec["id"] + ".txt")
        if not dry_run:
            os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(full)
        return p

    if dry_run:
        line, cut = fit(rec, spill)
        return {"status": "duplicate" if rec["id"] in tail_ids(path) else "added", "id": rec["id"], "truncated": cut, "line": line.decode("utf-8")}
    with locked(d):
        if rec["id"] in tail_ids(path):
            return {"status": "duplicate", "id": rec["id"], "truncated": False}
        line, cut = fit(rec, spill)
        try:
            if os.path.getsize(path) >= ROTATE_BYTES:
                os.replace(path, path + ".1")  # 잠금 아래라 쓰는 쪽끼리 경합 없음. 읽는 쪽은 inode 변경으로 감지
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    return {"status": "added", "id": rec["id"], "truncated": cut}


def sender(source, kind, hub=None):
    """알림 문자열 -> 성공 여부 함수. 기존 '메인 터미널 전송 함수' 자리에 그대로 꽂음. 쓰기 실패는 False"""
    def send(text):
        try:
            append(kind, text, source, hub=hub)
            return True
        except (OSError, ValueError):
            return False
    return send


# ---- 읽기 -------------------------------------------------------------------------------------
def load_offset(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return int(d["inode"]), int(d["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, 0


def save_offset(path, inode, offset):
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"inode": inode, "offset": offset}, f)
    os.replace(tmp, path)


def read_from(path, offset):
    """offset 부터 마지막 완전한 줄까지. (파싱한 레코드 목록, 새 오프셋). 개행 없는 끝 조각은 쓰는 중일 수 있어 남김"""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b"\n") + 1
    recs = []
    for ln in data[:end].splitlines():
        try:
            r = json.loads(ln)
            recs.append(r if isinstance(r, dict) else {"kind": "other", "text": ln.decode("utf-8", "replace")})
        except ValueError:
            recs.append({"kind": "other", "source": "main_inbox", "text": ln.decode("utf-8", "replace")[:400]})  # 깨진 줄도 잃지 않고 보여줌
    return recs, offset + end


def read_new(d=None, commit=True):
    """읽은 위치 이후의 새 줄. 회전(inode 변경)이면 옛 파일(.1)의 잔여분을 먼저, 잘림(크기 < 오프셋)이면 처음부터.
    반환 (레코드 목록, 메모). 잠금 아래에서 읽고 기록해 순찰이 겹쳐도 같은 줄을 두 번 가져가지 않음"""
    d = d or hub_dir()
    path, opath = inbox_path(d), offset_path(d)
    with locked(d):
        ino, off = load_offset(opath)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return [], ""
        recs, notes = [], []
        if ino is not None and ino != st.st_ino:  # 회전됨
            try:
                if os.stat(path + ".1").st_ino == ino:
                    old, _ = read_from(path + ".1", off)
                    recs += old
                    notes.append(f"회전 감지: 옛 파일 잔여 {len(old)}줄 포함")
                else:
                    notes.append("회전 감지: 옛 파일 없음, 새 파일 처음부터")
            except FileNotFoundError:
                notes.append("회전 감지: 옛 파일 없음, 새 파일 처음부터")
            off = 0
        elif st.st_size < off:
            notes.append("잘림 감지: 처음부터")
            off = 0
        new, end = read_from(path, off)
        if commit:
            save_offset(opath, st.st_ino, end)
        return recs + new, "; ".join(notes)


def format_row(r):
    text = " ".join(str(r.get("text", "")).split())
    ref = f" [{r['ref']}]" if r.get("ref") else ""
    full = f" (전문 {r['full']})" if r.get("full") else ""
    return f"{str(r.get('at', ''))[5:19].replace('T', ' ')} {r.get('kind', '?'):<7} {r.get('source', '?')}: {text}{ref}{full}"


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"오류: {message} — --help 참고\n")
        sys.exit(EXIT_USAGE)


def build_parser():
    p = Parser(prog="main_inbox.py", formatter_class=argparse.RawDescriptionHelpFormatter,
               description="메인 코디네이터 받은 편지함(hub-inbox/main.jsonl). 자동 알림을 터미널 입력 대신 한 줄로 쌓고 순찰이 읽음.",
               epilog="예:\n  main_inbox.py append --kind ci --source ci_watch 'CI 실패 PR#12 …'\n  main_inbox.py append --kind other --source demo --dry-run --json 시험\n"
                      "  main_inbox.py read --since-offset          # 새 줄 표로 출력, 읽은 위치 기록\n  main_inbox.py read --since-offset --peek --json\n"
                      "환경: LRM_HUB_DIR\n종료 코드: 0 성공(중복 건너뜀 포함), 1 파일 읽기·쓰기 실패, 2 인자·입력 오류")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    a = sub.add_parser("append", help="한 줄 추가(같은 알림은 최근 500줄 안에서 한 번만)")
    a.add_argument("--kind", required=True, choices=KINDS)
    a.add_argument("--source", required=True, help="보낸 스크립트 이름")
    a.add_argument("--ref", help="관련 번호·경로(선택)")
    a.add_argument("--dry-run", action="store_true", help="쓰지 않고 쓸 줄·중복 여부만 출력")
    a.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로 출력")
    a.add_argument("text", help="본문. '-' 면 표준 입력")
    r = sub.add_parser("read", help="읽은 위치 이후 새 줄 출력")
    r.add_argument("--since-offset", action="store_true", required=True, help="main-inbox.offset 이후만(필수)")
    r.add_argument("--peek", action="store_true", help="읽은 위치를 기록하지 않음")
    r.add_argument("--json", action="store_true", help="줄마다 JSON 으로 출력")
    return p


def main(argv):
    a = build_parser().parse_args(argv[1:])
    try:
        if a.cmd == "append":
            text = sys.stdin.read() if a.text == "-" else a.text
            res = append(a.kind, text.strip(), a.source, a.ref, dry_run=a.dry_run)
            if a.json:
                print(json.dumps({k: v for k, v in res.items()}, ensure_ascii=False))
            elif a.dry_run:
                print(f"[dry-run] {res['status']} {res['id']}\n{res['line']}", end="")
            else:
                print(f"{'추가함' if res['status'] == 'added' else '이미 있음(건너뜀)'} {res['id']}")
            return 0
        recs, note = read_new(commit=not a.peek)
    except ValueError as err:
        print(f"오류: {err} — --help 참고", file=sys.stderr)
        return EXIT_USAGE
    except OSError as err:
        print(f"받은 편지함 읽기·쓰기 실패({type(err).__name__}) — 폴더 권한·디스크 확인", file=sys.stderr)
        return EXIT_IO
    if a.json:
        for r in recs:
            print(json.dumps(r, ensure_ascii=False))
        if note:
            print(json.dumps({"kind": "other", "source": "main_inbox", "text": note}, ensure_ascii=False))
    else:
        for r in recs:
            print(format_row(r))
        print(f"받은 편지함 새 줄 {len(recs)}건" + (f" · {note}" if note else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
