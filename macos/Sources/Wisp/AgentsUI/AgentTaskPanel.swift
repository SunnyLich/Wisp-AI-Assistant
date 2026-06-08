import AppKit
import SwiftUI

struct AgentTaskAgentDraft: Identifiable, Equatable {
    var id = UUID()
    var name: String
    var role: String
    var provider: String
    var model: String
    var responsibility: String

    static func defaultBuilder() -> AgentTaskAgentDraft {
        AgentTaskAgentDraft(
            name: "Builder",
            role: "Implementer",
            provider: "same as task",
            model: "same as task",
            responsibility: AgentRoleDefaults.responsibility(for: "Implementer")
        )
    }

    static func defaultTeam() -> [AgentTaskAgentDraft] {
        [
            AgentTaskAgentDraft(
                name: "Coordinator",
                role: "Coordinator",
                provider: "same as task",
                model: "same as task",
                responsibility: AgentRoleDefaults.responsibility(for: "Coordinator")
            ),
            AgentTaskAgentDraft.defaultBuilder(),
            AgentTaskAgentDraft(
                name: "Reviewer",
                role: "Reviewer",
                provider: "same as task",
                model: "same as task",
                responsibility: AgentRoleDefaults.responsibility(for: "Reviewer")
            ),
        ]
    }

    init(name: String, role: String, provider: String, model: String, responsibility: String) {
        self.name = name
        self.role = role
        self.provider = provider
        self.model = model
        self.responsibility = responsibility
    }

    init?(payload: [String: Any]) {
        let name = payload["name"] as? String ?? ""
        let role = payload["role"] as? String ?? "Implementer"
        if name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return nil
        }
        self.init(
            name: name,
            role: role,
            provider: payload["provider"] as? String ?? "same as task",
            model: payload["model"] as? String ?? "same as task",
            responsibility: payload["responsibility"] as? String ?? AgentRoleDefaults.responsibility(for: role)
        )
    }

    var payload: [String: String] {
        [
            "name": name.trimmingCharacters(in: .whitespacesAndNewlines),
            "role": role.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Implementer" : role.trimmingCharacters(in: .whitespacesAndNewlines),
            "provider": provider.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "same as task" : provider.trimmingCharacters(in: .whitespacesAndNewlines),
            "model": model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "same as task" : model.trimmingCharacters(in: .whitespacesAndNewlines),
            "responsibility": responsibility.trimmingCharacters(in: .whitespacesAndNewlines),
        ]
    }
}

struct AgentTaskCommunicationDraft: Identifiable, Equatable {
    var id = UUID()
    var fromAgent: String
    var toAgent: String
    var phase: String
    var trigger: String
    var message: String

    init(fromAgent: String, toAgent: String, phase: String, trigger: String, message: String) {
        self.fromAgent = fromAgent
        self.toAgent = toAgent
        self.phase = phase
        self.trigger = trigger
        self.message = message
    }

    static func defaultRules() -> [AgentTaskCommunicationDraft] {
        [
            AgentTaskCommunicationDraft(
                fromAgent: "Coordinator",
                toAgent: "Builder",
                phase: "Planning",
                trigger: "After reading the objective and scope",
                message: "Send the implementation plan, constraints, and first files to inspect."
            ),
            AgentTaskCommunicationDraft(
                fromAgent: "Builder",
                toAgent: "Reviewer",
                phase: "Review",
                trigger: "After changes and local verification",
                message: "Send changed files, verification results, and known tradeoffs for review."
            ),
            AgentTaskCommunicationDraft(
                fromAgent: "Reviewer",
                toAgent: "Coordinator",
                phase: "Completion",
                trigger: "After review is complete",
                message: "Send approval status, remaining concerns, and final-report notes."
            ),
        ]
    }

    init?(payload: [String: Any]) {
        let fromAgent = payload["from_agent"] as? String ?? ""
        let toAgent = payload["to_agent"] as? String ?? ""
        if fromAgent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || toAgent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return nil
        }
        self.init(
            fromAgent: fromAgent,
            toAgent: toAgent,
            phase: payload["phase"] as? String ?? "handoff",
            trigger: payload["trigger"] as? String ?? "when useful",
            message: payload["message"] as? String ?? ""
        )
    }

    var payload: [String: String] {
        [
            "from_agent": fromAgent.trimmingCharacters(in: .whitespacesAndNewlines),
            "to_agent": toAgent.trimmingCharacters(in: .whitespacesAndNewlines),
            "phase": phase.trimmingCharacters(in: .whitespacesAndNewlines),
            "trigger": trigger.trimmingCharacters(in: .whitespacesAndNewlines),
            "message": message.trimmingCharacters(in: .whitespacesAndNewlines),
        ]
    }
}

struct AgentTaskDraft: Equatable {
    static let permissionOptions = ["auto", "ask permission", "never permit"]

    var title: String
    var objective: String
    var requiredContext: String
    var completionCriteria: String
    var scopeFolder: String
    var sandboxMode: String
    var approvalPolicy: String
    var provider: String
    var model: String
    var modelFallbacks: String
    var reasoningEffort: String
    var maxRuntimeMinutes: String
    var maxTurns: String
    var allowShell: Bool
    var allowNetwork: Bool
    var allowGit: Bool
    var allowFileCreate: Bool
    var allowFileEdit: Bool
    var allowFileDelete: Bool
    var shellPermissionMode: String
    var networkPermissionMode: String
    var gitPermissionMode: String
    var fileCreatePermissionMode: String
    var fileEditPermissionMode: String
    var fileDeletePermissionMode: String
    var allowedFileGlobs: String
    var blockedFileGlobs: String
    var reportFormat: String
    var parallelReadOnlyBriefing: Bool
    var parallelExecution: Bool
    var maxParallelAgents: String
    var fullTurnMaxTokens: String
    var deltaTurnMaxTokens: String
    var readOnlyMaxTokens: String
    var agentTemperature: String
    var toolResultTextLimit: String
    var toolResultCommandLimit: String
    var toolResultValueLimit: String
    var toolResultListLimit: String
    var visibleFilesFullLimit: String
    var visibleFilesDeltaLimit: String
    var agentName: String
    var agentRole: String
    var agentResponsibility: String
    var agents: [AgentTaskAgentDraft]
    var communications: [AgentTaskCommunicationDraft]

