#import <XCTest/XCTest.h>
@interface NotificationStateTests : XCTestCase
@end
/* Simulator UI proofs for the notification feature's honest state machine:
   default OFF with persistence, permission prompt only on explicit opt-in,
   honest registration failure without a logged-in session, and no system
   permission alert on plain launches. Routing math itself is covered by the
   plain-JVM policy tests; these cover what only the running app can show. */
@implementation NotificationStateTests
static XCUIApplication *springboard(void){return [[XCUIApplication alloc]initWithBundleIdentifier:@"com.apple.springboard"];}

/* Configure any server (example.org) so the app has a home and the events
   dialog becomes reachable; mirrors ServerSetupTests keyboard handling. */
- (void)configureServer:(XCUIApplication *)app{
 XCUIElement *field=app.textFields[@"server-address"];
 if(![field waitForExistenceWithTimeout:2]){
  if(app.alerts.buttons[@"ОК"].exists)[app.alerts.buttons[@"ОК"] tap];
  [app.buttons[@"server-settings"] tap];
 }
 XCTAssertTrue([field waitForExistenceWithTimeout:10]);
 [field tap];[field pressForDuration:1.1];
 NSString *text=[field.value isKindOfClass:NSString.class]?field.value:@"";
 NSMutableString *deletes=[NSMutableString new];for(NSUInteger i=0;i<text.length;i++)[deletes appendString:XCUIKeyboardKeyDelete];
 [field typeText:deletes];[field typeText:@"https://example.org/"];
 XCUIElement *connect=app.buttons[@"Подключиться"];
 XCTAssertTrue(connect.enabled);[connect tap];
 XCTAssertFalse([field waitForExistenceWithTimeout:5]);
 if(app.alerts.count){XCUIElement *ok=app.alerts.buttons[@"ОК"];if(ok.exists)[ok tap];}
 [NSThread sleepForTimeInterval:1];
}

- (void)testEventsDialogShowsHonestDefaultOffAndPersists{
 XCUIApplication *app=[XCUIApplication new];[app launch];
 [self configureServer:app];
 XCUIElement *events=app.buttons[@"events-settings"];
 XCTAssertTrue([events waitForExistenceWithTimeout:10]);[events tap];
 XCUIElement *dialog=app.alerts[@"События и уведомления"];
 XCTAssertTrue([dialog waitForExistenceWithTimeout:5]);
 NSString *message=dialog.staticTexts.firstMatch.label;
 XCTAssertTrue([message containsString:@"выключены"],@"default state must be honest OFF, got: %@",message);
 XCTAssertTrue([app.alerts.buttons[@"Включить уведомления на устройстве"] exists]);
 XCTAssertTrue([app.alerts.buttons[@"Типы событий (аккаунт)"] exists]);
 XCTAssertTrue([app.alerts.buttons[@"Отключить и отозвать устройство"] exists]);
 [app.alerts.buttons[@"Закрыть"] tap];
 [app terminate];[app launch];
 XCUIElement *again=app.buttons[@"events-settings"];
 XCTAssertTrue([again waitForExistenceWithTimeout:10]);[again tap];
 XCTAssertTrue([app.alerts[@"События и уведомления"] waitForExistenceWithTimeout:5]);
 XCTAssertTrue([app.alerts.buttons[@"Включить уведомления на устройстве"] exists],@"state persists across launches");
 [app.alerts.buttons[@"Закрыть"] tap];
}
- (void)testEnableFlowAsksPermissionOnceAndFailsHonestWithoutSession{
 XCUIApplication *app=[XCUIApplication new];[app launch];
 [self configureServer:app];
 [app.buttons[@"events-settings"] tap];
 XCUIElement *enable=app.alerts.buttons[@"Включить уведомления на устройстве"];
 XCTAssertTrue([enable waitForExistenceWithTimeout:5]);[enable tap];
 // System permission alert is opt-in: grant it via SpringBoard if shown.
 XCUIElement *allow=springboard().buttons[@"Allow"];
 if(![allow waitForExistenceWithTimeout:5])allow=springboard().buttons[@"Разрешить"];
 if(allow.exists)[allow tap];
 XCUIElement *notice=[[app.alerts.staticTexts matchingPredicate:
  [NSPredicate predicateWithFormat:@"label CONTAINS 'не удалась' OR label CONTAINS 'Откройте панель' OR label CONTAINS 'не разрешены'"]] firstMatch];
 XCTAssertTrue([notice waitForExistenceWithTimeout:15],@"expected honest failure notice without a logged-in session");
 if(app.alerts.buttons[@"ОК"].exists)[app.alerts.buttons[@"ОК"] tap];
 // A granted-but-failed registration must not leave the feature enabled.
 [app.buttons[@"events-settings"] tap];
 XCTAssertTrue([app.alerts.buttons[@"Включить уведомления на устройстве"] waitForExistenceWithTimeout:5]);
 [app.alerts.buttons[@"Закрыть"] tap];
}
- (void)testNoPermissionAlertOnPlainLaunch{
 XCUIApplication *app=[XCUIApplication new];[app launch];
 if([app.textFields[@"server-address"] waitForExistenceWithTimeout:5]){
  XCUIElement *connect=app.buttons[@"Подключиться"];
  if(connect.exists)[app.alerts.buttons.firstMatch tap];
 }
 [NSThread sleepForTimeInterval:2];
 // Permission alerts surface through SpringBoard, not the app process.
 XCUIElement *permission=[[springboard().alerts matchingPredicate:
  [NSPredicate predicateWithFormat:@"label CONTAINS 'Dubs'"]] firstMatch];
 XCTAssertFalse([permission waitForExistenceWithTimeout:3],@"system notification permission alert must never appear without explicit opt-in");
}
@end
