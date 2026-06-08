import XCTest
import Foundation
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
        draft.networkPermissionMode = "auto"
        draft.fileDeletePermissionMode = "ask permission"
        draft.allowedFileGlobs = "*.swift, macos/*"
        draft.parallelExecution = true
        draft.maxParallelAgents = "6"

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
        XCTAssertEqual(payload["network_permission_mode"] as? String, "auto")
        XCTAssertEqual(payload["file_delete_permission_mode"] as? String, "ask permission")
        XCTAssertEqual(payload["allowed_file_globs"] as? [String], ["*.swift", "macos/*"])
        XCTAssertEqual(payload["blocked_file_globs"] as? [String], [".env", "private/*", ".git/*"])
        XCTAssertEqual(payload["parallel_execution"] as? Bool, true)
        XCTAssertEqual(payload["max_parallel_agents"] as? Int, 6)

        let agents = try XCTUnwrap(payload["agents"] as? [[String: String]])
        XCTAssertEqual(agents.count, 3)
        XCTAssertEqual(agents.first?["name"], "Coordinator")
        XCTAssertEqual(agents.first?["role"], "Coordinator")
        XCTAssertEqual(agents.first?["provider"], "same as task")
        XCTAssertEqual(agents.first?["model"], "same as task")

        let communications = try XCTUnwrap(payload["communications"] as? [[String: String]])
        XCTAssertEqual(communications.count, 3)
        XCTAssertEqual(communications.first?["from_agent"], "Coordinator")
        XCTAssertEqual(communications.first?["to_agent"], "Builder")
    }

    func testDefaultAutoAgentTeamMatchesWindowsDialog() throws {
        let draft = AgentTaskDraft.load(environment: ["WISP_REPO_ROOT": "/tmp/wisp"], readDotEnv: false)

        XCTAssertEqual(draft.agents.map(\.name), ["Coordinator", "Builder", "Reviewer"])
        XCTAssertEqual(draft.agents.map(\.role), ["Coordinator", "Implementer", "Reviewer"])
        XCTAssertEqual(draft.communications.map(\.fromAgent), ["Coordinator", "Builder", "Reviewer"])
        XCTAssertEqual(draft.communications.map(\.toAgent), ["Builder", "Reviewer", "Coordinator"])
        XCTAssertEqual(draft.shellPermissionMode, "auto")
        XCTAssertEqual(draft.networkPermissionMode, "never permit")
        XCTAssertEqual(draft.blockedFileGlobs, ".env, private/*, .git/*")
    }

    func testAgentTaskPanelKeepsWindowsCopyAndPreviewActions() throws {
        let root = sourceRoot()
        let panelSource = try String(
            contentsOf: root.appendingPathComponent("Sources/Wisp/AgentsUI/AgentTaskPanel.swift"),
            encoding: .utf8
        )
        let appSource = try String(
            contentsOf: root.appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "Copy from Last Task",
            "Preview Spec",
            "onCopyLast",
            "copyFromLastTask()",
            "previewSpec()",
        ] {
            XCTAssertTrue(panelSource.contains(expected), "AgentTaskPanel is missing \(expected).")
        }
        XCTAssertTrue(appSource.contains("brain.agent.last_spec.read"))
        XCTAssertTrue(appSource.contains("copyLastNativeAgentTask()"))
        XCTAssertTrue(appSource.contains("agent.approval.request"))
        XCTAssertTrue(appSource.contains("brain.agent.approval.respond"))
        XCTAssertTrue(appSource.contains("presentAgentApproval"))
    }

    func testAgentTaskInputsUseReadableAdaptiveSystemColors() throws {
        let panelSource = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/AgentsUI/AgentTaskPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "private enum AgentTaskPalette",
            "NSColor.textColor",
            "NSColor.textBackgroundColor",
            ".foregroundStyle(AgentTaskPalette.inputText)",
            ".tint(AgentTaskPalette.inputText)",
            ".background(AgentTaskPalette.inputBackground)",
            "textView.textColor = .textColor",
            "textView.backgroundColor = .textBackgroundColor",
        ] {
            XCTAssertTrue(panelSource.contains(expected), "AgentTaskPanel input contrast is missing \(expected).")
        }
        XCTAssertFalse(panelSource.contains("textView.textColor = .labelColor"))
    }

    func testDraftParsesHistorySpecPayload() throws {
        let draft = try XCTUnwrap(AgentTaskDraft(payload: [
            "title": "Continue: Demo",
            "objective": "Inspect artifacts",
            "required_context": "Continuing from previous agent run",
            "completion_criteria": "Report risks",
            "scope_folder": "/tmp",
            "provider": "anthropic",
            "model": "claude-test",
            "model_fallbacks": "openai:gpt-test",
            "reasoning_effort": "high",
            "max_runtime_minutes": 20,
            "max_turns": 7,
            "allow_shell": false,
            "allow_network": true,
            "allow_git": false,
            "allow_file_create": true,
            "allow_file_edit": false,
            "allow_file_delete": true,
            "agents": [
                [
                    "name": "Reviewer",
                    "role": "Reviewer",
                    "responsibility": "Check the work.",
                ]
            ],
        ]))

        XCTAssertEqual(draft.title, "Continue: Demo")
        XCTAssertEqual(draft.requiredContext, "Continuing from previous agent run")
        XCTAssertEqual(draft.provider, "anthropic")
        XCTAssertEqual(draft.model, "claude-test")
        XCTAssertEqual(draft.maxRuntimeMinutes, "20")
        XCTAssertEqual(draft.maxTurns, "7")
        XCTAssertFalse(draft.allowShell)
        XCTAssertTrue(draft.allowNetwork)
        XCTAssertEqual(draft.agentName, "Reviewer")
        XCTAssertEqual(draft.agentRole, "Reviewer")
        XCTAssertEqual(draft.agentResponsibility, "Check the work.")
    }

    func testDraftSerializesMultipleAgents() throws {
        var draft = AgentTaskDraft.load(environment: ["WISP_REPO_ROOT": "/tmp/wisp"], readDotEnv: false)
        draft.title = "Multi-agent task"
        draft.objective = "Plan, build, and review."
        draft.scopeFolder = "/tmp"
        draft.agents = [
            AgentTaskAgentDraft(
                name: "Planner",
                role: "Planner",
                provider: "same as task",
                model: "same as task",
                responsibility: "Plan the work."
            ),
            AgentTaskAgentDraft(
                name: "Reviewer",
                role: "Reviewer",
                provider: "anthropic",
                model: "claude-test",
                responsibility: "Review the result."
            ),
        ]

        let payload = draft.payload
        let agents = try XCTUnwrap(payload["agents"] as? [[String: String]])

        XCTAssertEqual(agents.count, 2)
        XCTAssertEqual(agents[0]["name"], "Planner")
        XCTAssertEqual(agents[0]["role"], "Planner")
        XCTAssertEqual(agents[1]["name"], "Reviewer")
        XCTAssertEqual(agents[1]["provider"], "anthropic")
        XCTAssertEqual(agents[1]["model"], "claude-test")
    }

    func testDraftParsesMultipleAgents() throws {
        let draft = try XCTUnwrap(AgentTaskDraft(payload: [
            "title": "Multi-agent task",
            "objective": "Plan, build, and review.",
            "scope_folder": "/tmp",
            "agents": [
                [
                    "name": "Planner",
                    "role": "Planner",
                    "provider": "same as task",
                    "model": "same as task",
                    "responsibility": "Plan the work.",
                ],
                [
                    "name": "Reviewer",
                    "role": "Reviewer",
                    "provider": "anthropic",
                    "model": "claude-test",
                    "responsibility": "Review the result.",
                ],
            ],
        ]))

        XCTAssertEqual(draft.agents.count, 2)
        XCTAssertEqual(draft.agentName, "Planner")
        XCTAssertEqual(draft.agents[0].role, "Planner")
        XCTAssertEqual(draft.agents[1].name, "Reviewer")
        XCTAssertEqual(draft.agents[1].provider, "anthropic")
    }

    func testDraftSerializesAgentCommunications() throws {
        var draft = AgentTaskDraft.load(environment: ["WISP_REPO_ROOT": "/tmp/wisp"], readDotEnv: false)
        draft.title = "Communication task"
        draft.objective = "Coordinate planned handoffs."
        draft.scopeFolder = "/tmp"
        draft.agents = [
            AgentTaskAgentDraft(
                name: "Planner",
                role: "Planner",
                provider: "same as task",
                model: "same as task",
                responsibility: "Plan."
            ),
            AgentTaskAgentDraft(
                name: "Reviewer",
                role: "Reviewer",
                provider: "same as task",
                model: "same as task",
                responsibility: "Review."
            ),
        ]
        draft.communications = [
            AgentTaskCommunicationDraft(
                fromAgent: "Planner",
                toAgent: "Reviewer",
                phase: "review",
                trigger: "ready_for_review",
                message: "Review the implementation."
            )
        ]

        let payload = draft.payload
        let communications = try XCTUnwrap(payload["communications"] as? [[String: String]])

        XCTAssertEqual(communications.count, 1)
        XCTAssertEqual(communications[0]["from_agent"], "Planner")
        XCTAssertEqual(communications[0]["to_agent"], "Reviewer")
        XCTAssertEqual(communications[0]["phase"], "review")
        XCTAssertEqual(communications[0]["trigger"], "ready_for_review")
        XCTAssertEqual(communications[0]["message"], "Review the implementation.")
    }

    func testDraftParsesAgentCommunications() throws {
        let draft = try XCTUnwrap(AgentTaskDraft(payload: [
            "title": "Communication task",
            "objective": "Coordinate planned handoffs.",
            "scope_folder": "/tmp",
            "agents": [
                ["name": "Planner", "role": "Planner"],
                ["name": "Reviewer", "role": "Reviewer"],
            ],
            "communications": [
                [
                    "from_agent": "Planner",
                    "to_agent": "Reviewer",
                    "phase": "review",
                    "trigger": "ready_for_review",
                    "message": "Review the implementation.",
                ]
            ],
        ]))

        XCTAssertEqual(draft.communications.count, 1)
        XCTAssertEqual(draft.communications[0].fromAgent, "Planner")
        XCTAssertEqual(draft.communications[0].toAgent, "Reviewer")
        XCTAssertEqual(draft.communications[0].phase, "review")
        XCTAssertEqual(draft.communications[0].trigger, "ready_for_review")
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
