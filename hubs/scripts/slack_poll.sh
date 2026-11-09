#!/bin/bash
# 봇 DM 에서 사용자 응답(최상위 메시지·스레드 답글·반응)을 읽어 slack-inbox.jsonl 에 추가.
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/cert.pem}"  # python.org 파이썬은 시스템 인증서를 못 찾음
# 읽기와 파일 기록만 함. 수신 본문은 데이터로만 다루고 실행·eval 하지 않음.
set -eu
usage() {
  cat <<'EOF'
사용: slack_poll.sh --once [--json] [--days N]
  --once    한 번 수집 후 종료(필수, launchd 가 주기 실행)
  --json    결과 요약을 JSON 한 줄로 출력 {"ok":true,"added":N}
  --days N  목록 조회 기간(기본 7)
예:
  slack_poll.sh --once
  slack_poll.sh --once --json
출력 파일: ~/.config/lrm-hubs/slack-inbox.jsonl (항목: type=reaction|thread_reply|command|note)
           사용자 메시지에 첨부가 있으면 "files":[저장 경로...] 추가(없으면 키 없음)
           내려받기에 실패한 첨부는 "files_failed":[{"id":파일 id,"why":사유 코드}] 추가(없으면 키 없음)
첨부 저장: ~/.config/lrm-hubs/slack-files/<file_id>_<정리한 이름> (폴더 700, 파일 600, 25MB 상한, 봇 파일 제외)
상태 파일: ~/.config/lrm-hubs/slack-poll-state.json (중복 방지, 스레드별 마지막 답글 시각,
           file_share_since = 첨부 메시지 수집을 시작한 시각. 그 이전 첨부 메시지는 기록·내려받기 없이 건너뜀,
           reacted = 읽음 반응 진행 표 {메시지 ts: new|eyes|done|skip}, reactions_disabled_reason = 반응 권한 부족 사유,
           acked = {스레드 루트 ts: 확인 답장 시각}, ack_sent = 최근 확인 답장 시각 목록)
읽음 반응: 새 사용자 메시지(note·command·thread_reply)에 👀, 메인이 읽음 처리(.main-read-lines 의 줄 수가 그 줄을 지남)하면 ✅ 로 교체.
           reactions:write scope 가 없으면 조용히 건너뛰고 10분마다 재시도
수신 확인 답장: 새 사용자 메시지의 스레드에 "확인했어요, 분석 중이에요 👀" 1회(chat:write). 같은 스레드 30초 1회, 60초 3회, 회차 3회. 결정·승인 같은 명령 답글에는 달지 않음.
           slack_cmd.sh 즉답 명령·30분 넘게 묵은 항목은 제외. 권한 부족이면 조용히 건너뛰고 10분마다 재시도(상태: ack_disabled_reason).
           전송 전에 상태(acked·ack_sent)부터 저장하므로 실패해도 다시 보내지 않음. 이미 보낸 답장은 지우거나 고치지 않음
환경 변수: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs), SLACK_API_BASE API 주소(테스트용),
           LRM_ACK=off 수신 확인 답장 끔(기본 켜짐), LRM_ACK_TEXT 답장 문구(200자 제한·제어 문자 제거),
           LRM_RETRY_CAP 429 대기 상한 초(기본 20), LRM_DL_BUDGET 회차 첨부 내려받기 시간 상한 초(기본 60),
           LRM_REACT_MAX 회차당 반응 호출 상한(기본 10, 0~50. 정수가 아니면 10. 1은 2로 올림: ✅ 전환은 호출 2개 한 쌍)
종료 코드: 0 성공, 1 API·네트워크 실패, 2 설정·인자 오류
EOF
}
ONCE=0 JSON=0 DAYS=7
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --once) ONCE=1; shift ;;
    --json) JSON=1; shift ;;
    --days) DAYS="${2:?--days 값 필요}"; shift 2 ;;
    *) echo "알 수 없는 인자: $1 — --help 참고" >&2; exit 2 ;;
  esac
