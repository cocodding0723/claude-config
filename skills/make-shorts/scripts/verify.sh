#!/bin/bash
# 완성본을 기계로 재고, 눈으로 볼 타일을 만든다. 타일(build/check/sheet_*.jpg)은 반드시 직접 열어 본다.
#   verify.sh <프로젝트> [타일 간격 초=1.5]
set -uo pipefail
P="$(cd "$1" && pwd)"; STEP="${2:-1.5}"; F="$P/build/final.mp4"; C="$P/build/check"; mkdir -p "$C"; rm -f "$C"/sheet_*.jpg
FAIL=0; bad() { echo "✗ $1"; FAIL=1; }; ok() { echo "✓ $1"; }
IFS=, read -r W H FPS _ < <(ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -of csv=p=0 "$F")
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F"); SIZE=$(stat -f%z "$F")
[ "$W" = 1080 ] && [ "$H" = 1920 ] && ok "해상도 ${W}x${H} · $FPS fps" || bad "해상도 ${W}x${H} (1080x1920 이어야 함)"
awk -v d="$DUR" 'BEGIN{exit !(d<=180)}' && ok "길이 ${DUR}초" || bad "길이 ${DUR}초 — 쇼츠 한도(3분) 초과"
echo "  크기 $(echo "scale=1;$SIZE/1048576" | bc)MB"
L=$(ffmpeg -hide_banner -nostats -i "$F" -af ebur128=peak=true -f null - 2>&1 | tail -16)
I=$(echo "$L" | awk '$1=="I:"{print $2}' | tail -1); TP=$(echo "$L" | awk '/Peak:/{print $2}' | tail -1)
awk -v i="$I" 'BEGIN{exit !(i>=-16.5 && i<=-9)}' && ok "음량 $I LUFS · 피크 $TP dBTP" || bad "음량 $I LUFS (목표 -14 안팎)"
SIL=$(ffmpeg -hide_banner -nostats -i "$F" -af silencedetect=noise=-45dB:d=1.2 -f null - 2>&1 | grep -c silence_end || true)
[ "$SIL" -le 1 ] && ok "1.2초 넘는 무음 ${SIL}곳" || bad "1.2초 넘는 무음이 ${SIL}곳 — 말이 끊긴 구간 확인"
# 빈 화면: 모드가 내용을 두는 영역이 통째로 검으면 장면이 비어 있는 것이다
MODE=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['mode'])" "$P/script.json")
[ "$MODE" = codingapple ] && AREA="1080:950:0:100" || AREA="1080:1000:0:440"
BLK=$(ffmpeg -hide_banner -nostats -i "$F" -vf "crop=$AREA,blackdetect=d=1.5:pix_th=0.06:pic_th=0.995" -an -f null - 2>&1 | grep -c black_start || true)
[ "$BLK" = 0 ] && ok "빈 화면 없음" || bad "내용 영역이 1.5초 넘게 거의 비는 구간 ${BLK}곳"
ffmpeg -y -loglevel error -i "$F" -vf "fps=1/$STEP,scale=270:-1,tile=6x2" "$C/sheet_%02d.jpg"
echo "타일: $(ls "$C"/sheet_*.jpg | tr '\n' ' ')"
exit $FAIL
