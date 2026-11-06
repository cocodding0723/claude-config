---
name: web-tool-design
description: 게임 데이터 웹 편집 도구처럼 내부용 관리 화면(시트 행 보기·검증·편집 표)을 만들 때 쓰는 규칙. Node 내장 모듈 + 순수 JS 의 자체 완결 페이지를 전제한다. frontend-design 스킬을 기본 과정으로 함께 적용하고, 이 스킬은 관리 도구에 맞게 범위를 좁힌다.
---

# 웹 관리 도구 디자인

## 적용 순서

1. `frontend-design` 의 과정(토큰 계획 → 브리프 대조 → 구현 → 자기 검토)을 그대로 따른다.
2. 관리 도구에서는 "대담함"을 장식이 아니라 **읽기 속도와 오류 발견**에 쓴다. 아래 규칙이 frontend-design 과 부딪히면 이 문서가 우선한다.
3. 구현 뒤 브라우저로 직접 열어 라이트·다크 두 화면을 확인한다.

## 관리 화면 원칙

- 산만함을 최소로 한다. 장식 그라데이션·그림자·진입 애니메이션을 넣지 않는다.
- 정보 밀도는 높이되 정렬과 여백으로 묶는다. 카드로 잘게 쪼개지 않는다.
- 화면마다 주요 버튼은 1개(예: "저장")만 강조색을 쓴다. 나머지는 보조 스타일이다.
- 헤더는 최소로 둔다: 도구 이름, 현재 시트·탭, 저장 상태 정도.
- 키보드 우선: 모든 기능을 마우스 없이 쓸 수 있어야 한다. 단축키(저장 `Ctrl/Cmd+S`, 검색 `/`, 취소 `Esc`)는 화면 안에 표시한다.
- 포커스 테두리는 항상 보이게 한다(`:focus-visible`). `outline: none` 금지.
- 버튼 이름은 결과를 말한다("저장", "검증 실행"). 같은 동작은 흐름 끝까지 같은 이름을 쓴다.
- 오류 문구는 원인 + 조치만 쓴다. 사과·모호한 표현 금지.

## 데이터 표

- **고정 헤더**: `thead th { position: sticky; top: 0; }`. 첫 열(ID)도 가로 스크롤 때 고정한다.
- **열 정렬**: 헤더 클릭과 `Enter`/`Space` 로 정렬한다. 현재 방향은 `aria-sort` 와 화살표로 함께 표시한다.
- **정렬 기준**: 숫자 열은 오른쪽 정렬 + `font-variant-numeric: tabular-nums`. 글자 열은 왼쪽, 짧은 상태값은 가운데.
- **행 높이**: 기본 32px 안팎의 촘촘한 밀도, 줄무늬 또는 hover 로 행을 구분한다. 긴 글은 말줄임 후 셀 포커스 때 전체 표시.
- **편집 셀 표시**: 편집 가능한 셀과 읽기 전용 셀을 구분한다(읽기 전용은 흐린 배경). 바뀐 셀은 저장 전까지 표시(왼쪽 색 막대 + 화면 위 "바뀐 칸 n개")하고 되돌리기를 제공한다.
- **검증 오류 표시**: 오류 셀은 색만으로 알리지 않는다. 테두리 + 아이콘 + `aria-invalid="true"` + `aria-describedby` 로 문구를 연결한다. 표 위에 오류 개수와 "다음 오류로 이동"을 둔다.
- **ARIA grid**: 편집 가능한 표는 `role="grid"` 와 roving tabindex(표 안에서 탭 정지 1개)를 쓴다. 화살표로 셀 이동, `Enter`/`F2` 편집 시작, `Esc` 취소, `Enter` 확정, `Home`/`End` 행 처음·끝. 읽기 전용 표는 일반 `<table>` 로 충분하다.
- **행 수 기준**: 천 행 이하는 브라우저에서 바로 정렬·필터한다. 그 이상이면 가상 스크롤(보이는 행만 그리기)을 직접 구현한다.
- 필터·검색은 열별 입력 + 전체 검색 하나로 시작한다.

## 색과 대비

- 글자·배경 대비는 WCAG AA 4.5:1 이상, 큰 글자·아이콘·테두리는 3:1 이상이다.
- 상태색(오류·경고·성공)은 라이트·다크 각각 대비를 확인한다.

## 라이트·다크 토큰

- 색은 전부 `:root` CSS 변수로 정의한다(`--bg`, `--surface`, `--text`, `--muted`, `--border`, `--accent`, `--error`, `--warn`, `--ok`, `--edited`).
- 다크는 `@media (prefers-color-scheme: dark)` 와 `[data-theme="dark"]` 둘 다로 덮어쓴다. 수동 전환 버튼을 둔다.
- 컴포넌트에서 hex 값을 직접 쓰지 않는다.

## 자체 완결

- 외부 CDN·웹폰트·npm 패키지를 쓰지 않는다. 서버는 Node 내장 모듈(`http`, `fs`, `path`, `url`)만 쓴다.
- 글꼴은 시스템 글꼴 스택(`system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif`)을 쓴다.
- 오프라인에서 열려야 한다.

## 데스크톱(Electron) 전환을 막지 않는 구조

- 화면(브라우저 JS)과 데이터 접근(Node)을 나누고, 화면은 `fetch('/api/...')` 같은 좁은 인터페이스로만 데이터에 닿는다. 나중에 Electron IPC 로 바꿀 때 이 한 곳만 바꾼다.
- 화면 코드에서 Node 모듈·파일 경로를 직접 쓰지 않는다.
- 주소·포트·데이터 경로는 설정 한 곳에 모은다.
- 브라우저 전용 기능(`window.open` 다중 창, 쿠키 의존 등)에 기대지 않는다.

## 참고

- frontend-design (Anthropic 공식): https://github.com/anthropics/skills/tree/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/frontend-design
- Admin Design (커뮤니티, 원문 보관본): https://github.com/majiayu000/claude-skill-registry-data/blob/a21980a5b576646393ed574f158d7dd92fae4c3f/design/admin-design/SKILL.md
- Building Tables (커뮤니티): https://github.com/ancoleman/ai-design-components/tree/main/skills/building-tables , https://claudskills.com/skills/building-tables/
- WAI-ARIA Grid 패턴: https://www.w3.org/WAI/ARIA/apg/patterns/grid/
- WCAG 2.2 대비 기준: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
