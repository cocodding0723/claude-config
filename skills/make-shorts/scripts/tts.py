# /// script
# requires-python = ">=3.10"
# dependencies = ["edge-tts>=7.2"]
# ///
"""script.json 을 읽어 나레이션과 타임라인을 만든다.

    uv run tts.py <프로젝트>

만드는 것: build/narration.wav, build/timeline.json
말 속도는 script.json 의 sps(초당 음절) 목표에 맞게 한 번 재합성해서 맞춘다.
"""
import asyncio, json, re, subprocess, sys
from pathlib import Path

import edge_tts

DEFAULTS = {
    "gamedev": dict(voice="ko-KR-InJoonNeural", sps=6.4, gap=0.32, lead=0.15, tail=0.6, cap_chars=13, cap_lines=2),
    "codingapple": dict(voice="ko-KR-InJoonNeural", sps=7.4, gap=0.12, lead=0.05, tail=0.4, cap_chars=11, cap_lines=2),
}
HANGUL = re.compile(r"[가-힣]")
NORM = re.compile(r"[^0-9A-Za-z가-힣]")


async def synth(text, voice, rate, out):
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(out, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append(dict(text=ch["text"], start=s, end=s + ch["duration"] / 1e7))
    if not words:
        raise SystemExit(f"음성 합성 결과에 낱말 시각이 없다: {text!r}")
    return words


async def synth_all(beats, voice, rate, tmp):
    sem = asyncio.Semaphore(4)

    async def one(i, b):
        async with sem:
            for attempt in range(3):
                try:
                    return await synth(b["say"], voice, rate, tmp / f"b{i:02d}.mp3")
                except Exception as e:  # 비공식 서비스라 가끔 끊긴다
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5)

    return await asyncio.gather(*[one(i, b) for i, b in enumerate(beats)])


def token_times(text, words):
    """화면에 쓸 원문 어절마다 시각을 붙인다. 합성기가 낱말을 다르게 쪼개도(숫자 읽기 등) 글자 비율로 맞춘다."""
    chars = []  # 합성기 낱말을 글자 단위 시각으로 편다
    for w in words:
        n = max(1, len(NORM.sub("", w["text"])))
        for k in range(n):
            chars.append((w["start"] + (w["end"] - w["start"]) * k / n, w["start"] + (w["end"] - w["start"]) * (k + 1) / n))
    toks = text.split()
    lens = [max(1, len(NORM.sub("", t))) for t in toks]
    total, pos, out = sum(lens), 0, []
    for t, n in zip(toks, lens):
        a = min(len(chars) - 1, round(pos / total * len(chars)))
        b = min(len(chars) - 1, max(a, round((pos + n) / total * len(chars)) - 1))
        out.append(dict(text=t, start=chars[a][0], end=chars[b][1]))
        pos += n
    return out


def chunk(toks, max_chars, max_lines):
    """어절을 자막 덩어리로 묶는다. 쉼표·긴 쉼에서 끊고, 한 덩어리는 max_lines 줄을 넘지 않는다."""
    groups, cur = [], []
    limit = max_chars * max_lines
    for i, t in enumerate(toks):
        cur.append(t)
        length = len(" ".join(x["text"] for x in cur))
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is None:
            break
        pause = nxt["start"] - t["end"]
        would = length + 1 + len(nxt["text"])
        if would > limit or (t["text"].endswith((",", "…")) and length >= 5) or (pause > 0.22 and length >= 6) or (length >= max_chars and would > max_chars + 4):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    # 한두 글자짜리 꼬리는 앞 덩어리에 붙인다
    if len(groups) > 1 and len("".join(x["text"] for x in groups[-1])) <= 3 and len(" ".join(x["text"] for x in groups[-2] + groups[-1])) <= limit:
        tail = groups.pop()
        groups[-1].extend(tail)
    caps = []
    for g in groups:
        text = " ".join(x["text"] for x in g)
        if len(text) > max_chars and max_lines > 1:  # 가운데에 가장 가까운 띄어쓰기에서 줄을 나눈다
            cuts = [m.start() for m in re.finditer(" ", text)]
            if cuts:
                c = min(cuts, key=lambda p: abs(p - len(text) / 2))
                text = text[:c] + "\n" + text[c + 1:]
        caps.append(dict(text=text, start=g[0]["start"], end=g[-1]["end"]))
    return caps


