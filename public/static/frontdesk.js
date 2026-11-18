(() => {
'use strict';
const $ = (id) => document.getElementById(id);
const motionPreference = matchMedia('(prefers-reduced-motion: reduce)');
$('year').textContent = new Date().getFullYear();

// Progressive enhancement: the full page is visible without JavaScript.
if (!motionPreference.matches && 'IntersectionObserver' in window) {
 document.documentElement.classList.add('js-motion');
 const reveal = new IntersectionObserver(entries => entries.forEach(entry => {
  if (entry.isIntersecting) { entry.target.classList.add('in'); reveal.unobserve(entry.target); }
 }), {threshold: .12});
 document.querySelectorAll('.reveal').forEach(el => reveal.observe(el));
}
const menu = document.querySelector('.menu-button');
const nav = $('main-nav');
menu.addEventListener('click', () => {
 const open = menu.getAttribute('aria-expanded') !== 'true';
 menu.setAttribute('aria-expanded', String(open));menu.setAttribute('aria-label',open?'Close menu':'Open menu');nav.classList.toggle('open',open);
});
nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => { nav.classList.remove('open');menu.setAttribute('aria-expanded','false');menu.setAttribute('aria-label','Open menu'); }));

// Continuous full-viewport perspective field. Rendered locally; no video download.
const canvas=$('signal-canvas'),ctx=canvas.getContext('2d',{alpha:false});
if(ctx){
 let width=0,height=0,dpr=1,raf=0,last=0,time=0,paused=false,scroll=0;
 let pointerX=0,pointerY=0,px=0,py=0;
 const coarse=matchMedia('(pointer: coarse)');
 function resize(){width=innerWidth;height=innerHeight;dpr=Math.min(devicePixelRatio||1,1.5);canvas.width=width*dpr;canvas.height=height*dpr;draw(time);}
 function draw(t){ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#101112';ctx.fillRect(0,0,width,height);
  const glow=ctx.createRadialGradient(width*.76,height*.42,0,width*.7,height*.5,width*.65);glow.addColorStop(0,'#30343b');glow.addColorStop(.5,'#171b20');glow.addColorStop(1,'#101112');ctx.fillStyle=glow;ctx.fillRect(0,0,width,height);
  const progress=scroll*.00018;const cx=width*(.68+Math.sin(t*.13)*.055)+px*25,cy=height*(.5+Math.cos(t*.17)*.035)+py*18;
  const lines=coarse.matches?45:70;
  for(let i=lines;i>=0;i--){
   const z=((i/lines+t*.025+progress)%1);const depth=Math.pow(z,2.5);const radius=35+depth*width*.91;
   ctx.beginPath();
   for(let j=0;j<=100;j++){
    const a=j/100*Math.PI*2;
    const warp=1+.19*Math.sin(a*3+t*.48+i*.022)+.09*Math.cos(a*2-t*.23);
    const x=cx+Math.cos(a)*radius*warp,y=cy+Math.sin(a)*radius*.71*warp;
    if(j===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
   }
   ctx.closePath();ctx.strokeStyle=i%9===0?`rgba(224,174,135,${.19+depth*.38})`:`rgba(171,186,198,${.08+depth*.21})`;ctx.lineWidth=i%9===0?1.45:.7;ctx.stroke();
  }
 }
 function tick(now){raf=0;if(paused||motionPreference.matches||document.hidden)return;if(!last)last=now;const delta=now-last;if(delta>=(coarse.matches?32:16)){time+=Math.min(delta,60)/1000;last=now;px+=(pointerX-px)*.035;py+=(pointerY-py)*.035;draw(time);}raf=requestAnimationFrame(tick);}
 function update(){cancelAnimationFrame(raf);last=0;const stop=paused||motionPreference.matches;$('motion-toggle').setAttribute('aria-pressed',String(stop));$('motion-toggle').setAttribute('aria-label',stop?'Play background animation':'Pause background animation');$('motion-icon').textContent=stop?'▷':'Ⅱ';$('motion-toggle').disabled=motionPreference.matches;draw(time);if(!stop&&!document.hidden)raf=requestAnimationFrame(tick);}
 $('motion-toggle').addEventListener('click',()=>{paused=!paused;update();});
 addEventListener('pointermove',e=>{pointerX=(e.clientX/innerWidth-.5)*2;pointerY=(e.clientY/innerHeight-.5)*2;},{passive:true});
 addEventListener('scroll',()=>{scroll=scrollY;},{passive:true});
 addEventListener('resize',resize);document.addEventListener('visibilitychange',update);motionPreference.addEventListener('change',update);resize();update();
}

// Illustrative conversations are separate from the live chat.
const examples={
 trades:[['customer','Hi, do you paint kitchen cabinets or just walls?'],['assistant','We do both. Are you looking to refresh your existing cabinets, or have new ones painted?'],['customer','Refresh the old ones. We’re in Southsea.']],
 salon:[['customer','Hi! Do you do balayage, and how much roughly?'],['assistant','We do — it starts from £160. Have you had your hair coloured with us before?'],['customer','Not yet! I’d love to come in next week.']],
 services:[['customer','Do you offer an end-of-tenancy clean?'],['assistant','Yes, we do. How many bedrooms does the property have, and when are you moving out?'],['customer','Two bedrooms. We hand the keys back on Friday.']]
};
const tabs=[...document.querySelectorAll('[data-example]')];
function setExample(button){tabs.forEach(tab=>{const selected=tab===button;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;});$('example-conversation').setAttribute('aria-labelledby',button.id);const fragment=document.createDocumentFragment();examples[button.dataset.example].forEach(([who,text])=>{const el=document.createElement('div');el.className=`example-message ${who}`;const label=document.createElement('span');label.textContent=who==='customer'?'CUSTOMER':'YOUR FRONTDESK';const p=document.createElement('p');p.textContent=text;el.append(label,p);fragment.append(el);});$('example-messages').replaceChildren(fragment);}
tabs.forEach((tab,index)=>{tab.addEventListener('click',()=>setExample(tab));tab.addEventListener('keydown',event=>{let target;if(event.key==='ArrowRight')target=(index+1)%tabs.length;if(event.key==='ArrowLeft')target=(index+tabs.length-1)%tabs.length;if(event.key==='Home')target=0;if(event.key==='End')target=tabs.length-1;if(target!==undefined){event.preventDefault();tabs[target].focus();setExample(tabs[target]);}});});

// Estimated enquiry value, with missed enquiries constrained to total enquiries.
const money=n=>'£'+Math.round(n).toLocaleString('en-GB');
function calculate(){const enquiries=Number($('enq').value);$('miss').max=enquiries;$('miss').value=Math.min(Number($('miss').value),enquiries);const missed=Number($('miss').value),job=Number($('job').value);$('ev').textContent=enquiries;$('mv').textContent=missed;$('jv').textContent=money(job);$('lost').textContent=money(missed*job*4.33);$('enq').setAttribute('aria-valuetext',`${enquiries} enquiries a week`);$('miss').setAttribute('aria-valuetext',`${missed} missed enquiries a week`);$('job').setAttribute('aria-valuetext',`${money(job)} per job`);}
['enq','miss','job'].forEach(id=>$(id).addEventListener('input',calculate));calculate();

// Native dialog provides Escape support, modal focus handling and focus return.
const chat=$('chat');let started=false,busy=false,previousFocus=null;
function add(text,who){const message=document.createElement('div');message.className=`message ${who}`;message.textContent=text;$('msgs').append(message);$('msgs').scrollTop=$('msgs').scrollHeight;}
function openChat(){if(chat.open)return;previousFocus=document.activeElement;chat.showModal();document.body.style.overflow='hidden';if(!started){started=true;add('Hi! I’m the Frontdesk assistant — a live example of what Andra builds. What kind of business do you run?','assistant');}if(matchMedia('(min-width: 761px)').matches)$('inp').focus();else $('close-chat').focus();}
function closeChat(){chat.close();}
document.querySelectorAll('[data-chat]').forEach(button=>button.addEventListener('click',openChat));$('close-chat').addEventListener('click',closeChat);chat.addEventListener('close',()=>{document.body.style.overflow='';previousFocus?.focus();});chat.addEventListener('click',event=>{if(event.target!==chat)return;const r=chat.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeChat();});
$('chat-form').addEventListener('submit',async event=>{
 event.preventDefault();const input=$('inp'),value=input.value.trim();if(!value||busy)return;busy=true;add(value,'user');input.value='';$('snd').disabled=true;input.disabled=true;$('chat-status').textContent='Frontdesk is replying…';
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),25000);
 try{const response=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({message:value}),signal:controller.signal});if(!response.ok)throw Error('Chat unavailable');const data=await response.json();if(typeof data.reply!=='string'||!data.reply.trim())throw Error('Missing reply');add(data.reply,'assistant');}
 catch(error){add('Sorry, I couldn’t get a reply through. Please try again, or contact Andra at andradudan4@gmail.com.','assistant');}
 finally{clearTimeout(timeout);busy=false;$('snd').disabled=false;input.disabled=false;$('chat-status').textContent='';if(chat.open&&!matchMedia('(pointer: coarse)').matches)input.focus();}
});
// This is informational: essential session cookies cannot be switched off here.
let acknowledged=false;try{acknowledged=!!localStorage.getItem('frontdeskCookieInfo')||!!localStorage.getItem('cookieConsent');}catch(error){}
if(!acknowledged)$('cookie-note').hidden=false;
$('cookie-dismiss').addEventListener('click',()=>{$('cookie-note').hidden=true;try{localStorage.setItem('frontdeskCookieInfo','seen');}catch(error){}});
})();

