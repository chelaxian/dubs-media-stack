#import <XCTest/XCTest.h>
@interface NotificationStateTests : XCTestCase
@end
/* Simulator UI proofs for the notification feature's honest state machine:
   default OFF with persistence, permission prompt only on explicit opt-in,
   honest registration failure without a logged-in session, and no system
   permission alert on plain launches. Routing math itself is covered by the
   plain-JVM policy tests; these cover what only the running app can show. */
@implementation NotificationStateTests
- (void)setUp{
 [super setUp];
 self.continueAfterFailure=NO;
}

- (void)replaceTextInField:(XCUIElement *)field withText:(NSString *)replacement {
 [field tap];
 NSString *text=[field.value isKindOfClass:NSString.class]?field.value:@"";
 if([text isEqualToString:field.placeholderValue])text=@"";
 // Fixtures are bounded. Tap the trailing edge to move to the visible end,
 // then read back after deleting; repeat if a scrolled field left a suffix.
 XCTAssertLessThanOrEqual(text.length,128u,@"Unexpected server fixture: %@",text);
 if(text.length>128)return;
 for(NSUInteger attempt=0;text.length&&attempt<129;attempt++){
  [[field coordinateWithNormalizedOffset:CGVectorMake(0.95,0.5)] tap];
  NSMutableString *deletes=[NSMutableString new];
  for(NSUInteger i=0;i<text.length;i++)[deletes appendString:XCUIKeyboardKeyDelete];
  [field typeText:deletes];
  NSString *remaining=field.value;
  if([remaining isEqualToString:field.placeholderValue])remaining=@"";
  XCTAssertLessThan(remaining.length,text.length,@"Deletion did not reduce field value: %@",remaining);
  if(remaining.length>=text.length)return;
  text=remaining;
 }
 XCTAssertEqualObjects(text,@"",@"Field must be empty before replacement");
 [field typeText:replacement];
 NSPredicate *matches=[NSPredicate predicateWithFormat:@"value == %@",replacement];
 [self expectationForPredicate:matches evaluatedWithObject:field handler:nil];
 [self waitForExpectationsWithTimeout:5 handler:nil];
 XCTAssertEqualObjects(field.value,replacement);
}
- (void)waitForConnect:(XCUIElement *)connect enabled:(BOOL)enabled {
 [self expectationForPredicate:[NSPredicate predicateWithFormat:@"enabled == %@",@(enabled)] evaluatedWithObject:connect handler:nil];
 [self waitForExpectationsWithTimeout:5 handler:nil];
 XCTAssertEqual(connect.enabled,enabled);
}

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
 NSString *normalized=@"https://example.org/";
 [self replaceTextInField:field withText:normalized];
 XCUIElement *connect=app.buttons[@"Подключиться"];
 [self waitForConnect:connect enabled:YES];[connect tap];
 [self expectationForPredicate:[NSPredicate predicateWithFormat:@"exists == NO"] evaluatedWithObject:field handler:nil];
 [self waitForExpectationsWithTimeout:5 handler:nil];
 // Wait for the real selected server after asynchronous website-data removal
 // and preference commit before opening notification settings.
 XCUIElement *settings=app.buttons[@"server-settings"];
 [self expectationForPredicate:[NSPredicate predicateWithFormat:@"value == %@",normalized] evaluatedWithObject:settings handler:nil];
 [self waitForExpectationsWithTimeout:60 handler:nil];
 XCTAssertEqualObjects(settings.value,normalized,@"Server must be selected before notification actions");
 if(app.alerts.count){XCUIElement *ok=app.alerts.buttons[@"ОК"];if(ok.exists)[ok tap];}
}

- (void)testEventsDialogShowsHonestDefaultOffAndPersists{
 XCUIApplication *app=[XCUIApplication new];[app launch];
 [self configureServer:app];
 XCUIElement *events=app.buttons[@"events-settings"];
 XCTAssertTrue([events waitForExistenceWithTimeout:10]);[events tap];
 XCUIElement *dialog=app.alerts[@"События и уведомления"];
 XCTAssertTrue([dialog waitForExistenceWithTimeout:5]);
 NSPredicate *statusMessage=[NSPredicate predicateWithFormat:@"label CONTAINS %@",@"Уведомления на этом устройстве"];
 XCUIElement *messageElement=[[dialog.staticTexts matchingPredicate:statusMessage] firstMatch];
 XCTAssertTrue([messageElement waitForExistenceWithTimeout:5],@"expected notification status message");
 NSString *message=messageElement.label;
 XCTAssertTrue([message containsString:@"выключены"],@"default state must be honest OFF, got: %@",message);
 XCTAssertTrue([app.alerts.buttons[@"Включить уведомления на устройстве"] exists]);
 XCTAssertTrue([app.alerts.buttons[@"Типы событий (аккаунт)"] exists]);
 XCTAssertTrue([app.alerts.buttons[@"Отключить и отозвать устройство"] exists]);
 [app.alerts.buttons[@"Закрыть"] tap];
 [app terminate];[app launch];
 XCUIElement *again=app.buttons[@"events-settings"];
 XCTAssertTrue([again waitForExistenceWithTimeout:10]);[again tap];
 XCTAssertTrue([app.alerts[@"События и уведомления"] waitForExistenceWithTimeout:5]);
 XCUIElement *persistedMessage=[[app.alerts[@"События и уведомления"].staticTexts matchingPredicate:statusMessage] firstMatch];
 XCTAssertTrue([persistedMessage waitForExistenceWithTimeout:5],@"expected notification status message after relaunch");
 XCTAssertTrue([persistedMessage.label containsString:@"выключены"],@"state must persist as OFF across launches, got: %@",persistedMessage.label);
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