done
[ "$ONCE" = 1 ] || { echo "--once 필요 — 반복은 launchd 가 담당" >&2; exit 2; }
case "$DAYS" in ''|*[!0-9]*) echo "--days 는 정수" >&2; exit 2 ;; esac
DIR="${LRM_HUB_DIR:-$HOME/.config/lrm-hubs}"
ENV="$DIR/slack-bot.env"
[ -f "$ENV" ] || { echo "slack-bot.env 없음 — 사용자 설정 필요" >&2; exit 2; }
. "$ENV"
: "${SLACK_BOT_TOKEN:?}" "${SLACK_USER_ID:?}"
# 동시 실행 방지(launchd 30초 간격과 수동 실행 겹침)
LOCK="$DIR/.slack-poll.lock"
# 5분 넘은 잠금은 죽은 실행의 잔재로 보고 제거
[ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +5 2>/dev/null)" ] && rmdir "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { [ "$JSON" = 1 ] && echo '{"ok":true,"added":0,"skipped":"locked"}'; exit 0; }
trap 'rmdir "$LOCK"' EXIT
export SLACK_BOT_TOKEN SLACK_USER_ID
# 수집 실패(429 포함)여도 아래 즉답·대기 목록 단계는 건너뛰지 않음. 종료 코드만 마지막에 전달
RC=0
# 회차 전체 시간 예산(초). 수집·대기 목록·즉답(내부에서 대기 목록 재호출)의 재시도 예산 합이 5분 잠금 만료(300초) 아래여야 동시 실행이 안 생김.
# 앞 단계가 쓴 만큼 뒤 단계 예산을 줄여 줌
TOTAL="${LRM_TOTAL_BUDGET:-270}"; T0=$(date +%s)
cap() {  # cap <단계 상한>: 남은 총 예산과 상한 중 작은 값, 최소 1초
  local v=$((TOTAL - ($(date +%s) - T0)))
  [ "$v" -gt "$1" ] && v="$1"
  [ "$v" -lt 1 ] && v=1
  echo "$v"
}
LRM_POLL_BUDGET="$(cap "${LRM_POLL_BUDGET:-90}")" python3 -I - "$DIR" "$DAYS" "$JSON" <<'PY' || RC=$?
import json, os, re, subprocess, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
DIR, DAYS, AS_JSON = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
TOKEN, ME = os.environ["SLACK_BOT_TOKEN"], os.environ["SLACK_USER_ID"]
BASE = os.environ.get("SLACK_API_BASE", "https://slack.com/api")
RETRY_CAP = float(os.environ.get("LRM_RETRY_CAP", "20"))  # 429 한 번당 대기 상한
# 회차 전체 재시도 시간 예산. 5분 잠금 만료 전에 끝나야 동시 실행·이중 기록이 안 생김
DEADLINE = time.time() + float(os.environ.get("LRM_POLL_BUDGET", "90"))
RETRIES = 2
PAGE_LIMIT = 200
MAX_PAGES = 5
SENT, INBOX, STATE = (os.path.join(DIR, n) for n in ("slack-sent.jsonl", "slack-inbox.jsonl", "slack-poll-state.json"))
REACTION_ACTION = {"white_check_mark": "approve", "x": "hold", "비서-추천대로": "approve", "비서-lgtm": "approve", "비서동의": "approve", "비서-보류": "hold", "비서-a": "a", "비서-b": "b", "비서-c": "c", "비서-d": "d"}
COMMANDS = [
    ("decision", re.compile(r"^결정\s*(\d+)\s+(\S+)$")),
    ("all_recommended", re.compile(r"^전부\s*추천$")),
    ("status", re.compile(r"^현황$")),
    ("hold", re.compile(r"^보류(\s+.+)?$")),
    ("approve", re.compile(r"^승인(\s+.+)?$")),
]
# 첨부 내려받기 설정. 토큰은 slack.com 계열 https 주소에만 보냄
# 주소 판정은 파싱 전 원문 기준. urlsplit 은 \t\r\n 을 지우고 \ 를 다르게 읽어 curl 해석과 어긋나므로, 공백·제어 문자·"·\ 가 든 주소는 거부함
SLACK_URL_RE = re.compile(r"https://(?:[a-z0-9-]+\.)*slack\.com(?:/[!#-\[\]-~]*)?", re.ASCII | re.IGNORECASE)
NON_ASCII_RE = re.compile(r"[^\x00-\x7f]+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")  # curl 설정 파일에서 줄바꿈이 새 옵션이 되는 것을 막는 이중 방어
# curl 자식에게 넘길 환경 허용 목록. 프록시·CA 변수는 사내망에서 내려받기에 필요하며 토큰·비밀이 아님
CURL_ENV_KEYS = ("PATH", "HOME", "SSL_CERT_FILE", "LANG", "LC_ALL", "TMPDIR", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                 "NO_PROXY", "no_proxy", "CURL_CA_BUNDLE")
FILES_DIR = os.path.join(DIR, "slack-files")
FILE_MAX_BYTES = 25 * 1024 * 1024
FILE_MAX_TIME = 45                 # 파일 1개 curl --max-time 초
# 회차 전체 내려받기 시간 상한(초). api() 와 같은 벽시계 DEADLINE 을 쓰므로 내려받기가 길면 뒤의 conversations.replies·reactions.get
# 호출 시간이 줄어 회차가 실패할 수 있음. 그래서 DEADLINE 앞 DL_RESERVE 초는 내려받기에 쓰지 않음
FILE_BUDGET = float(os.environ.get("LRM_DL_BUDGET", "60"))
DL_RESERVE = 20
NAME_MAX_CHARS, NAME_MAX_BYTES = 100, 200  # 파일명 상한. 바이트 상한은 한글 3바이트 × 이름 + 접두사가 APFS 255바이트를 넘지 않게 함
USER_SUBTYPES = (None, "file_share")       # 사용자 본인 메시지로 인정할 subtype. 첨부 메시지는 file_share
SHARE_SINCE_KEY = "file_share_since"       # 상태 파일 키: 첨부 메시지 수집 시작 시각(초)
SHARE_GRACE = 300                          # 수집 시작 시각을 현재보다 앞당기는 여유(초)
OWN_SINCE_KEY = "own_reply_since"          # 상태 파일 키: 본인 최상위 메시지의 스레드 답글 수집 시작 시각(초)
ID_RE = re.compile(r"[A-Za-z0-9]{1,32}")
UNSAFE_CHARS = re.compile(r"[^0-9A-Za-z가-힣._-]")
DL = {"ok": 0, "fail": {}, "spent": 0.0}  # 회차 집계: 성공 수, 사유별 실패 수, 사용 시간
# 읽음 반응 설정. 진행 표 state["reacted"][ts] = new(👀 대기) → eyes(👀 달림) → done(✅) | skip(포기)
RX_SEEN, RX_DONE = "eyes", "white_check_mark"
RX_TYPES = ("note", "command", "thread_reply")  # 반응 대상 레코드. type=reaction·봇 줄은 제외
def env_int(name, default, lo, hi):
    """정수 환경 변수. 정수가 아니면 default, 범위 밖이면 경계값. 잘못된 설정 하나가 수집 전체를 죽이지 않게 함"""
    try:
        v = int(os.environ.get(name, default))
    except ValueError:
        return default
    return max(lo, min(v, hi))

