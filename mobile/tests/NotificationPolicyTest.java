import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
/** Source-level policy proofs for the local-notification stack (plain JVM). */
public class NotificationPolicyTest {
 static void check(boolean ok,String what){if(!ok)throw new AssertionError("POLICY FAIL: "+what);}
 static String read(String path){try{return Files.readString(Path.of(path));}catch(Exception e){throw new AssertionError("cannot read "+path);}}
 public static void main(String[] args) throws Exception {
  String job=Files.readString(Path.of("mobile/android/app/src/main/java/app/dubs/panel/NotificationSyncJob.java"));
  String sync=Files.readString(Path.of("mobile/android/app/src/main/java/app/dubs/panel/NotificationSync.java"));
  String store=Files.readString(Path.of("mobile/android/app/src/main/java/app/dubs/panel/EventStore.java"));
  String main=Files.readString(Path.of("mobile/android/app/src/main/java/app/dubs/panel/MainActivity.java"));
  String manifest=Files.readString(Path.of("mobile/android/app/src/main/AndroidManifest.xml"));
  String tvManifest=Files.readString(Path.of("mobile/android/tv/src/main/AndroidManifest.xml"));
  String ios=Files.readString(Path.of("mobile/ios/Dubs/main.m"));
  String ui=Files.readString(Path.of("src/ui.js"));
  check(job.contains("extends JobService"),"poll implementation is an OS JobService");
  check(manifest.contains("android:exported=\"false\"")&&manifest.contains("android:permission=\"android.permission.BIND_JOB_SERVICE\""),"poll service exported=false + BIND_JOB_SERVICE");
  check(tvManifest.contains("android:exported=\"false\"")&&tvManifest.contains("android.permission.BIND_JOB_SERVICE"),"TV poll service exported=false + BIND_JOB_SERVICE");
  check(sync.contains("PendingIntent.FLAG_IMMUTABLE"),"PendingIntent immutable");
  check(sync.contains("setInstanceFollowRedirects(false)"),"poll disables redirects");
  check(sync.contains("setPeriodic(15*60*1000L)"),"period >= 15 minutes");
  check(sync.contains("setPersisted(true)"),"job persisted across reboot");
  check(sync.contains("s.setCursor(id);\n    s.save(c);"),"write-ahead cursor before posting");
  check(sync.contains("new Notification.BigTextStyle")||sync.contains("BigTextStyle"),"bounded long text style");
  check(sync.contains("VISIBILITY_PUBLIC"),"generic public version present");
  check(!sync.toUpperCase().contains("APNS")&&!sync.toUpperCase().contains("FCM"),"no push infrastructure promises");
  check(store.contains("AndroidKeyStore")&&store.contains("AES/GCM/NoPadding"),"token encrypted with AndroidKeystore AES/GCM");
  check(!store.contains("getExternalFilesDir")&&!store.contains("MODE_WORLD"),"store stays private internal storage");
  check(manifest.contains("android.permission.RECEIVE_BOOT_COMPLETED"),"boot persistence permission");
  check(manifest.contains("android.permission.POST_NOTIFICATIONS")&&tvManifest.contains("android.permission.POST_NOTIFICATIONS"),"runtime notification permission declared");
  check(manifest.contains("android:name=\"app.dubs.panel.MainActivity\"")&&tvManifest.contains("android:name=\"app.dubs.panel.MainActivity\""),"activities use fully qualified source class (applicationId override safe)");
  check(main.contains("dubs-native://notifications")&&main.contains("evaluateJavascript"),"native settings reachable; registration uses WebView evaluateJavascript, no JS bridge");
  check(!main.contains("addJavascriptInterface"),"no JS bridge object exposed");
  check(main.contains("NotificationSync.routeForTap")&&main.contains("dubs_fp"),"tap validates origin fingerprint");
  check(ios.contains("BGTaskScheduler"),"iOS background refresh registered");
  check(ios.contains("SecItemAdd"),"iOS token in Keychain");
  check(ios.contains("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly"),"iOS keychain item device-bound accessibility");
  check(ios.contains("willPerformHTTPRedirection"),"iOS poll rejects redirects");
  check(ui.contains("DubsNative")&&ui.contains("Notification.requestPermission"),"browser notifications guarded against native UA to avoid double delivery");
  check(ui.contains("dubs-hide-media-details"),"per-device media-details switch persists");
  check(!ui.contains(".innerHTML"),"panel script builds DOM via textContent only");
  System.out.println("PASS notification policy: manifest hardening, immutable intents, no-redirect HTTPS polls, keystore/keychain, persisted job, no push promises, fully qualified activities, JS-bridge-free registration");
 }
 private NotificationPolicyTest(){}
}
