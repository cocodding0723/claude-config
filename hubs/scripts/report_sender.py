#!/usr/bin/env python3
"""허브 보고 전송기: hub_report.py 가 outbox 에 남긴 미전송 보고를 Slack 주제 스레드에 올림. 토큰은 slack_notify.sh 만 가짐.

작업 번호마다 주제 스레드 1개(없으면 시작 메시지를 만들고 ts 저장). 보고 본문은 그 스레드의 답글이고,
진행 스레드(ledger_sync.py 가 만든 부모)에는 한 줄 + 주제 스레드 링크만 올림. 진행 스레드 부모가 아직 없으면 줄은 생략.
kind=decide 는 스레드 답글이 아니라 slack_notify.sh --kind decide --options 버튼 카드 그대로 올림(카드 ts 로 링크).
상태 파일(전송 단계·ts·실패 횟수)은 이 프로세스만 씀. 단계마다 바로 저장해 중간에 죽어도 같은 글을 두 번 올리지 않음.
전송 성공한 것만 전송됨 표시. 실패는 다음 회차에 재시도하고 3회째 실패에 경고 카드를 1건만 보냄.
동시 실행은 상태 파일 옆 .lock 비차단 flock 으로 1개. 다른 회차가 진행 중이면 busy 로 건너뜀.

환경: LRM_HUB_DIR 작업 폴더 · LRM_REPORT_OUTBOX outbox · LRM_REPORT_STATE 상태 파일 · LRM_REPORT_NOTIFY 전송 스크립트(기본 같은 폴더 slack_notify.sh)
      LRM_LEDGER 장부(주제 제목용) · LRM_LEDGER_STATE ledger_sync 상태(진행 스레드 부모 ts) · LRM_REPORT_SENT 전송 기록(채널 조회) · LRM_REPORT_BUDGET_S 회차 시간 예산
종료 코드: 0 성공(처리할 것 없음·busy 포함), 1 일부 전송 실패(재시도 대상), 2 설정·입력 오류·상태 파일 손상(전송 중단, 복구 안내 출력)
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import hub_report  # noqa: E402
import hub_status  # noqa: E402
import ledger  # noqa: E402

HUB_DIR = hub_report.HUB_DIR
FAIL_LIMIT = 3  # 이 횟수째 실패에 경고 1건
NOTIFY_TIMEOUT_S = 30
DEFAULT_BUDGET_S = 40.0  # 회차 시간 예산. 넘기면 새 전송을 시작하지 않고 다음 회차로 넘김
MAX_PER_RUN = 10
LINE_MAX = 60
LINK_BASE = "https://slack.com/archives"
ERR_MAX = 100


@dataclass
class Conf:
    outbox: str = ""
    state: str = ""
    notify: str = ""
    ledger: str = ""
    sync_state: str = ""
    sent: str = ""
    log: str = ""
    budget_s: float = DEFAULT_BUDGET_S
    max_per_run: int = MAX_PER_RUN
    fail_limit: int = FAIL_LIMIT

    def __post_init__(self) -> None:
        env = os.environ.get
        join = lambda n: os.path.join(HUB_DIR, n)  # noqa: E731
        self.outbox = self.outbox or hub_report.outbox_path()
        self.state = self.state or env("LRM_REPORT_STATE") or join("report-sender.state.json")
        self.notify = self.notify or env("LRM_REPORT_NOTIFY") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "slack_notify.sh")
        self.ledger = self.ledger or env("LRM_LEDGER") or join("feedback-ledger.tsv")  # intake.py 와 같은 기본 경로
        self.sync_state = self.sync_state or env("LRM_LEDGER_STATE") or join("ledger-sync.state.json")
        self.sent = self.sent or env("LRM_REPORT_SENT") or join("slack-sent.jsonl")
        self.log = self.log or env("LRM_REPORT_LOG") or join("report-sender.log")
        try:
            self.budget_s = float(env("LRM_REPORT_BUDGET_S", self.budget_s))
        except ValueError:
            pass


class NotifyError(Exception):
    pass


def run_notify(conf: Conf, args: list, deadline: float) -> str:
    """전송 스크립트 1회 호출 → 새 메시지 ts. 실패는 NotifyError(사유 한 줄, 비밀 값 가림)"""
    left = deadline - time.monotonic()
    if left <= 0:
        raise NotifyError("회차 시간 예산 초과")
    try:
        p = subprocess.run([conf.notify, *args], capture_output=True, text=True, timeout=min(NOTIFY_TIMEOUT_S, left))
    except subprocess.TimeoutExpired:
        raise NotifyError("전송 시간 초과")
    except OSError as err:
        raise NotifyError(f"전송 스크립트 실행 실패({err.strerror})")
    last = (p.stdout.strip().splitlines() or [""])[-1]
    if p.returncode == 0 and last.startswith("ok ") and last[3:].strip():
        return last[3:].strip()
    why = " ".join((p.stderr.strip() or p.stdout.strip() or f"종료 코드 {p.returncode}").split())[:ERR_MAX]
    raise NotifyError("비밀 값 포함 오류 숨김" if hub_status.SECRET_RE.search(why) else why)


class StateCorrupt(Exception):
    """상태 파일이 있는데 못 읽음(손상·권한). 빈 상태로 보면 이미 올린 보고를 다시 올리므로 전송을 멈춤"""


STATE_KEYS = ("topics", "reports", "warned", "bad")


def load_state(path: str) -> dict:
    """파일이 없을 때만 초기 상태. 읽기 실패·JSON 손상·모양 오류는 StateCorrupt"""
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return {k: {} for k in STATE_KEYS}
    except OSError as err:
        raise StateCorrupt(f"읽기 실패({err.strerror})")
    except ValueError:
        raise StateCorrupt("JSON 손상")
    if not isinstance(st, dict) or any(k in st and not isinstance(st[k], dict) for k in STATE_KEYS):
        raise StateCorrupt("최상위·항목 모양 오류")
    return {k: st.get(k, {}) for k in STATE_KEYS}


def state_signature(path: str) -> str:
    """손상 경고 1회 판정용 지문. 같은 손상 상태에는 같은 값"""
    try:
        s = os.stat(path)
        return f"{s.st_mtime_ns}:{s.st_size}"
    except OSError as err:
        return f"oserror:{err.errno}"


def save_state(path: str, st: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".report-sender-", suffix=".tmp", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def exclusive(state_path: str):
    """상태 파일 옆 .lock 비차단 배타 잠금. 잡으면 True, 다른 프로세스가 쥐면 False"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
        lock = open(state_path + ".lock", "a")
    except OSError as err:
        raise hub_report.ReportError(f"전송 잠금 실패({err.strerror}) — 작업 폴더 권한 확인", hub_report.EXIT_IO)
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def read_json_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def channel_of(conf: Conf, ts: str) -> str:
    """메시지 ts 의 채널. 전송 기록에서 찾고, 없으면 진행 스레드 상태의 채널"""
    try:
        with open(conf.sent, encoding="utf-8") as f:
            for ln in reversed(f.read().splitlines()):
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("ts") == ts and d.get("channel"):
                    return d["channel"]
    except OSError:
        pass
    return read_json_file(conf.sync_state).get("channel", "")


