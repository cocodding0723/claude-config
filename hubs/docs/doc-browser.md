# 문서 모아보기 (로컬 페이지)

주소: http://127.0.0.1:17420/ (내 컴퓨터에서만 열림). launchd 가 상시 실행함(`ai.lrm.docbrowser`).

## 하는 일

- 정리기록·hub-inbox·작업 공간 `docs/out` 의 html 을 최신순으로 모음. 허브 필터, 제목 검색, 도식 유무·답 상태·보냄 필터.
- 문서마다 결정 번호(D-…)와 답 상태(대기·답함·보류·충돌)를 표시함. 출처는 `slack-sent.jsonl`(첨부 기록)과 `slack-inbox.jsonl`(버튼·반응 답).
- 도식 썸네일 또는 '도식 없음' 표시. 판정은 doc-kit `check.py --json`(`diagram_info`)을 그대로 씀. 검사기가 옛 버전이면 '판정 불가'.
- 행 클릭 시 페이지 안에서 열람(스크립트·외부 요청 없는 격리 창).
- [보내기] → 받는 사람 선택 → 메시지(제목·핵심 3줄 초안, 수정 가능) → 확인 후 전송 → 봇이 그 사람 DM 에 파일과 메시지를 보냄. 기록은 `doc-sent.jsonl`.

## 처음 한 번 해야 할 일

1. 받는 사람 설정: `~/.config/lrm-hubs/doc-recipients.json` 의 `slack_id` 를 채움(예시는 `doc-recipients.example.json`). ID 는 저장소에 올리지 않음.
2. 폴더 접근 권한: 시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한(또는 파일 및 폴더)에서 launchd 가 쓰는 python3 허용. 정리기록 폴더가 `~/Documents` 안이라 이 권한이 없으면 그 폴더 문서가 목록에서 빠짐.
3. 적재: `bash install.sh --reload-launchd` 또는 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.lrm.docbrowser.plist`.

## 보안 규칙

- 바인드는 코드 상수 `HOST=127.0.0.1` 고정(설정·환경 변수로 못 바꿈). Host 헤더가 127.0.0.1·localhost 가 아니면 403.
- 보내기는 `POST /api/send` 한 곳. Origin 이 서버 주소가 아니거나 없으면, `Sec-Fetch-Site` 가 same-origin 이 아니면, `X-CSRF-Token` 이 페이지 열 때 발급한 값과 다르면 403.
- 전송 함수(`doc_send.send`)를 부르는 곳은 서버의 POST 처리기뿐. 명령줄 진입점·자동 호출 경로 없음. 허브·스크립트는 보내기를 호출할 수 없음.
- 문서는 수집된 id 로만 엶. 경로를 받지 않음. 비밀 파일(.env·키·인증서)은 첨부 단계에서 한 번 더 거부함.
- `slack_notify.sh --to-user` 는 `doc-recipients.json` 에 등록된 ID 만 허용함.

## 문제 해결

| 증상 | 원인·조치 |
|---|---|
| 정리기록 문서가 목록에 없음 | python3 폴더 접근 권한 없음. 위 2번 |
| 보내기 창에서 'Slack ID 미설정' | `doc-recipients.json` 의 `slack_id` 비어 있음 |
| '보안 토큰 없음·만료' | 페이지를 새로 고침(토큰은 페이지를 열 때마다 발급, 12시간 유효) |
| 접속 안 됨 | `tail ~/.config/lrm-hubs/doc-browser.log`. 포트 충돌이면 `LRM_DOC_PORT` 로 바꾸고 plist 에 환경 변수 추가 |

구조·결정 근거: `docs/out/doc-browser/doc-browser.html`.