RX_MAX = env_int("LRM_REACT_MAX", 10, 0, 50)  # 회차당 반응 API 호출 상한
RX_MAX = max(RX_MAX, 2) if RX_MAX else 0      # ✅ 전환이 제거+추가 한 쌍이라 1이면 영영 전환할 수 없음
RX_RETRY = 600                  # 권한 부족 뒤 재시도 간격(초)
RX_STALE = 1800                 # 이 시간보다 오래된 새 메시지에는 👀 를 달지 않음(권한 복구 직후 묵은 메시지 폭주 방지)
RX_KEEP = 30 * 86400            # 진행 표 보관 기간(seen 과 같음)
READ_MARK = ".main-read-lines"  # 메인이 읽은 inbox 줄 수. 메인만 갱신하고 여기서는 읽기만 함
# 같은 호출을 다시 해도 되는 오류는 성공으로 봄
RX_OK = {"add": "already_reacted", "remove": "no_reaction"}
# 권한·인증 문제: 회차를 멈추고 RX_RETRY 뒤 재시도 / 메시지 단위 문제: 그 메시지만 포기. 그 밖(429 소진·5xx·시간 예산)은 다음 회차에 재시도
RX_DISABLE = frozenset({"missing_scope", "not_allowed_token_type", "not_authed", "invalid_auth", "account_inactive", "token_revoked", "no_permission", "access_denied", "org_login_required", "ekm_access_denied", "team_access_not_granted"})
RX_SKIP = frozenset({"message_not_found", "channel_not_found", "bad_timestamp", "not_reactable", "too_many_emoji", "too_many_reactions", "is_archived", "thread_locked", "not_in_channel", "user_is_restricted"})
# 수신 확인 자동 답장 설정. 새 사용자 메시지의 스레드에 짧은 답장 1회. 켜고 끄기 LRM_ACK=off, 문구 LRM_ACK_TEXT
ACK_DEFAULT_TEXT = "확인했어요, 분석 중이에요 👀"
ACK_TEXT_MAX = 200                           # 문구 상한(글자 수)
ACK_OFF = frozenset({"off", "0", "false", "no"})
ACK_THREAD_GAP = 30                          # 같은 스레드 재전송 간격(초)
ACK_WINDOW, ACK_MAX = 60, 3                  # 상한: ACK_WINDOW 초 안 ACK_MAX 회. 회차당에도 같은 값을 적용(회차가 창보다 길어 창이 비어도 폭주하지 않게)
ACK_MIN_LEFT = 5                             # 남은 시간 예산이 이보다 짧으면 호출하지 않고 건너뜀(초)
ACK_KEEP, ACK_KEYS_MAX = 30 * 86400, 500     # 스레드별 기록 보관 기간·개수 상한
ACK_DISABLE = RX_DISABLE | {"channel_not_found", "not_in_channel", "is_archived", "restricted_action"}  # 권한·채널 문제: ACK_RETRY 뒤 재시도
ACK_RETRY = RX_RETRY
# slack_cmd.sh 가 직접 답하는 명령. case 라벨(대기|미승인|"승인 대기" / PR|pr|"초안 PR" / 할일|"할 일" / CI|ci / 문서\ *)을 옮긴 것이라 그쪽을 바꾸면 함께 바꿀 것
CMD_REPLY_EXACT = frozenset({"대기", "미승인", "승인 대기", "PR", "pr", "초안 PR", "할일", "할 일", "CI", "ci"})
CMD_REPLY_PREFIX = "문서 "

class ApiError(Exception):
    code = None  # Slack 응답의 error 코드. HTTP·시간 예산 오류는 None

