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
        XCTAssertEqual(config.snip.hotkey, "ctrl+alt+q")
        XCTAssertTrue(config.snip.contextAmbient)
        XCTAssertFalse(config.snip.contextDocuments)
        XCTAssertFalse(config.snip.contextTools)
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

    func testSnipEnvironmentOverridesUseSameKeysAsPythonSettings() {
        let config = WispConfig.load(environment: [
            "HOTKEY_SNIP": "ctrl+option+4",
            "SNIP_CONTEXT_AMBIENT": "false",
            "SNIP_CONTEXT_DOCUMENTS": "true",
            "SNIP_CONTEXT_TOOLS": "true",
        ], readDotEnv: false)

        XCTAssertEqual(config.snip.hotkey, "ctrl+option+4")
        XCTAssertFalse(config.snip.contextAmbient)
        XCTAssertTrue(config.snip.contextDocuments)
        XCTAssertTrue(config.snip.contextTools)
    }

    func testScreenshotModeAcceptsLegacyBooleanValues() {
        XCTAssertEqual(ScreenshotMode.normalized("true"), .auto)
        XCTAssertEqual(ScreenshotMode.normalized("tool"), .model)
        XCTAssertEqual(ScreenshotMode.normalized("no"), .off)
        XCTAssertEqual(ScreenshotMode.normalized("???", default: .model), .model)
    }

    func testDotEnvRenderUpdatesAndRemovesCallerRows() {
        let original = """
        # keep comments
        LLM_PROVIDER=chatgpt
        CALLER_COUNT=2
        CALLER_1_LABEL=Old
        CALLER_2_LABEL=Stale
        TTS_PROVIDER=none
        """

        let rendered = DotEnvFile.renderUpdating(
            original,
            updates: [
                "LLM_PROVIDER": "openai",
                "CALLER_COUNT": "1",
                "CALLER_1_LABEL": "General",
                "CALLER_1_INTENT_1_PROMPT": "Explain this clearly.",
            ],
            removingPrefixes: ["CALLER_"]
        )

        XCTAssertTrue(rendered.contains("# keep comments"))
        XCTAssertTrue(rendered.contains("LLM_PROVIDER=openai"))
        XCTAssertTrue(rendered.contains("TTS_PROVIDER=none"))
        XCTAssertTrue(rendered.contains("CALLER_COUNT=1"))
        XCTAssertTrue(rendered.contains("CALLER_1_LABEL=General"))
        XCTAssertTrue(rendered.contains("CALLER_1_INTENT_1_PROMPT=\"Explain this clearly.\""))
        XCTAssertFalse(rendered.contains("CALLER_2_LABEL"))
        XCTAssertEqual(DotEnvFile.readValues(fromText: rendered)["CALLER_1_LABEL"], "General")
    }

    func testSettingsDraftSerializesCallerContract() {
        var draft = SettingsDraft.empty
        draft.llmProvider = "anthropic"
        draft.llmModel = "claude-sonnet-4-5"
        draft.callers = [
            SettingsCallerDraft(
                hotkey: "ctrl+option+space",
                label: "Research",
                pasteBack: true,
                customKey: "x",
                contextAmbient: false,
                contextDocuments: true,
                contextTools: false,
                contextScreenshot: .model,
                contextClipboard: true,
                intents: [
                    SettingsIntentDraft(
                        key: "r",
                        label: "Review",
                        hint: "Find risks",
                        prompt: "Review this."
                    )
                ]
            )
        ]

        let values = draft.envValues()

        XCTAssertEqual(values["LLM_PROVIDER"], "anthropic")
        XCTAssertEqual(values["LLM_MODEL"], "claude-sonnet-4-5")
        XCTAssertEqual(values["CALLER_COUNT"], "1")
        XCTAssertEqual(values["CALLER_1_LABEL"], "Research")
        XCTAssertEqual(values["CALLER_1_PASTE_BACK"], "true")
        XCTAssertEqual(values["CALLER_1_CONTEXT_SCREENSHOT"], "model")
        XCTAssertEqual(values["CALLER_1_CONTEXT_CLIPBOARD"], "true")
        XCTAssertEqual(values["CALLER_1_INTENT_COUNT"], "1")
        XCTAssertEqual(values["CALLER_1_INTENT_1_KEY"], "r")
        XCTAssertEqual(values["CALLER_1_INTENT_1_PROMPT"], "Review this.")
        XCTAssertEqual(values["HOTKEY_SNIP"], "ctrl+alt+q")
        XCTAssertEqual(values["SNIP_CONTEXT_AMBIENT"], "true")
        XCTAssertEqual(values["SNIP_CONTEXT_DOCUMENTS"], "false")
        XCTAssertEqual(values["SNIP_CONTEXT_TOOLS"], "false")
    }
}
