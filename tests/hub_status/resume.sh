#!/usr/bin/env bash
# resume_all.sh --dry-run·hubs.tsv 시험. 가짜 orca(PATH 앞)·임시 상태 폴더만 씀. 허브를 실제로 띄우지 않음.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
# ---- resume_all.sh --dry-run -----------------------------------------------------------------
# 허브 목록 전부(주석·빈 줄 제외)가 출력에 나오는지
SLUGS=(); while IFS= read -r l; do SLUGS+=("$l"); done < <(grep -v '^[[:space:]]*#' "$TSV" | grep -v '^[[:space:]]*$' | cut -f1)
chk "hubs.tsv 허브 10개 이상, slug 중복 없음" '[ "${#SLUGS[@]}" -ge 10 ] && [ -z "$(printf "%s\n" "${SLUGS[@]}" | sort | uniq -d)" ]'
chk "hubs.tsv 필드 5칸(하위 허브만 6칸)" '! grep -v "^[[:space:]]*#" "$TSV" | grep -v "^[[:space:]]*$" | awk -F"\t" "NF!=5 && NF!=6" | grep -q .'
chk "hubs.tsv 부모 칸은 부모 없는 허브만 가리킴(한 층)" '[ -z "$(grep -v "^[[:space:]]*#" "$TSV" | grep -v "^[[:space:]]*$" | awk -F"\t" "{p[\$1]=\$6} END{for(s in p) if(p[s]!=\"\"&&p[s]!=\"-\"&&(!(p[s] in p)||(p[p[s]]!=\"\"&&p[p[s]]!=\"-\"))) print s}")" ]'
# 부모 칸: 하위 허브는 부모 뒤에 띄움(표 순서와 무관)
printf 'kid\t하위\t~/r/role-k.txt\t-\t-\tpar\npar\t부모\t~/r/role-p.txt\t-\t-\n' > "$SB/hubs6.tsv"
OUT6=$(HUBS_TSV="$SB/hubs6.tsv" bash "$RA" --dry-run 2>&1)
chk "재기동: 부모 칸 있는 허브는 부모 다음, 6칸이 repo 에 섞이지 않음" '[ "$(printf "%s\n" "$OUT6" | grep "^== " | tr "\n" "|")" = "== 부모|== 하위|" ] && ! printf "%s" "$OUT6" | grep -q "REPO=.*par"'
chk "hubs.tsv 역할 파일이 ~/ 경로이거나 저장소 hubs/roles 와 짝" 'ROLES_OK=1; for r in $(grep -v "^[[:space:]]*#" "$TSV" | grep -v "^[[:space:]]*$" | cut -f3); do case "$r" in "~/.config/lrm-hubs/roles/"*) b=$(basename "$r"); [ -e "$REPO/hubs/roles/$b" ] || [ -e "$REPO/hubs/roles/$b.tmpl" ] || ROLES_OK=0 ;; *) ROLES_OK=0 ;; esac; done; [ "$ROLES_OK" = 1 ]'

hs write --slug ui-layer --run run_ui1 --task "U11 후속 조사" --next "U12 확정" >/dev/null
mkfile data-design "$OLDT"
rm -f "$SB/status/scrum.json"
OUT=$(bash "$RA" --dry-run 2>&1); RC=$?
chk "dry-run 종료 코드 0" '[ "$RC" = 0 ]' "rc=$RC"
MISSING=""; for s in "${SLUGS[@]}"; do printf '%s' "$OUT" | grep -q -- "--slug $s " || MISSING="$MISSING $s"; done
chk "dry-run 출력에 허브 전부(slug 별 create_hub 명령)" '[ -z "$MISSING" ]' "빠진 허브:$MISSING"
chk "dry-run: 상태 있는 허브는 상태 문구" 'printf "%s" "$OUT" | grep -q "Run: run_ui1"'
chk "dry-run: 24시간 초과 허브는 오래된 상태 표시" 'printf "%s" "$OUT" | grep -q "오래된 상태($OLDT)"'
chk "dry-run: 상태 파일 없는 허브는 일반 문구" 'printf "%s" "$OUT" | grep -q "상태 파일 없음 — HANDOFF.md 와 run-list 로 이어받기"'
chk "dry-run: 옛 하드코딩 문구 없음" '! printf "%s" "$OUT" | grep -q "run_a186706b4d4b\|run_ed7531cdf2bf"'
chk "dry-run: 가짜 터미널 목록 외 실제 기동 없음(create_hub 미실행)" '! printf "%s" "$OUT" | grep -q "hub=.*terminal=term_"'
chk "resume_all.sh 에 하드코딩 Run ID 없음" '! grep -q "run_[0-9a-f]\{12\}" "$RA"'

