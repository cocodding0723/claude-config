# 설명 문서 스펙 (HTML)

`explain-diff` 2단계에서 쓴다. 산출물은 **자체 완결 HTML 한 파일** — CSS·JS 인라인, 외부 요청 0건. 브라우저로 보고, 인쇄해서 읽고, 폰으로도 본다.

## 기본 구성

`SKILL.md`의 **의도와 선택 → PUML 동작 흐름 → 실제 화면·검증** 3절이 기본이다. 첫 절에서 왜 이 설계를 택했고 왜 이 순서·범위로 작업했는지 설명한다. 도식은 그 선택이 구현되는 흐름, 촬영 이미지는 의도한 결과를 보여 준다. 짧게 쓰되 판단 근거를 생략하지 않는다. 짧은 문서에는 목차를 억지로 넣지 않는다. 상세 해설을 요청했을 때만 아래 확장 구조를 적용하고, 퀴즈는 별도 요청이 있을 때만 붙인다.

## 확장 구조 — 상세 해설 요청 시

한 페이지에 이어 쓴다. 탭으로 최상위 구조를 나누지 않는다. 긴 문서에는 앵커 목차를 둔다.

### 1. Background

읽는 사람이 얼마나 아는지 모른다는 게 전제다. 그래서 **두 겹**으로 쓴다:

- **깊은 배경** — 이 도메인을 처음 보는 사람용. 섹션 첫 줄에 "이미 아신다면 건너뛰세요" callout을 단다
- **좁은 배경** — 이번 변경이 직접 건드리는 부분의 기존 동작. **변경 전 코드가 어떻게 동작했는지**를 여기서 확실히 세운다

배경이 부실하면 뒤가 전부 무너진다. 이 섹션을 쓰려면 디프 밖의 주변 코드를 읽어야 한다.

### 2. Intuition

**본질만.** 전체 디테일을 여기서 설명하지 않는다.

- 이 변경이 **무엇을 달성하려는지**, 그리고 그게 왜 자연스러운 해법인지
- **장난감 데이터로 된 구체 예시** 하나를 끝까지 따라간다. 추상적 서술 대신 "요청 A가 들어오면 → 캐시가 X를 보고 → B를 반환" 식
- 관련 개념이 있으면 이름을 붙여준다 (예: "이건 본질적으로 isometric projection이다")
- **그림을 아끼지 않는다**

### 3. Code

변경의 높은 수준 walkthrough.

- **파일 알파벳순 금지.** 이해되는 순서 = 대개 데이터가 흐르는 순서, 또는 "핵심 한 곳 → 그걸 지탱하는 주변" 순서
- 관련된 변경을 개념으로 묶고 그룹에 제목을 준다
- 코드 스니펫은 **설명 문장 사이에 끼워넣는다** (literate style). 스니펫 덩어리를 먼저 던지고 밑에 설명을 붙이지 않는다
- 스니펫은 원문 그대로가 아니라 **요지가 보이게 잘라서** 쓴다. 생략은 `// …` 로 표시
- 각 스니펫에 `파일:줄` 을 단다

### 4. 번외 (있을 때만)

읽다 발견한 결함·의문·개선 여지. **본문에 섞지 않는다.** 없으면 섹션 자체를 넣지 않는다.

### 5. 자가 점검 퀴즈

서술형 — **최소 5문항이고 변화량에 비례해 늘린다**(본문 3장 개념 절마다 최소 1문항, 기준은 `SKILL.md`의 «퀴즈 문항 수»). 유형·출제 기준은 `quiz-protocol.md`를 따른다. **문서 맨 끝에 둔다** (본문보다 먼저 보이면 답 찾기 읽기가 된다).

- 문항 텍스트는 그대로 노출하되, **정답·핵심 요소는 문항별 `<details><summary>정답 보기</summary>…</details>`로 숨긴다** (기본 접힘)
- 정답 블록에는 핵심 요소와 근거 `파일:줄`을 적는다 — 읽는 사람이 혼자 대조할 수 있게
- 섹션 첫머리에 "자기 답을 먼저 적거나 소리 내 말한 뒤 토글을 열 것" 안내 한 줄

## 다이어그램

**동작·구조 도식은 PUML로 작성해 SVG로 렌더한다.** `.puml` 원본을 보존하고 HTML에는 인라인 SVG 또는 SVG data URI를 넣는다. 원문 코드블록만 보여 주고 도식을 완성했다고 하지 않는다. ASCII 아트는 쓰지 않는다.