    static func load(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        readDotEnv: Bool = true
    ) -> AgentTaskDraft {
        let values = WispConfig.loadValues(environment: environment, readDotEnv: readDotEnv)
        let root = WispConfig.repoRoot(environment: environment)
        let provider = values["LLM_PROVIDER"] ?? "same as app"
        let model = values["LLM_MODEL"] ?? ""
        return AgentTaskDraft(
            title: "",
            objective: "",
            requiredContext: "",
            completionCriteria: "Summarize changes, changed files, verification, and risks.",
            scopeFolder: root.path,
            sandboxMode: "workspace-write: scope folder only",
            approvalPolicy: "ask before escalation",
            provider: provider,
            model: model,
            modelFallbacks: values["LLM_FALLBACKS"] ?? "",
            reasoningEffort: values["REASONING_EFFORT"] ?? "medium",
            maxRuntimeMinutes: "60",
            maxTurns: "30",
            allowShell: true,
            allowNetwork: false,
            allowGit: true,
            allowFileCreate: true,
            allowFileEdit: true,
            allowFileDelete: false,
            shellPermissionMode: "auto",
            networkPermissionMode: "never permit",
            gitPermissionMode: "auto",
            fileCreatePermissionMode: "auto",
            fileEditPermissionMode: "auto",
            fileDeletePermissionMode: "never permit",
            allowedFileGlobs: "",
            blockedFileGlobs: ".env, private/*, .git/*",
            reportFormat: "Summary + changed files + verification",
            parallelReadOnlyBriefing: true,
            parallelExecution: false,
            maxParallelAgents: "4",
            fullTurnMaxTokens: "8192",
            deltaTurnMaxTokens: "6144",
            readOnlyMaxTokens: "3072",
            agentTemperature: "0.0",
            toolResultTextLimit: "6000",
            toolResultCommandLimit: "8000",
            toolResultValueLimit: "3000",
            toolResultListLimit: "120",
            visibleFilesFullLimit: "200",
            visibleFilesDeltaLimit: "80",
            agentName: "Builder",
            agentRole: "Implementer",
            agentResponsibility: AgentRoleDefaults.responsibility(for: "Implementer"),
            agents: AgentTaskAgentDraft.defaultTeam(),
            communications: AgentTaskCommunicationDraft.defaultRules()
        )
    }

    init(
        title: String,
        objective: String,
        requiredContext: String,
        completionCriteria: String,
        scopeFolder: String,
        sandboxMode: String = "workspace-write: scope folder only",
        approvalPolicy: String = "ask before escalation",
        provider: String,
        model: String,
        modelFallbacks: String,
        reasoningEffort: String,
        maxRuntimeMinutes: String,
        maxTurns: String,
        allowShell: Bool,
        allowNetwork: Bool,
        allowGit: Bool,
        allowFileCreate: Bool,
        allowFileEdit: Bool,
        allowFileDelete: Bool,
        shellPermissionMode: String = "auto",
        networkPermissionMode: String = "never permit",
        gitPermissionMode: String = "auto",
        fileCreatePermissionMode: String = "auto",
        fileEditPermissionMode: String = "auto",
        fileDeletePermissionMode: String = "never permit",
        allowedFileGlobs: String = "",
        blockedFileGlobs: String = ".env, private/*, .git/*",
        reportFormat: String = "Summary + changed files + verification",
        parallelReadOnlyBriefing: Bool = true,
        parallelExecution: Bool = false,
        maxParallelAgents: String = "4",
        fullTurnMaxTokens: String = "8192",
        deltaTurnMaxTokens: String = "6144",
        readOnlyMaxTokens: String = "3072",
        agentTemperature: String = "0.0",
        toolResultTextLimit: String = "6000",
        toolResultCommandLimit: String = "8000",
        toolResultValueLimit: String = "3000",
        toolResultListLimit: String = "120",
        visibleFilesFullLimit: String = "200",
        visibleFilesDeltaLimit: String = "80",
        agentName: String,
        agentRole: String,
        agentResponsibility: String,
        agents: [AgentTaskAgentDraft] = [],
        communications: [AgentTaskCommunicationDraft] = []
    ) {
        self.title = title
        self.objective = objective
        self.requiredContext = requiredContext
        self.completionCriteria = completionCriteria
        self.scopeFolder = scopeFolder
        self.sandboxMode = sandboxMode
        self.approvalPolicy = approvalPolicy
        self.provider = provider
        self.model = model
        self.modelFallbacks = modelFallbacks
        self.reasoningEffort = reasoningEffort
        self.maxRuntimeMinutes = maxRuntimeMinutes
        self.maxTurns = maxTurns
        self.allowShell = allowShell
        self.allowNetwork = allowNetwork
        self.allowGit = allowGit
        self.allowFileCreate = allowFileCreate
        self.allowFileEdit = allowFileEdit
        self.allowFileDelete = allowFileDelete
        self.shellPermissionMode = shellPermissionMode
        self.networkPermissionMode = networkPermissionMode
        self.gitPermissionMode = gitPermissionMode
        self.fileCreatePermissionMode = fileCreatePermissionMode
        self.fileEditPermissionMode = fileEditPermissionMode
        self.fileDeletePermissionMode = fileDeletePermissionMode
        self.allowedFileGlobs = allowedFileGlobs
        self.blockedFileGlobs = blockedFileGlobs
        self.reportFormat = reportFormat
        self.parallelReadOnlyBriefing = parallelReadOnlyBriefing
        self.parallelExecution = parallelExecution
        self.maxParallelAgents = maxParallelAgents
        self.fullTurnMaxTokens = fullTurnMaxTokens
        self.deltaTurnMaxTokens = deltaTurnMaxTokens
        self.readOnlyMaxTokens = readOnlyMaxTokens
        self.agentTemperature = agentTemperature
        self.toolResultTextLimit = toolResultTextLimit
        self.toolResultCommandLimit = toolResultCommandLimit
        self.toolResultValueLimit = toolResultValueLimit
        self.toolResultListLimit = toolResultListLimit
        self.visibleFilesFullLimit = visibleFilesFullLimit
        self.visibleFilesDeltaLimit = visibleFilesDeltaLimit
        self.agentName = agentName
        self.agentRole = agentRole
        self.agentResponsibility = agentResponsibility
        self.agents = agents.isEmpty
            ? [AgentTaskAgentDraft(name: agentName, role: agentRole, provider: "same as task", model: "same as task", responsibility: agentResponsibility)]
            : agents
        self.communications = communications
    }

