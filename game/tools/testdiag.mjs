import { chromium } from 'playwright';
const b = await chromium.launch({ executablePath:'/opt/pw-browsers/chromium', args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist','--no-sandbox','--disable-dev-shm-usage','--mute-audio']});
const p = await b.newPage({viewport:{width:800,height:450}});
const msgs=[]; p.on('console',m=>{if(m.type()==='error'||m.type()==='warning')msgs.push('['+m.type()+'] '+m.text().slice(0,180));}); p.on('pageerror',e=>msgs.push('[PAGEERR] '+e.message.slice(0,180)));
await p.goto('file://'+process.cwd()+'/dist/standalone.html',{waitUntil:'load',timeout:30000});
await p.waitForFunction(()=>window.__game&&window.__game.R,{timeout:60000});
const r = await p.evaluate(()=>{
  const g=window.__game;
  g.R.render();
  const c=document.getElementById('scene');
  const cv=document.createElement('canvas');cv.width=c.width;cv.height=c.height;const ctx=cv.getContext('2d');ctx.drawImage(c,0,0);
  const px=(x,y)=>Array.from(ctx.getImageData(x,y,1,1).data).slice(0,3);
  return { cw:c.width, ch:c.height, childCount:g.scene.children.length, env:!!g.scene.environment,
    center:px(c.width/2|0,c.height/2|0), low:px(c.width/2|0, c.height-40) };
});
console.log('DIAG', JSON.stringify(r));
console.log(msgs.slice(0,10).join('\n')||'(no err/warn)');
await b.close();
