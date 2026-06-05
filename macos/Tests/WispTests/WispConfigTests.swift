import XCTest
@testable import Wisp

final class WispConfigTests: XCTestCase {

    func testDefaultCallersMatchWindowsIntentShape() {
        let config = WispConfig.load(environment: [:], readDotEnv: false)

        XCTAssertEqual(config.callers.count, 2)
        XCTAssertEqual(config.callers[0].hotkey, "ctrl+q")
        XCTAssertEqual(config.callers[0].label, "General")
        XCTAssertFalse(config.callers[0].pasteBack)
        XCTAssertEqual(config.callers[0].customKey, "s")
        XCTAssertEqual(config.callers[0].intents.map(\.key), ["w", "a", "d"])
        XCTAssertEqual(config.callers[1].label, "Rewrite & Paste")
        XCTAssertTrue(config.callers[1].pasteBack)
    }

    func testCallerEnvironmentOverridesUseSameKeysAsPythonSettings() {
        let config = WispConfig.load(environment: [
            "CALLER_COUNT": "1",
            "CALLER_1_HOTKEY": "ctrl+option+space",
            "CALLER_1_LABEL": "Research",
            "CALLER_1_PASTE_BACK": "true",
            "CALLER_1_CUSTOM_KEY": "x",
            "CALLER_1_CONTEXT_AMBIENT": "false",
            "CALLER_1_CONTEXT_DOCUMENTS": "false",
            "CALLER_1_CONTEXT_TOOLS": "false",
            "CALLER_1_CONTEXT_SCREENSHOT": "model",
            "CALLER_1_CONTEXT_CLIPBOARD": "true",
            "CALLER_1_INTENT_COUNT": "2",
            "CALLER_1_INTENT_1_KEY": "r",
            "CALLER_1_INTENT_1_LABEL": "Review",
            "CALLER_1_INTENT_1_HINT": "Find risks",
            "CALLER_1_INTENT_1_PROMPT": "Review this.",
            "CALLER_1_INTENT_2_KEY": "f",
            "CALLER_1_INTENT_2_LABEL": "Fix",
            "CALLER_1_INTENT_2_PROMPT": "Fix this.",
        ], readDotEnv: false)

        XCTAssertEqual(config.callers.count, 1)
        let caller = try XCTUnwrap(config.callers.first)
        XCTAssertEqual(caller.hotkey, "ctrl+option+space")
        XCTAssertEqual(caller.label, "Research")
        XCTAssertTrue(caller.pasteBack)
        XCTAssertEqual(caller.customKey, "x")
        XCTAssertFalse(caller.contextAmbient)
        XCTAssertFalse(caller.contextDocuments)
        XCTAssertFalse(caller.contextTools)
        XCTAssertEqual(caller.contextScreenshot, .model)
        XCTAssertTrue(caller.contextClipboard)
        XCTAssertEqual(caller.intents.map(\.key), ["r", "f"])
        XCTAssertEqual(caller.intents[0].hint, "Find risks")
        XCTAssertEqual(caller.intents[1].prompt, "Fix this.")
    }

    func testScreenshotModeAcceptsLegacyBooleanValues() {
        XCTAssertEqual(ScreenshotMode.normalized("true"), .auto)
        XCTAssertEqual(ScreenshotMode.normalized("tool"), .model)
        XCTAssertEqual(ScreenshotMode.normalized("no"), .off)
        XCTAssertEqual(ScreenshotMode.normalized("???", default: .model), .model)
    }
}