    init?(payload: [String: Any]) {
        let rawAgents = payload["agents"] as? [[String: Any]] ?? []
        let parsedAgents = rawAgents.compactMap { AgentTaskAgentDraft(payload: $0) }
        let primaryAgent = parsedAgents.first ?? AgentTaskAgentDraft.defaultBuilder()
        let rawCommunications = payload["communications"] as? [[String: Any]] ?? []
        let parsedCommunications = rawCommunications.compactMap { AgentTaskCommunicationDraft(payload: $0) }
        let shellMode = payload["shell_permission_mode"] as? String
            ?? AgentTaskDraft.permissionMode(from: payload["allow_shell"] as? Bool, default: true)
        let networkMode = payload["network_permission_mode"] as? String
            ?? AgentTaskDraft.permissionMode(from: payload["allow_network"] as? Bool, default: false)
        let gitMode = payload["git_permission_mode"] as? String
            ?? AgentTaskDraft.permissionMode(from: payload["allow_git"] as? Bool, default: true)
        let createMode = payload["file_create_permission_mode"] as? String
            ?? AgentTaskDraft.permissionMode(from: payload["allow_file_create"] as? Bool, default: true)
        let editMode = payload["file_edit_permission_mode"] as? String
            ?? AgentTaskDraft.permissionMode(from: payload["allow_file_edit"] as? Bool, default: true)
        let deleteMode = payload["file_delete_permission_mode"] as? String
            ?? AgentTaskDraft.permissionMode(from: payload["allow_file_delete"] as? Bool, default: false)
        self.init(
            title: payload["title"] as? String ?? "",
            objective: payload["objective"] as? String ?? "",
            requiredContext: payload["required_context"] as? String ?? "",
            completionCriteria: payload["completion_criteria"] as? String ?? "",
            scopeFolder: payload["scope_folder"] as? String ?? "",
            sandboxMode: payload["sandbox_mode"] as? String ?? "workspace-write: scope folder only",
            approvalPolicy: payload["approval_policy"] as? String ?? "ask before escalation",
            provider: payload["provider"] as? String ?? "same as app",
            model: payload["model"] as? String ?? "",
            modelFallbacks: payload["model_fallbacks"] as? String ?? "",
            reasoningEffort: payload["reasoning_effort"] as? String ?? "medium",
            maxRuntimeMinutes: AgentTaskDraft.stringValue(payload["max_runtime_minutes"], default: "60"),
            maxTurns: AgentTaskDraft.stringValue(payload["max_turns"], default: "30"),
            allowShell: AgentTaskDraft.permissionEnabled(shellMode),
            allowNetwork: AgentTaskDraft.permissionEnabled(networkMode),
            allowGit: AgentTaskDraft.permissionEnabled(gitMode),
            allowFileCreate: AgentTaskDraft.permissionEnabled(createMode),
            allowFileEdit: AgentTaskDraft.permissionEnabled(editMode),
            allowFileDelete: AgentTaskDraft.permissionEnabled(deleteMode),
            shellPermissionMode: shellMode,
            networkPermissionMode: networkMode,
            gitPermissionMode: gitMode,
            fileCreatePermissionMode: createMode,
            fileEditPermissionMode: editMode,
            fileDeletePermissionMode: deleteMode,
            allowedFileGlobs: AgentTaskDraft.joinGlobs(payload["allowed_file_globs"]),
            blockedFileGlobs: AgentTaskDraft.joinGlobs(payload["blocked_file_globs"], default: ".env, private/*, .git/*"),
            reportFormat: payload["report_format"] as? String ?? "Summary + changed files + verification",
            parallelReadOnlyBriefing: payload["parallel_read_only_briefing"] as? Bool ?? true,
            parallelExecution: payload["parallel_execution"] as? Bool ?? false,
            maxParallelAgents: AgentTaskDraft.stringValue(payload["max_parallel_agents"], default: "4"),
            fullTurnMaxTokens: AgentTaskDraft.stringValue(payload["full_turn_max_tokens"], default: "8192"),
            deltaTurnMaxTokens: AgentTaskDraft.stringValue(payload["delta_turn_max_tokens"], default: "6144"),
            readOnlyMaxTokens: AgentTaskDraft.stringValue(payload["read_only_max_tokens"], default: "3072"),
            agentTemperature: AgentTaskDraft.stringValue(payload["agent_temperature"], default: "0.0"),
            toolResultTextLimit: AgentTaskDraft.stringValue(payload["tool_result_text_limit"], default: "6000"),
            toolResultCommandLimit: AgentTaskDraft.stringValue(payload["tool_result_command_limit"], default: "8000"),
            toolResultValueLimit: AgentTaskDraft.stringValue(payload["tool_result_value_limit"], default: "3000"),
            toolResultListLimit: AgentTaskDraft.stringValue(payload["tool_result_list_limit"], default: "120"),
            visibleFilesFullLimit: AgentTaskDraft.stringValue(payload["visible_files_full_limit"], default: "200"),
            visibleFilesDeltaLimit: AgentTaskDraft.stringValue(payload["visible_files_delta_limit"], default: "80"),
            agentName: primaryAgent.name,
            agentRole: primaryAgent.role,
            agentResponsibility: primaryAgent.responsibility,
            agents: parsedAgents.isEmpty ? [primaryAgent] : parsedAgents,
            communications: parsedCommunications
        )
        if cleaned(title).isEmpty || cleaned(objective).isEmpty {
            return nil
        }
    }

