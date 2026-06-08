import Foundation

enum NativeLaunchDiagnostics {
    static let startupMarkerName = "native-app-launch.log"

    static func writeStartupRecord(config: WispConfig, brainConfig: BrainClient.Config) {
        let environment = RunLogLocator.environmentByResolvingLogDirectory(
            environment: ProcessInfo.processInfo.environment,
            resourceURL: Bundle.main.resourceURL
        )
        guard let logDirectory = RunLogLocator.logDirectory(
            environment: environment,
            resourceURL: Bundle.main.resourceURL
        ) else { return }
        do {
            try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
            let url = logDirectory.appendingPathComponent(startupMarkerName)
            let record = startupRecord(environment: environment, config: config, brainConfig: brainConfig)
            try record.write(to: url, atomically: true, encoding: .utf8)
            NSLog("[wisp] native launch marker written: %@", url.path)
        } catch {
            NSLog("[wisp] native launch marker failed: %@", String(describing: error))
        }
    }

    static func startupRecord(
        now: Date = Date(),
        environment: [String: String] = ProcessInfo.processInfo.environment,
        config: WispConfig,
        brainConfig: BrainClient.Config,
        resourceURL: URL? = Bundle.main.resourceURL
    ) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let resolvedPython = brainConfig.resolvedPythonExecutable()
        let configuredPython = brainConfig.pythonExecutable
        let fileManager = FileManager.default

        return [
            "started_at=\(formatter.string(from: now))",
            "process_id=\(ProcessInfo.processInfo.processIdentifier)",
            "bundle_identifier=\(Bundle.main.bundleIdentifier ?? "")",
            "bundle_path=\(Bundle.main.bundleURL.path)",
            "executable_path=\(Bundle.main.executableURL?.path ?? "")",
            "repo_root=\(environment["WISP_REPO_ROOT"] ?? "")",
            "run_log_dir=\(environment["WISP_RUN_LOG_DIR"] ?? "")",
            "resource_url=\(resourceURL?.path ?? "")",
            "brain_python=\(resolvedPython.path)",
            "brain_python_exists=\(fileManager.fileExists(atPath: resolvedPython.path))",
            "brain_python_is_executable=\(fileManager.isExecutableFile(atPath: resolvedPython.path))",
            "brain_python_configured=\(configuredPython.path)",
            "brain_python_configured_exists=\(fileManager.fileExists(atPath: configuredPython.path))",
            "brain_dir=\(brainConfig.brainDirectory.path)",
            "brain_dir_exists=\(fileManager.fileExists(atPath: brainConfig.brainDirectory.path))",
            "brain_pythonpath=\(brainConfig.extraPythonPath.map(\.path).joined(separator: ":"))",
            "caller_count=\(config.callers.count)",
            "snip_hotkey=\(config.snip.hotkey)",
        ].joined(separator: "\n") + "\n"
    }
}
