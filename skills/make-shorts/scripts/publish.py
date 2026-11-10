#!/usr/bin/env python3
"""완성본을 한 파일짜리 HTML 로 감싸고, 요청하면 바삭 페이지에 올린다.

    publish.py <프로젝트>                       → build/page.html 만 만든다
    publish.py <프로젝트> --post --group all    → 바삭 페이지에 올리고 링크를 출력한다

바삭 페이지는 HTML 만 받고 20MB 가 한도라, 영상을 base64 로 HTML 안에 넣는다.
올리는 것은 사내에 공개하는 일이다. 사용자가 그 건의 게시와 공개 범위를 정한 뒤에만 --post 를 쓴다.
"""
import base64, html, json, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path

LIMIT = 20 * 1024 * 1024
URL = "https://bs-portal.taile54389.ts.net/pages/api/items"


def main():
    args = sys.argv[1:]
    proj = Path(args[0]).resolve()
    build = proj / "build"
    script = json.loads((proj / "script.json").read_text())
    title = " ".join(script["title"])
    video = build / "final.mp4"
    # base64 는 4/3 배로 커진다. 넘치면 화질을 낮춰 다시 인코딩한다.
    if video.stat().st_size * 4 / 3 > LIMIT - 300_000:
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)]))
        kbps = int((LIMIT - 300_000) * 0.75 * 8 / dur / 1000) - 200
        small = build / "final.small.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-c:v", "libx264", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(small)], check=True)
        video = small
        print(f"20MB 한도에 맞춰 {kbps}kbps 로 다시 인코딩했다: {video}")
    b64 = base64.b64encode(video.read_bytes()).decode()
    lines = "".join(f"<li>{html.escape(b['text'])}</li>" for b in script["beats"])
    sources = "".join(f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener">{html.escape(s["title"])}</a></li>' for s in script.get("sources", []))
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in script.get("notes", []))
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#0b0f10;--fg:#eef2f2;--sub:#9fb0b3;--line:#22302f;--acc:#E4FF0A}}
@media (prefers-color-scheme: light){{:root{{--bg:#f6f8f7;--fg:#111a19;--sub:#566462;--line:#d9e2e0;--acc:#3a6b00}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:Pretendard,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;line-height:1.6}}
main{{max-width:980px;margin:0 auto;padding:24px 16px 64px;display:grid;gap:28px;grid-template-columns:minmax(0,360px) minmax(0,1fr);align-items:start}}
@media (max-width:760px){{main{{grid-template-columns:1fr}}}}
video{{width:100%;aspect-ratio:9/16;background:#000;border-radius:14px;position:sticky;top:16px}}
h1{{font-size:26px;line-height:1.3;margin:0 0 4px}} h2{{font-size:15px;color:var(--sub);margin:24px 0 8px;font-weight:700}}
ol,ul{{margin:0;padding-left:20px}} li{{margin:4px 0}} a{{color:var(--acc)}}
.meta{{color:var(--sub);font-size:14px}}
</style></head><body><main>
<video controls playsinline preload="metadata" src="data:video/mp4;base64,{b64}"></video>
<section>
<h1>{html.escape(title)}</h1>
<div class="meta">{html.escape(script.get("mode", ""))} 모드 · 문장 {len(script["beats"])}개</div>
<h2>대본</h2><ol>{lines}</ol>
{f"<h2>참고 자료</h2><ul>{sources}</ul>" if sources else ""}
{f"<h2>제작 메모</h2><ul>{notes}</ul>" if notes else ""}
</section></main></body></html>"""
    out = build / "page.html"
    out.write_text(page)
    size = out.stat().st_size
    print(f"{out} ({size / 1048576:.1f}MB)")
    if size > LIMIT:
        raise SystemExit("20MB 를 넘는다. 영상을 줄여야 한다.")
    if "--post" not in args:
        return
    group = args[args.index("--group") + 1]
    q = urllib.parse.urlencode({"group": group, "title": title, "description": script.get("description", "코드로 만든 해설 쇼츠")})
    req = urllib.request.Request(f"{URL}?{q}", data=out.read_bytes(), method="POST", headers={"X-Basak-Pages": "1", "Content-Type": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"올리기 실패 {e.code}: {e.read().decode()}")


main()