    var payload: [String: Any] {
        [
            "title": cleaned(title),
            "objective": cleaned(objective),
            "scope_folder": cleaned(scopeFolder),
            "sandbox_mode": cleaned(sandboxMode).isEmpty ? "workspace-write: scope folder only" : cleaned(sandboxMode),
            "approval_policy": cleaned(approvalPolicy).isEmpty ? "ask before escalation" : cleaned(approvalPolicy),
            "provider": cleaned(provider).isEmpty ? "same as app" : cleaned(provider),
            "model": cleaned(model),
            "reasoning_effort": cleaned(reasoningEffort).isEmpty ? "medium" : cleaned(reasoningEffort),
            "max_runtime_minutes": intValue(maxRuntimeMinutes, default: 60),
            "max_turns": intValue(maxTurns, default: 30),
            "allow_shell": AgentTaskDraft.permissionEnabled(shellPermissionMode),
            "allow_network": AgentTaskDraft.permissionEnabled(networkPermissionMode),
            "allow_git": AgentTaskDraft.permissionEnabled(gitPermissionMode),
            "allow_file_create": AgentTaskDraft.permissionEnabled(fileCreatePermissionMode),
            "allow_file_edit": AgentTaskDraft.permissionEnabled(fileEditPermissionMode),
            "allow_file_delete": AgentTaskDraft.permissionEnabled(fileDeletePermissionMode),
            "shell_permission_mode": AgentTaskDraft.normalizedPermissionMode(shellPermissionMode, default: "auto"),
            "network_permission_mode": AgentTaskDraft.normalizedPermissionMode(networkPermissionMode, default: "never permit"),
            "git_permission_mode": AgentTaskDraft.normalizedPermissionMode(gitPermissionMode, default: "auto"),
            "file_create_permission_mode": AgentTaskDraft.normalizedPermissionMode(fileCreatePermissionMode, default: "auto"),
            "file_edit_permission_mode": AgentTaskDraft.normalizedPermissionMode(fileEditPermissionMode, default: "auto"),
            "file_delete_permission_mode": AgentTaskDraft.normalizedPermissionMode(fileDeletePermissionMode, default: "never permit"),
            "allowed_file_globs": splitGlobs(allowedFileGlobs),
            "blocked_file_globs": splitGlobs(blockedFileGlobs),
            "required_context": cleaned(requiredContext),
            "completion_criteria": cleaned(completionCriteria),
            "report_format": cleaned(reportFormat).isEmpty ? "Summary + changed files + verification" : cleaned(reportFormat),
            "model_fallbacks": cleaned(modelFallbacks),
            "agents": normalizedAgents().map(\.payload),
            "communications": normalizedCommunications().map(\.payload),
            "parallel_read_only_briefing": parallelReadOnlyBriefing,
            "parallel_execution": parallelExecution,
            "max_parallel_agents": intValue(maxParallelAgents, default: 4),
            "full_turn_max_tokens": intValue(fullTurnMaxTokens, default: 8192),
            "delta_turn_max_tokens": intValue(deltaTurnMaxTokens, default: 6144),
            "read_only_max_tokens": intValue(readOnlyMaxTokens, default: 3072),
            "agent_temperature": doubleValue(agentTemperature, default: 0.0),
            "tool_result_text_limit": intValue(toolResultTextLimit, default: 6000),
            "tool_result_command_limit": intValue(toolResultCommandLimit, default: 8000),
            "tool_result_value_limit": intValue(toolResultValueLimit, default: 3000),
            "tool_result_list_limit": intValue(toolResultListLimit, default: 120),
            "visible_files_full_limit": intValue(visibleFilesFullLimit, default: 200),
            "visible_files_delta_limit": intValue(visibleFilesDeltaLimit, default: 80),
        ]
    }

