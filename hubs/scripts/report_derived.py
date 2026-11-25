#!/usr/bin/env python3
"""HTML 이 있는 보고의 첨부를 PDF·미리보기와 함께 메시지 하나로 올림. 글 전송(report_sender.py)과 따로 도는 작업.

변환이 한 번 실패하면 원본과 만든 것을 먼저 묶어 올리고, 못 만든 것만 나중에 이어 올림.

글 전송 회차 끝에서 남은 시간으로 만들던 때는 바쁜 동안 한 건도 못 나갔음(11-25 10건 대기). 그래서 잠금·상태·시간 예산을 따로 둠.
전송기가 보고 상태에 남긴 derived 표시(올릴 스레드)를 읽기만 하고, 진행 기록은 자기 상태 파일에만 씀(전송기 상태를 두 프로세스가 쓰지 않게).
실패는 DERIVED_BACKOFF_S 부터 간격을 늘려 DERIVED_TRIES 회까지 다시 시도하고, 끝내 못 만들면 스레드에 '첨부 실패' 한 줄을 남김.
한 보고가 실패해도 다음 보고를 계속함. 적게 실패한 것부터 처리해 계속 실패하는 문서가 예산을 다 쓰지 않음.

환경: report_sender.py 와 같음 + LRM_DERIVED_STATE 상태 파일(기본 <작업 폴더>/report-derived.state.json) · LRM_DERIVED_BUDGET_S 회차 시간 예산
종료 코드: 0 성공(할 일 없음·다른 회차 실행 중 포함), 1 다시 보낼 보고를 못 찾음, 2 인자 오류·상태 파일 손상
"""
import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hub_report  # noqa: E402
import report_sender as sender  # noqa: E402

DEFAULT_BUDGET_S = 120.0
MAKE_TIMEOUT_S = 45  # 평소 1~2초. 부하가 높을 때(코어의 몇 배) 15초를 넘긴 적이 있어 넉넉히 둠
MIN_LEFT_S = 8  # 예산이 이만큼 안 남았으면 시작하지 않음(시간이 모자란 것을 실패로 세지 않게)
DERIVED_TRIES, DERIVED_BACKOFF_S = 5, 120  # 120·240·480·960초 간격. 30분쯤 멈춰도 풀린 뒤 올라감
KILL_GRACE_S = 2  # 정상 종료 요청 뒤 강제 종료까지. 변환 스크립트가 임시 폴더를 지울 시간
STATE_NAME = "report-derived.state.json"


class MakeError(Exception):
    """변환 실패. 나중에 다시 시도함"""


def state_path(conf) -> str:
    return os.environ.get("LRM_DERIVED_STATE") or os.path.join(os.path.dirname(os.path.abspath(conf.state)), STATE_NAME)


