import XCTest
import Foundation

final class VisualContrastTests: XCTestCase {

    func testCustomDarkSwiftUISurfacesDoNotUseAdaptivePrimaryOrSecondaryText() throws {
        for url in try swiftSourcesWithCustomDarkSurfaces() {
            let source = try String(contentsOf: url, encoding: .utf8)
            let path = sourceRoot().relativePath(to: url)
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

    private func swiftSourcesWithCustomDarkSurfaces() throws -> [URL] {
        let root = sourceRoot().appendingPathComponent("Sources/Wisp")
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        ) else {
            return []
        }

        return try enumerator.compactMap { item in
            guard let url = item as? URL, url.pathExtension == "swift" else { return nil }
            let source = try String(contentsOf: url, encoding: .utf8)
            return hasCustomDarkSurface(source) ? url : nil
        }
    }

    private func hasCustomDarkSurface(_ source: String) -> Bool {
        source.contains("calibratedWhite: 0.")
            || source.contains("calibratedRed: 0.")
            || source.contains("NSColor.black")
            || source.contains("Color.black")
    }

    private func sourceRoot() -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        return sourceRoot(from: currentDirectory)
    }

    private func sourceRoot(from currentDirectory: URL) -> URL {
        let direct = currentDirectory.appendingPathComponent("Sources/Wisp")
        if FileManager.default.fileExists(atPath: direct.path) {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }
}

private extension URL {
    func relativePath(to child: URL) -> String {
        let base = standardizedFileURL.path
        let target = child.standardizedFileURL.path
        guard target.hasPrefix(base) else { return target }
        return String(target.dropFirst(base.count + 1))
    }
}
