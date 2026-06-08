import XCTest
import Foundation

final class MemoryPanelTests: XCTestCase {

    func testMemoryInputsUseReadableAdaptiveSystemColors() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/MemoryUI/MemoryPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "private enum MemoryInputPalette",
            "NSColor.textColor",
            "NSColor.textBackgroundColor",
            ".foregroundStyle(MemoryInputPalette.inputText)",
            ".tint(MemoryInputPalette.inputText)",
        ] {
            XCTAssertTrue(source.contains(expected), "Memory input contrast is missing \(expected).")
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
