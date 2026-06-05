import AppKit
import SwiftUI

@MainActor
final class ResponseBubblePanel: NSPanel {

    private let model: ResponseBubbleModel
    private var dotsTimer: Timer?
    private var hideTimer: Timer?

    init(onTap: @escaping () -> Void = {}) {
        self.model = ResponseBubbleModel(onTap: onTap)
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 356, height: 96),
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
        model.mode = .thinking
        model.text = ""
        model.dotCount = 1
        reposition(anchor: anchor)
        orderFrontRegardless()
        startDots()
    }

    func showListening(anchor: NSRect?) {
        hideTimer?.invalidate()
        stopDots()
        model.mode = .listening
        model.text = "Recording - release to send"
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
        model.text += chunk
        reposition(anchor: nil)
        orderFrontRegardless()
    }

    func setText(_ text: String) {
        hideTimer?.invalidate()
        stopDots()
        model.mode = .reply
        model.text = text
        orderFrontRegardless()
    }

    func showNotice(_ text: String, anchor: NSRect?, timeout: TimeInterval = 6.0) {
        hideTimer?.invalidate()
        stopDots()
        model.mode = .notice
        model.text = text
        reposition(anchor: anchor)
        orderFrontRegardless()
        scheduleHide(after: timeout)
    }

    func finish() {
        stopDots()
        if model.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            model.mode = .notice
            model.text = "No reply from model. Check model name or API key in Settings."
        }
        scheduleHide(after: 3.5)
    }

    func clear() {
        hideTimer?.invalidate()
        stopDots()
        model.text = ""
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

    private func scheduleHide(after delay: TimeInterval) {
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.orderOut(nil)
            }
        }
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
    @Published var text = ""
    @Published var dotCount = 1

    let onTap: () -> Void

    init(onTap: @escaping () -> Void) {
        self.onTap = onTap
    }

    var displayText: String {
        switch mode {
        case .thinking:
            return String(repeating: ".", count: dotCount)
        default:
            return text
        }
    }
}

private struct ResponseBubbleView: View {
    @ObservedObject var model: ResponseBubbleModel

    var body: some View {
        HStack(spacing: 0) {
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color(nsColor: NSColor(calibratedRed: 0.11, green: 0.11, blue: 0.15, alpha: 0.92)))
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(Color.white.opacity(0.08), lineWidth: 1)
                    )

                Text(model.displayText.isEmpty ? " " : model.displayText)
                    .font(.system(size: 13))
                    .foregroundStyle(textColor)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
            }
            .frame(width: 340, height: 88)

            BubbleTail()
                .fill(Color(nsColor: NSColor(calibratedRed: 0.11, green: 0.11, blue: 0.15, alpha: 0.92)))
                .frame(width: 12, height: 18)
        }
        .frame(width: 356, height: 96)
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
            return Color(nsColor: NSColor(calibratedWhite: 0.92, alpha: 1.0))
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
