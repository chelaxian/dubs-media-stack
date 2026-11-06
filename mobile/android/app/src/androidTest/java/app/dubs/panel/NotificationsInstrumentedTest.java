package app.dubs.panel;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import android.app.job.JobScheduler;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import androidx.test.core.app.ApplicationProvider;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
/**
 * Real-device/emulator proofs (API 28 and 36 in CI): encrypted-at-rest token
 * store, deterministic notification ids, persisted periodic JobScheduler job,
 * manifest hardening of the poll service, and tap-route origin validation.
 * No test server, no QA endpoints: network code paths are exercised against
 * the contract shape only through pure functions here.
 */
@RunWith(AndroidJUnit4.class)
public class NotificationsInstrumentedTest {
 private final Context context=ApplicationProvider.getApplicationContext();
 @After public void cleanup(){NotificationSync.cancel(context);EventStore.reset(context);}
 @Test public void eventStoreSurvivesRestartAndNeverStoresPlaintextToken(){
  EventStore s=new EventStore();
  s.setRegistration("A-Za-z0-9_-43chars-exactly-test00000000000000000X","device-1","https://panel.example.org/","fp0123456789abcdef",4242,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  s.setEnabled(true);
  s.save(context);
  EventStore loaded=EventStore.load(context);
  assertEquals("device-1",loaded.deviceId());
  assertEquals("https://panel.example.org/",loaded.origin());
  assertEquals(4242L,loaded.cursor());
  assertEquals("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",loaded.instanceId());
  assertTrue(loaded.enabled());
  assertTrue(loaded.matchesOrigin("https://panel.example.org/","fp0123456789abcdef"));
  assertFalse(loaded.matchesOrigin("https://other.example.org/","fp0123456789abcdef"));
  byte[] raw=new byte[0];
  try{raw=Files.readAllBytes(new File(context.getFilesDir(),"dubs_events.bin").toPath());}catch(Exception io){throw new AssertionError(io);}
  String text=new String(raw,StandardCharsets.ISO_8859_1);
  assertFalse("token must not be readable in the store file",text.contains("A-Za-z0-9_-43chars"));
 }
 @Test public void notificationIdsAreDeterministicAndOriginScoped(){
  int a=NotificationSync.notificationId("https://panel.example.org/","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",123);
  assertEquals(a,NotificationSync.notificationId("https://panel.example.org/","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",123));
  assertFalse(a==NotificationSync.notificationId("https://panel.example.org/","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",124));
  assertFalse(a==NotificationSync.notificationId("https://other.example.org/","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",123));
 }
 @Test public void fingerprintIsStableAndNotReversibleOrigin(){
  String fp=NotificationSync.fingerprint("https://panel.example.org/");
  assertEquals(fp,NotificationSync.fingerprint("https://panel.example.org/"));
  assertEquals(16,fp.length());
  assertFalse(fp.contains("example"));
 }
 @Test public void tapRouteValidatesOriginFingerprint(){
  String fp=NotificationSync.fingerprint("https://panel.example.org/");
  assertEquals("#released?episode=31",NotificationSync.routeForTap(fp,fp,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",31,null));
  assertEquals("#settings?section=events",NotificationSync.routeForTap(fp,fp,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",0,"events"));
  assertNull(NotificationSync.routeForTap(fp,"stale","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",31,null));
  assertNull(NotificationSync.routeForTap(null,fp,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",31,null));
  assertNull(NotificationSync.routeForTap(fp,fp,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",0,"unknown"));
  assertNull(NotificationSync.routeForTap(fp,fp,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",0,null));
 }
 @Test public void staleEpochAndLongTapAreValidated(){
  String epoch="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  assertEquals("#released?episode=9999999999",NotificationSync.routeForTap("fp","fp",epoch,epoch,9999999999L,null));
  assertNull(NotificationSync.routeForTap("fp","fp",epoch,epoch,10000000000L,null));
  assertNull(NotificationSync.routeForTap("fp","fp",epoch,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",31,null));
 }
 @Test public void periodicJobIsPersistedAndAtLeastQuarterHour() throws Exception{
  NotificationSync.schedule(context);
  JobScheduler js=context.getSystemService(JobScheduler.class);
  assertNotNull(js.getPendingJob(NotificationSync.JOB_ID));
  assertTrue(js.getPendingJob(NotificationSync.JOB_ID).getIntervalMillis()>=15*60*1000L);
  assertTrue(js.getPendingJob(NotificationSync.JOB_ID).isPersisted());
 }
 @Test public void pollServiceIsNotExportedAndJobBound() throws Exception{
  PackageInfo info=context.getPackageManager().getPackageInfo(context.getPackageName(),PackageManager.GET_SERVICES|PackageManager.GET_PERMISSIONS);
  boolean found=false;
  for(ServiceInfo service:info.services){
   if(service.name.equals("app.dubs.panel.NotificationSyncJob")){
    found=true;
    assertFalse("service must not be exported",service.exported);
    assertEquals("android.permission.BIND_JOB_SERVICE",service.permission);
   }
  }
  assertTrue("poll service must be declared",found);
  boolean asksNotifications=false;
  for(String permission:info.requestedPermissions){
   if("android.permission.POST_NOTIFICATIONS".equals(permission))asksNotifications=true;
  }
  assertTrue("POST_NOTIFICATIONS must be declared",asksNotifications);
 }
 @Test public void disabledStoreStopsPollingWithoutNetwork(){
  EventStore s=new EventStore();
  s.setRegistration("A-Za-z0-9_-43chars-exactly-test00000000000000000X","device-1","https://panel.example.org/","fp",1,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  s.save(context);
  assertFalse("pollOnce must be a no-op while disabled (no network at all)",NotificationSync.pollOnce(context));
 }

 private void awaitNotifications(android.app.NotificationManager manager,int expected) throws Exception {
  long deadline=android.os.SystemClock.elapsedRealtime()+3000;
  while(manager.getActiveNotifications().length!=expected&&android.os.SystemClock.elapsedRealtime()<deadline)Thread.sleep(25);
  assertEquals(expected,manager.getActiveNotifications().length);
 }
 private EventStore registration(long cursor){
  EventStore state=new EventStore();String origin="https://panel.example.org/";
  state.setRegistration("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","fixture",origin,NotificationSync.fingerprint(origin),cursor,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  state.setEnabled(true);state.save(context);return state;
 }
 private org.json.JSONObject page(long id,String epoch) throws Exception {
  return new org.json.JSONObject().put("instanceId",epoch).put("cursor",id)
   .put("settings",new org.json.JSONObject().put("enabled",true).put("showTitles",false).put("types",new org.json.JSONArray().put("new_episode")))
   .put("events",new org.json.JSONArray().put(new org.json.JSONObject().put("id",id).put("type","new_episode").put("title","Private title").put("message","Private message").put("episodeId",9999999999L)));
 }
 private final StringBuilder permissionShellTrace=new StringBuilder();
 private String shell(String command) throws Exception {
  // UiAutomation returns a pipe, not a process exit code. Run a shell wrapper
  // that merges stderr and appends the actual command status to that pipe.
  String marker="DUBS_SHELL_EXIT=";
  // Send the script through stdin: executeShellCommand does not promise shell
  // parsing of quotes/redirections in its command argument. This helper is
  // called only on API 33+, where executeShellCommandRw (API 31) is public.
  android.os.ParcelFileDescriptor[] pipes=androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().getUiAutomation().executeShellCommandRw("sh");
  String script="exec 2>&1\n"+command+"\nstatus=$?\nprintf '\\n"+marker+"%s\\n' \"$status\"\n";
  String text;
  try(java.io.InputStream output=new android.os.ParcelFileDescriptor.AutoCloseInputStream(pipes[0]);
      java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream()){
   try(java.io.OutputStream input=new android.os.ParcelFileDescriptor.AutoCloseOutputStream(pipes[1])){
    input.write(script.getBytes(StandardCharsets.UTF_8));
   }
   byte[] buffer=new byte[4096];int n;
   while((n=output.read(buffer))!=-1)bytes.write(buffer,0,n);
   text=new String(bytes.toByteArray(),StandardCharsets.UTF_8);
  }
  permissionShellTrace.append(command).append(" => ").append(text).append('\n');
  int end=text.lastIndexOf(marker);
  assertTrue("missing shell exit status: "+permissionShellTrace,end>=0);
  assertEquals("shell command failed: "+permissionShellTrace,"0",text.substring(end+marker.length()).trim());
  return text.substring(0,end).trim();
 }
 private int notificationOpMode(boolean raw){
  android.app.AppOpsManager ops=context.getSystemService(android.app.AppOpsManager.class);
  // Observe the operation set by the shell independently of permissionToOp:
  // a missing permission mapping must remain visible, not prevent fixture proof.
  String op="android:post_notification";
  if(ops==null)return -1;
  return raw&&android.os.Build.VERSION.SDK_INT>=29
   ?ops.unsafeCheckOpRawNoThrow(op,android.os.Process.myUid(),context.getPackageName())
   :ops.checkOpNoThrow(op,android.os.Process.myUid(),context.getPackageName());
 }
 private String permissionDiagnostics() throws Exception {
  android.app.NotificationManager manager=context.getSystemService(android.app.NotificationManager.class);
  android.app.NotificationChannel channel=manager.getNotificationChannel(NotificationSync.CHANNEL_ID);
  return "sdk="+android.os.Build.VERSION.SDK_INT+" package="+context.getPackageName()
   +" ownUid="+android.os.Process.myUid()+" contextUid="+context.getApplicationInfo().uid
   +" packageUid="+context.getPackageManager().getApplicationInfo(context.getPackageName(),0).uid
   +" permission="+context.checkSelfPermission("android.permission.POST_NOTIFICATIONS")
   +" permissionToOp="+android.app.AppOpsManager.permissionToOp("android.permission.POST_NOTIFICATIONS")
   +" normalMode="+notificationOpMode(false)+" rawMode="+notificationOpMode(true)
   +" NMS.areNotificationsEnabled="+manager.areNotificationsEnabled()
   +" channelImportance="+(channel==null?"absent":channel.getImportance())
   +" activeNotifications="+manager.getActiveNotifications().length
   +" productAllowed="+NotificationSync.notificationsAllowed(context)
   +" shellPackageOp="+shell("cmd appops get "+context.getPackageName()+" POST_NOTIFICATION")
   +" shellUidOp="+shell("cmd appops get --uid "+context.getPackageName()+" POST_NOTIFICATION")
   +" shellTrace="+permissionShellTrace;
 }
 private void permission(boolean allowed) throws Exception {
  if(android.os.Build.VERSION.SDK_INT>=33){
   assertEquals("pm grant emitted unexpected output: "+permissionShellTrace,"",shell("pm grant "+context.getPackageName()+" android.permission.POST_NOTIFICATIONS"));
   // AppOps is the OS app-level notification switch; unlike revoking a
   // runtime permission from the test process it does not kill the runner.
   assertEquals("appops set emitted unexpected output: "+permissionShellTrace,"",shell("cmd appops set "+context.getPackageName()+" POST_NOTIFICATION "+(allowed?"allow":"ignore")));
   int expected=allowed?android.app.AppOpsManager.MODE_ALLOWED:android.app.AppOpsManager.MODE_IGNORED;
   long started=android.os.SystemClock.elapsedRealtime();
   permissionShellTrace.append("rawModeImmediatelyAfterSet=").append(notificationOpMode(true)).append('\n');
   long deadline=started+5000;
   while(notificationOpMode(true)!=expected&&android.os.SystemClock.elapsedRealtime()<deadline)Thread.sleep(25);
   permissionShellTrace.append("appOpsWaitMs=").append(android.os.SystemClock.elapsedRealtime()-started).append('\n');
   assertEquals("OS AppOps fixture did not converge: "+permissionDiagnostics(),expected,notificationOpMode(true));
  }
 }
 @Test public void realDeliveryDedupePrivacyDisableAndRealmReset() throws Exception {
  permission(true);
  android.app.NotificationManager manager=context.getSystemService(android.app.NotificationManager.class);
  manager.createNotificationChannel(new android.app.NotificationChannel(NotificationSync.CHANNEL_ID,"Events",android.app.NotificationManager.IMPORTANCE_DEFAULT));
  EventStore baseline=registration(1);
  org.json.JSONObject response=page(2,baseline.instanceId());
  NotificationSync.consume(context,baseline,response);
  assertEquals(2,EventStore.load(context).cursor());
  awaitNotifications(manager,1);
  android.app.Notification notification=manager.getActiveNotifications()[0].getNotification();
  assertEquals("Dubs",notification.extras.getString(android.app.Notification.EXTRA_TITLE));
  assertFalse(notification.extras.toString().contains("Private title"));
  assertFalse(notification.extras.toString().contains("https://"));
  manager.cancelAll();awaitNotifications(manager,0);
  NotificationSync.consume(context,baseline,response);
  assertEquals("replayed page must not repost",0,manager.getActiveNotifications().length);
  EventStore.reset(context);
  NotificationSync.consume(context,baseline,page(3,baseline.instanceId()));
  assertFalse("disable during an in-flight poll cannot resurrect state",EventStore.load(context).enabled());
  assertEquals(0,manager.getActiveNotifications().length);
  baseline=registration(1);
  NotificationSync.consume(context,baseline,page(8,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
  assertEquals(8,EventStore.load(context).cursor());
  assertEquals("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",EventStore.load(context).instanceId());
  assertEquals("epoch change silently baselines",0,manager.getActiveNotifications().length);
 }
 @Test public void systemDeniedIsNotReportedActiveAndCursorStillAdvances() throws Exception {
  if(android.os.Build.VERSION.SDK_INT<33)return;
  Throwable denialFailure=null;
  try{
   permission(false);
   EventStore baseline=registration(1);
   long started=android.os.SystemClock.elapsedRealtime();
   permissionShellTrace.append("productAllowedImmediatelyAfterFixture=").append(NotificationSync.notificationsAllowed(context)).append('\n');
   long deadline=started+5000;
   while(NotificationSync.notificationsAllowed(context)&&android.os.SystemClock.elapsedRealtime()<deadline)Thread.sleep(25);
   permissionShellTrace.append("productGateWaitMs=").append(android.os.SystemClock.elapsedRealtime()-started).append('\n');
   String diagnostics=permissionDiagnostics();
   android.util.Log.i("DubsDenialFixture",diagnostics);
   assertFalse("product reports active after OS denial: "+diagnostics,NotificationSync.notificationsAllowed(context));
   NotificationSync.consume(context,baseline,page(2,baseline.instanceId()));
   assertEquals("denied delivery must advance cursor: "+diagnostics,2,EventStore.load(context).cursor());
   assertEquals("denied delivery must not post: "+permissionDiagnostics(),0,context.getSystemService(android.app.NotificationManager.class).getActiveNotifications().length);
  }catch(Exception|AssertionError failure){
   denialFailure=failure;
   throw failure;
  }finally{
   try{permission(true);}catch(Exception|AssertionError restoreFailure){
    if(denialFailure!=null)denialFailure.addSuppressed(restoreFailure);else throw restoreFailure;
   }
  }
 }

 @Test public void realWebViewRegistrationMailboxAwaitsAndClears() throws Exception {
  java.util.concurrent.CountDownLatch done=new java.util.concurrent.CountDownLatch(1);
  java.util.concurrent.atomic.AtomicReference<Throwable> error=new java.util.concurrent.atomic.AtomicReference<>();
  android.os.Handler polling=new android.os.Handler(android.os.Looper.getMainLooper());
  java.util.concurrent.atomic.AtomicBoolean stopped=new java.util.concurrent.atomic.AtomicBoolean();
  android.webkit.WebView[] ownedWeb=new android.webkit.WebView[1];
  try{
   androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().runOnMainSync(()->{
    android.webkit.WebView web=new android.webkit.WebView(context);
    ownedWeb[0]=web;
    web.getSettings().setJavaScriptEnabled(true);
    web.setWebViewClient(new android.webkit.WebViewClient(){
     @Override public void onPageFinished(android.webkit.WebView view,String url){
      if(stopped.get())return;
      view.evaluateJavascript(NotificationSync.registrationMailboxScript("android","fixture"),started->{
       if(stopped.get())return;
       try{assertEquals("mailbox-started",new org.json.JSONTokener(started).nextValue());}
       catch(Throwable failure){error.set(failure);done.countDown();return;}
       readMailbox(view,0,done,error,polling,stopped);
      });
     }
    });
    web.loadDataWithBaseURL("https://panel.example.org/","<script>window.fetch=(url)=>Promise.resolve({ok:true,json:()=>new Promise(resolve=>setTimeout(()=>resolve(url==='/api/session'?{csrf:'fixture'}:{token:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',deviceId:'fixture',cursor:9,instanceId:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}),20))});</script>","text/html","UTF-8",null);
   });
   assertTrue("bounded async mailbox callback",done.await(15,java.util.concurrent.TimeUnit.SECONDS));
   if(error.get()!=null)throw new AssertionError(error.get());
  }finally{
   androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().runOnMainSync(()->{
    stopped.set(true);
    polling.removeCallbacksAndMessages(null);
    if(ownedWeb[0]!=null){
     ownedWeb[0].stopLoading();
     ownedWeb[0].setWebViewClient(null);
     ownedWeb[0].destroy();
     ownedWeb[0]=null;
    }
   });
  }
 }
 private void readMailbox(android.webkit.WebView web,int attempt,java.util.concurrent.CountDownLatch done,java.util.concurrent.atomic.AtomicReference<Throwable> error,android.os.Handler polling,java.util.concurrent.atomic.AtomicBoolean stopped){
  if(stopped.get())return;
  web.evaluateJavascript(NotificationSync.registrationPollScript("fixture"),result->{
   if(stopped.get())return;
   try{
    String raw=(String)new org.json.JSONTokener(result).nextValue();
    // This WebView is detached: View.postDelayed waits for attachment.
    if(raw.isEmpty()&&attempt<24){polling.postDelayed(()->readMailbox(web,attempt+1,done,error,polling,stopped),100);return;}
    org.json.JSONObject registration=new org.json.JSONObject(raw);
    assertEquals("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",registration.getString("instanceId"));
    assertEquals(9,registration.getLong("cursor"));assertEquals(43,registration.getString("token").length());
    web.evaluateJavascript(NotificationSync.registrationClearScript("fixture"),ignored->{
     if(stopped.get())return;
     web.evaluateJavascript(NotificationSync.registrationPollScript("fixture"),cleared->{
      if(stopped.get())return;
      try{assertEquals("",new org.json.JSONTokener(cleared).nextValue());}catch(Throwable failure){error.set(failure);}finally{done.countDown();}
     });
    });
   }catch(Throwable failure){error.set(failure);done.countDown();}
  });
 }
}
