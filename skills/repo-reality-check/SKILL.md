---
name: repo-reality-check
description: CLAUDE.md·검증 하네스 문서·허브 공통 규칙이 실제 파일·명령·설정과 맞는지 점검해 HTML 보고서로 낸다. 없는 경로·명령, 낡은 표, 서로 어긋난 규칙을 찾는다. "문서가 현실과 맞는지", "CLAUDE.md 점검", "규칙 낡았는지 봐줘" 요청에 사용한다. 수정은 승인 뒤에만 한다.
---

# repo-reality-check

에이전트는 문서를 믿고 움직인다. 문서가 낡으면 잘못된 경로를 실행하고 그 결과를 믿는다. 이 스킬은 **문서의 주장 하나하나를 실제와 대조**한다.

## 대상

- 저장소 `CLAUDE.md`, `docs/ANDROID-PC-COMPATIBILITY.md`, `docs/RELEASE-CHECKLIST.md` 등 CLAUDE.md 가 불러오는 문서
- 검증 하네스: `Tools/verify/*`(verify.sh·stop-gate.sh·summarize-nunit.mjs), `.claude/settings*.json` 훅
- 허브 공통 규칙: `~/.claude/meat-proxy/WORKFLOW.md`, `~/.config/lrm-hubs/*`, 전역 `~/.claude/CLAUDE.md`

## 점검 항목

| 종류 | 확인 방법 |
|---|---|
| 경로 | 문서에 나온 파일·폴더가 존재하는가 (`test -e`) |
| 명령·플래그 | 스크립트가 그 플래그를 실제로 처리하는가 (스크립트 본문 grep, 실행하지 않음) |
| 짝 상수 | 문서가 "같이 고쳐야 한다"는 값이 실제로 같은가 (예: `ForcedWidth`/`EXPECTED_W` 1080×1920) |
| 표·수치 | 엔드포인트·버전·Unity 버전·브랜치 규칙 표가 설정 파일과 일치하는가 |
| 규칙 충돌 | 전역·저장소·허브 규칙이 서로 반대인가 (예: 커밋 본문 허용 여부, push 허용 범위) |
| 낡은 기록 | 삭제된 모듈·이전 실험(REST 비교 등)을 현재 기준처럼 쓰는가 |

## 절차

1. 문서에서 검증 가능한 주장(경로·명령·값·규칙)을 뽑아 목록으로 만든다.
2. 주장마다 읽기 전용 확인(`test -e`·grep·설정 읽기)을 하고 `명령 — 결과`를 남긴다. 스크립트·빌드·설치는 실행하지 않는다.
3. 판정: 맞음 / 틀림 / 낡음 / 확인 불가(이유).
4. **HTML 보고서**(자체 완결)로 낸다: 요약 3줄 → 틀림·낡음 표(문서 위치, 주장, 실제, 근거 명령, 고칠 안) → 규칙 충돌 → 확인 불가 목록. 고칠 안이 여럿이면 선택지+추천.
5. 수정은 사용자 승인 뒤, 문서별로 나눠 한다.

## 하지 않는 것

- 점수를 매기지 않는다. 고칠 목록이 결과다.
- 보고서 작성 중 문서를 고치지 않는다.

## 참고

- 개념 출처: cursor/plugins `agent-compatibility/skills/check-agent-compatibility` (라이선스 없음, 문구 미사용)
