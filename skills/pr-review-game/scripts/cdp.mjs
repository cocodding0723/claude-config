// 실제 좌표 입력만으로 플레이: Input.dispatchMouseEvent / dispatchTouchEvent
import {spawn} from "node:child_process";import {mkdtempSync} from "node:fs";
const [,,file,mode,W,H,plan]=process.argv;
const ud=mkdtempSync("/tmp/cdp-");const port=9300+Math.floor(Math.random()*500);
const ch=spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",["--headless=new",`--remote-debugging-port=${port}`,`--user-data-dir=${ud}`,`--window-size=${W},${H}`,"about:blank"],{stdio:"ignore"});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let ws;for(let i=0;i<50;i++){try{const l=await (await fetch(`http://127.0.0.1:${port}/json`)).json();const p=l.find(x=>x.type=="page");ws=new WebSocket(p.webSocketDebuggerUrl);break}catch{await sleep(200)}}
await new Promise(r=>ws.onopen=r);let id=0;const pend={};const errs=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id&&pend[m.id]){pend[m.id](m.result);delete pend[m.id]}if(m.method=="Runtime.exceptionThrown")errs.push(m.params.exceptionDetails.exception?.description?.slice(0,120));if(m.method=="Runtime.consoleAPICalled"&&m.params.type=="error")errs.push("console.error")};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}))});
const ev=async x=>(await send("Runtime.evaluate",{expression:x,returnByValue:true})).result.value;
await send("Runtime.enable");await send("Page.enable");
const touch=mode=="touch";
await send("Emulation.setDeviceMetricsOverride",{width:+W,height:+H,deviceScaleFactor:1,mobile:touch});
if(touch)await send("Emulation.setTouchEmulationEnabled",{enabled:true,maxTouchPoints:1});
await send("Page.navigate",{url:"file://"+file});await sleep(1200);
const log=[];const ok=(n,c)=>{log.push((c?"OK   ":"FAIL ")+n);};
async function pt(sel,noscroll){return ev(`(()=>{const e=${sel};if(!e)return null;if(!${noscroll?1:0})e.scrollIntoView({block:"center"});const r=e.getBoundingClientRect();const x=r.x+Math.min(r.width/2,40),y=r.y+r.height/2;const top=document.elementFromPoint(x,y);return {x,y,hit:top===e||e.contains(top),vis:x>=0&&y>=0&&x<=innerWidth&&y<=innerHeight}})()`)}
async function tap(sel,name){const p=await pt(sel);if(!p){ok(name+" (요소 없음) "+sel.slice(0,90),false);return false}
 if(!p.hit||!p.vis)ok(name+" (가려짐/화면 밖)",false);
 if(touch){await send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x:p.x,y:p.y}]});await send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]})}
 else{for(const t of ["mouseMoved","mousePressed","mouseReleased"])await send("Input.dispatchMouseEvent",{type:t,x:p.x,y:p.y,button:"left",clickCount:1})}
 await sleep(150);return true}
async function drag(a,b){await pt(`document.querySelector(".say")`);const p=await pt(a,1),q=await pt(b,1);
 if(touch){await send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x:p.x,y:p.y}]});for(let k=1;k<=8;k++)await send("Input.dispatchTouchEvent",{type:"touchMove",touchPoints:[{x:p.x+(q.x-p.x)*k/8,y:p.y+(q.y-p.y)*k/8}]});await send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]})}
 else{await send("Input.dispatchMouseEvent",{type:"mousePressed",x:p.x,y:p.y,button:"left",clickCount:1});for(let k=1;k<=8;k++)await send("Input.dispatchMouseEvent",{type:"mouseMoved",x:p.x+(q.x-p.x)*k/8,y:p.y+(q.y-p.y)*k/8,button:"left"});await send("Input.dispatchMouseEvent",{type:"mouseReleased",x:q.x,y:q.y,button:"left",clickCount:1})}
 await sleep(150)}
