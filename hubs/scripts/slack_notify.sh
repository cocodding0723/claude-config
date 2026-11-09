#!/bin/bash
# 봇으로 사용자 DM 에 글·파일 게시. 토큰은 slack-bot.env(SLACK_BOT_TOKEN, SLACK_USER_ID)에서만 읽음, 출력 금지.
# 보낸 메시지 ts·channel 은 slack-sent.jsonl 에 기록(slack_poll.sh 가 스레드 답글·반응 수집에 사용).
# --kind 를 주면 메시지 양식 A(hubs/docs/slack-format.md)를 검사함. --kind 없는 기존 호출은 검사·출력 모두 불변.
set -eu
usage() {
  cat <<'EOF'
사용: slack_notify.sh [--tag 허브] [--title 제목] [--thread TS] [--kind 종류 [--choices N] [--emoji-set 종류]] [--no-check] "메시지" [파일경로]
  --tag        관련 허브 태그(기록용)
  --title      기록용 제목(기본: 메시지 첫 줄)
  --thread     해당 ts 메시지의 스레드 답글로 게시
  --kind       decide|todo|done|progress|warn|doc. 첫 줄에 종류 이모지가 없으면 붙이고 양식 A 를 검사
  --choices    2~4(--kind decide 전용). 메시지 끝에 '👉 답:' 이모지 안내 줄을 붙임(본문에 👉 줄이 있으면 생략)
  --emoji-set  custom(기본, :비서-a: 등)|std(표준 :a: :regional_indicator_c: 등). 안내 줄 이모지 종류
  --no-check   양식 검사 생략(slack-sent.jsonl 에 unchecked:true 기록)
환경 변수: LRM_FORMAT=warn|strict|off (기본 warn). warn 은 위반해도 전송하고 stderr 경고, strict 는 위반 시 미전송·종료 코드 2
예:
  slack_notify.sh "빌드 완료"
  slack_notify.sh --tag ui-hub "결정1 A/B?" report.html
  slack_notify.sh --thread 1730790000.123456 "반영함"
  slack_notify.sh --kind done "알림 시각 통일"
  slack_notify.sh --kind decide --choices 3 "알림 시각 정하기"
종료 코드: 0 성공, 1 API 실패, 2 설정·인자·양식 오류
EOF
}
die() { echo "$1" >&2; exit 2; }
TAG="" TITLE="" THREAD="" KIND="" NOCHECK=0 CHOICES="" CHOICES_SET=0 EMOJISET=custom
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --tag) TAG="${2:?--tag 값 필요}"; shift 2 ;;
    --title) TITLE="${2:?--title 값 필요}"; shift 2 ;;
    --thread) THREAD="${2:?--thread 값 필요}"; shift 2 ;;
    --kind) [ $# -ge 2 ] || die "--kind 값 필요 — decide|todo|done|progress|warn|doc 중 하나"; KIND="$2"; shift 2 ;;
    --choices) [ $# -ge 2 ] || die "--choices 값 필요 — 2~4 사이 숫자"; CHOICES="$2" CHOICES_SET=1; shift 2 ;;
    --emoji-set) [ $# -ge 2 ] || die "--emoji-set 값 필요 — custom|std 중 하나"; EMOJISET="$2"; shift 2 ;;
    --no-check) NOCHECK=1; shift ;;
    --) shift; break ;;
    -*) echo "알 수 없는 옵션: $1 — --help 참고" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || { usage >&2; exit 2; }
case "$KIND" in
  ""|decide|todo|done|progress|warn|doc) ;;
  *) die "알 수 없는 --kind: $KIND — decide|todo|done|progress|warn|doc 중 하나로 지정" ;;
esac
case "$EMOJISET" in
  custom|std) ;;
  *) die "알 수 없는 --emoji-set: $EMOJISET — custom|std 중 하나로 지정" ;;
esac
if [ "$CHOICES_SET" = 1 ]; then
  case "$CHOICES" in 2|3|4) ;; *) die "--choices 범위 오류: $CHOICES — 2~4 사이 숫자로 지정" ;; esac
  [ "$KIND" = decide ] || die "--choices 는 --kind decide 와만 사용 — --kind decide 를 함께 지정"
fi
DIR="$HOME/.config/lrm-hubs"
ENV="$DIR/slack-bot.env"
SENT="$DIR/slack-sent.jsonl"
FMTLOG="$DIR/slack-format.log"
[ -f "$ENV" ] || { echo "slack-bot.env 없음 — 사용자 설정 필요" >&2; exit 2; }
. "$ENV"
: "${SLACK_BOT_TOKEN:?}" "${SLACK_USER_ID:?}"
API="${SLACK_API_BASE:-https://slack.com/api}"  # SLACK_API_BASE: 시험용 주소 재정의
MSG="$1"