def api(method, **params):
    q = urllib.parse.urlencode(params)
    for attempt in range(RETRIES + 1):
        left = DEADLINE - time.time()
        if left <= 0:
            raise ApiError(f"{method}: 재시도 시간 예산 초과")
        req = urllib.request.Request(f"{BASE}/{method}?{q}", headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=min(15, left)) as r:
                d = json.load(r)
            break
        except urllib.error.HTTPError as e:
            # 429 는 Retry-After 만큼 쉬고 재시도. 횟수·시간 예산 소진 시 ApiError 로 올려 회차를 실패 처리
            if e.code != 429 or attempt == RETRIES:
                raise ApiError(f"{method}: HTTP {e.code}")
            try:
                wait = min(float(e.headers.get("Retry-After", "1")), RETRY_CAP)
            except ValueError:
                wait = 1.0
            if wait >= DEADLINE - time.time():
                raise ApiError(f"{method}: HTTP 429, 대기 {wait:g}초가 남은 예산을 넘김")
            time.sleep(wait)
    if not d.get("ok"):
        err = ApiError(f"{method}: {d.get('error')}")
        err.code = d.get("error")
        raise err
    return d

def safe_name(name):
    # NFC 정규화(macOS 의 NFD 한글 이름 보존) 뒤 경로 구분자는 마지막 조각만 남기고, ".."·제어 문자·앞 점·허용 문자 밖은 제거 또는 _ 치환
    n = "".join(c for c in unicodedata.normalize("NFC", str(name or "")) if c.isprintable()).replace("\\", "/").rsplit("/", 1)[-1]
    n = UNSAFE_CHARS.sub("_", n)
    while ".." in n:
        n = n.replace("..", ".")
    n = n.lstrip(".")
    stem, dot, ext = n.rpartition(".")
    stem, ext = (stem, "." + ext) if dot and 0 < len(ext) <= 10 else (n, "")
    while len(stem) + len(ext) > NAME_MAX_CHARS or len((stem + ext).encode()) > NAME_MAX_BYTES:
        stem = stem[:-1]
    return (stem or "file") + ext

def slack_url(url):
    """slack.com 계열 https 주소면 비ASCII 만 퍼센트 인코딩한 주소를, 아니면 None 반환. curl 에는 이 반환값을 넘김"""
    if not isinstance(url, str):
        return None
    # 한글 경로는 허용. 비ASCII 공백·제어 문자(U+2028·U+2029·U+0085·NBSP 등)는 인코딩해 통과시키지 않고 거부
    if any(ord(c) > 127 and (c.isspace() or not c.isprintable()) for c in url):
        return None
    try:
        url = NON_ASCII_RE.sub(lambda m: urllib.parse.quote(m.group()), url)
    except UnicodeEncodeError:  # 짝 없는 서로게이트
        return None
    if not SLACK_URL_RE.fullmatch(url):
        return None
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return url if host == "slack.com" or host.endswith(".slack.com") else None

def is_slack_url(url):
    return slack_url(url) is not None

def curl_conf(url):
    # 토큰이 명령줄(ps)에 안 보이게 curl 설정을 표준입력(-K -)으로 넘김
    if CONTROL_RE.search(str(url)) or CONTROL_RE.search(TOKEN):
        raise ValueError("curl 설정에 제어 문자 불가")
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    return f'header = "Authorization: Bearer {esc(TOKEN)}"\nurl = "{esc(url)}"\n'

def fetch_file(f):
    """첨부 1개를 FILES_DIR 에 저장하고 (경로, None) 반환. 건너뛰면 (None, 사유 코드). 예외를 밖으로 내지 않음"""
    fid = str(f.get("id") or "")
    if not ID_RE.fullmatch(fid):
        return None, "id"
    # 본인이 올린 파일만 받음. 다른 사람 소유 파일은 건너뜀(owner). user 키가 없는 파일(삭제·접근 확인 필요)은 통과하나 url 이 없어 url 실패가 됨
    if f.get("user") and f["user"] != ME:
        return None, "owner"
    os.makedirs(FILES_DIR, mode=0o700, exist_ok=True)
    os.chmod(FILES_DIR, 0o700)
    prefix = f"{fid}_"
    have = sorted(n for n in os.listdir(FILES_DIR) if n.startswith(prefix))
    if have:  # 같은 file_id 가 이미 있으면 다시 받지 않음
        return os.path.join(FILES_DIR, have[0]), None
    url = f.get("url_private_download") or f.get("url_private")
    url = slack_url(url)
    if not url:
        return None, "url"
    size = f.get("size")
    if isinstance(size, int) and size > FILE_MAX_BYTES:
        return None, "size"
    left = min(FILE_BUDGET - DL["spent"], DEADLINE - time.time() - DL_RESERVE)
    if left < 1:
        return None, "budget"
    max_time = int(min(FILE_MAX_TIME, left))
    tmp = os.path.join(FILES_DIR, f".part-{os.getpid()}-{fid}")
    t0 = time.monotonic()
    try:
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        env = {k: os.environ[k] for k in CURL_ENV_KEYS if k in os.environ}
        # -q 는 첫 인자여야 ~/.curlrc 를 무시함
        p = subprocess.run(
            ["curl", "-q", "-sS", "-L", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
             "--max-filesize", str(FILE_MAX_BYTES), "--max-time", str(max_time),
             "-o", tmp, "-w", "%{http_code}\t%{content_type}", "-K", "-"],
            input=curl_conf(url), capture_output=True, text=True, timeout=max_time + 10, env=env)
        if p.returncode != 0:
            return None, "curl"
        code, _, ctype = p.stdout.strip().partition("\t")
        if code != "200":
            return None, "429" if code == "429" else "http"
        orig = (f.get("mimetype") or "").lower()
        is_html = orig.startswith("text/html") or f.get("filetype") == "html"
        if not is_html:  # 권한 부족 시 로그인 HTML 로 리다이렉트됨
            with open(tmp, "rb") as fh:
                head = fh.read(512).lstrip().lower()
            if ctype.lower().startswith("text/html") or head.startswith((b"<!doctype html", b"<html")):
                return None, "html"
        if os.path.getsize(tmp) > FILE_MAX_BYTES:
            return None, "size"
        dest = os.path.join(FILES_DIR, prefix + safe_name(f.get("name") or f.get("title")))
        os.replace(tmp, dest)
        os.chmod(dest, 0o600)
        return dest, None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception:
        return None, "error"
    finally:
        DL["spent"] += time.monotonic() - t0
        if os.path.exists(tmp):
            os.unlink(tmp)

