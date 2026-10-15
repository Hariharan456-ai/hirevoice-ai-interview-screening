function copyText(t,btn){navigator.clipboard.writeText(t).then(()=>{const o=btn.textContent;btn.textContent='Copied ✔';setTimeout(()=>btn.textContent=o,1200);});}
fetch('/system_check').then(r=>r.json()).then(s=>{
  const ok=v=>/^(OK|installed|Piper ready)/.test(v)?'🟢':'🟠';
  document.getElementById('sys').innerHTML=`<b>Local AI status</b><br>${ok(s.ollama)} LLM: ${s.ollama}<br>${ok(s.whisper)} Speech-to-text: ${s.whisper}<br>${ok(s.tts)} Voice: ${s.tts}`;}).catch(()=>{});
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
const STATUS_LABEL={pending:'Not started',in_progress:'Live now',incomplete:'Stopped early',scoring:'Scoring…',completed:'Completed'};
function startLiveDashboard(qcount, rid){
  async function tick(){
    try{const resp=await fetch('/api/ranking?role='+rid,{cache:'no-store'});if(resp.status===401){location='/login';return;}const d=await resp.json();const ub=document.getElementById('upd');if(ub){ub.textContent='Updated '+new Date().toLocaleTimeString();ub.className='live';}
      for(const k of ['total','live','completed','avg','top','flagged']){const e=document.getElementById('s-'+k);if(e)e.textContent=d.stats[k]+((k==='avg'||k==='top')?'%':'');}
      const tb=document.querySelector('#rank tbody');
      tb.innerHTML=d.rows.map(r=>{
        const lbl=(r.status==='in_progress'&&r.progress)?`Live · Q${r.progress.q}/${r.progress.total}`:STATUS_LABEL[r.status]||r.status;
        const st=`<span class="pill ${esc(r.status)}">${r.status==='in_progress'?'● ':''}${esc(lbl)}</span>`;
        return `<tr class="${r.ai?'flag':''}"><td><span class="rk">${r.rank??'–'}</span></td>
        <td><a href="/candidates/${r.id}" class="${r.ai?'redname':''}">${esc(r.name)}</a>${r.ai?' <span class="pill red">AI?</span>':''}</td>
        <td>${esc(r.degree)} ${esc(r.spec)}</td><td>${st}</td><td>${r.done}/${qcount}</td>
        <td>${r.total!=null?r.total.toFixed(1)+' / '+r.max:'–'}</td>
        <td>${r.pct!=null?`<div class="bar"><i style="width:${r.pct}%"></i></div>${r.pct.toFixed(1)}%`:'–'}</td>
        <td>${r.rt!=null?r.rt.toFixed(1)+'s':'–'}</td><td>${r.pdf?`<a href="/report/${r.id}.pdf" target="_blank">PDF</a>`:'–'}${r.drive?` · <a href="${esc(r.drive)}" target="_blank">☁</a>`:''}</td></tr>`;}).join('')||'<tr><td colspan="9" class="muted">No candidates yet.</td></tr>';
      const labels=['0–20%','20–40%','40–60%','60–80%','80–100%'],mx=Math.max(1,...d.stats.dist);
      document.getElementById('dist').innerHTML=d.stats.dist.map((v,i)=>`<div class="drow"><span>${labels[i]}</span><div class="bar big"><i style="width:${v/mx*100}%"></i></div><b>${v}</b></div>`).join('');
    }catch(e){const ub=document.getElementById('upd');if(ub){ub.textContent='⚠ connection lost - retrying…';ub.className='err';}}
  }
  tick();setInterval(tick,3000);
}