    func validationError() -> String? {
        if cleaned(title).isEmpty {
            return "Title is required."
        }
        if cleaned(objective).isEmpty {
            return "Objective is required."
        }
        let scope = cleaned(scopeFolder)
        if scope.isEmpty {
            return "Scope folder is required."
        }
        var isDirectory: ObjCBool = false
        if !FileManager.default.fileExists(atPath: scope, isDirectory: &isDirectory) || !isDirectory.boolValue {
            return "Scope folder does not exist."
        }
        let agentNames = normalizedAgents().map { cleaned($0.name).lowercased() }
        if agentNames.isEmpty {
            return "Add at least one agent."
        }
        if Set(agentNames).count != agentNames.count {
            return "Agent names must be unique."
        }
        let nameSet = Set(agentNames)
        for communication in normalizedCommunications() {
            let from = cleaned(communication.fromAgent).lowercased()
            let to = cleaned(communication.toAgent).lowercased()
            if !nameSet.contains(from) || !nameSet.contains(to) {
                return "Every communication must reference existing agents."
            }
            if from == to {
                return "Communication endpoints must be different agents."
            }
        }
        return nil
    }

    private func cleaned(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func intValue(_ value: String, default fallback: Int) -> Int {
        Int(cleaned(value)) ?? fallback
    }

    private func doubleValue(_ value: String, default fallback: Double) -> Double {
        Double(cleaned(value)) ?? fallback
    }

    private func splitGlobs(_ value: String) -> [String] {
        value
            .split(separator: ",")
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private func normalizedAgents() -> [AgentTaskAgentDraft] {
        let candidates = agents.isEmpty
            ? [AgentTaskAgentDraft(name: agentName, role: agentRole, provider: "same as task", model: "same as task", responsibility: agentResponsibility)]
            : agents
        return candidates.compactMap { agent in
            let name = cleaned(agent.name)
            guard !name.isEmpty else { return nil }
            return AgentTaskAgentDraft(
                name: name,
                role: cleaned(agent.role).isEmpty ? "Implementer" : cleaned(agent.role),
                provider: cleaned(agent.provider).isEmpty ? "same as task" : cleaned(agent.provider),
                model: cleaned(agent.model).isEmpty ? "same as task" : cleaned(agent.model),
                responsibility: cleaned(agent.responsibility)
            )
        }
    }

    private func normalizedCommunications() -> [AgentTaskCommunicationDraft] {
        communications.compactMap { communication in
            let from = cleaned(communication.fromAgent)
            let to = cleaned(communication.toAgent)
            guard !from.isEmpty, !to.isEmpty else { return nil }
            return AgentTaskCommunicationDraft(
                fromAgent: from,
                toAgent: to,
                phase: cleaned(communication.phase).isEmpty ? "handoff" : cleaned(communication.phase),
                trigger: cleaned(communication.trigger).isEmpty ? "when useful" : cleaned(communication.trigger),
                message: cleaned(communication.message)
            )
        }
    }

    private static func stringValue(_ value: Any?, default fallback: String) -> String {
        if let value = value as? String {
            return value
        }
        if let value = value as? Int {
            return String(value)
        }
        if let value = value as? Double {
            return String(value)
        }
        return fallback
    }

    private static func joinGlobs(_ value: Any?, default fallback: String = "") -> String {
        if let values = value as? [String] {
            return values.joined(separator: ", ")
        }
        if let values = value as? [Any] {
            return values.map { String(describing: $0) }.joined(separator: ", ")
        }
        if let value = value as? String {
            return value
        }
        return fallback
    }

    private static func permissionMode(from enabled: Bool?, default fallback: Bool) -> String {
        (enabled ?? fallback) ? "auto" : "never permit"
    }

    private static func normalizedPermissionMode(_ value: String, default fallback: String) -> String {
        let cleaned = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch cleaned {
        case "auto", "allow", "always":
            return "auto"
        case "ask", "ask permission", "approval", "approval required":
            return "ask permission"
        case "never", "never permit", "deny", "disabled":
            return "never permit"
        default:
            return fallback
        }
    }

    private static func permissionEnabled(_ value: String) -> Bool {
        normalizedPermissionMode(value, default: "never permit") != "never permit"
    }
}

struct AgentRunResult: Equatable {
    var runDir: String
    var final: String
    var error: String
    var cancelled: Bool
}

enum AgentRoleDefaults {
    static let roles = ["Coordinator", "Planner", "Implementer", "Reviewer", "Tester", "Researcher"]

    static func responsibility(for role: String) -> String {
        switch role {
        case "Coordinator":
            return "Break down the task, assign work, merge decisions, arbitrate conflicts, and decide when the group is done."
        case "Planner":
            return "Turn the objective into a concrete plan, identify risks and dependencies, and hand clear next steps to the right agent."
        case "Reviewer":
            return "Inspect proposed changes, call out defects or missing tests, request fixes when needed, and approve completion when no Coordinator exists."
        case "Tester":
            return "Design and run focused verification, reproduce failures, report exact commands and results, and confirm fixes."
        case "Researcher":
            return "Gather read-only context before implementation: inspect files, docs, logs, APIs, and constraints, then brief the team with findings and recommendations."
        default:
            return "Make the code changes inside the selected scope, keep edits focused, run relevant checks, and report risks or blockers."
        }
    }
}

@MainActor
final class AgentTaskPanel: NSPanel {

    private let model: AgentTaskModel

    init(
        onStart: @escaping (AgentTaskDraft) -> Void,
        onCancel: @escaping () -> Void,
        onCopyLast: @escaping () -> Void
    ) {
        self.model = AgentTaskModel(onStart: onStart, onCancel: onCancel, onCopyLast: onCopyLast)
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 820, height: 660),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Start Agent Task"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 680, height: 520)
        contentView = NSHostingView(rootView: AgentTaskPanelView(model: model))
        center()
    }

    func showTask() {
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func reloadDraft() {
        guard !model.isRunning else { return }
        model.draft = AgentTaskDraft.load()
        model.status = "Ready"
        model.errorText = ""
    }

    func setDraft(_ draft: AgentTaskDraft) {
        guard !model.isRunning else { return }
        model.draft = draft
        model.status = "Ready"
        model.errorText = ""
        model.finalText = ""
        model.runLines = []
        model.runDir = ""
    }

    func setStatus(_ status: String) {
        guard !model.isRunning else { return }
        model.status = status
    }

    func beginRun() {
        model.isRunning = true
        model.errorText = ""
        model.runLines = []
        model.finalText = ""
        model.runDir = ""
        model.status = "Agent running"
    }

    func appendLog(_ line: String) {
        model.appendLog(line)
    }

    func appendTrace(_ line: String) {
        model.appendLog("trace: \(line)")
    }

    func finishRun(_ result: AgentRunResult) {
        model.isRunning = false
        model.runDir = result.runDir
        model.finalText = result.final
        model.errorText = result.error
        if result.cancelled {
            model.status = "Agent cancelled"
        } else if !result.error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            model.status = "Agent failed"
        } else {
            model.status = "Agent complete"
        }
    }

    func fail(_ message: String) {
        model.isRunning = false
        model.status = "Agent error"
        model.errorText = message
    }
}

@MainActor
private final class AgentTaskModel: ObservableObject {
    @Published var draft = AgentTaskDraft.load()
    @Published var isRunning = false
    @Published var status = "Ready"
    @Published var errorText = ""
    @Published var runLines: [String] = []
    @Published var finalText = ""
    @Published var runDir = ""
    @Published var scrollToken = 0