# --kind 종류 이모지. 첫 줄에 6종 중 하나(유니코드·치환 이름 모두)가 있으면 붙이지 않음. 붙인 이모지는 아래 치환표를 그대로 탐
kind_glyph() {
  case "$1" in
    decide) echo '🙋' ;; todo) echo '🛠️' ;; done) echo '✅' ;;
    progress) echo '🔄' ;; warn) echo '⚠️' ;; doc) echo '📄' ;;
  esac
}
has_kind_emoji() {
  local first="${1%%$'\n'*}" t
  for t in 🙋 🛠 ✅ 🔄 ⚠ 📄 :비서-선택해주세요: :raising_hand: :hammer_and_wrench: :비서-추천대로: :white_check_mark: \
    :비서-진행중: :arrows_counterclockwise: :비서-긴급: :warning: :비서-공유합니다: :page_facing_up:; do
    case "$first" in *"$t"*) return 0 ;; esac
  done
  return 1
}
if [ -n "$KIND" ] && ! has_kind_emoji "$MSG"; then MSG="$(kind_glyph "$KIND") $MSG"; fi

# 기본 이모지 → 워크스페이스 강비서 이모지
# 줄머리(공백·'•' 불릿 뒤 포함) ✅ 는 완료 표시라 :white_check_mark:, 줄 중간 ✅ 는 반응 선택지라 :비서-추천대로:.
# 기존 호출자 불변 원칙의 명시적 예외: --kind 없는 호출에도 줄머리 ✅ 규칙을 적용함
MSG="$(printf '%s' "$MSG" | sed -e 's/🅰️/:비서-a:/g' -e 's/🅱️/:비서-b:/g' -e 's/^\([[:blank:]]*\)✅/\1:white_check_mark:/' -e 's/^\([[:blank:]]*• *\)✅/\1:white_check_mark:/' -e 's/✅/:비서-추천대로:/g' -e 's/❌/:비서-보류:/g' -e 's/🙋/:비서-선택해주세요:/g' -e 's/📄/:비서-공유합니다:/g' -e 's/🔄/:비서-진행중:/g' -e 's/⚠️/:비서-긴급:/g')"
# 🅲·🅳 는 변형 선택자(VS16) 유무 둘 다 처리. 대응 이모지(:비서-c:·:비서-d:) 존재 미확인이라 --kind 지정 시에만 치환, 기존 호출은 원문 유지
if [ -n "$KIND" ]; then
  MSG="$(printf '%s' "$MSG" | sed -e 's/🅲️/:비서-c:/g' -e 's/🅲/:비서-c:/g' -e 's/🅳️/:비서-d:/g' -e 's/🅳/:비서-d:/g')"
fi

# 양식 A 검사·안내 줄 부착용 파이썬. 모든 정규식은 여기에만 둠. 구간 제거는 길이를 유지해 줄 위치를 보존함
#   check  <종류> <안내줄부착예정 0|1>: "수준<TAB>코드<TAB>메시지" 줄 출력. reject 는 위반(strict 에서 거부), warn 은 경고만
#   attach <안내줄>: 본문에 '👉 답:' 줄이 없으면 마지막 '참고:' 줄 앞(없으면 맨 끝)에 안내 줄을 끼워 출력
# 검사 제외 구간: 코드 블록·인라인 코드·URL, 마지막 '참고:' 줄 이후(티켓·커밋 번호 허용, 첫 줄 제외)
read -r -d '' FORM_PY <<'PYEOF' || true
import re, sys, unicodedata

RX = {
    "fence": re.compile(r"```.*?```", re.S),
    "inline": re.compile(r"`[^`\n]*`"),
    "url": re.compile(r"https?://\S+"),
    "ref": re.compile(r"^[ \t]*(?:[•\-*][ \t]*)?참고[ \t]*:", re.M),
    "code": re.compile(r"(?<!\w):[\w+\-]{1,40}:(?!\w)"),
    "ticket": re.compile(r"(?<![0-9A-Za-z_])LRM-\d+(?![0-9A-Za-z_])"),
    "hex": re.compile(r"(?<![0-9A-Za-z_])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9A-Za-z_])", re.I),
    "bold": re.compile(r"(?<![\w*])\*(?!\s)[^*\n]+?(?<!\s)\*(?![0-9A-Za-z_*])"),
    "answer": re.compile(r"^[ \t]*(?:👉|:point_right:)[ \t]*답[ \t]*:", re.M),
}

def scrub(msg):
    for key in ("fence", "inline", "url"):
        msg = RX[key].sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), msg)
    return msg

