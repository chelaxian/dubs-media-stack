package app.dubs.panel;
import android.app.*;import android.os.*;import android.content.*;import android.graphics.Color;import android.net.Uri;import android.net.http.SslError;import android.view.*;import android.webkit.*;import android.widget.*;
import org.json.JSONObject;import org.json.JSONTokener;
public final class MainActivity extends Activity {
 private static final int REQUEST_POST_NOTIFICATIONS=9101;
 private String home;private AlertDialog serverDialog;private boolean switchingServer;
 private AlertDialog notificationsDialog;private Switch deviceToggle;private TextView notifStatus;private boolean enablingNotifications;
 private String notificationFailure;
 private String pendingHash; private long registrationGeneration,permissionGeneration; private String permissionOrigin;
 private static final int REGISTRATION_POLLS=24;
 private boolean television(){return getPackageName().endsWith(".tv");}
 /** Prefs live in a FIXED file so a build-var applicationId change never orphans the chosen server; the legacy activity-scoped file is migrated once. */
 private SharedPreferences prefs(){return getSharedPreferences("dubs_prefs",MODE_PRIVATE);}
 private WebView web;private ProgressBar loading;private LinearLayout root;
 private boolean panel(Uri u){String origin=ServerAddress.normalize(u.buildUpon().path("/").clearQuery().fragment(null).build().toString());return home!=null&&home.equals(origin)&&u.getUserInfo()==null;}
 private boolean library(Uri u){if(!"https".equals(u.getScheme())||u.getHost()==null||u.getUserInfo()!=null||u.getQuery()!=null||!"/web/".equals(u.getPath()))return false;String f=u.getFragment();return f!=null&&f.matches("/details\\?id=[a-fA-F0-9]{32}(&serverId=[a-fA-F0-9]{32})?");}
 private void serverSettings(){if(serverDialog!=null&&serverDialog.isShowing())return;
  final EditText address=new EditText(this);address.setSingleLine(true);address.setHint("https://panel.example.org");address.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_URI);address.setText(home==null?"":home);address.setSelectAllOnFocus(true);address.setPadding(24,16,24,16);
  AlertDialog.Builder builder=new AlertDialog.Builder(this).setTitle("Адрес веб-панели").setMessage("Укажите HTTPS-адрес панели Dubs с действительным сертификатом. При смене сервера потребуется войти заново.").setView(address).setPositiveButton("Подключиться",null);
  if(home!=null)builder.setNegativeButton("Отмена",null);serverDialog=builder.create();serverDialog.setCancelable(home!=null);
  serverDialog.setOnShowListener(d->{serverDialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{String next=ServerAddress.normalize(address.getText().toString());if(next==null){address.setError("Введите HTTPS-адрес без пути, логина и пароля");return;}boolean changed=!next.equals(home);serverDialog.dismiss();if(!changed){web.loadUrl(home);return;}switchingServer=true;disableNotifications(false);web.stopLoading();web.loadUrl("about:blank");web.clearHistory();web.clearCache(true);WebStorage.getInstance().deleteAllData();CookieManager.getInstance().removeAllCookies(ok->{CookieManager.getInstance().flush();home=next;prefs().edit().putString("panel_url",next).apply();switchingServer=false;EventStore.reset(this);NotificationSync.cancel(this);web.loadUrl(home);});});address.requestFocus();});serverDialog.show();
 }
 private void injectSettings(){web.evaluateJavascript("(()=>{const add=()=>{const p=document.querySelector('#events-panel');if(p&&!document.getElementById('native-events-settings')){const b=document.createElement('button');b.id='native-events-settings';b.type='button';b.className='quiet';b.textContent='Уведомления на этом устройстве';b.addEventListener('click',()=>{location.href='dubs-native://notifications'});p.appendChild(b);}const s=document.querySelector('#settings');if(!s||document.getElementById('native-server-settings'))return;const a=document.createElement('a');a.id='native-server-settings';a.href='dubs-settings://server';a.className='quiet';a.textContent='Изменить адрес веб-панели';s.prepend(a)};add();if(!window.dubsServerObserver){window.dubsServerObserver=new MutationObserver(add);window.dubsServerObserver.observe(document.body,{childList:true,subtree:true})}})()",null);}
 private void notificationsSettings(){if(home==null){serverSettings();return;}
  if(notificationsDialog!=null&&notificationsDialog.isShowing())return;
  EventStore s=EventStore.load(this);
  LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding((int)(getResources().getDisplayMetrics().density*24),(int)(getResources().getDisplayMetrics().density*12),(int)(getResources().getDisplayMetrics().density*24),0);
  notifStatus=new TextView(this);notifStatus.setText(statusLine(s));box.addView(notifStatus);
  deviceToggle=new Switch(this);deviceToggle.setText("События на этом устройстве");deviceToggle.setChecked(s.enabled()&&!s.needsReconnect()&&NotificationSync.notificationsAllowed(this));box.addView(deviceToggle);
  TextView hint=new TextView(this);hint.setText("Типы событий задаёт аккаунт: Настройки → «События и уведомления». Мгновенной доставки нет: фоновая проверка выполняется с периодом от 15 минут, ОС может отложить запуск.");hint.setTextSize(12);box.addView(hint);
  Button types=new Button(this);types.setText("Типы событий (аккаунт)");types.setOnClickListener(v->{notificationsDialog.dismiss();web.loadUrl(home+"#settings?section=events");});box.addView(types);
  Button system=new Button(this);system.setText("Системные настройки уведомлений");system.setOnClickListener(v->systemNotificationSettings());box.addView(system);
  Button revoke=new Button(this);revoke.setText("Отключить и отозвать устройство");revoke.setOnClickListener(v->{disableNotifications(true);refreshDialog();});box.addView(revoke);
  notificationsDialog=new AlertDialog.Builder(this).setTitle("События и уведомления").setView(box).setNegativeButton("Закрыть",null).create();
  deviceToggle.setOnCheckedChangeListener((sw,on)->{if(on)enableNotifications();else disableNotifications(true);refreshDialog();});
  notificationsDialog.show();
 }
 private String statusLine(EventStore s){
  if(notificationFailure!=null)return notificationFailure;
  if(s.needsReconnect())return "Регистрация отозвана. Выключите и снова включите уведомления.";
  if(!s.enabled()||!NotificationSync.notificationsAllowed(this))return NotificationSync.notificationsAllowed(this)?"Уведомления на этом устройстве выключены.":"Разрешение на уведомления не выдано: включите его в системных настройках, затем включите переключатель.";
  return "Устройство зарегистрировано. Фоновая проверка: с периодом от 15 минут (JobScheduler, переживает перезагрузку), ОС может откладывать.";
 }
 private void refreshDialog(){if(notificationsDialog==null||!notificationsDialog.isShowing())return;deviceToggle.setOnCheckedChangeListener(null);deviceToggle.setChecked(EventStore.load(this).enabled()&&!EventStore.load(this).needsReconnect()&&NotificationSync.notificationsAllowed(this));deviceToggle.setOnCheckedChangeListener((sw,on)->{if(on)enableNotifications();else disableNotifications(true);refreshDialog();});notifStatus.setText(statusLine(EventStore.load(this)));}
 private void enableNotifications(){
  notificationFailure=null;
  if(Build.VERSION.SDK_INT>=33&&!NotificationSync.notificationsAllowed(this)){permissionOrigin=home;permissionGeneration=++registrationGeneration;requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"},REQUEST_POST_NOTIFICATIONS);return;}
  completeEnable();
 }
 @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] results){
  super.onRequestPermissionsResult(code,permissions,results);
  if(code==REQUEST_POST_NOTIFICATIONS){
   if(permissionGeneration!=registrationGeneration||permissionOrigin==null||!permissionOrigin.equals(home))return;
   if(results.length>0&&results[0]==android.content.pm.PackageManager.PERMISSION_GRANTED)completeEnable();
   else{if(notifStatus!=null)notifStatus.setText("Разрешение не выдано. Откройте системные настройки уведомлений и повторите.");}
  }
 }
 private void completeEnable(){
  if(enablingNotifications)return;
  if(!NotificationSync.notificationsAllowed(this)){systemNotificationSettings();return;}
  if(home==null||web.getUrl()==null||!panel(Uri.parse(web.getUrl())))return;
  enablingNotifications=true;
  final String origin=home,nonce=java.util.UUID.randomUUID().toString().replace("-","");
  final long generation=++registrationGeneration;
  web.evaluateJavascript(NotificationSync.registrationMailboxScript(television()?"android_tv":"android",nonce),ignored->pollRegistration(origin,nonce,generation,0));
 }
 private void pollRegistration(String origin,String nonce,long generation,int count){
  if(generation!=registrationGeneration||switchingServer||!origin.equals(home)||web.getUrl()==null||!panel(Uri.parse(web.getUrl()))){web.evaluateJavascript(NotificationSync.registrationClearScript(nonce),null);return;}
  web.evaluateJavascript(NotificationSync.registrationPollScript(nonce),result->{
   String failureMessage="Регистрация не удалась: нет корректного ответа панели. Проверьте вход и сеть.";
   try{
    Object value=new JSONTokener(result).nextValue();
    String raw=value instanceof String?(String)value:"";
    if(raw.isEmpty()&&count<REGISTRATION_POLLS){web.postDelayed(()->pollRegistration(origin,nonce,generation,count+1),500);return;}
    web.evaluateJavascript(NotificationSync.registrationClearScript(nonce),null);
    if(generation!=registrationGeneration||!origin.equals(home)||switchingServer)return;
    JSONObject d=new JSONObject(raw);
    String token=d.optString("token"),device=d.optString("deviceId"),instance=d.optString("instanceId");
    long cursor=d.optLong("cursor",-1);
    if(!token.matches("[A-Za-z0-9_-]{43}")||device.isEmpty()||cursor<0||!instance.matches(NotificationSync.INSTANCE_PATTERN))throw new IllegalStateException();
    failureMessage="Не удалось безопасно сохранить токен. Уведомления не включены, проверьте хранилище устройства.";
    synchronized(EventStore.LOCK){
     EventStore state=new EventStore();
     state.setRegistration(token,device,origin,NotificationSync.fingerprint(origin),cursor,instance);
     state.setEnabled(true);state.save(this);NotificationSync.schedule(this);notificationFailure=null;
     new Thread(()->NotificationSync.pollOnce(this),"dubs-events-register").start();
    }
   }catch(Exception fail){notificationFailure=failureMessage;}
   enablingNotifications=false;refreshDialog();
  });
 }
 private void disableNotifications(boolean revoke){
  notificationFailure=null;
  ++registrationGeneration;enablingNotifications=false;
  synchronized(EventStore.LOCK){
   EventStore s=EventStore.load(this);EventStore.reset(this);NotificationSync.cancel(this);
   if(revoke&&!s.deviceId().isEmpty()&&web.getUrl()!=null&&panel(Uri.parse(web.getUrl())))web.evaluateJavascript(NotificationSync.revokeScript(s.deviceId()),null);
  }
 }
 private void systemNotificationSettings(){
  try{startActivity(new Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,getPackageName()));}catch(Exception missing){if(notifStatus!=null)notifStatus.setText("Откройте настройки системы → Приложения → Dubs → Уведомления вручную.");}
 }
 private void applyPendingHash(){
  if(pendingHash==null||web==null||home==null)return;
  web.loadUrl(home+pendingHash);
  pendingHash=null;
 }
 private void handleTap(Intent intent){
  if(intent==null||!"app.dubs.panel.EVENT_TAP".equals(intent.getAction())||home==null)return;
  String hash=NotificationSync.routeForTap(intent.getStringExtra("dubs_fp"),NotificationSync.fingerprint(home),EventStore.load(this).instanceId(),intent.getStringExtra("dubs_instance"),intent.getLongExtra("dubs_episode",0L),intent.getStringExtra("dubs_section"));
  if(hash==null)return;
  pendingHash=hash;
  if(web!=null&&web.getUrl()!=null&&panel(Uri.parse(web.getUrl())))applyPendingHash();
 }
 @Override public void onCreate(Bundle state){super.onCreate(state);if(!prefs().contains("panel_url")){String legacy=getPreferences(MODE_PRIVATE).getString("panel_url","");if(legacy.isEmpty())legacy=getSharedPreferences("MainActivity",MODE_PRIVATE).getString("panel_url","");if(legacy.isEmpty())legacy=getSharedPreferences(getPackageName()+".MainActivity",MODE_PRIVATE).getString("panel_url","");if(!legacy.isEmpty())prefs().edit().putString("panel_url",legacy).apply();}home=ServerAddress.normalize(prefs().getString("panel_url",""));root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(Color.BLACK);root.setOnApplyWindowInsetsListener((v,insets)->{v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;});setContentView(root);if(!television()){LinearLayout bar=new LinearLayout(this);bar.setOrientation(LinearLayout.HORIZONTAL);Button settings=new Button(this);settings.setText("⚙ Сервер");settings.setContentDescription("Настройки адреса веб-панели");settings.setOnClickListener(v->serverSettings());bar.addView(settings,new LinearLayout.LayoutParams(0,44*(int)getResources().getDisplayMetrics().density,1));Button events=new Button(this);events.setText("🔔 События");events.setContentDescription("События и уведомления на этом устройстве");events.setOnClickListener(v->notificationsSettings());bar.addView(events,new LinearLayout.LayoutParams(0,44*(int)getResources().getDisplayMetrics().density,1));root.addView(bar,new LinearLayout.LayoutParams(-1,44*(int)getResources().getDisplayMetrics().density));}
  loading=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);loading.setMax(100);root.addView(loading,new LinearLayout.LayoutParams(-1,5));web=new WebView(this);root.addView(web,new LinearLayout.LayoutParams(-1,0,1));web.setBackgroundColor(Color.BLACK);WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);s.setSupportMultipleWindows(false);s.setUserAgentString(s.getUserAgentString()+" DubsNative/1.2 Android"+(television()?" DubsTV/1.0":""));CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
  web.setWebChromeClient(new WebChromeClient(){@Override public void onProgressChanged(WebView v,int value){loading.setProgress(value);loading.setVisibility(value<100?View.VISIBLE:View.GONE);}});
  web.setWebViewClient(new WebViewClient(){@Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){Uri u=r.getUrl();if(r.isForMainFrame()&&panel(v.getUrl()==null?Uri.EMPTY:Uri.parse(v.getUrl()))){if("dubs-settings://server".equals(u.toString())){serverSettings();return true;}if("dubs-native://notifications".equals(u.toString())){notificationsSettings();return true;}if(openPlayerURL(u))return true;}if(!r.isForMainFrame())return !panel(u);if(panel(u))return false;if(panel(v.getUrl()==null?Uri.EMPTY:Uri.parse(v.getUrl()))&&library(u)){openEpisode(u);return true;}return true;}@Override public void onPageFinished(WebView v,String url){CookieManager.getInstance().flush();if(panel(Uri.parse(url))){if(pendingHash!=null)applyPendingHash();injectSettings();}if(television()&&panel(Uri.parse(url))){try{java.io.InputStream in=getAssets().open("tv.js");java.util.Scanner scanner=new java.util.Scanner(in,"UTF-8").useDelimiter("\\A");String js=scanner.hasNext()?scanner.next():"";scanner.close();web.evaluateJavascript(js,null);}catch(java.io.IOException ignored){}}}@Override public void onReceivedSslError(WebView v,SslErrorHandler h,SslError e){h.cancel();new AlertDialog.Builder(MainActivity.this).setTitle("Ошибка сертификата").setMessage("Защищённое соединение не установлено. Проверьте дату телефона и доступность сервера.").setPositiveButton("ОК",null).setNeutralButton("Изменить сервер",(d,w)->serverSettings()).show();}@Override public void onReceivedError(WebView v,WebResourceRequest r,WebResourceError e){if(r.isForMainFrame()&&!switchingServer)new AlertDialog.Builder(MainActivity.this).setTitle("Нет соединения").setMessage("Мини-ПК или сеть недоступны. Ваши задания сохранены на сервере.").setPositiveButton("Повторить",(d,w)->web.loadUrl(home)).setNegativeButton("Изменить сервер",(d,w)->serverSettings()).show();}});
  if(Build.VERSION.SDK_INT>=33)getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,()->handleBack());
  if(home==null)serverSettings();else web.loadUrl(home);
  handleTap(getIntent());
 }
 @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);handleTap(intent);}
 @Override protected void onResume(){super.onResume();EventStore s=EventStore.load(this);if(!s.enabled())return;String origin=home==null?"":home;if(!s.matchesOrigin(origin,NotificationSync.fingerprint(origin))){EventStore.reset(this);NotificationSync.cancel(this);return;}new Thread(()->NotificationSync.pollOnce(this),"dubs-events-fg").start();}
 private boolean openPlayerURL(Uri u){String raw=u.toString();String[] patterns={"^fladder://details[?]id=[a-fA-F0-9]{32}&userId=[a-fA-F0-9]{32}&serverId=[a-fA-F0-9]{32}$","^streamyfin://items/page[?]id=[a-fA-F0-9]{32}$","^jellyfin://item/[a-fA-F0-9-]{36}$"};
 for(String pattern:patterns){if(raw.matches(pattern)){String[] packages="fladder".equals(u.getScheme())?new String[]{"nl.jknaapen.fladder"}:"streamyfin".equals(u.getScheme())?new String[]{"com.fredrikburmester.streamyfin"}:television()?new String[]{"dev.jdtech.jellyfin.dubs.tv"}:new String[]{"dev.jdtech.jellyfin.dubs","dev.jdtech.jellyfin"};for(String pkg:packages){try{startActivity(new Intent(Intent.ACTION_VIEW,u).addCategory(Intent.CATEGORY_BROWSABLE).setPackage(pkg));return true;}catch(ActivityNotFoundException ignored){}}new AlertDialog.Builder(this).setTitle("Клиент не найден").setMessage("Установите выбранный клиент или измените его в настройках Dubs.").setPositiveButton("ОК",null).show();return true;}}
 if("dubs-browser".equals(u.getScheme())&&"open".equals(u.getHost())&&u.getQueryParameterNames().size()==1){String value=u.getQueryParameter("url");if(value!=null){Uri target=Uri.parse(value);if(library(target)&&"/web/".equals(target.getPath())){openBrowser(target);return true;}}}return false;
 }
 private void openEpisode(Uri original){String fragment=original.getFragment();if(fragment==null)return;int q=fragment.indexOf('?');if(q<0)return;String id=Uri.parse("https://local/?"+fragment.substring(q+1)).getQueryParameter("id");if(id==null||!id.matches("[a-fA-F0-9]{32}"))return;String uuid=id.replaceFirst("([a-fA-F0-9]{8})([a-fA-F0-9]{4})([a-fA-F0-9]{4})([a-fA-F0-9]{4})([a-fA-F0-9]{12})","$1-$2-$3-$4-$5");Uri target=Uri.parse("jellyfin://item/"+uuid);Intent intent=new Intent(Intent.ACTION_VIEW,target).addCategory(Intent.CATEGORY_BROWSABLE);for(String pkg:(television()?new String[]{"dev.jdtech.jellyfin.dubs.tv"}:new String[]{"dev.jdtech.jellyfin.dubs","dev.jdtech.jellyfin"})){intent.setPackage(pkg);try{startActivity(intent);return;}catch(ActivityNotFoundException ignored){}}
  new AlertDialog.Builder(this).setTitle("Нужен клиент с deeplink").setMessage("Установите Findroid Dubs с поддержкой перехода к серии и подключите свой сервер Jellyfin. Официальный Jellyfin для Android пока не поддерживает такой переход.").setPositiveButton("ОК",null).setNeutralButton("Открыть в браузере",(d,w)->openBrowser(original)).show();
 }
 private void openBrowser(Uri url){try{startActivity(new Intent(Intent.ACTION_VIEW,url));}catch(ActivityNotFoundException missing){new AlertDialog.Builder(this).setTitle("Браузер не установлен").setMessage("Выберите Findroid TV в настройках Dubs для просмотра серии.").setPositiveButton("ОК",null).show();}}
 private int heldArrow=-1;private boolean groupHoldHandled=false;
 private String arrowDirection(int key){return key==KeyEvent.KEYCODE_DPAD_LEFT?"left":key==KeyEvent.KEYCODE_DPAD_RIGHT?"right":key==KeyEvent.KEYCODE_DPAD_UP?"up":"down";}
 private boolean keyboardVisible(){return Build.VERSION.SDK_INT>=30&&root.getRootWindowInsets()!=null&&root.getRootWindowInsets().isVisible(WindowInsets.Type.ime());}
 private void navigateTV(int key,boolean hold){web.evaluateJavascript("window.DubsTVNavigate&&window.DubsTVNavigate('"+arrowDirection(key)+"',"+(hold?"true":"false")+")",null);}
 @Override public boolean dispatchKeyEvent(KeyEvent event){
  if(serverDialog!=null&&serverDialog.isShowing())return super.dispatchKeyEvent(event);
  if(notificationsDialog!=null&&notificationsDialog.isShowing())return super.dispatchKeyEvent(event);
  if(television()&&event.getAction()==KeyEvent.ACTION_UP&&event.getKeyCode()==heldArrow){if(!groupHoldHandled)navigateTV(heldArrow,event.getEventTime()-event.getDownTime()>=500);heldArrow=-1;groupHoldHandled=false;return true;}
  if(event.getKeyCode()==KeyEvent.KEYCODE_MENU&&event.getAction()==KeyEvent.ACTION_DOWN&&(event.isLongPress()||!television()||home==null||web.getUrl()==null||web.getUrl().contains("/login"))){serverSettings();return true;}
  if(television()&&event.getKeyCode()==KeyEvent.KEYCODE_MENU&&event.getAction()==KeyEvent.ACTION_DOWN){((android.view.inputmethod.InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(web.getWindowToken(),0);web.evaluateJavascript("window.DubsTVFocusNav&&window.DubsTVFocusNav()",null);return true;}
  if(television()&&!keyboardVisible()){
   int key=event.getKeyCode();boolean arrow=key==KeyEvent.KEYCODE_DPAD_LEFT||key==KeyEvent.KEYCODE_DPAD_RIGHT||key==KeyEvent.KEYCODE_DPAD_UP||key==KeyEvent.KEYCODE_DPAD_DOWN;
   if(arrow&&event.getAction()==KeyEvent.ACTION_DOWN){if(heldArrow!=key){heldArrow=key;groupHoldHandled=false;}if(!groupHoldHandled&&(event.isLongPress()||(event.getEventTime()-event.getDownTime()>=500&&event.getRepeatCount()>0))){groupHoldHandled=true;navigateTV(key,true);}return true;}
   if(key==KeyEvent.KEYCODE_MENU&&event.getAction()==KeyEvent.ACTION_DOWN){web.evaluateJavascript("window.DubsTVFocusNav&&window.DubsTVFocusNav()",null);return true;}
  }
  return super.dispatchKeyEvent(event);
 }
 private void defaultBack(){if(notificationsDialog!=null&&notificationsDialog.isShowing()){notificationsDialog.dismiss();return;}if(web.canGoBack())web.goBack();else serverSettings();}
 private void handleBack(){if(television())web.evaluateJavascript("window.DubsTVBack?window.DubsTVBack():false",result->{if(!"true".equals(result))defaultBack();});else defaultBack();}
 @Override public void onBackPressed(){handleBack();}
 @Override protected void onPause(){super.onPause();CookieManager.getInstance().flush();}
 @Override protected void onDestroy(){web.destroy();super.onDestroy();}
}
