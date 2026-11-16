#!/bin/bash
# 사용: hud_build_outcome_tmp.sh <워크스페이스> <태그> <success,fail> (시안을 뒤에 깔고 1100x2100 포함 렌더)
WS="$1"; TAG="$2"; VARS="${3:-success,fail}"
P=Assets/Game/SceneView/Scene/Design/UI_Prefab/Ingame/Automa/AutomaMissionOutcomeDialog.prefab
SIZES="1100x2100,1080x1920,1080x2400,1440x3120,1536x2048,720x1280"
mkdir -p "$WS/Assets/Editor/RenderBg"
cp ~/Documents/90_정리기록/20261116-수진-오토마-UI-시안/인게임_찬스성공.png "$WS/Assets/Editor/RenderBg/bg_success.png"
cp ~/Documents/90_정리기록/20261116-수진-오토마-UI-시안/인게임_승부실패.png "$WS/Assets/Editor/RenderBg/bg_fail.png"
cp "$WS/Assets/Editor/RenderBg/bg_fail.png" "$WS/Assets/Editor/RenderBg/bg_rumor-success.png"
cp ~/.config/lrm-hubs/tools/ModalRenderer.cs ~/.config/lrm-hubs/tools/RenderShots.cs "$WS/Assets/Editor/"
export LRM_SLOT_OWNER_PID=$$ LRM_UNITY_SLOTS=2 LRM_SLOT_PRIORITY=1
S=$(bash ~/.config/lrm-hubs/unity_slot.sh acquire outcome-prefab-build)
trap 'bash ~/.config/lrm-hubs/unity_slot.sh release "$S"; rm -rf "$WS/Assets/Editor/RenderBg" "$WS/Assets/Editor/RenderBg.meta" "$WS"/Assets/Editor/{ModalRenderer,RenderShots}.cs{,.meta}' EXIT
for V in ${VARS//,/ }; do
/Applications/Unity/Hub/Editor/6000.3.16f1/Unity.app/Contents/MacOS/Unity -batchmode -projectPath "$WS" -executeMethod TmpMissionOutcomeBuilder.Run -variant $V \
  -renderOut "$WS/TestResults/modalrender" -renderTag "${TAG}_$V" -renderMotion "${LRM_RENDER_MOTION:-1}" -renderMotionSec "${LRM_RENDER_MOTION_SEC:-0.45}" -renderSizes "$SIZES" -renderPrefabs "$P" -renderBg "Assets/Editor/RenderBg/bg_$V.png" -logFile /tmp/outcome-${TAG}_$V.log >/dev/null 2>&1
echo "$V exit=$?"
done
ls "$WS/TestResults/modalrender"/${TAG}_* | head -3