def ref_start(scan):
    """마지막 '참고:' 줄의 시작 위치. 첫 줄은 주제라 제외, 없으면 None"""
    pos = [m.start() for m in RX["ref"].finditer(scan) if m.start() > 0]
    return pos[-1] if pos else None

def check_form(msg, kind, guided):
    out = []
    head = RX["code"].sub("", msg.split("\n", 1)[0])
    head = "".join(c for c in head if unicodedata.category(c) not in ("So", "Sk", "Cf", "Mn", "Me") and not c.isspace())
    if len(head) > 40:
        out.append(("reject", "title-len", "첫 줄이 %d자(최대 40) — 주제를 짧게 줄이고 세부는 '• 무엇:' 줄로 옮기세요" % len(head)))
    scan = scrub(msg)
    pos = ref_start(scan)
    body = scan if pos is None else scan[:pos]
    for key, label in (("ticket", "티켓 번호"), ("hex", "커밋 번호로 보이는 값")):
        hits = list(dict.fromkeys(x.group(0) for x in RX[key].finditer(body)))
        if hits:
            out.append(("reject", key, "본문에 %s %s — 맨 아래 '참고:' 줄로 옮기세요" % (label, ", ".join(hits[:3]))))
    if kind in ("decide", "todo") and not guided and not RX["answer"].search(body):
        out.append(("reject", "no-answer", "결정·할 일인데 '👉 답:' 줄이 없음 — '👉 답: …' 줄을 쓰거나 --kind decide --choices N 으로 안내 줄을 붙이세요"))
    n = len(RX["bold"].findall(scan))
    if n > 1:
        out.append(("warn", "bold", "굵게가 %d곳 — 메시지당 1곳으로 줄이세요" % n))
    return out

def attach_guide(msg, guide):
    scan = scrub(msg)
    pos = ref_start(scan)
    if RX["answer"].search(scan if pos is None else scan[:pos]):
        return msg
    if pos is None:
        return msg.rstrip("\n") + "\n\n" + guide
    return msg[:pos].rstrip("\n") + "\n\n" + guide + "\n\n" + msg[pos:].lstrip("\n")

data = sys.stdin.buffer.read().decode("utf-8", "replace")
if sys.argv[1] == "check":
    for lvl, code, text in check_form(data, sys.argv[2], sys.argv[3] == "1"):
        sys.stdout.buffer.write(("%s\t%s\t%s\n" % (lvl, code, text)).encode("utf-8"))
else:
    sys.stdout.buffer.write(attach_guide(data, sys.argv[2]).encode("utf-8"))
PYEOF
check_form() { printf '%s' "$1" | python3 -c "$FORM_PY" check "$2" "$3"; }
attach_guide() { printf '%s' "$1" | python3 -c "$FORM_PY" attach "$2"; }

# 결정 메시지 끝의 이모지 답 안내 줄. --choices N 만큼 A.. 나열, 추천대로·보류는 항상 붙임. 이름은 slack_poll.sh 반응표와 일치해야 함
guide_line() {
  local a b c d ok no g
  if [ "$EMOJISET" = std ]; then
    a=:a: b=:b: c=:regional_indicator_c: d=:regional_indicator_d: ok=:white_check_mark: no=:x:
  else
    a=:비서-a: b=:비서-b: c=:비서-c: d=:비서-d: ok=:비서-추천대로: no=:비서-보류:
  fi
  g="👉 답: $a A · $b B"
  if [ "$CHOICES" -ge 3 ]; then g="$g · $c C"; fi
  if [ "$CHOICES" -ge 4 ]; then g="$g · $d D"; fi
  printf '%s' "$g · $ok 추천대로 · $no 보류"
}

