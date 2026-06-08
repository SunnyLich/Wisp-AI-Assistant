import XCTest
import Foundation

final class SettingsWiringTests: XCTestCase {

    func testSettingsPanelUsesSharedSettingsTabMap() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/SettingsUI/SettingsPanel.swift"),
            encoding: .utf8
        )

        assertContainsInOrder(
            [
                ".tabItem { Text(\"LLM\") }",
                ".tabItem { Text(\"TTS / Voice\") }",
                ".tabItem { Text(\"Prompts\") }",
                ".tabItem { Text(\"Keybinds\") }",
                ".tabItem { Text(\"App\") }",
                ".tabItem { Text(\"Memory\") }",
                ".tabItem { Text(\"Tools\") }",
            ],
            in: source
        )
    }

    func testSettingsPanelKeepsSharedControlsAndFooter() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/SettingsUI/SettingsPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "SettingsSection(\"Authentication\")",
            "SettingsSection(\"API Keys\")",
            "SettingsSection(\"Main\")",
            "SettingsSection(\"Vision\")",
            "SettingsSection(\"Memory Model\")",
            "SettingsTextField(\"Fallbacks\", text: $model.draft.llmFallbacks)",
            "SettingsTextField(\"Fallbacks\", text: $model.draft.visionFallbacks)",
            "SettingsTextField(\"Fallbacks\", text: $model.draft.memoryFallbacks)",
            "llmTestRow(.main)",
            "llmTestRow(.vision)",
            "llmTestRow(.memory)",
            "func fallbacks(in draft: SettingsDraft) -> String",
            "return draft.llmFallbacks",
            "return draft.visionFallbacks",
            "return draft.memoryFallbacks",
            "SettingsSection(\"System Prompt\")",
            "SettingsSection(\"Context Hotkeys\")",
            "SettingsSection(\"Tool Calling\")",
            "Text(\"Reset All...\")",
            "Text(\"Cancel\")",
            "Text(\"Apply\")",
            ".help(\"Reset all settings\")",
            "model.resetAll()",
        ] {
            XCTAssertTrue(source.contains(expected), "SettingsPanel is missing \(expected).")
        }
    }

    func testSettingsPanelModelKeepsNativeCredentialCallbacks() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/SettingsUI/SettingsPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "onRefreshSecrets",
            "onSaveSecret",
            "onClearSecret",
            "onRefreshAuth",
            "onStartChatGPTLogin",
            "onStartGitHubLogin",
            "onClearAuthProvider",
            "onSaveCopilotToken",
            "onTestCopilotToken",
            "onResetAll",
        ] {
            XCTAssertTrue(source.contains(expected), "SettingsPanel is missing callback \(expected).")
        }
    }

    func testSettingsInputsUseReadableAdaptiveSystemColors() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/SettingsUI/SettingsPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "private enum SettingsInputPalette",
            "NSColor.textColor",
            "NSColor.textBackgroundColor",
            ".foregroundStyle(SettingsInputPalette.inputText)",
            ".tint(SettingsInputPalette.inputText)",
            ".scrollContentBackground(.hidden)",
            ".background(SettingsInputPalette.inputBackground)",
            "SecureField(\"New API key\", text: $secret.value)",
            "SecureField(\"GitHub Copilot token\", text: $model.copilotToken)",
        ] {
            XCTAssertTrue(source.contains(expected), "Settings input contrast is missing \(expected).")
        }
    }

    func testAppDelegateWiresSettingsToBrainHandlers() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "brain.secrets.status",
            "brain.secrets.set",
            "brain.secrets.clear",
            "brain.auth.status",
            "brain.auth.chatgpt.browser_login",
            "brain.auth.github.device_login",
            "brain.auth.chatgpt.clear",
            "brain.auth.github.clear",
            "brain.auth.copilot.clear",
            "brain.auth.copilot.set",
            "brain.auth.copilot.test",
            "brain.settings.reset_credentials",
            "\"fallbacks\": route.fallbacks(in: draft)",
            "confirmResetAllSettings()",
            "FileManager.default.removeItem(at: dotEnv)",
            "reloadBrainConfig()",
        ] {
            XCTAssertTrue(source.contains(expected), "AppDelegate is missing Settings brain wiring for \(expected).")
        }
    }

    private func sourceRoot() -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let direct = currentDirectory.appendingPathComponent("Sources/Wisp")
        if FileManager.default.fileExists(atPath: direct.path) {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }

    private func assertContainsInOrder(
        _ needles: [String],
        in source: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        var searchStart = source.startIndex
        for needle in needles {
            guard let range = source.range(of: needle, range: searchStart..<source.endIndex) else {
                XCTFail("Missing or out-of-order settings marker: \(needle)", file: file, line: line)
                return
            }
            searchStart = range.upperBound
        }
    }
}
