#import <XCTest/XCTest.h>
@interface ServerSetupTests : XCTestCase
@end
@implementation ServerSetupTests
- (void)testFirstStartValidationPersistenceAndChange {
 XCUIApplication *app=[XCUIApplication new];[app launch];
 XCUIElement *field=app.textFields[@"server-address"];
 if(![field waitForExistenceWithTimeout:2]){
  if(app.alerts.buttons[@"ОК"].exists)[app.alerts.buttons[@"ОК"] tap];
  [app.buttons[@"server-settings"] tap];
 }
 XCTAssertTrue([field waitForExistenceWithTimeout:10]);
 XCUIElement *connect=app.buttons[@"Подключиться"];
 [field tap];NSString *previous=field.value;NSMutableString *clear=[NSMutableString new];for(NSUInteger i=0;i<previous.length;i++)[clear appendString:XCUIKeyboardKeyDelete];[field typeText:clear];[field typeText:@"http://example.org"];XCTAssertFalse(connect.enabled);
 [field pressForDuration:1.1];
 // Clear through accessibility typing, independent of keyboard language/menu.
 NSString *text=field.value;NSMutableString *deletes=[NSMutableString new];for(NSUInteger i=0;i<text.length;i++)[deletes appendString:XCUIKeyboardKeyDelete];[field typeText:deletes];[field typeText:@"https://example.org:443/"];XCTAssertTrue(connect.enabled);[connect tap];
 [app terminate];[app launch];
 XCTAssertFalse([app.textFields[@"server-address"] waitForExistenceWithTimeout:2]);
 if(app.alerts.count)[app.alerts.buttons[@"ОК"] tap];
 [app.buttons[@"server-settings"] tap];XCTAssertTrue([app.textFields[@"server-address"] waitForExistenceWithTimeout:5]);XCTAssertEqualObjects(app.textFields[@"server-address"].value,@"https://example.org/");
 [app.buttons[@"Отмена"] tap];
}
@end