def fetch_files(files):
    """(저장 경로 목록, 실패 목록[{"id","why"}]) 반환. 실패 항목에는 id 와 사유 코드만 담음(이름·URL·토큰 금지)"""
    paths, failed = [], []
    for f in files or []:
        try:  # 폴더 생성 실패 같은 OSError 가 main() 의 API 실패 처리로 번지지 않게 막음
            path, why = fetch_file(f) if isinstance(f, dict) else (None, "id")
        except Exception:
            path, why = None, "error"
        if path:
            paths.append(path)
            DL["ok"] += 1
        else:
            DL["fail"][why] = DL["fail"].get(why, 0) + 1
            fid = str(f.get("id") or "") if isinstance(f, dict) else ""
            failed.append({"id": fid if ID_RE.fullmatch(fid) else "", "why": why})
    return paths, failed

def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out

def classify(text):
    t = " ".join(text.split())
    for name, pat in COMMANDS:
        m = pat.match(t)
        if m:
            args = [g.strip() for g in m.groups() if g]
            return "command", name, args
    return "note", None, []

def seen_from_inbox():
    seen = {}
    for d in load_jsonl(INBOX):
        key = f"react:{d.get('parent_ts')}:{d.get('emoji')}" if d.get("type") == "reaction" else f"msg:{d.get('ts')}"
        seen[key] = int(time.time())
    return seen

def load_state():
    # 상태 파일이 없거나 손상된 경우 모두 inbox 로 seen 복원. 손상 뒤 회차가 API 실패로 끝나 파일이 안 만들어져도 다음 회차에 중복 기록이 안 생김
    if not os.path.exists(STATE):
        return {"seen": seen_from_inbox()}
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("상태 파일 형식 오류")
        return state
    except ValueError:
        os.replace(STATE, f"{STATE}.corrupt-{int(time.time())}")
        return {"seen": seen_from_inbox()}

def read_ts():
    """메인이 읽은 inbox 줄(READ_MARK 의 줄 수)까지의 사용자 메시지 ts 집합. 표식이 없거나 깨졌으면 빈 집합"""
    try:
        with open(os.path.join(DIR, READ_MARK), encoding="utf-8") as f:
            n = int(f.read().strip())
        out = set()
        # n 이 inbox 줄 수보다 크면 전부 읽음으로 봄. inbox 를 회전·자르면 표식(.main-read-lines)도 0 으로 함께 리셋할 것
        with open(INBOX, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= n:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict) and d.get("type") in RX_TYPES and d.get("ts"):
                    out.add(d["ts"])
        return out
    except (OSError, ValueError):
        return set()

def rx_map(state):
    r = state.get("reacted")
    if not isinstance(r, dict):
        r = state["reacted"] = {}
    return r

