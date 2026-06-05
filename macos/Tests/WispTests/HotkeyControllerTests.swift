import ApplicationServices
import XCTest
@testable import Wisp

final class HotkeyControllerTests: XCTestCase {

    func testParsesCallerHotkeyString() throws {
        let hotkey = try XCTUnwrap(
            HotkeyDefinition.parse("ctrl+shift+q", callerIndex: 1, label: "Rewrite")
        )

        XCTAssertEqual(hotkey.callerIndex, 1)
        XCTAssertEqual(hotkey.keyCode, 12)
        XCTAssertTrue(hotkey.modifiers.contains(.maskControl))
        XCTAssertTrue(hotkey.modifiers.contains(.maskShift))
        XCTAssertFalse(hotkey.modifiers.contains(.maskAlternate))
    }

    func testParsesOptionAliasAndNamedKeys() throws {
        let hotkey = try XCTUnwrap(
            HotkeyDefinition.parse("control+option+space", callerIndex: 0)
        )

        XCTAssertEqual(hotkey.keyCode, 49)
        XCTAssertTrue(hotkey.modifiers.contains(.maskControl))
        XCTAssertTrue(hotkey.modifiers.contains(.maskAlternate))
    }

    func testMatchesExactModifierSet() throws {
        let general = try XCTUnwrap(HotkeyDefinition.parse("ctrl+q", callerIndex: 0))
        let rewrite = try XCTUnwrap(HotkeyDefinition.parse("ctrl+shift+q", callerIndex: 1))

        XCTAssertTrue(general.matches(keyCode: 12, flags: [.maskControl]))
        XCTAssertFalse(general.matches(keyCode: 12, flags: [.maskControl, .maskShift]))
        XCTAssertTrue(rewrite.matches(keyCode: 12, flags: [.maskControl, .maskShift]))
    }

    func testRejectsInvalidHotkey() {
        XCTAssertNil(HotkeyDefinition.parse("ctrl+unknown-key", callerIndex: 0))
        XCTAssertNil(HotkeyDefinition.parse("ctrl+q+w", callerIndex: 0))
        XCTAssertNil(HotkeyDefinition.parse("", callerIndex: 0))
    }
}
