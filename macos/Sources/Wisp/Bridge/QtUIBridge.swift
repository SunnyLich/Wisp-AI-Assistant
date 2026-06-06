import Foundation

/// Parent-side supervisor for the optional Python/Qt UI process.
///
/// This is intentionally simpler than BrainClient: commands are fire-and-forget
/// window actions. The child writes status/error events for logs, while Swift
/// remains responsible for the app lifecycle and native macOS surfaces.
@MainActor
final class QtUIBridge {

    struct Config {
        var pythonExecutable: URL
        var hostScript: URL
        var repoRoot: URL
    }

    private let config: Config
    private var process: Process?
    private var stdinHandle: FileHandle?
    private var stdoutBuffer = Data()

    /// Async notification of the child's `{"event": ...}` lines. Commands are
    /// still fire-and-forget; this lets the app correct an optimistic status when
    /// the child reports `ui.error` (e.g. a window failed to open).
    var onEvent: ((_ event: String, _ payload: [String: Any]) -> Void)?

    init(config: Config) {
        self.config = config
    }

    func showAgentHistory() throws {
        try send("ui.show_agent_history")
    }

    func reloadConfig() throws {
        try send("ui.reload_config")
    }

    func shutdown() {
        guard let proc = process, proc.isRunning else { return }
        try? writeLine(["method": "__shutdown__", "params": [:]])
        stdinHandle?.closeFile()
        // Bounded wait so quitting Wisp never hangs on a child stuck in a modal
        // dialog. Force-terminate if it doesn't exit on its own in time.
        let deadline = Date().addingTimeInterval(2.0)
        while proc.isRunning, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if proc.isRunning { proc.terminate() }
        process = nil
        stdinHandle = nil
    }

    private func send(_ method: String, _ params: [String: Any] = [:]) throws {
        try ensureStarted()
        try writeLine(["method": method, "params": params])
    }

    private func ensureStarted() throws {
        if process?.isRunning == true { return }

        let proc = Process()
        proc.executableURL = config.pythonExecutable
        proc.arguments = [config.hostScript.path]
        proc.currentDirectoryURL = config.repoRoot

        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["WISP_QT_UI_HOST"] = "1"
        env["WISP_REPO_ROOT"] = config.repoRoot.path
        let existingPath = env["PYTHONPATH"].flatMap { $0.isEmpty ? nil : $0 }
        env["PYTHONPATH"] = [config.repoRoot.path, existingPath].compactMap { $0 }.joined(separator: ":")
        proc.environment = env

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        proc.standardInput = stdinPipe
        proc.standardOutput = stdoutPipe
        proc.standardError = stderrPipe

        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor in self?.ingest(data) }
        }
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty, let s = String(data: data, encoding: .utf8) {
                NSLog("[wisp qt-ui stderr] %@", s.trimmingCharacters(in: .whitespacesAndNewlines))
            }
        }
        proc.terminationHandler = { [weak self] terminated in
            Task { @MainActor in
                guard let self, self.process === terminated else { return }
                self.process = nil
                self.stdinHandle = nil
                NSLog("[wisp qt-ui] exited with status %d", terminated.terminationStatus)
            }
        }

        do {
            try proc.run()
        } catch {
            throw BrainError.spawnFailed("Qt UI host: \(error.localizedDescription)")
        }

        process = proc
        stdinHandle = stdinPipe.fileHandleForWriting
    }

    private func writeLine(_ object: [String: Any]) throws {
        guard let stdinHandle else { throw BrainError.notRunning }
        var data = try JSONSerialization.data(withJSONObject: object, options: [])
        data.append(0x0A)
        try stdinHandle.write(contentsOf: data)
    }

    /// Accumulate child stdout bytes and route each `\n`-delimited line. The host
    /// writes one JSON object per line (`ui.ready` / `ui.ok` / `ui.error`); any
    /// non-JSON line is mirrored to the log.
    private func ingest(_ chunk: Data) {
        stdoutBuffer.append(chunk)
        while let nl = stdoutBuffer.firstIndex(of: 0x0A) {
            let line = stdoutBuffer.subdata(in: stdoutBuffer.startIndex..<nl)
            stdoutBuffer.removeSubrange(stdoutBuffer.startIndex...nl)
            route(line)
        }
    }

    private func route(_ line: Data) {
        let trimmed = String(data: line, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !trimmed.isEmpty else { return }

        guard
            let object = try? JSONSerialization.jsonObject(with: line),
            let msg = object as? [String: Any],
            let event = msg["event"] as? String
        else {
            NSLog("[wisp qt-ui] %@", trimmed)
            return
        }

        if event == "ui.error" {
            let detail = (msg["error"] as? String) ?? "unknown error"
            NSLog("[wisp qt-ui] error: %@", detail)
        } else {
            NSLog("[wisp qt-ui] %@", trimmed)
        }
        onEvent?(event, msg)
    }
}