# 특수 문자 값이 dry-run 명령 줄에서 인용되는지: 출력한 명령을 bash 로 다시 해석해도 값이 보존돼야 함
hs write --slug docs --task "$NASTY" >/dev/null
OUT2=$(bash "$RA" --dry-run --only docs 2>&1)
CMDLINE=$(printf '%s\n' "$OUT2" | grep '^\$ ' | sed 's/^\$ //')
chk "dry-run 명령이 bash 재해석 시 인자 4개 값 보존(--resume 에 원문)" 'python3 - "$SB" "$CMDLINE" "$NASTY" <<PY
import subprocess,sys,json
sb,cmd,nasty=sys.argv[1:4]
# 출력된 명령의 bash 를 echo 로 바꿔 인자 목록만 JSON 으로 받음
probe=cmd.replace("bash ","python3 -c \"import json,sys;print(json.dumps(sys.argv[1:]))\" ",1)
r=subprocess.run(["bash","-c",probe],capture_output=True,text=True)
args=json.loads(r.stdout.strip().splitlines()[-1])
i=args.index("--resume"); assert nasty.split("\n")[0].replace("\n"," ") in args[i+1].replace("\n"," "), args
assert "둘째 줄" in args[i+1]
assert not __import__("os").path.exists(sb+"/PWNED")
PY'
chk "--only 는 지정 허브만" '! printf "%s" "$OUT2" | grep -q -- "--slug ui-layer "'
chk "--only 없는 slug: 종료 코드 2 + 조치" 'bash "$RA" --dry-run --only zzz 2>"$SB/e" >/dev/null; [ $? = 2 ] && grep -q "hubs.tsv" "$SB/e"'

# 살아 있는 허브 건너뛰기
printf '{"result":{"terminals":[%s,%s]}}' "$(tm /x/coord-vfx-hub claude true true false 5)" "$(tm /x/coord-test-qa-hub codex true true false 5)" > "$SB/terms-live.json"
OUT3=$(FAKE_TERMINALS="$SB/terms-live.json" bash "$RA" --dry-run 2>&1)
chk "살아 있는 허브(claude)는 건너뜀" 'printf "%s" "$OUT3" | grep -q "== 연출·VFX 폴리싱: 살아 있음, 건너뜀" && ! printf "%s" "$OUT3" | grep -q -- "--slug vfx "'
chk "codex 터미널은 살아 있는 허브로 안 침" 'printf "%s" "$OUT3" | grep -q -- "--slug test-qa "'
# 생존 판정: connected=true 만 살아 있음. orphaned 는 판정에 쓰지 않음
printf '{"result":{"terminals":[%s,%s,%s]}}' "$(tm /x/coord-vfx-hub claude false true false 5)" "$(tm /x/coord-test-qa-hub claude true true true 5)" '{"worktreePath":"/x/coord-scrum-hub","agentIdentity":"claude","orphaned":false}' > "$SB/terms-mixed.json"
OUT4=$(FAKE_TERMINALS="$SB/terms-mixed.json" bash "$RA" --dry-run 2>&1)
chk "connected=false claude 는 죽은 허브로 보고 재기동 대상" 'printf "%s" "$OUT4" | grep -q -- "--slug vfx "'
chk "connected=true·orphaned=true 는 고아라 살아 있지 않음(재기동 대상)" 'printf "%s" "$OUT4" | grep -q -- "--slug test-qa "'
chk "connected 필드 없음은 살아 있음으로 단정하지 않음" 'printf "%s" "$OUT4" | grep -q -- "--slug scrum "'

# 생존 판정 세부: 쓰기 불가·마지막 출력 오래됨은 죽은 허브, 워커 터미널은 코디네이터가 아님
printf '{"result":{"terminals":[%s,%s,%s]}}' "$(tm /x/coord-vfx-hub claude true false false 5)" "$(tm /x/coord-test-qa-hub claude true true false 999)" "$(tm /x/coord-scrum-hub claude true true false 5 term_wk1)" > "$SB/terms-stale.json"
printf '{"result":{"workers":[{"dispatchId":"d1","terminal":"term_wk1","liveness":"live","terminalState":"active"}]}}' > "$SB/workers.json"
OUTS=$(FAKE_TERMINALS="$SB/terms-stale.json" FAKE_WORKERS="$SB/workers.json" bash "$RA" --dry-run 2>&1)
chk "쓰기 불가(writable=false) claude 는 죽은 허브" 'printf "%s" "$OUTS" | grep -q -- "--slug vfx "'
chk "마지막 출력이 기준(HUB_ALIVE_MIN) 초과면 죽은 허브" 'printf "%s" "$OUTS" | grep -q -- "--slug test-qa "'
chk "워커 터미널(worker-list 의 Dispatch)은 허브 코디네이터로 안 침" 'printf "%s" "$OUTS" | grep -q -- "--slug scrum "'
OUTM=$(HUB_ALIVE_MIN=2000 FAKE_TERMINALS="$SB/terms-stale.json" bash "$RA" --dry-run --only test-qa 2>&1)
chk "HUB_ALIVE_MIN 상향 시 오래된 출력도 살아 있음" 'printf "%s" "$OUTM" | grep -q "살아 있음, 건너뜀"'