const card=w=>`[...document.querySelectorAll(".cardw")].find(c=>c.textContent===${JSON.stringify(w)}&&!c.classList.contains("used"))`;
const slot=n=>`document.querySelectorAll(".slot")[${n}]`;
const slotText=n=>ev(`document.querySelectorAll(".slot")[${n}].textContent`);
const G=await ev("G.map(g=>({ans:g.ans.map(a=>a[0]),decoy:g.decoy,risk:g.risk||null,tut:g.tut}))");
await tap(`document.getElementById("who")`,"이름칸");await send("Input.insertText",{text:"테스터"});
await tap(`document.getElementById("go")`,"시작");ok("시작 → 손님1",await ev(`!!document.querySelector(".slot")`));
const steps=JSON.parse(plan);
for(let i=0;i<G.length;i++){const g=G[i],s=steps[i]||{};const tag=`손님${i+1}`;
 if(s.pause){await tap(`document.getElementById("pz")`,tag+" 멈춤");ok(tag+" 멈춤 창",await ev(`document.getElementById("pause").classList.contains("on")`));await tap(`document.getElementById("resume")`,tag+" 계속");ok(tag+" 계속",!(await ev(`document.getElementById("pause").classList.contains("on")`)))}
 if(s.hint){await tap(`document.getElementById("hintb")`,tag+" 힌트");ok(tag+" 힌트 줄",await ev(`!!document.querySelector("#code .h")`))}
 if(s.burn){await tap(`document.getElementById("burn")`,tag+" 건너뛰기")}
 else{for(let n=0;n<g.ans.length;n++){if(g.tut===n)continue;
   if(s.swap){await tap(card(g.decoy[0]),"오답카드");await tap(slot(n),"빈칸");await tap(card(g.ans[n]),"정답카드");await tap(slot(n),"빈칸");ok(tag+" 오답→정답 교체",await slotText(n)===g.ans[n])}
   else if(s.remove){await tap(card(g.ans[n]),"카드");await tap(slot(n),"빈칸");await tap(slot(n),"빈칸 빼기");ok(tag+" 카드 빼기",(await ev(`!document.querySelectorAll(".slot")[${n}].dataset.c`)));await tap(card(g.ans[n]),"카드");await tap(slot(n),"빈칸");ok(tag+" 다시 넣기",await slotText(n)===g.ans[n])}
   else if(s.wrong){await tap(card(g.decoy[0]),"오답카드");await tap(slot(n),"빈칸")}
   else if(s.drag){await drag(card(g.ans[n]),slot(n));ok(tag+" 드래그",await slotText(n)===g.ans[n])}
   else{await tap(card(g.ans[n]),"카드");await tap(slot(n),"빈칸");ok(tag+" 탭 끼우기",await slotText(n)===g.ans[n])}}
  ok(tag+" 안내 문구",/고해 받기/.test(await ev(`(document.getElementById("need")||{}).textContent||""`)));
  await tap(`document.getElementById("sub")`,tag+" 고해 받기");
  ok(tag+(s.wrong?" 오답 판정":" 정답 판정"),s.wrong?/아깝다/.test(await ev(`document.getElementById("fb").textContent`)):/사함/.test(await ev(`document.getElementById("fb").textContent`)));
  if(g.risk&&s.risk){const L=s.risk=="ok"?g.risk.lines[0]:g.risk.cand.find(c=>!g.risk.lines.includes(c));await tap(`document.getElementById("code").children[${L}]`,tag+" 위험 줄");ok(tag+" 위험 줄 "+s.risk,(await ev(`document.getElementById("bonus").textContent`)).includes(s.risk=="ok"?"+50":"초록"))}}
 ok(tag+" 다음 버튼 보임",!!(await pt(`document.getElementById("next")`)));
 await tap(`document.getElementById("next")`,tag+" 다음");}
ok("두루마리",await ev(`!!document.getElementById("result")`));await tap(`document.getElementById("cp")`,"복사");ok("복사 반응",/복사됨/.test(await ev(`document.getElementById("cp").textContent`)));
log.push("결과 "+await ev(`(document.getElementById("out")||{}).value?.split("\\n").slice(0,3).join(" / ")`));
log.push("예외 "+errs.length+" "+errs.join("|"));console.log(`== ${mode} ${W}x${H}\n`+log.join("\n"));ws.close();ch.kill();process.exit(0);
