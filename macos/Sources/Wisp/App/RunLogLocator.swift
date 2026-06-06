import AppKit

enum RunLogLocator {

    static var logDirectory: URL? {
        logDirectory(
            environment: ProcessInfo.processInfo.environment,
            resourceURL: Bundle.main.resourceURL
        )
    }

    static func logDirectory(
        environment: [String: String],
        currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        resourceURL: URL? = nil,
        fileManager: FileManager = .default
    ) -> URL? {
        if let path = environment["WISP_RUN_LOG_DIR"]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        return latestLogDirectory(
            repoRoot: repoRoot(
                environment: environment,
                currentDirectory: currentDirectory,
                resourceURL: resourceURL,
                fileManager: fileManager
            ),
            fileManager: fileManager
        )
    }

    @MainActor
    static func openLogDirectory() -> Bool {
        guard let url = logDirectory else { return false }
        NSWorkspace.shared.open(url)
        return true
    }

    private static func repoRoot(
        environment: [String: String],
        currentDirectory: URL,
        resourceURL: URL?,
        fileManager: FileManager
    ) -> URL {
        if let path = environment["WISP_REPO_ROOT"]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        if let resourceURL,
           let devRoot = repoRootForDevBundle(resourceURL: resourceURL, fileManager: fileManager) {
            return devRoot
        }
        if currentDirectory.lastPathComponent == "macos" {
            return currentDirectory.deletingLastPathComponent()
        }
        return currentDirectory
    }

    private static func repoRootForDevBundle(resourceURL: URL, fileManager: FileManager) -> URL? {
        let repoRoot = resourceURL
            .deletingLastPathComponent() // Contents
            .deletingLastPathComponent() // Wisp.app
            .deletingLastPathComponent() // WispNative
            .deletingLastPathComponent() // build
            .deletingLastPathComponent()
        let buildLogs = repoRoot.appendingPathComponent("build_logs")
        guard fileManager.fileExists(atPath: buildLogs.path) else {
            return nil
        }
        return repoRoot
    }

    private static func latestLogDirectory(repoRoot: URL, fileManager: FileManager) -> URL? {
        let buildLogs = repoRoot.appendingPathComponent("build_logs")
        guard let urls = try? fileManager.contentsOfDirectory(
            at: buildLogs,
            includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey]
        ) else {
            return nil
        }

        return urls
            .filter { url in
                let name = url.lastPathComponent
                return name.hasPrefix("macos_phase1_") || name.hasPrefix("macos_native_tests_")
            }
            .filter { url in
                let values = try? url.resourceValues(forKeys: [.isDirectoryKey])
                return values?.isDirectory == true
            }
            .sorted { lhs, rhs in
                let leftDate = (try? lhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let rightDate = (try? rhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                if leftDate != rightDate {
                    return leftDate > rightDate
                }
                return lhs.lastPathComponent > rhs.lastPathComponent
            }
            .first
    }
}
