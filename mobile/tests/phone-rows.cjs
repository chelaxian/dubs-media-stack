const fs=require('fs'),path=require('path'),assert=require('assert'),{execFileSync}=require('child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const env={...process.env,DUBS_TEST_WATCHING:JSON.stringify([{title:'Первый брак Джорджи и Мэнди',series:{id:7,title:'Georgie and Mandy',year:2024,status:'continuing'}}]),DUBS_TEST_PREFERENCES:JSON.stringify({'7':{studio:'LostFilm',mode:'fallback'}})};
const html=execFileSync('python3',[path.join(__dirname,'render-fixture.py')],{env,encoding:'utf8'});
const data={updated:1,scheduled:{99:{state:'scheduled'}},actions:{31:{state:'waiting',message:'Ожидаем раздачу',percent:0},99:{state:'queued',message:'Запланировано'}},released:[{seriesId:7,title:'Первый брак Джорджи и Мэнди',episodes:[{id:31,seriesId:7,sonarrId:1,season:3,number:5,title:'Длинное название серии',airDate:'2026-11-05'},{id:32,seriesId:7,sonarrId:2,season:3,number:4,title:'Уже скачанная серия',airDate:'2026-11-01',hasFile:true,jellyfinUrl:'https://jellyfin.example.org/web/#/details?id=abc',localMedia:{status:'verified',quality:{label:'1080p'},audioTracks:[{language:'rus',studio:'LostFilm'}]}}]}],upcoming:[{id:99,seriesId:7,seriesTitle:'Первый брак Джорджи и Мэнди',title:'A Snotty Attitude and a Party on a School Night',season:3,number:5,airDate:'2099-11-06',calendarDate:'2099-11-06',future:true,sonarrId:9}]};
data.released[0].episodes.push({id:33,seriesId:7,sonarrId:3,season:3,number:3,title:'Ещё не заказана',airDate:'2026-10-01'});
data.upcoming=[{seriesId:7,title:'Первый брак Джорджи и Мэнди',episodes:data.upcoming}];
(async()=>{
 const browser=await chromium.launch({executablePath:'/opt/google/chrome/chrome',args:['--no-sandbox','--disable-gpu']});
 for(const native of [false,true]){
  const page=await browser.newPage({viewport:{width:390,height:844},hasTouch:true});page.setDefaultTimeout(6000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('https://panel.test/**',route=>{
   const url=new URL(route.request().url());
   if(['/style.css','/ui.js'].includes(url.pathname))return route.fulfill({contentType:url.pathname.endsWith('css')?'text/css':'text/javascript',body:fs.readFileSync(path.join(root,'src',url.pathname.slice(1)),'utf8')});
   if(url.pathname==='/api/episodes')return route.fulfill({json:data});
   if(url.pathname==='/api/studio-order')return route.fulfill({json:{order:[],effectiveOrder:[],state:'applied'}});
   if(url.pathname.startsWith('/api/'))return route.fulfill({json:{events:[],cursor:0,enabled:false,types:[]}});
   if(url.pathname.startsWith('/cover/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="70"><rect width="100" height="70" fill="#632c38"/></svg>'});
   return route.fulfill({contentType:'text/html',body:html.replace('<!doctype html>','<!doctype html><html'+(native?' class="dubs-native-mobile dubs-native-ios"':'')+'>')});
  });
  await page.goto('https://panel.test/#released');await page.waitForSelector('.card-actions');
  for(const width of [320,375,390,430]){
   await page.setViewportSize({width,height:844});
   await page.locator('.episode-show').first().evaluate(n=>n.open=true);
   const edge=await page.locator('.episode-card').first().evaluate(n=>({header:n.querySelector('.card-actions').getBoundingClientRect().right,episodes:[...n.querySelectorAll('.episode-line>.episode-actions')].map(x=>x.getBoundingClientRect().right)}));
   assert(Math.abs((await page.locator('.card-actions').first().boundingBox()).width-(await page.locator('.episode-line>.episode-actions').first().boundingBox()).width)<1,'shared left and right edges');
   for(const right of edge.episodes)assert(Math.abs(right-edge.header)<1,'matching show/episode right gutter');
   const pending=await page.locator('.is-not-ordered').first().evaluate(n=>{const b=x=>x.getBoundingClientRect().toJSON();return{box:b(n),download:b(n.querySelector('.episode-primary')),find:b(n.querySelector('.episode-find')),watch:b(n.querySelector('.watch-button'))}});
   assert(Math.abs(pending.box.width-pending.download.width)<1,'pending download full width');
   assert(Math.abs(pending.find.y-pending.watch.y)<1,'find and check same row');
   assert(pending.download.bottom<=pending.find.y,'download above find');
   const active=await page.locator('.is-active-download').evaluate(n=>{const b=x=>x.getBoundingClientRect().toJSON();return{box:b(n),cancel:b(n.querySelector('.download-task>button')),ring:b(n.querySelector('.download-ring')),label:b(n.querySelector('.ring-label')),find:b(n.querySelector('.episode-find')),watch:b(n.querySelector('.watch-button'))}});
   assert(Math.abs(active.cancel.width-active.find.width)<1,'cancel/find width');
   assert(Math.abs(active.ring.width-active.watch.width)<1 && Math.abs(active.ring.height-active.watch.height)<1,'ring/watch dimensions');
   assert(Math.abs(active.ring.x-active.watch.x)<1,'ring/watch aligned');
   assert(Math.abs(active.ring.x+active.ring.width/2-active.label.x-active.label.width/2)<1,'ring label centered X');
   assert(Math.abs(active.ring.y+active.ring.height/2-active.label.y-active.label.height/2)<1,'ring label centered Y');
   const row=await page.locator('.episode-card').first().evaluate(n=>{const s=n.querySelector('summary').getBoundingClientRect(),p=n.querySelector('.cover').getBoundingClientRect(),a=n.querySelector('.card-actions').getBoundingClientRect(),t=n.querySelector('.show-copy').getBoundingClientRect();return{s:s.toJSON(),p:p.toJSON(),a:a.toJSON(),t:t.toJSON()};});
   assert(row.a.top<row.p.bottom&&row.a.bottom>row.p.top,'released same row '+width);
   assert(row.t.right<=row.a.left+1,'released text/actions overlap '+width);
   assert(row.a.right<=width,'released overflow '+width);
   await page.locator('.episode-show').evaluate(n=>n.open=true);
   const ready=await page.locator('.episode-line[data-episode-id="32"]').evaluate(n=>{
    const rect=s=>n.querySelector(s).getBoundingClientRect().toJSON();
    return {media:rect('.media-badge'),find:rect('.episode-find'),play:rect('.episode-primary'),watch:rect('.watch-button'),code:rect('.episode-number'),title:rect('.episode-title'),height:n.querySelector('.episode-actions').getBoundingClientRect().height};
   });
   assert(Math.abs(ready.media.top-ready.find.top)<2,'quality/find first row '+width);
   assert(Math.abs(ready.play.top-ready.watch.top)<2,'library/watch second row '+width);
   assert(ready.play.top>=ready.media.bottom,'second row below first '+width);
   assert(ready.height<=72,'exactly two action rows '+width);
   assert(ready.code.right<=ready.title.left,'episode code overlaps title '+width);
   await page.screenshot({path:'/tmp/dubs-phone-released-'+(native?'native':'web')+'-'+width+'.png'});
   await page.evaluate(()=>location.hash='#upcoming');await page.waitForSelector('.upcoming-line');
   const future=await page.locator('.upcoming-line').first().evaluate(n=>{const p=n.querySelector('.cover').getBoundingClientRect(),a=n.querySelector('.episode-actions').getBoundingClientRect(),t=n.children[2].getBoundingClientRect();return {p:p.toJSON(),a:a.toJSON(),t:t.toJSON(),buttons:[...n.querySelectorAll('.episode-actions button')].map(b=>b.getBoundingClientRect().toJSON())};});
   assert(future.a.top<future.p.bottom&&future.a.bottom>future.p.top,'upcoming same row '+width);
   assert(future.t.right<=future.a.left+1,'upcoming overlap '+width);
   for(const b of future.buttons){assert(b.right<=width&&b.left>=0,'upcoming button overflow '+width);}
   assert(future.buttons[1].top>=future.buttons[0].bottom,'cancel belongs to second action row '+width);
   assert(Math.abs(future.buttons[1].top-future.buttons[2].top)<3,'cancel and ring share second row '+width);
   assert(future.a.height<=70,'actions stay within compact two-row group '+width);
   assert(future.p.width>=66,'larger poster '+width);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'page overflow '+width);
   await page.screenshot({path:'/tmp/dubs-phone-upcoming-'+(native?'native':'web')+'-'+width+'.png'});
   await page.evaluate(()=>location.hash='#released');await page.waitForSelector('.card-actions');
  }
  await page.evaluate(()=>location.hash='#voice');await page.waitForSelector('.show-voice.is-selected');
  assert.equal(await page.locator('.show-voice.is-selected').evaluate(n=>getComputedStyle(n).color),'rgb(255, 53, 61)');
  if(native){assert((await page.locator('.masthead').boundingBox()).y>=48,'native controls reserve');}
  const result=await page.evaluate(()=>{const g=new Event('gesturestart',{bubbles:true,cancelable:true});document.dispatchEvent(g);const multi=new Event('touchmove',{bubbles:true,cancelable:true});Object.defineProperty(multi,'touches',{value:[{},{}]});document.dispatchEvent(multi);const single=new Event('touchmove',{bubbles:true,cancelable:true});Object.defineProperty(single,'touches',{value:[{}]});document.dispatchEvent(single);return {gesture:g.defaultPrevented,multi:multi.defaultPrevented,single:single.defaultPrevented};});
  assert.equal(result.gesture,native);assert.equal(result.multi,native);assert.equal(result.single,false);
  assert.deepEqual(errors,[]);await page.close();
 }
 const desktop=await browser.newPage({viewport:{width:1280,height:900}});
 await desktop.route('https://desktop.test/**',route=>{
  const u=new URL(route.request().url());
  if(u.pathname==='/api/episodes')return route.fulfill({json:data});
  if(u.pathname.startsWith('/api/'))return route.fulfill({json:{events:[],enabled:false,order:[],state:'applied'}});
  if(['/style.css','/ui.js'].includes(u.pathname))return route.fulfill({body:fs.readFileSync(path.join(root,'src',u.pathname.slice(1)),'utf8'),contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript'});
  if(u.pathname.startsWith('/cover/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="70"><rect width="100" height="70" fill="#632c38"/></svg>'});
  return route.fulfill({body:html,contentType:'text/html'});
 });
 await desktop.goto('https://desktop.test/#upcoming');await desktop.waitForSelector('.upcoming-line');
 for(const width of [768,1280,1920]){
  await desktop.setViewportSize({width,height:900});
  const row=await desktop.locator('.upcoming-line').evaluate(n=>{const r=s=>n.querySelector(s).getBoundingClientRect().toJSON();return {row:n.getBoundingClientRect().toJSON(),poster:r('.cover'),text:n.children[2].getBoundingClientRect().toJSON(),actions:r('.episode-actions')};});
  assert(row.actions.top<row.poster.bottom&&row.actions.bottom>row.poster.top,'desktop upcoming one track '+width);
  assert(Math.abs(row.actions.right-(row.row.right-30))<2,'desktop upcoming actions at right edge '+width);
  assert(row.text.right<=row.actions.left,'desktop upcoming copy/action collision '+width);
  await desktop.screenshot({path:'/tmp/dubs-desktop-upcoming-'+width+'.png'});
 }
 await desktop.goto('https://desktop.test/#released');await desktop.waitForSelector('.card-actions');await desktop.locator('.episode-show').evaluate(n=>n.open=true);
 for(const tv of [false,true]){
  await desktop.evaluate(tv=>document.documentElement.classList.toggle('dubs-tv',tv),tv);
  for(const width of (tv?[1280,1920]:[768,1280,1920])){
   await desktop.setViewportSize({width,height:1080});
   const rails=await desktop.locator('.episode-card').evaluate(n=>{const h=n.querySelector('.card-actions').getBoundingClientRect();return{left:h.left,right:h.right,rows:[...n.querySelectorAll('.episode-line>.episode-actions')].map(x=>{const b=x.getBoundingClientRect();return{left:b.left,right:b.right}})}});
   for(const r of rails.rows){assert(Math.abs(r.left-rails.left)<1,'desktop/TV shared rail left');assert(Math.abs(r.right-rails.right)<1,'desktop/TV shared rail right');}
  }
 }
 await desktop.close();
 await browser.close();console.log('PASS phone cards 320–430: larger poster beside two action rows, no overlap/overflow, selected studio red, iOS controls reserve, native-only gesture lock with normal scrolling/browser zoom retained');
})().catch(e=>{console.error(e);process.exit(1)});
