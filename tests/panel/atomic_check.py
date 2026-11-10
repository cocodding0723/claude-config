"""원자 쓰기·잠금 확인. 사용: atomic_check.py <panel_collect.py 가 있는 폴더> (LRM_HUB_DIR 은 시험 샌드박스)
1) 쓰기 도중 실패(json.dump 가 반쯤 쓰고 예외) → 기존 panel-auto.json 온전, 임시 파일 없음
2) 교체는 같은 폴더 임시 파일 → os.replace 한 번. 기존 파일을 제자리에서 고치지 않음(하드 링크 증인이 그대로)
3) 결과 파일 권한 0600
4) main --once 수준에서 쓰기 실패 → 기존 파일 바이트 동일·잠금 해제·로그 write fail
5) 실행 중 잠금 시각 갱신(keep_lock), 남의 잠금은 갱신 안 함
실패하면 첫 실패를 한 줄로 출력하고 종료 1."""
import glob
import json
import os
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import panel_collect as pc  # noqa: E402


def fail(msg):
    print(f"FAIL {msg}")
    sys.exit(1)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class HalfJson:
    """dump 만 반쯤 쓰고 실패. load 는 진짜(main 의 이전 값 읽기용)"""
    load = staticmethod(json.load)
    loads = staticmethod(json.loads)

    @staticmethod
    def dump(data, f, **kw):
        f.write('{"version": 1, "sour')
        f.flush()
        raise OSError("disk full")


def no_tmp():
    return not glob.glob(os.path.join(pc.DIR, ".panel-auto.*.tmp"))


# 1) 쓰기 도중 실패
old = {"version": 1, "updatedAt": 1, "sources": {"keep": True}}
pc.write_atomic(old)
pc.json = HalfJson
try:
    pc.write_atomic({"version": 1, "updatedAt": 2, "sources": {}})
    fail("예외가 나지 않음")
except OSError:
    pass
finally:
    pc.json = json
try:
    with open(pc.OUT, encoding="utf-8") as f:
        now = json.load(f)
except (OSError, ValueError) as e:
    fail(f"기존 파일 손상: {type(e).__name__}")
if now != old or not no_tmp():
    fail(f"내용 변경={now != old} 임시 파일 잔존={not no_tmp()}")

# 2) os.replace(임시 → 결과) 한 번, 제자리 쓰기 없음
witness = os.path.join(pc.DIR, "witness")
os.link(pc.OUT, witness)
before = read_bytes(witness)
calls, real_replace = [], os.replace


def spy(src, dst, *a, **kw):
    calls.append((src, dst))
    return real_replace(src, dst, *a, **kw)


pc.os.replace = spy
try:
    pc.write_atomic({"version": 1, "updatedAt": 3, "sources": {}})
finally:
    pc.os.replace = real_replace
moved = [c for c in calls if c[1] == pc.OUT]
if len(moved) != 1 or not os.path.samefile(os.path.dirname(moved[0][0]), pc.DIR) or moved[0][0] == pc.OUT:
    fail(f"결과 파일 교체가 같은 폴더 임시 파일의 os.replace 1회가 아님(호출 {len(moved)}회)")
if read_bytes(witness) != before:
    fail("기존 파일을 제자리에서 고침(하드 링크 증인이 바뀜)")
if json.loads(read_bytes(pc.OUT)).get("updatedAt") != 3:
    fail("새 내용이 결과 파일에 없음")

# 3) 권한
mode = os.stat(pc.OUT).st_mode & 0o777
if mode != 0o600:
    fail(f"결과 파일 권한 {oct(mode)} (0600 이어야 함)")

# 4) main 수준 쓰기 실패
keep = read_bytes(pc.OUT)
pc.collect = lambda *a, **kw: ({"version": 1, "updatedAt": 4, "sources": {}}, "off")
pc.json = HalfJson
try:
    rc = pc.main(["--once"])
finally:
    pc.json = json
if rc != 0:
    fail(f"쓰기 실패에 종료 코드 {rc}")
if not os.path.exists(pc.OUT) or read_bytes(pc.OUT) != keep:
    fail("쓰기 실패 뒤 기존 파일이 없어지거나 바뀜")
if os.path.isdir(pc.LOCK) or not no_tmp():
    fail("쓰기 실패 뒤 잠금·임시 파일이 남음")
with open(pc.LOG, encoding="utf-8") as f:
    if "write fail" not in f.read():
        fail("로그에 write fail 없음")

# 5) 잠금 갱신
os.mkdir(pc.LOCK)
os.utime(pc.LOCK, (1, 1))
pc.LOCK_BEAT = 0.05
stop = threading.Event()
t = threading.Thread(target=pc.keep_lock, args=(stop, pc.lock_owner()), daemon=True)
t.start()
time.sleep(0.3)
stop.set()
t.join(2)
fresh = time.time() - os.path.getmtime(pc.LOCK) < 5
if not fresh or t.is_alive():
    fail(f"실행 중 잠금 시각이 갱신되지 않음(갱신={fresh}, 멈춤={not t.is_alive()})")
# 남의 잠금(주인 값이 다름)은 갱신하지 않고 갱신 스레드를 끝냄
os.utime(pc.LOCK, (1, 1))
t = threading.Thread(target=pc.keep_lock, args=(threading.Event(), ("other",)), daemon=True)
t.start()
t.join(2)
other_fresh = os.path.getmtime(pc.LOCK) > 1
os.rmdir(pc.LOCK)
if other_fresh or t.is_alive():
    fail(f"남의 잠금을 갱신함(갱신={other_fresh}, 멈춤={not t.is_alive()})")
print("ok")
