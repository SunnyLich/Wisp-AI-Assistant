import AppKit
import SwiftUI

@MainActor
final class ResponseBubblePanel: NSPanel {

    private let model: ResponseBubbleModel
    private var dotsTimer: Timer?
    private var revealTimer: Timer?
    private var hideTimer: Timer?

    init(onTap: @escaping () -> Void = {}) {
        let config = ResponseBubbleConfig.load()
        self.model = ResponseBubbleModel(onTap: onTap, config: config)
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: config.panelWidth, height: config.panelHeight),
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )

        isFloatingPanel = true
        level = .floating
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        hidesOnDeactivate = false
        contentView = NSHostingView(rootView: ResponseBubbleView(model: model))
    }

    func startThinking(anchor: NSRect?) {
        hideTimer?.invalidate()
        stopReveal()
        model.mode = .thinking
        model.resetText()
        model.dotCount = 1
        reposition(anchor: anchor)
        orderFrontRegardless()
        startDots()
    }

    func showListening(anchor: NSRect?) {
        hideTimer?.invalidate()
        stopDots()
        stopReveal()
        model.mode = .listening
        model.setInstantText("Recording - release to send")
        reposition(anchor: anchor)
        orderFrontRegardless()
    }

    func appendChunk(_ chunk: String) {
        guard !chunk.isEmpty else { return }
        hideTimer?.invalidate()
        if model.mode == .thinking {
            stopDots()
        }
        model.mode = .reply
        model.appendChunk(chunk)
        startRevealIfNeeded()
        reposition(anchor: nil)
        orderFrontRegardless()
    }

    func setText(_ text: String) {
        hideTimer?.invalidate()
        stopDots()
        model.mode = .reply
        model.replaceBufferedText(text)
        startRevealIfNeeded()
        orderFrontRegardless()
    }

    func showNotice(_ text: String, anchor: NSRect?, timeout: TimeInterval = 6.0) {
        hideTimer?.invalidate()
        stopDots()
        stopReveal()
        model.mode = .notice
        model.setInstantText(text)
        reposition(anchor: anchor)
        orderFrontRegardless()
        scheduleHide(after: timeout)
    }

    func finish() {
        stopDots()
        if model.fullText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            stopReveal()
            model.mode = .notice
            model.setInstantText("No reply from model. Check model name or API key in Settings.")
            scheduleHide(after: 3.5)
            return
        }
        if model.hasUnrevealedWords {
            model.isFinishing = true
            startRevealIfNeeded()
        } else {
            stopReveal()
            model.isFinishing = false
            scheduleHide(after: hideDelay())
        }
    }

    func clear() {
        hideTimer?.invalidate()
        stopDots()
        stopReveal()
        model.resetText()
        model.mode = .hidden
        orderOut(nil)
    }

    func reposition(anchor: NSRect?) {
        guard let anchor else { return }
        let margin: CGFloat = 6
        let target = NSPoint(
            x: anchor.minX - frame.width - margin,
            y: anchor.midY - frame.height / 2
        )
        if let screenFrame = NSScreen.main?.visibleFrame {
            setFrameOrigin(NSPoint(
                x: max(screenFrame.minX + 8, min(target.x, screenFrame.maxX - frame.width - 8)),
                y: max(screenFrame.minY + 8, min(target.y, screenFrame.maxY - frame.height - 8))
            ))
        } else {
            setFrameOrigin(target)
        }
    }

    private func startDots() {
        stopDots()
        dotsTimer = Timer.scheduledTimer(withTimeInterval: 0.45, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.model.dotCount = self.model.dotCount % 3 + 1
            }
        }
    }

    private func stopDots() {
        dotsTimer?.invalidate()
        dotsTimer = nil
    }

    private func startRevealIfNeeded() {
        guard model.hasUnrevealedWords, revealTimer == nil else { return }
        revealTimer = Timer.scheduledTimer(withTimeInterval: revealInterval(), repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.revealNextWord()
            }
        }
    }

    private func revealNextWord() {
        model.revealNextWord()
        if model.hasUnrevealedWords {
            return
        }
        let shouldHide = model.isFinishing
        stopReveal()
        if shouldHide {
            scheduleHide(after: hideDelay())
        }
    }

    private func stopReveal() {
        revealTimer?.invalidate()
        revealTimer = nil
        model.isFinishing = false
    }

    private func scheduleHide(after delay: TimeInterval) {
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.orderOut(nil)
            }
        }
    }

    private func revealInterval() -> TimeInterval {
        let values = WispConfig.loadValues()
        let wpm = max(1, intValue(values["BUBBLE_REVEAL_WPM"], default: 170))
        return 60.0 / Double(wpm)
    }

    private func hideDelay() -> TimeInterval {
        let values = WispConfig.loadValues()
        let milliseconds = max(500, intValue(values["BUBBLE_HIDE_DELAY_MS"], default: 3500))
        return Double(milliseconds) / 1000.0
    }

    private func intValue(_ raw: String?, default fallback: Int) -> Int {
        guard let raw, let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            return fallback
        }
        return value
    }
}

struct ResponseBubbleConfig {
    var bubbleWidth: CGFloat
    var lines: Int
    var bubbleColor: NSColor
    var textColor: NSColor

