const fs=require('fs'),path=require('path'),assert=require('assert');
const {execFileSync}=require('child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const html=execFileSync('python3',[path.join(__dirname,'render-fixture.py')],{encoding:'utf8'});
const original=JSON.parse(fs.readFileSync(path.join(root,'studios.json'))).map(s=>s.name);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox','--disable-gpu']});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1100},hasTouch:true});page.setDefaultTimeout(7000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let state='applied',desired=[...original],effective=[...original],posts=[];
  const ep=(id,sid,extra={})=>({id,seriesId:sid,seriesTitle:'Test '+sid,title:'Episode '+id,season:1,number:id,sonarrId:id,...extra});
  const snapshot={updated:1,actions:{'21':{state:'waiting',message:'Waiting'}},scheduled:{},released:[
   {seriesId:1,title:'Start',episodes:[ep(11,1)]},
   {seriesId:2,title:'Cancel',episodes:[ep(21,2)]},
   {seriesId:3,title:'Complete',episodes:[ep(31,3,{hasFile:true})]}],upcoming:[]};
  await page.route('https://settings.test/**',async r=>{
   const name=new URL(r.request().url()).pathname.slice(1);
   if(['style.css','ui.js'].includes(name))return r.fulfill({body:fs.readFileSync(path.join(root,'src',name)),contentType:name.endsWith('css')?'text/css':'application/javascript'});
   if(!name)return r.fulfill({body:html,contentType:'text/html'});
   if(name==='api/session')return r.fulfill({json:{csrf:'x'.repeat(43)}});
   if(name==='api/episodes')return r.fulfill({json:snapshot});
   if(name==='api/studio-order'){
    if(r.request().method()==='POST'){
     const form=new URLSearchParams(r.request().postData());
     assert.equal(form.get('csrf'),'x'.repeat(43));
     desired=JSON.parse(form.get('order'));posts.push(desired);state='pending';
    }
    return r.fulfill({json:{order:desired,effectiveOrder:effective,state,message:{pending:'Сохранено. Ожидает применения в Sonarr',applied:'Применено в Sonarr',error:'Sonarr не подтвердил применение. Автоматический повтор через 30 секунд'}[state]}});
   }
   return r.fulfill({json:{}});
  });
  await page.goto('https://settings.test/#settings');
  const list=page.locator('#studio-order-list');
  const names=()=>list.locator('li').evaluateAll(ns=>ns.map(n=>n.dataset.studio));
  const status=page.locator('#studio-order-status');
  await page.waitForFunction(()=>document.querySelector('#studio-order-status').dataset.state==='applied');
  assert.deepEqual(await names(),original);
  assert(await list.locator('li').first().locator('[data-move="-1"]').isDisabled());
  await list.locator('li').first().locator('[data-move="1"]').focus();
  await page.keyboard.press('Enter');
  assert.equal((await names())[1],original[0]);
  assert.equal(await status.getAttribute('data-state'),'unsaved');
  assert.equal(posts.length,0,'reorder must not save automatically');
  // Real browser HTML drag-and-drop, then keyboard button recovery.
  const dragged=list.locator('li').nth(0),target=list.locator('li').nth(3);
  const first=(await names())[0];
  await dragged.dragTo(target,{targetPosition:{x:40,y:50}});
  assert.notEqual((await names()).indexOf(first),0,'drag changed order');
  const responseWait=page.waitForResponse(r=>r.url().endsWith('/api/studio-order')&&r.request().method()==='POST');
  await page.locator('#studio-order-save').click();
  await responseWait;
  await page.waitForFunction(()=>document.querySelector('#studio-order-status').dataset.state==='pending');
  assert.equal(posts.length,1);assert.deepEqual(posts[0],await names());
  state='error';await page.evaluate(()=>window.dispatchEvent(new HashChangeEvent('hashchange')));
  await page.waitForFunction(()=>document.querySelector('#studio-order-status').dataset.state==='error');
  assert.match(await status.textContent(),/Sonarr/);
  const retryWait=page.waitForResponse(r=>r.url().endsWith('/api/studio-order')&&r.request().method()==='POST');
  await page.locator('#studio-order-save').click();
  await retryWait;
  await page.waitForFunction(()=>document.querySelector('#studio-order-status').dataset.state==='pending');
  assert.equal(posts.length,2);
  state='applied';effective=[...desired];await page.evaluate(()=>window.dispatchEvent(new HashChangeEvent('hashchange')));
  await page.waitForFunction(()=>document.querySelector('#studio-order-status').dataset.state==='applied');
  assert.deepEqual(await page.locator('#media-badge-config').evaluate(n=>JSON.parse(n.dataset.config).studios),effective);
  assert.equal(await page.evaluate(()=>mediaVoice({tracks:[{studio:'LostFilm'},{studio:'Red Head Sound'}]},{seriesId:77})), 'RHS');
  assert.equal(await page.evaluate(()=>{
    const node=document.querySelector('#media-badge-config'),config=JSON.parse(node.dataset.config);
    config.preferred['77']='LostFilm';node.dataset.config=JSON.stringify(config);
    return mediaVoice({tracks:[{studio:'LostFilm'},{studio:'Red Head Sound'}]},{seriesId:77});
  }), 'Lost', 'personal studio precedes global badge order');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#studio-order-status').dataset.state==='applied');
  assert.deepEqual(await names(),desired,'reload reads persisted order');
  for(const width of [390,320]){
   await page.setViewportSize({width,height:844});
   await list.locator('li').nth(1).locator('[data-move="-1"]').tap();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile no overflow');
   const box=await list.locator('li').first().locator('button').last().boundingBox();assert(box.height>=44&&box.width>=44,'touch target');
  }
  await page.setViewportSize({width:1920,height:1080});
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'mobile/android/tv/src/main/assets/tv.js'),'utf8')});
  const beforeTV=await names();
  const down=list.locator('li').first().locator('[data-move="1"]');await down.focus();await page.keyboard.press('Enter');
  assert.equal((await names())[1],beforeTV[0],'TV Enter moves item');
  assert(await page.evaluate(()=>document.activeElement.matches('#studio-order-list button')),'TV retains native focus');
  // The unchanged start action remains filled; cancel and all-ordered are outlined.
  await page.evaluate(()=>location.hash='#released');
  await page.locator('.card-actions .bulk-download').first().waitFor();
  const styles=await page.locator('.card-actions .bulk-download').evaluateAll(ns=>ns.map(n=>({text:n.textContent,disabled:n.disabled,bg:getComputedStyle(n).backgroundColor,border:getComputedStyle(n).borderTopColor,opacity:getComputedStyle(n).opacity})));
  assert(styles.some(s=>s.text==='Скачать всё'&&s.bg==='rgb(215, 0, 11)'));
  for(const text of ['Отменить','В медиатеке']){
   const s=styles.find(s=>s.text===text);assert(s,text+' exists');assert.equal(s.bg,'rgb(37, 37, 40)');assert.equal(s.border,'rgb(255, 53, 61)');assert.equal(s.opacity,'1');
   if(text==='В медиатеке')assert(s.disabled);
  }
  const cancelCard=page.locator('.episode-card').filter({has:page.locator('.bulk-download',{hasText:'Отменить'})});
  await cancelCard.locator('summary').first().click();
  const cancel=cancelCard.locator('.download-task button').first();await cancel.waitFor();
  assert.equal(await cancel.evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(37, 37, 40)');
  assert.equal(await cancel.evaluate(n=>getComputedStyle(n).borderTopColor),'rgb(255, 53, 61)');
  assert.deepEqual(errors,[]);
  console.log('PASS studio order: real drag, keyboard/mobile/TV move, explicit save, pending/error/retry/applied, reload, badge, outlined bulk/episode cancels and disabled all-ordered');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
