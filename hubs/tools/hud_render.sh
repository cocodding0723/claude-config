#!/bin/bash
# UI 프리팹 배치 렌더(커밋 금지). 사용: hud_render.sh <워크스페이스> <태그> <WxH,WxH> [프리팹경로(UI_Prefab/Ingame/ 기준),콤마]
# 예: hud_render.sh $WS before 1080x1920,1600x900 IngameView/IngameView.prefab,Automa/AutomaMissionBanner.prefab
# 결과: <워크스페이스>/TestResults/hudrender/<태그>_<W>x<H>.png. 끝나면 Assets/Editor/{HudRenderer,RenderShots}.cs(+.meta) 제거할 것. 샷: <태그>_<W>x<H>_t0(시작)·_t1(중간)·_t2(완료)+기존 이름(=t2). 모션 끄기: LRM_RENDER_MOTION=0.
set -u
WS="$1"; TAG="$2"; SIZES="${3:-1080x1920}"; PF="${4:-IngameView/IngameView.prefab,Automa/AutomaMissionBanner.prefab}"
cp ~/.config/lrm-hubs/tools/HudRenderer.cs ~/.config/lrm-hubs/tools/RenderShots.cs "$WS/Assets/Editor/"
export LRM_SLOT_OWNER_PID=$$
S=$(~/.config/lrm-hubs/unity_slot.sh acquire hud-render)
trap "~/.config/lrm-hubs/unity_slot.sh release $S" EXIT
/Applications/Unity/Hub/Editor/6000.3.16f1/Unity.app/Contents/MacOS/Unity -batchmode -projectPath "$WS" -executeMethod HudRenderer.Run \
  -renderOut "$WS/TestResults/hudrender" -renderTag "$TAG" -renderMotion "${LRM_RENDER_MOTION:-1}" -renderMotionSec "${LRM_RENDER_MOTION_SEC:-0.45}" -renderSizes "$SIZES" -renderPrefabs "$PF" -logFile /tmp/hudrender.log >/dev/null 2>&1
ls "$WS/TestResults/hudrender"/${TAG}_* 
