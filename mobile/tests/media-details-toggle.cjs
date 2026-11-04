const fs=require('fs'),path=require('path'),assert=require('assert'),{execFileSync}=require('child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const html=execFileSync('python3',[path.join(__dirname,'render-fixture.py')],{encoding:'utf8'});
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',e=>errors.push(e.message));const episode={id:31,seriesId:7,seriesTitle:'Fixture',title:'Episode',season:1,number:1,sonarrId:3,airDate:'2026-01-01',hasFile:true,jellyfinUrl:'https://media.example.org/web/#/details?id='+'a'.repeat(32),localMedia:{status:'verified',quality:{label:'1080p',width:1920,height:1080,codec:'h264',source:'jellyfin'},audioTracks:[{index:1,language:'rus',languageLabel:'Русский',title:'Custom tagged dub',studio:null,codec:'ac3',channels:6,isDefault:true},{index:2,language:'eng',languageLabel:'Английский',studio:null,codec:'aac',channels:2,isDefault:false}],verifiedAt:Date.now()/1000,source:'jellyfin'}};await page.route('https://panel.test/**',r=>{const u=new URL(r.request().url());if(u.pathname==='/ui.js')return r.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'src/ui.js'),'utf8')});if(u.pathname==='/style.css')return r.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(root,'src/style.css'),'utf8')});if(u.pathname==='/api/episodes')return r.fulfill({json:{updated:1,released:[{seriesId:7,title:'Fixture',episodes:[episode]}],upcoming:[],actions:{},scheduled:{}}});if(u.pathname==='/api/session')return r.fulfill({json:{csrf:'x'.repeat(43),userId:'f'.repeat(32)}});if(u.pathname==='/api/notifications/settings')return r.fulfill({json:{enabled:true,types:['new_episode'],showTitles:false}});if(u.pathname.startsWith('/api/notifications/'))return r.fulfill({json:{events:[],cursor:0,instanceId:'a'.repeat(32),devices:[],hasMore:false,nextBefore:null,settings:{enabled:true,types:['new_episode'],showTitles:false}}});if(u.pathname.startsWith('/cover/'))return r.fulfill({status:404,body:''});return r.fulfill({contentType:'text/html',body:html})});await page.goto('https://panel.test/');assert.equal(await page.locator('#media-details-switch').getAttribute('aria-checked'),'true');await page.waitForSelector('.episode-show');await page.locator('.episode-show').evaluate(n=>n.open=true);await page.waitForSelector('.media-badge');const compactBaseline=await page.locator('.episode-line').boundingBox();const libraryHref=await page.locator('.library-link').getAttribute('href');await page.locator('.media-badge').click();const tracks=await page.locator('.media-tracks').innerText();assert(tracks.includes('Custom tagged dub'),'raw track title retained without guessing studio');assert((await page.locator('.media-studio').allTextContents()).every(t=>t.includes('студия не указана')),'unknown studio remains honest');assert.equal(await page.locator('.media-tracks li').count(),2);assert.equal((tracks.match(/по умолчанию/g)||[]).length,1,'default only on actual default track');await page.locator('.media-badge').focus();await page.keyboard.press('Enter');assert(await page.locator('.media-panel').isHidden());await page.evaluate(()=>location.hash='settings');assert.equal(await page.locator('#media-details-switch').getAttribute('aria-checked'),'true');await page.locator('#media-details-switch').focus();await page.keyboard.press('Enter');await page.evaluate(()=>location.hash='released');assert.equal(await page.locator('.media-badge').count(),0);assert.equal(await page.locator('.media-panel').count(),0);assert.equal(await page.locator('.library-link').count(),1);await page.locator('.episode-show').evaluate(n=>n.open=true);const compactRow=await page.locator('.episode-line').boundingBox();assert(compactRow.height<=compactBaseline.height+.5,'hidden details add no empty row spacing');assert.equal(await page.locator('.library-link').getAttribute('href'),libraryHref,'library/player link unchanged');assert.equal(await page.evaluate(()=>localStorage.getItem('dubs-hide-media-details')),'1');await page.reload();await page.waitForSelector('.episode-show');assert.equal(await page.locator('.media-badge').count(),0);await page.evaluate(()=>location.hash='settings');await page.locator('#media-details-switch').click();await page.evaluate(()=>location.hash='released');await page.locator('.episode-show').evaluate(n=>n.open=true);await page.waitForSelector('.media-badge');assert.equal(errors.length,0,errors.join('; '));await page.evaluate(()=>location.hash='released?episode=31');await page.waitForFunction(()=>document.activeElement.classList.contains('episode-line'));assert(await page.locator('.episode-show').evaluate(n=>n.open));assert.equal(await page.locator('.download-task').count(),0,'notification tap never starts download');await page.evaluate(()=>location.hash='released?episode=999999');await page.waitForTimeout(100);assert.equal(await page.locator('.download-task').count(),0);await badgeRegressions(page);
await page.evaluate(()=>{data.released[0].episodes[0].localMedia.status='stale';render();});assert.equal(await page.locator('.media-badge.is-stale').count(),1);await page.evaluate(()=>{data.released[0].episodes[0].localMedia={status:'unknown',quality:null,audioTracks:[]};render();});assert.equal(await page.locator('.media-badge.is-unknown').count(),1);assert(!(await page.locator('.media-badge').textContent()).includes('1080p'),'unknown does not reuse old/preferred quality');console.log('PASS actual server template + shared UI media switch off/persistence/on, no script errors');await browser.close()})().catch(e=>{console.error(e);process.exit(1)});

