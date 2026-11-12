#!/usr/bin/env python3
"""허브 보고 전송기: hub_report.py 가 outbox 에 남긴 미전송 보고를 Slack 주제 스레드에 올림. 토큰은 slack_notify.sh 만 가짐.

작업 번호마다 주제 스레드 1개(없으면 시작 메시지를 만들고 ts 저장). 보고 본문은 그 스레드의 답글이고,
진행 스레드(ledger_sync.py 가 만든 부모)에는 한 줄 + 주제 스레드 링크만 올림. 진행 스레드 부모가 아직 없으면 줄은 생략.
kind=decide 는 스레드 답글이 아니라 slack_notify.sh --kind decide --options 버튼 카드 그대로 올림(카드 ts 로 링크).
상태 파일(전송 단계·ts·실패 횟수)은 이 프로세스만 씀. 단계마다 바로 저장해 중간에 죽어도 같은 글을 두 번 올리지 않음.
전송 성공한 것만 전송됨 표시. 실패는 영구(입력·형식 오류 = 알림 스크립트 종료 코드 2)와 일시(네트워크·429·5xx·시간 초과)로 나눔.
영구 실패는 재시도 없이 바로, 일시 실패는 지수 대기(BACKOFF)로 FAIL_LIMIT 회까지 재시도한 뒤 같은 방식으로 중단함:
상태 bad 에 보고 id 를 기록해 재시도 대상에서 빼고, 원래 주제 스레드(없으면 DM)에 원인·조치 경고를 1건만 보냄.
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
FAIL_LIMIT = 6  # 일시 실패 재시도 상한. 이 횟수째 실패에 중단·경고 1건
BACKOFF_BASE_S, BACKOFF_MAX_S = 60, 1800  # 일시 실패 뒤 대기 = BASE·2^(실패-1), 상한 MAX. 1시간 안팎에서 상한 도달
PERMANENT_RC = 2  # 알림 스크립트 종료 코드: 설정·인자·양식 오류 = 재시도해도 같은 결과
USER_FIX_MARKS = ("slack-bot.env 없음",)  # 종료 코드 2 지만 사용자가 파일을 만들면 풀리는 환경 문제 = 일시로 취급
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
    backoff_s: float = BACKOFF_BASE_S

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
    """permanent: 재시도해도 같은 결과. counts: 실패 횟수에 셈(회차 예산 초과는 전송을 시도하지 않았으므로 세지 않음)"""

    def __init__(self, msg: str, permanent: bool = False, counts: bool = True) -> None:
        super().__init__(msg)
        self.permanent, self.counts = permanent, counts


def run_notify(conf: Conf, args: list, deadline: float) -> str:
    """전송 스크립트 1회 호출 → 새 메시지 ts. 실패는 NotifyError(사유 한 줄, 비밀 값 가림)"""
    left = deadline - time.monotonic()
    if left <= 0:
        raise NotifyError("회차 시간 예산 초과", counts=False)
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
    perm = p.returncode == PERMANENT_RC and not any(m in why for m in USER_FIX_MARKS)
    raise NotifyError("비밀 값 포함 오류 숨김" if hub_status.SECRET_RE.search(why) else why, permanent=perm)


class StateCorrupt(Exception):
    """상태 파일이 있는데 못 읽음(손상·권한). 빈 상태로 보면 이미 올린 보고를 다시 올리므로 전송을 멈춤"""


STATE_KEYS = ("topics", "reports", "warned", "bad")
REQUIRED_KEYS = STATE_KEYS[:3]  # bad 는 뒤에 생긴 키라 옛 상태 파일에는 없을 수 있음


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
    if not isinstance(st, dict) or any(not isinstance(st.get(k), dict) for k in REQUIRED_KEYS) or not isinstance(st.get("bad", {}), dict):
        raise StateCorrupt("필수 항목(topics·reports·warned) 없음 또는 모양 오류")  # {} 로 비워진 파일을 새 상태로 보면 보고를 다시 올림
    if (any(not isinstance(v, dict) or not is_ts(v.get("ts")) for v in st["topics"].values())
            or any(not report_state_ok(v) for v in st["reports"].values())
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in st["warned"].values())):
        raise StateCorrupt("항목 내용 모양 오류")
    return {k: st.get(k, {}) for k in STATE_KEYS}


def is_ts(v) -> bool:
    """비어 있지 않은 Slack ts(숫자.숫자)인가"""
    return isinstance(v, str) and bool(hub_report.TS_RE.match(v))


def report_state_ok(v) -> bool:
    """보고 1건의 전송 단계 기록이 일관된가. body_ts 는 있으면 ts 형식, line_ts 는 ts 형식이거나 빈 문자열(진행 줄 생략),
    전송됨(sent)이면 body_ts·line_ts 가 모두 있어야 함. 아니면 손상(빈 body_ts 로 본문을 건너뛴 채 전송됨 처리되는 것을 막음)"""
    if not isinstance(v, dict):
        return False
    sent, fails = v.get("sent", 0), v.get("fails", 0)
    if isinstance(sent, bool) or not isinstance(sent, (int, float)) or isinstance(fails, bool) or not isinstance(fails, int):
        return False
    if "error" in v and not isinstance(v["error"], str):
        return False
    if "body_ts" in v and not is_ts(v["body_ts"]):
        return False
    if "line_ts" in v and not (v["line_ts"] == "" or is_ts(v["line_ts"])):
        return False
    return not sent or ("body_ts" in v and "line_ts" in v)


def check_consistency(st: dict, reports: list) -> None:
    """상태와 outbox 의 교차 검증: 전송됨인 주제 보고는 그 주제의 ts 가 상태에 있어야 함(없으면 상태가 잘렸거나 손으로 고쳐진 것)"""
    for r in reports:
        if st["reports"].get(r["id"], {}).get("sent") and r["kind"] != "decide" and not r.get("thread") and topic_key(r) not in st["topics"]:
            raise StateCorrupt(f"전송됨 보고 {r['id']} 의 주제 스레드 기록 없음")


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
    if rep.get("thread"):
        return f"thread:{rep['thread']}"  # 지정 스레드별로 순서 유지. 주제 스레드는 만들지 않음
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
    if rep.get("thread"):  # 지정 스레드 답글. 진행 스레드 한 줄은 올리지 않음
        if "body_ts" not in rs:
            _, _, kind = hub_report.KINDS[rep["kind"]]
            rs["body_ts"] = run_notify(conf, ["--tag", rep["hub"], "--kind", kind, "--thread", rep["thread"], body_text(rep)], deadline)
            save_state(conf.state, st)
        rs["line_ts"] = ""
        rs["sent"] = int(time.time())
        save_state(conf.state, st)
        return {"topic_ts": rep["thread"], "body_ts": rs["body_ts"], "line_ts": "", "pinned": True}
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


def warn_stop(conf: Conf, rep: dict, st: dict, why: str, permanent: bool, deadline: float) -> None:
    """재시도 중단 경고 1건(원인 + 조치). 원래 주제 스레드가 있으면 그 답글, 없으면(결정 카드 등) DM 새 글"""
    label = hub_report.KINDS[rep["kind"]][1]
    thread = rep.get("thread") or st["topics"].get(topic_key(rep), {}).get("ts", "")
    cause = f"입력 오류(재시도해도 같은 결과) — {why}" if permanent else f"{conf.fail_limit}회 연속 전송 실패 — {why}"
    msg = (f"허브 보고 전송 중단\n\n• 무엇: {rep['hub']} {label} 보고 {rep['id']} 재시도 중단\n"
           f"• 원인: {cause}\n• 조치: 보고를 고쳐 hub_report.py add 로 다시 기록")
    run_notify(conf, ["--tag", rep["hub"], "--kind", "warn"] + (["--thread", thread] if thread else []) + [msg], deadline)


def stop_report(conf: Conf, rep: dict, st: dict, why: str, permanent: bool, deadline: float) -> None:
    """보고를 재시도 대상에서 빼고(bad) 경고 1건. 경고 전송이 실패하면 다음 회차에 경고만 다시 시도"""
    st["bad"][rep["id"]] = int(time.time())
    st["reports"].setdefault(rep["id"], {})["error"] = why
    save_state(conf.state, st)
    warn_pending(conf, rep, st, deadline, permanent)


def warn_pending(conf: Conf, rep: dict, st: dict, deadline: float, permanent: bool = False) -> None:
    if rep["id"] in st["warned"]:
        return
    try:
        warn_stop(conf, rep, st, st["reports"].get(rep["id"], {}).get("error", "원인 미확인"), permanent, deadline)
        st["warned"][rep["id"]] = int(time.time())
        save_state(conf.state, st)
    except NotifyError:
        pass  # 경고 전송 실패는 다음 회차에 다시 시도


def input_error(rep: dict) -> str:
    """전송 전에 알 수 있는 영구 오류(접수 시점 검사와 같은 규칙). 없으면 빈 문자열"""
    if rep["kind"] == "decide" and not hub_report.DECISION_ID_RE.fullmatch(rep["decision_id"]):
        return hub_report.DECISION_ID_ERR
    return ""


def log(conf: Conf, line: str) -> None:
    with contextlib.suppress(OSError):
        with open(conf.log, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")


def run(conf: Conf, dry: bool = False) -> dict:
    """한 회차. {'status': idle|done|partial|busy, 'sent': n, 'failed': n, 'pending': n, 'lines': [처리 로그]}"""
    out = {"status": "idle", "sent": 0, "failed": 0, "pending": 0, "lines": []}
    deadline = time.monotonic() + conf.budget_s  # 회차 공통 시간 예산. 경고·주제·본문·진행 줄 전송이 모두 이 안에서 끝남
    if dry:
        return _round(conf, out, True, deadline)
    with exclusive(conf.state) as got:
        if not got:
            out["status"] = "busy"
            return out
        return _round(conf, out, False, deadline)


def halt(conf: Conf, err: StateCorrupt, out: dict, dry: bool, deadline: float) -> dict:
    """상태 파일을 못 읽음. 전송을 멈추고 복구 안내를 돌려줌. 메인 경고는 같은 손상 상태에 1번만"""
    out.update(status="halted", error=f"상태 파일 {err} — {conf.state}",
               guide="내용을 확인해 고치거나, 이미 올라간 보고가 다시 올라가도 괜찮으면 파일을 지운 뒤 다시 실행")
    marker = conf.state + ".warned"
    sig = state_signature(conf.state)
    if dry or read_text(marker) == sig:
        return out
    msg = f"허브 보고 전송 중단\n\n• 원인: 상태 파일 {err}\n• 결과: 보고 전송 멈춤(같은 보고 재게시 방지)\n• 조치: {out['guide']}"
    try:
        run_notify(conf, ["--kind", "warn", msg], deadline)  # 회차 공통 deadline 을 지킴
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


LOCK_POLL_S = 0.02


@contextlib.contextmanager
def try_lock(path: str, deadline: float):
    """배타 잠금을 deadline 안에서만 비차단으로 시도. 잡으면 True, 못 잡으면 False(대기 무기한 금지)"""
    lock = None
    try:
        lock = open(path + ".lock", "a")
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() + LOCK_POLL_S >= deadline:
                    yield False
                    return
                time.sleep(LOCK_POLL_S)
        yield True
    except OSError:
        yield False
    finally:
        if lock is not None:
            lock.close()


def repair_outbox(conf: Conf, deadline: float) -> None:
    """읽기 전에 잠금 아래에서 끝줄 개행 복구(정상 JSON 끝 개행 누락·잘린 줄 격리). 남은 예산 안에서 잠금을 못 잡거나 복구가 실패하면
    다음 회차로 미룸(개행 없는 끝줄은 읽지 않으므로 보고가 사라지지 않음)"""
    if not os.path.exists(conf.outbox):
        return
    with try_lock(conf.outbox, deadline) as got:
        if not got:
            log(conf, "outbox 끝줄 복구 보류: 잠금 대기 예산 초과")
            return
        try:
            hub_report.repair_tail(conf.outbox)
        except hub_report.ReportError as err:
            log(conf, f"outbox 끝줄 복구 실패: {err}")


def _round(conf: Conf, out: dict, dry: bool, deadline: float) -> dict:
    if not dry:
        repair_outbox(conf, deadline)
    bad = []
    reports = hub_report.read_outbox(conf.outbox, bad)
    try:
        st = load_state(conf.state)
    except StateCorrupt as err:
        return halt(conf, err, out, dry, deadline)
    try:
        check_consistency(st, reports)
    except StateCorrupt as err:
        return halt(conf, err, out, dry, deadline)
    out["bad"] = quarantine(conf, bad, st, dry)
    if not dry:
        for r in reports:  # 중단됐지만 경고가 안 나간 보고
            if r["id"] in st["bad"] and r["id"] not in st["warned"] and not st["reports"].get(r["id"], {}).get("sent"):
                warn_pending(conf, r, st, deadline, True)
    todo = [r for r in reports if not st["reports"].get(r["id"], {}).get("sent") and r["id"] not in st["bad"]]
    out["pending"] = len(todo)
    if not todo:
        return out
    if dry:
        out["status"] = "dry-run"
        out["lines"] = [f"전송 예정 {r['id']} {r['hub']} {r['kind']} → {'결정 카드' if r['kind'] == 'decide' else '주제 ' + topic_key(r)}" for r in todo]
        return out
    blocked = set()  # 앞 보고가 실패한 주제는 순서가 바뀌지 않게 이번 회차에 건너뜀
    tried = 0
    for rep in todo:
        key = topic_key(rep)
        if key in blocked:
            continue  # 건너뛴 보고는 시도 건수에 세지 않음(막힌 주제가 다른 주제를 굶기지 않게)
        if tried >= conf.max_per_run:
            break
        fails = st["reports"].setdefault(rep["id"], {})
        if fails.get("next_at", 0) > time.time():
            blocked.add(key)  # 대기 중. 같은 주제의 뒤 보고도 순서 유지를 위해 함께 대기
            continue
        tried += 1
        bad_input = input_error(rep)
        if bad_input or fails.get("fails", 0) >= conf.fail_limit:  # 접수 전 쌓인 영구 오류·상한 초과 기록은 호출 없이 중단
            why = bad_input or fails.get("error", "재시도 상한 초과")
            stop_report(conf, rep, st, why, bool(bad_input), deadline)
            out["stopped"] = out.get("stopped", 0) + 1
            line = f"중단 {rep['id']} {rep['hub']} {rep['kind']}: {why}"
            out["lines"].append(line)
            log(conf, line)
            continue
        try:
            res = deliver(conf, rep, st, deadline)
        except NotifyError as err:
            if not err.counts:
                blocked.add(key)
                continue
            fails["fails"], fails["error"] = fails.get("fails", 0) + 1, str(err)
            fails["next_at"] = int(time.time() + min(conf.backoff_s * 2 ** (fails["fails"] - 1), BACKOFF_MAX_S))
            blocked.add(key)
            if err.permanent or fails["fails"] >= conf.fail_limit:
                stop_report(conf, rep, st, str(err), err.permanent, deadline)
                out["stopped"] = out.get("stopped", 0) + 1
                line = f"중단 {rep['id']} {rep['hub']} {rep['kind']} {fails['fails']}회째({'영구' if err.permanent else '상한'}): {err}"
            else:
                save_state(conf.state, st)
                out["failed"] += 1
                line = f"실패 {rep['id']} {rep['hub']} {rep['kind']} {fails['fails']}회째: {err}"
        else:
            out["sent"] += 1
            line = (f"전송 {rep['id']} {rep['hub']} {rep['kind']} → "
                    + (f"결정 카드 {res['body_ts']}" if rep["kind"] == "decide" else f"주제 {key} 스레드 {res['topic_ts']} 답글 {res['body_ts']}")
                    + (f" · 진행 줄 {res['line_ts']}" if res["line_ts"] else " · 진행 줄 없음(스레드 지정)" if res.get("pinned") else " · 진행 줄 생략"))
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
        print(f"전송기: {res['status']} · 전송 {res['sent']} · 실패 {res['failed']} · 중단 {res.get('stopped', 0)} · 대기 {res['pending']}")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
