#import <XCTest/XCTest.h>
@interface ServerSetupTests : XCTestCase
@end
@implementation ServerSetupTests
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
- (void)testFirstStartValidationPersistenceAndChange {
 self.continueAfterFailure=NO;
 XCUIApplication *app=[XCUIApplication new];app.launchArguments=@[@"--dubs-settings-fixture"];[app launch];
 XCUIElement *field=app.textFields[@"server-address"];
 if(![field waitForExistenceWithTimeout:2]){
  if(app.alerts.buttons[@"ОК"].exists)[app.alerts.buttons[@"ОК"] tap];
  [app.links[@"Изменить адрес веб-панели"] tap];
 }
 XCTAssertTrue([field waitForExistenceWithTimeout:10]);
 // Use a different server from the persisted one, so old preferences cannot
 // satisfy the save/relaunch assertion when fresh input was never accepted.
 NSString *host=[field.value isEqualToString:@"https://example.net/"]?@"example.org":@"example.net";
 NSString *input=[NSString stringWithFormat:@"https://%@:443/",host];
 NSString *normalized=[NSString stringWithFormat:@"https://%@/",host];
 XCUIElement *connect=app.buttons[@"Подключиться"];
 [self replaceTextInField:field withText:@"http://example.org"];
 [self waitForConnect:connect enabled:NO];
 [self replaceTextInField:field withText:input];
 [self waitForConnect:connect enabled:YES];[connect tap];
 [self expectationForPredicate:[NSPredicate predicateWithFormat:@"exists == NO"] evaluatedWithObject:field handler:nil];
 [self waitForExpectationsWithTimeout:5 handler:nil];
 // Observe the selected server only after asynchronous website-data removal
 // and preference commit. Predicate evaluation performs no UI actions.
 XCUIElement *settings=app.links[@"Изменить адрес веб-панели"];
 XCTAssertTrue([settings waitForExistenceWithTimeout:60]);
 [settings tap];XCTAssertTrue([field waitForExistenceWithTimeout:5]);XCTAssertEqualObjects(field.value,normalized);
 [app.buttons[@"Отмена"] tap];
 [app terminate];[app launch];
 XCTAssertFalse([app.textFields[@"server-address"] waitForExistenceWithTimeout:2]);
 if(app.alerts.count)[app.alerts.buttons[@"ОК"] tap];
 [app.links[@"Изменить адрес веб-панели"] tap];XCTAssertTrue([field waitForExistenceWithTimeout:5]);XCTAssertEqualObjects(field.value,normalized);
 [app.buttons[@"Отмена"] tap];
}
@end