async function badgeRegressions(page) {
  const aliases = [
    ['К-Б','Кураж-Бамбей','Кураж-Бомбей'], ['КвК','Кубик в Кубе'],
    ['Nova','NovaMedia','NovaFilm'], ['RHS','Red Head Sound'], ['Lost','LostFilm'],
    ['HDr','HDRezka Studio','HDRezka18+','HDRezka 18+','HDRezka'], ['Alex','AlexFilm'],
    ['VP','Viruse Project','Virus Project'], ['DMS','Dragon Money Studio','DMS'],
    ['RUd','RUDUB'], ['LE-P','LE-Production'], ['Cold','ColdFilm'],
    ['SNDK','Сыендук','Syenduk','sndk'], ['?','Other Russian studio',null]
  ];
  const set = async (studios, preferred, tracks, quality) => {
    await page.evaluate(({studios,preferred,tracks,quality}) => {
      document.querySelector('#media-badge-config').setAttribute('data-config',JSON.stringify({studios,preferred}));
      const e=data.released[0].episodes[0];
      e.localMedia={status:'verified',quality:quality||{label:'1080p H264',codec:'HEVC'},audioTracks:tracks.map(studio=>({studio,language:'rus',title:'Raw tag H264',codec:'ac3',isDefault:true}))};
      render(); document.querySelector('.episode-show').open=true;
    },{studios,preferred,tracks,quality});
  };
  for (const [short,...names] of aliases) for (const name of names) {
    await set(names.filter(Boolean),{},[name]);
    assert.equal(await page.locator('.media-badge').textContent(),'1080p '+short);
  }
  // Test the repository catalog itself, including its combined canonical names.
  const catalog = JSON.parse(fs.readFileSync(path.join(root,'studios.json'),'utf8')).map(s=>s.name);
  const expected = ['RHS','Lost','КвК','Nova','HDr','Alex','VP','DMS','RUd','LE-P','Cold','К-Б','SNDK','?'];
  assert.equal(catalog.length,expected.length);
  for (let i=0;i<catalog.length;i++) {
    await set(catalog,{},[catalog[i]]);
    assert.equal(await page.locator('.media-badge').textContent(),'1080p '+expected[i],catalog[i]);
  }
  await set(catalog,{'7':'NovaMedia / NovaFilm'},['LostFilm','NovaMedia / NovaFilm'],{label:'1080p H264',width:1920,height:800,codec:'h264'});
  assert.equal(await page.locator('.media-badge').textContent(),'1080p Nova');
  await set(catalog,{'7':'Сыендук / Syenduk / sndk'},['LostFilm','sndk']);
  assert.equal(await page.locator('.media-badge').textContent(),'1080p SNDK');
  await set(['NovaMedia / NovaFilm','LostFilm'],{},['LostFilm','NovaFilm']);
  assert.equal(await page.locator('.media-badge').textContent(),'1080p Nova');
  await set(catalog,{},['NovaMedia / NovaFilm'],{label:'1080i HEVC',width:1920,height:800,codec:'hevc'});
  assert.equal(await page.locator('.media-badge').textContent(),'1080i Nova');
  await set(catalog,{},['NovaMedia / NovaFilm'],{label:'H264',height:720,codec:'h264'});
  assert.equal(await page.locator('.media-badge').textContent(),'720p Nova');
  await set(['LostFilm','Кураж-Бамбей'],{},['Кураж-Бомбей','LostFilm','LostFilm']);
  assert.equal(await page.locator('.media-badge').textContent(),'1080p Lost');
  await set(['LostFilm','Кураж-Бамбей'],{'7':'Кураж-Бамбей'},['LostFilm','Кураж-Бомбей']);
  assert.equal(await page.locator('.media-badge').textContent(),'1080p К-Б');
  await set(['LostFilm','Кураж-Бамбей'],{'7':'AlexFilm'},['Кураж-Бомбей','LostFilm']);
  assert.equal(await page.locator('.media-badge').textContent(),'1080p Lost');
  await set(['Кураж-Бамбей','LostFilm'],{},['LostFilm','Кураж-Бомбей']);
  assert.equal(await page.locator('.media-badge').textContent(),'1080p К-Б');
  await page.locator('.media-badge').click();
  assert.equal(await page.locator('.media-badge').getAttribute('aria-expanded'),'true');
  assert((await page.locator('.media-panel').innerText()).includes('HEVC'));
  assert((await page.locator('.media-panel').innerText()).includes('Raw tag H264'));
  assert.equal(await page.locator('.media-tracks li').count(),2);
  await page.locator('.media-badge').click();
  assert.deepEqual(await page.locator('.episode-actions > a, .episode-actions > button').allTextContents(),['▶ В медиатеке','1080p К-Б','Найти','✓']);
  await page.locator('.library-link').focus();
  for (const selector of ['.media-badge','.episode-actions button.quiet','.episode-actions .watch-button']) {
    await page.keyboard.press('Tab');
    assert(await page.locator(selector).evaluate(n=>n===document.activeElement),'DOM tab order '+selector);
  }
  for (const width of [320,390,768,1920]) {
    await page.setViewportSize({width,height:1080});
    await page.evaluate(tv=>document.documentElement.classList.toggle('dubs-tv',tv),width===1920);
    for(const selector of ['.library-link','.media-badge','.episode-actions .watch-button']) {
      const box=await page.locator(selector).boundingBox();
      assert(box.x>=0 && box.x+box.width<=width+1,selector+' stays within '+width);
    }
  }
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'mobile/android/tv/src/main/assets/tv.js'),'utf8')});
  await page.locator('.library-link').focus();
  for (const selector of ['.media-badge','.episode-actions button.quiet','.episode-actions .watch-button']) {
    await page.keyboard.press('ArrowRight');
    assert(await page.locator(selector).evaluate(n=>n===document.activeElement),'TV short right '+selector);
  }
  await set(['LostFilm'],{},[null],{label:'HEVC',codec:'HEVC'});
  assert.equal(await page.locator('.media-badge').textContent(),'Проверено ?');
  console.log('PASS badge aliases, actual-only global/manual ranking, reordered catalog, details, DOM/tab/TV order and mobile/TV bounds');
}
