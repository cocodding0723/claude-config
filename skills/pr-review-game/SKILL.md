---
name: pr-review-game
description: PR 하나를 팀원(리뷰어)이 혼자 플레이하는 객관식 카드 퀴즈 HTML 파일로 만든다(v4). 문항마다 바뀐 코드 조각 + "왜 이런 구조로 만들었을까?" 보기 3~4개, 밝은 파스텔·진한 외곽선, 튀어 오르는 카드·히트 플래시·화면 흔들림·콤보·WebAudio 합성음. 끝 화면에서 점수·PUML 구조 도식(구조/이전/문제 위치/현재)·왜 이 구조인가·화면 또는 콘솔 전/후·Slack/PR 댓글용 복사. "PR 리뷰 게임", "리뷰 퀘스트", "PR 퀴즈로 만들어줘", `/pr-review-game <번호>` 요청에 사용한다. 결함 탐지 리뷰는 code-review, 변경 해설 문서는 doc-kit 이 맡는다.
---

# PR 리뷰 게임 (v4: 객관식 카드 퀴즈)

v4 가 기본이다. 아래 「v3 고해실」 절은 `assets/template-v3.html` 용 옛 방식 기록이다.

## v4 만드는 법
1. 재료: `gh pr view <N> -R <repo>`, `gh pr diff <N> -R <repo>`, 기존 해설 문서(`<날짜>-PR리뷰/pr-<N>.html`)가 있으면 의도 근거로 쓴다. diff 밖 사실은 저장소 grep 으로 확인한다.
2. 데이터 JSON 작성. 예시: `assets/example-pr-521.json`. 키:
   - `pr{num,repo,title,url,summary[]}`
   - `q[]` 4~6개: `file·node·ask("왜 ~했을까?")·code[](+/- 접두 유지)·opts[3~4]·ans·why(정답 해설 한 줄)·miss[](보기별 오답 해설, 정답 칸은 "")`
   - `why[]{what,reason,result}` 한 줄 한 사실, 무엇→왜→결과
   - `puml{structure,before,problem,after}` 위→아래 실제 참조 계층. problem 은 빨간 화살표·분홍 노드, after 는 바뀐 부분 초록
   - `nodes{PUML별칭: 문항 번호}` 도식 상자 클릭 → 그 문항 코드 조각
   - `console{note,rows[{where,before[],after[]}]}` 화면 변화 없는 PR 의 전/후 근거(선택)
3. 빌드: `python3 scripts/build_v4.py <data.json> <out.html> [--before shot_before.png --after shot_after.png --video smoke.mp4]`
   - PUML 은 로컬 `/opt/homebrew/bin/plantuml -tsvg -pipe` 로만 렌더해 SVG 인라인. 외부 서버 금지.
   - UI PR 이면 스모크 스크린샷 전/후 + 녹화 mp4 를 경로 인자로 넣는다. 화면 변화 없는 PR 은 억지로 만들지 않고 `console` 로 대체한다.
   - 폰트 Jua 서브셋 내장, 3MB 초과 시 실패.
4. 검사(필수, 실제 좌표 입력): 폴더를 `python3 -m http.server 8765 --bind 127.0.0.1` 로 서빙하고
   `node scripts/cdp-v4.mjs <url> touch 390 844 <shot-390.png> 2` 와 `node scripts/cdp-v4.mjs <url> mouse 1440 900 <shot-1440.png> 4`.
   마지막 인자는 일부러 틀릴 문항 번호(쉼표). FAIL 0·오류 0, 샷을 직접 열어 확인한다.
   도식(`.dia svg`)에 `min-width` 금지. 폭 100%·height auto 로 줄이고 390px 에서 가로 스크롤 0 으로 확인한다.
   보기 순서 섞기: `scripts/shuffle_opts.py` 가 빌드 때 opts·miss·ans 를 같은 순열로 재배치(시드=PR번호), 정답 위치 편중(위치별 횟수 편차 2 이상)이면 빌드 실패. 위치 지칭 해설 문항은 제외.

## v4 문항 규칙
- 질문은 "왜 이런 구조로 만들었을까?" 형태, 쉬운 난이도, 자연스러운 문장. 보기는 PR 실제 의도에서만.
- 오답은 확실히 틀린 것. 오답 해설 한 줄·정답 해설 한 줄.
- 문체: 평서 "~다" 또는 명사형. "~합니다" 금지.
- 점수: 정답 100 + 콤보 (n-1)×50. 틀려도 감점 없음, 정답을 보여 주고 다음으로.
- 연출: 하스스톤 등 기존 게임의 이름·아트·UI 모양을 베끼지 않는다. 느낌(튀어 오름·쾅)만.
- 소리: WebAudio 합성(선택·정답·오답·콤보·결과), 첫 클릭 이후, 음소거 버튼 상시.

