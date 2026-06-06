import XCTest
import Foundation
@testable import Wisp

final class BrainLocatorTests: XCTestCase {

    func testEnvironmentBrainSettingsWinWhenNoBundledRuntimeExists() {
        let config = BrainLocator.resolve(
            environment: [
                "WISP_BRAIN_PYTHON": "/tmp/python",
                "WISP_BRAIN_DIR": "/tmp/brain",
                "WISP_REPO_ROOT": "/tmp/repo",
            ],
            currentDirectory: URL(fileURLWithPath: "/tmp/repo/macos"),
            resourceURL: nil,
            fileManager: .default
        )

        XCTAssertEqual(config.pythonExecutable.path, "/tmp/python")
        XCTAssertEqual(config.brainDirectory.path, "/tmp/brain")
        XCTAssertEqual(config.extraPythonPath.map(\.path), ["/tmp/repo"])
    }

    func testFinderLaunchedDevBundleInfersRepoBrainFromBundlePath() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-brain-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let resourceURL = root
            .appendingPathComponent("build/WispNative/Wisp.app/Contents/Resources")
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent(".venv/bin"),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("macos/brain"),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(at: resourceURL, withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(
            atPath: root.appendingPathComponent(".venv/bin/python").path,
            contents: Data()
        ))

        let config = BrainLocator.resolve(
            environment: [:],
            currentDirectory: URL(fileURLWithPath: "/"),
            resourceURL: resourceURL,
            fileManager: .default
        )

        XCTAssertEqual(
            config.pythonExecutable.standardizedFileURL.path,
            root.appendingPathComponent(".venv/bin/python").standardizedFileURL.path
        )
        XCTAssertEqual(
            config.brainDirectory.standardizedFileURL.path,
            root.appendingPathComponent("macos/brain").standardizedFileURL.path
        )
        XCTAssertEqual(config.extraPythonPath.map { $0.standardizedFileURL.path }, [root.standardizedFileURL.path])
    }
}
