import AppKit

enum RunLogLocator {

    static var logDirectory: URL? {
        let env = ProcessInfo.processInfo.environment
        guard let path = env["WISP_RUN_LOG_DIR"], !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path)
    }

    @MainActor
    static func openLogDirectory() -> Bool {
        guard let url = logDirectory else { return false }
        NSWorkspace.shared.open(url)
        return true
    }
}