def react_phase(state, ch, now):
    """👀 달기(new → eyes), 읽힌 줄은 👀 제거 + ✅ 추가(→ done). 호출은 회차당 RX_MAX 개까지.
    다른 반응은 건드리지 않음(제거는 이 봇의 👀 만). 예외는 호출부가 삼킴"""
    rx = rx_map(state)
    for k in [k for k in rx if not (isinstance(k, str) and k.replace(".", "", 1).isdigit()) or float(k) < now - RX_KEEP]:
        del rx[k]
    # 권한 정지·호출 상한과 무관하게 묵은 new 를 정리. 정지 중 쌓인 과거 메시지에 복구 뒤 소급 반응이 달리는 것을 막음
    for k, s in rx.items():
        if s == "new" and float(k) < now - RX_STALE:
            rx[k] = "skip"
    if not any(s in ("new", "eyes") for s in rx.values()):
        return
    at = state.get("reactions_disabled_at")
    if state.get("reactions_disabled_reason") and isinstance(at, (int, float)) and now - at < RX_RETRY:
        return
    read = read_ts()
    calls = 0

    def call(op, ts, name):
        """'ok' 성공 / 'skip' 이 메시지 포기 / 'stop' 이번 회차 중단"""
        nonlocal calls
        if calls >= RX_MAX:
            return "stop"
        calls += 1
        try:
            api(f"reactions.{op}", channel=ch, timestamp=ts, name=name)
        except ApiError as e:
            if e.code == RX_OK[op]:
                return "ok"
            if e.code in RX_DISABLE:
                if state.get("reactions_disabled_reason") != e.code:  # 같은 사유는 다시 알리지 않음
                    print(f"읽음 반응 사용 불가({e.code}) — Slack 앱 scope(reactions:write) 확인, {RX_RETRY // 60}분마다 재시도", file=sys.stderr)
                state["reactions_disabled_reason"], state["reactions_disabled_at"] = e.code, int(time.time())
            return "skip" if e.code in RX_SKIP else "stop"
        state.pop("reactions_disabled_reason", None)
        state.pop("reactions_disabled_at", None)
        return "ok"

    for ts in sorted(k for k, s in rx.items() if s == "new" and k not in read):
        r = call("add", ts, RX_SEEN)
        if r == "stop":
            return
        rx[ts] = "eyes" if r == "ok" else "skip"
    for ts in sorted(k for k, s in rx.items() if s in ("new", "eyes") and k in read):
        if rx[ts] == "eyes":
            if calls + 2 > RX_MAX:  # 제거만 하고 추가가 상한에 걸리면 👀·✅ 둘 다 없는 회차가 생김. 한 쌍이 들어갈 때만 시작
                return
            r = call("remove", ts, RX_SEEN)
            if r == "stop":
                return
            if r == "skip":
                rx[ts] = "skip"
                continue
        r = call("add", ts, RX_DONE)
        if r == "stop":
            return
        rx[ts] = "done" if r == "ok" else "skip"

def ack_text():
    """LRM_ACK_TEXT 를 정리한 답장 문구. 제어 문자·짝 없는 서로게이트(잘못된 UTF-8)는 제거, ACK_TEXT_MAX 자로 자름. 비면 기본 문구"""
    raw = os.environ.get("LRM_ACK_TEXT") or ACK_DEFAULT_TEXT
    t = "".join(c for c in raw if unicodedata.category(c) not in ("Cc", "Cs")).strip()[:ACK_TEXT_MAX].strip()
    return t or ACK_DEFAULT_TEXT

def cmd_replies(item):
    """slack_cmd.sh 가 직접 답하는 메시지인가. slack_cmd.sh 의 처리 대상(type=command|note, 스레드 답글 제외)과
    줄 단위 case 판별(read -r 이 줄 양끝 공백·탭 제거)을 그대로 따름"""
    if item.get("type") not in ("command", "note") or item.get("parent_ts"):
        return False
    for line in (item.get("text") or item.get("command") or "").strip().split("\n"):
        t = line.strip(" \t")
        if t in CMD_REPLY_EXACT or t.startswith(CMD_REPLY_PREFIX):
            return True
    return False

def ack_phase(state, ch, now, items):
    """새 사용자 메시지의 스레드(스레드 답글이면 부모, 최상위면 그 메시지)에 확인 답장 1회.
    제외: 결정·승인 같은 명령(command, 분석 대상 아님), slack_cmd.sh 즉답 명령, RX_STALE 보다 묵은 항목. 같은 스레드 ACK_THREAD_GAP 초 1회, ACK_WINDOW 초 ACK_MAX 회, 회차 ACK_MAX 회.
    전송 전에 상태를 먼저 저장(at-most-once): 실패해도 재시도하지 않음. 예외는 호출부가 삼킴"""
    if os.environ.get("LRM_ACK", "").strip().lower() in ACK_OFF:
        return
    cand = sorted((it for it in items if it.get("type") in RX_TYPES and float(it["ts"]) >= now - RX_STALE
                 and not it.get("command") and not cmd_replies(it)),
                  key=lambda it: float(it["ts"]))
    if not cand:
        return
    at = state.get("ack_disabled_at")
    if state.get("ack_disabled_reason") and isinstance(at, (int, float)) and time.time() - at < ACK_RETRY:
        return
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    t = int(time.time())
    acked = state.get("acked")
    acked = {k: v for k, v in (acked if isinstance(acked, dict) else {}).items() if num(v) and v > t - ACK_KEEP}
    for k in sorted(acked, key=acked.get)[:max(0, len(acked) - (ACK_KEYS_MAX - ACK_MAX))]:  # 이번 회차에 더해도 ACK_KEYS_MAX 이하
        del acked[k]
    state["acked"] = acked
    sent = state.get("ack_sent")
    sent = [x for x in (sent if isinstance(sent, list) else []) if num(x)]
    text, count = ack_text(), 0
    for it in cand:
        root = str(it.get("parent_ts") or it["ts"])
        t = int(time.time())
        sent = [x for x in sent if 0 <= t - x < ACK_WINDOW]
        if count >= ACK_MAX or len(sent) >= ACK_MAX:
            break
        if 0 <= t - acked.get(root, -ACK_THREAD_GAP - 1) < ACK_THREAD_GAP:
            continue
        if DEADLINE - time.time() < ACK_MIN_LEFT:  # 수집이 예산을 다 쓴 회차. 남은 항목은 새 항목이 아니게 돼 다시 오지 않으므로 답장 없이 끝남(재시도 안 함)
            print("수신 확인 답장 건너뜀(budget) — 회차 시간 예산 부족, 재시도 안 함", file=sys.stderr)
            return
        acked[root] = t
        sent.append(t)
        state["ack_sent"] = sent
        save_state(state)
        count += 1
        try:
            api("chat.postMessage", channel=ch, thread_ts=root, text=text)
        except ApiError as e:
            if e.code in ACK_DISABLE:
                if state.get("ack_disabled_reason") != e.code:  # 같은 사유는 다시 알리지 않음
                    print(f"수신 확인 답장 사용 불가({e.code}) — Slack 앱 scope(chat:write)·채널 확인, {ACK_RETRY // 60}분마다 재시도", file=sys.stderr)
                state["ack_disabled_reason"], state["ack_disabled_at"] = e.code, int(time.time())
            else:
                print(f"수신 확인 답장 실패({e.code or 'http'}) — 재시도 안 함", file=sys.stderr)
            return
        state.pop("ack_disabled_reason", None)
        state.pop("ack_disabled_at", None)
    state["ack_sent"] = sent

