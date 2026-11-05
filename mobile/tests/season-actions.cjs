const fs=require('fs'),path=require('path'),assert=require('assert'),{execFileSync}=require('child_process'),{chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'), html=execFileSync('python3',[path.join(__dirname,'render-fixture.py')],{encoding:'utf8'});
const episodes=[{id:31,seriesId:7,season:2,number:1,title:'Episode 1',airDate:'2020-01-01'},{id:32,seriesId:7,season:2,number:2,title:'Episode 2',airDate:'2020-01-02'},{id:33,seriesId:7,season:3,number:1,title:'Episode 3',airDate:'2020-01-03'}];
(async()=>{let browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/opt/google/chrome/chrome',args:['--no-sandbox','--disable-gpu']});
for(const [width,tv] of [[390,false],[1280,false],[1280,true]]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[],actions=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://panel.test/**',route=>{const u=new URL(route.request().url()),p=u.pathname;
 if(['/style.css','/ui.js'].includes(p))return route.fulfill({contentType:p.endsWith('css')?'text/css':'text/javascript',body:fs.readFileSync(root+'/src'+p,'utf8')});
 if(p==='/api/episodes')return route.fulfill({json:{updated:1,actions:{},scheduled:{},upcoming:[],released:[{seriesId:7,title:'Тестовый сериал',episodes}]}});
 if(p==='/api/session')return route.fulfill({json:{csrf:'x'.repeat(43)}});
 if(p==='/api/studio-order')return route.fulfill({json:{order:[],effectiveOrder:[],state:'applied'}});
 if(p==='/api/action'){actions.push(Object.fromEntries(new URLSearchParams(route.request().postData())));return route.fulfill({json:{jobs:[{episode:31,job:'a'.repeat(32)}]}})}
 if(p.startsWith('/api/'))return route.fulfill({json:{events:[],cursor:0,enabled:false,types:[]}});
 if(p.startsWith('/cover/'))return route.fulfill({status:404,body:''});
 return route.fulfill({contentType:'text/html',body:tv?html.replace('<html','<html class="dubs-tv"'):html});});
 await page.goto('https://panel.test/#released');await page.waitForSelector('.episode-show');await page.locator('summary').first().click();
 assert.equal(await page.locator('.season-header').count(),2);assert.equal(await page.locator('.season-title').first().innerText(),'Сезон 2');
 const bounds=await page.evaluate(()=>{let a=document.querySelector('.season-header .card-actions').getBoundingClientRect(), b=document.querySelector('.episode-card>.card-actions').getBoundingClientRect(), t=document.querySelector('.season-title').getBoundingClientRect();return {a:a.toJSON(),b:b.toJSON(),t:t.toJSON(),sw:document.documentElement.scrollWidth,iw:innerWidth}});
 assert(Math.abs(bounds.a.right-bounds.b.right)<2,JSON.stringify(bounds));assert(Math.abs(bounds.a.left-bounds.b.left)<2,JSON.stringify(bounds));assert(bounds.t.right<=bounds.a.left);assert(bounds.sw<=bounds.iw);
 await page.locator('.season-header .bulk-download').first().click();await page.waitForTimeout(300);assert(actions.some(x=>x.op==='download_season'&&x.episode==='31'),JSON.stringify(actions));assert.deepEqual(errors,[]);
 console.log('PASS season layout and scoped download:',width,tv?'TV':'web');await page.close();}
 await browser.close();})().catch(e=>{console.error(e);process.exit(1)});
