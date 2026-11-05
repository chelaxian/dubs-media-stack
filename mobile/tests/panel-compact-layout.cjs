const fs = require('fs'), path = require('path'), assert = require('assert');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const html = execFileSync('python3', [path.join(__dirname, 'render-fixture.py')], {encoding:'utf8'});
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || '/opt/google/chrome/chrome', args:['--no-sandbox','--disable-gpu']});
  const page = await browser.newPage({viewport:{width:1920,height:1080},hasTouch:true});
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let task = {state:'downloading', percent:37, message:'Ожидаем завершения <img src=x onerror=alert(1)>', eta:120};
  let polls = 0;
  const actions = [];
  let rejectAction = false;
  await page.route('https://panel.test/**', route => {
    const url = new URL(route.request().url());
    if (['/style.css','/ui.js'].includes(url.pathname)) return route.fulfill({contentType:url.pathname.endsWith('css')?'text/css':'text/javascript',body:fs.readFileSync(path.join(root,'src',url.pathname.slice(1)),'utf8')});
    if (url.pathname === '/api/episodes') {
      polls++;
      return route.fulfill({json:{updated:1,actions:{31:task},scheduled:{},upcoming:[],released:[{seriesId:7,title:'Тестовый сериал',episodes:[{id:31,seriesId:7,sonarrId:3,title:'Серия с фактическими данными',season:1,number:1,airDate:'2026-01-01',hasFile:true,localMedia:{status:'verified',quality:{label:'1080p',codec:'h264'},audioTracks:[{language:'rus',title:'Русская дорожка',studio:'LostFilm'}]}}]}]}});
    }
    if (url.pathname === '/api/session') return route.fulfill({json:{csrf:'x'.repeat(43)}});
    if (url.pathname.startsWith('/api/notifications/')) return route.fulfill({json:{enabled:false,types:[],events:[],devices:[],cursor:0,hasMore:false}});
    if (url.pathname === '/api/studio-order' && route.request().method() === 'GET') {
      return route.fulfill({json:{order:[],effectiveOrder:[],state:'applied',message:'Применено'}});
    }
    if (url.pathname === '/api/action') {
      actions.push(Object.fromEntries(new URLSearchParams(route.request().postData())));
      return rejectAction ? route.fulfill({status:503,json:{message:'Повторите попытку позже'}}) : route.fulfill({json:{job:'a'.repeat(32)}});
    }
    if (url.pathname.startsWith('/api/jobs/')) return route.fulfill({json:{state:'done'}});
    if (url.pathname.startsWith('/api/')) throw Error('Unexpected API action: '+url.pathname);
    if (url.pathname.startsWith('/cover/')) return route.fulfill({status:404,body:''});
    return route.fulfill({contentType:'text/html',body:html});
  });
  await page.goto('https://panel.test/');
  await page.waitForSelector('.episode-show');
  assert.equal(await page.locator('#view-title').textContent(),'Вышедшие серии');
  assert.equal(await page.locator('.toolbar nav,.toolbar .eyebrow').count(),0);
  assert(await page.locator('#settings').isHidden(),'grid settings honors hidden');
  await page.locator('.card-actions .watch-button').evaluate(n=>n.hidden=true);
  assert(await page.locator('.card-actions .watch-button').isHidden(),'inline-grid watch honors hidden');
  await page.locator('.card-actions .watch-button').evaluate(n=>n.hidden=false);
  assert.equal(await page.locator('[data-tab]').count(),4);
  await page.locator('.episode-show').evaluate(n=>n.open=true);
  const ring = page.locator('.download-ring'), details = page.locator('.job-details');
  assert(await details.isHidden());
  assert.equal(await ring.getAttribute('aria-expanded'),'false');
  assert.equal(await ring.getAttribute('aria-controls'),await details.getAttribute('id'));
  await ring.tap();
  assert(await details.isVisible());
  assert((await details.textContent()).includes('<img'));
  assert.equal(await details.locator('img').count(),0);
  await ring.focus();
  task = {...task,percent:42};
  const previousPolls = polls;
  await page.waitForFunction(()=>document.querySelector('.ring-label')?.textContent==='42%',null,{timeout:7000});
  assert(polls>previousPolls);
  assert(await ring.evaluate(n=>n===document.activeElement),'polling preserves ring keyboard focus');
  assert(await details.isVisible(),'open choice survives actual polling');
  await ring.focus();
  await page.keyboard.press('Enter');
  assert(await details.isHidden());
  task = {...task,state:'error',message:'Ошибка <script>alert(1)</script>'};
  await page.waitForFunction(()=>!document.querySelector('.download-task .quiet'),null,{timeout:7000});
  assert(await details.isHidden(),'polling error never forces open');
  assert.equal(await page.locator('.episode-actions > button.library-link').count(),1,'original media action retained after task error');
  await ring.click();
  assert((await details.textContent()).includes('Ошибка <script>'));
  assert(await page.locator('.media-panel').isHidden(),'specific media detail styles honor hidden');
  await page.locator('.media-badge').click();
  for (const width of [1920,390]) {
    await page.setViewportSize({width,height:width===390?844:1080});
    for (const button of await page.locator('.watch-button').all()) {
      const b=await button.boundingBox(); assert(Math.abs(b.width-b.height)<0.1,'watch circle '+width);
      assert.equal(await button.evaluate(n=>getComputedStyle(n).flexShrink),'0');
    }
    const actions=await page.locator('.episode-actions').boundingBox();
    if(width===1920) assert((await page.locator('.episode-title').boundingBox()).width>300,'desktop action flex does not squeeze episode text');
    for(const selector of ['.media-panel','.job-details']) {
      const b=await page.locator(selector).boundingBox();
      assert(Math.abs(b.x+b.width-actions.x-actions.width)<2,'details right edge '+width);
      assert(b.y>actions.y+35,'details below buttons');
      assert(b.x>=0 && b.x+b.width<=width,'details within viewport');
      if(width===1920) assert(b.width<=480,'desktop details bounded to 480px');
      if(width===390) assert(Math.abs(b.width-(await page.locator('.episode-line').boundingBox()).width)<2,'mobile details full row width');
    }
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no overflow '+width);
    await page.screenshot({path:width===1920?'/tmp/dubs-layout-desktop.png':'/tmp/dubs-layout-mobile.png',fullPage:true});
  }
  await page.locator('.bottom-nav [data-tab=settings]').click();
  await page.waitForFunction(()=>document.querySelector('.counter').hidden);
  assert(await page.locator('.counter').isHidden());
  assert.equal(await page.locator('h1:visible,h2:visible').allTextContents().then(x=>x.filter(t=>t==='Настройки').length),1);
  for(const width of [390,1920]) {
    await page.setViewportSize({width,height:1080});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'settings no overflow');
    assert.equal(await page.locator('#settings').evaluate(n=>getComputedStyle(n).gridTemplateColumns.split(' ').length),width===390?1:2);
  }
  await page.screenshot({path:'/tmp/dubs-layout-settings.png',fullPage:true});
  await page.locator('.bottom-nav [data-tab=released]').click();
  await page.waitForSelector('.download-ring');
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'mobile/android/tv/src/main/assets/tv.js'),'utf8')});
  await page.waitForTimeout(250);
  await ring.focus();
  const expanded=await ring.getAttribute('aria-expanded');
  await page.keyboard.press('Enter');
  assert.notEqual(await ring.getAttribute('aria-expanded'),expanded,'TV Enter toggles ring');
  await page.keyboard.press('ArrowRight');
  assert(await page.locator('.media-badge').evaluate(n=>n===document.activeElement),'TV ring to media action');
  await page.evaluate(()=>{data.actions[31]={state:"queued",message:"Ожидаем релиз"};render();});
  assert.equal(await ring.locator(".ring-label").textContent(),"…");
  assert(await details.isHidden(),"closed choice survives queued rerender");
  await ring.click();
  assert.equal(await details.textContent(),"В очереди");
  // A failed task must retain the real download handler, including visible failure toast.
  await page.evaluate(()=>{
    data.released[0].episodes[0].hasFile=false;
    data.actions[31]={state:'error',message:'Не удалось скачать'};
    openJobDetails.delete('31');
    render();
  });
  const retry=page.getByRole('button',{name:'Скачать',exact:true});
  assert(await retry.isEnabled(),'failed download remains retryable');
  assert(await details.isHidden());
  rejectAction=true;
  await retry.click();
  await page.waitForFunction(()=>document.querySelector('.episode-toast')?.textContent==='Повторите попытку позже');
  assert(await page.locator('.episode-toast').isVisible(),'action failure toast remains visible');
  assert(await retry.isEnabled(),'failed retry restores button');
  assert(await details.isHidden(),'toast does not force inline status open');
  rejectAction=false;
  await retry.click();
  await page.waitForFunction(()=>data.actions[31].state==='queued');
  assert.equal(actions.length,2);
  assert(actions.every(a=>a.op==='download' && a.episode==='31' && a.csrf==='x'.repeat(43)));
  // Exercise future controls directly with the real controls renderer and handlers.
  await page.evaluate(()=>{
    data.actions[32]={state:'error',message:'Не удалось запланировать'};
    const host=document.createElement('div');host.id='future-controls';
    host.append(controls({id:32,season:1,number:2,future:true},null));
    document.querySelector('main').append(host);
  });
  const future=page.locator('#future-controls');
  assert(await future.getByRole('button',{name:'Запланировать',exact:true}).isEnabled());
  assert.equal(await future.getByRole('button',{name:'Отменить',exact:true}).count(),0);
  await future.locator('.download-ring').click();
  assert.equal(actions.length,2,'future error ring never dispatches cancel');
  assert(await future.locator('.job-details').isVisible());
  await future.getByRole('button',{name:'Запланировать',exact:true}).click();
  await page.waitForFunction(()=>!!data.scheduled?.[32]);
  assert.equal(actions[2].op,'download');assert.equal(actions[2].episode,'32');
  await page.evaluate(()=>{
    const host=document.querySelector('#future-controls');
    host.replaceChildren(controls({id:32,season:1,number:2,future:true},null));
  });
  assert(await future.getByRole('button',{name:'Запланировано',exact:true}).isDisabled());
  await future.locator('.download-ring').click();
  assert.equal(actions.length,3,'active future ring only discloses');
  await future.getByRole('button',{name:'Отменить',exact:true}).click();
  await page.waitForFunction(()=>!data.scheduled?.[32]);
  assert.equal(actions[3].op,'cancel');assert.equal(actions[3].episode,'32');
  assert.deepEqual(errors,[]);
  await browser.close();
  console.log('PASS compact layout: circular group/episode checks, disclosure click/Enter/poll persistence/error escaping, desktop/mobile right edges, overflow, top navigation removal, settings badge/grid, TV ring');
})().catch(e=>{console.error(e);process.exit(1)});
