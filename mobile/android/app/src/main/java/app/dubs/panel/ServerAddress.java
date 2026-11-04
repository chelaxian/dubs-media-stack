package app.dubs.panel;
import java.net.URI;
import java.util.Locale;
/** Only an HTTPS origin; never accept credentials, paths, query strings or fragments. */
public final class ServerAddress {
 public static String normalize(String input){
  try{String value=input.trim();if(!value.contains("://"))value="https://"+value;
   URI u=new URI(value);int port=u.getPort();String host=u.getHost();
   if(!"https".equalsIgnoreCase(u.getScheme())||host==null||host.isEmpty()||u.getRawUserInfo()!=null||u.getRawQuery()!=null||u.getRawFragment()!=null||port==0||port>65535||port< -1||!(u.getRawPath()==null||u.getRawPath().isEmpty()||u.getRawPath().equals("/")))return null;
   return new URI("https",null,host.toLowerCase(Locale.ROOT),port==443?-1:port,"/",null,null).toASCIIString();
  }catch(Exception invalid){return null;}
 }
 private ServerAddress(){}
}
