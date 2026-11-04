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
 private void shell(String command) throws Exception {
  android.os.ParcelFileDescriptor result=androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().getUiAutomation().executeShellCommand(command);
  try(java.io.FileInputStream output=new java.io.FileInputStream(result.getFileDescriptor())){while(output.read()!=-1){}}
  result.close();
 }
 private void permission(boolean allowed) throws Exception {
  if(android.os.Build.VERSION.SDK_INT>=33){
   shell("pm grant "+context.getPackageName()+" android.permission.POST_NOTIFICATIONS");
   // AppOps is the OS app-level notification switch; unlike revoking a
   // runtime permission from the test process it does not kill the runner.
   shell("cmd appops set "+context.getPackageName()+" POST_NOTIFICATION "+(allowed?"allow":"ignore"));
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
  permission(false);
  EventStore baseline=registration(1);
  assertFalse(NotificationSync.notificationsAllowed(context));
  NotificationSync.consume(context,baseline,page(2,baseline.instanceId()));
  assertEquals(2,EventStore.load(context).cursor());
  assertEquals(0,context.getSystemService(android.app.NotificationManager.class).getActiveNotifications().length);
  permission(true);
 }

 @Test public void realWebViewRegistrationMailboxAwaitsAndClears() throws Exception {
  java.util.concurrent.CountDownLatch done=new java.util.concurrent.CountDownLatch(1);
  java.util.concurrent.atomic.AtomicReference<Throwable> error=new java.util.concurrent.atomic.AtomicReference<>();
  androidx.test.platform.app.InstrumentationRegistry.getInstrumentation().runOnMainSync(()->{
   android.webkit.WebView web=new android.webkit.WebView(context);
   web.getSettings().setJavaScriptEnabled(true);
   web.setWebViewClient(new android.webkit.WebViewClient(){
    @Override public void onPageFinished(android.webkit.WebView view,String url){
     view.evaluateJavascript(NotificationSync.registrationMailboxScript("android","fixture"),started->{
      try{assertEquals("mailbox-started",new org.json.JSONTokener(started).nextValue());}
      catch(Throwable failure){error.set(failure);done.countDown();view.destroy();return;}
      readMailbox(view,0,done,error);
     });
    }
   });
   web.loadDataWithBaseURL("https://panel.example.org/","<script>window.fetch=(url)=>Promise.resolve({ok:true,json:()=>new Promise(resolve=>setTimeout(()=>resolve(url==='/api/session'?{csrf:'fixture'}:{token:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',deviceId:'fixture',cursor:9,instanceId:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}),20))});</script>","text/html","UTF-8",null);
  });
  assertTrue("bounded async mailbox callback",done.await(15,java.util.concurrent.TimeUnit.SECONDS));
  if(error.get()!=null)throw new AssertionError(error.get());
 }
 private void readMailbox(android.webkit.WebView web,int attempt,java.util.concurrent.CountDownLatch done,java.util.concurrent.atomic.AtomicReference<Throwable> error){
  web.evaluateJavascript(NotificationSync.registrationPollScript("fixture"),result->{
   try{
    String raw=(String)new org.json.JSONTokener(result).nextValue();
    if(raw.isEmpty()&&attempt<24){web.postDelayed(()->readMailbox(web,attempt+1,done,error),100);return;}
    org.json.JSONObject registration=new org.json.JSONObject(raw);
    assertEquals("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",registration.getString("instanceId"));
    assertEquals(9,registration.getLong("cursor"));assertEquals(43,registration.getString("token").length());
    web.evaluateJavascript(NotificationSync.registrationClearScript("fixture"),ignored->web.evaluateJavascript(NotificationSync.registrationPollScript("fixture"),cleared->{
     try{assertEquals("",new org.json.JSONTokener(cleared).nextValue());}catch(Throwable failure){error.set(failure);}finally{web.destroy();done.countDown();}
    }));
   }catch(Throwable failure){error.set(failure);web.destroy();done.countDown();}
  });
 }
}
