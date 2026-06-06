import XCTest
@testable import Wisp

final class AgentTaskPanelTests: XCTestCase {

    func testDraftSerializesBrainAgentRunContract() throws {
        var draft = AgentTaskDraft.load(environment: [
            "WISP_REPO_ROOT": "/tmp/wisp",
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "gpt-test",
            "LLM_FALLBACKS": "anthropic:claude-test",
        ], readDotEnv: false)
        draft.title = "Port task window"
        draft.objective = "Move the visible agent task runner to Swift."
        draft.scopeFolder = "/tmp"
        draft.maxRuntimeMinutes = "45"
        draft.maxTurns = "12"
        draft.allowNetwork = true
        draft.allowFileDelete = true

        let payload = draft.payload

        XCTAssertEqual(payload["title"] as? String, "Port task window")
        XCTAssertEqual(payload["objective"] as? String, "Move the visible agent task runner to Swift.")
        XCTAssertEqual(payload["scope_folder"] as? String, "/tmp")
        XCTAssertEqual(payload["provider"] as? String, "openai")
        XCTAssertEqual(payload["model"] as? String, "gpt-test")
        XCTAssertEqual(payload["model_fallbacks"] as? String, "anthropic:claude-test")
        XCTAssertEqual(payload["max_runtime_minutes"] as? Int, 45)
        XCTAssertEqual(payload["max_turns"] as? Int, 12)
        XCTAssertEqual(payload["allow_network"] as? Bool, true)
        XCTAssertEqual(payload["allow_file_delete"] as? Bool, true)

        let agents = try XCTUnwrap(payload["agents"] as? [[String: String]])
        XCTAssertEqual(agents.first?["name"], "Builder")
        XCTAssertEqual(agents.first?["role"], "Implementer")
        XCTAssertEqual(agents.first?["provider"], "same as task")
        XCTAssertEqual(agents.first?["model"], "same as task")
    }
}
