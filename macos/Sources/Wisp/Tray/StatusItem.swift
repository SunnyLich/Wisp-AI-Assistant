import AppKit

/// The menubar presence (`NSStatusItem`) — replaces the Qt system tray. For the
/// native rewrite it owns the product tray actions: ask, chat, memory, settings,
/// voice, overlay visibility, permissions, logs, and quit.
@MainActor
final class StatusItemController: NSObject {

    private let statusItem: NSStatusItem
    private let statusMenuItem: NSMenuItem
    private let hotkeyMenuItem: NSMenuItem
    private let loginItemMenuItem: NSMenuItem

    private let onShowPrompt: () -> Void
    private let onRunEchoSmoke: () -> Void
    private let onShowContext: () -> Void
    private let onShowPermissions: () -> Void
    private let onCaptureScreen: () -> Void
    private let onStartSnip: () -> Void
    private let onOpenRunLogs: () -> Void
    private let onOpenConfigFolder: () -> Void
    private let onShowSettings: () -> Void
    private let onShowChat: () -> Void
    private let onShowNewChat: () -> Void
    private let onShowMemory: () -> Void
    private let onShowPluginManager: () -> Void
    private let onShowAgentTask: () -> Void
    private let onShowAgentHistory: () -> Void
    private let onStartVoiceQuery: () -> Void
    private let onStopVoiceQuery: () -> Void
    private let onSpeakResponse: () -> Void
    private let onRememberPrompt: () -> Void
    private let onSearchMemory: () -> Void
    private let onToggleOverlay: () -> Void
    private let onToggleLoginItem: () -> Void
    private let onRetryHotkey: () -> Void

    init(
        onShowPrompt: @escaping () -> Void,
        onRunEchoSmoke: @escaping () -> Void,
        onShowContext: @escaping () -> Void,
        onShowPermissions: @escaping () -> Void,
        onCaptureScreen: @escaping () -> Void,
        onStartSnip: @escaping () -> Void,
        onOpenRunLogs: @escaping () -> Void,
        onOpenConfigFolder: @escaping () -> Void,
        onShowSettings: @escaping () -> Void,
        onShowChat: @escaping () -> Void,
        onShowNewChat: @escaping () -> Void,
        onShowMemory: @escaping () -> Void,
        onShowPluginManager: @escaping () -> Void,
        onShowAgentTask: @escaping () -> Void,
        onShowAgentHistory: @escaping () -> Void,
        onStartVoiceQuery: @escaping () -> Void,
        onStopVoiceQuery: @escaping () -> Void,
        onSpeakResponse: @escaping () -> Void,
        onRememberPrompt: @escaping () -> Void,
        onSearchMemory: @escaping () -> Void,
        onToggleOverlay: @escaping () -> Void,
        onToggleLoginItem: @escaping () -> Void,
        onRetryHotkey: @escaping () -> Void
    ) {
        self.onShowPrompt = onShowPrompt
        self.onRunEchoSmoke = onRunEchoSmoke
        self.onShowContext = onShowContext
        self.onShowPermissions = onShowPermissions
        self.onCaptureScreen = onCaptureScreen
        self.onStartSnip = onStartSnip
        self.onOpenRunLogs = onOpenRunLogs
        self.onOpenConfigFolder = onOpenConfigFolder
        self.onShowSettings = onShowSettings
        self.onShowChat = onShowChat
        self.onShowNewChat = onShowNewChat
        self.onShowMemory = onShowMemory
        self.onShowPluginManager = onShowPluginManager
        self.onShowAgentTask = onShowAgentTask
        self.onShowAgentHistory = onShowAgentHistory
        self.onStartVoiceQuery = onStartVoiceQuery
        self.onStopVoiceQuery = onStopVoiceQuery
        self.onSpeakResponse = onSpeakResponse
        self.onRememberPrompt = onRememberPrompt
        self.onSearchMemory = onSearchMemory
        self.onToggleOverlay = onToggleOverlay
        self.onToggleLoginItem = onToggleLoginItem
        self.onRetryHotkey = onRetryHotkey
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusMenuItem = NSMenuItem(title: "Brain: starting...", action: nil, keyEquivalent: "")
        hotkeyMenuItem = NSMenuItem(title: "Hotkey: starting...", action: nil, keyEquivalent: "")
        loginItemMenuItem = NSMenuItem(title: "Launch at Login: checking...", action: #selector(toggleLoginItem), keyEquivalent: "")

        super.init()

        configureStatusButton()

        let menu = NSMenu()
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)
        hotkeyMenuItem.isEnabled = false
        menu.addItem(hotkeyMenuItem)
        menu.addItem(.separator())

        func addItem(
            _ title: String,
            action: Selector,
            keyEquivalent: String = "",
            modifiers: NSEvent.ModifierFlags = []
        ) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
            item.keyEquivalentModifierMask = modifiers
            item.target = self
            menu.addItem(item)
        }