// Project inspection and a guided example: every control has a clear destination.
(() => {
 const root=document.documentElement,bar=document.getElementById('reading-progress');
 const links=[...document.querySelectorAll('.site-header nav a,.mobile-shortcuts a')];
 const targets=['assistant','website','pricing','payments','contact'].map(id=>document.getElementById(id));
 let queued=false;
 function updateNavigation(){queued=false;const max=root.scrollHeight-innerHeight;bar.style.transform=`scaleX(${max>0?scrollY/max:0})`;let current='';for(const target of targets){if(target.getBoundingClientRect().top<innerHeight*.35)current=target.id;}links.forEach(link=>{if(link.hash===`#${current}`)link.setAttribute('aria-current','true');else link.removeAttribute('aria-current');});}
 addEventListener('scroll',()=>{if(!queued){queued=true;requestAnimationFrame(updateNavigation);}},{passive:true});addEventListener('resize',updateNavigation);updateNavigation();
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){const button=document.querySelector('.menu-button');if(button.getAttribute('aria-expanded')==='true'){document.getElementById('main-nav').classList.remove('open');button.setAttribute('aria-expanded','false');button.setAttribute('aria-label','Open menu');button.focus();}}});
 const next=document.getElementById('walkthrough-next'),conversation=document.getElementById('example-conversation'),lead=document.getElementById('lead-preview');
 const summaries={trades:['Kitchen cabinet repaint','Existing cabinets to refresh · Southsea'],salon:['Balayage enquiry','New customer · Interested in next week'],services:['End-of-tenancy clean','Two-bedroom property · Keys returned Friday']};
 function showEmail(show){conversation.hidden=show;lead.hidden=!show;next.setAttribute('aria-expanded',String(show));next.replaceChildren(document.createTextNode(show?'Back to conversation':'See the enquiry email'));const arrow=document.createElement('span');arrow.setAttribute('aria-hidden','true');arrow.textContent=show?'←':'→';next.append(arrow);document.getElementById('walkthrough-step').textContent=show?'02 / THE EMAIL YOU RECEIVE':'01 / CUSTOMER CONVERSATION';if(show){const selected=document.querySelector('[data-example][aria-selected=true]').dataset.example;document.getElementById('lead-service').textContent=summaries[selected][0];document.getElementById('lead-notes').textContent=summaries[selected][1];}}
 next.addEventListener('click',()=>showEmail(lead.hidden));
 document.querySelectorAll('[data-example]').forEach(button=>{button.addEventListener('click',()=>showEmail(false));button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key))showEmail(false);});});
})();

