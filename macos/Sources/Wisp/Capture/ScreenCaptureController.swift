import AppKit
import CoreGraphics

struct ScreenCaptureResult {
    var url: URL
    var width: Int
    var height: Int

    var displayText: String {
        """
        Screen capture saved:
        \(url.path)

        Size: \(width)x\(height)
        """
    }
}

@MainActor
final class ScreenCaptureController {

    func captureMainDisplay(promptForPermission: Bool) throws -> ScreenCaptureResult {
        if promptForPermission, !CGPreflightScreenCaptureAccess() {
            _ = CGRequestScreenCaptureAccess()
        }

        guard CGPreflightScreenCaptureAccess() else {
            throw ScreenCaptureError.permissionDenied
        }

        let displayID = CGMainDisplayID()
        guard let image = CGDisplayCreateImage(displayID) else {
            throw ScreenCaptureError.captureFailed
        }

        let url = outputURL()
        let rep = NSBitmapImageRep(cgImage: image)
        guard let data = rep.representation(using: .png, properties: [:]) else {
            throw ScreenCaptureError.encodeFailed
        }
        try data.write(to: url, options: [.atomic])

        return ScreenCaptureResult(
            url: url,
            width: image.width,
            height: image.height
        )
    }

    private func outputURL() -> URL {
        let env = ProcessInfo.processInfo.environment
        let base: URL
        if let logDir = env["WISP_RUN_LOG_DIR"], !logDir.isEmpty {
            base = URL(fileURLWithPath: logDir)
        } else {
            base = FileManager.default.temporaryDirectory
        }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let stamp = formatter.string(from: Date())
        return base.appendingPathComponent("screen-capture-\(stamp).png")
    }
}

enum ScreenCaptureError: Error, CustomStringConvertible {
    case permissionDenied
    case captureFailed
    case encodeFailed

    var description: String {
        switch self {
        case .permissionDenied:
            return "Screen Recording permission is not granted"
        case .captureFailed:
            return "CoreGraphics did not return a screen image"
        case .encodeFailed:
            return "screen image could not be encoded as PNG"
        }
    }
}