---

# v3 고해실 (옛 방식, template-v3.html)

지루한 리뷰 문서 대신, 작업 맥락을 모르는 팀원이 10분 안에 PR 핵심을 짚게 한다.

## 0. 금지
- 읽기만 한다. push·PR 수정·댓글 게시·커밋 금지.
- 지어내기 금지. 문제·정답·해설 근거는 **diff·커밋 메시지·PR 본문·기존 리뷰 문서**에서만. 근거 없는 문제는 내지 않는다.

## 1. 재료 수집
```bash
gh pr view <N> --json title,body,commits,files,author
gh pr diff <N>
```
- 기존 리뷰 문서가 있으면 재사용: `~/Documents/90_정리기록/<날짜>-PR리뷰/pr-<N>.html` 의 의도·⚠️ 포인트·검수 지적.
- diff 밖 사실(예: 이미 있던 `using`)을 정답 근거로 쓰면 `grep` 으로 확인하고 해설에 파일·줄을 적는다.

## 2. 손님 설계 (고해실 규칙, 기본 쉬움)
- 손님 = 바뀐 덩어리(hunk) 순서. 사소한 hunk(개행·EOF)는 합치거나 뺀다. 코드 조각 10~20줄, `+`/`-` 유지.
- 손님마다 대사 위에 `🔎 이 코드가 하는 일:` 쉬운 말 한 줄(`easy`) + 용어 풀이 한 줄(`term`).
- 대사 빈칸 1~2개. 손패 = 정답 + 오답 2장(쉬움). 오답은 확실히 다른 것(다른 덩어리의 단어·무관한 식별자). 보통 = 오답 전부 + 이전 손님 정답 카드(↩)가 섞임.
- 첫 손님은 튜토리얼: `tut: 0` 으로 첫 칸을 미리 끼워 보여 준다.
- 시간 압박 없음. 촛불은 장식. ⚡신속(20초 내 정답) +50 보너스만.
- 💡힌트 버튼 상시 + 10초 뒤 자동: `hint` 줄 하이라이트 + 빈칸에 정답 첫 글자.
- 👾위험 손님: 통과 뒤 `risk.cand` 후보 3줄만 점선 강조, 그중 `risk.lines` 고르면 🗡️날카로움 +50. **선택 보너스다. 다음 버튼은 찍기와 무관하게 판정 직후 바로 나온다**(11-05 사고: 찍기 전엔 다음 버튼이 없어 첫 손님에서 막힘).
- 손님마다 `👉 넘어가려면: …` 안내 줄과 ⏭ 모르겠음·건너뛰기 버튼을 항상 둔다. 다음 버튼 위에도 안내 한 줄.
- 틀려도 감점 없음: 정답을 채워 보여 주고 다음으로. 놓친 곳·🔥모르겠음은 끝 화면 "작성자에게 물어볼 곳"으로.
- 점수: 통과 100 · 신속 +50 · 날카로움 +50. 이해도 % = 통과 손님 비율.

## 문구 규칙
- 정답을 끼운 완성 문장을 소리 내 읽어 자연스러워야 한다.
- 영어 식별자 카드 바로 뒤에 조사(을/를·이/가·으로/로)를 붙이지 않는다. `(을)를` 같은 병기도 금지. 뒤에 한국어 명사(함수·태그·값·설정·호출)를 두어 조사를 그 명사에 붙인다. 예: `[LogEngine.LogError] 함수가 찍어요`.
- 한 문장 = 한 사실, 30자 안팎.
- 빈칸은 핵심 단어만. 조사·접속어를 빈칸으로 만들지 않는다.
- 식별자 카드만 영어, 나머지는 쉬운 한국어. 손님 대사는 구어(~요), 그 밖은 짧은 명사형.
- 완성 후 writing-humanizer 기준으로 어색한 문장 0개인지 점검한다.

## 3. HTML 규칙
- 단일 파일. 외부 요청 없음. 리소스는 base64 내장. 5MB 이하.
- 390px 폭 가로 스크롤 없음(코드 박스만 내부 스크롤). 입력은 드래그(pointer 이벤트)와 탭(카드 → 빈칸) 둘 다.
- 로비: PR 소개 + 난이도(쉬움 기본/보통) + 리뷰어 이름 + "Slack 미리보기에서는 동작하지 않음, 내려받아 PC 브라우저로 열기" 안내.
- 진행 중: 촛불·점수·⏸ 멈춤 창·🔊 음소거. 효과음은 사용자 클릭 뒤에만. 손님마다 💬 질문 메모.
- 끝 = 📜 두루마리: 이해도·점수·신속·날카로움·놓친 곳·Slack/PR 댓글용 복사 텍스트(이름·난이도·점수·이해도·놓친 곳·질문 메모·PR 링크)·다시.
- Safari 호환: `navigator.clipboard` 는 file:// 에서 없을 수 있어 `execCommand("copy")` 폴백 유지. 소리는 AAC m4a·mp3. 최신 문법(`?.`·`??`)은 쓰지 않는다.
- 서버·순위표 없음. 최고 점수만 `localStorage`(try/catch, 막혀도 진행).

