import AppKit
import Darwin

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
        userLogBaseDirectory: URL? = nil,
        fileManager: FileManager = .default
    ) -> URL? {
        if let path = environment["WISP_RUN_LOG_DIR"]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        if let latest = latestLogDirectory(
            repoRoot: repoRoot(
                environment: environment,
                currentDirectory: currentDirectory,
                resourceURL: resourceURL,
                fileManager: fileManager
            ),
            fileManager: fileManager
        ) {
            return latest
        }
        return userLogDirectory(baseDirectory: userLogBaseDirectory, fileManager: fileManager)
    }

    static func writableLogDirectory(fileManager: FileManager = .default) -> URL? {
        guard let url = logDirectory(
            environment: ProcessInfo.processInfo.environment,
            resourceURL: Bundle.main.resourceURL,
            fileManager: fileManager
        ) else { return nil }
        do {
            try fileManager.createDirectory(at: url, withIntermediateDirectories: true)
            return url
        } catch {
            NSLog("[wisp] could not create run log directory: %@", String(describing: error))
            return nil
        }
    }

    static func environmentByResolvingLogDirectory(
        environment: [String: String],
        currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        resourceURL: URL? = nil,
        userLogBaseDirectory: URL? = nil,
        fileManager: FileManager = .default
    ) -> [String: String] {
        var resolved = environment
        if missingEnvironmentValue(resolved["WISP_REPO_ROOT"]),
           let resourceURL,
           let devRoot = repoRootForDevBundle(resourceURL: resourceURL, fileManager: fileManager) {
            resolved["WISP_REPO_ROOT"] = devRoot.path
        }

        if !missingEnvironmentValue(resolved["WISP_RUN_LOG_DIR"]) {
            return resolved
        }
        guard let logDirectory = logDirectory(
            environment: resolved,
            currentDirectory: currentDirectory,
            resourceURL: resourceURL,
            userLogBaseDirectory: userLogBaseDirectory,
            fileManager: fileManager
        ) else {
            return resolved
        }
        resolved["WISP_RUN_LOG_DIR"] = logDirectory.path
        return resolved
    }

    static func seedProcessEnvironmentIfMissing() {
        let environment = ProcessInfo.processInfo.environment
        let resolved = environmentByResolvingLogDirectory(
            environment: environment,
            resourceURL: Bundle.main.resourceURL
        )
        if missingEnvironmentValue(environment["WISP_REPO_ROOT"]),
           let repoRoot = resolved["WISP_REPO_ROOT"] {
            setenv("WISP_REPO_ROOT", repoRoot, 0)
            NSLog("[wisp] inferred repo root: %@", repoRoot)
        }
        if missingEnvironmentValue(environment["WISP_RUN_LOG_DIR"]),
           let logDirectory = resolved["WISP_RUN_LOG_DIR"] {
            setenv("WISP_RUN_LOG_DIR", logDirectory, 0)
            NSLog("[wisp] inferred run log directory: %@", logDirectory)
        }
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
        let brain = repoRoot.appendingPathComponent("macos/brain")
        guard fileManager.fileExists(atPath: brain.path) else {
            return nil
        }
        return repoRoot
    }

    private static func missingEnvironmentValue(_ value: String?) -> Bool {
        value?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true
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
                return name.hasPrefix("macos_phase1_")
                    || name.hasPrefix("macos_native_tests_")
                    || name.hasPrefix("macos_package_")
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

    private static func userLogDirectory(baseDirectory: URL?, fileManager: FileManager) -> URL? {
        let library = baseDirectory ?? fileManager.urls(for: .libraryDirectory, in: .userDomainMask).first
        return library?
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("Wisp", isDirectory: true)
    }
}
