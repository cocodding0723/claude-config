"""원자 쓰기 확인. 사용: atomic_check.py <panel_collect.py 가 있는 폴더> (LRM_HUB_DIR 은 시험 샌드박스)
쓰기 도중 실패를 흉내 내어(json.dump 가 반쯤 쓰고 예외) 기존 panel-auto.json 이 온전한지, 임시 파일이 남지 않는지 봄."""
import glob
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import panel_collect as pc  # noqa: E402

old = {"version": 1, "updatedAt": 1, "sources": {"keep": True}}
pc.write_atomic(old)


class HalfJson:
    @staticmethod
    def dump(data, f, **kw):
        f.write('{"version": 1, "sour')
        f.flush()
        raise OSError("disk full")


real = pc.json
pc.json = HalfJson
try:
    pc.write_atomic({"version": 1, "updatedAt": 2, "sources": {}})
    print("FAIL 예외가 나지 않음")
    sys.exit(1)
except OSError:
    pass
finally:
    pc.json = real
try:
    with open(pc.OUT, encoding="utf-8") as f:
        now = json.load(f)
except (OSError, ValueError) as e:
    print(f"FAIL 기존 파일 손상: {type(e).__name__}")
    sys.exit(1)
left = glob.glob(os.path.join(pc.DIR, ".panel-auto.*.tmp"))
if now != old or left:
    print(f"FAIL 내용 변경={now != old} 임시 파일 잔존={len(left)}")
    sys.exit(1)
print("ok")
