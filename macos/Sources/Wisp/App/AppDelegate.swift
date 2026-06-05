import AppKit

/// Phase-1/2 app wiring: bring up the menubar item and the floating overlay, then
/// perform the brain handshake (spawn the Python sidecar, `ping` it, stream a
/// `brain.echo`) and surface the result in the menu. This is the runnable proof
/// that the Swift host can drive the verified Python seam.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var statusController: StatusItemController?
    private var overlay: OverlayPanel?
    private var promptPanel: PromptPanel?
    private var hotkey: HotkeyController?
    private let nativeContext = NativeContextController()
    private let screenCapture = ScreenCaptureController()
    private let audioRecorder = AudioRecorder()
    private let audioPlayer = AudioPlayer()
    private var brain: BrainClient?
    private var qtUI: QtUIBridge?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let client = BrainClient(config: BrainLocator.resolve())
        brain = client
        if let qtConfig = QtUILocator.resolve() {
            let bridge = QtUIBridge(config: qtConfig)
            bridge.onEvent = { [weak self] event, payload in
                self?.handleQtUIEvent(event, payload)
            }
            qtUI = bridge
        } else {
            NSLog("[wisp] Qt UI host unavailable; WISP_REPO_ROOT/WISP_BRAIN_PYTHON not resolved")
        }

        let prompt = PromptPanel { [weak self] text, mode in
            Task { await self?.runPrompt(text, mode: mode) }
        }
        promptPanel = prompt

        let panel = OverlayPanel { [weak self] in
            self?.showPrompt()
        }
        panel.orderFrontRegardless()
        overlay = panel

        let status = StatusItemController(
            onShowPrompt: { [weak self] in self?.showPrompt() },
            onRunEchoSmoke: { [weak self] in self?.runEchoSmoke() },
            onShowContext: { [weak self] in self?.showContextSnapshot() },
            onShowPermissions: { [weak self] in self?.showPermissionSnapshot() },
            onCaptureScreen: { [weak self] in self?.captureScreenSmoke() },
            onOpenRunLogs: { [weak self] in self?.openRunLogs() },
            onShowSettings: { [weak self] in self?.showQtSettings() },
            onShowChat: { [weak self] in self?.showQtChat(new: false) },
            onShowNewChat: { [weak self] in self?.showQtChat(new: true) },
            onShowMemory: { [weak self] in self?.showQtMemory() },
            onShowPluginManager: { [weak self] in self?.showQtPluginManager() },
            onShowAgentTask: { [weak self] in self?.showQtAgentTask() },
            onShowAgentHistory: { [weak self] in self?.showQtAgentHistory() },
            onStartVoiceQuery: { [weak self] in self?.startVoiceQuery() },
            onStopVoiceQuery: { [weak self] in self?.stopVoiceQuery() },
            onSpeakResponse: { [weak self] in self?.speakLastResponse() },
            onRememberPrompt: { [weak self] in self?.rememberPrompt() },
            onSearchMemory: { [weak self] in self?.searchMemory() },
            onToggleOverlay: { [weak self] in self?.overlay?.toggleVisibility() },
            onRetryHotkey: { [weak self] in self?.installHotkey(promptForPermission: true) }
        )
        statusController = status

        let hotkey = HotkeyController { [weak self] in
            self?.showPrompt()
        }
        self.hotkey = hotkey
        installHotkey(promptForPermission: true)

        Task { await self.handshake(client, status: status, panel: panel) }
    }

    func applicationWillTerminate(_ notification: Notification) {
        qtUI?.shutdown()
        let client = brain
        Task { await client?.shutdown() }
    }

    /// Spawn + ping + a streamed echo. Mirrors the Python `test_brain_host.py`
    /// flow, but driven from Swift to validate the real transport end to end.
    private func handshake(_ client: BrainClient, status: StatusItemController, panel: OverlayPanel) async {
        do {
            panel.setState(.thinking)
            let pong = try await client.call("ping", ["value": "hello-from-swift"])
            let pid = (pong?["pid"] as? Int).map(String.init) ?? "?"
            status.setBrainStatus("ok (pid \(pid))")

            panel.setState(.speaking)
            var assembled = ""
            for try await item in client.stream("brain.echo", ["text": "the brain seam works"]) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    if let text = data?["text"] as? String { assembled += text }
                case .result(let result):
                    assembled = (result?["text"] as? String) ?? assembled
                default:
                    break
                }
            }
            NSLog("[wisp] echo stream assembled: %@", assembled)
            panel.setState(.idle)
        } catch {
            status.setBrainStatus("error: \(error)")
            panel.setState(.idle)
            NSLog("[wisp] brain handshake failed: %@", String(describing: error))
        }
    }

    private func showPrompt() {
        promptPanel?.showPrompt()
    }

    private func runEchoSmoke() {
        promptPanel?.showPrompt()
        Task { await self.runPrompt("tray smoke test", mode: .echo) }
    }

    private func installHotkey(promptForPermission: Bool) {
        guard let hotkey else { return }
        let result = hotkey.start(promptForPermission: promptForPermission)
        statusController?.setHotkeyStatus(result.statusText)
    }

    private func showContextSnapshot() {
        let snapshot = nativeContext.snapshot(promptForAccessibility: true)
        promptPanel?.showPrompt()
        promptPanel?.setResponse(snapshot.displayText)
        statusController?.setBrainStatus("context snapshot ok")
        NSLog("[wisp] context snapshot: %@", snapshot.logSummary)
    }

    private func showPermissionSnapshot() {
        let snapshot = nativeContext.permissions(promptForAccessibility: true)
        promptPanel?.showPrompt()
        promptPanel?.setResponse(snapshot.displayText)
        statusController?.setHotkeyStatus(snapshot.accessibilityTrusted ? "Ctrl-Option-Space ready" : "Accessibility permission needed")
        NSLog("[wisp] permission snapshot: ax=%@ screen=%@ mic=%@",
              String(describing: snapshot.accessibilityTrusted),
              String(describing: snapshot.screenRecordingTrusted),
              snapshot.microphoneStatus)
    }

    private func captureScreenSmoke() {
        promptPanel?.showPrompt()
        overlay?.setState(.thinking)

        do {
            let result = try screenCapture.captureMainDisplay(promptForPermission: true)
            promptPanel?.setResponse(result.displayText)
            overlay?.setState(.idle)
            statusController?.setBrainStatus("screen capture ok")
            NSLog("[wisp] screen capture saved: %@ (%dx%d)", result.url.path, result.width, result.height)
        } catch {
            promptPanel?.failRequest(String(describing: error))
            overlay?.setState(.idle)
            statusController?.setBrainStatus("screen capture error")
            NSLog("[wisp] screen capture failed: %@", String(describing: error))
        }
    }

    private func openRunLogs() {
        if !RunLogLocator.openLogDirectory() {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("Run log directory is unavailable. Launch with scripts/macos_phase1_validate.sh --run to enable this.")
            NSLog("[wisp] run log directory unavailable")
        }
    }

    private func withQtUI(_ label: String, action: (QtUIBridge) throws -> Void) {
        guard let qtUI else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("The Qt UI host is unavailable. Launch through Start Wisp.command so the Python .venv and repo path are exported.")
            statusController?.setBrainStatus("Qt UI unavailable")
            NSLog("[wisp] Qt UI action unavailable: %@", label)
            return
        }

        do {
            try action(qtUI)
            statusController?.setBrainStatus("\(label) opened")
        } catch {
            promptPanel?.showPrompt()
            promptPanel?.failRequest(String(describing: error))
            statusController?.setBrainStatus("\(label) error")
            NSLog("[wisp] Qt UI action failed (%@): %@", label, String(describing: error))
        }
    }

    /// Surface asynchronous status from the Qt UI host. Commands are sent
    /// fire-and-forget, so a window that fails to open only reports back here via
    /// a `ui.error` event — correct the optimistic "opened" status when it does.
    private func handleQtUIEvent(_ event: String, _ payload: [String: Any]) {
        switch event {
        case "ui.error":
            let detail = (payload["error"] as? String) ?? "unknown error"
            promptPanel?.showPrompt()
            promptPanel?.setResponse("Qt UI host error:\n\(detail)")
            statusController?.setBrainStatus("Qt UI error")
        case "ui.ready":
            NSLog("[wisp] Qt UI host ready")
        default:
            break
        }
    }

    private func showQtSettings() {
        withQtUI("Settings") { try $0.showSettings() }
    }

    private func showQtChat(new: Bool) {
        withQtUI(new ? "New chat" : "Chat") { try $0.showChat(new: new) }
    }

    private func showQtMemory() {
        withQtUI("Memory") { try $0.showMemory() }
    }

    private func showQtPluginManager() {
        withQtUI("Plugin manager") { try $0.showPluginManager() }
    }

    private func showQtAgentTask() {
        withQtUI("Agent task") { try $0.showAgentTask() }
    }

    private func showQtAgentHistory() {
        withQtUI("Agent history") { try $0.showAgentHistory() }
    }

    private func startVoiceQuery() {
        promptPanel?.showPrompt()
        overlay?.setState(.listening)
        promptPanel?.setResponse("Listening...")
        statusController?.setBrainStatus("voice recording")

        Task {
            do {
                let url = try await audioRecorder.start()
                promptPanel?.setResponse("Listening...\n\nRecording to:\n\(url.path)")
                NSLog("[wisp] voice query recording started: %@", url.path)
            } catch {
                overlay?.setState(.idle)
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("voice error")
                NSLog("[wisp] voice query start failed: %@", String(describing: error))
            }
        }
    }

    private func stopVoiceQuery() {
        promptPanel?.showPrompt()
        overlay?.setState(.thinking)
        promptPanel?.setResponse("Stopping recording and transcribing...")
        statusController?.setBrainStatus("transcribing")

        Task {
            do {
                let recording = try audioRecorder.stop()
                promptPanel?.setResponse(recording.displayText + "\n\nTranscribing...")
                let transcript = try await transcribe(recording.url)
                guard !transcript.isEmpty else {
                    overlay?.setState(.idle)
                    promptPanel?.setResponse(recording.displayText + "\n\nNo speech detected.")
                    statusController?.setBrainStatus("voice empty")
                    return
                }

                promptPanel?.setPrompt(transcript)
                promptPanel?.setResponse("Transcript:\n\(transcript)\n\nQuerying...")
                NSLog("[wisp] voice transcript: %@", transcript)
                await self.runPrompt(transcript, mode: .query)
            } catch {
                overlay?.setState(.idle)
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("voice error")
                NSLog("[wisp] voice query stop failed: %@", String(describing: error))
            }
        }
    }

    private func speakLastResponse() {
        let text = promptPanel?.currentResponse().trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("No response to speak yet.")
            return
        }

        promptPanel?.showPrompt()
        statusController?.setBrainStatus("tts synthesizing")
        overlay?.setState(.speaking)

        Task {
            do {
                let url = try await synthesizeSpeech(text)
                try audioPlayer.play(url: url)
                statusController?.setBrainStatus("tts playing")
                promptPanel?.setResponse("\(text)\n\nSpeaking:\n\(url.path)")
            } catch {
                overlay?.setState(.idle)
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("tts error")
                NSLog("[wisp] tts failed: %@", String(describing: error))
            }
        }
    }

    private func transcribe(_ url: URL) async throws -> String {
        guard let client = brain else { throw BrainError.notRunning }
        let result = try await client.call("brain.transcribe", ["pcm_path": url.path], timeout: .seconds(120))
        return result?["text"] as? String ?? ""
    }

    private func synthesizeSpeech(_ text: String) async throws -> URL {
        guard let client = brain else { throw BrainError.notRunning }
        let result = try await client.call("brain.tts.synthesize", ["text": text], timeout: .seconds(120))
        guard let path = result?["path"] as? String else {
            throw BrainError.malformedResponse
        }
        if let bytes = result?["bytes"] as? Int, bytes == 0 {
            throw BrainError.remote("TTS produced no audio; check TTS_PROVIDER and API keys")
        }
        return URL(fileURLWithPath: path)
    }

    private func rememberPrompt() {
        let text = promptPanel?.currentPrompt().trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("No prompt text to remember.")
            return
        }

        promptPanel?.showPrompt()
        promptPanel?.setResponse("Saving memory...")
        statusController?.setBrainStatus("memory saving")

        Task {
            do {
                guard let client = brain else { throw BrainError.notRunning }
                let result = try await client.call("brain.memory.add", ["text": text], timeout: .seconds(60))
                let stored = result?["text"] as? String ?? text
                promptPanel?.setResponse("Remembered:\n\(stored)")
                statusController?.setBrainStatus("memory saved")
                NSLog("[wisp] memory saved: %@", stored)
            } catch {
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("memory error")
                NSLog("[wisp] memory save failed: %@", String(describing: error))
            }
        }
    }

    private func searchMemory() {
        let text = promptPanel?.currentPrompt().trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("Type a memory search query in the prompt first.")
            return
        }

        promptPanel?.showPrompt()
        promptPanel?.setResponse("Searching memory...")
        statusController?.setBrainStatus("memory searching")

        Task {
            do {
                guard let client = brain else { throw BrainError.notRunning }
                let result = try await client.call("brain.memory.search", ["query": text, "top_k": 5], timeout: .seconds(60))
                let found = (result?["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
                promptPanel?.setResponse(found?.isEmpty == false ? found! : "No relevant memory found.")
                statusController?.setBrainStatus("memory searched")
                NSLog("[wisp] memory searched for: %@", text)
            } catch {
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("memory error")
                NSLog("[wisp] memory search failed: %@", String(describing: error))
            }
        }
    }

    private func runPrompt(_ text: String, mode: PromptMode) async {
        guard let client = brain else {
            promptPanel?.failRequest("brain client is not available")
            statusController?.setBrainStatus("error: missing client")
            return
        }

        promptPanel?.beginRequest(mode: mode)
        overlay?.setState(mode == .echo ? .speaking : .thinking)
        statusController?.setBrainStatus("\(mode.rawValue.lowercased()) running")

        do {
            let params = try paramsForPrompt(text, mode: mode)
            var assembled = ""

            for try await item in client.stream(mode.method, params) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    if let chunk = data?["text"] as? String {
                        assembled += chunk
                        promptPanel?.setResponse(assembled)
                    }
                case .result(let result):
                    if let finalText = result?["text"] as? String {
                        assembled = finalText
                        promptPanel?.setResponse(finalText)
                    }
                default:
                    break
                }
            }

            if assembled.isEmpty {
                promptPanel?.setResponse("(empty response)")
            }
            promptPanel?.finishRequest()
            overlay?.setState(.idle)
            statusController?.setBrainStatus("\(mode.rawValue.lowercased()) ok")
            NSLog("[wisp] %@ prompt completed: %@", mode.rawValue, assembled)
        } catch {
            promptPanel?.failRequest(String(describing: error))
            overlay?.setState(.idle)
            statusController?.setBrainStatus("error: \(error)")
            NSLog("[wisp] %@ prompt failed: %@", mode.rawValue, String(describing: error))
        }
    }

    private func paramsForPrompt(_ text: String, mode: PromptMode) throws -> [String: Any] {
        switch mode {
        case .echo:
            return ["text": text, "chunk_size": 1, "delay": 0.02]
        case .query, .queryScreen:
            let snapshot = nativeContext.snapshot(promptForAccessibility: false)
            var params: [String: Any] = [
                "intent_prompt": text,
                "ambient_text": snapshot.ambientText,
                "use_tools": false,
            ]
            if let selected = snapshot.selectedText, !selected.isEmpty {
                params["selected"] = selected
            }
            if mode == .queryScreen {
                let capture = try screenCapture.captureMainDisplay(promptForPermission: true)
                let data = try Data(contentsOf: capture.url)
                params["screenshot_b64"] = data.base64EncodedString()
                params["ambient_text"] = "\(snapshot.ambientText)\n\nScreenshot saved: \(capture.url.path)"
                NSLog("[wisp] query screenshot attached: %@ (%dx%d)", capture.url.path, capture.width, capture.height)
            }
            return params
        }
    }
}
