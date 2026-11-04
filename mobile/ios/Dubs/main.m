#import <UIKit/UIKit.h>
#import <WebKit/WebKit.h>
#import <UserNotifications/UserNotifications.h>
#import <BackgroundTasks/BackgroundTasks.h>
#import <Security/Security.h>
#import <CommonCrypto/CommonDigest.h>
@class PanelController;
#define EVENTS_TASK_ID @"dubs.events.refresh"
#define EVENTS_SERVICE @"dubs.events"
#define EVENTS_ACCOUNT @"token"
/* ---- Local event notifications (no push: there is no APNs account).
   Token lives ONLY in the app Keychain (kSecAttrAccessibleAfterFirstUnlock-
   ThisDeviceOnly, app access group) — any Keychain failure disables the
   feature (fail closed, no plaintext fallback). The durable cursor is
   persisted BEFORE posting each notification, so restarts and re-enables
   never replay. Polls hit the registered HTTPS origin only, redirects are
   rejected, and the narrow scoped token never authenticates anything else.
   Delivery is opportunistic: BGAppRefresh may be delayed or skipped by iOS,
   and after force-quit nothing is guaranteed. ---- */
static NSString *OriginString(NSURL *url){
 if(!url.host.length)return @"";
 if(url.port&&!([url.port integerValue]==443))return [NSString stringWithFormat:@"https://%@:%@/",url.host.lowercaseString,url.port];
 return [NSString stringWithFormat:@"https://%@/",url.host.lowercaseString];
}
static NSString *Fingerprint(NSURL *url){
 NSString *origin=OriginString(url);
 if(!origin.length)return @"";
 unsigned char digest[CC_SHA256_DIGEST_LENGTH];
 const char *bytes=[origin UTF8String];
 CC_SHA256(bytes,(CC_LONG)strlen(bytes),digest);
 NSMutableString *hex=[NSMutableString stringWithCapacity:16];
 for(int i=0;i<8;i++)[hex appendFormat:@"%02x",digest[i]];
 return hex;
}
static BOOL KeychainSaveToken(NSString *token){
 if(!token.length)return NO;
 NSData *data=[token dataUsingEncoding:NSUTF8StringEncoding];
 NSDictionary *query=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:EVENTS_SERVICE,(__bridge id)kSecAttrAccount:EVENTS_ACCOUNT};
 SecItemDelete((__bridge CFDictionaryRef)query);
 NSDictionary *attributes=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:EVENTS_SERVICE,(__bridge id)kSecAttrAccount:EVENTS_ACCOUNT,(__bridge id)kSecValueData:data,(__bridge id)kSecAttrAccessible:(__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly};
 OSStatus status=SecItemAdd((__bridge CFDictionaryRef)attributes,NULL);
 return status==errSecSuccess;
}
static NSString *KeychainLoadToken(void){
 NSDictionary *query=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:EVENTS_SERVICE,(__bridge id)kSecAttrAccount:EVENTS_ACCOUNT,(__bridge id)kSecReturnData:@YES,(__bridge id)kSecMatchLimit:(__bridge id)kSecMatchLimitOne};
 CFDataRef found=NULL;
 if(SecItemCopyMatching((__bridge CFDictionaryRef)query,(CFTypeRef *)&found)!=errSecSuccess)return nil;
 NSString *token=[[NSString alloc]initWithData:(__bridge_transfer NSData *)found encoding:NSUTF8StringEncoding];
 return token;
}
static void KeychainDeleteToken(void){
 NSDictionary *query=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:EVENTS_SERVICE,(__bridge id)kSecAttrAccount:EVENTS_ACCOUNT};
 SecItemDelete((__bridge CFDictionaryRef)query);
}
static NSUInteger EventsGeneration;
static BOOL ValidInstance(id value){return [value isKindOfClass:NSString.class]&&[[NSPredicate predicateWithFormat:@"SELF MATCHES %@",@"[a-fA-F0-9]{32}"]evaluateWithObject:value];}
static NSDictionary *EventsDefaults(void){return [[NSUserDefaults standardUserDefaults]dictionaryForKey:@"dubs.events.state"]?:@{};}
static void EventsSetDefaults(NSDictionary *state){[[NSUserDefaults standardUserDefaults]setObject:state forKey:@"dubs.events.state"];}
static NSString *RegistrationScript(NSString *platform){
 return [NSString stringWithFormat:@"return await (async()=>{try{const s=await fetch('/api/session',{credentials:'same-origin'});if(!s.ok)return JSON.stringify({error:'session'});const sess=await s.json();const body=new URLSearchParams({csrf:sess.csrf,platform:'%@'});const r=await fetch('/api/notifications/devices',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});if(!r.ok)return JSON.stringify({error:'register '+r.status});return JSON.stringify(await r.json())}catch(e){return JSON.stringify({error:'network'})}})()",platform];
}
static NSString *RevokeScript(NSString *deviceId){
 NSString *safe=[deviceId stringByReplacingOccurrencesOfString:[NSString stringWithFormat:@"[^A-Za-z0-9_.-]"] withString:@"" options:NSRegularExpressionSearch range:NSMakeRange(0,deviceId.length)];
 return [NSString stringWithFormat:@"(async()=>{try{const s=await fetch('/api/session',{credentials:'same-origin'});if(!s.ok)return JSON.stringify({error:'session'});const sess=await s.json();const body=new URLSearchParams({csrf:sess.csrf,deviceId:'%@'});const r=await fetch('/api/notifications/revoke',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});return JSON.stringify({ok:r.ok})}catch(e){return JSON.stringify({error:'network'})}})()",safe];
}
static NSString *EventsRoute(NSString *fingerprintExpected,NSString *fingerprintActual,NSInteger episode,NSString *section){
 if(!fingerprintExpected.length||![fingerprintExpected isEqualToString:fingerprintActual])return nil;
 if(episode>0&&episode<=9999999999LL)return [NSString stringWithFormat:@"#released?episode=%ld",(long)episode];
 if([section isEqualToString:@"events"])return @"#settings?section=events";
 return nil;
}
static NSDictionary *TypeLabels(void){return @{@"download_started":@"Начало загрузки",@"torrent_complete":@"Раздача скачана",@"library_ready":@"Появилось в медиатеке",@"new_episode":@"Новая серия",@"upgrade_started":@"Ежедневная проверка замены",@"upgrade_result":@"Результат замены"};}
@interface DubsPollSession : NSObject <NSURLSessionDataDelegate>
@property(nonatomic,copy)void(^done)(NSDictionary *json);
@property(nonatomic,strong)NSMutableData *buffer;
@property(nonatomic,strong)NSURLSession *session;
- (void)finish:(NSDictionary *)json;
@end
@implementation DubsPollSession
- (void)start:(NSURL *)url token:(NSString *)token{
 self.buffer=[NSMutableData data];
 NSURLSessionConfiguration *config=[NSURLSessionConfiguration ephemeralSessionConfiguration];
 config.URLCache=nil;
 self.session=[NSURLSession sessionWithConfiguration:config delegate:self delegateQueue:NSOperationQueue.mainQueue];
 NSMutableURLRequest *request=[NSMutableURLRequest requestWithURL:url];
 request.timeoutInterval=30;
 [request setValue:[NSString stringWithFormat:@"Bearer %@",token]forHTTPHeaderField:@"Authorization"];
 [request setValue:@"application/json" forHTTPHeaderField:@"Accept"];
 [[self.session dataTaskWithRequest:request]resume];
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data{if(self.buffer.length+data.length>512*1024){[self finish:nil];return;}[self.buffer appendData:data];}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request completionHandler:(void (^)(NSURLRequest *))handler{
 handler(nil); // scoped token must not follow redirects
 [self finish:nil];
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error{
 if(!error&&[(NSHTTPURLResponse *)task.response statusCode]>=200&&[(NSHTTPURLResponse *)task.response statusCode]<=201){
  NSDictionary *json=[NSJSONSerialization JSONObjectWithData:self.buffer options:0 error:nil];
  [self finish:[json isKindOfClass:NSDictionary.class]?json:nil];
 }else {NSInteger code=[(NSHTTPURLResponse *)task.response statusCode];[self finish:(code==401||code==403)?@{@"revoked":@YES}:nil];}
}
- (void)finish:(NSDictionary *)json{
 void(^done)(NSDictionary *json)=self.done;
 self.done=nil;
 if(done)done(json);
 if(self.session){[self.session invalidateAndCancel];self.session=nil;}
}
@end
/* A single completion gate is shared by network completion and expiration. */
@interface DubsRefreshCompletion : NSObject
@property(nonatomic,copy)void (^done)(BOOL);
@property(nonatomic,copy)void (^cancel)(void);
@property(nonatomic,assign)BOOL finished;
- (void)finish:(BOOL)ok;
- (void)expire;
@end
@implementation DubsRefreshCompletion
- (void)finish:(BOOL)ok{if(self.finished)return;self.finished=YES;void (^done)(BOOL)=self.done;self.done=nil;self.cancel=nil;if(done)done(ok);}
- (void)expire{if(self.finished)return;self.finished=YES;void (^cancel)(void)=self.cancel;void (^done)(BOOL)=self.done;self.cancel=nil;self.done=nil;if(cancel)cancel();if(done)done(NO);}
@end
@interface DubsEvents : NSObject
+ (NSString *)registrationScript;
+ (void)registerBackgroundHandler;
+ (void)scheduleRefresh;
+ (void)cancelRefresh;
+ (BOOL)enabled;
+ (NSString *)statusLine;
+ (void)pollWithCompletion:(void (^)(BOOL))completion;
+ (void)wipe:(PanelController *)panel revoke:(BOOL)revoke;
+ (BOOL)consume:(NSDictionary *)json state:(NSDictionary *)state;
+ (void)post:(NSDictionary *)event showTitles:(BOOL)showTitles fingerprint:(NSString *)fingerprint;
@end
static DubsPollSession *ActivePoll;
@implementation DubsEvents
+ (NSString *)registrationScript{return RegistrationScript(@"ios");}
+ (void)registerBackgroundHandler{
 [[BGTaskScheduler sharedScheduler]registerTaskWithIdentifier:EVENTS_TASK_ID usingQueue:dispatch_get_main_queue() launchHandler:^(BGTask *task){
  if(![task isKindOfClass:BGAppRefreshTask.class]){[task setTaskCompletedWithSuccess:NO];return;}
  DubsRefreshCompletion *gate=[DubsRefreshCompletion new];
  gate.done=^(BOOL ok){task.expirationHandler=nil;[task setTaskCompletedWithSuccess:ok];[self scheduleRefresh];};
  gate.cancel=^{++EventsGeneration;[ActivePoll finish:nil];};
  task.expirationHandler=^{dispatch_async(dispatch_get_main_queue(),^{[gate expire];});};
  [self pollWithCompletion:^(BOOL ok){[gate finish:ok];}];
 }];
}
+ (void)scheduleRefresh{
 if(![self enabled])return;
 BGAppRefreshTaskRequest *request=[[BGAppRefreshTaskRequest alloc]initWithIdentifier:EVENTS_TASK_ID];
 request.earliestBeginDate=[NSDate dateWithTimeIntervalSinceNow:15*60];
 [[BGTaskScheduler sharedScheduler]submitTaskRequest:request error:nil];
}
+ (void)cancelRefresh{
 [[BGTaskScheduler sharedScheduler]cancelTaskRequestWithIdentifier:EVENTS_TASK_ID];
}
+ (BOOL)enabled{
 return [EventsDefaults()[@"enabled"]boolValue]&&![EventsDefaults()[@"needsReconnect"]boolValue]&&KeychainLoadToken().length>0;
}
+ (NSString *)statusLine{
 if(![self enabled])return @"Уведомления на этом устройстве выключены. Разрешение iOS запрашивается при включении.";
 return @"Устройство зарегистрировано. Фон: BGAppRefresh — iOS выполняет его без гарантий и никогда после принудительного закрытия приложения.";
}
/* Serialized poll: background task and foreground trigger never race. */
static BOOL pollLocked;
+ (void)pollWithCompletion:(void (^)(BOOL))completion{
 if(!NSThread.isMainThread){dispatch_async(dispatch_get_main_queue(),^{[self pollWithCompletion:completion];});return;}
 @synchronized(self){
  if(pollLocked){if(completion)completion(NO);return;}
  pollLocked=YES;
 }
 NSDictionary *state=EventsDefaults();
 NSString *token=KeychainLoadToken();
 if(![self enabled]||![state[@"origin"]isKindOfClass:NSString.class]||![state[@"origin"]length]||!token.length){pollLocked=NO;if(completion)completion(NO);return;}
 NSURL *url=[NSURL URLWithString:[NSString stringWithFormat:@"%@api/notifications/events?after=%@&limit=50",state[@"origin"],state[@"cursor"]?:@0]];
 if(!url||![url.scheme isEqualToString:@"https"]||url.user.length||url.password.length){pollLocked=NO;if(completion)completion(NO);return;}
 __block BOOL result=NO;
 DubsPollSession *poll=[DubsPollSession new];ActivePoll=poll;
 NSUInteger generation=EventsGeneration;
 poll.done=^(NSDictionary *json){
  @try{
   if(json&&generation==EventsGeneration&&[EventsDefaults() isEqual:state]){
    if([json[@"revoked"]boolValue]){NSMutableDictionary *revoked=[state mutableCopy];revoked[@"needsReconnect"]=@YES;EventsSetDefaults(revoked);[self cancelRefresh];}
    else result=[self consume:json state:state];
   }
  }@finally{
   pollLocked=NO;ActivePoll=nil;
   if(completion)completion(result);
  }
 };
 [poll start:url token:token];
}
+ (BOOL)consume:(NSDictionary *)json state:(NSDictionary *)state{
 if(![state[@"enabled"]boolValue]||![EventsDefaults() isEqual:state])return NO;
 NSString *instance=json[@"instanceId"];
 if(!ValidInstance(instance))return NO;
 NSNumber *cursor=json[@"cursor"];
 if(![cursor isKindOfClass:NSNumber.class]||cursor.longLongValue<0)return NO;
 BOOL rebase=instance&&state[@"instanceId"]&&![instance isEqual:state[@"instanceId"]];
 NSMutableDictionary *next=[state mutableCopy];
 if(instance)next[@"instanceId"]=instance;
 if(cursor)next[@"cursor"]=cursor;
 if(rebase){EventsSetDefaults(next);[UNUserNotificationCenter.currentNotificationCenter removeAllPendingNotificationRequests];[UNUserNotificationCenter.currentNotificationCenter removeAllDeliveredNotifications];return YES;} // server state reset: re-baseline silently, never replay
 NSDictionary *settings=[json[@"settings"]isKindOfClass:NSDictionary.class]?json[@"settings"]:@{};
 BOOL accountOn=[settings[@"enabled"]boolValue];
 BOOL showTitles=[settings[@"showTitles"]boolValue];
 NSSet *allowed=[NSSet setWithArray:[settings[@"types"]isKindOfClass:NSArray.class]?settings[@"types"]:@[]];
 NSArray *events=[json[@"events"]isKindOfClass:NSArray.class]?json[@"events"]:@[];
 if(events.count>100)return NO;
 long long last=[state[@"cursor"]longLongValue];
 for(id item in events){if(![item isKindOfClass:NSDictionary.class]||![item[@"id"]isKindOfClass:NSNumber.class]||[item[@"id"]longLongValue]<=last||![item[@"type"]isKindOfClass:NSString.class]||!TypeLabels()[item[@"type"]])return NO;last=[item[@"id"]longLongValue];}
 if(cursor.longLongValue<last)return NO;
 for(NSDictionary *event in events){
  if(![event isKindOfClass:NSDictionary.class])continue;
  long long eventId=[event[@"id"]longLongValue];
  long long previous=[state[@"cursor"]longLongValue];
  if(eventId<=previous)continue;
  /* Write-ahead: durable cursor BEFORE posting, so restarts never duplicate. */
  NSMutableDictionary *advance=[EventsDefaults()mutableCopy];
  advance[@"cursor"]=@(MAX(eventId,[advance[@"cursor"]longLongValue]));
  EventsSetDefaults(advance);
  if(!accountOn||![allowed containsObject:event[@"type"]])continue;
  [self post:event showTitles:showTitles fingerprint:state[@"fp"]];
 }
 NSMutableDictionary *tail=[EventsDefaults()mutableCopy];
 if([cursor isKindOfClass:NSNumber.class]&&cursor.longLongValue>[tail[@"cursor"]longLongValue]){tail[@"cursor"]=cursor;EventsSetDefaults(tail);}
 return YES;
}
+ (void)post:(NSDictionary *)event showTitles:(BOOL)showTitles fingerprint:(NSString *)fingerprint{
 long long eventId=[event[@"id"]longLongValue];
 NSString *type=[event[@"type"]isKindOfClass:NSString.class]?event[@"type"]:@"";
 NSString *generic=TypeLabels()[type]?:@"Событие медиатеки";
 NSString *serverTitle=[event[@"title"]isKindOfClass:NSString.class]?event[@"title"]:@"";
 NSString *message=[event[@"message"]isKindOfClass:NSString.class]&&[event[@"message"] length]?event[@"message"]:serverTitle;
 if(!message.length)message=generic;
 NSString *title=(showTitles&&serverTitle.length)?serverTitle:@"Dubs";
 UNMutableNotificationContent *content=[UNMutableNotificationContent new];
 content.title=title;
 content.body=showTitles?message:generic; /* privacy default: generic only */
 content.sound=[UNNotificationSound defaultSound];
 long long episodeId=[event[@"episodeId"]isKindOfClass:NSNumber.class]?[event[@"episodeId"]longLongValue]:0;
 NSMutableDictionary *info=[NSMutableDictionary dictionary];
 info[@"eventId"]=@(eventId);
 if(episodeId>0&&episodeId<=9999999999LL)info[@"episodeId"]=@(episodeId);else info[@"section"]=@"events";
 info[@"fp"]=fingerprint?:@"";info[@"instanceId"]=EventsDefaults()[@"instanceId"]?:@"";
 content.userInfo=info;
 NSString *identifier=[NSString stringWithFormat:@"%@-%@-%lld",fingerprint?:@"",EventsDefaults()[@"instanceId"]?:@"",eventId];
 UNNotificationRequest *request=[UNNotificationRequest requestWithIdentifier:identifier content:content trigger:nil];
 [[UNUserNotificationCenter currentNotificationCenter]addNotificationRequest:request withCompletionHandler:nil];
}
+ (void)wipe:(PanelController *)panel revoke:(BOOL)revoke{
 ++EventsGeneration;[ActivePoll finish:nil];
 NSMutableDictionary *state=[EventsDefaults()mutableCopy];
 NSString *deviceId=state[@"deviceId"]?:@"";
 EventsSetDefaults(@{});
 KeychainDeleteToken();
 [UNUserNotificationCenter.currentNotificationCenter removeAllPendingNotificationRequests];
 [UNUserNotificationCenter.currentNotificationCenter removeAllDeliveredNotifications];
 [self cancelRefresh];
 if(revoke&&deviceId.length&&panel)[(id)panel evaluateJavaScriptString:RevokeScript(deviceId)];
}
@end
@interface PanelController : UIViewController <WKNavigationDelegate,WKUIDelegate,UNUserNotificationCenterDelegate>
@property(nonatomic,strong) WKWebView *web;
@property(nonatomic,strong) NSURL *serverURL;
@property(nonatomic,assign) BOOL switchingServer;
- (void)evaluateJavaScriptString:(NSString *)script;
- (void)applyRoute:(NSString *)hash;
@end
static __weak PanelController *activePanel;
@implementation PanelController
- (UIStatusBarStyle)preferredStatusBarStyle{return UIStatusBarStyleLightContent;}
- (void)evaluateJavaScriptString:(NSString *)script{[self.web evaluateJavaScript:script completionHandler:nil];}
- (void)applyRoute:(NSString *)hash{
 if(!self.serverURL||!hash.length)return;
 [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithFormat:@"%@%@",OriginString(self.serverURL),hash]]]];
}
- (NSURL *)normalizedServer:(NSString *)input{
 NSString *value=[input stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];if(![value containsString:@"://"])value=[@"https://" stringByAppendingString:value];
 NSURLComponents *c=[NSURLComponents componentsWithString:value];
 if(![c.scheme.lowercaseString isEqual:@"https"]||!c.host.length||c.user||c.password||c.query||c.fragment||(c.path.length&&![c.path isEqual:@"/"])||(c.port&&(c.port.intValue<1||c.port.intValue>65535)))return nil;
 c.scheme=@"https";c.host=c.host.lowercaseString;if(c.port.intValue==443)c.port=nil;c.path=@"/";return c.URL;
}
- (NSURL *)home{return self.serverURL;}
- (BOOL)panel:(NSURL *)u{return self.home&&[u.scheme isEqual:@"https"]&&[u.host.lowercaseString isEqual:self.home.host.lowercaseString]&&!u.user&&!u.password&&(u.port.intValue?:443)==(self.home.port.intValue?:443);}
- (BOOL)library:(NSURL *)u{
 if(![u.scheme isEqual:@"https"]||!u.host.length||u.user||u.password||u.query||![u.path isEqual:@"/web/"])return NO;
 return u.fragment&&[u.fragment rangeOfString:@"^/details[?]id=[a-fA-F0-9]{32}(&serverId=[a-fA-F0-9]{32})?$" options:NSRegularExpressionSearch].location!=NSNotFound;
}
- (void)serverAddressChanged:(UITextField *)field{UIAlertController *a=(UIAlertController *)self.presentedViewController;if([a isKindOfClass:UIAlertController.class])a.actions.firstObject.enabled=[self normalizedServer:field.text]!=nil;}
- (void)serverSettings{
 if(self.presentedViewController)return;
 UIAlertController *a=[UIAlertController alertControllerWithTitle:@"Адрес веб-панели" message:@"Укажите HTTPS-адрес панели Dubs с действительным сертификатом. При смене сервера потребуется войти заново." preferredStyle:UIAlertControllerStyleAlert];
 [a addTextFieldWithConfigurationHandler:^(UITextField *field){field.placeholder=@"https://panel.example.org";field.text=self.home.absoluteString?:@"";field.keyboardType=UIKeyboardTypeURL;field.autocapitalizationType=UITextAutocapitalizationTypeNone;field.autocorrectionType=UITextAutocorrectionTypeNo;field.accessibilityIdentifier=@"server-address";[field addTarget:self action:@selector(serverAddressChanged:) forControlEvents:UIControlEventEditingChanged];}];
 [a addAction:[UIAlertAction actionWithTitle:@"Подключиться" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action){NSURL *next=[self normalizedServer:a.textFields.firstObject.text];if(!next){dispatch_async(dispatch_get_main_queue(),^{[self serverSettings];});return;}if([next isEqual:self.home]){[self.web loadRequest:[NSURLRequest requestWithURL:next]];return;}self.switchingServer=YES;[self.web stopLoading];[self.web loadHTMLString:@"" baseURL:nil];[DubsEvents wipe:self revoke:NO];NSSet *types=WKWebsiteDataStore.allWebsiteDataTypes;[WKWebsiteDataStore.defaultDataStore removeDataOfTypes:types modifiedSince:NSDate.distantPast completionHandler:^{self.serverURL=next;[NSUserDefaults.standardUserDefaults setObject:next.absoluteString forKey:@"panel_url"];self.switchingServer=NO;[self.web loadRequest:[NSURLRequest requestWithURL:next]];}];}]];
 a.actions.firstObject.enabled=self.home!=nil;
 if(self.home)[a addAction:[UIAlertAction actionWithTitle:@"Отмена" style:UIAlertActionStyleCancel handler:nil]];
 [self presentViewController:a animated:YES completion:nil];
}
- (void)eventsSettings{
 [UNUserNotificationCenter.currentNotificationCenter getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings){dispatch_async(dispatch_get_main_queue(),^{[self showEventsSettings:settings];});}];
}
- (void)showEventsSettings:(UNNotificationSettings *)permission{
 if(self.presentedViewController||!self.home)return;
 NSDictionary *state=EventsDefaults();
 NSMutableString *status=[NSMutableString string];
 BOOL permitted=permission.authorizationStatus==UNAuthorizationStatusAuthorized||permission.authorizationStatus==UNAuthorizationStatusProvisional;
 if(!permitted)[status appendString:@"Разрешение iOS не выдано. Откройте системные настройки. "];
 if([DubsEvents enabled]&&permitted)[status appendString:@"Устройство зарегистрировано. iOS выполняет фоновую проверку (BGAppRefresh) без гарантий; после принудительного закрытия приложения уведомлений не будет."];
 else [status appendString:@"Уведомления на этом устройстве выключены."];
 UIAlertController *a=[UIAlertController alertControllerWithTitle:@"События и уведомления" message:status preferredStyle:UIAlertControllerStyleAlert];
 BOOL on=[DubsEvents enabled]&&permitted;
 UIAlertAction *enableAction=[UIAlertAction actionWithTitle:on?"Проверить сейчас":"Включить уведомления на устройстве" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action){if(on)[[DubsEvents class]pollWithCompletion:nil];else[self requestAuthorizationAndRegister];}];
 [a addAction:enableAction];
 [a addAction:[UIAlertAction actionWithTitle:@"Типы событий (аккаунт)" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action){[self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithFormat:@"%@#settings?section=events",OriginString(self.serverURL)]]]];}]];
 [a addAction:[UIAlertAction actionWithTitle:@"Системные настройки" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action){[[UIApplication sharedApplication]openURL:[NSURL URLWithString:UIApplicationOpenSettingsURLString]options:@{} completionHandler:nil];}]];
 [a addAction:[UIAlertAction actionWithTitle:@"Отключить и отозвать устройство" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *action){[DubsEvents wipe:self revoke:YES];[self eventsSettings];}]];
 [a addAction:[UIAlertAction actionWithTitle:@"Закрыть" style:UIAlertActionStyleCancel handler:nil]];
 [self presentViewController:a animated:YES completion:nil];
}
- (void)requestAuthorizationAndRegister{
 NSString *origin=OriginString(self.serverURL);NSUInteger generation=EventsGeneration;
 [UNUserNotificationCenter.currentNotificationCenter requestAuthorizationWithOptions:(UNAuthorizationOptionAlert|UNAuthorizationOptionSound) completionHandler:^(BOOL granted,NSError *error){
  dispatch_async(dispatch_get_main_queue(),^{
   if(generation!=EventsGeneration||![origin isEqualToString:OriginString(self.serverURL)])return;
   if(!granted){[self notice:@"Уведомления не разрешены. Включите их в Системных настройках или используйте веб-панель." browser:nil];return;}
   if(!self.web.URL||![self panel:self.web.URL]){[self notice:@"Откройте панель и войдите, чтобы зарегистрировать события." browser:nil];return;}
   [self registerDevice];
  });
 }];
}
- (void)registerDevice{
 __weak PanelController *weakSelf=self;
 NSString *capturedOrigin=OriginString(self.serverURL);NSUInteger generation=++EventsGeneration;
 [self.web callAsyncJavaScript:[DubsEvents registrationScript] arguments:@{} inFrame:nil inContentWorld:WKContentWorld.pageWorld completionHandler:^(id result,NSError *error){
  PanelController *self=weakSelf;if(!self||generation!=EventsGeneration||self.switchingServer||![capturedOrigin isEqualToString:OriginString(self.serverURL)]||![self panel:self.web.URL])return;
  NSString *json=nil;
  if([result isKindOfClass:NSString.class])json=result;
  else if(result)json=[NSString stringWithFormat:@"%@",result];
  if(!json.length){[self notice:@"Регистрация не удалась: нет ответа сервера." browser:nil];return;}
  NSData *data=[json dataUsingEncoding:NSUTF8StringEncoding];
  id parsed=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
  if(parsed&&! [parsed isKindOfClass:NSDictionary.class]){
   NSData *inner=[parsed isKindOfClass:NSString.class]?[(NSString *)parsed dataUsingEncoding:NSUTF8StringEncoding]:nil;
   parsed=inner?[NSJSONSerialization JSONObjectWithData:inner options:0 error:nil]:nil;
  }
  if(![parsed isKindOfClass:NSDictionary.class]){[self notice:@"Регистрация не удалась: некорректный ответ." browser:nil];return;}
  NSString *errorField=[parsed[@"error"]isKindOfClass:NSString.class]?parsed[@"error"]:nil;
  if(errorField){[self notice:[NSString stringWithFormat:@"Регистрация не удалась: %@.",errorField] browser:nil];return;}
  NSString *token=[parsed[@"token"]isKindOfClass:NSString.class]?parsed[@"token"]:nil;
  NSString *deviceId=[parsed[@"deviceId"]isKindOfClass:NSString.class]?parsed[@"deviceId"]:nil;
  NSNumber *cursor=[parsed[@"cursor"]isKindOfClass:NSNumber.class]?parsed[@"cursor"]:nil;
  NSString *instanceId=ValidInstance(parsed[@"instanceId"])?parsed[@"instanceId"]:nil;
  NSPredicate *tokenShape=[NSPredicate predicateWithFormat:@"SELF MATCHES %@",@"[A-Za-z0-9_-]{43}"];
  if(![tokenShape evaluateWithObject:token]||!deviceId.length||!cursor||cursor.longLongValue<0||!instanceId){[self notice:@"Регистрация не удалась: некорректный ответ сервера." browser:nil];return;}
  if(!KeychainSaveToken(token)){[self notice:@"Keychain недоступен — уведомления не включены (защита не понижается)." browser:nil];return;}
  EventsSetDefaults(@{@"deviceId":deviceId,@"cursor":cursor,@"instanceId":instanceId,@"origin":OriginString(self.serverURL),@"fp":Fingerprint(self.serverURL),@"enabled":@YES});
  [DubsEvents scheduleRefresh];[DubsEvents pollWithCompletion:nil];
  [self notice:@"События включены. Фоновая проверка — BGAppRefresh, iOS может откладывать или пропускать её." browser:nil];
 }];
}
- (void)viewDidAppear:(BOOL)animated{[super viewDidAppear:animated];if(!self.home)[self serverSettings];}
- (void)webView:(WKWebView *)web didFinishNavigation:(WKNavigation *)navigation{
 if(![self panel:web.URL])return;
 [web evaluateJavaScript:@"(()=>{const add=()=>{const p=document.querySelector('#events-panel');if(p&&!document.getElementById('native-events-settings')){const b=document.createElement('button');b.id='native-events-settings';b.type='button';b.className='quiet';b.textContent='Уведомления на этом устройстве';b.addEventListener('click',()=>{location.href='dubs-native://notifications'});p.appendChild(b);}const s=document.querySelector('#settings');if(!s||document.getElementById('native-server-settings'))return;const a=document.createElement('a');a.id='native-server-settings';a.href='dubs-settings://server';a.className='quiet';a.textContent='Изменить адрес веб-панели';s.prepend(a)};add();if(!window.dubsServerObserver){window.dubsServerObserver=new MutationObserver(add);window.dubsServerObserver.observe(document.body,{childList:true,subtree:true})}})()" completionHandler:nil];
 if([DubsEvents enabled])[[DubsEvents class]pollWithCompletion:nil];
}
- (BOOL)validID:(NSString *)s{return s&&[s rangeOfString:@"^[a-fA-F0-9]{32}$" options:NSRegularExpressionSearch].location!=NSNotFound;}
- (void)viewDidLoad{
 [super viewDidLoad];self.view.backgroundColor=UIColor.blackColor;self.serverURL=[self normalizedServer:[NSUserDefaults.standardUserDefaults stringForKey:@"panel_url"]?:@""];
 activePanel=self;
 WKWebViewConfiguration *config=[WKWebViewConfiguration new];config.websiteDataStore=WKWebsiteDataStore.defaultDataStore;config.preferences.javaScriptCanOpenWindowsAutomatically=NO;config.applicationNameForUserAgent=@"DubsNative/1.2 iOS";
 self.web=[[WKWebView alloc]initWithFrame:CGRectZero configuration:config];self.web.navigationDelegate=self;self.web.UIDelegate=self;self.web.allowsBackForwardNavigationGestures=YES;self.web.opaque=NO;self.web.backgroundColor=UIColor.blackColor;self.web.scrollView.backgroundColor=UIColor.blackColor;self.web.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:self.web];UILayoutGuide *safe=self.view.safeAreaLayoutGuide;
 [NSLayoutConstraint activateConstraints:@[[self.web.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor],[self.web.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor],[self.web.topAnchor constraintEqualToAnchor:safe.topAnchor],[self.web.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor]]];
 UIRefreshControl *refresh=[UIRefreshControl new];refresh.tintColor=UIColor.systemRedColor;[refresh addTarget:self action:@selector(reload:) forControlEvents:UIControlEventValueChanged];self.web.scrollView.refreshControl=refresh;if(self.home)[self.web loadRequest:[NSURLRequest requestWithURL:self.home]];
 UIButton *settings=[UIButton buttonWithType:UIButtonTypeSystem];[settings setTitle:@"⚙ Сервер" forState:UIControlStateNormal];settings.accessibilityLabel=@"Настройки адреса веб-панели";settings.accessibilityIdentifier=@"server-settings";[settings addTarget:self action:@selector(serverSettings) forControlEvents:UIControlEventTouchUpInside];settings.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:settings];
 UIButton *events=[UIButton buttonWithType:UIButtonTypeSystem];[events setTitle:@"🔔" forState:UIControlStateNormal];events.accessibilityLabel=@"События и уведомления на этом устройстве";events.accessibilityIdentifier=@"events-settings";[events addTarget:self action:@selector(eventsSettings) forControlEvents:UIControlEventTouchUpInside];events.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:events];
 [NSLayoutConstraint activateConstraints:@[[events.trailingAnchor constraintEqualToAnchor:settings.leadingAnchor constant:-8],[events.topAnchor constraintEqualToAnchor:safe.topAnchor],[events.heightAnchor constraintEqualToConstant:44],[events.widthAnchor constraintEqualToConstant:44]]];
 [NSLayoutConstraint activateConstraints:@[[settings.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-8],[settings.topAnchor constraintEqualToAnchor:safe.topAnchor],[settings.heightAnchor constraintEqualToConstant:44],[settings.widthAnchor constraintEqualToConstant:100]]];
 settings.backgroundColor=[UIColor.blackColor colorWithAlphaComponent:0.85];settings.tintColor=UIColor.systemRedColor;events.backgroundColor=[UIColor.blackColor colorWithAlphaComponent:0.85];events.tintColor=UIColor.systemRedColor;
}
- (void)reload:(UIRefreshControl *)sender{if(self.home)[self.web loadRequest:[NSURLRequest requestWithURL:self.home]];else [self serverSettings];[sender endRefreshing];}
- (void)notice:(NSString *)message browser:(NSURL *)url{
 if(self.presentedViewController)return;
 UIAlertController *a=[UIAlertController alertControllerWithTitle:@"Dubs" message:message preferredStyle:UIAlertControllerStyleAlert];[a addAction:[UIAlertAction actionWithTitle:@"ОК" style:UIAlertActionStyleCancel handler:nil]];
 if(url)[a addAction:[UIAlertAction actionWithTitle:@"Открыть в браузере" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action){[UIApplication.sharedApplication openURL:url options:@{} completionHandler:nil];}]];
 [self presentViewController:a animated:YES completion:nil];
}
- (void)openEpisode:(NSURL *)original{
 NSString *fragment=original.fragment;NSRange q=[fragment rangeOfString:@"?"];if(!fragment||q.location==NSNotFound)return;
 NSURLComponents *parts=[NSURLComponents componentsWithString:[@"https://local/?" stringByAppendingString:[fragment substringFromIndex:q.location+1]]];NSString *item=nil,*server=nil;
 for(NSURLQueryItem *part in parts.queryItems){if([part.name isEqual:@"id"])item=part.value;if([part.name isEqual:@"serverId"])server=part.value;}
 if(![self validID:item]||![self validID:server])return;
 __weak PanelController *weakSelf=self;
 [self.web callAsyncJavaScript:@"const r=await fetch('/api/session',{credentials:'same-origin'});if(!r.ok)return '';return (await r.json()).userId||'';" arguments:@{} inFrame:nil inContentWorld:WKContentWorld.pageWorld completionHandler:^(id result,NSError *error){
  PanelController *self=weakSelf;if(!self||generation!=EventsGeneration||self.switchingServer||![capturedOrigin isEqualToString:OriginString(self.serverURL)]||![self panel:self.web.URL])return;
  if(error||![result isKindOfClass:NSString.class]||![self validID:result]){[self notice:@"Обновите вход в Dubs и повторите переход." browser:nil];return;}
  NSURL *target=[NSURL URLWithString:[NSString stringWithFormat:@"swiftfin://%@/%@/item/%@",server,result,item]];
  [UIApplication.sharedApplication openURL:target options:@{} completionHandler:^(BOOL success){if(!success)[weakSelf notice:@"Для перехода к конкретной серии установите Swiftfin и подключите в нём свой сервер Jellyfin под своей учётной записью. Jellyfin Mobile пока не поддерживает этот формат ссылки." browser:original];}];
 }];
}
- (BOOL)openPlayerURL:(NSURL *)url{
 if(![self panel:self.web.URL])return NO;
 NSString *raw=url.absoluteString;NSArray *patterns=@[@"^swiftfin://[a-fA-F0-9]{32}/[a-fA-F0-9]{32}/item/[a-fA-F0-9]{32}$",@"^fladder://details[?]id=[a-fA-F0-9]{32}&userId=[a-fA-F0-9]{32}&serverId=[a-fA-F0-9]{32}$",@"^streamyfin://items/page[?]id=[a-fA-F0-9]{32}$",@"^infuse://series/[0-9]+-[0-9]+-[0-9]+$"];
 for(NSString *pattern in patterns){if([raw rangeOfString:pattern options:NSRegularExpressionSearch].location!=NSNotFound){[UIApplication.sharedApplication openURL:url options:@{} completionHandler:^(BOOL opened){if(!opened)[self notice:@"Выбранный клиент не установлен или не принимает ссылку. Выберите другой в настройках Dubs." browser:nil];}];return YES;}}
 if([url.scheme isEqual:@"dubs-browser"]&&[url.host isEqual:@"open"]){NSURLComponents *parts=[NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO];if(parts.queryItems.count!=1)return NO;NSURL *target=[NSURL URLWithString:parts.queryItems.firstObject.value];if([self library:target]&&[target.path isEqual:@"/web/"]){[UIApplication.sharedApplication openURL:target options:@{} completionHandler:nil];return YES;}}
 return NO;
}
- (void)webView:(WKWebView *)web decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler{
 NSURL *url=action.request.URL;if([url.absoluteString isEqual:@"dubs-settings://server"]&&[self panel:self.web.URL]){decisionHandler(WKNavigationActionPolicyCancel);[self serverSettings];return;}if([url.absoluteString isEqual:@"dubs-native://notifications"]&&[self panel:self.web.URL]){decisionHandler(WKNavigationActionPolicyCancel);[self eventsSettings];return;}if([self openPlayerURL:url]){decisionHandler(WKNavigationActionPolicyCancel);return;}if([self panel:url]){decisionHandler(WKNavigationActionPolicyAllow);return;}decisionHandler(WKNavigationActionPolicyCancel);if([self library:url]&&[self panel:self.web.URL])[self openEpisode:url];
}
- (WKWebView *)webView:(WKWebView *)web createWebViewWithConfiguration:(WKWebViewConfiguration *)config forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features{
 NSURL *url=action.request.URL;if([self openPlayerURL:url])return nil;if([self panel:url])[self.web loadRequest:action.request];else if([self library:url]&&[self panel:self.web.URL])[self openEpisode:url];return nil;
}
- (void)webView:(WKWebView *)web didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error{if(!self.switchingServer&&error.code!=NSURLErrorCancelled)[self notice:@"Сервер или сеть недоступны. Потяните страницу вниз для повторного подключения. Задания на мини-ПК сохранены." browser:nil];}
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)web{[web reload];}
/* Foreground presentation + validated tap routing. */
- (void)userNotificationCenter:(UNUserNotificationCenter *)center willPresentNotification:(UNNotification *)notification completionHandler:(void (^)(UNNotificationPresentationOptions))completionHandler{completionHandler(UNNotificationPresentationOptionBanner|UNNotificationPresentationOptionSound);}
- (void)userNotificationCenter:(UNUserNotificationCenter *)center didReceiveNotificationResponse:(UNNotificationResponse *)response completionHandler:(void (^)(void))completionHandler{
 [self loadViewIfNeeded];
 NSDictionary *info=response.notification.request.content.userInfo;
 if(!ValidInstance(info[@"instanceId"])||![info[@"instanceId"]isEqual:EventsDefaults()[@"instanceId"]]){completionHandler();return;}
 NSString *hash=EventsRoute([info[@"fp"]isKindOfClass:NSString.class]?info[@"fp"]:@"",Fingerprint(self.serverURL),[info[@"episodeId"]longLongValue],[info[@"section"]isKindOfClass:NSString.class]?info[@"section"]:nil];
 if(hash)[self applyRoute:hash];
 completionHandler();
}
@end
@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic,strong) UIWindow *window;
@end
@implementation AppDelegate
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options{
 [DubsEvents registerBackgroundHandler];
 /* One live PanelController is both the root view controller and the
    UNUserNotificationCenter delegate (window retains it for app lifetime). */
 PanelController *panel=[PanelController new];
 UNUserNotificationCenter.currentNotificationCenter.delegate=panel;
 [DubsEvents scheduleRefresh];
 self.window=[[UIWindow alloc]initWithFrame:UIScreen.mainScreen.bounds];self.window.rootViewController=panel;self.window.overrideUserInterfaceStyle=UIUserInterfaceStyleDark;[self.window makeKeyAndVisible];return YES;
}
- (void)applicationDidBecomeActive:(UIApplication *)application{[DubsEvents pollWithCompletion:nil];}
- (void)applicationDidEnterBackground:(UIApplication *)application{[DubsEvents scheduleRefresh];}
@end
int main(int argc,char *argv[]){@autoreleasepool{return UIApplicationMain(argc,argv,nil,NSStringFromClass(AppDelegate.class));}}
