import XCTest
import Foundation
@testable import Wisp

final class RunLogLocatorTests: XCTestCase {

    func testExplicitRunLogDirectoryWins() {
        let url = RunLogLocator.logDirectory(
            environment: [
                "WISP_RUN_LOG_DIR": "/tmp/wisp-logs",
                "WISP_REPO_ROOT": "/tmp/repo",
            ],
            currentDirectory: URL(fileURLWithPath: "/tmp/repo/macos")
        )

        XCTAssertEqual(url?.path, "/tmp/wisp-logs")
    }

    func testFallsBackToNewestNativeMacLogDirectory() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-run-log-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let logs = root.appendingPathComponent("build_logs")
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let old = logs.appendingPathComponent("macos_native_tests_20260101-010101")
        let newest = logs.appendingPathComponent("macos_package_20260202-020202")
        let ignored = logs.appendingPathComponent("windows_build_20260303-030303")
        try FileManager.default.createDirectory(at: old, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: newest, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: ignored, withIntermediateDirectories: true)

        let oldDate = Date(timeIntervalSince1970: 100)
        let newestDate = Date(timeIntervalSince1970: 200)
        try FileManager.default.setAttributes([.modificationDate: oldDate], ofItemAtPath: old.path)
        try FileManager.default.setAttributes([.modificationDate: newestDate], ofItemAtPath: newest.path)
        try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSince1970: 300)], ofItemAtPath: ignored.path)

        let url = RunLogLocator.logDirectory(
            environment: ["WISP_REPO_ROOT": root.path],
            currentDirectory: root.appendingPathComponent("macos")
        )

        XCTAssertEqual(url?.standardizedFileURL.path, newest.standardizedFileURL.path)
    }

    func testFinderLaunchedDevBundleInfersRunLogsFromBundlePath() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-run-log-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let resourceURL = root
            .appendingPathComponent("build/WispNative/Wisp.app/Contents/Resources")
        let logs = root.appendingPathComponent("build_logs")
        let newest = logs.appendingPathComponent("macos_package_20260404-040404")
        try FileManager.default.createDirectory(at: resourceURL, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("macos/brain"),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(at: newest, withIntermediateDirectories: true)

        let url = RunLogLocator.logDirectory(
            environment: [:],
            currentDirectory: URL(fileURLWithPath: "/"),
            resourceURL: resourceURL
        )

        XCTAssertEqual(url?.standardizedFileURL.path, newest.standardizedFileURL.path)
    }

    func testResolvedEnvironmentSeedsMissingRunLogDirectory() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-run-log-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let logs = root.appendingPathComponent("build_logs")
        let newest = logs.appendingPathComponent("macos_native_tests_20260505-050505")
        try FileManager.default.createDirectory(at: newest, withIntermediateDirectories: true)

        let environment = RunLogLocator.environmentByResolvingLogDirectory(
            environment: ["WISP_REPO_ROOT": root.path],
            currentDirectory: root.appendingPathComponent("macos")
        )

        XCTAssertEqual(
            environment["WISP_RUN_LOG_DIR"].map { URL(fileURLWithPath: $0).standardizedFileURL.path },
            newest.standardizedFileURL.path
        )
    }

    func testResolvedEnvironmentSeedsRepoRootFromDevBundlePath() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-run-log-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let resourceURL = root
            .appendingPathComponent("build/WispNative/Wisp.app/Contents/Resources")
        let newest = root.appendingPathComponent("build_logs/macos_phase1_20260606-060606")
        try FileManager.default.createDirectory(at: resourceURL, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("macos/brain"),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(at: newest, withIntermediateDirectories: true)

        let environment = RunLogLocator.environmentByResolvingLogDirectory(
            environment: [:],
            currentDirectory: URL(fileURLWithPath: "/"),
            resourceURL: resourceURL
        )

        XCTAssertEqual(
            environment["WISP_REPO_ROOT"].map { URL(fileURLWithPath: $0).standardizedFileURL.path },
            root.standardizedFileURL.path
        )
        XCTAssertEqual(
            environment["WISP_RUN_LOG_DIR"].map { URL(fileURLWithPath: $0).standardizedFileURL.path },
            newest.standardizedFileURL.path
        )
    }

    func testFallsBackToUserLogDirectoryForPackagedAppOutsideCheckout() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-run-log-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let library = root.appendingPathComponent("Library")
        let resourceURL = root.appendingPathComponent("Applications/Wisp.app/Contents/Resources")
        try FileManager.default.createDirectory(at: resourceURL, withIntermediateDirectories: true)

        let url = RunLogLocator.logDirectory(
            environment: [:],
            currentDirectory: URL(fileURLWithPath: "/"),
            resourceURL: resourceURL,
            userLogBaseDirectory: library
        )

        XCTAssertEqual(
            url?.standardizedFileURL.path,
            library.appendingPathComponent("Logs/Wisp").standardizedFileURL.path
        )
    }

    func testResolvedEnvironmentSeedsUserLogDirectoryForPackagedApp() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-run-log-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let library = root.appendingPathComponent("Library")
        let environment = RunLogLocator.environmentByResolvingLogDirectory(
            environment: [:],
            currentDirectory: URL(fileURLWithPath: "/"),
            resourceURL: root.appendingPathComponent("Applications/Wisp.app/Contents/Resources"),
            userLogBaseDirectory: library
        )

        XCTAssertEqual(
            environment["WISP_RUN_LOG_DIR"].map { URL(fileURLWithPath: $0).standardizedFileURL.path },
            library.appendingPathComponent("Logs/Wisp").standardizedFileURL.path
        )
        XCTAssertNil(environment["WISP_REPO_ROOT"])
    }

    func testResolvedEnvironmentDoesNotReplaceExplicitRunLogDirectory() throws {
        let environment = RunLogLocator.environmentByResolvingLogDirectory(
            environment: [
                "WISP_RUN_LOG_DIR": "/tmp/explicit-wisp-logs",
                "WISP_REPO_ROOT": "/tmp/repo",
            ],
            currentDirectory: URL(fileURLWithPath: "/tmp/repo/macos")
        )

        XCTAssertEqual(environment["WISP_RUN_LOG_DIR"], "/tmp/explicit-wisp-logs")
    }
}