**소수의 다이어그램 패밀리를 정해 재사용한다.** 문서마다 다른 그림 언어를 쓰면 독자가 그림 읽는 법부터 매번 새로 배운다. 유용한 두 가지:

1. **동작 순서** — sequence 또는 activity. 시작 조건부터 핵심 처리와 결과까지 연결한다.
2. **시스템 구조** — component. 변경과 관련된 책임·연결만 남기고 화살표에 동작이나 예시 데이터를 붙인다.

같은 패밀리로 "변경 전 / 변경 후"를 나란히 그리는 게 가장 잘 먹힌다.

### 실제 화면 캡처 — UI 가 바뀌면 넣는다

화면이 바뀌는 변경이면 실제 스모크·실기 캡처 중 핵심 차이를 보여 주는 2~4장만 골라 관련 흐름 바로 옆에 놓는다. 전체 촬영본을 갤러리로 나열하지 않는다. 실제 캡처가 없으면 미촬영으로 표시한다.

- 출처: 저장소의 검증 산출물(예: LRM `TestResults/shots/*.png` + `manifest.json`의 `expect`). 기준 SHA·실행 시각/토큰과 해상도를 확인하고 캡션 아래 `.src` 줄에 짧게 적는다. 이전 실행·다른 작업 트리·스킵된 샷을 이번 검증 근거로 쓰지 않는다
- 자체 완결 규칙 때문에 **base64 data URI 로 박는다.** 원본 PNG 를 그대로 넣지 않고 `sips -s format jpeg -s formatOptions 72 --resampleWidth 540` 정도로 줄인다(장당 약 100KB)
- 설명하는 요소 위에 짧은 라벨 핀을 놓거나 필요한 부분을 확대한다. 캡션은 관찰할 변화 한 줄만 적는다. 직접 이미지를 열어 라벨 위치·텍스트 가독성·설명과 실제 상태의 일치를 확인한다
- 같은 화면의 목업과 캡처를 둘 다 두지 않는다. 캡처가 있으면 목업을 뺀다

## 문체

- **한국어로 쓴다.** 코드 식별자·에러 메시지·파일 경로는 원문 유지. 평서 `~다`, 나머지 문체·장치는 `~/.claude/skills/tech-record/references/house-style.md`
- 섹션 구성은 `SKILL.md`의 기본 3절이 정본이다. 위 확장 구조는 상세 요청 시에만 적용한다
- Martin Kleppmann 같은 명료함과 흐름. 섹션 간 전환이 매끄러워야 한다 — 각 섹션 끝에서 다음 섹션으로 넘어가는 한 문장
- classic style: 독자에게 무언가를 **보여주는** 글. "본 문서에서는 ~을 다룬다" 같은 메타 서술 최소화
- 핵심 개념·정의·중요한 엣지 케이스는 callout으로 뽑는다

## HTML 요구사항

- 단일 파일, `<style>`·`<script>` 인라인. 외부 폰트/CDN 금지
- 반응형 — 폰에서도 읽힌다
- **다크/라이트 모두 대응** (`prefers-color-scheme`)
- **인쇄 CSS 필수** — 리트의 사용법 중 하나가 "인쇄해서 카페에서 읽기"다. 배경색 유지, 섹션 중간에서 페이지가 잘리지 않게
- 목차를 넣으면 앵커 링크 사용

### 코드블록 — 반드시 확인할 것

코드는 `<pre>` 태그를 쓴다. 커스텀 div로 대체할 경우 CSS에 **`white-space: pre-wrap`이 반드시 있어야 한다.** 없으면 브라우저가 모든 줄바꿈을 하나로 접어서 코드가 한 줄로 뭉개진다.

**파일을 저장하기 전에 HTML 소스의 코드블록을 전부 훑어 `white-space: pre` 또는 `pre-wrap`이 걸려 있는지 확인한다.** 이건 실제로 자주 나는 사고다.

### 구문 강조 — 필수

코드블록은 색을 입힌다. 흑백 코드는 키워드·문자열·주석이 구분되지 않아 읽기 힘들다는 피드백이 있었다(2026-10-08).

