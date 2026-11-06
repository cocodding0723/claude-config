// v4 흐름 검사: 실제 좌표 입력(마우스/터치)으로 로비→문항→결과까지 진행하고 스크린샷.
// 사용: node cdp-v4.mjs <url> <mouse|touch> <W> <H> <shot.png> [wrongCsv=1]
import {spawn} from "node:child_process";import {mkdtempSync,writeFileSync} from "node:fs";import {tmpdir} from "node:os";import {join} from "node:path";
const [,,url,mode,W,H,shot,wrongCsv="1"]=process.argv;const wrong=wrongCsv.split(",").map(Number);
const ud=mkdtempSync(join(tmpdir(),"cdp4-"));const port=9300+Math.floor(Math.random()*500);
const ch=spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",["--headless=new",`--remote-debugging-port=${port}`,`--user-data-dir=${ud}`,"--autoplay-policy=no-user-gesture-required",`--window-size=${W},${H}`,"about:blank"],{stdio:"ignore"});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let ws;for(let i=0;i<50;i++){try{const l=await (await fetch(`http://127.0.0.1:${port}/json`)).json();const p=l.find(x=>x.type=="page");ws=new WebSocket(p.webSocketDebuggerUrl);break}catch{await sleep(200)}}
await new Promise(r=>ws.onopen=r);let id=0;const pend={};const errs=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id&&pend[m.id]){pend[m.id](m.result);delete pend[m.id]}
 if(m.method=="Runtime.exceptionThrown")errs.push(m.params.exceptionDetails.exception?.description?.slice(0,160));
 if(m.method=="Runtime.consoleAPICalled"&&m.params.type=="error")errs.push("console.error "+JSON.stringify(m.params.args).slice(0,160));
 if(m.method=="Log.entryAdded"&&m.params.entry.level=="error")errs.push("log "+m.params.entry.text.slice(0,160))};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}))});
const ev=async x=>(await send("Runtime.evaluate",{expression:x,returnByValue:true})).result.value;
await send("Runtime.enable");await send("Page.enable");await send("Log.enable");
const touch=mode=="touch";
await send("Emulation.setDeviceMetricsOverride",{width:+W,height:+H,deviceScaleFactor:touch?2:1,mobile:touch});
if(touch)await send("Emulation.setTouchEmulationEnabled",{enabled:true,maxTouchPoints:1});
await send("Page.navigate",{url});await sleep(1200);
const log=[];const ok=(n,c)=>log.push((c?"OK   ":"FAIL ")+n);
async function tap(sel,name){const p=await ev(`(()=>{const e=${sel};if(!e)return null;e.scrollIntoView({block:"center"});const r=e.getBoundingClientRect();const x=r.x+r.width/2,y=r.y+r.height/2;const t=document.elementFromPoint(x,y);return {x,y,hit:t===e||e.contains(t)}})()`);
 if(!p){ok(name+" 요소 없음",false);return}if(!p.hit)ok(name+" 가려짐",false);
 if(touch){await send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x:p.x,y:p.y}]});await send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]})}
 else for(const t of ["mouseMoved","mousePressed","mouseReleased"])await send("Input.dispatchMouseEvent",{type:t,x:p.x,y:p.y,button:"left",clickCount:1});
 await sleep(200)}
const n=await ev("G.q.length");ok("가로 넘침 없음(로비)",await ev("document.documentElement.scrollWidth<=innerWidth"));
await tap(`document.getElementById("who")`,"이름");await send("Input.insertText",{text:"테스터"});
await tap(`document.getElementById("go")`,"시작");ok("Q1 표시",await ev(`!!document.querySelector(".opt")`));
for(let i=0;i<n;i++){await sleep(700);const ans=await ev(`G.q[${i}].ans`);const k=wrong.includes(i+1)?(ans+1)%(await ev(`G.q[${i}].opts.length`)):ans;
 if(i==0){await tap(`document.getElementById("memoB")`,"메모");await send("Input.insertText",{text:"Q1 질문 메모"})}
 ok(`Q${i+1} 가로 넘침 없음`,await ev("document.documentElement.scrollWidth<=innerWidth"));
 await tap(`document.querySelector('.opt[data-i="${k}"]')`,`Q${i+1} 보기`);
 const fb=await ev(`document.getElementById("fb").textContent`);ok(`Q${i+1} ${k==ans?"정답":"오답"} 판정`,k==ans?/정답/.test(fb):/아깝다/.test(fb));
 await sleep(400);await tap(`document.getElementById("next")`,`Q${i+1} 다음`)}
await sleep(900);ok("결과창",await ev(`!!document.getElementById("result")`));
ok("도식 SVG",await ev(`!!document.querySelector("#dia svg")`));
await tap(`document.querySelector("#dia g.entity[data-hit]")`,"도식 노드");ok("노드 코드 표시",await ev(`document.getElementById("nodeCode").classList.contains("on")`));
for(const k of ["before","problem","after"]){await tap(`document.querySelector('.tab[data-k="${k}"]')`,"탭 "+k);ok("탭 "+k+" SVG",await ev(`!!document.querySelector("#dia svg")`))}
await tap(`document.querySelector('.tab[data-k="structure"]')`,"탭 structure");
await tap(`document.getElementById("cp")`,"복사");ok("복사 반응",/복사됨/.test(await ev(`document.getElementById("cp").textContent`)));
ok("가로 넘침 없음(결과)",await ev("document.documentElement.scrollWidth<=innerWidth"));
log.push("복사 텍스트: "+(await ev(`document.getElementById("out").value`)).replace(/\n/g," / "));
await ev("scrollTo(0,0)");await sleep(1500);
const m=await send("Page.getLayoutMetrics");const h=Math.ceil(m.cssContentSize.height);
const img=await send("Page.captureScreenshot",{format:"png",captureBeyondViewport:true,clip:{x:0,y:0,width:+W,height:h,scale:1}});
writeFileSync(shot,Buffer.from(img.data,"base64"));log.push("샷 "+shot+" "+W+"x"+h);
log.push("오류 "+errs.length+" "+errs.join(" | "));console.log(`== ${mode} ${W}\n`+log.join("\n"));ws.close();ch.kill();process.exit(0);
