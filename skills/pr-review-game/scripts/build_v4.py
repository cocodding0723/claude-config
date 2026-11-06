#!/usr/bin/env python3
"""v4 빌드: 데이터 JSON + PUML 렌더(로컬 plantuml) + 선택 매체를 템플릿에 인라인.
사용: build_v4.py <data.json> <out.html> [--before a.png] [--after b.png] [--video smoke.mp4]
"""
import argparse, base64, json, os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, '..', 'assets')
PLANTUML = os.environ.get('PLANTUML', '/opt/homebrew/bin/plantuml')
MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.mp4': 'video/mp4', '.webm': 'video/webm'}

def render(src):
    # 외부 서버로 보내지 않음: 로컬 plantuml 파이프만 사용
    svg = subprocess.run([PLANTUML, '-tsvg', '-pipe'], input=src.encode(), capture_output=True, check=True).stdout.decode()
    svg = re.sub(r'<\?plantuml[^>]*\?>', '', svg)
    svg = re.sub(r'style="width:[^;]*;height:[^;]*;', 'style="', svg, count=1)
    svg = re.sub(r' width="[\d.]+px" height="[\d.]+px"', '', svg, count=1)
    return svg

def uri(p):
    return 'data:%s;base64,%s' % (MIME[os.path.splitext(p)[1].lower()], base64.b64encode(open(p, 'rb').read()).decode())

ap = argparse.ArgumentParser()
ap.add_argument('data'); ap.add_argument('out')
ap.add_argument('--before'); ap.add_argument('--after'); ap.add_argument('--video')
ap.add_argument('--template', default=os.path.join(ASSETS, 'template-v4.html'))
a = ap.parse_args()
d = json.load(open(a.data))
d['svg'] = {k: render(v) for k, v in d.get('puml', {}).items()}
d.pop('puml', None)
if a.before or a.after or a.video:
    d['media'] = {k: uri(v) for k, v in (('before', a.before), ('after', a.after), ('video', a.video)) if v}
tpl = open(a.template).read()
data = json.dumps(d, ensure_ascii=False).replace('</', '<\\/')
with tempfile.TemporaryDirectory() as t:
    chars = os.path.join(t, 'c.txt'); woff = os.path.join(t, 'f.woff')
    open(chars, 'w').write(''.join(sorted(set(tpl + data))) + '0123456789%')
    subprocess.run(['pyftsubset', os.path.join(ASSETS, 'Jua.ttf'), '--text-file=' + chars, '--flavor=woff', '--output-file=' + woff], check=True)
    font = base64.b64encode(open(woff, 'rb').read()).decode()
s = tpl.replace('{{FONT}}', font).replace('{{DATA}}', data)
open(a.out, 'w').write(s)
mb = len(s.encode()) / 1e6
print('%s %.2fMB' % (a.out, mb))
if mb > 3: sys.exit('3MB 초과: 매체를 줄일 것')
