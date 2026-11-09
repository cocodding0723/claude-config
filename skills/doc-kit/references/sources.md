# 선례 출처

새 문서를 쓰기 전에 같은 유형의 선례를 **최소 1개 열어 본다.** 아래는 2026-10-08 분석 기준. 이후 machamy 문서·PR 이 늘면 같은 방법으로 갱신한다.

## 읽는 법

```bash
# Tech Records — 워킹트리가 낡았을 수 있어 origin/dev 에서 읽는다
git -C ~/Documents/project/JobMaker fetch -q origin
git -C ~/Documents/project/JobMaker log origin/dev --author=machamy --name-only --pretty=format: -- 'docs/**' | sort | uniq -c | sort -rn | head -40
git -C ~/Documents/project/JobMaker show "origin/dev:docs/Tech Records/<파일>.md"

# PR 본문
gh search prs --author machamy --limit 100 --json repository,number,title
gh pr view <번호> -R Basakansoft/JobMaker --json title,body
```

JobMaker 는 **읽기 전용**이다. 엔드포인트·설정값을 다른 저장소로 옮기지 않는다.

## 유형별 대표 선례

| 유형 | 문서 / PR |
|---|---|
| 변경 해설 | 「안티치트 Track B 애셋 무결성」(머리말·빈칸 문장·실패 분기 표·한계 별점), 「속보 로테이션 A·B·C 와 이번 주 속보 UI」(우선순위·부재/빈 값), 「이벤트 프레임워크」(문서망·실전 장), 「GameComplete/변경 해설·이해도 퀴즈」(독자별 읽는 순서·커밋↔절 표) |
| 설계 기록 | 「도전 독서왕 서버 이관 (설계 기록)」 |
| 계획 | cross-build 입력용 「독서왕 회차 핀 통합·리롤 환급 계획」 / 장기 「GameComplete/계획」(검토 이력 v11→v25) |
| 계약 | 「유저 통계 팝업 서버 계약」, 「신규 속보 10종 — 저작 인터페이스 계약」, 「스코프 변수 토큰 — 문법 계약」 |
| 규약 | 「래퍼 ID 타입 규약」 (「RemoteConfig 스위치 규약과 개최 게이트」는 규약 내용을 담은 변경 해설) |
| 폴더형 | 질문 단위 「인게임 효과 중앙화/00 개요~05」, 「효과 정의 V2 단일화/00~04」 / 역할 단위 「GameComplete/」 |
| 런북·지시서 | 「GameComplete/CS 런북」, 「백서린 목표 설정 스토리 제너레이터 반영 지시서」 |
| 데이터 명세 | `docs/Json Specs/breaking_news.json.md`, `효과 정의.md`, `콘텐츠 태그.md` (`career.json.md` 는 다른 작성자) |
| PR — 10월 최신형 | JobMaker #2620, #2623, #2629, #2616 (유저·QA 가 보는 변화 표·원인·왜 이 방식인가·게이트) |
| PR — 9월 5절형 | #2436, #2487, #2552, #2559, #2560 (요약/변경 목록/검증/리뷰 포인트/범위 밖) |
| PR — fix | #2616 (원인 아닌 경로 배제·QA 재현 단계), #2549 (버전별 크래시 표), #2559 (모르는 것 인정) |
| PR — refactor | #2615 (다른 브랜치·운영 주의), #2438 (기각한 것 표) |
| PR — 서버 | job-maker-function #16 (머지 뒤 실행할 명령), #11 (선행 PR·새 함수 표) |
| PR — docs/chore | #2625 (기준값 실측), #2487 |

## 정제 코퍼스

2026-10-08 정제본: 문서 52편(machamy 단독 45·주 작성 7, 변경 해설 26)·PR 25건. 타인 문서 2편은 대조군. 작성자 판정은 `git log origin/dev --format='%an' -- <path>`. 수치(문체·퀴즈·장치 빈도)는 `style.md`에 반영했다.

## 대조군 — machamy 가 아닌 문서

「효과 파이프라인 레거시 제거 기록」, 「저속망 부팅 크래시와 Functions TLS 병목」은 다른 작성자다. 판정 기준·제거 목록을 쌓는 대장 형식이 필요할 때만 참고한다.

## Slack

machamy 의 Slack 글은 아직 분석하지 못했다(2026-10-08, 세션에 Slack 커넥터 없음). 연결되면 공지·스레드 요약의 형식을 여기 추가하고 `style.md`에 반영한다.