# 중복 경고: 같은 허브에 코디네이터 여럿이면 경고 1줄 + 정리 제안(닫지 않음). 워커는 세지 않음
printf '{"result":{"terminals":[%s,%s,%s,%s]}}' "$(tm /x/coord-vfx-hub claude true true false 5 term_a1)" "$(tm /x/coord-vfx-hub claude true true false 1 term_a2)" "$(tm /x/coord-vfx-hub claude true true false 1 term_wk1)" "$(tm /x/coord-scrum-hub claude true true false 1 term_s1)" > "$SB/terms-dup.json"
OUTD=$(FAKE_TERMINALS="$SB/terms-dup.json" FAKE_WORKERS="$SB/workers.json" bash "$RA" --dry-run 2>&1)
chk "중복 경고 1줄: 코디네이터 2개(워커 제외)·유지 후보는 최근 출력" '[ "$(printf "%s" "$OUTD" | grep -c "^경고: .*코디네이터 2개")" = 1 ] && printf "%s" "$OUTD" | grep "^경고: " | grep -q -- "--slug vfx --keep term_a2 --dry-run"'
chk "단독 허브(scrum)는 중복 경고 없음" '! printf "%s" "$OUTD" | grep "^경고: " | grep -q -- "--slug scrum"'
chk "중복 경고는 닫기 명령을 실행하지 않음(orca close 미호출)" '! grep -q "terminal close" "$SB/orca.log" 2>/dev/null'

# 조회 실패: 허브 없음과 구분, 명령 출력 없이 비정상 종료
OUT5=$(FAKE_ORCA_FAIL=1 bash "$RA" --dry-run 2>"$SB/e5"); RC5=$?
chk "terminal list 실패: 종료 코드 1·기동 명령 없음" '[ "$RC5" = 1 ] && ! printf "%s" "$OUT5" | grep -q -- "--slug " && grep -q "orca terminal list 조회 실패" "$SB/e5"'
printf '<html>not json' > "$SB/terms-bad.json"
OUT6=$(FAKE_TERMINALS="$SB/terms-bad.json" bash "$RA" --dry-run 2>"$SB/e6"); RC6=$?
chk "응답이 JSON 이 아니면 같은 처리" '[ "$RC6" = 1 ] && ! printf "%s" "$OUT6" | grep -q -- "--slug " && grep -q "조회 실패" "$SB/e6"'
chk "조회 성공·터미널 0개는 정상(허브 없음)" 'bash "$RA" --dry-run >/dev/null 2>&1; [ $? = 0 ]'

# --only 는 정규식이 아닌 정확한 문자열 비교
chk "--only 정규식 문자 거부(u.-layer)" 'bash "$RA" --dry-run --only "u.-layer" >/dev/null 2>"$SB/e7"; [ $? = 2 ] && grep -q "hubs.tsv" "$SB/e7"'
chk "--only 부분 문자열 거부(ui)" 'bash "$RA" --dry-run --only ui >/dev/null 2>&1; [ $? = 2 ]'
# 응답 형태 손상(종료 코드 0 이지만 목록이 아님): 조회 실패로 보고 기동 명령 없이 rc=1
for bad in '{"result":{"terminals":null}}' '{"result":{"terminals":[null]}}' '{"result":{"terminals":[{"worktreePath":null}]}}' '{"result":{"terminals":[{"worktreePath":"/x/a","connected":"yes"}]}}' '{"result":{"terminals":{}}}'; do
  printf '%s' "$bad" > "$SB/terms-shape.json"
  OUTB=$(FAKE_TERMINALS="$SB/terms-shape.json" bash "$RA" --dry-run --only docs 2>"$SB/eb"); RCB=$?
  chk "응답 형태 손상: rc=1·기동 명령 없음·진단: $bad" '[ "$RCB" = 1 ] && ! printf "%s" "$OUTB" | grep -q -- "--slug " && grep -q "조회 실패" "$SB/eb"'
done
printf '%s' '{"result":{"terminals":[{"worktreePath":"/x/n","agentIdentity":null}]}}' > "$SB/terms-ok.json"
OUTK=$(FAKE_TERMINALS="$SB/terms-ok.json" bash "$RA" --dry-run --only docs 2>&1); RCK=$?
chk "agentIdentity null·connected 없음 항목은 정상 응답(오탐 없음)" '[ "$RCK" = 0 ] && printf "%s" "$OUTK" | grep -q -- "--slug docs "'

chk "--help" 'bash "$RA" --help | grep -q -- "--dry-run" && bash "$RA" --help | grep -q "종료 코드"'
chk "알 수 없는 인자: 종료 코드 2" 'bash "$RA" --bogus >/dev/null 2>&1; [ $? = 2 ]'

printf '\n%s\n' "결과: PASS $PASS / FAIL $FAIL"
[ "$FAIL" = 0 ]
