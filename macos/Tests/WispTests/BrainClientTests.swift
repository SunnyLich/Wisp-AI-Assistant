import XCTest
@testable import Wisp

final class BrainClientTests: XCTestCase {

    func testNonExecutablePythonFailsBeforeSpawnWithSetupGuidance() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-brain-client-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let python = root.appendingPathComponent("bin/python")
        let brainDir = root.appendingPathComponent("brain")
        try FileManager.default.createDirectory(at: python.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: brainDir, withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: python.path, contents: Data()))

        let client = BrainClient(
            config: BrainClient.Config(
                pythonExecutable: python,
                brainDirectory: brainDir,
                extraPythonPath: []
            )
        )

        do {
            _ = try await client.call("ping", timeout: .seconds(1))
            XCTFail("Expected non-executable Python to fail before spawning.")
        } catch BrainError.spawnFailed(let message) {
            XCTAssertTrue(message.contains("Python runtime is not executable or was not found"))
            XCTAssertTrue(message.contains("Start Wisp (Mac Native).command"))
            XCTAssertTrue(message.contains("WISP_PYTHON_RUNTIME_DIR"))
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        await client.shutdown()
    }

    func testPingAndEchoAgainstConfiguredSidecar() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["WISP_BRAIN_PYTHON"], !python.isEmpty,
              let brainDir = env["WISP_BRAIN_DIR"], !brainDir.isEmpty else {
            throw XCTSkip("Set WISP_BRAIN_PYTHON and WISP_BRAIN_DIR to run the sidecar integration test.")
        }

        let repoRoot = env["WISP_REPO_ROOT"].map { URL(fileURLWithPath: $0) }
        let client = BrainClient(
            config: BrainClient.Config(
                pythonExecutable: URL(fileURLWithPath: python),
                brainDirectory: URL(fileURLWithPath: brainDir),
                extraPythonPath: repoRoot.map { [$0] } ?? []
            )
        )

        do {
            let pong = try await client.call("ping", ["value": "hello-from-xctest"])
            XCTAssertEqual(pong?["pong"] as? Bool, true)
            XCTAssertEqual(pong?["value"] as? String, "hello-from-xctest")
            XCTAssertNotNil(pong?["pid"] as? Int)

            var chunks = ""
            var resultText = ""
            for try await item in client.stream("brain.echo", ["text": "swift stream ok"]) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    chunks += data?["text"] as? String ?? ""
                case .result(let result):
                    resultText = result?["text"] as? String ?? ""
                default:
                    break
                }
            }

            XCTAssertFalse(chunks.isEmpty)
            XCTAssertEqual(
                chunks.trimmingCharacters(in: .whitespacesAndNewlines),
                "swift stream ok",
                "Unexpected streamed chunks: \(String(reflecting: chunks))"
            )
            XCTAssertEqual(resultText, "swift stream ok")
            await client.shutdown()
        } catch {
            await client.shutdown()
            throw error
        }
    }
}