def fetch_history(ch, oldest):
    msgs, cursor = [], None
    for _ in range(MAX_PAGES):
        params = {"channel": ch, "oldest": oldest, "limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        d = api("conversations.history", **params)
        msgs += d.get("messages", [])
        cursor = (d.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return msgs

def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)

def main():
    state = load_state()
    # 첨부 메시지 수집 시작 시각. 키가 없으면 API 호출 전에 저장해, 첫 회차가 실패를 반복해도 시각이 뒤로 밀리지 않게 함.
    # now-SHARE_GRACE: 배포 직전 5분 안에 올린 본인 첨부는 새 것으로 처리하는 것이 의도이며, 그 이전 첨부(옛 명령)는 계속 재생하지 않음
    if not isinstance(state.get(SHARE_SINCE_KEY), (int, float)):
        state[SHARE_SINCE_KEY] = int(time.time()) - SHARE_GRACE
        save_state(state)
    since = state[SHARE_SINCE_KEY]
    # 본인 메시지 스레드 답글 수집 시작 시각. 이전에는 수집하지 않던 묵은 답글이 배포 직후 새 명령으로 재생되지 않게 하는 경계. 키가 없으면 지금(-여유)으로 저장
    if not isinstance(state.get(OWN_SINCE_KEY), (int, float)):
        state[OWN_SINCE_KEY] = int(time.time()) - SHARE_GRACE
        save_state(state)
    own_since = state[OWN_SINCE_KEY]
    seen = state.setdefault("seen", {})
    latest = state.setdefault("latest", {})  # 스레드 부모 ts → 마지막으로 읽은 latest_reply
    ch = api("conversations.open", users=ME)["channel"]["id"]
    cutoff = time.time() - DAYS * 86400
    sent = {r["ts"]: r for r in load_jsonl(SENT) if r.get("ts") and r.get("at", 0) >= cutoff}
    # 파일 업로드는 ts 를 못 받아 file_id 로만 기록됨. 메시지의 files[].id 로 찾아 답글·반응 수집
    sent_files = {r["file_id"]: r for r in load_jsonl(SENT) if r.get("file_id") and r.get("at", 0) >= cutoff}
    new = []
    now = int(time.time())

    # since 이전 첨부 메시지는 지난 메시지가 새 명령·답으로 재생되지 않게 seen 에만 넣음
    def is_backlog(msg):
        return msg.get("subtype") == "file_share" and float(msg["ts"]) < since

    def add(key, item, files=None, backlog=False):
        if key in seen:
            return
        seen[key] = now
        if backlog:
            return
        item.update({"channel": ch, "received_at": now})
        # 새 항목일 때만 내려받음(30초 주기 재조회에서 반복 방지). 파일이 없으면 기존 레코드 모양 유지
        paths, failed = fetch_files(files)
        if paths:
            item["files"] = paths
        if failed:
            item["files_failed"] = failed
        new.append(item)
        if item["type"] in RX_TYPES:
            rx_map(state).setdefault(item["ts"], "new")

    def collect_replies(m, meta, floor=None):
        """답글이 바뀐 스레드만 조회해 사용자 답글을 thread_reply 로 추가. latest_reply 가 응답에 없을 수 있어 없으면 reply_count 로 비교.
        floor: 이 시각(초) 이전 답글은 기록만 하고 새 항목으로 만들지 않음(본인 메시지 스레드의 배포 전 답글)"""
        ts = m["ts"]
        marker = m.get("latest_reply") or f"n{m.get('reply_count')}"
        if m.get("reply_count") and marker != latest.get(ts):
            lr = m.get("latest_reply")
            if not (floor and lr and float(lr) < floor):  # 마지막 답글이 경계 이전이면 조회 자체를 생략
                for rp in api("conversations.replies", channel=ch, ts=ts, limit=200).get("messages", []):
                    if rp["ts"] != ts and rp.get("user") == ME and not rp.get("bot_id") and rp.get("subtype") in USER_SUBTYPES:
                        kind, cmd, args = classify(rp.get("text", ""))
                        item = {"type": "thread_reply", "ts": rp["ts"], "text": rp.get("text", ""), **meta}
                        if cmd:
                            item.update({"command": cmd, "args": args})
                        add(f"msg:{rp['ts']}", item, rp.get("files"), is_backlog(rp) or bool(floor and float(rp["ts"]) < floor))
            latest[ts] = marker

    # 반응·답글 수는 목록 응답에 들어 있어 메시지별 조회가 필요 없음. 반응은 오래된 메시지에도 달리므로 매번 기간 전체를 읽음
    hist = fetch_history(ch, f"{cutoff:.6f}")
    for m in hist:
        ts = m["ts"]
        mine = m.get("user") == ME
        own_top = mine and not m.get("bot_id") and m.get("subtype") in USER_SUBTYPES and m.get("thread_ts", ts) == ts
        if own_top:
            kind, cmd, args = classify(m.get("text", ""))
            item = {"type": kind, "ts": ts, "text": m.get("text", "")}
            if cmd:
                item.update({"command": cmd, "args": args})
            add(f"msg:{ts}", item, m.get("files"), is_backlog(m))
        # 반응·답글은 slack_notify.sh 가 기록한 메시지만. 대기 목록 같은 다른 봇 메시지의 반응이 결정 답으로 오판되지 않게 함
        rec = sent.get(ts) or next((sent_files[f["id"]] for f in m.get("files", []) if f.get("id") in sent_files), None)
        if mine and not m.get("files") or rec is None:
            # 본인 최상위 메시지: 확인 답장 스레드에 이어 쓴 사용자 답글만 수집(반응은 대상 아님, 봇 답장은 collect_replies 가 제외)
            if own_top:
                collect_replies(m, {"parent_ts": ts, "title": "", "tag": ""}, own_since)
            continue
        meta = {"parent_ts": ts, "title": rec.get("title", ""), "tag": rec.get("tag", "")}
        reactions = m.get("reactions", [])
        # 응답의 users 는 잘릴 수 있어 본인 반응이 안 보이고 개수가 더 많으면 그 메시지만 reactions.get 으로 보강
        if any(r.get("count", 0) > len(r.get("users", [])) and ME not in r.get("users", []) for r in reactions):
            reactions = api("reactions.get", channel=ch, timestamp=ts).get("message", {}).get("reactions", [])
        for r in reactions:
            if ME in r.get("users", []):
                name = r["name"]
                add(f"react:{ts}:{name}", {"type": "reaction", "emoji": name,
                                           "action": REACTION_ACTION.get(name, name), **meta})
        collect_replies(m, meta)

    # 30일 지난 중복 방지 키 정리, 기간 밖 스레드 기록 정리
    for k in [k for k, v in seen.items() if v < now - 30 * 86400]:
        del seen[k]
    in_range = {m["ts"] for m in hist}
    for k in [k for k in latest if k not in in_range and float(k) < cutoff]:
        del latest[k]
    with open(INBOX, "a", encoding="utf-8") as f:
        for it in new:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    save_state(state)  # 반응 호출은 DEADLINE 까지 걸릴 수 있어, 그 사이 죽어도 inbox 중복 기록이 안 생기게 먼저 저장
    try:
        ack_phase(state, ch, now, new)
    except Exception:  # 확인 답장 실패가 수집·상태 저장·반응 단계를 막지 않게 함
        pass
    try:
        react_phase(state, ch, now)
    except Exception:  # 반응 실패가 수집·상태 저장을 막지 않게 함
        pass
    save_state(state)
    bad = sum(DL["fail"].values())
    if bad:  # 사유 코드만 출력. URL·파일명·토큰은 넣지 않음
        why = ", ".join(f"{k} {v}" for k, v in sorted(DL["fail"].items()))
        print(f"첨부 내려받기 실패 {bad}건(성공 {DL['ok']}건) — {why}", file=sys.stderr)
    return len(new)

try:
    n = main()
except (ApiError, OSError) as e:
    msg = str(e)
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False) if AS_JSON else f"실패: {msg} — 네트워크·scope(im:history, reactions:read) 확인", file=sys.stdout if AS_JSON else sys.stderr)
    sys.exit(1)
print(json.dumps({"ok": True, "added": n}) if AS_JSON else f"새 항목 {n}건 → {INBOX}")
PY

# 2시간마다 미답 결정 목록 전송(11-05 사용자 요청). 마지막 전송 시각은 .pending-last
L="$DIR/.pending-last"; now=$(date +%s); last=$(cat "$L" 2>/dev/null || echo 0)
if [ $((now - last)) -ge 7200 ]; then echo "$now" > "$L"; LRM_PENDING_BUDGET="$(cap "${LRM_PENDING_BUDGET:-120}")" "$DIR/slack_pending.sh" >/dev/null 2>&1 || true; fi

# 봇 DM 즉답 명령(대기·PR) 처리. 대기 명령이 대기 목록을 다시 부르므로 남은 예산을 그대로 물려줌
LRM_PENDING_BUDGET="$(cap "${LRM_PENDING_BUDGET:-120}")" "$DIR/slack_cmd.sh" >/dev/null 2>&1 || true
exit "$RC"
