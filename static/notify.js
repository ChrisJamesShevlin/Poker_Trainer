/* Pop-ups for a new badge or a cleared level. notify([{kind, title, text}, ...]); click one to open the Grinding page. */
(()=>{
const css=document.createElement('style');
css.textContent=`#notifyBox{position:fixed;top:68px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:10px;max-width:min(360px,calc(100vw - 32px))}
.ntf{display:flex;gap:12px;align-items:flex-start;background:var(--panel,#171f27);color:var(--text,#e8eef4);border:2px solid #f59e0b;border-radius:14px;padding:12px 14px;box-shadow:0 8px 28px #000a;cursor:pointer;animation:ntfIn .35s ease-out}
.ntf.level{border-color:#22c55e}.ntf.gold{border-color:#facc15;box-shadow:0 0 24px #facc1588,0 8px 28px #000a}
.ntf .ic{font-size:30px;line-height:1}.ntf b{display:block;font-size:15px}.ntf span{font-size:13px;color:var(--dim,#8da0b3)}
.ntf .x{margin-left:auto;background:none;border:0;color:var(--dim,#8da0b3);font-size:18px;cursor:pointer;padding:0 0 0 6px}
@keyframes ntfIn{from{transform:translateX(30px);opacity:0}to{transform:none;opacity:1}}`;
document.head.appendChild(css);
const lk=document.createElement('link');lk.rel='stylesheet';lk.href='/static/chips.css';document.head.appendChild(lk);
window.notify=list=>{
  if(!list||!list.length)return;
  let box=document.getElementById('notifyBox');
  if(!box){box=document.createElement('div');box.id='notifyBox';document.body.appendChild(box)}
  list.forEach((n,i)=>setTimeout(()=>{
    const el=document.createElement('div');el.className='ntf '+(n.kind==='level'?'level':'')+(n.kind==='completionist'?' gold':'');
    const ic=document.createElement('div');ic.className='ic';
    if(n.chip){const t=n.chip_text||'';ic.innerHTML=`<div class="chip v${n.chip} l${t.length}" style="--size:46px"><span>${t.replace(/[<>&]/g,'')}</span></div>`}else ic.textContent=n.kind==='level'?'🏆':'🏅';
    const body=document.createElement('div');
    const t=document.createElement('b');t.textContent=n.title;const s=document.createElement('span');s.textContent=n.text;
    body.append(t,s);
    const x=document.createElement('button');x.className='x';x.textContent='×';x.title='Dismiss';
    x.onclick=e=>{e.stopPropagation();el.remove()};
    el.append(ic,body,x);el.title='Open Grinding';el.onclick=()=>{location.href='/grind'};
    box.appendChild(el);setTimeout(()=>el.remove(),10000);
  },i*500));
};
})();