### 만드는 법
1. `assets/template-v3.html` 을 복사해 `PR`·`G` 데이터만 바꾼다(`face·file·boss·easy·term·code·say·ans·decoy·hint·tut·risk{q,cand,lines,why}·why`). `{{...}}` 자리표시는 그대로 둔다.
2. `cd <작업 폴더> && python3 ~/.claude/skills/pr-review-game/scripts/build.py <template> <out.html> ~/.claude/skills/pr-review-game/assets`. 폰트를 쓰인 글자만 남겨 줄이고 이미지·소리를 넣는다.
3. 흐름 검사(필수, 실제 좌표 입력): `node scripts/cdp.mjs <out.html> mouse 1280 900 '<계획>'` 와 `touch 390 844`. 계획은 손님별 `{swap|remove|wrong|drag|burn|hint|pause|risk:"ok"|"bad"}` JSON 배열. `element.click()`·함수 직접 호출 테스트만으로 완료라 하지 않는다. 정답만·오답 교체·넣었다 빼기·힌트·위험 줄 생략·위험 줄 오답·건너뛰기·멈춤→계속을 모두 돌린다.
4. 보조 검사: out.html 의 첫 `</body>` 앞에 `scripts/test.js` 를 넣은 사본을 만든 뒤, 헤드리스 Chrome `--dump-dom` 으로 `data-log` 를 읽는다. `?nols` 를 붙이면 localStorage 차단 상태로 돈다. 1280·390 두 폭에서 전부 OK, 오류 0 이어야 한다.
5. 콘솔 오류는 `--enable-logging=stderr --v=0` 출력의 `CONSOLE` 줄로 확인한다.

## 참고: 가져온 리소스 (Basakansoft/GameJam2026-BasakUnion, dev 브랜치, LFS)
- 배경 `Assets/ConfessionalWordplay/Art/InGamePsd/room_background.png` → room.jpg(900px, q60)
- 말풍선 `…/InGamePsd/speech_bubble.png`, 카드 `…/InGamePsd/word_card.png`(현재 미사용), 촛불 `…/InGamePsd/candle_on.png` → candle.png(160px), 두루마리 `…/InGamePsd/inventory_scroll.png` → scroll.png(420px)
- 효과음 `Assets/ConfessionalWordplay/Audio/` ScrollingPaper.wav(카드), CandleLight.wav(정답), WrongOrgan.wav(오답) → AAC 64k m4a, fire3.mp3(모르겠음). 개별 라이선스 파일 없음. 같은 회사 저장소라 사용 허가.
- 폰트 `Assets/Fonts/Jua/Jua-Regular.ttf`: SIL OFL 1.1(`assets/Jua-OFL.txt`), 내장 가능. `DNFForgedBlade-Medium.ttf` 는 라이선스 파일이 없어 쓰지 않음.
- 받는 법: `gh api contents/<path>?ref=dev` 로 LFS 포인터(oid·size) 확인 → `https://github.com/Basakansoft/GameJam2026-BasakUnion.git/info/lfs/objects/batch` 에 download 요청 → href 로 받기. clone·실행 금지.
- 로직: 원작 `Runtime/ConfessionSession.cs` 는 음절 조각 편집(자르기·합치기·교체) 방식이다. 이 게임은 빈칸 판정·정답 이어받기·힌트·화형·멈춤·두루마리 규칙만 옮겼다. 충돌 시 쉬움 규칙 우선.

## 4. 산출·확인
- 저장: `~/Documents/90_정리기록/<날짜>-PR리뷰/game/pr-<N>-game.html`
- 390 폭 캡처: 헤드리스 창 최소 폭이 500 이라 390 iframe 래퍼로 찍는다. `--allow-file-access-from-files` 필요.
```bash
D=$(mktemp -d); "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --allow-file-access-from-files --user-data-dir="$D" --window-size=390,1500 \
  --virtual-time-budget=3000 --screenshot=<out.png> file://<wrapper.html>; rm -rf "$D"   # wrapper 의 iframe src 끝에 #demo
```
- 캡처를 직접 열어 잘림·가로 넘침 확인. 문제 수·스테이지 수·근거 없는 문제 0개를 보고한다.
