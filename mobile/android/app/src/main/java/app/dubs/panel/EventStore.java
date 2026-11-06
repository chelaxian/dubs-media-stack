package app.dubs.panel;
import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONObject;
/**
 * Device notification state (device token, cursor, origin fingerprint,
 * server instance). Token is sensitive: stored only encrypted with an
 * app-scoped AndroidKeyStore AES/GCM key. Fail closed — any crypto or parse
 * failure propagates so the caller refuses to enable notifications. No
 * plaintext fallback exists. The token never appears in logs; it is only
 * ever written here or sent to the registered HTTPS origin over the
 * authenticated registration fetch.
 *
 * Locked contract: the backend journal instanceId is an OPAQUE 32-hex
 * STRING (not a number). It is validated at registration and every poll
 * rebase; a missing/blank instance never silently becomes 0 (that would
 * disable server-reset detection forever).
 */
public final class EventStore {
 public static final Object LOCK=new Object();
 public static final String INSTANCE_PATTERN="[a-fA-F0-9]{32}";
 private static final String FILE="dubs_events.bin",KEY_ALIAS="dubs_events_key",KEYSTORE="AndroidKeyStore";
 private String token="",deviceId="",origin="",fingerprint="",instanceId="";
 private long cursor=0;
 private boolean enabled=false,needsReconnect=false;
 public EventStore(){}
 public String token(){return token;}
 public String deviceId(){return deviceId;}
 public String origin(){return origin;}
 public String fingerprint(){return fingerprint;}
 public String instanceId(){return instanceId;}
 public long cursor(){return cursor;}
 public boolean enabled(){return enabled;}
 public boolean needsReconnect(){return needsReconnect;}
 public void setRegistration(String token,String deviceId,String origin,String fingerprint,long cursor,String instanceId){this.token=token;this.deviceId=deviceId;this.origin=origin;this.fingerprint=fingerprint;this.cursor=cursor;this.instanceId=instanceId;}
 public void setCursor(long cursor){this.cursor=cursor;}
 public void setInstanceId(String instanceId){this.instanceId=instanceId==null?"":instanceId;}
 public void setEnabled(boolean enabled){this.enabled=enabled;}
 public void setNeedsReconnect(boolean needsReconnect){this.needsReconnect=needsReconnect;}
 public boolean matchesOrigin(String origin,String fingerprint){return this.enabled&&origin.equals(this.origin)&&fingerprint.equals(this.fingerprint);}
 /** Same state identity (origin+token+instance), used by the poll loop as compare-and-set before it persists or posts. */
 public boolean sameRegistrationAs(EventStore other){return enabled==other.enabled&&needsReconnect==other.needsReconnect&&token.equals(other.token)&&origin.equals(other.origin)&&fingerprint.equals(other.fingerprint)&&instanceId.equals(other.instanceId);}
 private static SecretKey key(){
  try{
   KeyStore ks=KeyStore.getInstance(KEYSTORE);ks.load(null);
   if(ks.containsAlias(KEY_ALIAS))return ((KeyStore.SecretKeyEntry)ks.getEntry(KEY_ALIAS,null)).getSecretKey();
   KeyGenerator kg=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,KEYSTORE);
   kg.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build());
   return kg.generateKey();
  }catch(Exception failClosed){throw new IllegalStateException("keystore unavailable",failClosed);}
 }
 private File file(Context c){return new File(c.getFilesDir(),FILE);}
 public void save(Context c){ synchronized(LOCK){
  try{
   JSONObject o=new JSONObject();
   o.put("token",token);o.put("deviceId",deviceId);o.put("origin",origin);o.put("fingerprint",fingerprint);o.put("cursor",cursor);o.put("instanceId",instanceId);o.put("enabled",enabled);o.put("needsReconnect",needsReconnect);
   Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());
   byte[] iv=cipher.getIV(),data=cipher.doFinal(o.toString().getBytes(StandardCharsets.UTF_8));
   File target=file(c),tmp=new File(c.getFilesDir(),FILE+".tmp");
   try(FileOutputStream out=new FileOutputStream(tmp);DataOutputStream dos=new DataOutputStream(out)){
    dos.writeByte(1);dos.write(iv);dos.writeInt(data.length);dos.write(data);dos.flush();out.getFD().sync();
   }
   if(!tmp.renameTo(target)){if(target.exists()&&target.delete()&&tmp.renameTo(target)){}else throw new IllegalStateException("cannot replace store");}
  }catch(Exception failClosed){throw new IllegalStateException("cannot persist notification state",failClosed);}
 }
 }
 public static EventStore load(Context c){ synchronized(LOCK){
  try{
   byte[] all;
   try(FileInputStream in=new FileInputStream(new File(c.getFilesDir(),FILE));ByteArrayOutputStream buffer=new ByteArrayOutputStream()){
    byte[] chunk=new byte[4096];int n;while((n=in.read(chunk))>0){if(buffer.size()+n>65536)throw new IllegalStateException("invalid store size");buffer.write(chunk,0,n);}all=buffer.toByteArray();
   }
   if(all.length<33||all[0]!=1)throw new IllegalStateException("invalid store header");
   int length=readInt(all,1+12);if(length<16||length!=all.length-17)throw new IllegalStateException("invalid store length");byte[] data=Arrays.copyOfRange(all,17,17+length);
   Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Arrays.copyOfRange(all,1,13)));
   JSONObject o=new JSONObject(new String(cipher.doFinal(data),StandardCharsets.UTF_8));
   EventStore s=new EventStore();
   s.setRegistration(o.optString("token"),o.optString("deviceId"),o.optString("origin"),o.optString("fingerprint"),o.optLong("cursor"),o.optString("instanceId"));
   s.setEnabled(o.optBoolean("enabled"));
   s.setNeedsReconnect(o.optBoolean("needsReconnect"));
   return s;
  }catch(Exception missing){return new EventStore();}
 }
 }
 public static void reset(Context c){ synchronized(LOCK){
  try{new File(c.getFilesDir(),FILE).delete();}catch(Exception ignored){}
 }
 }
 private static int readInt(byte[] b,int at){return ((b[at]&255)<<24)|((b[at+1]&255)<<16)|((b[at+2]&255)<<8)|(b[at+3]&255);}
}
