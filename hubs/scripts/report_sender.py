#!/usr/bin/env python3
"""허브 보고 전송기: hub_report.py 가 outbox 에 남긴 미전송 보고를 Slack 주제 스레드에 올림. 토큰은 slack_notify.sh 만 가짐.

작업 번호마다 주제 스레드 1개(없으면 시작 메시지를 만들고 ts 저장). 보고 본문은 그 스레드의 답글이고,
진행 스레드(ledger_sync.py 가 만든 부모)에는 한 줄 + 주제 스레드 링크만 올림. 진행 스레드 부모가 아직 없으면 줄은 생략.
kind=decide 는 스레드 답글이 아니라 slack_notify.sh --kind decide --options 버튼 카드 그대로 올림(카드 ts 로 링크).
상태 파일(전송 단계·ts·실패 횟수)은 이 프로세스만 씀. 단계마다 바로 저장해 중간에 죽어도 같은 글을 두 번 올리지 않음.
전송 성공한 것만 전송됨 표시. 실패는 영구(입력·형식 오류 = 알림 스크립트 종료 코드 2)와 일시(네트워크·429·5xx·시간 초과)로 나눔.
영구 실패는 재시도 없이 바로, 일시 실패는 지수 대기(BACKOFF)로 FAIL_LIMIT 회까지 재시도한 뒤 같은 방식으로 중단함:
상태 bad 에 보고 id 를 기록해 재시도 대상에서 빼고, 원래 주제 스레드(없으면 DM)에 원인·조치 경고를 1건만 보냄.
카드 주제 보고에서 카드 설명의 무엇·왜 칸이 비면(원료 폴백으로도) 영구 중단이 아니라 보류: 회차마다 카드를 다시 읽고, 고쳐지면 그대로 나감.
보류 중에는 카드당 하루 1회 DM 경고(원인 + 조치).
동시 실행은 상태 파일 옆 .lock 비차단 flock 으로 1개. 다른 회차가 진행 중이면 busy 로 건너뜀.

첨부: 보고의 files(--file)는 본문 뒤에 Slack 파일로 올림. 올리는 곳은 일반 보고 = 주제 스레드, 지정 스레드 보고 = 그 스레드,
결정 카드 = 카드 스레드(카드를 먼저 올림. 파일 업로드 메시지에는 버튼을 달 수 없음). 파일마다 알림 스크립트를 1회 호출(업로드 방식은 거기에 있음).
접수 때 복사본(staged)이 있으면 그걸 올리고, 보고가 끝나면(전송·중단) 복사본을 지움. 못 지운 것은 STAGE_KEEP_S 뒤 회차 시작에 정리함.
복사본이 없는 옛 기록은 원본을 읽는데, launchd 에서는 macOS 개인정보 보호(TCC)가 ~/Documents 등 읽기를 막아 curl 이 종료 코드 26 으로 실패함.
올리기 전에 직접 열어 보고 막히면 재시도 없이 'UNREADABLE' 한 줄만 스레드에 남김(다시 hub_report.py add 로 보고하면 접수 때 복사됨).
없는 파일·폴더·크기 초과는 입력 문제라 재시도 없이 스레드에 '첨부 실패: 이유' 한 줄만 남기고 보고는 계속함. 네트워크 등 일시 실패는 보고 재시도 규칙(BACKOFF)을 따르고 올린 파일은 다시 올리지 않음.
HTML 은 html_pdf.sh 로 PDF(Slack 에서 바로 열림), html_preview.sh 로 390폭 미리보기 PNG 를 만들어 이어서 올림. 못 만든 것은 생략하고 계속함.
본문에 로컬 경로만 있고 첨부가 없으면 로그에 경고(접수 경고와 같은 판정 hub_report.unattached_paths).

환경: LRM_HUB_DIR 작업 폴더 · LRM_REPORT_OUTBOX outbox · LRM_REPORT_STATE 상태 파일 · LRM_REPORT_NOTIFY 전송 스크립트(기본 같은 폴더 slack_notify.sh) · LRM_REPORT_PREVIEW HTML 미리보기 스크립트(기본 같은 폴더 html_preview.sh) · LRM_REPORT_PDF HTML→PDF 스크립트(기본 같은 폴더 html_pdf.sh)
      LRM_LEDGER 장부(주제 제목용) · LRM_LEDGER_STATE ledger_sync 상태(진행 스레드 부모 ts) · LRM_REPORT_SENT 전송 기록(채널 조회) · LRM_REPORT_BUDGET_S 회차 시간 예산
종료 코드: 0 성공(처리할 것 없음·busy 포함), 1 일부 전송 실패(재시도 대상), 2 설정·입력 오류·상태 파일 손상(전송 중단, 복구 안내 출력)
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import board_cards  # noqa: E402
import hub_report  # noqa: E402
import hub_status  # noqa: E402
import ledger  # noqa: E402

HUB_DIR = hub_report.HUB_DIR
FAIL_LIMIT = 6  # 일시 실패 재시도 상한. 이 횟수째 실패에 중단·경고 1건
BACKOFF_BASE_S, BACKOFF_MAX_S = 60, 1800  # 일시 실패 뒤 대기 = BASE·2^(실패-1), 상한 MAX. 1시간 안팎에서 상한 도달
PERMANENT_RC = 2  # 알림 스크립트 종료 코드: 설정·인자·양식 오류 = 재시도해도 같은 결과
# 종료 코드 2 지만 사용자가 파일을 만들면 풀리는 환경 문제 = 일시. stderr 전체가 이 줄과 같을 때만 인정(입력값이 섞인 메시지를 부분 일치로 분류하지 않음)
USER_FIX_LINES = ("slack-bot.env 없음 — 사용자 설정 필요",)
NOTIFY_TIMEOUT_S = 30
UPLOAD_TIMEOUT_S = 120  # 파일 업로드는 본문 전송보다 오래 걸릴 수 있음
PREVIEW_TIMEOUT_S = 30
PREVIEW_SUFFIX = "#preview:"  # 미리보기 단계 키 = 원본 경로 + 이 접미사 + 원본 내용 해시(att_done). 임시 경로에 기대지 않아 회차가 바뀌어도 같은 키
PDF_SUFFIX = "#pdf:"  # PDF 단계 키. 미리보기와 같은 규칙(원본 경로 + 접미사 + 내용 해시)
HTML_EXTS = (".html", ".htm")
# HTML 한 개에 딸려 올라가는 파일(올리는 순서대로): 키 접미사 → (Conf 의 스크립트 칸, 로그·안내에 쓰는 이름)
DERIVED = {PDF_SUFFIX: ("pdf", "PDF"), PREVIEW_SUFFIX: ("preview", "미리보기")}
DEFAULT_BUDGET_S = 40.0  # 회차 시간 예산. 넘기면 새 전송을 시작하지 않고 다음 회차로 넘김
MAX_PER_RUN = 10
LINE_MAX = 60
ERR_MAX = 100
STAGE_KEEP_S = 24 * 3600  # 남은 복사본 보관 기간
# 카드 주제 부모 메시지(양식 ①~⑥). 양식 문구는 이 블록 한 곳. 버전을 올리면 열린 스레드 부모가 새 양식으로 다시 고쳐짐
CARD_RE = re.compile(r"[a-z][a-z0-9]{1,15}-[a-z0-9]{1,12}(\.[0-9]{1,3})*")  # slack_notify.sh CARD_ID_RE 와 같은 카드 id
PARENT_FORMAT = "1"
PARENT_TITLE_MAX = 38  # 양식 A 제목 40자 한도에서 이모지 여유
PARENT_FIELD_MAX = 200
CARD_FIELDS = ("쉬운 이름", "무엇", "왜")
CARD_REQUIRED = ("무엇",)  # 왜는 비어도 보냄(WHY_MISSING). 무엇은 제목 폴백까지 비는 경우만 보류
CARD_FIELD_RE = re.compile(r"^\s*(?:[-•*]\s*)?(" + "|".join(CARD_FIELDS) + r")\s*:\s*(.*\S)\s*$", re.M)
CARD_META_KEYS = ("순서", "우선순위", "라벨", "의존")  # 설명 머리의 메타 줄. 무엇 폴백 문장에서 제외
CARD_SKIP_RE = re.compile(r"\b[a-z]{2,8}-[a-z0-9]*\d[a-z0-9]*(\.\d{1,3})*\b|`")  # 폴백 문장에서 뺄 내부 기호(카드 id·백틱)
CARD_QUOTE_RE = re.compile(r"원문\(.*?\)\s*:\s*[\"“]?(.+?)[\"”]?\s*$")  # '원문(날짜…): "사용자 말"' 줄
CARD_ASK_HEAD_RE = re.compile(r"^#+\s*지시")
CARD_WARN_INTERVAL_S = 24 * 3600  # 같은 카드 칸 경고 간격
HOLD_MAX_S = 6 * 3600  # 칸이 비어 보고를 보류하는 최대 시간(접수 시각 기준). 넘으면 있는 칸만으로 보냄(환경 LRM_HOLD_MAX_S)
WHY_MISSING = "카드에 이유가 안 적혀 있음 — 담당 허브가 채울 예정"  # 왜 칸 끝내 빈 카드의 표시 줄(보류하지 않고 이 줄로 보냄)
TITLE_TAG_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")  # 카드 제목 머리표([impl] 등)
TITLE_CARD_RE = re.compile(r"^\s*카드\s*[:：-]?\s*")  # 머리표·번호를 뺀 뒤 남는 '카드' 머리말
UPDATE_DEAD_ERRORS = ("message_not_found", "cant_update_message")  # 다시 시도해도 같은 결과인 고쳐 쓰기 실패
PARENT_HOW_TO = "이 스레드에 진행·질문·결과가 올라옴. 답은 이 스레드에 달면 작업 카드로 감"
CARD_GAP_FIX = "카드 설명에 '무엇:'·'왜:' 줄 추가(bd update {card} -d ...). 추가하면 다음 회차에 자동 전송"
WHY_GAP_FIX = "카드 설명에 '왜:' 줄 추가(bd update {card} -d ...). 보고는 이미 '이유 미기재' 표시로 나갔고, 추가하면 스레드 첫 메시지가 고쳐짐"
BD_FALLBACK = "/opt/homebrew/bin/bd"  # launchd 의 짧은 PATH 에서 bd 를 못 찾을 때
BD_TIMEOUT_S = 10
UNREADABLE = "백그라운드에서 읽을 수 없는 폴더 — 다시 보고"
WS_THREAD_MAX = 10  # 기능 하나가 동시에 여는 작업 공간 스레드 상한. 넘으면 새 작업 공간 보고는 기능 스레드 답글로 감(환경 LRM_WS_THREAD_MAX)


@dataclass
class Conf:
    outbox: str = ""
    state: str = ""
    notify: str = ""
    preview: str = ""
    pdf: str = ""
    ledger: str = ""
    sync_state: str = ""
    sent: str = ""
    log: str = ""
    budget_s: float = DEFAULT_BUDGET_S
    max_per_run: int = MAX_PER_RUN
    fail_limit: int = FAIL_LIMIT
    backoff_s: float = BACKOFF_BASE_S
    ws_thread_max: int = WS_THREAD_MAX
    hold_max_s: float = HOLD_MAX_S
    bd: str = ""
    board: str = ""
    cards: dict = field(default_factory=dict)  # 한 Conf(회차) 안의 카드 조회 캐시

    def __post_init__(self) -> None:
        env = os.environ.get
        join = lambda n: os.path.join(HUB_DIR, n)  # noqa: E731
        self.outbox = self.outbox or hub_report.outbox_path()
        self.state = self.state or env("LRM_REPORT_STATE") or join("report-sender.state.json")
        self.notify = self.notify or env("LRM_REPORT_NOTIFY") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "slack_notify.sh")
        self.preview = self.preview or env("LRM_REPORT_PREVIEW") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "html_preview.sh")
        self.pdf = self.pdf or env("LRM_REPORT_PDF") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "html_pdf.sh")
        self.ledger = self.ledger or env("LRM_LEDGER") or join("feedback-ledger.tsv")  # intake.py 와 같은 기본 경로
        self.sync_state = self.sync_state or env("LRM_LEDGER_STATE") or join("ledger-sync.state.json")
        self.sent = self.sent or env("LRM_REPORT_SENT") or join("slack-sent.jsonl")
        self.log = self.log or env("LRM_REPORT_LOG") or join("report-sender.log")
        self.bd = self.bd or env("LRM_REPORT_BD") or shutil.which("bd") or BD_FALLBACK
        self.board = self.board or env("BOARD_DIR") or os.path.expanduser("~/orca/board")
        try:
            self.budget_s = float(env("LRM_REPORT_BUDGET_S", self.budget_s))
        except ValueError:
            pass
        try:
            self.ws_thread_max = int(env("LRM_WS_THREAD_MAX", self.ws_thread_max))
        except ValueError:
            pass
        try:
            self.hold_max_s = float(env("LRM_HOLD_MAX_S", self.hold_max_s))
        except ValueError:
            pass


class NotifyError(Exception):
    """permanent: 재시도해도 같은 결과. counts: 실패 횟수에 셈(회차 예산 초과는 전송을 시도하지 않았으므로 세지 않음)"""

    def __init__(self, msg: str, permanent: bool = False, counts: bool = True) -> None:
        super().__init__(msg)
        self.permanent, self.counts = permanent, counts


def run_notify(conf: Conf, args: list, deadline: float, upload: bool = False) -> str:
    """전송 스크립트 1회 호출 → 새 메시지 ts. 실패는 NotifyError(사유 한 줄, 비밀 값 가림).
    upload=True 는 파일 업로드 호출: 알림 스크립트가 'ok file_id=<id>'(ts 없음)를 출력하므로 file_id 를 돌려줌"""
    left = deadline - time.monotonic()
    if left <= 0:
        raise NotifyError("회차 시간 예산 초과", counts=False)
    try:
        p = subprocess.run([conf.notify, *args], capture_output=True, text=True, timeout=min(UPLOAD_TIMEOUT_S if upload else NOTIFY_TIMEOUT_S, left))
    except subprocess.TimeoutExpired:
        raise NotifyError("전송 시간 초과")
    except OSError as err:
        raise NotifyError(f"전송 스크립트 실행 실패({err.strerror})")
    last = (p.stdout.strip().splitlines() or [""])[-1]
    if upload and p.returncode == 0 and (m := re.fullmatch(r"ok(?:\s+file_id=(\S*))?", last)):
        return m.group(1) or ""
    if p.returncode == 0 and last == "suppressed":  # 하루 1회 판정이 막음(--from 호출만). 보낸 것은 아니지만 실패도 아님
        return ""
    if p.returncode == 0 and last.startswith("ok ") and last[3:].strip():
        return last[3:].strip()
    why = " ".join((p.stderr.strip() or p.stdout.strip() or f"종료 코드 {p.returncode}").split())[:ERR_MAX]
    perm = p.returncode == PERMANENT_RC and p.stderr.strip() not in USER_FIX_LINES
    raise NotifyError("비밀 값 포함 오류 숨김" if hub_status.SECRET_RE.search(why) else why, permanent=perm)


class StateCorrupt(Exception):
    """상태 파일이 있는데 못 읽음(손상·권한). 빈 상태로 보면 이미 올린 보고를 다시 올리므로 전송을 멈춤"""


STATE_KEYS = ("topics", "reports", "warned", "bad", "card_warn")
REQUIRED_KEYS = STATE_KEYS[:3]  # bad·card_warn 은 뒤에 생긴 키라 옛 상태 파일에는 없을 수 있음


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


def request_of(conf: Conf, task: str) -> str:
    """주제 시작 메시지에 쓸 장부 요청 한 줄. 장부에 없으면 빈 문자열"""
    try:
        return next((ledger.clean(r["request"]) for r in ledger.read_rows(conf.ledger) if r["id"] == task), "")
    except ledger.LedgerError:
        return ""


def topic_key(rep: dict) -> str:
    if rep.get("thread"):
        return f"thread:{rep['thread']}"  # 지정 스레드별로 순서 유지. 주제 스레드는 만들지 않음
    if rep.get("card"):
        return f"card:{rep['card']}"  # 작업판 카드 하나 = 스레드 하나
    return rep["task"] if rep["task"] != hub_report.NO_TASK else f"hub:{rep['hub']}"


def card_of(rep: dict) -> str:
    """주제 부모 양식을 읽을 카드. 작업 공간 보고는 그 카드, 아니면 작업(task) 카드"""
    return rep.get("card") or rep["task"]


def is_card_topic(rep: dict) -> bool:
    return not rep.get("thread") and card_of(rep) != hub_report.NO_TASK and bool(CARD_RE.fullmatch(card_of(rep)))


def bd_json(conf: Conf, *args: str) -> "list | None":
    """작업판 bd 조회 → JSON 목록. 실패·시간 초과·JSON 아님은 None(stderr 경고 줄은 무시)"""
    try:
        p = subprocess.run([conf.bd, "-C", conf.board, *args, "--json"], capture_output=True, text=True, timeout=BD_TIMEOUT_S)
        d = json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return d if isinstance(d, list) else None


def first_sentence(line: str) -> str:
    line = CARD_SKIP_RE.sub("", re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", line))
    m = re.match(r"(.+?[.!?。](?=\s|$)|.+?다\.?(?=\s|$))", line.strip())
    return " ".join((m.group(1) if m else line).split()).strip(" ,;:·()")


def what_fallback(desc: str) -> str:
    """'무엇:' 줄이 없을 때 설명 첫 문장. 머리줄(#)·메타 줄·빈 줄·내부 기호만 남는 줄은 건너뛰고 '## 지시' 앞에서 멈춤"""
    for ln in desc.splitlines():
        if CARD_ASK_HEAD_RE.match(ln.strip()):
            break  # 지시 칸은 사용자 원문이라 무엇(작업 설명)이 아님
        if not ln.strip() or CARD_FIELD_RE.match(ln) or ln.lstrip().startswith("#") or ln.split(":", 1)[0].strip(" -•*") in CARD_META_KEYS:
            continue
        got = first_sentence(ln)
        if got:
            return got
    return ""


def why_fallback(desc: str) -> str:
    """'왜:' 줄이 없을 때 사용자 원문('원문(…): "…"')의 첫 줄, 없으면 '## 지시' 칸 첫 내용 줄"""
    lines = desc.splitlines()
    for ln in lines:
        m = CARD_QUOTE_RE.search(ln)
        if m:
            return m.group(1).strip()
    for i, ln in enumerate(lines):
        if CARD_ASK_HEAD_RE.match(ln.strip()):
            for nxt in lines[i + 1:]:
                if nxt.lstrip().startswith("#"):
                    break
                got = first_sentence(nxt)
                if got:
                    return got
    return ""


def title_what(title: str) -> str:
    """'무엇' 마지막 폴백: 카드 제목에서 머리표([impl])·카드 번호·'카드' 머리말을 뺀 것"""
    t = TITLE_TAG_RE.sub("", title)
    t = TITLE_CARD_RE.sub("", CARD_SKIP_RE.sub("", t))
    t = TITLE_CARD_RE.sub("", " ".join(t.split()).lstrip(" :：-·"))
    return t.strip(" :：-·")


def card_info(conf: Conf, card: str) -> "dict | None":
    """카드 제목·설명 줄(쉬운 이름·무엇·왜)·하위 카드 진행률. 카드를 못 읽으면 None. 회차 안에서는 한 번만 조회"""
    if card not in conf.cards:
        conf.cards[card] = None
        shown = bd_json(conf, "show", card)
        kids = bd_json(conf, "list", "--parent", card, "--all", "-n", "0", "--brief")
        if shown and isinstance(shown[0], dict):
            desc = shown[0].get("description") or ""
            fields = {m.group(1): m.group(2) for m in reversed(list(CARD_FIELD_RE.finditer(desc)))}  # 같은 칸이 여럿이면 앞 줄
            for name, fallback in (("무엇", what_fallback), ("왜", why_fallback)):
                fields[name] = fields.get(name) or fallback(desc)
            fields["무엇"] = fields["무엇"] or title_what(str(shown[0].get("title") or ""))
            why_missing = not fields["왜"]
            fields["왜"] = fields["왜"] or WHY_MISSING  # 이유가 없어도 보고는 보냄(경고는 별도)
            conf.cards[card] = {"id": card, "title": str(shown[0].get("title") or ""), "fields": fields, "why_missing": why_missing,
                                "closed": sum(1 for k in kids or [] if isinstance(k, dict) and k.get("status") == "closed"),
                                "total": len(kids) if kids is not None else None}  # None = 하위 조회 실패(0개와 구분)
    return conf.cards[card]


def card_gaps(info: dict) -> list:
    """양식 필수 칸 중 빈 것. 비어 있으면 부모를 보내지 않음(순수 판정)"""
    return [f for f in CARD_REQUIRED if not info["fields"].get(f)]


def card_gap_error(info: dict) -> str:
    return f"카드 {info['id']} 설명 칸 비어 있음({'·'.join(card_gaps(info))}) — " + CARD_GAP_FIX.format(card=info["id"])


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def split_slash(label: str, value: str) -> list:
    """카드 설명의 ' / ' 로 이은 값을 항목 줄로 나눔(알림 스크립트 줄 검사가 거부하는 모양이라 조립 쪽에서 풀어 줌)"""
    parts = [clip(p.strip(), PARENT_FIELD_MAX) for p in board_cards.DOC_SLASH_JOIN.split(value) if p.strip()] or [""]
    return [f"- {label}: {parts[0]}"] + [f"- {p}" for p in parts[1:]]


def card_parent_text(info: dict, hub: str) -> str:
    """부모 양식 ①~⑥: 제목(쉬운 이름) · 무엇 · 왜 · 진행률 · 답하는 법 · 참고(카드 번호·허브 이름은 여기에만)"""
    f = info["fields"]
    title = clip(f.get("쉬운 이름") or title_what(info["title"]) or info["title"], PARENT_TITLE_MAX)
    rows = split_slash("무엇", f["무엇"]) + split_slash("왜", f["왜"])
    if info["total"] is not None:  # 하위 조회 실패면 거짓 '없음' 대신 줄을 뺌
        rows.append(f"- 지금: 하위 작업 {info['closed']}/{info['total']} 완료" if info["total"] else "- 지금: 하위 작업 없음")
    return "\n".join([title, "", *rows, f"- 답하는 법: {PARENT_HOW_TO}", "", f"참고: {info['id']} · 허브 {hub}"])


def parent_stamp(text: str) -> str:
    """상태에 남기는 양식 버전 + 내용 해시. 달라졌을 때만 부모를 고쳐 씀"""
    return f"{PARENT_FORMAT}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def card_problem(conf: Conf, rep: dict, st: dict) -> str:
    """주제 스레드를 새로 열어야 하는 카드 보고의 양식 칸 검사(폴백 반영). 문제 없으면 빈 문자열(카드를 못 읽는 경우 포함: 옛 양식으로 보냄).
    문제는 입력 오류가 아니라 보류 사유: 카드가 고쳐지면 다음 회차에 나감. 보류가 hold_max_s 를 넘으면 문제 없음으로 보고 있는 칸만으로 보냄"""
    if rep["kind"] == "decide" or not is_card_topic(rep) or topic_key(rep) in st["topics"]:
        return ""
    if time.time() - rep.get("at", time.time()) > conf.hold_max_s:
        return ""
    info = card_info(conf, card_of(rep))
    return card_gap_error(info) if info and card_gaps(info) else ""


def why_notice(conf: Conf, rep: dict, st: dict) -> str:
    """왜 칸이 끝내 빈 카드의 새 스레드 보고: 보내되 허브에 알릴 사유(보류 아님). 없으면 빈 문자열"""
    if rep["kind"] == "decide" or not is_card_topic(rep) or topic_key(rep) in st["topics"]:
        return ""
    info = card_info(conf, card_of(rep))
    return f"카드 {info['id']} 설명 칸 비어 있음(왜) — " + WHY_GAP_FIX.format(card=info["id"]) if info and info.get("why_missing") else ""


def topic_text(conf: Conf, rep: dict) -> str:
    key = topic_key(rep)
    info = card_info(conf, card_of(rep)) if is_card_topic(rep) else None
    if info and not card_gaps(info):
        return card_parent_text(info, rep["hub"])
    title = f"{rep['task']} 보고 스레드" if rep["task"] != hub_report.NO_TASK else f"{rep['hub']} 보고 스레드"
    lines = [title, "", f"- 허브: {rep['hub']}"]
    ask = request_of(conf, rep["task"]) if not key.startswith("hub:") else ""
    if ask:
        lines.append(f"- 요청: {ask[:LINE_MAX]}")
    elif not key.startswith("hub:"):
        log(conf, f"경고: 주제 {key} 시작 메시지에 '무엇' 줄 없음(장부·카드에 요청이 비어 있음)")
    return "\n".join(lines)


def refresh_parent(conf: Conf, rep: dict, st: dict, deadline: float) -> None:
    """열린 카드 주제 스레드의 부모를 현재 양식·진행률로 고쳐 씀. 칸이 비었거나 카드를 못 읽으면 건너뜀. 전송 실패는 보고를 막지 않고 다음 보고에서 다시 시도"""
    key = topic_key(rep)
    topic = st["topics"].get(key)
    if rep["kind"] == "decide" or not topic or not is_card_topic(rep) or topic.get("update_dead") or topic.get("shared"):  # 공유 스레드 부모는 기능 카드 몫
        return
    info = card_info(conf, card_of(rep))
    if not info:
        return
    if card_gaps(info):
        log(conf, f"경고: 열린 스레드 {key} 부모를 고치지 못함 — {card_gap_error(info)}")
        return
    text = card_parent_text(info, rep["hub"])
    stamp = parent_stamp(text)
    if topic.get("fmt") == stamp:
        return
    try:
        run_notify(conf, ["--tag", rep["hub"], "--kind", "progress", "--update", topic["ts"], text], deadline)
    except NotifyError as err:
        if any(code in str(err) for code in UPDATE_DEAD_ERRORS):  # 부모가 지워진 스레드는 보고마다 재시도하지 않음
            topic["update_dead"] = True
            save_state(conf.state, st)
        log(conf, f"경고: 스레드 {key} 부모 고쳐 쓰기 실패 — {err}")
        return
    topic["fmt"] = stamp
    save_state(conf.state, st)
    log(conf, f"부모 고쳐 씀 {key} {topic['ts']} {stamp}")


def body_text(rep: dict, shared: bool = False) -> str:
    """shared: 기능 스레드를 같이 쓰는 작업 공간 카드의 보고. 어느 카드 글인지 머리에 카드 id 를 붙임"""
    label = hub_report.KINDS[rep["kind"]][1]
    lines = [ln for ln in rep["text"].splitlines() if ln.strip()]
    if rep["kind"] == "decide":  # 첫 줄이 제목, 나머지가 배경. 선택지·답 줄은 slack_notify.sh 가 붙임
        out = hub_report.bullet_body("\n".join(lines)).splitlines()
    else:
        out = [f"{label} · {rep['hub']}" + (f" · {rep['card']}" if shared and rep.get("card") else ""), ""] + hub_report.bullet_items(lines)
    return "\n".join(out)


def progress_line(rep: dict) -> str:
    """진행 스레드 한 줄. 원본 링크는 알림 스크립트(--quote)가 chat.getPermalink 정식 주소로 붙임(손수 만든 주소 금지, 실패 시 '원본: 링크 없음')"""
    emoji, label, _ = hub_report.KINDS[rep["kind"]]
    first = rep["text"].splitlines()[0]
    first = first if len(first) <= LINE_MAX else first[: LINE_MAX - 1] + "…"
    out = [f"{emoji} {rep['hub']} {label}", f"- {first}"]
    return "\n".join(out)


def attached_args(rep: dict) -> list:
    """알림 스크립트에 '이 파일들은 같은 보고에서 따로 첨부함'을 알림(본문의 경로 검사용). 본문·진행 줄 호출에 붙임"""
    return [x for f in rep["files"] for x in ("--attached", f)]


def broadcast_args(rep: dict) -> list:
    """스레드 답글을 DM 목록에도 보이게 할 보고인가. 규칙은 hub_report.BROADCAST_KINDS 한 곳, 그 밖은 보고가 직접 요청(--broadcast)한 것만"""
    return ["--broadcast"] if rep.get("broadcast") or rep["kind"] in hub_report.BROADCAST_KINDS else []


def decide_args(rep: dict) -> list:
    a = ["--tag", rep["hub"], "--kind", "decide", "--options", rep["options"], "--recommend", rep["recommend"], "--decision-id", rep["decision_id"]]
    a += ["--no-doc"] if rep.get("no_doc") and not rep["files"] else []
    return a + (["--reversible", rep["reversible"]] if rep.get("reversible") else [])


def content_hash(path: str) -> str:
    """원본 내용 해시 앞 12자. 못 읽으면 빈 문자열(미리보기 생략)"""
    try:
        with open(os.path.expanduser(path), "rb") as f:
            return hashlib.sha1(f.read(hub_report.MAX_FILE_BYTES + 1)).hexdigest()[:12]
    except OSError:
        return ""


def source_of(rep: dict, path: str) -> str:
    """올릴 파일 경로: 접수 때 만든 복사본이 아직 있으면 복사본, 아니면 원본"""
    cp = (rep.get("staged") or {}).get(path)
    return cp if cp and os.path.isfile(cp) else path


def read_problem(path: str) -> str:
    """이 프로세스가 파일을 열 수 없으면(권한·TCC) UNREADABLE. 그 밖의 오류는 알림 스크립트·file_problem 판정에 맡김"""
    try:
        with open(os.path.expanduser(path), "rb") as f:
            f.read(1)
    except PermissionError:
        return UNREADABLE
    except OSError:
        pass
    return ""


def drop_staged(conf: Conf, rep: dict) -> None:
    """보고의 첨부 복사본 폴더 삭제. 접수 때 만든 폴더(<복사 폴더>/<id>)만 지움"""
    if re.fullmatch(r"[0-9a-f]{12}", rep["id"]):
        shutil.rmtree(os.path.join(hub_report.stage_dir(conf.outbox), rep["id"]), ignore_errors=True)


def gc_staged(conf: Conf) -> None:
    """STAGE_KEEP_S 보다 오래된 복사본 폴더 삭제(중단·예외로 못 지운 것)"""
    root = hub_report.stage_dir(conf.outbox)
    with contextlib.suppress(OSError):
        for name in os.listdir(root):
            d = os.path.join(root, name)
            if os.path.isdir(d) and time.time() - os.path.getmtime(d) > STAGE_KEEP_S:
                shutil.rmtree(d, ignore_errors=True)


def make_preview(conf: Conf, path: str, digest: str, deadline: float) -> str:
    """HTML → 390폭 미리보기 PNG 경로. 경로는 상태 폴더 아래 내용 해시로 고정(응답 유실 복구가 보낸 기록의 경로로 찾음).
    스크립트·Chrome 이 없거나 실패하면 빈 문자열(첨부는 계속)"""
    left = deadline - time.monotonic()
    if not (os.access(conf.preview, os.X_OK) and left > 0):
        return ""
    out = os.path.join(os.path.dirname(os.path.abspath(conf.state)), "report-previews", f"{digest}-{os.path.splitext(os.path.basename(path))[0]}-preview.png")
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        p = subprocess.run([conf.preview, os.path.abspath(os.path.expanduser(path)), out], capture_output=True, text=True, timeout=min(PREVIEW_TIMEOUT_S, left))
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return out if p.returncode == 0 and os.path.isfile(out) else ""


def make_pdf(conf: Conf, path: str, digest: str, deadline: float) -> str:
    """HTML → PDF 경로. Slack 이 HTML 을 웹 페이지로 열지 않아 바로 열리는 PDF 를 함께 올림.
    파일 이름이 Slack 에 그대로 보이므로 해시는 폴더 이름에만 둠. 스크립트·Chrome 이 없거나 실패하면 빈 문자열(첨부는 계속)"""
    left = deadline - time.monotonic()
    if not (os.access(conf.pdf, os.X_OK) and left > 0):
        return ""
    out = os.path.join(os.path.dirname(os.path.abspath(conf.state)), "report-previews", digest, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        p = subprocess.run([conf.pdf, os.path.abspath(os.path.expanduser(path)), out], capture_output=True, text=True, timeout=min(PREVIEW_TIMEOUT_S, left))
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return out if p.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0 else ""


MAKERS = {PDF_SUFFIX: make_pdf, PREVIEW_SUFFIX: make_preview}


def attach_failed(conf: Conf, rep: dict, target: str, name: str, why: str, deadline: float) -> None:
    """스레드에 '첨부 실패: 이유' 한 줄. 이 줄 전송이 실패해도 보고는 계속함(원인은 로그에 남음)"""
    line = f"첨부 실패: {name} — {why}"
    log(conf, f"{rep['id']} {line}")
    with contextlib.suppress(NotifyError):
        run_notify(conf, ["--tag", rep["hub"], "--thread", target, line], deadline)


def uploaded_id(conf: Conf, src: str, target: str, since: int) -> str:
    """응답이 유실돼 실패로 보였지만 알림 스크립트가 보낸 기록(slack-sent.jsonl, kind=file)에 남은 업로드의 file_id. 없으면 빈 문자열.
    같은 스레드에 같은 파일을 다른 보고가 올린 기록과 섞이지 않게 이 보고의 첫 업로드 시도 이후(since)만 봄"""
    try:
        with open(conf.sent, encoding="utf-8") as f:
            for ln in reversed(f.read().splitlines()):
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if (isinstance(d, dict) and d.get("kind") == "file" and d.get("path") == src and d.get("thread_ts") == target
                        and isinstance(d.get("at"), (int, float)) and d["at"] >= since and d.get("file_id")):
                    return d["file_id"]
    except OSError:
        pass
    return ""


def send_files(conf: Conf, rep: dict, rs: dict, st: dict, target: str, deadline: float) -> None:
    """첨부 파일을 target 스레드에 올림. 업로드 완료(file_id)를 att_done 에 바로 저장해 재시도 때 건너뜀(응답 유실은 sent 기록으로 복구).
    입력 문제(파일 없음·크기 초과·민감 파일·알림 스크립트의 영구 오류)는 한 줄 알리고 넘어가고, 일시 실패는 NotifyError 로 올려 보고 재시도 규칙을 탐"""
    if not isinstance(rs.get("att_done"), dict):
        rs["att_done"] = {}
    done = rs["att_done"]
    since = rs.setdefault("att_start", int(time.time()))
    for path in rep["files"]:
        name = os.path.basename(path) or path
        jobs = [(path, name)]
        origin = source_of(rep, path)
        digest = content_hash(origin) if path.lower().endswith(HTML_EXTS) else ""
        if digest:
            jobs += [(path + suffix + digest, "") for suffix in DERIVED]
        for key, label in jobs:
            if key in done:
                continue
            src = origin
            made = next((s for s in DERIVED if key == path + s + digest), "") if digest else ""
            if made:  # 딸린 파일은 업로드 직전에 만듦. 못 만들면 생략으로 기록
                src = "" if deadline - time.monotonic() <= 0 else MAKERS[made](conf, origin, digest, deadline)
                label = os.path.basename(src)
                if not src and deadline - time.monotonic() <= 0:
                    raise NotifyError("회차 시간 예산 초과", counts=False)  # 시간이 없어 못 만든 것은 생략으로 굳히지 않고 다음 회차로
                if not src:
                    log(conf, f"{rep['id']} {DERIVED[made][1]} 생략: {name}")
                    done[key] = ""
                    save_state(conf.state, st)
                    continue
            if fid := uploaded_id(conf, src, target, since):
                done[key] = fid
                save_state(conf.state, st)
                continue
            save_state(conf.state, st)  # 첫 시도 시각(att_start)을 업로드 전에 저장
            why = hub_report.file_problem(src) or read_problem(src)
            try:
                if why:
                    raise NotifyError(why, permanent=True)
                done[key] = run_notify(conf, ["--tag", rep["hub"], "--thread", target] + attached_args(rep) + [f"첨부: {label}", src], deadline, upload=True) or "uploaded"
            except NotifyError as err:
                if not err.permanent:
                    raise NotifyError(f"첨부 실패: {label} — {err}", counts=err.counts)
                attach_failed(conf, rep, target, label, str(err), deadline)
                done[key] = ""
            if made:
                with contextlib.suppress(OSError):
                    os.unlink(src)  # 올렸거나 포기한 딸린 파일 정리
                if made == PDF_SUFFIX:
                    with contextlib.suppress(OSError):
                        os.rmdir(os.path.dirname(src))  # 해시 폴더는 비었을 때만 지워짐
            save_state(conf.state, st)


def card_attach(conf: Conf, topic: dict) -> None:
    """카드에 스레드 ts(·작업 공간)를 붙임. 실패해도 보고는 계속하고 topic["attached"] 를 비워 둬 다음 회차에 다시 시도.
    같은 스레드가 다른 카드에 이미 붙어 있으면 feedback_route 가 거부하고 확인 카드를 만듦 → 재시도하지 않음('conflict')"""
    import board_cards
    import feedback_route
    try:
        feedback_route.attach(topic["card"], topic["ts"], topic.get("ws") or "")
        topic["attached"] = True
    except board_cards.Fail as err:
        topic["attached"] = "conflict" if err.code == feedback_route.EXIT_CONFLICT else False
        log(conf, f"카드 {topic['card']} 스레드 연결 {'거부' if topic['attached'] else '실패(다음 회차 재시도)'}: {err}")


def open_ws_threads(parent: str) -> int:
    """기능 epic 아래에서 지금 열려 있는 작업 공간 스레드 수(자기 스레드가 붙고 반납 전인 작업 공간 카드). 조회 실패는 0(상한을 못 재도 보고는 올림).
    bd 가 하위 카드에 물려준 부모의 slack-thread 는 자기 스레드가 아니므로 셈에서 뺌"""
    import board_cards
    import feedback_route
    try:
        rows = board_cards.bd_json("list", "--all", "--parent", parent, "--limit", "0") or []
        up = feedback_route.card_row(parent) or {}
    except board_cards.Fail:
        return 0
    inherited = {lb for lb in up.get("labels") or [] if lb.startswith(feedback_route.THREAD)}
    return sum(1 for r in rows if isinstance(r, dict) and feedback_route.ws_of(r) is not None and feedback_route.is_open(r)
               and any(lb.startswith(feedback_route.THREAD) and lb not in inherited for lb in r.get("labels") or []))


def parent_of(card: str) -> str:
    import board_cards
    import feedback_route
    try:
        row = feedback_route.card_row(card)
    except board_cards.Fail:
        return ""
    return (row or {}).get("parent") or ""


FEATURE_HUB = "feat-"  # feature_open.sh 가 만드는 기능 코디네이터 허브 이름 머리


def feature_thread(conf: Conf, rep: dict) -> "tuple[str, str]":
    """기능 코디네이터 허브의 보고가 모일 기능 epic 과 그 스레드 ts. 기능 허브가 아니거나 스레드가 아직 없으면 ("", "")"""
    hub = rep.get("hub") or ""
    if not hub.startswith(FEATURE_HUB):
        return "", ""
    rows = bd_json(conf, "list", "-t", "epic", "-l", f"feature:{hub[len(FEATURE_HUB):]}", "--all", "--limit", "0") or []
    for row in rows:
        ts = next((lb.split(":", 1)[1] for lb in row.get("labels") or [] if lb.startswith("slack-thread:")), "")
        if ts:
            return row.get("id") or "", ts
    return "", ""


def in_thread(rep: dict, topic: dict) -> bool:
    """보고 본문을 주제 스레드 안에 올리는가(False 면 맨 위 글). 판정 경우는 tests/report_sender/test_feature_route.py 의 표.
    같이 쓰는 스레드(shared)의 결정은 기능 코디네이터 것만 안에 올림: 그 스레드의 글 답은 기능 코디네이터가 받으므로,
    다른 허브나 작업 공간 세션(하위 카드 + ws)의 결정을 넣으면 글 답이 올린 세션이 아닌 곳으로 감"""
    if rep["kind"] != "decide":
        return True
    if not topic.get("ts"):
        return False
    if not topic.get("shared"):
        return True
    return str(rep.get("hub") or "").startswith(FEATURE_HUB) and not (rep.get("card") and rep.get("ws"))


def open_topic(conf: Conf, rep: dict, st: dict, deadline: float) -> None:
    """주제 스레드 시작 메시지를 올리고 상태에 저장. 카드 주제면 카드에 스레드를 붙임.
    작업 공간 카드가 기능당 스레드 상한(ws_thread_max)에 걸리면 자기 스레드를 열지 않고 부모 기능 epic 의 스레드를 같이 씀(스레드는 첫 보고 때만 열리므로 이 결정도 한 번만)"""
    key = topic_key(rep)
    card = rep.get("card")
    # 기능 허브 보고는 카드·티켓·허브 주제를 새로 열지 않고 기능 스레드 하나로 모음(11-19 스레드 흩어짐 지적)
    epic, ts = feature_thread(conf, rep)
    if ts and key != f"card:{epic}":
        st["topics"][key] = {"ts": ts, "channel": st["topics"].get(f"card:{epic}", {}).get("channel", ""), "shared": epic}
        save_state(conf.state, st)
        log(conf, f"기능 허브 {rep['hub']} 보고 {key} 는 기능 스레드({epic})로")
        return
    if card and rep.get("ws"):
        parent = parent_of(card)
        if parent and open_ws_threads(parent) >= conf.ws_thread_max:
            pkey = f"card:{parent}"
            if pkey not in st["topics"]:
                open_topic(conf, dict(rep, card=parent, ws=None), st, deadline)
            st["topics"][key] = {"ts": st["topics"][pkey]["ts"], "channel": st["topics"][pkey].get("channel", ""), "shared": parent}
            save_state(conf.state, st)
            log(conf, f"작업 공간 스레드 {conf.ws_thread_max}개 상한: 카드 {card} 보고는 기능 스레드({parent})로")
            return
    text = topic_text(conf, rep)
    ts = run_notify(conf, ["--tag", rep["hub"], "--kind", "progress", text], deadline)
    st["topics"][key] = {"ts": ts, "channel": channel_of(conf, ts)}
    if is_card_topic(rep) and text.endswith(f"참고: {card_of(rep)} · 허브 {rep['hub']}"):
        st["topics"][key]["fmt"] = parent_stamp(text)  # 새 양식으로 열림. 옛 양식 대체 문구는 기록하지 않아 나중에 고쳐 쓰기 대상으로 남음
    if card:
        st["topics"][key].update(card=card, ws=rep.get("ws") or "", attached=False)
    save_state(conf.state, st)
    if card:
        card_attach(conf, st["topics"][key])
        save_state(conf.state, st)


def retry_attach(conf: Conf, st: dict) -> None:
    """붙이기에 실패했던 카드 스레드를 회차마다 다시 시도(보고를 더 안 올려도 카드가 스레드를 갖게)"""
    changed = False
    for t in st["topics"].values():
        if t.get("card") and t.get("attached") is False:
            card_attach(conf, t)
            changed = True
    if changed:
        save_state(conf.state, st)


def deliver(conf: Conf, rep: dict, st: dict, deadline: float) -> dict:
    """보고 1건 전송. 단계(topic·body·line)마다 상태를 저장하고, 끝나면 결과 요약을 돌려줌. 실패는 NotifyError"""
    rs = st["reports"].setdefault(rep["id"], {})
    rs.setdefault("task", rep["task"])  # 진행 목록이 작업별 첨부 메시지를 찾는 데 씀
    decide = rep["kind"] == "decide"
    key = topic_key(rep)
    if rep.get("thread"):  # 지정 스레드 답글. 진행 스레드 한 줄은 올리지 않음
        if "body_ts" not in rs:
            _, _, kind = hub_report.KINDS[rep["kind"]]
            warn_unattached(conf, rep)
            pinned = ["--broadcast"] if rep.get("broadcast") else []  # 지정 스레드는 질문에 답하는 자리라 요청한 것만
            rs["body_ts"] = run_notify(conf, ["--tag", rep["hub"], "--kind", kind, "--thread", rep["thread"]] + pinned + attached_args(rep) + [body_text(rep)], deadline)
            save_state(conf.state, st)
        send_files(conf, rep, rs, st, rep["thread"], deadline)
        rs["line_ts"] = ""
        rs["sent"] = int(time.time())
        save_state(conf.state, st)
        return {"topic_ts": rep["thread"], "body_ts": rs["body_ts"], "line_ts": "", "pinned": True}
    # 카드를 준 결정은 그 작업 스레드 안에. 기능 코디네이터의 결정은 카드 지정이 없어도 기능 스레드 안에(승인과 작업 이력을 한 스레드로). 그 밖의 결정은 맨 위 글
    threaded = not decide or bool(rep.get("card")) or bool(feature_thread(conf, rep)[1])
    if threaded and key not in st["topics"]:
        open_topic(conf, rep, st, deadline)
    elif threaded:
        refresh_parent(conf, rep, st, deadline)
    topic = st["topics"].get(key, {})
    shared = bool(topic.get("shared"))
    threaded = threaded and in_thread(rep, topic)
    if "body_ts" not in rs:
        warn_unattached(conf, rep)
        if decide:
            where = ["--thread", topic["ts"]] + broadcast_args(rep) if threaded else []
            rs["body_ts"] = run_notify(conf, decide_args(rep) + where + attached_args(rep) + [body_text(rep)], deadline)
        else:
            _, _, kind = hub_report.KINDS[rep["kind"]]
            rs["body_ts"] = run_notify(conf, ["--tag", rep["hub"], "--kind", kind, "--thread", topic["ts"]] + broadcast_args(rep) + attached_args(rep) + [body_text(rep, shared)], deadline)
        save_state(conf.state, st)
    send_files(conf, rep, rs, st, topic["ts"] if threaded else rs["body_ts"], deadline)
    if "line_ts" not in rs:
        parent = read_json_file(conf.sync_state).get("ts", "")
        if parent:
            target = rs["body_ts"] if decide else topic["ts"]
            rs["line_ts"] = run_notify(conf, ["--thread", parent, "--quote", target] + attached_args(rep) + [progress_line(rep)], deadline)
        else:
            rs["line_ts"] = ""  # 진행 스레드 부모가 아직 없음. 본문은 이미 올라가 있어 줄만 생략
        save_state(conf.state, st)
    rs["sent"] = int(time.time())
    save_state(conf.state, st)
    return {"topic_ts": topic.get("ts", ""), "body_ts": rs["body_ts"], "line_ts": rs["line_ts"]}


def warn_unattached(conf: Conf, rep: dict) -> None:
    """본문에 로컬 경로만 있고 첨부가 없는 보고를 로그로 경고(접수 시점 경고와 같은 판정). 본문 첫 전송 때 1번"""
    if found := hub_report.unattached_paths(rep["text"], rep["files"]):
        log(conf, f"경고: 보고 {rep['id']} 본문에 로컬 경로({', '.join(found[:3])})만 있고 첨부 없음 — hub_report.py --file 로 첨부")


def safe_text(text: str) -> str:
    """경고 문구의 로컬 경로를 확장자 없는 파일 이름으로 바꿈. 경로가 든 경고는 알림 스크립트의 경로 검사에 걸려 경고가 안 나감(검사 예외 없이 문구로 해결)"""
    for p in sorted(set(hub_report.local_paths(text)), key=len, reverse=True):
        text = text.replace(p, os.path.splitext(os.path.basename(p))[0] or "파일")
    return text


def warn_stop(conf: Conf, rep: dict, st: dict, why: str, permanent: "bool | None", deadline: float) -> None:
    """재시도 중단 경고 1건(원인 + 조치). 원래 주제 스레드가 있으면 그 답글, 없으면(결정 카드 등) DM 새 글"""
    label = hub_report.KINDS[rep["kind"]][1]
    thread = rep.get("thread") or st["topics"].get(topic_key(rep), {}).get("ts", "")
    if permanent is None:  # 분류를 저장하기 전 기록: 입력 오류로 단정하지 않음
        cause = "원인 미확인(이전 기록)" + (f" — {why}" if why else "")
    else:
        cause = f"입력 오류(재시도해도 같은 결과) — {why}" if permanent else f"{conf.fail_limit}회 연속 전송 실패 — {why}"
    cause = board_cards.DOC_SLASH_JOIN.sub("/", safe_text(cause))  # 원인에 섞인 ' / ' 가 경고 자신을 줄 검사에 걸리게 하지 않음
    msg = (f"허브 보고 전송 중단\n\n- 무엇: {rep['hub']} {label} 보고 {rep['id']} 재시도 중단\n"
           f"- 원인: {cause}\n- 조치: 보고를 고쳐 hub_report.py add 로 다시 기록")
    run_notify(conf, ["--tag", rep["hub"], "--kind", "warn", "--from", "report_sender", "--subject", rep["id"], "--state", "stopped"]
               + (["--thread", thread] if thread else []) + [msg], deadline)


def incident(kind: str, cause: str, **fields) -> None:
    """사건 자동 기록(작업판 incidents.jsonl, 형식은 board/lib/incident.py). 작업판이 없거나 못 써도 전송 흐름은 그대로"""
    path = os.environ.get("BOARD_INCIDENTS") or ""
    if not path:
        board = os.environ.get("BOARD_DIR") or os.path.expanduser("~/orca/board")
        path = os.path.join(board, "incidents.jsonl") if os.path.isdir(board) else ""
    if not path:
        return
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "kind": kind, "cause": safe_text(cause)[:500],
           "source": "report_sender", **fields}
    try:
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def stop_report(conf: Conf, rep: dict, st: dict, why: str, permanent: bool, deadline: float) -> None:
    """보고를 재시도 대상에서 빼고(bad) 경고 1건. 경고 전송이 실패하면 다음 회차에 경고만 다시 시도"""
    st["bad"][rep["id"]] = int(time.time())
    rs = st["reports"].setdefault(rep["id"], {})
    rs["error"], rs["stop_permanent"] = why, permanent  # 경고 재시도에도 같은 원인 분류를 쓰도록 저장
    save_state(conf.state, st)
    incident("report_stopped", ("입력 오류: " if permanent else f"{conf.fail_limit}회 연속 실패: ") + str(why),
             hub=rep.get("hub", ""), report=rep.get("id", ""))
    warn_pending(conf, rep, st, deadline)


def warn_pending(conf: Conf, rep: dict, st: dict, deadline: float) -> None:
    if rep["id"] in st["warned"]:
        return
    rs = st["reports"].get(rep["id"], {})
    try:
        warn_stop(conf, rep, st, rs.get("error", ""), rs.get("stop_permanent"), deadline)  # 키 없는 옛 상태는 분류 불명(None)
        st["warned"][rep["id"]] = int(time.time())
        save_state(conf.state, st)
    except NotifyError as e:
        log(conf, f"전송 중단 경고 실패(다음 회차에 재시도) 보고 {rep['id']}: {safe_text(str(e))[:200]}")


def hold_report(conf: Conf, rep: dict, st: dict, why: str, deadline: float, held: bool = True) -> None:
    """카드 칸이 비어 보고를 보류(held=False 면 보류 없이 보내면서 알림만). 카드당 하루 1회 그 허브에 원인·조치 경고(실패하면 다음 회차에 다시)"""
    card = card_of(rep)
    if time.time() - st["card_warn"].get(card, 0) < CARD_WARN_INTERVAL_S:
        return
    head, state = ("허브 보고 대기", "held") if held else ("허브 보고 카드 칸 안내", "sent")
    what = f"{rep['hub']} 보고 {rep['id']} 전송 대기(카드 {card})" if held else f"{rep['hub']} 보고 {rep['id']} 은 칸이 빈 채 전송됨(카드 {card})"
    msg = f"{head}\n\n- 무엇: {what}\n- 원인: {why.split(' — ')[0]}\n- 조치: {why.split(' — ', 1)[-1]}"
    try:
        run_notify(conf, ["--tag", rep["hub"], "--kind", "warn", "--from", "report_sender", "--subject", card, "--state", state, msg], deadline)
    except NotifyError:
        return
    st["card_warn"][card] = int(time.time())
    save_state(conf.state, st)


def input_error(rep: dict) -> str:
    """전송 전에 알 수 있는 영구 오류(접수 시점 검사와 같은 규칙). 없으면 빈 문자열"""
    if rep["kind"] == "decide" and not hub_report.DECISION_ID_RE.fullmatch(rep["decision_id"]):
        return hub_report.DECISION_ID_ERR
    if rep["kind"] == "decide" and not rep["files"] and not rep.get("no_doc"):
        return hub_report.NO_DOC_ERR  # 접수 규칙 이전에 쌓인 기록
    return hub_report.path_problem(rep["text"], rep["files"])


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
    incident("report_stopped", f"상태 파일 {err}", state=conf.state)
    msg = f"허브 보고 전송 중단\n\n- 원인: 상태 파일 {err}\n- 결과: 보고 전송 멈춤(같은 보고 재게시 방지)\n- 조치: {out['guide']}"
    try:
        run_notify(conf, ["--kind", "warn", "--from", "report_sender", "--state", "halted", msg], deadline)  # 회차 공통 deadline 을 지킴
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
                warn_pending(conf, r, st, deadline)
    if not dry:
        gc_staged(conf)
        retry_attach(conf, st)
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
        bad_input = input_error(rep)
        gap = "" if bad_input else card_problem(conf, rep, st)
        if gap:
            blocked.add(key)  # 순서 유지를 위해 같은 주제의 뒤 보고도 함께 보류
            out["held"] = out.get("held", 0) + 1
            hold_report(conf, rep, st, gap, deadline)
            line = f"보류 {rep['id']} {rep['hub']} {rep['kind']}: {gap}"
            out["lines"].append(line)
            log(conf, line)
            continue
        notice = "" if bad_input else why_notice(conf, rep, st)
        tried += 1
        if bad_input or fails.get("fails", 0) >= conf.fail_limit:  # 접수 전 쌓인 영구 오류·상한 초과 기록은 호출 없이 중단
            why = bad_input or fails.get("error", "재시도 상한 초과")
            stop_report(conf, rep, st, why, bool(bad_input), deadline)
            drop_staged(conf, rep)
            out["stopped"] = out.get("stopped", 0) + 1
            line = f"중단 {rep['id']} {rep['hub']} {rep['kind']}: {why}"
            out["lines"].append(line)
            log(conf, line)
            continue
        try:
            res = deliver(conf, rep, st, deadline)
            drop_staged(conf, rep)
        except NotifyError as err:
            if not err.counts:
                blocked.add(key)
                continue
            fails["fails"], fails["error"] = fails.get("fails", 0) + 1, str(err)
            fails["next_at"] = int(time.time() + min(conf.backoff_s * 2 ** (fails["fails"] - 1), BACKOFF_MAX_S))
            blocked.add(key)
            if err.permanent or fails["fails"] >= conf.fail_limit:
                stop_report(conf, rep, st, str(err), err.permanent, deadline)
                drop_staged(conf, rep)
                out["stopped"] = out.get("stopped", 0) + 1
                line = f"중단 {rep['id']} {rep['hub']} {rep['kind']} {fails['fails']}회째({'영구' if err.permanent else '상한'}): {err}"
            else:
                save_state(conf.state, st)
                out["failed"] += 1
                line = f"실패 {rep['id']} {rep['hub']} {rep['kind']} {fails['fails']}회째: {err}"
        else:
            out["sent"] += 1
            if notice:  # 전송이 성공한 뒤에만 안내(실패하면 안내만 먼저 나가는 일 없음. 다음 회차에 같은 조건이면 다시 판정)
                hold_report(conf, rep, st, notice, deadline, held=False)
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
        print(f"전송기: {res['status']} · 전송 {res['sent']} · 실패 {res['failed']} · 중단 {res.get('stopped', 0)} · 보류 {res.get('held', 0)} · 대기 {res['pending']}")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
