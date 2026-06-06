import XCTest
import Foundation

final class VisualContrastTests: XCTestCase {

    func testCustomDarkSurfacesDoNotUseAdaptivePrimaryOrSecondaryText() throws {
        for path in [
            "Sources/Wisp/Chat/ChatPanel.swift",
            "Sources/Wisp/Input/IntentPanel.swift",
        ] {
            let source = try readSource(path)
            XCTAssertFalse(
                source.contains(".foregroundStyle(.primary)"),
                "\(path) has a custom dark surface; use explicit readable colors instead of .primary."
            )
            XCTAssertFalse(
                source.contains(".foregroundStyle(.secondary)"),
                "\(path) has a custom dark surface; use explicit readable colors instead of .secondary."
            )
        }
    }

    private func readSource(_ relativePath: String) throws -> String {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let url = sourceRoot(from: currentDirectory)
            .appendingPathComponent(relativePath)
        return try String(contentsOf: url, encoding: .utf8)
    }

    private func sourceRoot(from currentDirectory: URL) -> URL {
        let direct = currentDirectory.appendingPathComponent("Sources/Wisp")
        if FileManager.default.fileExists(atPath: direct.path) {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }
}
