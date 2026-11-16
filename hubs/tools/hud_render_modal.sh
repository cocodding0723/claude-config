#!/bin/bash
# 모달 프리팹 배치 렌더(커밋 금지). 기준 1100x2100(SafeCanvasScaler 와 같은 min 배율), 프리팹은 화면 가득 stretch 단독 인스턴스(프리팹 자체 Dim 포함).
# 사용: hud_render_modal.sh <워크스페이스> <태그> <프리팹 Assets/ 전체경로,콤마> [WxH,콤마(기본 1080x1920,1080x2400,1440x3120,1536x2048,720x1280)] [배경스프라이트 Assets/경로]
# 결과: <워크스페이스>/TestResults/modalrender/<태그>_<W>x<H>.png. 끝나면 Assets/Editor/ModalRenderer.cs(+.meta) 제거(커밋 금지).
set -u
WS="$1"; TAG="$2"; PF="$3"; SIZES="${4:-1080x1920,1080x2400,1440x3120,1536x2048,720x1280}"; BG="${5:-}"
mkdir -p "$WS/Assets/Editor"
cp ~/.config/lrm-hubs/tools/ModalRenderer.cs "$WS/Assets/Editor/ModalRenderer.cs"
export LRM_SLOT_OWNER_PID=$$
S=$(~/.config/lrm-hubs/unity_slot.sh acquire modal-render)
cleanup() { ~/.config/lrm-hubs/unity_slot.sh release "$S"; rm -f "$WS/Assets/Editor/ModalRenderer.cs" "$WS/Assets/Editor/ModalRenderer.cs.meta"; }
trap cleanup EXIT
/Applications/Unity/Hub/Editor/6000.3.16f1/Unity.app/Contents/MacOS/Unity -batchmode -projectPath "$WS" -executeMethod ModalRenderer.Run \
  -renderOut "$WS/TestResults/modalrender" -renderTag "$TAG" -renderSizes "$SIZES" -renderPrefabs "$PF" -renderBg "$BG" -logFile /tmp/modalrender-$TAG.log >/dev/null 2>&1
ls "$WS/TestResults/modalrender"/${TAG}_* 2>&1
