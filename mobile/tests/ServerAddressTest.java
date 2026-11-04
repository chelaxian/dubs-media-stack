import app.dubs.panel.ServerAddress;
public class ServerAddressTest {
 static void check(String input,String expected){String actual=ServerAddress.normalize(input);if(!java.util.Objects.equals(expected,actual))throw new AssertionError(input+": "+actual);}
 public static void main(String[] args){
  check("",null);check("  example.org  ","https://example.org/");check("https://EXAMPLE.org:443/","https://example.org/");check("localhost:9193","https://localhost:9193/");check("https://192.0.2.10:9193/","https://192.0.2.10:9193/");check("https://example.org:8443","https://example.org:8443/");
  for(String bad:new String[]{"http://example.org/","javascript:alert(1)","file:///etc/passwd","https://user:password@example.org","https://example.org/?token=secret","https://example.org/#server","https://example.org/path","https://example.org:0/","https://example.org:65536/","https://example.org\\@evil.org","https://example.org%2f.evil.org/"})check(bad,null);
  System.out.println("PASS HTTPS origin policy, custom host/port, no credentials/path/token/insecure schemes");
 }
}
