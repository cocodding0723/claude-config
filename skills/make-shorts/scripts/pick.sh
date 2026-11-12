#!/bin/bash
# 자료 후보를 한 장의 타일로 묶어 번호 목록과 함께 보여 준다. 이미지를 하나씩 열어 보지 않기 위한 것이다.
#   pick.sh <출력.jpg> <파일...>      (이미지·영상 모두 가능. 영상은 가운데쯤의 한 프레임)
set -euo pipefail
OUT="$1"; shift; T=$(mktemp -d); N=0
for f in "$@"; do
  N=$((N+1)); SS=()
  case "$(echo "${f##*.}" | tr A-Z a-z)" in mp4|mov|webm|mkv) D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || echo 2); SS=(-ss "$(echo "$D/3" | bc -l)");; esac
  ffmpeg -y -loglevel error ${SS[@]+"${SS[@]}"} -i "$f" -f lavfi -i color=0x6b7280:s=220x220 \
    -filter_complex "[0:v]scale=220:220:force_original_aspect_ratio=decrease[fg];[1:v][fg]overlay=(W-w)/2:(H-h)/2:shortest=0" -frames:v 1 "$T/$(printf %04d $N).jpg" 2>/dev/null || { echo "$N ✗ 못 읽음: $f"; ffmpeg -y -loglevel error -f lavfi -i color=red:s=220x220 -frames:v 1 "$T/$(printf %04d $N).jpg"; continue; }
  S=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x "$f" 2>/dev/null | head -1)
  echo "$N  $S  $f"
done
COLS=8; ROWS=$(( (N + COLS - 1) / COLS ))
ffmpeg -y -loglevel error -pattern_type glob -i "$T/*.jpg" -vf "tile=${COLS}x${ROWS}:padding=4:color=black" -frames:v 1 "$OUT"
rm -rf "$T"; echo "타일(왼쪽 위부터 번호순, 한 줄 ${COLS}칸): $OUT"
