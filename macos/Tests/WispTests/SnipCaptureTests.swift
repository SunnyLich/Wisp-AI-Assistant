import XCTest
import Foundation

final class SnipCaptureTests: XCTestCase {

    func testScreenCaptureControllerKeepsMainAndRegionCaptureContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Capture/ScreenCaptureController.swift"),
            encoding: .utf8
        )

        for expected in [
            "CGPreflightScreenCaptureAccess()",
            "CGRequestScreenCaptureAccess()",
            "CGDisplayCreateImage(displayID)",
            "CGWindowListCreateImage(",
            ".optionOnScreenOnly",
            "kCGNullWindowID",
            "[.bestResolution]",
            "normalized.width > 4, normalized.height > 4",
            "outputURL(prefix: \"screen-snip\")",
            "NSBitmapImageRep(cgImage: image)",
            "rep.representation(using: .png, properties: [:])",
            "RunLogLocator.writableLogDirectory()",
            "\\(prefix)-\\(stamp).png",
        ] {
            XCTAssertTrue(source.contains(expected), "ScreenCaptureController is missing \(expected).")
        }
    }

    func testSnipOverlayKeepsSelectionAndCoordinateConversionContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Capture/SnipOverlayPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "struct SnipSelection",
            "var captureRect: CGRect",
            "level = .screenSaver",
            "collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]",
            "backgroundColor = .clear",
            "override var canBecomeKey: Bool { true }",
            "makeFirstResponder(snipView)",
            "NSScreen.screens",
            "frame = frame.union(screen.frame)",
            "addCursorRect(bounds, cursor: .crosshair)",
            "NSColor.black.withAlphaComponent(0.45).setFill()",
            "Click and drag to select a region  |  ESC to cancel",
            "screenFrame.minX + selectionRect.minX",
            "screenFrame.maxY - selectionRect.maxY",
            "onSelection(SnipSelection(captureRect: captureRect))",
            "finishCancelled()",
        ] {
            XCTAssertTrue(source.contains(expected), "SnipOverlayPanel is missing \(expected).")
        }
    }

    func testAppDelegateKeepsSnipQueryPayloadWired() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "private struct PendingSnipContext",
            "var screenshotB64: String",
            "private var snipPanel: SnipOverlayPanel?",
            "private let screenCapture = ScreenCaptureController()",
            "private var pendingSnip: PendingSnipContext?",
            "snipPanel = SnipOverlayPanel(",
            "handleSnipSelection(selection)",
            "cancelSnip()",
            "case .snip:",
            "startSnip()",
            "let result = try screenCapture.captureRegion(selection.captureRect, promptForPermission: true)",
            "let data = try Data(contentsOf: result.url)",
            "pendingSnip = PendingSnipContext(",
            "screenshotB64: data.base64EncodedString()",
            "ambientText: snip.contextAmbient ? snapshot.ambientText(includeClipboard: false) : \"\"",
            "useTools: snip.contextTools",
            "capturePath: result.url.path",
            "intentPanel?.show(caller: caller)",
            "label: \"Screen Snip\"",
            "contextScreenshot: .off",
            "if let snip = pendingSnip",
            "\"screenshot_b64\": snip.screenshotB64",
            "\"use_tools\": snip.useTools",
            "\"allow_screenshot_tool\": false",
            "Screen snip saved: \\(snip.capturePath)",
        ] {
            XCTAssertTrue(source.contains(expected), "AppDelegate snip wiring is missing \(expected).")
        }
    }

    private func sourceRoot() -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let direct = currentDirectory.appendingPathComponent("Sources/Wisp")
        if FileManager.default.fileExists(atPath: direct.path) {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }
}