    var tailWidth: CGFloat { 12 }
    var bubbleHeight: CGFloat { CGFloat(lines) * 20 + 28 }
    var panelWidth: CGFloat { bubbleWidth + tailWidth + 4 }
    var panelHeight: CGFloat { bubbleHeight + 8 }

    static func load() -> ResponseBubbleConfig {
        let values = WispConfig.loadValues()
        return ResponseBubbleConfig(
            bubbleWidth: CGFloat(max(220, min(720, intValue(values["BUBBLE_WIDTH"], default: 340)))),
            lines: max(1, min(8, intValue(values["BUBBLE_LINES"], default: 3))),
            bubbleColor: color(values["BUBBLE_COLOR"], fallback: NSColor(calibratedRed: 0.11, green: 0.11, blue: 0.15, alpha: 0.92)),
            textColor: color(values["BUBBLE_TEXT_COLOR"], fallback: NSColor(calibratedWhite: 0.92, alpha: 1.0))
        )
    }

    private static func intValue(_ raw: String?, default fallback: Int) -> Int {
        guard let raw, let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            return fallback
        }
        return value
    }

    private static func color(_ raw: String?, fallback: NSColor) -> NSColor {
        let value = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.hasPrefix("#") else {
            return fallback
        }
        let hex = String(value.dropFirst())
        guard hex.count == 6 || hex.count == 8, let number = UInt64(hex, radix: 16) else {
            return fallback
        }
        let r: CGFloat
        let g: CGFloat
        let b: CGFloat
        let a: CGFloat
        if hex.count == 8 {
            r = CGFloat((number >> 24) & 0xff) / 255.0
            g = CGFloat((number >> 16) & 0xff) / 255.0
            b = CGFloat((number >> 8) & 0xff) / 255.0
            a = CGFloat(number & 0xff) / 255.0
        } else {
            r = CGFloat((number >> 16) & 0xff) / 255.0
            g = CGFloat((number >> 8) & 0xff) / 255.0
            b = CGFloat(number & 0xff) / 255.0
            a = 1.0
        }
        return NSColor(calibratedRed: r, green: g, blue: b, alpha: a)
    }
}

@MainActor
final class ResponseBubbleModel: ObservableObject {
    enum Mode {
        case hidden
        case thinking
        case listening
        case reply
        case notice
    }

    @Published var mode: Mode = .hidden
    @Published var fullText = ""
    @Published var revealedCount = 0
    @Published var dotCount = 1
    var isFinishing = false

    let onTap: () -> Void
    let config: ResponseBubbleConfig

    init(onTap: @escaping () -> Void, config: ResponseBubbleConfig) {
        self.onTap = onTap
        self.config = config
    }

    var displayText: String {
        switch mode {
        case .thinking:
            return String(repeating: ".", count: dotCount)
        default:
            return visibleReplyText
        }
    }

    var hasUnrevealedWords: Bool {
        revealedCount < words.count
    }

    private var words: [String] {
        fullText.split(whereSeparator: { $0.isWhitespace }).map(String.init)
    }

    private var visibleReplyText: String {
        let revealed = Array(words.prefix(revealedCount))
        guard !revealed.isEmpty else { return fullText.isEmpty ? "" : " " }
        return revealed.suffix(54).joined(separator: " ")
    }

    func resetText() {
        fullText = ""
        revealedCount = 0
        isFinishing = false
    }

    func setInstantText(_ text: String) {
        fullText = text
        revealedCount = words.count
        isFinishing = false
    }

    func appendChunk(_ chunk: String) {
        fullText += chunk
    }

    func replaceBufferedText(_ text: String) {
        let previousCount = revealedCount
        fullText = text
        revealedCount = min(previousCount, words.count)
    }

    func revealNextWord() {
        revealedCount = min(revealedCount + 1, words.count)
    }
}

private struct ResponseBubbleView: View {
    @ObservedObject var model: ResponseBubbleModel

    var body: some View {
        HStack(spacing: 0) {
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color(nsColor: model.config.bubbleColor))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(Color.white.opacity(0.08), lineWidth: 1)
                    )

                Text(model.displayText.isEmpty ? " " : model.displayText)
                    .font(.system(size: 13))
                    .foregroundStyle(textColor)
                    .lineLimit(model.config.lines)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
            }
            .frame(width: model.config.bubbleWidth, height: model.config.bubbleHeight)

            BubbleTail()
                .fill(Color(nsColor: model.config.bubbleColor))
                .frame(width: model.config.tailWidth, height: 18)
        }
        .frame(width: model.config.panelWidth, height: model.config.panelHeight)
        .contentShape(Rectangle())
        .onTapGesture {
            model.onTap()
        }
    }

    private var textColor: Color {
        switch model.mode {
        case .thinking:
            return Color(nsColor: NSColor(calibratedRed: 0.62, green: 0.62, blue: 0.72, alpha: 1.0))
        case .listening:
            return Color(nsColor: .systemBlue)
        case .notice:
            return Color(nsColor: .systemYellow)
        default:
            return Color(nsColor: model.config.textColor)
        }
    }
}

private struct BubbleTail: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.midY - rect.height / 2))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.midY + rect.height / 2))
        path.closeSubpath()
        return path
    }
}
