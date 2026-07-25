import { chromium } from 'playwright';
const b = await chromium.launch({ executablePath:'/opt/pw-browsers/chromium', args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist','--no-sandbox','--disable-dev-shm-usage','--mute-audio']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror', e=>console.log('[PAGEERR]', e.message.slice(0,300)));
await p.goto('http://localhost:5173/index.html',{waitUntil:'commit',timeout:30000});
await p.waitForFunction(()=>window.__game&&window.__game.R,{timeout:90000});
await p.evaluate(()=>{ const g=window.__game; try{g.audio.init();}catch(e){}
  document.getElementById('menu').classList.add('hidden');
  g.running=false; // keep static
  g.hud.criticalShot(450);
});
await p.waitForTimeout(300);
const r = await p.evaluate(()=>{
  const cr=document.getElementById('critical');
  const rect=cr.getBoundingClientRect();
  return {cls:cr.className, op:getComputedStyle(cr).opacity, rect:[rect.x|0,rect.y|0,rect.width|0,rect.height|0], txt:cr.querySelector('.critical-text').textContent};
});
console.log('STATE', JSON.stringify(r));
await p.screenshot({path:'shots/critdbg.png', timeout:20000});
console.log('SHOT ok');
await b.close();
