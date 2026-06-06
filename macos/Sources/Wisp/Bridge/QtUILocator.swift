import Foundation

/// Resolves the optional Qt UI host used by the hybrid macOS build.
///
/// The Swift app still owns native macOS capabilities, but it can launch a
/// separate Python/Qt process for product windows that already work well:
/// Agent history views that have not moved to Swift yet.
enum QtUILocator {

    static func resolve() -> QtUIBridge.Config? {
        let fm = FileManager.default
        let env = ProcessInfo.processInfo.environment

        let python = env["WISP_QT_UI_PYTHON"] ?? env["WISP_BRAIN_PYTHON"]
        let repoRoot = env["WISP_REPO_ROOT"]

        // Dev checkout path. `Start Wisp.command` exports WISP_REPO_ROOT and
        // WISP_BRAIN_PYTHON, so this is the primary route today.
        if let python, let repoRoot {
            let root = URL(fileURLWithPath: repoRoot)
            let host = env["WISP_QT_UI_HOST_SCRIPT"].map { URL(fileURLWithPath: $0) }
                ?? root.appendingPathComponent("macos/ui_host/wisp_qt_ui_host.py")
            if fm.fileExists(atPath: python), fm.fileExists(atPath: host.path) {
                return QtUIBridge.Config(
                    pythonExecutable: URL(fileURLWithPath: python),
                    hostScript: host,
                    repoRoot: root
                )
            }
        }

        // Future bundled app path.
        if let res = Bundle.main.resourceURL {
            let python = res.appendingPathComponent("python-runtime/bin/python3")
            let host = res.appendingPathComponent("ui_host/wisp_qt_ui_host.py")
            if fm.fileExists(atPath: python.path), fm.fileExists(atPath: host.path) {
                return QtUIBridge.Config(
                    pythonExecutable: python,
                    hostScript: host,
                    repoRoot: res
                )
            }
        }

        return nil
    }
}
