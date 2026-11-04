#import <UIKit/UIKit.h>
#import <WebKit/WebKit.h>
@interface PanelController : UIViewController <WKNavigationDelegate,WKUIDelegate>
@property(nonatomic,strong) WKWebView *web;
@property(nonatomic,strong) NSURL *serverURL;
@property(nonatomic,assign) BOOL switchingServer;
@end
@implementation PanelController
- (UIStatusBarStyle)preferredStatusBarStyle{return UIStatusBarStyleLightContent;}
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
 [a addAction:[UIAlertAction actionWithTitle:@"Подключиться" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action){NSURL *next=[self normalizedServer:a.textFields.firstObject.text];if(!next){dispatch_async(dispatch_get_main_queue(),^{[self serverSettings];});return;}if([next isEqual:self.home]){[self.web loadRequest:[NSURLRequest requestWithURL:next]];return;}self.switchingServer=YES;[self.web stopLoading];[self.web loadHTMLString:@"" baseURL:nil];NSSet *types=WKWebsiteDataStore.allWebsiteDataTypes;[WKWebsiteDataStore.defaultDataStore removeDataOfTypes:types modifiedSince:NSDate.distantPast completionHandler:^{self.serverURL=next;[NSUserDefaults.standardUserDefaults setObject:next.absoluteString forKey:@"panel_url"];self.switchingServer=NO;[self.web loadRequest:[NSURLRequest requestWithURL:next]];}];}]];
 a.actions.firstObject.enabled=self.home!=nil;
 if(self.home)[a addAction:[UIAlertAction actionWithTitle:@"Отмена" style:UIAlertActionStyleCancel handler:nil]];
 [self presentViewController:a animated:YES completion:nil];
}
- (void)viewDidAppear:(BOOL)animated{[super viewDidAppear:animated];if(!self.home)[self serverSettings];}
- (void)webView:(WKWebView *)web didFinishNavigation:(WKNavigation *)navigation{
 if(![self panel:web.URL])return;
 [web evaluateJavaScript:@"(()=>{const add=()=>{const s=document.querySelector('#settings');if(!s||document.getElementById('native-server-settings'))return;const a=document.createElement('a');a.id='native-server-settings';a.href='dubs-settings://server';a.className='quiet';a.textContent='Изменить адрес веб-панели';s.prepend(a)};add();if(!window.dubsServerObserver){window.dubsServerObserver=new MutationObserver(add);window.dubsServerObserver.observe(document.body,{childList:true,subtree:true})}})()" completionHandler:nil];
}
- (BOOL)validID:(NSString *)s{return s&&[s rangeOfString:@"^[a-fA-F0-9]{32}$" options:NSRegularExpressionSearch].location!=NSNotFound;}
- (void)viewDidLoad{
 [super viewDidLoad];self.view.backgroundColor=UIColor.blackColor;self.serverURL=[self normalizedServer:[NSUserDefaults.standardUserDefaults stringForKey:@"panel_url"]?:@""];
 WKWebViewConfiguration *config=[WKWebViewConfiguration new];config.websiteDataStore=WKWebsiteDataStore.defaultDataStore;config.preferences.javaScriptCanOpenWindowsAutomatically=NO;config.applicationNameForUserAgent=@"DubsNative/1.2 iOS";
 self.web=[[WKWebView alloc]initWithFrame:CGRectZero configuration:config];self.web.navigationDelegate=self;self.web.UIDelegate=self;self.web.allowsBackForwardNavigationGestures=YES;self.web.opaque=NO;self.web.backgroundColor=UIColor.blackColor;self.web.scrollView.backgroundColor=UIColor.blackColor;self.web.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:self.web];UILayoutGuide *safe=self.view.safeAreaLayoutGuide;
 [NSLayoutConstraint activateConstraints:@[[self.web.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor],[self.web.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor],[self.web.topAnchor constraintEqualToAnchor:safe.topAnchor],[self.web.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor]]];
 UIRefreshControl *refresh=[UIRefreshControl new];refresh.tintColor=UIColor.systemRedColor;[refresh addTarget:self action:@selector(reload:) forControlEvents:UIControlEventValueChanged];self.web.scrollView.refreshControl=refresh;if(self.home)[self.web loadRequest:[NSURLRequest requestWithURL:self.home]];
 UIButton *settings=[UIButton buttonWithType:UIButtonTypeSystem];[settings setTitle:@"⚙ Сервер" forState:UIControlStateNormal];settings.accessibilityLabel=@"Настройки адреса веб-панели";settings.accessibilityIdentifier=@"server-settings";[settings addTarget:self action:@selector(serverSettings) forControlEvents:UIControlEventTouchUpInside];settings.translatesAutoresizingMaskIntoConstraints=NO;[self.view addSubview:settings];[NSLayoutConstraint activateConstraints:@[[settings.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-8],[settings.topAnchor constraintEqualToAnchor:safe.topAnchor],[settings.heightAnchor constraintEqualToConstant:44],[settings.widthAnchor constraintEqualToConstant:100]]];settings.backgroundColor=[UIColor.blackColor colorWithAlphaComponent:0.85];settings.tintColor=UIColor.systemRedColor;
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
  PanelController *self=weakSelf;if(!self)return;
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
 NSURL *url=action.request.URL;if([url.absoluteString isEqual:@"dubs-settings://server"]&&[self panel:self.web.URL]){decisionHandler(WKNavigationActionPolicyCancel);[self serverSettings];return;}if([self openPlayerURL:url]){decisionHandler(WKNavigationActionPolicyCancel);return;}if([self panel:url]){decisionHandler(WKNavigationActionPolicyAllow);return;}decisionHandler(WKNavigationActionPolicyCancel);if([self library:url]&&[self panel:self.web.URL])[self openEpisode:url];
}
- (WKWebView *)webView:(WKWebView *)web createWebViewWithConfiguration:(WKWebViewConfiguration *)config forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features{
 NSURL *url=action.request.URL;if([self openPlayerURL:url])return nil;if([self panel:url])[self.web loadRequest:action.request];else if([self library:url]&&[self panel:self.web.URL])[self openEpisode:url];return nil;
}
- (void)webView:(WKWebView *)web didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error{if(!self.switchingServer&&error.code!=NSURLErrorCancelled)[self notice:@"Сервер или сеть недоступны. Потяните страницу вниз для повторного подключения. Задания на мини-ПК сохранены." browser:nil];}
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)web{[web reload];}
@end
@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic,strong) UIWindow *window;
@end
@implementation AppDelegate
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options{
 self.window=[[UIWindow alloc]initWithFrame:UIScreen.mainScreen.bounds];self.window.rootViewController=[PanelController new];self.window.overrideUserInterfaceStyle=UIUserInterfaceStyleDark;[self.window makeKeyAndVisible];return YES;
}
@end
int main(int argc,char *argv[]){@autoreleasepool{return UIApplicationMain(argc,argv,nil,NSStringFromClass(AppDelegate.class));}}
