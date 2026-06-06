import AppKit
import CoreGraphics

enum NativePastebackError: Error, CustomStringConvertible {
    case missingTarget
    case pasteboardRejected
    case eventCreationFailed

    var description: String {
        switch self {
        case .missingTarget:
            return "No original app was captured for paste-back."
        case .pasteboardRejected:
            return "Could not write the rewrite to the clipboard."
        case .eventCreationFailed:
            return "Could not create the Command-V paste event."
        }
    }
}

@MainActor
final class NativePastebackController {
    private let pasteKeyCode = CGKeyCode(9)

    func paste(_ text: String, intoProcessID pid: pid_t?) async throws {
        guard let pid else { throw NativePastebackError.missingTarget }

        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        guard pasteboard.setString(text, forType: .string) else {
            throw NativePastebackError.pasteboardRejected
        }

        if let app = NSRunningApplication(processIdentifier: pid) {
            app.activate(options: [.activateIgnoringOtherApps])
        }
        try await Task.sleep(nanoseconds: 150_000_000)
        try sendPasteKeystroke()
    }

    private func sendPasteKeystroke() throws {
        guard
            let down = CGEvent(keyboardEventSource: nil, virtualKey: pasteKeyCode, keyDown: true),
            let up = CGEvent(keyboardEventSource: nil, virtualKey: pasteKeyCode, keyDown: false)
        else {
            throw NativePastebackError.eventCreationFailed
        }

        down.flags = .maskCommand
        up.flags = .maskCommand
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }
}
