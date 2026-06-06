import Foundation

/// Parent-side supervisor + transport for the Python brain sidecar — the Swift
/// counterpart of `core/macos_helper/client.py` (and the `BrainSidecar` test
/// harness in `macos/brain/tests`). Spawns `python -m wisp_brain.host`, ships
/// requests on its stdin, and reads responses/events off its stdout on a
/// background readability handler. Responses are correlated by `id`; streaming
/// methods get an `AsyncThrowingStream` of `BrainStreamItem`s.
///
/// An `actor` so all mutable routing state (id counter, pending maps) is
/// serialized without manual locks; the stdout handler hops back in via `Task`.
actor BrainClient {

    struct Config {
        /// Python interpreter. In the shipped app this is the embedded
        /// python-build-standalone at `Wisp.app/Contents/Resources/python-runtime/bin/python3`.
        var pythonExecutable: URL
        /// Directory that contains the `wisp_brain` package (`macos/brain`).
        var brainDirectory: URL
        /// Extra entries prepended to PYTHONPATH (e.g. the repo root for `core`).
        var extraPythonPath: [URL] = []
    }

    private let config: Config
    private var process: Process?
    private var stdinHandle: FileHandle?
    private var readBuffer = Data()

    private var nextID = 1
    private var unaryPending: [Int: CheckedContinuation<[String: Any]?, Error>] = [:]
    private var streamPending: [Int: AsyncThrowingStream<BrainStreamItem, Error>.Continuation] = [:]

    init(config: Config) {
        self.config = config
    }

    // MARK: - Lifecycle

    private var isAlive: Bool { process?.isRunning ?? false }

    /// Spawn the sidecar if it isn't already running. Idempotent.
    func ensureStarted() throws {
        if isAlive { return }

        let proc = Process()
        proc.executableURL = config.pythonExecutable
        proc.arguments = ["-m", "wisp_brain.host"]
        proc.currentDirectoryURL = config.brainDirectory

        var env = RunLogLocator.environmentByResolvingLogDirectory(
            environment: ProcessInfo.processInfo.environment,
            resourceURL: Bundle.main.resourceURL
        )
        env["PYTHONUNBUFFERED"] = "1"
        if !config.extraPythonPath.isEmpty {
            let joined = config.extraPythonPath.map(\.path).joined(separator: ":")
            env["PYTHONPATH"] = [joined, env["PYTHONPATH"]].compactMap { $0 }.joined(separator: ":")
        }
        proc.environment = env

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        proc.standardInput = stdinPipe
        proc.standardOutput = stdoutPipe
        proc.standardError = stderrPipe

        // Route stdout lines into the actor for framing/routing.
        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            Task { await self?.ingest(chunk) }
        }
        // Mirror sidecar stderr (its logs / tracebacks) to ours for debugging.
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty, let s = String(data: data, encoding: .utf8) {
                FileHandle.standardError.write(Data(s.utf8))
            }
        }
        proc.terminationHandler = { [weak self] _ in
            Task { await self?.handleTermination() }
        }

        do {
            try proc.run()
        } catch {
            throw BrainError.spawnFailed(error.localizedDescription)
        }
        self.process = proc
        self.stdinHandle = stdinPipe.fileHandleForWriting
    }

    /// Ask the sidecar to exit, then wait briefly.
    func shutdown() {
        guard let proc = process, proc.isRunning else { return }
        let msg = ["id": 0, "method": "__shutdown__"] as [String: Any]
        if let line = try? BrainProtocol.encodeLine(msg) {
            try? stdinHandle?.write(contentsOf: line)
        }
        // Bounded wait so quitting Wisp never hangs on a wedged sidecar (stuck
        // mid-LLM-call, blocked socket, etc.). Force-terminate if it overstays.
        let deadline = Date().addingTimeInterval(2.0)
        while proc.isRunning, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if proc.isRunning { proc.terminate() }
    }

    private func handleTermination() {
        // Sidecar died: fail every in-flight call so nothing hangs (mirrors the
        // Python client's read-loop teardown).
        let err = BrainError.notRunning
        for (_, cont) in unaryPending { cont.resume(throwing: err) }
        unaryPending.removeAll()
        for (_, cont) in streamPending { cont.finish(throwing: err) }
        streamPending.removeAll()
        process = nil
        stdinHandle = nil
        readBuffer.removeAll()
    }

    // MARK: - Sending

    /// Unary request: await the single response `result`. Restarts the sidecar
    /// on demand (lazy spawn / restart-on-death).
    func call(_ method: String, _ params: [String: Any] = [:], timeout: Duration = .seconds(30)) async throws -> [String: Any]? {
        try ensureStarted()
        let id = nextID; nextID += 1
        let req = BrainProtocol.request(id: id, method: method, params: params)
        let line = try BrainProtocol.encodeLine(req)

        let timeoutTask = Task { [weak self] in
            do {
                try await Task.sleep(for: timeout)
                guard !Task.isCancelled else { return }
                await self?.timeoutUnary(id: id, method: method)
            } catch {
                // The normal response path cancels this task.
            }
        }
        defer { timeoutTask.cancel() }

        return try await withCheckedThrowingContinuation { cont in
            registerUnary(id: id, cont: cont, line: line)
        }
    }

    private func registerUnary(id: Int, cont: CheckedContinuation<[String: Any]?, Error>, line: Data) {
        unaryPending[id] = cont
        do {
            try stdinHandle?.write(contentsOf: line)
        } catch {
            unaryPending[id] = nil
            cont.resume(throwing: BrainError.notRunning)
        }
    }

    private func timeoutUnary(id: Int, method: String) {
        guard let cont = unaryPending.removeValue(forKey: id) else { return }
        cont.resume(throwing: BrainError.remote("\(method) timed out"))
    }

    /// Streaming request: yields id-tagged `.event` partials, then one terminal
    /// `.result`, then finishes. Used for `brain.query` / `brain.echo`.
    nonisolated func stream(_ method: String, _ params: [String: Any] = [:]) -> AsyncThrowingStream<BrainStreamItem, Error> {
        AsyncThrowingStream { continuation in
            Task {
                await self.startStream(method, params, continuation: continuation)
            }
        }
    }

    private func startStream(
        _ method: String,
        _ params: [String: Any],
        continuation: AsyncThrowingStream<BrainStreamItem, Error>.Continuation
    ) {
        var streamID: Int?
        do {
            try ensureStarted()
            let id = nextID; nextID += 1
            streamID = id
            continuation.onTermination = { [weak self] _ in
                Task { await self?.cancelStreamIfPending(id) }
            }
            streamPending[id] = continuation

            let req = BrainProtocol.request(id: id, method: method, params: params)
            let line = try BrainProtocol.encodeLine(req)
            try writeLine(line)
        } catch {
            if let streamID {
                streamPending.removeValue(forKey: streamID)
            }
            continuation.finish(throwing: error)
        }
    }

    private func writeLine(_ line: Data) throws {
        guard let stdinHandle else { throw BrainError.notRunning }
        try stdinHandle.write(contentsOf: line)
    }

    /// Request cooperative cancellation of an in-flight stream by its id.
    func cancel(streamID id: Int) async {
        cancelStreamIfPending(id)
    }

    private func cancelStreamIfPending(_ id: Int) {
        guard streamPending.removeValue(forKey: id) != nil else { return }
        sendCancel(for: id)
    }

    private func sendCancel(for targetID: Int) {
        guard let stdinHandle else { return }
        let id = nextID; nextID += 1
        let req = BrainProtocol.request(id: id, method: "brain.cancel", params: ["target": targetID])
        if let line = try? BrainProtocol.encodeLine(req) {
            try? stdinHandle.write(contentsOf: line)
        }
    }

    // MARK: - Receiving

    /// Accumulate stdout bytes and route each complete `\n`-delimited line.
    private func ingest(_ chunk: Data) {
        readBuffer.append(chunk)
        while let nl = readBuffer.firstIndex(of: 0x0A) {
            let line = readBuffer.subdata(in: readBuffer.startIndex..<nl)
            readBuffer.removeSubrange(readBuffer.startIndex...nl)
            guard let msg = BrainProtocol.decodeLine(line) else { continue }
            route(msg)
        }
    }

    private func route(_ msg: [String: Any]) {
        // Event: {"event": name, "id": Int?, "data": ...}
        if let event = msg["event"] as? String {
            if let id = msg["id"] as? Int, let cont = streamPending[id] {
                cont.yield(.event(name: event, data: msg["data"] as? [String: Any]))
            }
            return
        }
        // Response: {"id": Int, "ok": Bool, ...}
        guard let id = msg["id"] as? Int else { return }
        let ok = (msg["ok"] as? Bool) ?? false

        if let cont = streamPending.removeValue(forKey: id) {
            if ok {
                cont.yield(.result(msg["result"] as? [String: Any]))
                cont.finish()
            } else {
                cont.finish(throwing: BrainError.remote((msg["error"] as? String) ?? "unknown"))
            }
            return
        }
        if let cont = unaryPending.removeValue(forKey: id) {
            if ok {
                cont.resume(returning: msg["result"] as? [String: Any])
            } else {
                cont.resume(throwing: BrainError.remote((msg["error"] as? String) ?? "unknown"))
            }
        }
    }
}
