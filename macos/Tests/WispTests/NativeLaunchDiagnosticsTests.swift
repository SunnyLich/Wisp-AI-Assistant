import XCTest
import Foundation
@testable import Wisp

final class NativeLaunchDiagnosticsTests: XCTestCase {

    func testStartupRecordContainsOnlyLaunchDiagnostics() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-launch-diagnostics-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let resourceURL = root.appendingPathComponent("build/WispNative/Wisp.app/Contents/Resources")
        let python = resourceURL.appendingPathComponent("python-runtime/bin/python3")
        let brain = resourceURL.appendingPathComponent("brain")
        try FileManager.default.createDirectory(at: python.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: brain, withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: python.path, contents: Data()))

        let config = WispConfig.load(
            environment: [
                "CALLER_COUNT": "1",
                "CALLER_1_LABEL": "Research",
                "HOTKEY_SNIP": "ctrl+option+4",
            ],
            readDotEnv: false
        )
        let record = NativeLaunchDiagnostics.startupRecord(
            now: Date(timeIntervalSince1970: 1_704_067_200),
            environment: [
                "WISP_REPO_ROOT": root.path,
                "WISP_RUN_LOG_DIR": root.appendingPathComponent("build_logs/macos_phase1_20260101-000000").path,
                "OPENAI_API_KEY": "should-not-be-written",
            ],
            config: config,
            brainConfig: BrainClient.Config(
                pythonExecutable: python,
                brainDirectory: brain,
                extraPythonPath: [resourceURL]
            ),
            resourceURL: resourceURL
        )

        XCTAssertTrue(record.contains("started_at=2024-01-01T00:00:00Z"))
        XCTAssertTrue(record.contains("process_id="))
        XCTAssertTrue(record.contains("bundle_identifier="))
        XCTAssertTrue(record.contains("bundle_path="))
        XCTAssertTrue(record.contains("executable_path="))
        XCTAssertTrue(record.contains("repo_root=\(root.path)"))
        XCTAssertTrue(record.contains("run_log_dir=\(root.appendingPathComponent("build_logs/macos_phase1_20260101-000000").path)"))
        XCTAssertTrue(record.contains("resource_url=\(resourceURL.path)"))
        XCTAssertTrue(record.contains("brain_python=\(python.path)"))
        XCTAssertTrue(record.contains("brain_python_exists=true"))
        XCTAssertTrue(record.contains("brain_python_is_executable="))
        XCTAssertTrue(record.contains("brain_python_configured=\(python.path)"))
        XCTAssertTrue(record.contains("brain_python_configured_exists=true"))
        XCTAssertTrue(record.contains("brain_dir=\(brain.path)"))
        XCTAssertTrue(record.contains("brain_dir_exists=true"))
        XCTAssertTrue(record.contains("brain_pythonpath=\(resourceURL.path)"))
        XCTAssertTrue(record.contains("caller_count=1"))
        XCTAssertTrue(record.contains("snip_hotkey=ctrl+option+4"))
        XCTAssertFalse(record.contains("should-not-be-written"))
    }

    func testStartupRecordShowsResolvedVirtualenvPythonForMissingConfiguredPython() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-launch-diagnostics-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let python = root.appendingPathComponent(".venv/bin/python")
        try FileManager.default.createDirectory(at: python.deletingLastPathComponent(), withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: python.path, contents: Data()))

        let config = WispConfig.load(environment: [:], readDotEnv: false)
        let record = NativeLaunchDiagnostics.startupRecord(
            environment: ["WISP_REPO_ROOT": root.path],
            config: config,
            brainConfig: BrainClient.Config(
                pythonExecutable: URL(fileURLWithPath: "/missing/python"),
                brainDirectory: root.appendingPathComponent("macos/brain"),
                extraPythonPath: [root]
            ),
            resourceURL: root.appendingPathComponent("build/WispNative/Wisp.app/Contents/Resources")
        )

        XCTAssertTrue(record.contains("brain_python=\(python.path)"))
        XCTAssertTrue(record.contains("brain_python_exists=true"))
        XCTAssertTrue(record.contains("brain_python_configured=/missing/python"))
        XCTAssertTrue(record.contains("brain_python_configured_exists=false"))
    }
}