    private let onStart: (AgentTaskDraft) -> Void
    private let onCancel: () -> Void
    private let onCopyLast: () -> Void

    init(
        onStart: @escaping (AgentTaskDraft) -> Void,
        onCancel: @escaping () -> Void,
        onCopyLast: @escaping () -> Void
    ) {
        self.onStart = onStart
        self.onCancel = onCancel
        self.onCopyLast = onCopyLast
    }

    func chooseScope() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Use Folder"
        if FileManager.default.fileExists(atPath: draft.scopeFolder) {
            panel.directoryURL = URL(fileURLWithPath: draft.scopeFolder)
        }
        if panel.runModal() == .OK, let url = panel.url {
            draft.scopeFolder = url.path
        }
    }

    func start() {
        if let error = draft.validationError() {
            errorText = error
            status = "Agent needs input"
            return
        }
        errorText = ""
        onStart(draft)
    }

    func copyFromLastTask() {
        guard !isRunning else { return }
        errorText = ""
        status = "Loading last task..."
        onCopyLast()
    }

    func previewSpec() {
        if let error = draft.validationError() {
            errorText = error
            status = "Agent needs input"
            return
        }

        do {
            let payload = draft.payload
            guard JSONSerialization.isValidJSONObject(payload) else {
                errorText = "Task spec cannot be encoded as JSON."
                status = "Preview failed"
                return
            }
            let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
            let text = String(data: data, encoding: .utf8) ?? "{}"
            showPreview(text)
            status = "Spec previewed"
            errorText = ""
        } catch {
            errorText = String(describing: error)
            status = "Preview failed"
        }
    }

    func cancel() {
        if isRunning {
            onCancel()
            status = "Cancelling..."
        } else {
            NSApp.keyWindow?.performClose(nil)
        }
    }

    func appendLog(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        runLines.append(trimmed)
        if runLines.count > 400 {
            runLines.removeFirst(runLines.count - 400)
        }
        scrollToken += 1
    }

    func addAgent() {
        let number = draft.agents.count + 1
        draft.agents.append(
            AgentTaskAgentDraft(
                name: "Agent \(number)",
                role: "Implementer",
                provider: "same as task",
                model: "same as task",
                responsibility: AgentRoleDefaults.responsibility(for: "Implementer")
            )
        )
    }

    func resetAgentsToDefault() {
        draft.agents = AgentTaskAgentDraft.defaultTeam()
        draft.communications = AgentTaskCommunicationDraft.defaultRules()
        status = "Default agent team ready"
        errorText = ""
    }

    func useSoloBuilder() {
        draft.agents = [AgentTaskAgentDraft.defaultBuilder()]
        draft.communications = []
        status = "Solo agent ready"
        errorText = ""
    }

    func removeAgent(_ id: UUID) {
        guard draft.agents.count > 1 else { return }
        draft.agents.removeAll { $0.id == id }
    }

    func addCommunication() {
        let agents = draft.agents.map(\.name).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        let from = agents.first ?? "Planner"
        let to = agents.dropFirst().first ?? agents.first ?? "Reviewer"
        draft.communications.append(
            AgentTaskCommunicationDraft(
                fromAgent: from,
                toAgent: to,
                phase: "handoff",
                trigger: "when useful",
                message: "Share findings and next steps."
            )
        )
    }

    func removeCommunication(_ id: UUID) {
        draft.communications.removeAll { $0.id == id }
    }

    private func showPreview(_ text: String) {
        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 640, height: 420))
        textView.string = text
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.textColor = .textColor
        textView.backgroundColor = .textBackgroundColor

        let scrollView = NSScrollView(frame: textView.frame)
        scrollView.borderType = .bezelBorder
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = true
        scrollView.autohidesScrollers = false
        scrollView.documentView = textView

        let alert = NSAlert()
        alert.messageText = "Agent Task Spec"
        alert.informativeText = "This is the exact spec that will be sent to the agent runner."
        alert.accessoryView = scrollView
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

private struct AgentTaskPanelView: View {
    @ObservedObject var model: AgentTaskModel
    @FocusState private var objectiveFocused: Bool

