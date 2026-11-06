<script>
window.__err=[];addEventListener("error",e=>__err.push(e.message));
if(location.search.includes("nols")){Storage.prototype.getItem=Storage.prototype.setItem=function(){throw new Error("blocked")}}
setTimeout(()=>{const L=[];const ok=(n,c)=>L.push((c?"OK ":"FAIL ")+n);try{
 $("who").value="테스터";$("go").click();ok("시작",!!document.querySelector(".slot"));
 for(let i=0;i<G.length;i++){const g=G[i];const slots=[...document.querySelectorAll(".slot")];
  slots.forEach((s,n)=>{if(s.dataset.c)return;const c=[...document.querySelectorAll(".cardw")].find(x=>x.textContent===g.ans[n][0]);
   if(n%2==0){c.dispatchEvent(new PointerEvent("pointerdown",{clientX:1,clientY:1,bubbles:true}));dispatchEvent(new PointerEvent("pointerup",{clientX:1,clientY:1}));s.click();ok(`손님${i+1} 탭 끼우기`,s.textContent===g.ans[n][0])}
   else{const r=c.getBoundingClientRect(),t=s.getBoundingClientRect();c.dispatchEvent(new PointerEvent("pointerdown",{clientX:r.x+5,clientY:r.y+5,bubbles:true}));dispatchEvent(new PointerEvent("pointermove",{clientX:t.x+20,clientY:t.y+8}));dispatchEvent(new PointerEvent("pointerup",{clientX:t.x+t.width/2,clientY:t.y+t.height/2}));ok(`손님${i+1} 드래그 끼우기`,s.textContent===g.ans[n][0])}});
  $("sub").click();ok(`손님${i+1} 판정`,/사함|보너스/.test($("fb").textContent));
  if(g.risk){$("code").children[g.risk.lines[0]].click();ok("위험 줄",/날카로움 \+50/.test($("fb").textContent))}
  $("next").click()}
 ok("두루마리",!!$("result"));$("cp").click();ok("복사 버튼",true);L.push("텍스트1줄: "+$("out").value.split("\n")[0]);
}catch(e){L.push("EXC "+e.message)}L.push("오류 "+__err.length+" "+__err.join("|"));document.body.setAttribute("data-log",L.join(" ;; "))},500);
</script></body>