- **`references/highlight.html` 조각을 그대로 넣는다.** style 블록 내용은 문서 `<style>` 의 `@media print` 앞에, script 블록은 문서 맨 끝에. 외부 CDN 금지라 highlight.js·Prism 을 불러오지 않는다
- 코드블록마다 **언어 클래스**를 단다: `<pre><code class="lang-cs">`, `<pre><code class="lang-json">`. 클래스가 없으면 강조하지 않는다 — 셸 출력·로그는 일부러 비워 둔다
- 색은 조각의 `--hl-*` 변수만 쓴다. 라이트·다크 값이 같이 들어 있다
- 새 언어가 필요하면 조각의 `LANGS` 에 함수를 추가하고 이 문서의 지원 목록을 고친다. 문서 안에서만 고치지 않는다

### JSON 은 정렬해서 쓴다

데이터 예시를 한 줄이나 반쯤 접은 모양(`{"from":0,"to":1},…]}`)으로 쓰지 않는다. 실제 파일처럼 **2칸 들여쓰기, 키 하나에 한 줄, `": "` 뒤 공백**으로 쓴다.

- 생략은 값 자리에 `…` 를 넣지 말고 **줄 끝 `// …` 주석**으로 적는다(`"stageIds": [0, 1, 2, 3],   // … 25 까지`). `lang-json` 강조가 주석을 회색으로 칠한다
- 실제 파일에서 가져오면 경로를 박스 제목에 적는다(`graph-main.json`). 코드블록 첫 줄에 파일명을 넣지 않는다 — JSON 이 아니게 된다
- 정렬한 JSON 은 길어지므로 여러 개를 나란히(가로 3열) 두지 않는다. 세로로 쌓는다

## 스켈레톤

```html
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>…</title>
<style>
  :root {
    --bg:#fff; --fg:#1a1a1a; --muted:#666; --rule:#e0e0e0;
    --code-bg:#f6f6f4; --accent:#2b6cb0; --callout:#f0f6ff;
  }
  @media (prefers-color-scheme:dark) {
    :root { --bg:#16181c; --fg:#e6e6e6; --muted:#9a9a9a; --rule:#2e3238;
            --code-bg:#1e2126; --accent:#7ab0ea; --callout:#1b2430; }
  }
  body { max-width:46rem; margin:0 auto; padding:2rem 1.25rem 6rem;
         background:var(--bg); color:var(--fg);
         font:16px/1.7 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",
              "Noto Sans KR",sans-serif;
         word-break:keep-all; overflow-wrap:break-word; }
  h1,h2,h3 { line-height:1.3; margin:2.4em 0 .6em; }
  h2 { padding-bottom:.3em; border-bottom:1px solid var(--rule); }
  /* + references/highlight.html 의 style 블록 */
  pre { background:var(--code-bg); padding:.9rem 1rem; border-radius:6px;
        overflow-x:auto; white-space:pre; font-size:.86em; line-height:1.55; }
  code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  :not(pre) > code { background:var(--code-bg); padding:.12em .35em; border-radius:3px; }
  .callout { background:var(--callout); border-left:3px solid var(--accent);
             padding:.85rem 1rem; border-radius:0 5px 5px 0; margin:1.4em 0; }
  .src { color:var(--muted); font-size:.82em; margin:-.6em 0 1.2em; }
  figure { margin:1.8em 0; }
  figcaption { color:var(--muted); font-size:.86em; text-align:center; margin-top:.6em; }
  .toc a { color:var(--accent); }
  @media print {
    :root { --bg:#fff; --fg:#000; --code-bg:#f4f4f4; --callout:#f2f6fc; }
    body { max-width:none; font-size:11pt; }
    h2, figure, pre, .callout { break-inside:avoid; }
    h2, h3 { break-after:avoid; }
    .toc { display:none; }
    a[href^="http"]::after { content:" (" attr(href) ")"; font-size:.8em; color:#555; }
  }
</style>
```

PUML에서 렌더한 SVG는 위 레이아웃 안에 넣는다. 다크·라이트 모드 모두 도식의 글자와 선이 읽히는지 확인한다.

## 저장

```
<scratchpad>/explain-diff/YYYY-MM-DD-<slug>.html
```

**레포 안에 저장하지 않는다.** 날짜 접두는 생략하지 않는다.

예외는 하나다: 리뷰 동봉용 마크다운(SKILL.md `2-1. 산출 형식`의 B 경로)은 레포의 문서 디렉터리에 두고 PR에 넣는다. HTML 전용 레이아웃·색·`white-space` 규칙은 적용하지 않는다. 기본 3절과 PUML·촬영 이미지 원칙을 따르고, 요청한 퀴즈가 있을 때만 `<details>` 형식을 가져간다.