    var body: some View {
        HSplitView {
            form
                .frame(minWidth: 360, idealWidth: 440)
            runView
                .frame(minWidth: 300)
        }
        .frame(minWidth: 680, minHeight: 520)
        .onAppear { objectiveFocused = true }
    }

    private var form: some View {
        VStack(spacing: 0) {
            header
            Divider()
            copyRow
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    AgentSection("Task") {
                        AgentTextField("Title", text: $model.draft.title)
                        AgentTextEditor("Objective", text: $model.draft.objective, minHeight: 110)
                            .focused($objectiveFocused)
                        AgentTextEditor("Context", text: $model.draft.requiredContext, minHeight: 74)
                        AgentTextEditor("Completion", text: $model.draft.completionCriteria, minHeight: 62)
                    }

                    AgentSection("Scope") {
                        HStack(spacing: 8) {
                            AgentTextField("Folder", text: $model.draft.scopeFolder)
                            Button {
                                model.chooseScope()
                            } label: {
                                Image(systemName: "folder")
                            }
                            .help("Choose scope folder")
                        }
                    }

                    AgentSection("Model") {
                        AgentTextField("Provider", text: $model.draft.provider)
                        AgentTextField("Model", text: $model.draft.model)
                        AgentTextEditor("Fallbacks", text: $model.draft.modelFallbacks, minHeight: 50)
                        Picker("Reasoning", selection: $model.draft.reasoningEffort) {
                            ForEach(["minimal", "low", "medium", "high"], id: \.self) { value in
                                Text(value).tag(value)
                            }
                        }
                    }

                    AgentSection("Agent") {
                        HStack(spacing: 8) {
                            Button {
                                model.resetAgentsToDefault()
                            } label: {
                                Label("Auto Agent Team", systemImage: "person.3")
                            }
                            .buttonStyle(.borderless)
                            .help("Use the default Coordinator, Builder, Reviewer team")

                            Button {
                                model.useSoloBuilder()
                            } label: {
                                Label("Solo Builder", systemImage: "person")
                            }
                            .buttonStyle(.borderless)
                            .help("Use one implementer agent")

                            Spacer()
                        }

                        ForEach($model.draft.agents) { $agent in
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text(agent.name.isEmpty ? "Agent" : agent.name)
                                        .font(.caption.weight(.semibold))
                                    Spacer()
                                    Button {
                                        model.removeAgent(agent.id)
                                    } label: {
                                        Image(systemName: "minus.circle")
                                    }
                                    .buttonStyle(.borderless)
                                    .help("Remove agent")
                                    .disabled(model.draft.agents.count <= 1)
                                }
                                AgentTextField("Name", text: $agent.name)
                                Picker("Role", selection: $agent.role) {
                                    ForEach(AgentRoleDefaults.roles, id: \.self) { role in
                                        Text(role).tag(role)
                                    }
                                }
                                HStack(spacing: 10) {
                                    AgentTextField("Provider", text: $agent.provider)
                                    AgentTextField("Model", text: $agent.model)
                                }
                                AgentTextEditor("Responsibility", text: $agent.responsibility, minHeight: 68)
                            }
                            .padding(9)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(Color(nsColor: .controlBackgroundColor))
                            )
                        }
                        Button {
                            model.addAgent()
                        } label: {
                            Label("Add Agent", systemImage: "plus")
                        }
                        .buttonStyle(.borderless)
                    }

                    AgentSection("Communication") {
                        if model.draft.communications.isEmpty {
                            Text("No planned communication rules")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        ForEach($model.draft.communications) { $communication in
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text("\(communication.fromAgent.isEmpty ? "From" : communication.fromAgent) -> \(communication.toAgent.isEmpty ? "To" : communication.toAgent)")
                                        .font(.caption.weight(.semibold))
                                    Spacer()
                                    Button {
                                        model.removeCommunication(communication.id)
                                    } label: {
                                        Image(systemName: "minus.circle")
                                    }
                                    .buttonStyle(.borderless)
                                    .help("Remove communication")
                                }
                                HStack(spacing: 10) {
                                    AgentTextField("From", text: $communication.fromAgent)
                                    AgentTextField("To", text: $communication.toAgent)
                                }
                                HStack(spacing: 10) {
                                    AgentTextField("Phase", text: $communication.phase)
                                    AgentTextField("Trigger", text: $communication.trigger)
                                }
                                AgentTextEditor("Message", text: $communication.message, minHeight: 58)
                            }
                            .padding(9)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(Color(nsColor: .controlBackgroundColor))
                            )
                        }
                        Button {
                            model.addCommunication()
                        } label: {
                            Label("Add Communication", systemImage: "plus")
                        }
                        .buttonStyle(.borderless)
                    }

                    AgentSection("Runtime") {
                        AgentPickerRow(
                            "Sandbox",
                            selection: $model.draft.sandboxMode,
                            options: [
                                "workspace-write: scope folder only",
                                "read-only: inspect only",
                                "approval-required: ask before every write",
                            ]
                        )
                        AgentPickerRow(
                            "Approvals",
                            selection: $model.draft.approvalPolicy,
                            options: [
                                "never escalate",
                                "auto-approve safe reads",
                                "ask before escalation",
                            ]
                        )
                        HStack(spacing: 10) {
                            AgentTextField("Minutes", text: $model.draft.maxRuntimeMinutes)
                            AgentTextField("Turns", text: $model.draft.maxTurns)
                        }
                        HStack(spacing: 10) {
                            AgentPickerRow("Shell", selection: $model.draft.shellPermissionMode, options: AgentTaskDraft.permissionOptions)
                            AgentPickerRow("Network", selection: $model.draft.networkPermissionMode, options: AgentTaskDraft.permissionOptions)
                        }
                        HStack(spacing: 10) {
                            AgentPickerRow("Git", selection: $model.draft.gitPermissionMode, options: AgentTaskDraft.permissionOptions)
                            AgentPickerRow("Create files", selection: $model.draft.fileCreatePermissionMode, options: AgentTaskDraft.permissionOptions)
                        }
                        HStack(spacing: 10) {
                            AgentPickerRow("Edit files", selection: $model.draft.fileEditPermissionMode, options: AgentTaskDraft.permissionOptions)
                            AgentPickerRow("Delete files", selection: $model.draft.fileDeletePermissionMode, options: AgentTaskDraft.permissionOptions)
                        }
                        AgentTextField("Allow globs", text: $model.draft.allowedFileGlobs)
                        AgentTextField("Block globs", text: $model.draft.blockedFileGlobs)
                        AgentTextField("Report", text: $model.draft.reportFormat)
                        Toggle("Parallel read-only briefing", isOn: $model.draft.parallelReadOnlyBriefing)
                        Toggle("Parallel execution", isOn: $model.draft.parallelExecution)
                        HStack(spacing: 10) {
                            AgentTextField("Max parallel agents", text: $model.draft.maxParallelAgents)
                            AgentTextField("Temperature", text: $model.draft.agentTemperature)
                        }
                        HStack(spacing: 10) {
                            AgentTextField("Full turn tokens", text: $model.draft.fullTurnMaxTokens)
                            AgentTextField("Delta tokens", text: $model.draft.deltaTurnMaxTokens)
                        }
                        HStack(spacing: 10) {
                            AgentTextField("Read-only tokens", text: $model.draft.readOnlyMaxTokens)
                            AgentTextField("Tool text chars", text: $model.draft.toolResultTextLimit)
                        }
                        HStack(spacing: 10) {
                            AgentTextField("Command chars", text: $model.draft.toolResultCommandLimit)
                            AgentTextField("Value chars", text: $model.draft.toolResultValueLimit)
                        }
                        HStack(spacing: 10) {
                            AgentTextField("List items", text: $model.draft.toolResultListLimit)
                            AgentTextField("Visible files", text: $model.draft.visibleFilesFullLimit)
                        }
                        AgentTextField("Delta visible files", text: $model.draft.visibleFilesDeltaLimit)
                    }
                }
                .padding(14)
            }
            Divider()
            footer
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Agent Task")
                    .font(.system(size: 16, weight: .semibold))
                Text(model.status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if model.isRunning {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var copyRow: some View {
        HStack {
            Button {
                model.copyFromLastTask()
            } label: {
                Label("Copy from Last Task", systemImage: "doc.on.doc")
            }
            .disabled(model.isRunning)
            .help("Prefill all fields from the last started task")
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Button("Preview Spec") { model.previewSpec() }
                .disabled(model.isRunning)
            if !model.errorText.isEmpty {
                Text(model.errorText)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
            Spacer()
            Button("Cancel") { model.cancel() }
            Button("Start Task") { model.start() }
                .disabled(model.isRunning)
                .keyboardShortcut(.return, modifiers: [.command])
        }
        .padding(12)
    }

    private var runView: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Run")
                    .font(.system(size: 16, weight: .semibold))
                Spacer()
                if !model.runDir.isEmpty {
                    Text(model.runDir)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            Divider()

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 6) {
                        if model.runLines.isEmpty {
                            Text("No run output yet")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.top, 28)
                        } else {
                            ForEach(Array(model.runLines.enumerated()), id: \.offset) { index, line in
                                Text(line)
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .id(index)
                            }
                        }
                    }
                    .padding(12)
                }
                .onChange(of: model.scrollToken) { _ in
                    if !model.runLines.isEmpty {
                        proxy.scrollTo(model.runLines.count - 1, anchor: .bottom)
                    }
                }
            }

            if !model.finalText.isEmpty {
                Divider()
                ScrollView {
                    Text(model.finalText)
                        .font(.system(size: 13))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                }
                .frame(minHeight: 120, idealHeight: 180)
            }
        }
    }
}

