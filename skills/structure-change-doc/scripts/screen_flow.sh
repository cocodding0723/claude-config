#!/bin/bash
# 녹화 영상에서 화면 변화 흐름을 뽑는다: 장면이 바뀌는 프레임만 골라 가로 띠(PNG)와 짧은 움직이는 미리보기(GIF)를 만든다.
# 사용: screen_flow.sh 영상.mp4 출력폴더 [장면변화 민감도 0.08] [최대 장수 8]
set -euo pipefail
IN="${1:?영상}"; OUT="${2:?출력폴더}"; TH="${3:-0.08}"; MAX="${4:-8}"
mkdir -p "$OUT"
ffmpeg -v error -y -i "$IN" -vf "select='gt(scene,$TH)',scale=270:-1" -fps_mode vfr -frames:v "$MAX" "$OUT/scene_%02d.png"
n=$(ls "$OUT"/scene_*.png 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -gt 0 ] && ffmpeg -v error -y -i "$OUT/scene_%02d.png" -filter_complex "tile=${n}x1:padding=8:color=white" -frames:v 1 "$OUT/flow-strip.png"
ffmpeg -v error -y -i "$IN" -t 12 -vf "fps=8,scale=360:-1,split[s0][s1];[s0]palettegen=max_colors=96[p];[s1][p]paletteuse" -loop 0 "$OUT/preview.gif"
echo "장면 ${n}장 → $OUT/flow-strip.png, 미리보기 $OUT/preview.gif"
