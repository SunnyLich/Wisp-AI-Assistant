import AppKit

/// The menubar presence (`NSStatusItem`) — replaces the Qt system tray. For the
/// native rewrite it doubles as a compact smoke-test console: prompt, echo,
/// overlay toggle, hotkey retry, status, quit.
@MainActor
final class StatusItemController: NSObject {

    private let statusItem: NSStatusItem
    private let statusMenuItem: NSMenuItem
    private let hotkeyMenuItem: NSMenuItem

    private let onShowPrompt: () -> Void
    private let onRunEchoSmoke: () -> Void
    private let onShowContext: () -> Void
    private let onShowPermissions: () -> Void
    private let onCaptureScreen: () -> Void
    private let onOpenRunLogs: () -> Void
    private let onStartVoiceQuery: () -> Void
    private let onStopVoiceQuery: () -> Void
    private let onSpeakResponse: () -> Void
    private let onRememberPrompt: () -> Void
    private let onSearchMemory: () -> Void
    private let onToggleOverlay: () -> Void
    private let onRetryHotkey: () -> Void

    init(
        onShowPrompt: @escaping () -> Void,
        onRunEchoSmoke: @escaping () -> Void,
        onShowContext: @escaping () -> Void,
        onShowPermissions: @escaping () -> Void,
        onCaptureScreen: @escaping () -> Void,
        onOpenRunLogs: @escaping () -> Void,
        onStartVoiceQuery: @escaping () -> Void,
        onStopVoiceQuery: @escaping () -> Void,
        onSpeakResponse: @escaping () -> Void,
        onRememberPrompt: @escaping () -> Void,
        onSearchMemory: @escaping () -> Void,
        onToggleOverlay: @escaping () -> Void,
        onRetryHotkey: @escaping () -> Void
    ) {
        self.onShowPrompt = onShowPrompt
        self.onRunEchoSmoke = onRunEchoSmoke
        self.onShowContext = onShowContext
        self.onShowPermissions = onShowPermissions
        self.onCaptureScreen = onCaptureScreen
        self.onOpenRunLogs = onOpenRunLogs
        self.onStartVoiceQuery = onStartVoiceQuery
        self.onStopVoiceQuery = onStopVoiceQuery
        self.onSpeakResponse = onSpeakResponse
        self.onRememberPrompt = onRememberPrompt
        self.onSearchMemory = onSearchMemory
        self.onToggleOverlay = onToggleOverlay
        self.onRetryHotkey = onRetryHotkey
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusMenuItem = NSMenuItem(title: "Brain: starting...", action: nil, keyEquivalent: "")
        hotkeyMenuItem = NSMenuItem(title: "Hotkey: starting...", action: nil, keyEquivalent: "")

        super.init()

        statusItem.button?.title = "✦"
        statusItem.button?.toolTip = "Wisp"

        let menu = NSMenu()
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)
        hotkeyMenuItem.isEnabled = false
        menu.addItem(hotkeyMenuItem)
        menu.addItem(.separator())

        let promptItem = NSMenuItem(title: "Show Prompt", action: #selector(showPrompt), keyEquivalent: " ")
        promptItem.keyEquivalentModifierMask = [.control, .option]
        promptItem.target = self
        menu.addItem(promptItem)

        let echoItem = NSMenuItem(title: "Run Echo Smoke", action: #selector(runEchoSmoke), keyEquivalent: "e")
        echoItem.target = self
        menu.addItem(echoItem)

        let contextItem = NSMenuItem(title: "Context Snapshot", action: #selector(showContext), keyEquivalent: "c")
        contextItem.target = self
        menu.addItem(contextItem)

        let permissionsItem = NSMenuItem(title: "Permission Snapshot", action: #selector(showPermissions), keyEquivalent: "p")
        permissionsItem.target = self
        menu.addItem(permissionsItem)

        let captureItem = NSMenuItem(title: "Capture Screen Smoke", action: #selector(captureScreen), keyEquivalent: "s")
        captureItem.target = self
        menu.addItem(captureItem)

        menu.addItem(.separator())

        let startVoiceItem = NSMenuItem(title: "Start Voice Query", action: #selector(startVoiceQuery), keyEquivalent: "r")
        startVoiceItem.target = self
        menu.addItem(startVoiceItem)

        let stopVoiceItem = NSMenuItem(title: "Stop Voice Query", action: #selector(stopVoiceQuery), keyEquivalent: "t")
        stopVoiceItem.target = self
        menu.addItem(stopVoiceItem)

        let speakItem = NSMenuItem(title: "Speak Last Response", action: #selector(speakResponse), keyEquivalent: "v")
        speakItem.target = self
        menu.addItem(speakItem)

        menu.addItem(.separator())

        let rememberItem = NSMenuItem(title: "Remember Prompt", action: #selector(rememberPrompt), keyEquivalent: "m")
        rememberItem.target = self
        menu.addItem(rememberItem)

        let searchMemoryItem = NSMenuItem(title: "Search Memory", action: #selector(searchMemory), keyEquivalent: "f")
        searchMemoryItem.target = self
        menu.addItem(searchMemoryItem)

        menu.addItem(.separator())

        let overlayItem = NSMenuItem(title: "Toggle Overlay", action: #selector(toggleOverlay), keyEquivalent: "o")
        overlayItem.target = self
        menu.addItem(overlayItem)

        let retryHotkeyItem = NSMenuItem(title: "Retry Hotkey Permission", action: #selector(retryHotkey), keyEquivalent: "h")
        retryHotkeyItem.target = self
        menu.addItem(retryHotkeyItem)

        let logsItem = NSMenuItem(title: "Open Run Logs", action: #selector(openRunLogs), keyEquivalent: "l")
        logsItem.target = self
        menu.addItem(logsItem)

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Wisp", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    /// Reflect the result of the brain handshake in the menu.
    func setBrainStatus(_ text: String) {
        statusMenuItem.title = "Brain: \(text)"
    }

    func setHotkeyStatus(_ text: String) {
        hotkeyMenuItem.title = "Hotkey: \(text)"
    }

    @objc private func showPrompt() {
        onShowPrompt()
    }

    @objc private func runEchoSmoke() {
        onRunEchoSmoke()
    }

    @objc private func showContext() {
        onShowContext()
    }

    @objc private func showPermissions() {
        onShowPermissions()
    }

    @objc private func captureScreen() {
        onCaptureScreen()
    }

    @objc private func toggleOverlay() {
        onToggleOverlay()
    }

    @objc private func retryHotkey() {
        onRetryHotkey()
    }

    @objc private func openRunLogs() {
        onOpenRunLogs()
    }

    @objc private func startVoiceQuery() {
        onStartVoiceQuery()
    }

    @objc private func stopVoiceQuery() {
        onStopVoiceQuery()
    }

    @objc private func speakResponse() {
        onSpeakResponse()
    }

    @objc private func rememberPrompt() {
        onRememberPrompt()
    }

    @objc private func searchMemory() {
        onSearchMemory()
    }
}