private struct AgentSection<Content: View>: View {
    private let title: String
    private let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            content
        }
    }
}

private struct AgentTextField: View {
    let label: String
    @Binding var text: String

    init(_ label: String, text: Binding<String>) {
        self.label = label
        self._text = text
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            TextField(label, text: $text)
                .foregroundStyle(AgentTaskPalette.inputText)
                .tint(AgentTaskPalette.inputText)
                .textFieldStyle(.roundedBorder)
        }
    }
}

private struct AgentPickerRow: View {
    let label: String
    @Binding var selection: String
    let options: [String]

    init(_ label: String, selection: Binding<String>, options: [String]) {
        self.label = label
        self._selection = selection
        self.options = options
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Picker(label, selection: $selection) {
                ForEach(options, id: \.self) { option in
                    Text(option).tag(option)
                }
            }
            .labelsHidden()
        }
    }
}

private struct AgentTextEditor: View {
    let label: String
    @Binding var text: String
    var minHeight: CGFloat

    init(_ label: String, text: Binding<String>, minHeight: CGFloat) {
        self.label = label
        self._text = text
        self.minHeight = minHeight
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.system(size: 13))
                .foregroundStyle(AgentTaskPalette.inputText)
                .background(AgentTaskPalette.inputBackground)
                .frame(minHeight: minHeight)
                .overlay(
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                )
        }
    }
}

private enum AgentTaskPalette {
    static let inputText = Color(nsColor: NSColor.textColor)
    static let inputBackground = Color(nsColor: NSColor.textBackgroundColor)
}