def permalink(channel: str, ts: str) -> str:
    return f"{LINK_BASE}/{channel}/p{ts.replace('.', '')}" if channel and ts else ""


def request_of(conf: Conf, task: str) -> str:
    """주제 시작 메시지에 쓸 장부 요청 한 줄. 장부에 없으면 빈 문자열"""
    try:
        return next((ledger.clean(r["request"]) for r in ledger.read_rows(conf.ledger) if r["id"] == task), "")
    except ledger.LedgerError:
        return ""


def topic_key(rep: dict) -> str:
    return rep["task"] if rep["task"] != hub_report.NO_TASK else f"hub:{rep['hub']}"


def topic_text(conf: Conf, rep: dict) -> str:
    key = topic_key(rep)
    title = f"{rep['task']} 보고 스레드" if rep["task"] != hub_report.NO_TASK else f"{rep['hub']} 보고 스레드"
    lines = [title, "", f"• 허브: {rep['hub']}"]
    ask = request_of(conf, rep["task"]) if not key.startswith("hub:") else ""
    if ask:
        lines.append(f"• 요청: {ask[:LINE_MAX]}")
    return "\n".join(lines)


def body_text(rep: dict) -> str:
    label = hub_report.KINDS[rep["kind"]][1]
    lines = rep["text"].splitlines()
    if rep["kind"] == "decide":  # 첫 줄이 제목, 나머지가 배경. 선택지·답 줄은 slack_notify.sh 가 붙임
        out = [lines[0]] + ([""] + [f"• {ln}" for ln in lines[1:]] if lines[1:] else [])
    else:
        out = [f"{label} · {rep['hub']}", ""] + [f"• {ln}" for ln in lines]
    if rep["files"]:
        out += ["", "참고: " + ", ".join(rep["files"])]
    return "\n".join(out)


def progress_line(rep: dict, link: str) -> str:
    emoji, label, _ = hub_report.KINDS[rep["kind"]]
    first = rep["text"].splitlines()[0]
    first = first if len(first) <= LINE_MAX else first[: LINE_MAX - 1] + "…"
    where = f" · <{link}|{'결정 카드' if rep['kind'] == 'decide' else '주제 스레드'}>" if link else ""
    return f"{emoji} {rep['hub']} {label} · {first}{where}"


