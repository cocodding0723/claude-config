#!/usr/bin/env bash
# doc-kit 샘플 문서 전달 시험(계획서 «이전 전 시험» 검증 1~4). 임시 폴더에서만 실행하며 실제 ~/.config 는 읽지 않음.
# LLM 을 돌리지 않으므로 «보고 메시지 문구 확인» 까지만 본다. 규칙(제목 형식·직접전달 금지·기본값 문구)은 스킬·역할 파일에서 읽고,
# 값(채널·받는 사람·저장 위치)은 설정에서 읽어 전달 보고를 조립한 뒤 문구를 대조함.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd -P)
KIT="$REPO/skills/doc-kit"
FIX="$REPO/tests/doc-kit/fixtures"
CFG="$FIX/projects.test.toml"
ROLE="$REPO/hubs/roles/role-doc-kit.txt"
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
ko() { FAIL=$((FAIL + 1)); printf 'FAIL  %s — %s\n' "$1" "${2:-}"; }

W=$(mktemp -d "${TMPDIR:-/tmp}/cc-dockit-sample.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT
unset DOC_KIT_PROJECT DOC_KIT_CONFIG
export HOME="$W/home"; mkdir -p "$HOME"

cfg() { python3 -I "$KIT/scripts/doc_config.py" "$@"; }
chk() { python3 -I "$KIT/scripts/check.py" "$@"; }

# 전달 보고 조립. 인자: 프로젝트명(빈 값=미지정) 설정경로(빈 값=없음) 유형 주제. 새 허브처럼 origin 이 어느 match 에도 안 맞는 저장소 기준.
# 제목 형식은 역할 파일의 «제목 형식» 줄에서 읽음
report() {
  local project="$1" config="$2" type="$3" topic="$4" fmt title opt=() rec channel outdir rule path line
  [ -n "$project" ] && opt+=(--project "$project")
  [ -n "$config" ] && opt+=(--config "$config")
  opt+=(--repo "$REPO")
  fmt=$(sed -n 's/^제목 형식(허브→메인): "\(.*\)"\..*$/\1/p' "$ROLE" | head -1)
  title=${fmt//<유형>/$type}; title=${title//<주제>/$topic}
  channel=$(cfg get channel "${opt[@]}" 2>/dev/null)
  outdir=$(cfg get out_dir "${opt[@]}" 2>/dev/null); rule=$(cfg get name_rule "${opt[@]}" 2>/dev/null)
  rule=${rule//\{날짜\}/2026-11-06}; rule=${rule//\{주제\}/$topic}
  path="$outdir/$rule/$rule.html"
  echo "제목: $title"
  echo "파일: $path"
  if [ -z "$channel" ]; then
    echo "채널: 없음 — 파일 경로만 보고"
    echo "설정 없음 — 기본값"
  else
    echo "채널: $channel (메인이 업로드)"
  fi
  rec=$(cfg get recipients "${opt[@]}" 2>/dev/null)
  if [ -z "$rec" ]; then
    echo "직접 전달: 없음(사용자 본인에게만)"
  else
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      if [ "${line##*|}" = "직접전달 안 함" ]; then echo "직접 전달: 안 함 — ${line%%|*} 에게는 사용자를 거쳐서만"
      else echo "직접 전달: 허용 — ${line%%|*}"; fi
    done <<< "$rec"
  fi
}

# ---- 검증 1: 샘플 문서가 check.py 를 통과 -----------------------------------------
chk "$FIX/sample-ok.html" >/dev/null 2>&1 && ok "검증1: 샘플 문서 check.py 통과" || ko "검증1 샘플" "check.py 실패"
out=$(chk "$KIT/templates/doc.html" 2>&1); rc=$?
[ "$rc" = 1 ] && echo "$out" | grep -q '자리표시' && ok "검증1: 채우지 않은 템플릿은 자리표시로 실패" || ko "검증1 템플릿" "rc=$rc"

# ---- 검증 2: 프로젝트명을 적으면 그 섹션의 채널을 읽음 ------------------------------
out=$(report 시험A "$CFG" 구조안 샘플)
echo "$out" | grep -qx '채널: DM:시험채널값 (메인이 업로드)' && ! echo "$out" | grep -q '설정 없음' && ok "검증2: 프로젝트 지정 → 그 섹션 채널·업로드는 메인" || ko "검증2 채널" "$(echo "$out" | sed -n 3p)"
echo "$out" | grep -qx '파일: docs/out/2026-11-06-샘플/2026-11-06-샘플.html' && ok "검증2: out_dir·name_rule 로 저장 경로 조립(폴더 안 <이름>.html)" || ko "검증2 경로" "$(echo "$out" | sed -n 2p)"
out=$(report 시험B "$CFG" 구조안 샘플)
echo "$out" | grep -qx '채널: DM:다른채널값 (메인이 업로드)' && ok "검증2: 다른 프로젝트명이면 다른 섹션 채널" || ko "검증2 다른 프로젝트" ""

# ---- 검증 3: 프로젝트 미지정이면 기본값, 파일 경로만 보고 ----------------------------
for case in "설정 파일 있음|$CFG" "설정 파일 없음|$W/없는파일.toml"; do
  label=${case%%|*}; conf=${case#*|}
  out=$(report "" "$conf" 구조안 샘플)
  echo "$out" | grep -qx '채널: 없음 — 파일 경로만 보고' && echo "$out" | grep -qx '설정 없음 — 기본값' && echo "$out" | grep -qx '파일: docs/out/2026-11-06-샘플/2026-11-06-샘플.html' && echo "$out" | grep -qx '직접 전달: 없음(사용자 본인에게만)' \
    && ok "검증3: 프로젝트 미지정($label) → 기본값·채널 없음·받는 사람 없음" || ko "검증3 $label" "$(echo "$out" | tr '\n' '/')"
done

# ---- 검증 4: 제목 형식과 직접 전달 금지 -----------------------------------------------
out=$(report 시험A "$CFG" 보고서 샘플주제)
[ "$(echo "$out" | sed -n 1p)" = '제목: [문서→메인] 📄 보고서: 샘플주제' ] && ok "검증4: 제목이 «[문서→메인] 📄 <유형>: <주제>» 형식" || ko "검증4 제목" "$(echo "$out" | sed -n 1p)"
echo "$out" | grep -qx '직접 전달: 안 함 — 시험수신자 에게는 사용자를 거쳐서만' && ok "검증4: 직접전달 안 함 받는 사람은 사용자 경유" || ko "검증4 직접 전달" ""
# 직접전달이 허용된 받는 사람이면 허용으로 표시되는지(조건 분기 자체 확인)
printf '[project."시험C"]\nrecipients = ["시험수신자C|검토|직접전달 허용"]\n' > "$W/allow.toml"
report 시험C "$W/allow.toml" 구조안 샘플 | grep -qx '직접 전달: 허용 — 시험수신자C' && ok "검증4: 허용 표기가 있는 받는 사람만 직접 전달" || ko "검증4 허용 분기" ""
# 규칙 문구: 스킬·역할 파일이 같은 제목 형식과 금지 문구를 갖고 있음
skill_fmt=$(sed -n 's/.*메인에 보내는 제목은 `\(.*\)` 이다.*/\1/p' "$KIT/SKILL.md" | head -1)
role_fmt=$(sed -n 's/^제목 형식(허브→메인): "\(.*\)"\..*$/\1/p' "$ROLE" | head -1)
[ -n "$role_fmt" ] && [ "${skill_fmt/허브명/문서}" = "$role_fmt" ] && ok "검증4: SKILL.md 와 역할 파일의 제목 형식이 같음(허브명=문서)" || ko "검증4 제목 형식 일치" "skill=[$skill_fmt] role=[$role_fmt]"
grep -q '직접전달 안 함' "$KIT/SKILL.md" && grep -q '직접 보내지 않는다' "$ROLE" && grep -q '메인 코디네이터에 넘긴다' "$ROLE" && ok "검증4: 직접 전달 금지·메인에 업로드 위임 문구가 스킬·역할 파일에 있음" || ko "검증4 금지 문구" ""
# 한계(계획서): 메인이 없는 시험 터미널이므로 업로드 위임은 문구 확인까지만 함

printf '\n결과: PASS %s, FAIL %s\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
