import AppKit
import SwiftUI

/// The floating icon overlay — the macOS replacement for the Qt `Qt.Tool` window
/// that segfaulted under PyObjC (see MACOS_NATIVE_PLAN.md §0 and the
/// no-child-windows memory). Here it is a first-class `NSPanel`:
///
///   - `.nonactivatingPanel` so clicking it doesn't steal key focus from the
///     user's frontmost app (the whole point of an ambient assistant overlay).
///   - `.borderless`, `level = .floating`, joins all Spaces + fullscreen aux.
///   - SwiftUI content hosted via `NSHostingView` (AppKit owns the fragile window
///     graph; SwiftUI just draws — the architecture chosen in the plan).
///
/// Crucially, normal top-level windows (settings, chat) must be opened with
/// `parent: nil`, NOT as children of this panel — parenting a regular NSWindow to
/// an NSPanel is the documented Cocoa crash this whole rewrite avoids.
@MainActor
final class OverlayPanel: NSPanel {

    enum DollState: Hashable { case idle, listening, thinking, speaking }

    private let model: OverlayModel

    init(onTap: @escaping () -> Void = {}) {
        let model = OverlayModel(onTap: onTap)
        self.model = model
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: model.panelSize, height: model.panelSize),
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
        isMovableByWindowBackground = true

        contentView = NSHostingView(rootView: OverlayView(model: model))
        positionBottomTrailing()
    }

    func setState(_ state: DollState) { model.state = state }

    func toggleVisibility() {
        if isVisible {
            orderOut(nil)
        } else {
            positionBottomTrailing()
            orderFrontRegardless()
        }
    }

    private func positionBottomTrailing() {
        guard let screen = NSScreen.main else { return }
        let v = screen.visibleFrame
        let margin: CGFloat = 24
        setFrameOrigin(NSPoint(x: v.maxX - frame.width - margin,
                               y: v.minY + margin))
    }
}

/// Observable backing the SwiftUI overlay view.
@MainActor
final class OverlayModel: ObservableObject {
    @Published var state: OverlayPanel.DollState = .idle

    let onTap: () -> Void
    let iconSize: CGFloat
    private let images: [OverlayPanel.DollState: NSImage]

    init(onTap: @escaping () -> Void) {
        self.onTap = onTap
        self.iconSize = Self.loadIconSize()
        self.images = DollAssetLocator.loadImages()
    }

    func image(for state: OverlayPanel.DollState) -> NSImage? {
        images[state]
    }

    var panelSize: CGFloat {
        iconSize + 18
    }

    private static func loadIconSize() -> CGFloat {
        let values = WispConfig.loadValues()
        let raw = values["ICON_SIZE"] ?? values["DOLL_SIZE"] ?? "80"
        let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 80
        return CGFloat(max(32, min(160, parsed)))
    }
}

private struct OverlayView: View {
    @ObservedObject var model: OverlayModel

    private var color: Color {
        switch model.state {
        case .idle:      return .gray
        case .listening: return .blue
        case .thinking:  return .yellow
        case .speaking:  return .green
        }
    }

    var body: some View {
        Group {
            if let image = model.image(for: model.state) {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(width: model.iconSize, height: model.iconSize)
                    .shadow(radius: 6)
            } else {
                Circle()
                    .fill(color.opacity(0.9))
                    .overlay(Circle().stroke(.white.opacity(0.6), lineWidth: 2))
                    .frame(width: model.iconSize, height: model.iconSize)
                    .shadow(radius: 6)
            }
        }
        .padding(9)
        .contentShape(Rectangle())
        .onTapGesture {
            model.onTap()
        }
        .animation(.easeInOut(duration: 0.25), value: model.state)
    }
}

private enum DollAssetLocator {

    static func loadImages() -> [OverlayPanel.DollState: NSImage] {
        let names: [OverlayPanel.DollState: String] = [
            .idle: "idle.png",
            .listening: "listening.png",
            .thinking: "thinking.png",
            .speaking: "speaking.png",
        ]

        for directory in candidateDirectories() {
            var images: [OverlayPanel.DollState: NSImage] = [:]
            for (state, name) in names {
                let url = directory.appendingPathComponent(name)
                if let image = NSImage(contentsOf: url) {
                    images[state] = image
                }
            }
            if images[.idle] != nil {
                return images
            }
        }
        return [:]
    }

    private static func candidateDirectories() -> [URL] {
        var directories: [URL] = []
        if let resourceURL = Bundle.main.resourceURL {
            directories.append(resourceURL.appendingPathComponent("assets/doll"))
            directories.append(resourceURL.appendingPathComponent("doll"))
        }
        if let repoRoot = ProcessInfo.processInfo.environment["WISP_REPO_ROOT"] {
            directories.append(URL(fileURLWithPath: repoRoot).appendingPathComponent("assets/doll"))
        }
        directories.append(
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../assets/doll")
                .standardizedFileURL
        )
        return directories
    }
}
