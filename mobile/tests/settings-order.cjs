const fs=require('fs'),path=require('path'),assert=require('assert');
const {execFileSync}=require('child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const artifacts=['Dubs-1.10-TrollStore.ipa','Dubs-1.10.apk','DubsTV-1.2.apk','Swiftfin-1.5.1.ipa','Findroid-Dubs-1.1-arm64.apk','Findroid-Dubs-TV-1.2-arm64.apk','SHA256SUMS'].map(file=>({file,label:file}));
const html=execFileSync('python3',[path.join(__dirname,'render-fixture.py')],{encoding:'utf8',env:{...process.env,DUBS_TEST_ARTIFACTS:JSON.stringify(artifacts)}});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox','--disable-gpu']});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 await page.route('https://settings.test/**',r=>{
  const name=new URL(r.request().url()).pathname.slice(1);
  if(['style.css','ui.js'].includes(name))return r.fulfill({body:fs.readFileSync(path.join(root,'src',name)),contentType:name.endsWith('css')?'text/css':'application/javascript'});
  if(!name)return r.fulfill({body:html,contentType:'text/html'});
  return r.fulfill({json:{}});
 });
 await page.goto('https://settings.test/#settings');
 await page.locator('#events-panel').waitFor();
 const order=['events-panel','account-panel','apps-panel','appearance-panel','player-panel','cleanup-panel','download-panel'];
 assert.deepEqual(await page.locator('#settings .panel').evaluateAll(ns=>ns.map(n=>n.id)),order);
 const bounds=()=>page.locator('#settings .panel').evaluateAll(ns=>Object.fromEntries(ns.map(n=>{const r=n.getBoundingClientRect();return[n.id,{x:r.x,y:r.y,bottom:r.bottom,width:r.width}]})));
 for(const width of [1440,900,720]){
  await page.setViewportSize({width,height:1100});
  const b=await bounds();
  assert(b['events-panel'].x<b['appearance-panel'].x);
  assert.equal(b['events-panel'].y,b['appearance-panel'].y);
  for(const ids of [order.slice(0,3),order.slice(3)])for(let i=1;i<ids.length;i++)assert(Math.abs(b[ids[i]].y-b[ids[i-1]].bottom-16)<1,'independent panel gap');
  // Expanding a tall panel must not move the short stack.
  const before=b['player-panel'].y;
  await page.locator('#account-panel details').first().evaluate(n=>n.open=!n.open);
  assert.equal((await bounds())['player-panel'].y,before);
 }
 await page.setViewportSize({width:1440,height:1100});
 await page.screenshot({path:'/tmp/dubs-settings-order-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 let b=await bounds();
 for(let i=1;i<order.length;i++){assert.equal(b[order[i]].x,b[order[0]].x);assert(b[order[i]].y>=b[order[i-1]].bottom);}
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/dubs-settings-order-mobile.png',fullPage:true});
 // Exercise TV short up/down on the real renderer at both widths, including open forms.
 await page.addScriptTag({content:fs.readFileSync(path.join(root,'mobile/android/tv/src/main/assets/tv.js'),'utf8')});
 await page.waitForTimeout(300);
 for(const width of [1920,640]){
  await page.setViewportSize({width,height:1080});
  const tvBounds=await bounds();
  assert(width===1920?tvBounds['events-panel'].x<tvBounds['appearance-panel'].x:tvBounds['events-panel'].x===tvBounds['appearance-panel'].x);
  for(const expanded of [false,true]){
   await page.locator('#account-panel details').first().evaluate((n,value)=>n.open=value,expanded);
   const controls=await page.locator('#settings').evaluate(n=>Array.from(n.querySelectorAll('button,select,input,a,summary')).filter(el=>!el.disabled&&!el.closest('[hidden]')&&!Array.from(n.querySelectorAll('details:not([open])')).some(d=>d.contains(el)&&!d.querySelector('summary').contains(el))&&el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden').map((el,i)=>{el.dataset.testFocus=String(i);return String(i)}));
   await page.locator(`[data-test-focus="${controls[0]}"]`).focus();
   for(let i=1;i<controls.length;i++){
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.testFocus),controls[i],`TV down ${width} ${i}`);
   }
   const last=await page.locator(`[data-test-focus="${controls.at(-1)}"]`).boundingBox(),nav=await page.locator('.bottom-nav').boundingBox();
   assert(last.y>=50&&last.y+last.height<nav.y,'TV keeps focused control above footer');
   await page.keyboard.press('ArrowDown');
   assert(await page.evaluate(()=>document.activeElement.matches('.bottom-nav a.active')));
   await page.keyboard.press('ArrowUp');
   assert.equal(await page.evaluate(()=>document.activeElement.dataset.testFocus),controls.at(-1));
   for(let i=controls.length-2;i>=0;i--){await page.keyboard.press('ArrowUp');assert.equal(await page.evaluate(()=>document.activeElement.dataset.testFocus),controls[i],`TV up ${width} ${i}`);}
  }
 }
 console.log('PASS settings: semantic order; independent desktop stacks; responsive collapse; real TV up/down across collapsed/expanded forms; screenshots');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
