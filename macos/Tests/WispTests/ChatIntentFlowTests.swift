import XCTest
import Foundation

final class ChatIntentFlowTests: XCTestCase {

    func testChatPanelKeepsConversationAndStreamingContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Chat/ChatPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "final class ChatPanel: NSPanel",
            "func showChat(startNew: Bool = false, autoMessage: String? = nil)",
            "model.sendAutoMessage(autoMessage)",
            "func hasConversationHistory() -> Bool",
            "func recordExchange(user: String, assistant: String)",
            "func beginUserMessage(_ text: String) -> [[String: String]]",
            "func appendAssistantChunk(_ chunk: String)",
            "func finishAssistant(_ finalText: String? = nil)",
            "func failAssistant(_ message: String)",
            "conversations.contains { $0.messages.isEmpty == false }",
            "guard !trimmed.isEmpty, !isStreaming, hasConversationHistory else { return }",
            "conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: \"user\", content: text))",
            "conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: \"assistant\", content: chunk))",
            "activeConversation.messages.map { [\"role\": $0.role, \"content\": $0.content] }",
        ] {
            XCTAssertTrue(source.contains(expected), "ChatPanel is missing \(expected).")
        }
    }

    func testIntentPanelKeepsCallerRowsCustomPromptAndKeyboardContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Input/IntentPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "struct IntentSelection",
            "var caller: CallerConfig",
            "var intent: IntentConfig?",
            "var isCustom: Bool",
            "model.onRow = { [weak self] row in self?.choose(row) }",
            "model.onCustomSubmit = { [weak self] text in self?.submitCustom(text) }",
            "model.onCancel = { [weak self] in self?.cancel() }",
            "let rows = caller.intentRows()",
            "model.configure(title: caller.label.isEmpty ? \"Wisp\" : caller.label, rows: rows)",
            "if row.isCustom",
            "finish(prompt: row.prompt, intent: intent(for: row), isCustom: false)",
            "finish(prompt: trimmed, intent: nil, isCustom: true)",
            "NSEvent.addLocalMonitorForEvents(matching: .keyDown)",
            "if event.keyCode == 53",
            "event.charactersIgnoringModifiers?.lowercased()",
            "currentCaller.intents.first { $0.key.caseInsensitiveCompare(row.key) == .orderedSame }",
            "rows.append(",
            "label: \"Custom prompt\"",
            "key: customKey.isEmpty ? \"s\" : customKey",
        ] {
            XCTAssertTrue(source.contains(expected), "IntentPanel is missing \(expected).")
        }
    }

    func testHotkeyControllerKeepsCallerIntentDispatchContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Input/HotkeyController.swift"),
            encoding: .utf8
        )

        for expected in [
            "case caller(Int)",
            "case snip",
            "case addContext",
            "case clearContext",
            "case voiceStart",
            "case voiceStop",
            "definitions = callers.enumerated().compactMap",
            "HotkeyDefinition.parse(caller.hotkey, callerIndex: index, label: caller.label)",
            "HotkeyDefinition.parse($0.hotkey, action: .snip, label: \"Snip\")",
            "HotkeyDefinition.parse($0, action: .addContext, label: \"Add context\")",
            "HotkeyDefinition.parse($0, action: .clearContext, label: \"Clear context\")",
            "HotkeyDefinition.parse($0, action: .voiceStart, label: \"Voice\")",
            "AXIsProcessTrustedWithOptions(options)",
            "CGEvent.tapCreate(",
            "guard typeRawValue != CGEventType.keyDown.rawValue || !isRepeat else { return }",
            "if definition.action == .voiceStart",
            "onTrigger(.voiceStop)",
            "onTrigger(definition.action)",
        ] {
            XCTAssertTrue(source.contains(expected), "HotkeyController is missing \(expected).")
        }
    }

    func testAppDelegateKeepsChatIntentAndPromptStreamingWired() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "intentPanel = IntentPanel { [weak self] selection in",
            "Task { await self?.runIntent(selection) }",
            "chatPanel = ChatPanel { [weak self] text in",
            "Task { await self?.runChatMessage(text) }",
            "case .caller(let callerIndex):",
            "self?.showIntentPicker(callerIndex: callerIndex)",
            "pendingIntentContext = PendingNativeContext(",
            "intentPanel?.show(caller: caller)",
            "private func runIntent(_ selection: IntentSelection) async",
            "promptPanel?.setPrompt(selection.prompt)",
            "if selection.caller.pasteBack",
            "await runPasteBack(selection.prompt, context: pendingContext)",
            "await runPrompt(",
            "contextSnapshot: pendingContext?.snapshot",
            "let params = try await paramsForPrompt(text, mode: mode, caller: caller, contextSnapshot: contextSnapshot)",
            "private func paramsForPrompt(",
            ") async throws -> [String: Any]",
            "let willAttachScreenshot = mode == .queryScreen || policy.contextScreenshot == .auto",
            "\"include_active_document\": policy.contextDocuments && !willAttachScreenshot",
            "private func showNativeChat(new: Bool)",
            "let autoMessage = new ? nil : chatAutoElaboratePrompt()",
            "chatPanel?.showChat(startNew: new, autoMessage: autoMessage)",
            "CHAT_AUTO_ELABORATE",
            "CHAT_ELABORATE_PROMPT",
            "private func runChatMessage(_ text: String) async",
            "let messages = chatPanel?.beginUserMessage(text) ?? [[\"role\": \"user\", \"content\": text]]",
            "client.stream(\"brain.chat\", [\"messages\": messages])",
            "chatPanel?.appendAssistantChunk(chunk)",
            "chatPanel?.finishAssistant(finalText)",
            "chatPanel?.failAssistant(\"No reply from model. Check model name or API key in Settings.\")",
            "chatPanel?.recordExchange(user: text, assistant: assembled)",
            "client.stream(mode.method, params)",
            "responseBubble?.appendChunk(chunk)",
            "promptPanel?.setResponse(assembled)",
            "responseBubble?.finish()",
        ] {
            XCTAssertTrue(source.contains(expected), "AppDelegate chat/intent wiring is missing \(expected).")
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
