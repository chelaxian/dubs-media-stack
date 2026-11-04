package app.dubs.panel;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;
/**
 * Local notification machinery. Polls /api/notifications/events with the
 * narrow device token over the SAME registered HTTPS origin (redirects
 * disabled, bounded body, strict shapes), persists the durable cursor BEFORE
 * posting (at-most-once, no replay after restart or re-enable), and posts OS
 * local notifications with deterministic IDs. Every persisted change is
 * guarded by a compare-and-set against the state captured at poll start, so
 * a concurrent disable / origin change / re-registration can never be
 * resurrected by an in-flight poll. Privacy default: lock-screen shows
 * generic labels unless the account explicitly enables titles. No URLs,
 * tokens or credentials are ever placed into notifications, intents or logs.
 * There is no push: delivery is periodic (JobScheduler >=15min, persisted)
 * plus a foreground poll; Android 401/403 marks the device as needing
 * re-registration instead of polling forever against a revoked token.
 */
public final class NotificationSync {
 public static final int JOB_ID=4711;
 public static final String CHANNEL_ID="events";
 /** Locked backend contract: journal instanceId is an opaque 32-hex string. */
 public static final String INSTANCE_PATTERN="[a-fA-F0-9]{32}";
 /** Largest poll body accepted (JSON with <=100 events stays far below this). */
 public static final int MAX_BODY_BYTES=512*1024;
 /** Route epoch bound: episode ids beyond this are treated as invalid taps. */
 public static final long MAX_EPISODE=9999999999L;
 private static final int MAX_EVENTS=100;
 private static final Map<String,String> GENERIC=new HashMap<>();
 static{
  GENERIC.put("download_started","Начало загрузки");
  GENERIC.put("torrent_complete","Раздача скачана");
  GENERIC.put("library_ready","Появилось в медиатеке");
  GENERIC.put("new_episode","Новая серия");
  GENERIC.put("upgrade_started","Ежедневная проверка замены");
  GENERIC.put("upgrade_result","Результат замены");
 }
 /** Non-secret short fingerprint of the normalized HTTPS origin. */
 public static String fingerprint(String origin){
  try{
   byte[] d=MessageDigest.getInstance("SHA-256").digest(origin.getBytes(StandardCharsets.UTF_8));
   StringBuilder sb=new StringBuilder();
   for(int i=0;i<8;i++)sb.append(String.format("%02x",d[i]));
   return sb.toString();
  }catch(Exception failClosed){throw new IllegalStateException("digest unavailable",failClosed);}
 }
 /** Deterministic OS notification id: stable across restarts for the same event, never reused across origins/instances. */
 public static int notificationId(String origin,String instanceId,long eventId){return (origin+"#"+instanceId+"#"+eventId).hashCode();}
 /**
  * Registration fetch executed INSIDE the WebView page context (no JS
  * bridge). WebView.evaluateJavascript does NOT await Promises, so the
  * async IIFE must NOT be consumed as its result: it only starts the fetch
  * and stores the JSON outcome in a one-time window slot. Native then reads
  * that slot with bounded, synchronous evaluateJavascript polls
  * ({@link #registrationPollScript}) and clears it afterwards
  * ({@link #registrationClearScript}). The token exists only in the page
  * JS heap, the evaluation callback and the encrypted store — never in a
  * URL, custom scheme, log or intent.
  */
 public static String mailboxSlot(String nonce){return "DubsEventsResult"+nonce.replaceAll("[^A-Za-z0-9]","");}
 public static String registrationMailboxScript(String platform,String nonce){
  String slot=mailboxSlot(nonce);
  return "window['"+slot+"']='';(async()=>{try{const s=await fetch('/api/session',{credentials:'same-origin'});if(!s.ok)throw 0;const sess=await s.json();const body=new URLSearchParams({csrf:sess.csrf,platform:'"+platform+"'});const r=await fetch('/api/notifications/devices',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});const answer=JSON.stringify(r.ok?await r.json():{error:'register '+r.status});if(Object.prototype.hasOwnProperty.call(window,'"+slot+"'))window['"+slot+"']=answer}catch(e){if(Object.prototype.hasOwnProperty.call(window,'"+slot+"'))window['"+slot+"']=JSON.stringify({error:'network'})}})();'mailbox-started'";
 }
 public static String registrationPollScript(String nonce){
  return "(window['"+mailboxSlot(nonce)+"']||'')";
 }
 public static String registrationClearScript(String nonce){
  return "(delete window['"+mailboxSlot(nonce)+"'],'mailbox-cleared')";
 }
 /** Best-effort revoke: the page-side fetch runs regardless of Promise result; nothing is consumed. */
 public static String revokeScript(String deviceId){
  String safe=deviceId.replaceAll("[^A-Za-z0-9_.-]","");
  return "(async()=>{try{const s=await fetch('/api/session',{credentials:'same-origin'});if(!s.ok)return;const sess=await s.json();const body=new URLSearchParams({csrf:sess.csrf,deviceId:'"+safe+"'});fetch('/api/notifications/revoke',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body})}catch(e){}})();'revoke-started'";
 }
 public static void schedule(Context c){
  JobScheduler js=c.getSystemService(JobScheduler.class);
  if(js==null)return;
  js.schedule(new JobInfo.Builder(JOB_ID,new ComponentName(c,NotificationSyncJob.class)).setPeriodic(15*60*1000L).setPersisted(true).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).build());
 }
 public static void cancel(Context c){
  JobScheduler js=c.getSystemService(JobScheduler.class);
  if(js!=null)js.cancel(JOB_ID);
  NotificationManager nm=c.getSystemService(NotificationManager.class);
  if(nm!=null)nm.cancelAll();
 }
 /** Honest OS-level check: app-level block and channel block both count as denied (not just the API 33 runtime permission). */
 public static boolean notificationsAllowed(Context c){
  NotificationManager nm=c.getSystemService(NotificationManager.class);
  if(nm==null||!nm.areNotificationsEnabled())return false;
  if(Build.VERSION.SDK_INT>=26){
   NotificationChannel ch=nm.getNotificationChannel(CHANNEL_ID);
   if(ch!=null&&ch.getImportance()==NotificationManager.IMPORTANCE_NONE)return false;
  }
  return true;
 }
 private static byte[] readBounded(InputStream in) throws Exception{
  try(InputStream source=in;ByteArrayOutputStream buf=new ByteArrayOutputStream()){
   byte[] chunk=new byte[8192];int n;
   while((n=source.read(chunk))>0){buf.write(chunk,0,n);if(buf.size()>MAX_BODY_BYTES)return null;}
   return buf.toByteArray();
  }
 }
 private static boolean pollLocked(Context c){
  if(Thread.currentThread().isInterrupted())return false;
  EventStore baseline=EventStore.load(c);
  if(!baseline.enabled()||baseline.needsReconnect()||baseline.token().isEmpty()||baseline.origin().isEmpty()||!baseline.instanceId().matches(INSTANCE_PATTERN))return false;
  if(!baseline.origin().equals(ServerAddress.normalize(baseline.origin())))return false;
  HttpURLConnection conn=null;
  try{
   conn=(HttpURLConnection)new URL(baseline.origin()+"api/notifications/events?after="+baseline.cursor()+"&limit=50").openConnection();
   conn.setInstanceFollowRedirects(false);
   conn.setConnectTimeout(15000);
   conn.setReadTimeout(30000);
   conn.setRequestProperty("Authorization","Bearer "+baseline.token());
   conn.setRequestProperty("Accept","application/json");
   int code=conn.getResponseCode();
   if(code==401||code==403){
    // Token revoked/rejected: stop the poll loop until the user re-registers.
    synchronized(EventStore.LOCK){EventStore s=EventStore.load(c);
    if(s.sameRegistrationAs(baseline)){s.setNeedsReconnect(true);s.save(c);cancel(c);}}
    return false;
   }
   if(code<200||code>201)return false;
   byte[] raw=readBounded(conn.getInputStream());
   if(raw==null)return false;
   JSONObject d=new JSONObject(new String(raw,StandardCharsets.UTF_8));
   return consume(c,baseline,d);
  }catch(Exception failure){
   Log.w("DubsEvents",failure.getClass().getSimpleName());
   return false;
  }finally{
   if(conn!=null)conn.disconnect();
  }
 }
 private static boolean integer(Object value){return value instanceof Long||value instanceof Integer;}
 /** Apply one validated feed page; package-private for device tests of the real delivery path. */
 static boolean consume(Context c,EventStore baseline,JSONObject d) throws Exception {
   if(Thread.currentThread().isInterrupted())return false;
   String instance=d.optString("instanceId","");
   if(!(d.opt("instanceId") instanceof String)||!instance.matches(INSTANCE_PATTERN)||!integer(d.opt("cursor"))||d.optLong("cursor")<0)return false;
   if(!instance.equals(baseline.instanceId())){
    synchronized(EventStore.LOCK){EventStore s=EventStore.load(c);
    if(s.sameRegistrationAs(baseline)){
     // Server state was reset: re-baseline silently, never replay history.
     s.setInstanceId(instance);
     s.setCursor(d.optLong("cursor",s.cursor()));
     s.save(c);cancel(c);schedule(c);
    }}
    return false;
   }
   JSONObject settings=d.optJSONObject("settings");
   boolean accountOn=settings!=null&&settings.optBoolean("enabled");
   boolean showTitles=settings!=null&&settings.optBoolean("showTitles");
   Set<String> types=new HashSet<>();
   if(settings!=null){
    JSONArray list=settings.optJSONArray("types");
    if(list!=null)for(int i=0;i<list.length();i++)types.add(list.optString(i));
   }
   JSONArray events=d.optJSONArray("events");
   if(events==null||events.length()>MAX_EVENTS)return false;
   long previous=baseline.cursor();
   long declaredTail=d.optLong("cursor",-1);
   for(int i=0;i<events.length();i++){
    JSONObject ev=events.optJSONObject(i);
    if(ev==null||!integer(ev.opt("id"))||ev.optLong("id")<=previous||!GENERIC.containsKey(ev.optString("type")))return false;
    previous=ev.optLong("id");
   }
   if(declaredTail<previous)return false;
   for(int i=0;i<events.length();i++){
    JSONObject ev=events.optJSONObject(i);
    long id=ev==null?0:ev.optLong("id",0);
    String type=ev==null?"":ev.optString("type","");
    if(ev==null||id<=0||!GENERIC.containsKey(type))return false; // strict shape: never persist on ambiguity
    synchronized(EventStore.LOCK){
    EventStore s=EventStore.load(c);
    if(Thread.currentThread().isInterrupted()||!s.sameRegistrationAs(baseline))return false; // disabled / origin changed / re-registered mid-poll
    if(id<=s.cursor())continue;
    // Write-ahead: persist the cursor BEFORE posting, so restarts never duplicate.
    s.setCursor(id);
    s.save(c);
    if(accountOn&&types.contains(type))post(c,s,ev,showTitles);
    }
   }
   long tail=d.optLong("cursor",0);
   synchronized(EventStore.LOCK){EventStore s=EventStore.load(c);
   if(s.sameRegistrationAs(baseline)&&tail>s.cursor()){s.setCursor(tail);s.save(c);}}
   return false;
 }
 /** Serialized: one poll at a time (foreground trigger and JobScheduler never race). */
 public static boolean pollOnce(Context c){
  synchronized(NotificationSync.class){return pollLocked(c);}
 }
 // notificationsAllowed checks app/channel permission before any notify call.
 @android.annotation.SuppressLint("MissingPermission")
 private static void post(Context c,EventStore s,JSONObject ev,boolean showTitles){
  NotificationManager nm=c.getSystemService(NotificationManager.class);
  if(nm==null||!notificationsAllowed(c))return;
  NotificationChannel channel=new NotificationChannel(CHANNEL_ID,"События медиатеки",NotificationManager.IMPORTANCE_DEFAULT);
  nm.createNotificationChannel(channel);
  String type=ev.optString("type");
  String generic=GENERIC.containsKey(type)?GENERIC.get(type):"Событие медиатеки";
  String serverTitle=ev.optString("title");
  String text=ev.optString("message");
  if(text.isEmpty())text=serverTitle;
  if(text.isEmpty())text=generic;
  String title=showTitles&&!serverTitle.isEmpty()?serverTitle:"Dubs";
  long episodeId=ev.optLong("episodeId",0);
  Intent intent=new Intent(c,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP).setAction("app.dubs.panel.EVENT_TAP").putExtra("dubs_fp",s.fingerprint()).putExtra("dubs_instance",s.instanceId());
  if(episodeId>=1L&&episodeId<=MAX_EPISODE)intent.putExtra("dubs_episode",episodeId);
  else intent.putExtra("dubs_section","events");
  int notificationId=notificationId(s.origin(),s.instanceId(),ev.optLong("id"));
  PendingIntent pi=PendingIntent.getActivity(c,notificationId,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
  Notification.Builder builder=new Notification.Builder(c,CHANNEL_ID)
   .setSmallIcon(R.drawable.ic_dubs)
   .setContentTitle(title)
   .setContentText(showTitles?text:generic)
   .setStyle(new Notification.BigTextStyle().bigText(showTitles?text:generic))
   .setWhen(ev.optLong("created")*1000L)
   .setAutoCancel(true)
   .setVisibility(Notification.VISIBILITY_PRIVATE)
   .setContentIntent(pi);
  if(!showTitles){
   Notification publicVersion=new Notification.Builder(c,CHANNEL_ID)
    .setSmallIcon(R.drawable.ic_dubs)
    .setContentTitle("Dubs")
    .setContentText(generic)
    .setAutoCancel(true)
    .setVisibility(Notification.VISIBILITY_PUBLIC)
    .build();
   builder.setPublicVersion(publicVersion);
  }
  nm.notify(notificationId,builder.build());
 }
 /**
  * Visible-for-testing tap routing used by instrumented tests: returns the
  * hash to open, or null. Both the origin fingerprint and the journal
  * instance must match the current registration, and episode ids are
  * validated as a bounded number (1..9999999999). Only these internal IDs
  * travel in the notification extras — never URLs or tokens.
  */
 public static String routeForTap(String fingerprintExpected,String fingerprintActual,String instanceExpected,String instanceActual,long episode,String section){
  if(fingerprintExpected==null||!fingerprintExpected.equals(fingerprintActual))return null;
  if(instanceExpected==null||!instanceExpected.matches(INSTANCE_PATTERN)||!instanceExpected.equals(instanceActual))return null;
  if(episode>=1L&&episode<=MAX_EPISODE)return "#released?episode="+episode;
  if("events".equals(section))return "#settings?section=events";
  return null;
 }
 private NotificationSync(){}
}