        addItem("Ask Wisp", action: #selector(showPrompt), keyEquivalent: " ", modifiers: [.control, .option])
        addItem("Run Echo Smoke", action: #selector(runEchoSmoke), keyEquivalent: "e")
        addItem("Context Snapshot", action: #selector(showContext), keyEquivalent: "c")
        addItem("Capture Screen Smoke", action: #selector(captureScreen), keyEquivalent: "s")
        menu.addItem(.separator())
        addItem("Start agent task...", action: #selector(showAgentTask))
        addItem("Agent task history...", action: #selector(showAgentHistory))
        menu.addItem(.separator())
        addItem("New chat", action: #selector(showNewChat), keyEquivalent: "n")
        addItem("Last chat", action: #selector(showChat), keyEquivalent: "g")
        addItem("Hide icon", action: #selector(toggleOverlay), keyEquivalent: "o")
        menu.addItem(.separator())
        addItem("Memory", action: #selector(showMemory))
        addItem("Plugin Manager", action: #selector(showPluginManager))
        menu.addItem(.separator())
        addItem("Settings", action: #selector(showSettings), keyEquivalent: ",")

        menu.addItem(.separator())

        addItem("Snip Screen Region", action: #selector(startSnip))
        addItem("Start Voice Query", action: #selector(startVoiceQuery), keyEquivalent: "r")
        addItem("Stop Voice Query", action: #selector(stopVoiceQuery), keyEquivalent: "t")
        addItem("Speak Last Response", action: #selector(speakResponse), keyEquivalent: "v")

        menu.addItem(.separator())

        addItem("Remember Prompt", action: #selector(rememberPrompt), keyEquivalent: "m")
        addItem("Search Memory", action: #selector(searchMemory), keyEquivalent: "f")

        menu.addItem(.separator())

        addItem("Permissions", action: #selector(showPermissions), keyEquivalent: "p")
        loginItemMenuItem.target = self
        menu.addItem(loginItemMenuItem)
        addItem("Retry Hotkey Permission", action: #selector(retryHotkey), keyEquivalent: "h")
        addItem("Open Run Logs", action: #selector(openRunLogs), keyEquivalent: "l")
        addItem("Open Config Folder", action: #selector(openConfigFolder))

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    private func configureStatusButton() {
        guard let button = statusItem.button else { return }
        if let image = NSImage(systemSymbolName: "sparkles", accessibilityDescription: "Wisp") {
            image.isTemplate = true
            button.image = image
            button.title = ""
        } else {
            button.title = "W"
        }
        button.toolTip = "Wisp"
    }

    /// Reflect the result of the brain handshake in the menu.
    func setBrainStatus(_ text: String) {
        statusMenuItem.title = "Brain: \(text)"
    }

    func setHotkeyStatus(_ text: String) {
        hotkeyMenuItem.title = "Hotkey: \(text)"
    }

    func setLoginItemStatus(_ status: LoginItemStatus) {
        loginItemMenuItem.title = status.menuTitle
        loginItemMenuItem.state = status.isChecked ? .on : .off
        loginItemMenuItem.isEnabled = status.isActionable
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

    @objc private func startSnip() {
        onStartSnip()
    }

    @objc private func toggleOverlay() {
        onToggleOverlay()
    }

    @objc private func toggleLoginItem() {
        onToggleLoginItem()
    }

    @objc private func retryHotkey() {
        onRetryHotkey()
    }

    @objc private func openRunLogs() {
        onOpenRunLogs()
    }

    @objc private func openConfigFolder() {
        onOpenConfigFolder()
    }

    @objc private func showSettings() {
        onShowSettings()
    }

    @objc private func showChat() {
        onShowChat()
    }

    @objc private func showNewChat() {
        onShowNewChat()
    }

    @objc private func showMemory() {
        onShowMemory()
    }

    @objc private func showPluginManager() {
        onShowPluginManager()
    }

    @objc private func showAgentTask() {
        onShowAgentTask()
    }

    @objc private func showAgentHistory() {
        onShowAgentHistory()
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