def load(path: str) -> dict:
    """{보고 id: {done, tries, next_at, finished, target}}. 파일이 없을 때만 빈 상태, 손상이면 StateCorrupt(중복 전송 방지)"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise sender.StateCorrupt(f"딸린 파일 상태 읽기 실패({type(err).__name__})")
    if not isinstance(d, dict) or any(not isinstance(v, dict) or not isinstance(v.get("done"), dict) for v in d.values()):
        raise sender.StateCorrupt("딸린 파일 상태 모양 오류")
    return d


def run_maker(cmd: list, out: str, timeout: float) -> int:
    """변환 스크립트를 새 프로세스 그룹으로 실행 → 종료 코드. 시간 초과면 그룹째 끝내고 MakeError.
    스크립트만 죽이면 그 아래 Chrome 이 남아 쌓이고, 뒤늦게 결과 파일을 써서 지울 사람 없는 파일이 남음"""
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as err:
        raise MakeError(f"변환 스크립트 실행 실패({err.strerror})")
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MakeError("변환 시간 초과")
    finally:
        if p.returncode is None:
            with contextlib.suppress(OSError):
                os.killpg(p.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=KILL_GRACE_S)
            with contextlib.suppress(OSError):  # 스크립트가 끝났어도 Chrome 이 남았을 수 있어 그룹에 한 번 더
                os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            with contextlib.suppress(OSError):
                os.unlink(out)


def out_path(conf, suffix: str, name: str, digest: str) -> str:
    """만들 파일 경로. 내용 해시로 고정(응답 유실 복구가 보낸 기록의 경로로 찾음). PDF 는 이름이 Slack 에 그대로 보여 해시를 폴더 이름에만 둠"""
    root, base = os.path.join(os.path.dirname(os.path.abspath(conf.state)), "report-previews"), os.path.splitext(name)[0]
    return os.path.join(root, digest, base + ".pdf") if suffix == sender.PDF_SUFFIX else os.path.join(root, f"{digest}-{base}-preview.png")


def make(conf, suffix: str, origin: str, digest: str, name: str, deadline: float) -> str:
    """HTML → PDF·미리보기 경로. 변환 스크립트가 없으면 빈 문자열(생략), 실패는 MakeError"""
    script = getattr(conf, sender.DERIVED[suffix][0])
    if not os.access(script, os.X_OK):
        return ""
    out = out_path(conf, suffix, name, digest)
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    rc = run_maker([script, os.path.abspath(os.path.expanduser(origin)), out], out, min(MAKE_TIMEOUT_S, deadline - time.monotonic()))
    if rc != 0 or not os.path.isfile(out) or not os.path.getsize(out):
        raise MakeError(f"변환 실패(종료 코드 {rc})")
    return out


def jobs_of(rep: dict, rs: dict, entry: dict) -> list:
    """아직 안 올린 파일 (키, 종류, 원본 경로, 내용 해시, 이름). 종류가 빈 문자열이면 원본(전송기가 넘긴 묶음만). 원본이 실패한 HTML 의 딸린 파일은 제외"""
    sent, done, jobs = rs.get("att_done") or {}, entry["done"], []
    for path in dict.fromkeys(rep["files"]):
        origin, name = sender.source_of(rep, path), os.path.basename(path) or path
        first = done.get(path, sent.get(path))
        if entry.get("bundle") and first is None:
            jobs.append((path, "", origin, "", name))
        elif not first:
            continue
        if sender.is_html(path) and (digest := sender.content_hash(origin)):
            jobs += [(path + s + digest, s, origin, digest, name) for s in sender.DERIVED if path + s + digest not in done]
    return jobs


def prepare(conf, rep: dict, rs: dict, entry: dict, save, deadline: float) -> "tuple[list, str, bool]":
    """올릴 (키, 경로, 표시 이름) 목록 · 변환 실패 사유 · 끝까지 봤는가(예산이 모자라면 False). 만든 것은 made 에 남겨 다음 회차가 다시 만들지 않음"""
    items, why, target, since = [], "", entry["target"], rs.get("att_start", 0)
    made, bad = entry.setdefault("made", {}), set()
    for key, suffix, origin, digest, name in jobs_of(rep, rs, entry):
        src = out_path(conf, suffix, name, digest) if suffix else origin
        if suffix and origin in bad:
            continue
        if fid := sender.uploaded_id(conf, src, target, since):  # 올린 뒤 기록 전에 죽은 경우
            entry["done"][key] = fid
        elif suffix and not (key in made and os.path.isfile(src)):
            if deadline - time.monotonic() < MIN_LEFT_S:
                return items, why, False
            try:
                if make(conf, suffix, origin, digest, name, deadline):
                    made[key] = src
                else:
                    sender.log(conf, f"{rep['id']} {sender.DERIVED[suffix][1]} 생략: {name}")
                    entry["done"][key] = ""
            except MakeError as err:
                if deadline - time.monotonic() <= 0:
                    return items, why, False  # 예산이 끝나 끊긴 것은 실패로 세지 않음
                why = str(err)
                sender.log(conf, f"{rep['id']} {sender.DERIVED[suffix][1]} 보류 {entry['tries'] + 1}회째: {name} — {why}")
        if key not in entry["done"] and (not suffix or key in made):
            if problem := sender.input_problem(src):
                sender.attach_failed(conf, rep, target, os.path.basename(src) if suffix else name, problem, deadline)
                entry["done"][key] = ""
                if not suffix:
                    bad.add(origin)
            else:
                items.append((key, src, os.path.basename(src) if suffix else name))
        save()
    return items, why, True


def drop_made(entry: dict) -> None:
    """올렸거나 포기한 딸린 파일 정리"""
    made = entry.get("made") or {}
    for key in [k for k in made if k in entry["done"]]:
        src = made.pop(key)
        with contextlib.suppress(OSError):
            os.unlink(src)
        if src.endswith(".pdf"):
            with contextlib.suppress(OSError):
                os.rmdir(os.path.dirname(src))  # 해시 폴더는 비었을 때만 지워짐


def give_up(conf, rep: dict, entry: dict, jobs: list, why: str, deadline: float) -> None:
    names = " · ".join(f"{j[4]} {sender.DERIVED[j[1]][1] if j[1] else ''}".strip() for j in jobs)
    for j in jobs:
        entry["done"][j[0]] = ""
    sender.attach_failed(conf, rep, entry["target"], names, why, deadline)


def waiting(reports: list, st: dict, mine: dict) -> list:
    """처리할 (보고, 전송 상태, 내 기록). 실패가 적은 것부터, 같으면 접수 순서"""
    rows = []
    for rep in reports:
        rs = st["reports"].get(rep["id"], {})
        mark = rs.get("derived")
        entry = mine.get(rep["id"])
        if entry is None and not isinstance(mark, dict):
            continue
        bundle = bool((mark or {}).get("bundle"))
        if not rs.get("sent") and not bundle:  # 묶음은 본문이 나간 뒤에 넘어옴. 진행 줄이 실패해도 첨부는 보냄
            continue
        if entry is None:
            old = {k: v for k, v in (rs.get("att_done") or {}).items() if bundle or any(s in k for s in sender.DERIVED)}
            entry = mine[rep["id"]] = {"done": old, "tries": 0, "next_at": 0, "target": mark.get("target", ""), "bundle": bundle}
        if not entry.get("finished") and entry.get("next_at", 0) <= time.time():
            rows.append((rep, rs, entry))
    return sorted(rows, key=lambda r: r[2].get("tries", 0))


def work(conf, rep: dict, rs: dict, entry: dict, save, deadline: float) -> bool:
    """보고 1건. 준비된 것을 메시지 하나로 올림. 예산이 모자라 멈췄으면 False"""
    if deadline - time.monotonic() < MIN_LEFT_S:
        return False
    items, why, complete = prepare(conf, rep, rs, entry, save, deadline)
    if not complete or (items and deadline - time.monotonic() < MIN_LEFT_S):
        return False
    try:
        sender.upload_all(conf, rep, entry["target"], items, entry, entry["done"], save, deadline)
    except sender.NotifyError as err:
        if deadline - time.monotonic() <= 0:
            return False
        why = str(err)
        sender.log(conf, f"{rep['id']} 첨부 보류 {entry['tries'] + 1}회째: {why}")
    finally:
        drop_made(entry)
        save()
    if why:
        entry["tries"] += 1
        entry["next_at"] = int(time.time() + DERIVED_BACKOFF_S * 2 ** (entry["tries"] - 1))
        if entry["tries"] >= DERIVED_TRIES:
            give_up(conf, rep, entry, jobs_of(rep, rs, entry), why, deadline)
            drop_made(entry)
            why = ""
    if not why:
        entry["finished"] = int(time.time())
    save()
    if not why:
        sender.drop_staged(conf, rep)
    return True


def run(conf, budget_s: float = DEFAULT_BUDGET_S) -> dict:
    """한 회차. {'status': done|busy|halted, 'left': 남은 보고 수}"""
    path, deadline = state_path(conf), time.monotonic() + budget_s
    with sender.exclusive(path) as got:
        if not got:
            return {"status": "busy", "left": 0}
        try:
            st, mine = sender.load_state(conf.state), load(path)
        except sender.StateCorrupt as err:
            return {"status": "halted", "left": 0, "error": str(err)}
        reports = hub_report.read_outbox(conf.outbox, [])
        rows = waiting(reports, st, mine)
        for rep, rs, entry in rows:
            if not work(conf, rep, rs, entry, lambda: sender.save_state(path, mine), deadline):
                break
        return {"status": "done", "left": sum(not e.get("finished") for e in mine.values())}


def thread_of(conf, file_id: str) -> str:
    """올린 파일의 스레드 ts(보낸 기록에서). 없으면 빈 문자열"""
    with contextlib.suppress(OSError):
        with open(conf.sent, encoding="utf-8") as f:
            for ln in f:
                with contextlib.suppress(ValueError):
                    d = json.loads(ln)
                    if isinstance(d, dict) and d.get("file_id") == file_id and sender.is_ts(d.get("thread_ts")):
                        return d["thread_ts"]
    return ""


def retry(conf, rid: str) -> str:
    """생략으로 끝난 보고를 다시 대기열에 넣음. 못 넣으면 사유. HTML 이 그때와 다르면 거부(Slack 의 HTML 과 어긋난 PDF 방지)"""
    path = state_path(conf)
    with sender.exclusive(path) as got:
        if not got:
            return "다른 회차 실행 중 — 잠시 뒤 다시"
        try:
            st, mine = sender.load_state(conf.state), load(path)
        except sender.StateCorrupt as err:
            return str(err)
        rep = next((r for r in hub_report.read_outbox(conf.outbox, []) if r["id"] == rid), None)
        rs = st["reports"].get(rid, {})
        if rep is None or not rs.get("sent"):
            return "전송된 보고 없음 — 보고 id 확인"
        was = mine.get(rid, {})
        old = {**{k: v for k, v in rs["att_done"].items() if any(s in k for s in sender.DERIVED)}, **was.get("done", {})}
        missed = [k for k, v in old.items() if not v]
        target = was.get("target") or next((t for p in rep["files"] if (f := rs["att_done"].get(p)) and (t := thread_of(conf, f))), "")
        kept = {k: v for k, v in old.items() if v}
        now = {j[0] for j in jobs_of(rep, rs, {**was, "done": kept})}
        if not missed or not set(missed) <= now:
            return "다시 만들 것 없음 — 생략된 파일이 없거나 HTML 이 바뀜"
        if not target:
            return "스레드 기록 없음 — 새 보고로 첨부"
        mine[rid] = {**{k: was[k] for k in ("bundle",) if k in was}, "done": kept, "tries": 0, "next_at": 0, "target": target}
        sender.save_state(path, mine)
    return ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="report_derived.py", description="첨부 HTML 의 PDF·미리보기를 만들어 같은 Slack 스레드에 올림.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="예:\n  report_derived.py --once\n  report_derived.py --retry 144eb50eeae3   # 생략된 보고를 다시 대기열에(전송은 다음 --once)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="한 회차 처리 후 종료")
    g.add_argument("--retry", metavar="보고id", help="생략으로 끝난 보고를 다시 대기열에 넣음")
    args = p.parse_args(argv)
    conf = sender.Conf()
    if args.retry:
        why = retry(conf, args.retry)
        print(f"오류: {why}" if why else f"대기열에 넣음: {args.retry}", file=sys.stderr if why else sys.stdout)
        return 1 if why else 0
    try:
        budget = float(os.environ.get("LRM_DERIVED_BUDGET_S") or DEFAULT_BUDGET_S)
    except ValueError:
        budget = DEFAULT_BUDGET_S
    res = run(conf, budget)
    if res["status"] == "halted":
        print(f"오류: {res['error']} — {state_path(conf)} 확인", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
