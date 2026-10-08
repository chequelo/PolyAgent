import { chromium } from 'playwright';
const b = await chromium.launch({ executablePath:'/opt/pw-browsers/chromium', args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist','--no-sandbox','--disable-dev-shm-usage','--mute-audio']});
const p = await b.newPage({viewport:{width:1280,height:720}});
p.on('pageerror',e=>console.log('[PAGEERR]',e.message.slice(0,160)));
await p.goto('file://'+process.cwd()+'/dist/standalone.html',{waitUntil:'load',timeout:30000});
await p.waitForFunction(()=>window.__game&&window.__game.R,{timeout:60000});
await p.evaluate(()=>{const g=window.__game;try{g.audio.init();}catch(e){}
  document.getElementById('menu').classList.add('hidden');
  g.running=true;g.clock.start();g.player.enabled=true;g._loop();
  g.hud.setTargets(g.enemies.alive.length);});
await p.waitForTimeout(900);
await p.evaluate(()=>{window.__game.running=false;});
await p.waitForTimeout(150);
await p.screenshot({path:'shots/standalone.png',timeout:20000});
console.log('OK');
await b.close();
