import XCTest
import Foundation

final class OverlayBehaviorTests: XCTestCase {

    func testOverlayPanelKeepsNativeWindowAndStateContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/OverlayPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "enum DollState: Hashable { case idle, listening, thinking, speaking }",
            "styleMask: [.nonactivatingPanel, .borderless]",
            "level = .floating",
            "backgroundColor = .clear",
            "isOpaque = false",
            "collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]",
            "isMovableByWindowBackground = true",
            "contentView = OverlayHostingView(rootView: OverlayView(model: model), onRightClick: onRightClick)",
            "override func rightMouseDown(with event: NSEvent)",
            "func showAtLaunch()",
            "func setState(_ state: DollState)",
            "if state != .speaking",
            "model.amplitude = 0",
            "case .idle:",
            "scheduleAutoHide()",
            "case .listening, .thinking, .speaking:",
            "orderFrontRegardless()",
            "func setSpeechAmplitude(_ amplitude: Double)",
            "model.amplitude = max(0, min(1, amplitude))",
            "func toggleVisibility()",
            "ICON_AUTO_HIDE",
            "DOLL_AUTO_HIDE",
            "ICON_BACKSTOP_MS",
            "DOLL_ICON_BACKSTOP_MS",
        ] {
            XCTAssertTrue(source.contains(expected), "OverlayPanel is missing \(expected).")
        }
    }

    func testOverlayViewKeepsDollAssetsFallbackAndSpeechPulse() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/OverlayPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "let iconSize: CGFloat",
            "private let images: [OverlayPanel.DollState: NSImage]",
            "self.images = DollAssetLocator.loadImages()",
            "func image(for state: OverlayPanel.DollState) -> NSImage?",
            "let raw = values[\"ICON_SIZE\"] ?? values[\"DOLL_SIZE\"] ?? \"80\"",
            "return CGFloat(max(32, min(160, parsed)))",
            "case .idle:      return .gray",
            "case .listening: return .blue",
            "case .thinking:  return .yellow",
            "case .speaking:  return .green",
            "guard model.state == .speaking else { return 1.0 }",
            "return 1.0 + CGFloat(model.amplitude) * 0.10",
            "guard model.state == .speaking else { return 6 }",
            "return 6 + CGFloat(model.amplitude) * 8",
            "Image(nsImage: image)",
            "Circle()",
            ".animation(.easeInOut(duration: 0.25), value: model.state)",
            ".animation(.easeOut(duration: 0.08), value: model.amplitude)",
            ".idle: \"idle.png\"",
            ".listening: \"listening.png\"",
            ".thinking: \"thinking.png\"",
            ".speaking: \"speaking.png\"",
            "resourceURL.appendingPathComponent(\"assets/doll\")",
            "resourceURL.appendingPathComponent(\"doll\")",
            "ProcessInfo.processInfo.environment[\"WISP_REPO_ROOT\"]",
            ".appendingPathComponent(\"../assets/doll\")",
        ] {
            XCTAssertTrue(source.contains(expected), "OverlayView or DollAssetLocator is missing \(expected).")
        }
    }

    func testResponseBubbleKeepsStreamingRevealAndPlacementContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/ResponseBubblePanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "final class ResponseBubblePanel: NSPanel",
            "styleMask: [.nonactivatingPanel, .borderless]",
            "collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]",
            "func startThinking(anchor: NSRect?)",
            "model.mode = .thinking",
            "model.dotCount = 1",
            "startDots()",
            "func showListening(anchor: NSRect?)",
            "model.setInstantText(\"Recording - release to send\")",
            "func appendChunk(_ chunk: String)",
            "guard !chunk.isEmpty else { return }",
            "model.mode = .reply",
            "model.appendChunk(chunk)",
            "startRevealIfNeeded()",
            "func setText(_ text: String)",
            "model.replaceBufferedText(text)",
            "func showNotice(_ text: String, anchor: NSRect?, timeout: TimeInterval = 6.0)",
            "model.mode = .notice",
            "scheduleHide(after: timeout)",
            "func finish()",
            "No reply from model. Check model name or API key in Settings.",
            "if model.hasUnrevealedWords",
            "model.isFinishing = true",
            "scheduleHide(after: hideDelay())",
            "x: anchor.minX - frame.width - margin",
            "NSScreen.main?.visibleFrame",
            "Timer.scheduledTimer(withTimeInterval: 0.45",
            "Timer.scheduledTimer(withTimeInterval: revealInterval()",
            "BUBBLE_REVEAL_WPM",
            "BUBBLE_HIDE_DELAY_MS",
        ] {
            XCTAssertTrue(source.contains(expected), "ResponseBubblePanel is missing \(expected).")
        }
    }

    func testResponseBubbleModelKeepsReadableWordWindowAndHighlightContract() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/ResponseBubblePanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "final class ResponseBubbleModel: ObservableObject",
            "enum Mode",
            "case hidden",
            "case thinking",
            "case listening",
            "case reply",
            "case notice",
            "var displayText: String",
            "return String(repeating: \".\", count: dotCount)",
            "var hasUnrevealedWords: Bool",
            "fullText.split(whereSeparator: { $0.isWhitespace }).map(String.init)",
            "guard !revealed.isEmpty else { return fullText.isEmpty ? [] : [\" \"] }",
            "return Array(revealed.suffix(54))",
            "func setInstantText(_ text: String)",
            "revealedCount = words.count",
            "func replaceBufferedText(_ text: String)",
            "revealedCount = min(previousCount, words.count)",
            "func revealNextWord()",
            "let highlight = Color(nsColor: model.config.readWordColor)",
            "index == words.count - 1 ? highlight : normal",
            "BubbleTail()",
        ] {
            XCTAssertTrue(source.contains(expected), "ResponseBubbleModel/View is missing \(expected).")
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
