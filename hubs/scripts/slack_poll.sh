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
           file_share_since = 첨부 메시지 수집을 시작한 시각. 그 이전 첨부 메시지는 기록·내려받기 없이 건너뜀)
환경 변수: LRM_HUB_DIR 작업 폴더(기본 ~/.config/lrm-hubs), SLACK_API_BASE API 주소(테스트용),
           LRM_RETRY_CAP 429 대기 상한 초(기본 20), LRM_DL_BUDGET 회차 첨부 내려받기 시간 상한 초(기본 60)
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
ID_RE = re.compile(r"[A-Za-z0-9]{1,32}")
UNSAFE_CHARS = re.compile(r"[^0-9A-Za-z가-힣._-]")
DL = {"ok": 0, "fail": {}, "spent": 0.0}  # 회차 집계: 성공 수, 사유별 실패 수, 사용 시간

class ApiError(Exception):
    pass

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
        raise ApiError(f"{method}: {d.get('error')}")
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

    # 반응·답글 수는 목록 응답에 들어 있어 메시지별 조회가 필요 없음. 반응은 오래된 메시지에도 달리므로 매번 기간 전체를 읽음
    hist = fetch_history(ch, f"{cutoff:.6f}")
    for m in hist:
        ts = m["ts"]
        mine = m.get("user") == ME
        if mine and not m.get("bot_id") and m.get("subtype") in USER_SUBTYPES and m.get("thread_ts", ts) == ts:
            kind, cmd, args = classify(m.get("text", ""))
            item = {"type": kind, "ts": ts, "text": m.get("text", "")}
            if cmd:
                item.update({"command": cmd, "args": args})
            add(f"msg:{ts}", item, m.get("files"), is_backlog(m))
        # 반응·답글은 slack_notify.sh 가 기록한 메시지만. 대기 목록 같은 다른 봇 메시지의 반응이 결정 답으로 오판되지 않게 함
        rec = sent.get(ts) or next((sent_files[f["id"]] for f in m.get("files", []) if f.get("id") in sent_files), None)
        if mine and not m.get("files") or rec is None:
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
        # 답글이 바뀐 스레드만 조회. latest_reply 가 응답에 없을 수 있어 없으면 reply_count 로 비교
        marker = m.get("latest_reply") or f"n{m.get('reply_count')}"
        if m.get("reply_count") and marker != latest.get(ts):
            for rp in api("conversations.replies", channel=ch, ts=ts, limit=200).get("messages", []):
                if rp["ts"] != ts and rp.get("user") == ME and not rp.get("bot_id") and rp.get("subtype") in USER_SUBTYPES:
                    kind, cmd, args = classify(rp.get("text", ""))
                    item = {"type": "thread_reply", "ts": rp["ts"], "text": rp.get("text", ""), **meta}
                    if cmd:
                        item.update({"command": cmd, "args": args})
                    add(f"msg:{rp['ts']}", item, rp.get("files"), is_backlog(rp))
            latest[ts] = marker

    # 30일 지난 중복 방지 키 정리, 기간 밖 스레드 기록 정리
    for k in [k for k, v in seen.items() if v < now - 30 * 86400]:
        del seen[k]
    in_range = {m["ts"] for m in hist}
    for k in [k for k in latest if k not in in_range and float(k) < cutoff]:
        del latest[k]
    with open(INBOX, "a", encoding="utf-8") as f:
        for it in new:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
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