# 집계 로그 1줄(시각, 종류, 위반 코드 또는 ok, 모드, 태그). 본문·제목은 기록하지 않음. 쓰기 실패는 전송에 영향 없음
log_format() {
  local tag="${TAG:--}"; tag="${tag//[$'\t\r\n']/ }"; tag="$(printf '%s' "$tag" | LC_ALL=C tr -d '\000-\010\013-\037\177')"; tag="${tag:0:40}"
  ( umask 077; printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$1" "$2" "$3" "$tag" >> "$FMTLOG" ) 2>/dev/null || true
  chmod 600 "$FMTLOG" 2>/dev/null || true
}

if [ -n "$KIND" ] && [ "$NOCHECK" = 0 ]; then
  MODE="${LRM_FORMAT:-warn}"
  case "$MODE" in warn|strict|off) ;; *) die "LRM_FORMAT 값 오류: $MODE — warn|strict|off 중 하나로 지정" ;; esac
  if [ "$MODE" != off ]; then
    FORM_N=0 FORM_CODES="" FORM_OUT=""
    if ! FORM_OUT="$(check_form "$MSG" "$KIND" "$CHOICES_SET")"; then
      [ "$MODE" != strict ] || die "양식 검사 실패(전송 안 함) — python3 동작 확인 뒤 다시 보내거나 --no-check 로 우회"
      echo "양식 검사 실패 — 검사 없이 전송" >&2
      FORM_OUT="" FORM_CODES="check-fail"
    fi
    while IFS=$'\t' read -r lvl code text; do
      [ -n "$code" ] || continue
      FORM_CODES="$FORM_CODES${FORM_CODES:+,}$code"
      if [ "$lvl" = reject ]; then FORM_N=$((FORM_N + 1)); fi
      if [ "$lvl" = reject ] && [ "$MODE" = strict ]; then echo "거부: $text" >&2; else echo "경고: $text" >&2; fi
    done <<< "$FORM_OUT"
    log_format "$KIND" "${FORM_CODES:-ok}" "$MODE"
    if [ "$FORM_N" -gt 0 ]; then
      if [ "$MODE" = strict ]; then echo "양식 위반 ${FORM_N}건(전송 안 함) — 고치거나 --no-check 로 우회" >&2; exit 2; fi
      echo "양식 위반 ${FORM_N}건(전송함)" >&2
    fi
  fi
fi

# 검사 뒤에 안내 줄 부착. 치환을 이미 마쳤으므로 안내 줄의 이모지 이름은 다시 치환되지 않음
if [ "$CHOICES_SET" = 1 ]; then
  if GUIDED="$(attach_guide "$MSG" "$(guide_line)")"; then
    MSG="$GUIDED"
  else
    [ "${LRM_FORMAT:-warn}" != strict ] || die "안내 줄 부착 실패(전송 안 함) — python3 동작 확인 뒤 다시 보내거나 --choices 없이 '👉 답:' 줄을 직접 작성"
    echo "안내 줄 부착 실패 — 안내 줄 없이 전송" >&2
  fi
fi

[ -n "$TITLE" ] || TITLE="${MSG%%$'\n'*}"
CH=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -d "users=$SLACK_USER_ID" $API/conversations.open | python3 -c 'import sys,json;print(json.load(sys.stdin)["channel"]["id"])')

# 응답 JSON 을 받아 ok/에러 출력 + 성공 시 sent 기록. ts 는 postMessage 만 제공, 파일 업로드는 file_id 로 남김.
# form_kind(--kind 지정 시), unchecked(--no-check 시)는 해당할 때만 기록해 기존 레코드 모양을 유지함.
record() {
  python3 -c '
import sys,json,time,os
d=json.load(sys.stdin); sent,ch,title,tag,thread,kind,fkind,unchecked=sys.argv[1:9]
if not d.get("ok"):
    print(d.get("error")); sys.exit(1)
rec={"at":int(time.time()),"channel":ch,"ts":d.get("ts",""),"thread_ts":thread,"kind":kind,"title":title[:200],"tag":tag}
if fkind: rec["form_kind"]=fkind
if unchecked=="1": rec["unchecked"]=True
if kind=="file":
    rec["file_id"]=(d.get("files") or [{}])[0].get("id","")
    rec["path"]=os.environ.get("LRM_FILE_PATH","")
with open(sent,"a",encoding="utf-8") as f: f.write(json.dumps(rec,ensure_ascii=False)+"\n")
print("ok", rec["ts"])
' "$SENT" "$CH" "$TITLE" "$TAG" "$THREAD" "$1" "$KIND" "$NOCHECK"
}

if [ $# -ge 2 ]; then
  F="$2"; export LRM_FILE_PATH="$F"; N=$(basename "$F"); L=$(stat -f %z "$F")
  R=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" --data-urlencode "filename=$N" -d "length=$L" $API/files.getUploadURLExternal)
  U=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_url"])'); ID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["file_id"])')
  curl -s -o /dev/null -F "file=@$F" "$U"
  curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
b={"files":[{"id":sys.argv[1],"title":sys.argv[2]}],"channel_id":sys.argv[3],"initial_comment":sys.argv[4]}
if sys.argv[5]: b["thread_ts"]=sys.argv[5]
print(json.dumps(b))' "$ID" "$N" "$CH" "$MSG" "$THREAD")" \
    $API/files.completeUploadExternal | record file
else
  curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H 'Content-Type: application/json' \
    -d "$(python3 -c '
import json,sys
b={"channel":sys.argv[1],"text":sys.argv[2]}
if sys.argv[3]: b["thread_ts"]=sys.argv[3]
print(json.dumps(b))' "$CH" "$MSG" "$THREAD")" \
    $API/chat.postMessage | record text
fi
