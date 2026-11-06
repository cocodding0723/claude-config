#!/usr/bin/env bash
# Orca 재시작 뒤 허브 코디네이터 전체 재기동(--resume). 사용: bash ~/.config/lrm-hubs/resume_all.sh
set -uo pipefail
K="$HOME/.claude/skills/orca-hub-create/scripts/create_hub.sh"; R="$HOME/.config/lrm-hubs/roles"
alive(){ orca terminal list --json 2>/dev/null | python3 -c "import json,sys;ts=json.load(sys.stdin)['result']['terminals'];sys.exit(0 if any(t.get('worktreePath','').endswith('/coord-$1-hub') and t.get('agentIdentity')=='claude' for t in ts) else 1)"; }
run(){ if alive "$1"; then echo "== $2: 살아 있음, 건너뜀"; return; fi; echo "== $2"; bash "$K" --slug "$1" --name "$2" --role-file "$R/$3" --resume "$4" 2>&1 | tail -1; }
run ui-layer  "UI 계층 통합 설계"            role-ui.txt       "Run run_a186706b4d4b, 하위 docs-LRM-260·264·266·LRM-260-IView 조사 워크스페이스. U11=IView<TState> 확정, U11 후속(a)(b)(c) 조사 ctx_175d3d6cc82a, LRM-266 재시작 ctx_a9fd4e5b74f2, U12~U14·O1~O10 추천대로 확정"
run test-qa   "테스트·QA"                    role-test.txt     "Run run_ed7531cdf2bf, 하위 sprint-20261026-docs-LRM-262-test-speed"
run arch-audit "아키텍처 점검"               role-arch.txt     "Run run_1354029b3b0a(LRM-265)"
run scrum     "개인 비서"                    role-scrum.txt    "Run run_f5830ac29db2(스크럼 초안), 하네스 리서치 요청 진행 중"
run tutorial  "튜토리얼·가이드"              role-tutorial.txt "Run run_86b6fc02cc7b, 워커 ctx_dec28c569f38(docs/tutorial-stage-guide-check.md), 시트로 튜토리얼 가이드 관리 설계 요청"
run docs      "문서"                         role-docs.txt     "1차 종합 HTML 보고서 + 문서 스킬 웹 리서치 진행 중"
run data-design "게임 데이터·시나리오 구조 설계" role-data.txt  "Run run_d73204039681, 하위 docs-LRM-261-data-condition-design·docs/LRM-261-v3 브랜치들"
run vfx       "연출·VFX 폴리싱"              role-vfx.txt      "Run run_1ba741a03286, 하위 sprint-20261026-feat-LRM-259-tension-up-polish"
echo "확인: Codex 실행 옵션에 workspace-write 가 없어야 함 → ps -axww -o command | grep '^codex ' | grep -c workspace-write"
