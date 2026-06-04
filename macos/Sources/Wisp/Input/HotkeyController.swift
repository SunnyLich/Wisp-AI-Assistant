import AppKit
import ApplicationServices

enum HotkeyInstallResult: Equatable {
    case installed
    case accessibilityNeeded
    case failed(String)

    var statusText: String {
        switch self {
        case .installed:
            return "Ctrl-Option-Space ready"
        case .accessibilityNeeded:
            return "Accessibility permission needed"
        case .failed(let message):
            return "failed: \(message)"
        }
    }
}

@MainActor
final class HotkeyController {

    private static let spaceKeyCode: Int64 = 49

    private let onTrigger: () -> Void
    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?

    init(onTrigger: @escaping () -> Void) {
        self.onTrigger = onTrigger
    }

    func start(promptForPermission: Bool) -> HotkeyInstallResult {
        stop()

        let options = [kAXTrustedCheckOptionPrompt as String: promptForPermission] as CFDictionary
        guard AXIsProcessTrustedWithOptions(options) else {
            NSLog("[wisp] hotkey unavailable: Accessibility permission is not trusted yet")
            return .accessibilityNeeded
        }

        let mask = CGEventMask(1 << CGEventType.keyDown.rawValue)
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: mask,
            callback: hotkeyEventCallback,
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else {
            NSLog("[wisp] hotkey unavailable: CGEvent.tapCreate returned nil")
            return .failed("event tap could not be created")
        }

        guard let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0) else {
            return .failed("run loop source could not be created")
        }

        eventTap = tap
        runLoopSource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        NSLog("[wisp] hotkey installed: Ctrl-Option-Space")
        return .installed
    }

    func stop() {
        if let tap = eventTap {
            CGEvent.tapEnable(tap: tap, enable: false)
        }
        if let source = runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
        }
        eventTap = nil
        runLoopSource = nil
    }

    fileprivate func handleEvent(typeRawValue: UInt32, keyCode: Int64, flagsRawValue: UInt64, isRepeat: Bool) {
        if typeRawValue == CGEventType.tapDisabledByTimeout.rawValue ||
            typeRawValue == CGEventType.tapDisabledByUserInput.rawValue {
            if let eventTap {
                CGEvent.tapEnable(tap: eventTap, enable: true)
                NSLog("[wisp] hotkey event tap re-enabled after disable event")
            }
            return
        }

        guard typeRawValue == CGEventType.keyDown.rawValue else { return }
        guard keyCode == Self.spaceKeyCode, !isRepeat else { return }

        let flags = CGEventFlags(rawValue: flagsRawValue)
        guard flags.contains(.maskControl), flags.contains(.maskAlternate) else { return }

        NSLog("[wisp] hotkey triggered")
        onTrigger()
    }
}

private let hotkeyEventCallback: CGEventTapCallBack = { _, type, event, userInfo in
    if let userInfo {
        let controller = Unmanaged<HotkeyController>.fromOpaque(userInfo).takeUnretainedValue()
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        let isRepeat = event.getIntegerValueField(.keyboardEventAutorepeat) != 0
        let flagsRawValue = event.flags.rawValue
        let typeRawValue = type.rawValue
        Task { @MainActor in
            controller.handleEvent(
                typeRawValue: typeRawValue,
                keyCode: keyCode,
                flagsRawValue: flagsRawValue,
                isRepeat: isRepeat
            )
        }
    }
    return Unmanaged.passUnretained(event)
}
