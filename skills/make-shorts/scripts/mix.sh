#!/bin/bash
# 무음 영상 + 나레이션(+ BGM)을 합쳐 build/final.mp4 를 만든다.
#   mix.sh <프로젝트> [bgm 파일 | pad | none] [목표 LUFS]
# pad 는 ffmpeg 로 합성한 잔잔한 화음이다(저작권 걱정 없음). BGM 은 말소리가 나올 때 자동으로 낮춘다.
set -euo pipefail
P="$(cd "$1" && pwd)"; BGM="${2:-none}"; LUFS="${3:--14}"
B="$P/build"; DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$B/video.mp4")
if [ "$BGM" = "pad" ]; then
  BGM="$B/pad.wav"
  ffmpeg -y -loglevel error -f lavfi -i "sine=f=110:d=$DUR" -f lavfi -i "sine=f=164.81:d=$DUR" -f lavfi -i "sine=f=220:d=$DUR" -f lavfi -i "sine=f=277.18:d=$DUR" \
    -filter_complex "[0][1][2][3]amix=inputs=4:normalize=0,lowpass=f=900,tremolo=f=0.18:d=0.5,aecho=0.8:0.7:420|700:0.35|0.25,afade=t=in:d=1.5,afade=t=out:st=$(echo "$DUR-2" | bc):d=2,volume=0.5" -ar 48000 -ac 2 "$BGM"
fi
if [ "$BGM" = "none" ]; then
  ffmpeg -y -loglevel error -i "$B/video.mp4" -i "$B/narration.wav" \
    -filter_complex "[1:a]loudnorm=I=$LUFS:TP=-1.5:LRA=7[a]" \
    -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -ar 48000 -t "$DUR" -movflags +faststart "$B/final.mp4"
else
  ffmpeg -y -loglevel error -i "$B/video.mp4" -i "$B/narration.wav" -stream_loop -1 -i "$BGM" \
    -filter_complex "[1:a]asplit=2[n][sc];[2:a]volume=-16dB[b];[b][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];[n][duck]amix=inputs=2:normalize=0:duration=first,loudnorm=I=$LUFS:TP=-1.5:LRA=7[a]" \
    -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -ar 48000 -t "$DUR" -movflags +faststart "$B/final.mp4"
fi
echo "$B/final.mp4"
