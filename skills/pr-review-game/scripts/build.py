import base64,subprocess,sys,os
tpl,out,assets=sys.argv[1:4]
s=open(tpl).read()
b=lambda f:base64.b64encode(open(os.path.join(assets,f),'rb').read()).decode()
open('chars.txt','w').write(''.join(sorted(set(s)))+'0123456789%·→…')
subprocess.run(['pyftsubset',os.path.join(assets,'Jua.ttf'),'--text-file=chars.txt','--flavor=woff','--output-file=jua.woff'],check=True)
m={'FONT':base64.b64encode(open('jua.woff','rb').read()).decode(),'ROOM':b('room.jpg'),'BUBBLE':b('speech_bubble.png'),'SCROLL':b('scroll.png'),'CANDLE':b('candle.png'),
 'S_CARD':'data:audio/mp4;base64,'+b('ScrollingPaper.m4a'),'S_OK':'data:audio/mp4;base64,'+b('CandleLight.m4a'),'S_NO':'data:audio/mp4;base64,'+b('WrongOrgan.m4a'),'S_BURN':'data:audio/mpeg;base64,'+b('fire3.mp3')}
for k,v in m.items(): s=s.replace('{{'+k+'}}',v)
open(out,'w').write(s); print(len(s))