def decide_args(rep: dict) -> list:
    a = ["--tag", rep["hub"], "--kind", "decide", "--options", rep["options"], "--recommend", rep["recommend"], "--decision-id", rep["decision_id"]]
    return a + (["--reversible", rep["reversible"]] if rep.get("reversible") else [])


def deliver(conf: Conf, rep: dict, st: dict, deadline: float) -> dict:
    """보고 1건 전송. 단계(topic·body·line)마다 상태를 저장하고, 끝나면 결과 요약을 돌려줌. 실패는 NotifyError"""
    rs = st["reports"].setdefault(rep["id"], {})
    decide = rep["kind"] == "decide"
    key = topic_key(rep)
    if not decide and key not in st["topics"]:
        ts = run_notify(conf, ["--tag", rep["hub"], "--kind", "progress", topic_text(conf, rep)], deadline)
        st["topics"][key] = {"ts": ts, "channel": channel_of(conf, ts)}
        save_state(conf.state, st)
    topic = st["topics"].get(key, {})
    if "body_ts" not in rs:
        if decide:
            rs["body_ts"] = run_notify(conf, decide_args(rep) + [body_text(rep)], deadline)
        else:
            _, _, kind = hub_report.KINDS[rep["kind"]]
            rs["body_ts"] = run_notify(conf, ["--tag", rep["hub"], "--kind", kind, "--thread", topic["ts"], body_text(rep)], deadline)
        save_state(conf.state, st)
    if "line_ts" not in rs:
        parent = read_json_file(conf.sync_state).get("ts", "")
        if parent:
            target = rs["body_ts"] if decide else topic["ts"]
            channel = channel_of(conf, target) or topic.get("channel", "")
            rs["line_ts"] = run_notify(conf, ["--thread", parent, progress_line(rep, permalink(channel, target))], deadline)
        else:
            rs["line_ts"] = ""  # 진행 스레드 부모가 아직 없음. 본문은 이미 올라가 있어 줄만 생략
        save_state(conf.state, st)
    rs["sent"] = int(time.time())
    save_state(conf.state, st)
    return {"topic_ts": topic.get("ts", ""), "body_ts": rs["body_ts"], "line_ts": rs["line_ts"]}


def warn_failure(conf: Conf, rep: dict, why: str, deadline: float) -> None:
    label = hub_report.KINDS[rep["kind"]][1]
    msg = (f"허브 보고 전송 실패\n\n• 무엇: {rep['hub']} {label} 보고가 {conf.fail_limit}회 연속 전송 실패\n"
           f"• 원인: {why}\n• 결과: 다음 회차에도 재시도함")
    run_notify(conf, ["--tag", rep["hub"], "--kind", "warn", msg], deadline)


