#!/usr/bin/env bash
# doc-kit 검증: 설정 읽기·검사 스크립트·고유값 0건. 임시 폴더에서만 실행하며 실제 ~/.config 는 읽지 않음.
# 시험 4(샘플 문서 전달 규칙)는 sample.sh. 파이썬 3.9 시험은 /usr/bin/python3 가 3.11 미만일 때만 실행함.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
KIT="$REPO/skills/doc-kit"
FIX="$REPO/tests/doc-kit/fixtures"
CFG="$FIX/projects.test.toml"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

W=$(mktemp -d "${TMPDIR:-/tmp}/cc-dockit.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT
# 환경에 남은 지정값이 시험을 흔들지 않게 비움. HOME 도 임시 폴더로 바꿔 실제 설정을 읽지 않음
unset DOC_KIT_PROJECT DOC_KIT_CONFIG
export HOME="$W/home"; mkdir -p "$HOME"

cfg() { python3 -I "$KIT/scripts/doc_config.py" "$@"; }
chk() { python3 -I "$KIT/scripts/check.py" "$@"; }

# ---- 설정 읽기 ---------------------------------------------------------------
out=$(DOC_KIT_CONFIG="$W/없는파일.toml" cfg show 2>"$W/err"); rc=$?
[ "$rc" = 0 ] && echo "$out" | grep -qx 'audience=개발자' && echo "$out" | grep -qx 'out_dir=docs/out' && grep -q '설정 없음 — 기본값' "$W/err" && ok "설정 파일 없음: 기본값 + 안내 한 줄" || ko "설정 없음" "rc=$rc"
out=$(cfg show --config "$CFG" --project 시험A 2>&1); rc=$?
[ "$rc" = 0 ] && echo "$out" | grep -qx 'channel=있음' && echo "$out" | grep -qx 'recipients=있음' && ! echo "$out" | grep -q '시험채널값' && ok "show: 채널·받는 사람은 값 대신 있음만 출력" || ko "show 값 숨김" "$(echo "$out" | grep -E 'channel|recipients' | tr '\n' ' ')"
out=$(cfg show --json --config "$CFG" --project 시험A 2>&1); echo "$out" | grep -q '"channel": "있음"' && ! echo "$out" | grep -q '시험채널값' && ok "show --json: 채널 값 미출력" || ko "show --json 값 숨김" ""
out=$(DOC_KIT_CONFIG="$W/없는파일.toml" cfg show 2>/dev/null); echo "$out" | grep -qx 'channel=없음' && echo "$out" | grep -qx 'recipients=없음' && ok "show: 설정이 없으면 없음 표시" || ko "show 없음 표시" ""
[ -z "$(DOC_KIT_CONFIG="$W/없는파일.toml" cfg get channel 2>/dev/null)" ] && ok "설정 없음: channel 비어 있음(파일 경로만 보고)" || ko "설정 없음 channel" ""

[ "$(cfg get channel --config "$CFG" --project 시험A)" = "DM:시험채널값" ] && ok "프로젝트 지정: 그 섹션의 channel" || ko "프로젝트 지정" ""
cfg get recipients --config "$CFG" --project 시험A | grep -q '직접전달 안 함' && ok "recipients 의 직접전달 여부 읽음" || ko "recipients" ""
[ "$(cfg get out_dir --config "$CFG" --project 시험A)" = "docs/out" ] && [ "$(cfg get audience --config "$CFG" --project 시험A)" = "기획자" ] && ok "섹션 값이 default 를 덮고 나머지는 default 유지" || ko "default 덮어쓰기" ""
[ "$(cfg get channel --config "$CFG" --project 시험A --repo "$W")" = "DM:시험채널값" ] && ok "프로젝트명이 origin 보다 우선" || ko "우선순위" ""

# 우선순위: 지시 → 환경 변수 → origin → 폴더 이름 → default
[ "$(DOC_KIT_PROJECT=시험B cfg get channel --config "$CFG" --project 시험A)" = "DM:시험채널값" ] && ok "작업 지시(--project)가 환경 변수보다 우선" || ko "지시>환경" ""
[ "$(DOC_KIT_PROJECT=시험B cfg get channel --config "$CFG")" = "DM:다른채널값" ] && ok "환경 변수 DOC_KIT_PROJECT 로 지정" || ko "환경 변수" ""
git init -q "$W/r1" && git -C "$W/r1" remote add origin "git@example.com:org/시험저장소B.git"
[ "$(cfg get channel --config "$CFG" --repo "$W/r1")" = "DM:다른채널값" ] && ok "origin 주소가 match 와 맞으면 그 섹션" || ko "origin match" ""
[ "$(DOC_KIT_PROJECT=시험A cfg get channel --config "$CFG" --repo "$W/r1")" = "DM:시험채널값" ] && ok "환경 변수가 origin 보다 우선" || ko "환경>origin" ""
mkdir -p "$W/시험저장소A" && git init -q "$W/시험저장소A"
[ "$(cfg get channel --config "$CFG" --repo "$W/시험저장소A")" = "DM:시험채널값" ] && ok "원격이 없으면 폴더 이름으로 match" || ko "폴더 match" ""
mkdir -p "$W/무관" && git init -q "$W/무관"
[ -z "$(cfg get channel --config "$CFG" --repo "$W/무관")" ] && ok "아무것도 안 맞으면 default(channel 없음)" || ko "default 복귀" ""
out=$(cfg get channel --config "$CFG" --project 없는이름 2>"$W/err"); [ -z "$out" ] && grep -q '프로젝트 섹션 없음' "$W/err" && ok "없는 프로젝트명: default + 안내" || ko "없는 프로젝트명" ""
printf '[default\naudience = x\n' > "$W/bad.toml"
cfg show --config "$W/bad.toml" >/dev/null 2>&1; [ "$?" = 2 ] && ok "문법 오류 설정은 종료 코드 2" || ko "문법 오류" ""
cfg get channel --config "$CFG" --project 시험B --require >/dev/null 2>&1 && cfg get ticket_pattern --config "$CFG" --project 시험B --require >/dev/null 2>&1; [ "$?" = 3 ] && ok "--require: 빈 항목은 종료 코드 3" || ko "--require" ""

# 파이썬 3.9(내장 TOML 없음) 에서도 같은 결과
OLDPY=/usr/bin/python3
if [ -x "$OLDPY" ] && "$OLDPY" -c 'import sys; sys.exit(0 if sys.version_info < (3, 11) else 1)' 2>/dev/null; then
  a=$(python3 -I "$KIT/scripts/doc_config.py" show --config "$CFG" --project 시험A)
  b=$("$OLDPY" -I "$KIT/scripts/doc_config.py" show --config "$CFG" --project 시험A)
  [ -n "$a" ] && [ "$a" = "$b" ] && ok "파이썬 3.9(TOML 모듈 없음) 간이 읽기가 같은 결과" || ko "파이썬 3.9" "출력 다름"
  "$OLDPY" -I "$KIT/scripts/check.py" "$FIX/sample-ok.html" >/dev/null 2>&1 && ok "파이썬 3.9 로 check.py 통과" || ko "파이썬 3.9 check.py" ""
else
  printf 'SKIP  파이썬 3.9 — /usr/bin/python3 가 3.11 이상이거나 없음\n'
fi
python3 -I "$KIT/scripts/doc_config.py" show --config "$KIT/projects.example.toml" --project 예시A >/dev/null 2>&1 && ok "projects.example.toml 이 읽힘" || ko "예시 틀" "읽기 실패"
[ -z "$(cd "$REPO" && git ls-files | grep -E '(^|/)projects\.toml$')" ] && ok "저장소에 실제 값 파일(projects.toml) 없음" || ko "실제 값 파일" "추적됨"

# ---- check.py ---------------------------------------------------------------
chk "$FIX/sample-ok.html" >/dev/null 2>&1 && ok "check.py: 설정 없음, 샘플 통과" || ko "check 설정 없음" ""
DOC_KIT_CONFIG="$CFG" chk --project 시험A "$FIX/sample-ok.html" >/dev/null 2>&1 && ok "check.py: 설정 있음, 샘플 통과" || ko "check 설정 있음" ""
sed 's#규칙이 여러 곳에 있다\. 그래서#규칙이 TST-12 에 있다. 그래서#' "$FIX/sample-ok.html" > "$W/ticket.html"
cmp -s "$W/ticket.html" "$FIX/sample-ok.html" && ko "시험 문서 생성" "치환 안 됨"
out=$(DOC_KIT_CONFIG="$CFG" chk --project 시험A "$W/ticket.html" 2>&1); echo "$out" | grep -q 'TST-12' && ok "check.py: 설정의 ticket_pattern 으로 본문 티켓 키 검출" || ko "ticket_pattern 검출" ""
DOC_KIT_CONFIG="$CFG" chk "$W/ticket.html" >/dev/null 2>&1 && ok "check.py: 프로젝트 미지정이면 티켓 검사 생략" || ko "티켓 검사 생략" ""
sed 's#규칙이 여러 곳에 있다\. 그래서#규칙이 EffectManager 에 있다. 그래서#' "$FIX/sample-ok.html" > "$W/camel.html"
out=$(chk --audience 기획자 "$W/camel.html" 2>&1); echo "$out" | grep -q 'EffectManager' && ok "check.py: 기획자 독자면 본문 고유명사 검출" || ko "기획자 고유명사" ""
chk "$W/camel.html" >/dev/null 2>&1 && ok "check.py: 개발자 독자(기본)는 고유명사 허용" || ko "개발자 고유명사" ""
sed 's#규칙이 여러 곳에 있다\. 그래서#규칙이 20261109 에 있다. 그래서#' "$FIX/sample-ok.html" > "$W/digits.html"
cmp -s "$W/digits.html" "$FIX/sample-ok.html" && ko "시험 문서 생성(숫자)" "치환 안 됨"
chk --audience 기획자 "$W/digits.html" >/dev/null 2>&1 && ok "check.py: 기획자 독자라도 숫자만의 7자리(날짜 20261109)는 고유명사가 아님" || ko "숫자 오검출" "$(chk --audience 기획자 "$W/digits.html" 2>&1 | head -2 | tr '\n' ' ')"
sed 's#규칙이 여러 곳에 있다\. 그래서#규칙이 8763a17 에 있다. 그래서#' "$FIX/sample-ok.html" > "$W/hash.html"
out=$(chk --audience 기획자 "$W/hash.html" 2>&1); echo "$out" | grep -q '8763a17' && ok "check.py: 기획자 독자면 커밋 해시(8763a17)는 계속 검출" || ko "해시 검출" ""

# ---- check.py 도식 필수 ---------------------------------------------------------
SVG3='<svg width="40" height="20"><rect width="9" height="9"/><rect x="12" width="9" height="9"/><path d="M0 0L9 9"/></svg>'
SVG1='<svg width="9" height="9"><path d="M0 0L9 9"/></svg>'
IMG='<img alt="샷" src="data:image/png;base64,'$(printf 'A%.0s' $(seq 1 400))'">'
mk() { # mk 출력파일 종류 도식조각 [추가 meta]  — sample-ok 의 도식 2개를 바꿔 끼움
  python3 - "$FIX/sample-ok.html" "$1" "$2" "$3" "${4:-}" <<'PY'
import re, sys
src, out, kind, frag, extra = sys.argv[1:6]
s = open(src, encoding="utf-8").read()
s = re.sub(r"<svg.*?</svg>", "@@", s, flags=re.S)
parts = s.split("@@")
body = parts[0] + frag + "".join(p + (frag if i < 1 else "") for i, p in enumerate(parts[1:]))
meta = (f'<meta name="doc-kit-kind" content="{kind}">' if kind else "") + extra
open(out, "w", encoding="utf-8").write(body.replace("<title>", meta + "<title>", 1))
PY
}
mk "$W/d-ok.html" 구조안 "$SVG3" && chk "$W/d-ok.html" >/dev/null 2>&1 && ok "check.py 도식: 구조안 도식 2개 통과" || ko "구조안 도식 통과" ""
mk "$W/d-none.html" 구조안 ""; out=$(chk "$W/d-none.html" 2>&1); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -q '도식 0개' && echo "$out" | grep -q 'doc-kit-no-diagram' && ok "check.py 도식: 도식 0개는 종료 코드 1·원인과 조치 안내" || ko "도식 0개 실패" "rc=$rc"
mk "$W/d-icon.html" 구조안 "$SVG1"; chk "$W/d-icon.html" >/dev/null 2>&1; [ "$?" = 1 ] && ok "check.py 도식: 도형 1개짜리 아이콘 SVG 는 도식으로 안 셈" || ko "아이콘 SVG" ""
mk "$W/d-nokind.html" "" ""; chk "$W/d-nokind.html" >/dev/null 2>&1; [ "$?" = 1 ] && ok "check.py 도식: 종류 표식 없으면 설명 문서로 보아 도식 필요" || ko "표식 없음 보수 판정" ""
mk "$W/d-one.html" 구조안 "$SVG3"; python3 - "$W/d-one.html" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read(); s = re.sub(r"<svg.*?</svg>", "", s, count=1, flags=re.S); open(p, "w", encoding="utf-8").write(s)
PY
chk "$W/d-one.html" >/dev/null 2>&1; [ "$?" = 1 ] && ok "check.py 도식: 구조안에 도식 1개면 실패(2개 필요)" || ko "구조안 1개" ""
sed 's#content="구조안"#content="결정"#' "$W/d-one.html" > "$W/d-dec.html"
chk "$W/d-dec.html" >/dev/null 2>&1 && ok "check.py 도식: 결정 문서는 도식 1개로 통과" || ko "결정 1개" ""
js=$(chk --json "$W/d-ok.html" 2>&1); echo "$js" | grep -q '"diagrams": 2' && echo "$js" | grep -q '"kind": "구조안"' && ok "check.py --json: 종류·도식 수만 한 줄 출력(검사 문구 없음)" || ko "check.py --json" "$js"
js=$(chk --json "$W/d-icon.html" 2>&1); rc=$?; [ "$rc" = 0 ] && echo "$js" | grep -q '"diagrams": 0' && ok "check.py --json: 검사에 실패하는 문서도 종료 코드 0·도식 0 보고" || ko "check.py --json 도식 0" "rc=$rc $js"
mk "$W/d-img.html" 구조안 "$IMG"; chk "$W/d-img.html" >/dev/null 2>&1 && ok "check.py 도식: data URI 이미지도 도식으로 셈" || ko "data 이미지" ""
mk "$W/d-ext.html" 구조안 '<img src="https://example.com/a.png">'; chk "$W/d-ext.html" >/dev/null 2>&1; [ "$?" = 1 ] && ok "check.py 도식: 외부 URL 이미지는 도식 아님" || ko "외부 이미지" ""
mk "$W/d-ui.html" 비주얼 "$IMG"; out=$(chk "$W/d-ui.html" 2>&1); [ "$?" = 1 ] && echo "$out" | grep -q '전/후 이미지 쌍' && ok "check.py 도식: UI 문서는 전/후 쌍 없으면 실패" || ko "UI 쌍 없음" ""
mk "$W/d-ui2.html" 비주얼 '<img data-check="shot-before" alt="전" src="data:image/png;base64,'$(printf 'A%.0s' $(seq 1 400))'"><img data-check="shot-after" alt="후" src="data:image/png;base64,'$(printf 'A%.0s' $(seq 1 400))'">'
chk "$W/d-ui2.html" >/dev/null 2>&1 && ok "check.py 도식: UI 문서 전/후 쌍 통과" || ko "UI 쌍" ""
mk "$W/d-ex.html" 구조안 "" '<meta name="doc-kit-no-diagram" content="코드 없는 순수 합의 기록">'; out=$(chk "$W/d-ex.html" 2>&1)
[ $? = 0 ] && echo "$out" | grep -q '도식 예외 적용' && ok "check.py 도식: 예외 표식+사유는 통과하되 경고에 사유 출력" || ko "예외 표식" "$out"
mk "$W/d-ex0.html" 구조안 "" '<meta name="doc-kit-no-diagram" content="">'; out=$(chk "$W/d-ex0.html" 2>&1)
[ $? = 1 ] && echo "$out" | grep -q '사유 없음' && ok "check.py 도식: 사유 없는 예외 표식은 실패" || ko "사유 없음" "$out"
mk "$W/d-rep.html" "" ""; chk --report "$W/d-rep.html" >/dev/null 2>&1; [ "$?" = 1 ] && ok "check.py 도식: --report 도 도식 0개면 실패(예전엔 통과)" || ko "report 도식" ""
mk "$W/d-badkind.html" 모름 "$SVG3"; chk "$W/d-badkind.html" >/dev/null 2>&1; [ "$?" = 1 ] && ok "check.py 도식: 모르는 종류 표식은 실패" || ko "모르는 종류" ""

# ---- 고유값 0건 ---------------------------------------------------------------
out=$("$REPO/tests/doc-kit/check_unique.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "doc-kit 본문 고유값 0건" || ko "고유값" "$(echo "$out" | head -4 | tr '\n' ' ')"
cp -R "$KIT" "$W/kit-copy"
printf '정''오님께 전달\n' >> "$W/kit-copy/references/style.md"
out=$("$REPO/tests/doc-kit/check_unique.sh" "$W/kit-copy" 2>&1); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -q 'style.md:' && ! echo "$out" | grep -q '정''오' && ok "고유값 검출기: 심은 값을 파일:줄로만 지적(값 미출력)" || ko "검출기 자기 시험" "rc=$rc"

# ---- 전역 규칙: 쉬운 말 1개만, 나머지 양식 외 규칙은 전역에 없음 ------------------
G="$REPO/claude/CLAUDE.md"
n=$(awk '/^## 쉬운 말/{f=1;next} /^## /{f=0} f && /^- /{c++} END{print c+0}' "$G")
[ "$n" -ge 3 ] && [ "$n" -le 4 ] && ok "전역: 쉬운 말 규칙 3~4줄($n)" || ko "전역 쉬운 말" "줄 수 $n"
[ "$(grep -c '^## 쉬운 말' "$G")" = 1 ] && ok "전역: 쉬운 말 절 1개" || ko "전역 절 수" ""
leak=$(grep -nE '직접 ?전달|DM 으로|채널 ID|허브→메인|비주얼 변경 문서' "$G" | cut -d: -f1 | tr '\n' ' ')
[ -z "$leak" ] && ok "전역에 전달·허브 규칙 없음(doc-kit·허브 역할 파일 담당)" || ko "전역 누출" "줄 $leak"

# ---- 새 허브: 역할 파일·생성 스크립트·restart_hub ------------------------------
out=$("$REPO/tests/doc-kit/check_unique.sh" "$REPO/hubs/roles/role-doc-kit.txt" 2>&1) && ok "새 허브 역할 파일 고유값 0건" || ko "역할 파일 고유값" "$(echo "$out" | head -3 | tr '\n' ' ')"
grep -q 'doc_config.py' "$REPO/hubs/roles/role-doc-kit.txt" && grep -q '직접 보내지 않는다' "$REPO/hubs/roles/role-doc-kit.txt" && grep -q '\[문서→메인\] 📄' "$REPO/hubs/roles/role-doc-kit.txt" && ok "역할 파일: 설정 참조·직접 전달 금지·제목 형식 포함" || ko "역할 파일 내용" ""
DH="$W/dochub"; mkdir -p "$DH/ws"
before=$(find "$DH" | sort | tr '\n' ' ')
out=$(DOC_HUB_REPO="$DH/repo" DOC_HUB_WS_ROOT="$DH/ws" "$REPO/hubs/scripts/create_doc_hub.sh" --dry-run --project 시험A 2>&1); rc=$?
after=$(find "$DH" | sort | tr '\n' ' ')
[ "$rc" = 0 ] && [ "$before" = "$after" ] && echo "$out" | grep -q 'worktree add .* -b chore/docs-hub' && echo "$out" | grep -q '프로젝트: 시험A' && ok "create_doc_hub --dry-run: 명령만 출력·무변경·프로젝트 전달" || ko "create_doc_hub dry-run" "rc=$rc"
"$REPO/hubs/scripts/create_doc_hub.sh" --bad >/dev/null 2>&1; [ "$?" = 2 ] && ok "create_doc_hub: 알 수 없는 인자는 종료 코드 2" || ko "create_doc_hub 인자" ""

# restart_hub: 가짜 orca 로 프롬프트 확인
mkdir -p "$W/bin" "$W/hubroot/h1"; : > "$W/hubroot/h1/HANDOFF.md"
printf '#!/bin/sh\n[ "$2" = launch ] && { printf "%%s\\n" "$*" > "$ORCA_LOG"; echo "{\\"handle\\":\\"term_x\\"}"; }\nexit 0\n' > "$W/bin/orca"
chmod +x "$W/bin/orca"
rh() { ORCA_LOG="$W/orca.log" PATH="$W/bin:$PATH" HUB_ROOT="$W/hubroot" "$REPO/hubs/scripts/restart_hub.sh" term_old h1 docs "$@" >/dev/null 2>&1; cat "$W/orca.log"; }
rh_out=$(rh); echo "$rh_out" | grep -q 'common-rules.txt' && echo "$rh_out" | grep -q 'role-docs.txt' && ok "restart_hub 기본값: 기존 역할 파일·공통 규칙 경로 유지" || ko "restart_hub 기본값" "$rh_out"
rh_out=$(ROLE_FILE="$W/custom-role.txt" COMMON_FILE= rh); echo "$rh_out" | grep -q 'custom-role.txt' && ! echo "$rh_out" | grep -q 'common-rules' && ok "restart_hub: ROLE_FILE 지정·COMMON_FILE 비우면 공통 규칙 생략" || ko "restart_hub 새 허브" "$rh_out"

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
