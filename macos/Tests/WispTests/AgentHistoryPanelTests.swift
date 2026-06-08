import XCTest
import Foundation
@testable import Wisp

final class AgentHistoryPanelTests: XCTestCase {

    func testSummaryParsesHistoryListPayload() throws {
        let summary = try XCTUnwrap(AgentRunSummary(payload: [
            "id": "20260101-demo",
            "run_dir": "/tmp/wisp/agent-runs/20260101-demo",
            "title": "Demo run",
            "objective": "Inspect artifacts",
            "status": "complete",
            "modified_display": "2026-01-01 10:00:00",
            "has_final": true,
            "has_error": false,
            "has_diff": true,
        ]))

        XCTAssertEqual(summary.id, "20260101-demo")
        XCTAssertEqual(summary.runDir, "/tmp/wisp/agent-runs/20260101-demo")
        XCTAssertEqual(summary.title, "Demo run")
        XCTAssertEqual(summary.status, "complete")
        XCTAssertTrue(summary.hasFinal)
        XCTAssertTrue(summary.hasDiff)
        XCTAssertFalse(summary.hasError)
    }

    func testDetailParsesRunArtifacts() throws {
        let detail = try XCTUnwrap(AgentRunDetail(payload: [
            "id": "20260101-demo",
            "run_dir": "/tmp/wisp/agent-runs/20260101-demo",
            "title": "Demo run",
            "status": "failed",
            "task_json": "{\"title\":\"Demo run\"}",
            "final": "Final report",
            "error": "boom",
            "run_log": "agent run failed",
            "diff_patch": "diff --git a/a b/a",
        ]))

        XCTAssertEqual(detail.summary.status, "failed")
        XCTAssertEqual(detail.taskJSON, "{\"title\":\"Demo run\"}")
        XCTAssertEqual(detail.final, "Final report")
        XCTAssertEqual(detail.error, "boom")
        XCTAssertEqual(detail.runLog, "agent run failed")
        XCTAssertEqual(detail.diffPatch, "diff --git a/a b/a")
        XCTAssertTrue(detail.hasDisplayableDiff)
    }

    func testAgentHistoryAndDiffPanelsKeepNativeRunActions() throws {
        let history = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/AgentsUI/AgentHistoryPanel.swift"),
            encoding: .utf8
        )
        let diff = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/AgentsUI/AgentDiffPanel.swift"),
            encoding: .utf8
        )
        let appDelegate = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "onRetryRun",
            "onContinueRun",
            "onOpenDiff",
            "retrySelectedRun()",
            "continueSelectedRun()",
            "openDiff()",
            "Text(\"Final\").tag(\"final\")",
            "Text(\"Log\").tag(\"log\")",
            "Text(\"Task\").tag(\"task\")",
            "Text(\"Diff\").tag(\"diff\")",
            "detail.hasDisplayableDiff",
        ] {
            XCTAssertTrue(history.contains(expected), "AgentHistoryPanel is missing \(expected).")
        }

        for expected in [
            "final class AgentDiffPanel",
            "func showDiff(title: String, runDir: String, diffPatch: String)",
            "DiffTextScrollView",
            "model.openFolder()",
        ] {
            XCTAssertTrue(diff.contains(expected), "AgentDiffPanel is missing \(expected).")
        }

        for expected in [
            "brain.agent.history.list",
            "brain.agent.history.read",
            "brain.agent.history.retry_spec",
            "brain.agent.history.continue_spec",
            "showAgentDiff(detail)",
            "agentTaskPanel?.showTask",
        ] {
            XCTAssertTrue(appDelegate.contains(expected), "AppDelegate agent history wiring is missing \(expected).")
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
