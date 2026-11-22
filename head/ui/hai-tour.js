/* Spotlight onboarding. Targets the real HAI DOM; never sends requests or chooses settings. */
(() => {
  'use strict';
  const steps=[
    {target:'#sideTop',prepare:'sidebar',title:'Your chats live over here.',where:'☰ → chat sidebar',body:'Use New to start another conversation, or search for one you already had. On your phone, ☰ opens this list and ✕ closes it.',tip:'You can try the highlighted controls, or press Next.'},
    {target:'#gearBtn',mobileOnly:true,title:'Your phone keeps the controls under ⚙.',where:'Header → ⚙',body:'Tap here to open the model, effort, work, and view controls. HAI tucks them away so your conversation has more room.',tip:'Next will open the controls and show you each part.'},
    {target:'#model',prepare:'controls',title:'Choose a model—or leave it on Auto.',where:'Header → Models → Text',body:'Auto chooses from the measured options in the lab. Quick gives speed more weight. A pinned 27B choice keeps a visible queue if that model is busy.',tip:'In HAI, an explicit model choice is remembered for your account.'},
    {target:'#thinkAxis',prepare:'controls',title:'Effort controls the model’s thinking.',where:'Header → Effort',body:'Auto lets HAI choose. Off, Low, Medium, and Max set the chosen model’s own thinking effort. This is separate from the Work controls beside it.',tip:'Changing View later only changes what you see; it does not change this effort.'},
    {target:'#workAxis',prepare:'controls',title:'Think hard lives under Work.',where:'Header → Work',body:'Answer is an ordinary agent turn. Think hard adds review and refinement. Turn on Background to reveal a longer time budget—the old Absurd idea, with a clearer name.',tip:'The time is a maximum. HAI can finish earlier. Quick and Think hard pull in opposite directions.'},
    {target:'#viewMode',prepare:'controls',title:'Choose how much work you see.',where:'Header → View',body:'Normal keeps the steps and thoughts collapsed. Thinking opens the journal. Verbose opens both. Summary keeps the answer alone.',tip:'Try changing this selector and watch the conversation behind this callout.'},
    {target:'#f',title:'Keep talking while HAI works.',where:'The usual message box',body:'This is still where you send messages. During a run, a correction or extra requirement is picked up at HAI’s next natural step.',tip:'The tour never fills or sends a message for you. Use Next to keep exploring.'},
    {target:'#imageRenderer',prepare:'controls',title:'Pictures belong in the same chat.',where:'Header → Models → Images',body:'Pick an image preference here, then describe the picture in the normal message box. A usable saved identity model can make “me” mean you.',tip:'Uploaded-photo restyles currently use Auto. The pinned Flux.2 path reports that it cannot edit an uploaded image yet.'},
    {target:'#gearPanel',prepare:'gear',title:'The familiar utilities are under ⚙.',where:'Header → ⚙',body:'Compact context, New chat, and Forget memory live here. Upload is beside the message box for accounts with a private library. New chat keeps your saved long-term memory; Forget memory is the separate control that clears it.',tip:'Only controls available to your account appear in HAI.'},
    {target:'#planBtn',optional:true,title:'Your planning launcher is here.',where:'Header → Plan',body:'Turn a goal into a plan, revise it, and return to saved plans. Saving an ordinary user’s plan does not grant lab-admin execution permissions.',tip:'Your original planning tools are still here, in the current header.'},
    {target:'.branch[title^="Start a new chat"]',last:true,fallback:'#log',title:'Branch: take an answer in another direction.',where:'Under an answer → ⑂ Branch',body:'Every answer has a ⑂ Branch button. It starts a new chat that carries the conversation up to that answer, so you can try another idea without losing this one. The original chat stays exactly as it was, and the branch shows up in your chat list.',tip:'Good for “what if we tried it the other way?” — ask once, branch twice, compare.',fallbackTip:'Your conversation appears here. Once HAI answers, look for ⑂ Branch just under the answer.'}
  ];
  // A step's target, or its fallback when the target is not on screen (an empty chat has no ⑂ Branch yet).
  function pick(step,root){
    const all=Array.from(root.querySelectorAll(step.target)).filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden');
    const el=step.last?all[all.length-1]:all[0];
    if(el)return{el,fallback:false};
    const fb=step.fallback&&root.querySelector(step.fallback);
    return fb?{el:fb,fallback:true}:{el:null,fallback:false};
  }
  let active=null;
  const css=`
    :host{all:initial;position:fixed;inset:0;z-index:2147483000;pointer-events:none;color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
    *{box-sizing:border-box}.shade{position:fixed;background:rgba(4,7,14,.79);backdrop-filter:blur(1.5px);pointer-events:auto}
    .frame{position:fixed;pointer-events:none;border:2px solid #cdbaff;border-radius:11px;box-shadow:0 0 0 5px #cdbaff14,0 0 35px #aa93df38;transition:top .2s,left .2s,width .2s,height .2s}
    .card{position:fixed;pointer-events:auto;width:min(350px,calc(100vw - 24px));max-height:calc(100dvh - 24px);overflow:auto;overscroll-behavior:contain;background:#191d2b;color:#eee9fa;border:1px solid #bfa9ed55;border-radius:17px;padding:19px;box-shadow:0 18px 70px #0008}
    .top{display:flex;align-items:center;justify-content:space-between;gap:12px}.count{color:#baa8d8;font-size:10px;letter-spacing:.13em;text-transform:uppercase}.skip{background:none;border:0;color:#b9b2c9;padding:5px 0;font:inherit;font-size:11px;cursor:pointer}
    h2{font-size:21px;line-height:1.25;letter-spacing:-.025em;font-weight:550;margin:13px 0 10px}p{font-size:13px;line-height:1.75;color:#c0c5d7;margin:0}.tip{color:#ac9cbe;font-size:11px;margin-top:11px}.where{color:#b9a8d4;font-size:10px;margin-top:12px}
    .actions{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:18px}button{font-family:inherit;cursor:pointer}button:focus-visible{outline:2px solid #e1caff;outline-offset:4px}.back{padding:10px 14px;border-radius:100px;background:none;color:#c4bdd2;border:1px solid #ffffff21;font-size:12px}.back:disabled{opacity:.35;cursor:default}.next{padding:11px 20px;border:0;border-radius:100px;background:#e4d7fa;color:#211a2c;font-size:12px;font-weight:600}
    @media(pointer:coarse){button{min-height:44px}.card{padding:16px}h2{font-size:20px}}
    @media(prefers-reduced-motion:reduce){.frame{transition:none}}
  `;
  function start(options={}){
    if(active)active.destroy();
    const root=options.root||document,query=selector=>root.querySelector(selector),gate=query('#gate');
    if(gate&&gate.getClientRects().length&&getComputedStyle(gate).display!=='none')return{started:false,reason:'select-user'};
    const available=steps.filter(step=>{
      if(step.mobileOnly&&innerWidth>=760)return false;
      if(step.fallback)return !!pick(step,root).el;
      const target=query(step.target);return target&&(!step.optional||target.getClientRects().length&&getComputedStyle(target).visibility!=='hidden');});
    if(!available.length)return{started:false,reason:'no-targets'};
    const previousFocus=document.activeElement,body=document.body,initialSideHidden=body.classList.contains('side-hidden'),initialControlsOpen=body.classList.contains('ctl-open'),gear=query('#gearPanel'),initialGearDisplay=gear?.style.display;
    const host=document.createElement('hai-spotlight-tour'),shadow=host.attachShadow({mode:'open'});
    shadow.innerHTML=`<style>${css}</style><div class="shade top-shade"></div><div class="shade left-shade"></div><div class="shade right-shade"></div><div class="shade bottom-shade"></div><div class="frame" aria-hidden="true"></div><section class="card" role="dialog" aria-labelledby="hai-spotlight-title" aria-describedby="hai-spotlight-body"><div class="top"><span class="count" aria-live="polite"></span><button class="skip" type="button" data-go="skip">Skip tour ↗</button></div><h2 id="hai-spotlight-title"></h2><p id="hai-spotlight-body"></p><p class="tip"></p><p class="where"></p><div class="actions"><button class="back" type="button" data-go="back">Back</button><button class="next" type="button" data-go="next">Next →</button></div></section>`;
    document.body.append(host);
    const card=shadow.querySelector('.card'),frame=shadow.querySelector('.frame'),initialScroll={x:window.scrollX,y:window.scrollY};
    let index=0,ended=false,raf=0,target=null,resizeObserver=null;
    function resetTemporaryPanels(){body.classList.toggle('side-hidden',initialSideHidden);body.classList.toggle('ctl-open',initialControlsOpen);if(gear)gear.style.display=initialGearDisplay;}
    function box(el,x,y,w,h){Object.assign(el.style,{left:`${x}px`,top:`${y}px`,width:`${Math.max(0,w)}px`,height:`${Math.max(0,h)}px`});}
    function layout(){
      raf=0;if(ended||!target?.isConnected)return;
      const v=window.visualViewport,width=v?.width||innerWidth,height=v?.height||innerHeight,ox=v?.offsetLeft||0,oy=v?.offsetTop||0,r=target.getBoundingClientRect();
      const left=Math.max(ox+3,r.left-7),top=Math.max(oy+3,r.top-7),right=Math.min(ox+width-3,r.right+7),bottom=Math.min(oy+height-3,r.bottom+7);
      box(frame,left,top,right-left,bottom-top);
      box(shadow.querySelector('.top-shade'),ox,oy,width,top-oy);box(shadow.querySelector('.left-shade'),ox,top,left-ox,bottom-top);box(shadow.querySelector('.right-shade'),right,top,ox+width-right,bottom-top);box(shadow.querySelector('.bottom-shade'),ox,bottom,width,oy+height-bottom);
      card.style.maxHeight=`${Math.max(140,height-24)}px`;
      const c=card.getBoundingClientRect();
      const x=Math.min(Math.max(left+(right-left-c.width)/2,ox+12),ox+width-c.width-12);
      let y=bottom+14;if(y+c.height>oy+height-12)y=top-c.height-14;if(y<oy+12)y=Math.max(oy+12,oy+height-c.height-12);
      card.style.left=`${Math.max(ox+12,x)}px`;card.style.top=`${y}px`;
    }
    function schedule(){if(!raf&&!ended)raf=requestAnimationFrame(layout);}
    function finish(reason){
      if(ended)return;ended=true;resetTemporaryPanels();if(raf)cancelAnimationFrame(raf);resizeObserver?.disconnect();
      window.removeEventListener('resize',schedule);document.removeEventListener('scroll',schedule,true);document.removeEventListener('keydown',keyDown,true);window.visualViewport?.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('scroll',schedule);
      host.remove();window.scrollTo(initialScroll.x,initialScroll.y);if(previousFocus?.isConnected&&typeof previousFocus.focus==='function')previousFocus.focus({preventScroll:true});
      if(active?.host===host)active=null;if(reason==='complete')options.onFinish?.();else if(reason==='skip')options.onSkip?.();
    }
    function show(next){
      resetTemporaryPanels();index=next;const info=available[index];
      if(info.prepare==='sidebar')body.classList.remove('side-hidden');else if(innerWidth<760)body.classList.add('side-hidden');
      if(info.prepare==='controls')body.classList.add('ctl-open');
      if(info.prepare==='gear'&&gear){gear.style.display='block';body.classList.add('ctl-open');}
      const picked=info.fallback?pick(info,root):{el:query(info.target),fallback:false};target=picked.el;
      if(!target?.getClientRects().length){if(index<available.length-1){show(index+1);return;}finish('complete');return;}
      shadow.querySelector('.count').textContent=`${index+1} of ${available.length} · Around HAI`;shadow.querySelector('h2').textContent=info.title;shadow.querySelector('#hai-spotlight-body').textContent=info.body;shadow.querySelector('.tip').textContent=picked.fallback?info.fallbackTip:info.tip;shadow.querySelector('.where').textContent=info.where;
      shadow.querySelector('.back').disabled=index===0;shadow.querySelector('.next').textContent=index===available.length-1?'Finish ✓':'Next →';
      target.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});resizeObserver?.disconnect();resizeObserver=new ResizeObserver(schedule);resizeObserver.observe(target);resizeObserver.observe(card);layout();shadow.querySelector('.next').focus({preventScroll:true});
    }
    function keyDown(event){
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish('skip');return;}if(event.key!=='Tab')return;
      const selector='button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled)';
      const candidates=[...(target?.matches(selector)?[target]:[]),...Array.from(target?.querySelectorAll(selector)||[]),...shadow.querySelectorAll('button:not(:disabled)')].filter(el=>el.getClientRects().length);
      if(!candidates.length)return;const focused=shadow.activeElement||document.activeElement,current=candidates.indexOf(focused),next=(current+(event.shiftKey?-1:1)+candidates.length)%candidates.length;event.preventDefault();candidates[next].focus({preventScroll:true});
    }
    shadow.addEventListener('click',event=>{const go=event.target.closest('button')?.dataset.go;if(go==='skip')finish('skip');else if(go==='back'&&index>0)show(index-1);else if(go==='next'){if(index===available.length-1)finish('complete');else show(index+1);}});
    shadow.querySelectorAll('.shade').forEach(el=>el.addEventListener('pointerdown',event=>event.preventDefault()));
    window.addEventListener('resize',schedule);document.addEventListener('scroll',schedule,true);document.addEventListener('keydown',keyDown,true);window.visualViewport?.addEventListener('resize',schedule);window.visualViewport?.addEventListener('scroll',schedule);
    active={started:true,host,destroy:()=>finish('destroy'),next:()=>index<available.length-1?show(index+1):finish('complete')};show(0);return active;
  }
  window.HAITour=Object.freeze({steps,start});
})();