def main():
    proj = Path(sys.argv[1]).resolve()
    script = json.loads((proj / "script.json").read_text())
    cfg = {**DEFAULTS[script["mode"]], **{k: script[k] for k in ("voice", "sps", "gap", "lead", "tail", "cap_chars", "cap_lines") if k in script}}
    beats = script["beats"]
    for b in beats:
        b["say"] = b.get("say") or b["text"]  # say: 화면 글자와 다르게 읽혀야 할 때(예: "60fps" → "육십 프레임")
    build = proj / "build"
    tmp = build / "tts"
    tmp.mkdir(parents=True, exist_ok=True)

    def speed(all_words):
        syl = sum(len(HANGUL.findall(b["say"])) for b in beats)
        dur = sum(w[-1]["end"] - w[0]["start"] for w in all_words)
        return syl / dur

    rate = script.get("rate")
    if rate:
        words = asyncio.run(synth_all(beats, cfg["voice"], rate, tmp))
    else:
        words = asyncio.run(synth_all(beats, cfg["voice"], "+0%", tmp))
        pct = round((cfg["sps"] / speed(words) - 1) * 100)
        pct = max(-30, min(60, pct))
        if abs(pct) >= 3:
            rate = f"{pct:+d}%"
            words = asyncio.run(synth_all(beats, cfg["voice"], rate, tmp))
        else:
            rate = "+0%"

    # 문장마다 앞뒤 무음을 잘라 이어 붙인다. 합성기가 붙이는 꼬리 무음이 제각각이라 쉼 길이를 직접 정한다.
    t, tl_beats, tl_words, caps, inputs, filt = cfg["lead"], [], [], [], [], []
    for i, (b, ws) in enumerate(zip(beats, words)):
        a, e = max(0, ws[0]["start"] - 0.03), ws[-1]["end"] + 0.06
        gap = b.get("gap", cfg["gap"]) if i < len(beats) - 1 else cfg["tail"]
        inputs += ["-i", str(tmp / f"b{i:02d}.mp3")]
        filt.append(f"[{i}:a]atrim={a:.3f}:{e:.3f},asetpts=PTS-STARTPTS,aresample=48000,apad=pad_dur={gap:.3f}[a{i}]")
        off = t - a
        toks = token_times(b["text"], ws)
        for x in toks:
            x["start"] += off; x["end"] += off
        tl_words += toks
        cs = chunk(toks, cfg["cap_chars"], cfg["cap_lines"])
        tl_beats.append(dict(i=i, scene=b["scene"], text=b["text"], start=t, end=t + (e - a)))
        caps.append(cs)
        t += (e - a) + gap
    duration = t
    # 자막은 다음 덩어리가 뜰 때까지 남긴다. 문장의 마지막 덩어리는 문장이 끝난 직후 내린다.
    flat = []
    for bi, cs in enumerate(caps):
        for k, c in enumerate(cs):
            c["end"] = cs[k + 1]["start"] if k + 1 < len(cs) else tl_beats[bi]["end"] + min(0.25, cfg["gap"] * 0.6)
            flat.append({k2: (round(v, 3) if isinstance(v, float) else v) for k2, v in c.items()})

    # 같은 scene 이 이어지면 한 장면으로 묶는다
    scenes = []
    for b in tl_beats:
        if scenes and scenes[-1]["id"] == b["scene"]:
            scenes[-1]["end"] = b["end"]
        else:
            scenes.append(dict(id=b["scene"], key=f"{b['scene']}#{len(scenes)}", start=b["start"], end=b["end"]))
        b["scene"] = scenes[-1]["key"]
    for k, s in enumerate(scenes):  # 장면 경계는 다음 문장이 시작하는 순간
        s["start"] = 0.0 if k == 0 else scenes[k]["start"]
        s["end"] = scenes[k + 1]["start"] if k + 1 < len(scenes) else duration
    for k in range(1, len(scenes)):
        scenes[k - 1]["end"] = scenes[k]["start"]

    n = len(beats)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex",
                    ";".join(filt) + ";" + "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1,adelay={int(cfg['lead'] * 1000)}:all=1[o]",
                    "-map", "[o]", "-ac", "2", "-ar", "48000", str(build / "narration.wav")], check=True)

    r3 = lambda d: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}
    (build / "timeline.json").write_text(json.dumps(dict(
        duration=round(duration, 3), voice=cfg["voice"], rate=rate,
        beats=[r3(b) for b in tl_beats], scenes=[r3(s) for s in scenes], captions=flat, words=[r3(w) for w in tl_words],
    ), ensure_ascii=False, indent=1))

    syl = sum(len(HANGUL.findall(b["say"])) for b in beats)
    print(f"길이 {duration:.1f}초 · 문장 {n}개 · 한글 {syl}음절 · 말 속도 {speed(words):.2f}음절/초(목표 {cfg['sps']}) · rate {rate} · 자막 {len(flat)}덩어리")
    print("장면:")
    for s in scenes:
        print(f"  {s['start']:6.2f}–{s['end']:6.2f}  {s['id']}")


main()
