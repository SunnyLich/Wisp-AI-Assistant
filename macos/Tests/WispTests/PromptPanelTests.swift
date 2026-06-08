import XCTest
import Foundation

final class PromptPanelTests: XCTestCase {

    func testPromptPanelInputAndResponseUseReadableAdaptiveSystemColors() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Input/PromptPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "private enum PromptPanelPalette",
            "NSColor.textColor",
            ".foregroundStyle(PromptPanelPalette.inputText)",
            ".tint(PromptPanelPalette.inputText)",
        ] {
            XCTAssertTrue(source.contains(expected), "PromptPanel contrast is missing \(expected).")
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
}
