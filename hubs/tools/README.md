# 허브 렌더 도구

UI 프리팹을 배치모드로 해상도별 PNG 렌더한다. 게임 저장소에는 커밋하지 않는다.

- 사용: `bash ~/.config/lrm-hubs/tools/hud_render_modal.sh <작업공간> <태그> <프리팹 Assets/경로> [WxH,...]` (HUD 는 `hud_render.sh`)
- 로그: `<작업공간>/TestResults/<종류>-<태그>.log` (작업 공간별 분리)
- 빈 서브모듈은 시작 전에 자동 초기화한다. 실패하면 종료 코드 3.
- PNG 가 하나도 안 나오면 로그의 에러 줄을 출력하고 종료 코드 4.
- CanvasGroup 투명도는 게임 기본값을 유지한다. 켜서 볼 그룹은 `LRM_RENDER_SHOW_GROUPS=이름,이름` 으로 지정한다(모달은 프리팹 루트도 켬).
- 주의: 2026-11-16 이 수정 전에 찍힌 렌더는 비네트 덮개(organicvignette 계열)가 강제로 보여 화면이 붉게 나왔다. 그 샷의 색은 실제 게임과 다르다.
