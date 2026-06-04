import AppKit

// Executable entry point. `.accessory` keeps Wisp out of the Dock / app switcher
// (a menubar + floating-overlay assistant, not a windowed app).
@main
@MainActor
enum WispMain {
    private static var delegate: AppDelegate?

    static func main() {
        let app = NSApplication.shared
        let appDelegate = AppDelegate()
        delegate = appDelegate
        app.delegate = appDelegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}
