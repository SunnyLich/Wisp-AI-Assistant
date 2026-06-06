import AppKit
import SwiftUI

@MainActor
final class AgentDiffPanel: NSPanel {

    private let model: AgentDiffModel

    init(onOpenFolder: @escaping (String) -> Void) {
        self.model = AgentDiffModel(onOpenFolder: onOpenFolder)
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 900, height: 640),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Agent Diff"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 680, height: 420)
        contentView = NSHostingView(rootView: AgentDiffPanelView(model: model))
        center()
    }

    func showDiff(title: String, runDir: String, diffPatch: String) {
        model.title = title
        model.runDir = runDir
        model.diffPatch = diffPatch
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@MainActor
private final class AgentDiffModel: ObservableObject {
    @Published var title = "Agent diff"
    @Published var runDir = ""
    @Published var diffPatch = ""

    private let onOpenFolder: (String) -> Void

    init(onOpenFolder: @escaping (String) -> Void) {
        self.onOpenFolder = onOpenFolder
    }

    func openFolder() {
        guard !runDir.isEmpty else { return }
        onOpenFolder(runDir)
    }
}

private struct AgentDiffPanelView: View {
    @ObservedObject var model: AgentDiffModel

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            DiffTextScrollView(text: model.diffPatch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "(no diff artifact)" : model.diffPatch)
        }
        .frame(minWidth: 680, minHeight: 420)
    }

    private var header: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(model.title.isEmpty ? "Agent diff" : model.title)
                    .font(.system(size: 15, weight: .semibold))
                    .lineLimit(1)
                Text(model.runDir)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }
            Spacer()
            Button {
                model.openFolder()
            } label: {
                Image(systemName: "folder")
            }
            .help("Open selected run folder")
            .buttonStyle(.borderless)
            .disabled(model.runDir.isEmpty)
        }
        .padding(12)
    }
}

private struct DiffTextScrollView: View {
    var text: String

    var body: some View {
        ScrollView {
            Text(text)
                .font(.system(size: 12, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
        }
    }
}
