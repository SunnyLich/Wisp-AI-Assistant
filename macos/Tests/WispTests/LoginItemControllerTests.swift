import XCTest
@testable import Wisp

final class LoginItemControllerTests: XCTestCase {

    func testLoginItemStatusMenuTitles() {
        XCTAssertEqual(LoginItemStatus.enabled.menuTitle, "Launch at Login: on")
        XCTAssertEqual(LoginItemStatus.notRegistered.menuTitle, "Launch at Login: off")
        XCTAssertEqual(LoginItemStatus.requiresApproval.menuTitle, "Launch at Login: approval needed")
        XCTAssertEqual(LoginItemStatus.unavailable.menuTitle, "Launch at Login: unavailable")
        XCTAssertEqual(LoginItemStatus.unknown.menuTitle, "Launch at Login: unknown")
    }

    func testLoginItemStatusCheckedState() {
        XCTAssertTrue(LoginItemStatus.enabled.isChecked)
        XCTAssertFalse(LoginItemStatus.notRegistered.isChecked)
        XCTAssertFalse(LoginItemStatus.requiresApproval.isChecked)
        XCTAssertFalse(LoginItemStatus.unavailable.isChecked)
        XCTAssertFalse(LoginItemStatus.unknown.isChecked)
    }

    func testLoginItemToggleIntent() {
        XCTAssertFalse(LoginItemStatus.enabled.shouldRegisterOnToggle)
        XCTAssertTrue(LoginItemStatus.notRegistered.shouldRegisterOnToggle)
        XCTAssertTrue(LoginItemStatus.requiresApproval.shouldRegisterOnToggle)
        XCTAssertFalse(LoginItemStatus.unavailable.shouldRegisterOnToggle)
        XCTAssertFalse(LoginItemStatus.unknown.shouldRegisterOnToggle)
    }

    func testLoginItemActionableStates() {
        XCTAssertTrue(LoginItemStatus.enabled.isActionable)
        XCTAssertTrue(LoginItemStatus.notRegistered.isActionable)
        XCTAssertTrue(LoginItemStatus.requiresApproval.isActionable)
        XCTAssertFalse(LoginItemStatus.unavailable.isActionable)
        XCTAssertFalse(LoginItemStatus.unknown.isActionable)
    }
}
