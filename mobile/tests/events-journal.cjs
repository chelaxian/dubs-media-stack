const fs=require('fs'),path=require('path'),assert=require('assert'),{execFileSync}=require('child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
const html=execFileSync('python3',[path.join(__dirname,'render-fixture.py')],{encoding:'utf8'});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox']});
 try {
  const page=await browser.newPage();const requests=[],errors=[],posts=[];let account={enabled:true,types:["new_episode"],showTitles:false};page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
   window.permissionRequests=0;window.delivered=[];
   window.Notification=class {
    static permission='default';
    static async requestPermission(){++window.permissionRequests;this.permission='granted';return 'granted';}
    constructor(title,options){window.delivered.push({title,options});} addEventListener(){} close(){}
   };
  });
  await page.route('https://panel.test/**',r=>{
   const url=new URL(r.request().url());requests.push(url.pathname+url.search);
   if(url.pathname==='/ui.js'||url.pathname==='/style.css')return r.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(root,'src',url.pathname.slice(1)),'utf8')});
   if(url.pathname==='/api/episodes')return r.fulfill({json:{updated:1,released:[],upcoming:[],actions:{},scheduled:{}}});
   if(url.pathname==='/api/session')return r.fulfill({json:{csrf:'y'.repeat(43)}});
   if(url.pathname==='/api/notifications/settings'){if(r.request().method()==='POST'){const body=new URLSearchParams(r.request().postData());posts.push(Object.fromEntries(body));account={enabled:body.get('enabled')==='true',types:JSON.parse(body.get('types')),showTitles:body.get('show_titles')==='true'};}return r.fulfill({json:account});}
   if(url.pathname.startsWith('/api/notifications/'))return r.fulfill({json:{events:[],cursor:0,instanceId:'a'.repeat(32),nextBefore:null,settings:{enabled:true,types:['new_episode'],showTitles:false}}});
   return r.fulfill({contentType:'text/html; charset=utf-8',body:html});
  });
  await page.goto('https://panel.test/#settings?section=events');
  await page.waitForFunction(()=>localStorage.getItem('dubs-events-instance')!==null);
  await page.waitForSelector('#events-journal',{state:'visible'});
  assert.equal(await page.evaluate(()=>window.permissionRequests),0,'no permission request on launch/journal');
  assert(!requests.some(url=>url.includes('before=null')),'initial history has no invalid before=null');
  await page.locator('#events-notify-request').click();
  assert.equal(await page.evaluate(()=>window.permissionRequests),1,'only explicit opt-in requests permission');
  await page.locator('#events-show-titles').click();
  await page.waitForFunction(()=>!document.querySelector('#events-show-titles').disabled);
  assert.equal(posts[0].csrf,'y'.repeat(43),'account writes obtain fresh CSRF');assert.equal(posts[0].show_titles,'true');
  assert.deepEqual(Object.keys(posts[0]).sort(),['csrf','enabled','show_titles','types']);
  await page.locator('#events-show-titles').click();await page.waitForFunction(()=>!document.querySelector('#events-show-titles').disabled);
  const apply=async(epoch,id)=>page.evaluate(({epoch,id})=>applyEvents({instanceId:epoch,cursor:id,settings:{enabled:true,types:['new_episode'],showTitles:false},events:[{id,type:'new_episode',episodeId:31,title:'Private title',message:'Private details'}]}),{epoch,id});
  await apply('a'.repeat(32),1);await apply('a'.repeat(32),1);
  assert.equal(await page.evaluate(()=>window.delivered.length),1,'duplicate does not notify');
  assert.equal(await page.evaluate(()=>window.delivered[0].title),'Dubs');
  assert(!JSON.stringify(await page.evaluate(()=>window.delivered)).includes('Private'),'generic OS payload by default');
  await apply('b'.repeat(32),1);
  assert.equal(await page.evaluate(()=>window.delivered.length),1,'epoch reset baselines silently');
  await apply('b'.repeat(32),2);
  assert.equal(await page.evaluate(()=>window.delivered.length),2,'new epoch has independent event IDs');
  await page.locator('#events-device-enabled').click();await apply('b'.repeat(32),3);
  assert.equal(await page.evaluate(()=>window.delivered.length),2,'local disable stops delivery');
  assert.equal(errors.length,0,errors.join('; '));
  const native=await browser.newPage({userAgent:'DubsNative/1.3 Android'});
  await native.route('https://native.test/**',r=>{
   const pathname=new URL(r.request().url()).pathname;
   if(pathname==='/ui.js')return r.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'src/ui.js'),'utf8')});
   if(pathname.startsWith('/api/'))return r.fulfill({json:{events:[],cursor:0,instanceId:'a'.repeat(32),released:[],upcoming:[],settings:{enabled:true,types:[]},types:[]}});
   return r.fulfill({contentType:'text/html; charset=utf-8',body:html});
  });
  await native.goto('https://native.test/#settings');assert(await native.locator('#events-device-enabled').isHidden());assert(await native.locator('#events-notify-request').isHidden());
  console.log('PASS real journal: valid first page, gesture-only permission, privacy, dedupe, opaque epoch reset, local disable, native browser suppression');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
