import XCTest
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
    }
}
