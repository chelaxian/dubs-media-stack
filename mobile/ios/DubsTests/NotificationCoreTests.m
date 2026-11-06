#import <XCTest/XCTest.h>
#import <WebKit/WebKit.h>
@interface DubsRefreshCompletion : NSObject
@property(nonatomic,copy)void (^done)(BOOL);
@property(nonatomic,copy)void (^cancel)(void);
- (void)finish:(BOOL)ok;
- (void)expire;
@end
@interface DubsEvents : NSObject
+ (NSString *)registrationScript;
+ (BOOL)consume:(NSDictionary *)json state:(NSDictionary *)state;
@end
@interface NotificationCoreTests : XCTestCase <WKNavigationDelegate>
@property(nonatomic,strong)WKWebView *web;
@property(nonatomic,strong)XCTestExpectation *registered;
@end
@implementation NotificationCoreTests
- (void)tearDown{[NSUserDefaults.standardUserDefaults removeObjectForKey:@"dubs.events.state"];[super tearDown];}
- (void)testExpirationCancelsOnceAndCompletesOnce{
 DubsRefreshCompletion *gate=[DubsRefreshCompletion new];
 __block NSUInteger completions=0,cancellations=0;
 gate.done=^(BOOL ok){XCTAssertFalse(ok);++completions;};
 __weak DubsRefreshCompletion *weakGate=gate;
 gate.cancel=^{++cancellations;[weakGate finish:YES];};
 [gate expire];[gate finish:YES];[gate expire];
 XCTAssertEqual(completions,1u);XCTAssertEqual(cancellations,1u);
}
- (void)testSuccessfulCompletionSuppressesLateExpiration{
 DubsRefreshCompletion *gate=[DubsRefreshCompletion new];
 __block NSUInteger completions=0,cancellations=0;
 gate.done=^(BOOL ok){XCTAssertTrue(ok);++completions;};gate.cancel=^{++cancellations;};
 [gate finish:YES];[gate expire];[gate finish:NO];
 XCTAssertEqual(completions,1u);XCTAssertEqual(cancellations,0u);
}
- (NSDictionary *)state{
 NSDictionary *state=@{@"enabled":@YES,@"origin":@"https://panel.example.org/",@"fp":@"fixture",@"instanceId":@"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",@"cursor":@1};
 [NSUserDefaults.standardUserDefaults setObject:state forKey:@"dubs.events.state"];return state;
}
- (NSDictionary *)page:(NSString *)epoch cursor:(NSNumber *)cursor{
 return @{@"instanceId":epoch,@"cursor":cursor,@"settings":@{@"enabled":@NO},@"events":@[]};
}
- (void)testOpaqueEpochResetAndCursorAdvance{
 NSDictionary *state=[self state];
 XCTAssertTrue([DubsEvents consume:[self page:@"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" cursor:@8] state:state]);
 NSDictionary *current=[NSUserDefaults.standardUserDefaults dictionaryForKey:@"dubs.events.state"];
 XCTAssertEqualObjects(current[@"instanceId"],@"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");XCTAssertEqualObjects(current[@"cursor"],@8);
}
- (void)testDisabledOrChangedOriginRejectsInflightPage{
 NSDictionary *state=[self state];
 [NSUserDefaults.standardUserDefaults removeObjectForKey:@"dubs.events.state"];
 XCTAssertFalse([DubsEvents consume:[self page:state[@"instanceId"] cursor:@9] state:state]);
 XCTAssertNil([NSUserDefaults.standardUserDefaults dictionaryForKey:@"dubs.events.state"]);
 NSMutableDictionary *changed=[state mutableCopy];changed[@"origin"]=@"https://other.example.org/";
 [NSUserDefaults.standardUserDefaults setObject:changed forKey:@"dubs.events.state"];
 XCTAssertFalse([DubsEvents consume:[self page:state[@"instanceId"] cursor:@9] state:state]);
}
- (void)testMalformedEpochAndRegressingCursorFailClosed{
 NSDictionary *state=[self state];
 XCTAssertFalse([DubsEvents consume:[self page:@"7" cursor:@9] state:state]);
 XCTAssertFalse([DubsEvents consume:[self page:state[@"instanceId"] cursor:@YES] state:state]);
 XCTAssertFalse([DubsEvents consume:[self page:state[@"instanceId"] cursor:@1.5] state:state]);
 XCTAssertFalse([DubsEvents consume:[self page:state[@"instanceId"] cursor:@0] state:state]);
 XCTAssertEqualObjects([NSUserDefaults.standardUserDefaults dictionaryForKey:@"dubs.events.state"][@"cursor"],@1);
}
- (void)testRealWKAsyncRegistrationAwaitsJSON{
 self.registered=[self expectationWithDescription:@"awaited registration JSON"];
 self.web=[[WKWebView alloc]initWithFrame:CGRectZero];self.web.navigationDelegate=self;
 [self.web loadHTMLString:@"<script>window.fetch=url=>Promise.resolve({ok:true,json:()=>new Promise(resolve=>setTimeout(()=>resolve(url==='/api/session'?{csrf:'fixture'}:{token:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',deviceId:'fixture',cursor:9,instanceId:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}),20))});</script>" baseURL:[NSURL URLWithString:@"https://panel.example.org/"]];
 [self waitForExpectationsWithTimeout:15 handler:nil];self.web.navigationDelegate=nil;self.web=nil;
}
- (void)webView:(WKWebView *)web didFinishNavigation:(WKNavigation *)navigation{
 [web callAsyncJavaScript:[DubsEvents registrationScript] arguments:@{} inFrame:nil inContentWorld:WKContentWorld.pageWorld completionHandler:^(id value,NSError *error){
  XCTAssertNil(error);XCTAssertTrue([value isKindOfClass:NSString.class]);
  if([value isKindOfClass:NSString.class]){NSDictionary *data=[NSJSONSerialization JSONObjectWithData:[value dataUsingEncoding:NSUTF8StringEncoding]options:0 error:nil];XCTAssertEqualObjects(data[@"instanceId"],@"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");XCTAssertEqualObjects(data[@"cursor"],@9);}
  [self.registered fulfill];
 }];
}
@end