// Local-only concept studio. No input is transmitted until the visitor sends their email.
(() => {
 const el=id=>document.getElementById(id),canvas=el('studio-canvas');if(!canvas)return;
 const businesses={trades:{label:'Trades & property',heading:['Good work.','Great homes.'],kicker:'CRAFT / CARE / ATTENTION',description:'The small repairs. The big plans. A local team that takes pride in every detail.',services:'Repairs · Renovations · Maintenance',art:'BUILT\nWITH CARE.',message:'“We’d love a quote for a few repairs around the house.”'},salon:{label:'Hair & beauty',heading:['A little change.','All you.'],kicker:'COLOUR / CUT / CONFIDENCE',description:'A fresh perspective. A familiar face. Space to slow down and leave feeling like yourself.',services:'Cuts · Colour · Styling',art:'YOUR\nKIND OF YOU.',message:'“I’m thinking about a new colour. Could I arrange a consultation?”'},cleaning:{label:'Cleaning & home care',heading:['Come home.','Breathe out.'],kicker:'FRESH / SIMPLE / SORTED',description:'More time for your life. A beautifully cared-for home, without another thing on your list.',services:'Regular cleans · Deep cleans · Moving day',art:'ROOM\nTO BREATHE.',message:'“Could you help with a fortnightly clean for a three-bedroom house?”'}};
 let tone='copper',composition='editorial',timer;
 function update(animate=false){const business=businesses[el('studio-business').value],name=el('studio-name').value.trim()||'Your business.';el('concept-name').textContent=name;el('concept-heading').replaceChildren(document.createTextNode(business.heading[0]),document.createElement('br'),document.createTextNode(business.heading[1]));el('concept-kicker').textContent=business.kicker;el('concept-description').textContent=business.description;el('concept-services').textContent=business.services;el('concept-art-label').textContent=business.art;el('receipt-message').textContent=business.message;canvas.dataset.tone=tone;canvas.dataset.composition=composition;
 const direction=`${business.label} · ${tone==='blue'?'Electric':tone[0].toUpperCase()+tone.slice(1)} · ${composition[0].toUpperCase()+composition.slice(1)}`;el('studio-status').textContent=direction;
 el('studio-brief').href='mailto:andradudan4@gmail.com?subject='+encodeURIComponent('My Frontdesk website direction')+'&body='+encodeURIComponent(`Hi Andra,\n\nI tried the design studio and would like to discuss a website.\n\nBusiness: ${name}\nDirection: ${direction}\n\nWhat I need:\n`);
 if(animate){canvas.classList.remove('changing');clearTimeout(timer);requestAnimationFrame(()=>{canvas.classList.add('changing');timer=setTimeout(()=>canvas.classList.remove('changing'),400);});}}
 el('studio-name').addEventListener('input',()=>update());el('studio-business').addEventListener('change',()=>update(true));
 document.querySelectorAll('.studio-palette button').forEach(button=>button.addEventListener('click',()=>{tone=button.dataset.tone;document.querySelectorAll('.studio-palette button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));update(true);}));
 document.querySelectorAll('[data-composition][aria-pressed]').forEach(button=>button.addEventListener('click',()=>{composition=button.dataset.composition;document.querySelectorAll('[data-composition][aria-pressed]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));update(true);}));
 el('concept-enquire').addEventListener('click',()=>{el('concept-receipt').hidden=false;el('receipt-close').focus();});
 function close(){el('concept-receipt').hidden=true;el('concept-enquire').focus();}el('receipt-close').addEventListener('click',close);canvas.addEventListener('keydown',event=>{if(event.key==='Escape'&&!el('concept-receipt').hidden){event.preventDefault();close();}});update();
})();