def log(conf: Conf, line: str) -> None:
    with contextlib.suppress(OSError):
        with open(conf.log, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")


def run(conf: Conf, dry: bool = False) -> dict:
    """한 회차. {'status': idle|done|partial|busy, 'sent': n, 'failed': n, 'pending': n, 'lines': [처리 로그]}"""
    out = {"status": "idle", "sent": 0, "failed": 0, "pending": 0, "lines": []}
    if dry:
        return _round(conf, out, True)
    with exclusive(conf.state) as got:
        if not got:
            out["status"] = "busy"
            return out
        return _round(conf, out, False)


def halt(conf: Conf, err: StateCorrupt, out: dict, dry: bool) -> dict:
    """상태 파일을 못 읽음. 전송을 멈추고 복구 안내를 돌려줌. 메인 경고는 같은 손상 상태에 1번만"""
    out.update(status="halted", error=f"상태 파일 {err} — {conf.state}",
               guide="내용을 확인해 고치거나, 이미 올라간 보고가 다시 올라가도 괜찮으면 파일을 지운 뒤 다시 실행")
    marker = conf.state + ".warned"
    sig = state_signature(conf.state)
    if dry or read_text(marker) == sig:
        return out
    msg = f"허브 보고 전송 중단\n\n• 원인: 상태 파일 {err}\n• 결과: 보고 전송 멈춤(같은 보고 재게시 방지)\n• 조치: {out['guide']}"
    try:
        run_notify(conf, ["--kind", "warn", msg], time.monotonic() + NOTIFY_TIMEOUT_S)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(sig)
    except (NotifyError, OSError):
        pass  # 경고가 안 나가면 다음 회차에 다시 시도
    return out


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def quarantine(conf: Conf, bad: list, st: dict, dry: bool) -> int:
    """형식이 깨진 outbox 줄을 <outbox>.corrupt 로 한 번만 옮겨 적고(상태에 해시 기록) 건수를 돌려줌. outbox 자체는 고치지 않음"""
    fresh = [ln for ln in bad if hashlib.sha1(ln.encode("utf-8")).hexdigest()[:12] not in st["bad"]]
    if fresh and not dry:
        with contextlib.suppress(OSError):
            fd = os.open(conf.outbox + ".corrupt", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write("".join(ln + "\n" for ln in fresh))
            for ln in fresh:
                st["bad"][hashlib.sha1(ln.encode("utf-8")).hexdigest()[:12]] = int(time.time())
            save_state(conf.state, st)
            log(conf, f"불량 보고 {len(fresh)}건 격리 → {conf.outbox}.corrupt")
    return len(bad)


def _round(conf: Conf, out: dict, dry: bool) -> dict:
    bad = []
    reports = hub_report.read_outbox(conf.outbox, bad)
    try:
        st = load_state(conf.state)
    except StateCorrupt as err:
        return halt(conf, err, out, dry)
    out["bad"] = quarantine(conf, bad, st, dry)
    todo = [r for r in reports if not st["reports"].get(r["id"], {}).get("sent")]
    out["pending"] = len(todo)
    if not todo:
        return out
    if dry:
        out["status"] = "dry-run"
        out["lines"] = [f"전송 예정 {r['id']} {r['hub']} {r['kind']} → {'결정 카드' if r['kind'] == 'decide' else '주제 ' + topic_key(r)}" for r in todo]
        return out
    deadline = time.monotonic() + conf.budget_s
    blocked = set()  # 앞 보고가 실패한 주제는 순서가 바뀌지 않게 이번 회차에 건너뜀
    tried = 0
    for rep in todo:
        key = topic_key(rep)
        if key in blocked:
            continue  # 건너뛴 보고는 시도 건수에 세지 않음(막힌 주제가 다른 주제를 굶기지 않게)
        if tried >= conf.max_per_run:
            break
        tried += 1
        fails = st["reports"].setdefault(rep["id"], {})
        try:
            res = deliver(conf, rep, st, deadline)
        except NotifyError as err:
            fails["fails"], fails["error"] = fails.get("fails", 0) + 1, str(err)
            if fails["fails"] >= conf.fail_limit and rep["id"] not in st["warned"]:
                try:
                    warn_failure(conf, rep, str(err), deadline)
                    st["warned"][rep["id"]] = int(time.time())
                except NotifyError:
                    pass  # 경고 전송 실패는 다음 회차에 다시 시도
            save_state(conf.state, st)
            blocked.add(key)
            out["failed"] += 1
            line = f"실패 {rep['id']} {rep['hub']} {rep['kind']} {fails['fails']}회째: {err}"
        else:
            out["sent"] += 1
            line = (f"전송 {rep['id']} {rep['hub']} {rep['kind']} → "
                    + (f"결정 카드 {res['body_ts']}" if rep["kind"] == "decide" else f"주제 {key} 스레드 {res['topic_ts']} 답글 {res['body_ts']}")
                    + (f" · 진행 줄 {res['line_ts']}" if res["line_ts"] else " · 진행 줄 생략"))
        out["lines"].append(line)
        log(conf, line)
    out["status"] = "partial" if out["failed"] else "done"
    return out


class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"오류: {message} — --help 참고\n")
        sys.exit(2)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(
        prog="report_sender.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="outbox 의 미전송 허브 보고를 Slack 주제 스레드에 올리고 진행 스레드에 한 줄 + 링크를 남김.",
        epilog="예:\n  report_sender.py --once --dry-run   # 보낼 보고만 출력(전송·상태 저장 없음)\n  report_sender.py --once             # 실제 전송(보통 slack_poll.sh 회차 끝에서 호출)\n"
               "환경: LRM_HUB_DIR, LRM_REPORT_OUTBOX, LRM_REPORT_STATE, LRM_REPORT_NOTIFY, LRM_LEDGER, LRM_LEDGER_STATE, LRM_REPORT_BUDGET_S\n"
               "종료 코드: 0 성공(처리할 것 없음·busy 포함), 1 일부 전송 실패(재시도 대상), 2 설정·인자 오류·상태 파일 손상(전송 중단)")
    p.add_argument("--once", action="store_true", required=True, help="한 회차 처리 후 종료(필수)")
    p.add_argument("--dry-run", action="store_true", help="전송·상태 저장 없이 보낼 보고만 출력")
    p.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로 출력")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        res = run(Conf(), args.dry_run)
    except hub_report.ReportError as err:
        sys.stderr.write(f"오류: {err}\n")
        return err.code
    if res["status"] == "halted":
        sys.stderr.write(f"오류: {res['error']} — {res['guide']}\n")
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for ln in res["lines"]:
            print(ln)
        print(f"전송기: {res['status']} · 전송 {res['sent']} · 실패 {res['failed']} · 대기 {res['pending']}")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
