import AppKit
import SwiftUI

struct PluginSummary: Identifiable, Equatable {
    var id: String { "\(name)|\(path)" }
    var name: String
    var path: String
    var status: String
    var hooks: [String]
    var trayActions: [String]
    var tools: [String]
    var error: String

    init?(payload: [String: Any]) {
        guard let name = payload["name"] as? String, !name.isEmpty else { return nil }
        self.name = name
        self.path = payload["path"] as? String ?? ""
        self.status = payload["status"] as? String ?? "unknown"
        self.hooks = payload["hooks"] as? [String] ?? []
        self.trayActions = payload["tray_actions"] as? [String] ?? []
        self.tools = payload["tools"] as? [String] ?? []
        self.error = payload["error"] as? String ?? ""
    }
}

@MainActor
final class PluginManagerPanel: NSPanel {

    private let model: PluginManagerModel

    init(
        onRefresh: @escaping () -> Void,
        onRunAction: @escaping (PluginSummary, String) -> Void,
        onOpenFolder: @escaping (String) -> Void
    ) {
        self.model = PluginManagerModel(
            onRefresh: onRefresh,
            onRunAction: onRunAction,
            onOpenFolder: onOpenFolder
        )
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 620, height: 500),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Plugin Manager"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 520, height: 380)
        contentView = NSHostingView(rootView: PluginManagerView(model: model))
        center()
    }

    func showPluginManager() {
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        model.refresh()
    }

    func beginLoading(_ status: String) {
        model.isLoading = true
        model.status = status
    }

    func setPlugins(_ plugins: [PluginSummary], pluginsDir: String) {
        model.plugins = plugins
        model.pluginsDir = pluginsDir
        model.isLoading = false
        model.status = plugins.isEmpty ? "No plugins loaded" : "\(plugins.count) plugin\(plugins.count == 1 ? "" : "s")"
    }

    func fail(_ message: String) {
        model.isLoading = false
        model.status = "Plugin error"
        model.errorText = message
    }
}

@MainActor
private final class PluginManagerModel: ObservableObject {
    @Published var plugins: [PluginSummary] = []
    @Published var pluginsDir = ""
    @Published var status = "Ready"
    @Published var errorText = ""
    @Published var isLoading = false

    private let onRefresh: () -> Void
    private let onRunAction: (PluginSummary, String) -> Void
    private let onOpenFolder: (String) -> Void

    init(
        onRefresh: @escaping () -> Void,
        onRunAction: @escaping (PluginSummary, String) -> Void,
        onOpenFolder: @escaping (String) -> Void
    ) {
        self.onRefresh = onRefresh
        self.onRunAction = onRunAction
        self.onOpenFolder = onOpenFolder
    }

    func refresh() {
        errorText = ""
        onRefresh()
    }

    func openFolder() {
        guard !pluginsDir.isEmpty else { return }
        onOpenFolder(pluginsDir)
    }

    func openFolder(_ path: String) {
        guard !path.isEmpty else { return }
        onOpenFolder(path)
    }

    func runAction(_ plugin: PluginSummary, label: String) {
        guard !isLoading else { return }
        status = "Running \(label)..."
        onRunAction(plugin, label)
    }
}

private struct PluginManagerView: View {
    @ObservedObject var model: PluginManagerModel

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            pluginList
            Divider()
            footer
        }
        .frame(minWidth: 520, minHeight: 380)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("Plugin Manager")
                .font(.system(size: 15, weight: .semibold))
            Text(model.status)
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Spacer()
            if model.isLoading {
                ProgressView()
                    .controlSize(.small)
            }
            Button {
                model.refresh()
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .help("Refresh")
        }
        .padding(.horizontal, 14)
        .frame(height: 42)
    }

    private var pluginList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                if !model.errorText.isEmpty {
                    Text(model.errorText)
                        .font(.system(size: 12))
                        .foregroundStyle(.red)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if model.plugins.isEmpty {
                    Text("No plugins loaded.")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, minHeight: 260)
                } else {
                    ForEach(model.plugins) { plugin in
                        PluginSummaryRow(
                            plugin: plugin,
                            onOpenFolder: { model.openFolder(plugin.path) },
                            onRunAction: { action in model.runAction(plugin, label: action) }
                        )
                    }
                }
            }
            .padding(14)
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Text(model.pluginsDir)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            Spacer()
            Button {
                model.openFolder()
            } label: {
                Image(systemName: "folder")
            }
            .help("Open plugins folder")
            .disabled(model.pluginsDir.isEmpty)
        }
        .padding(12)
    }
}

private struct PluginSummaryRow: View {
    var plugin: PluginSummary
    var onOpenFolder: () -> Void
    var onRunAction: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(plugin.name)
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(1)
                Text(plugin.status)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(
                        Capsule()
                            .fill(Color(nsColor: NSColor.quaternaryLabelColor))
                    )
                Spacer()
                Button {
                    onOpenFolder()
                } label: {
                    Image(systemName: "folder")
                }
                .buttonStyle(.borderless)
                .help("Open plugin folder")
                .disabled(plugin.path.isEmpty)
            }

            if !plugin.path.isEmpty {
                Text(plugin.path)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }

            PluginTagLine(title: "Hooks", values: plugin.hooks)
            PluginTagLine(title: "Tools", values: plugin.tools)
            if !plugin.trayActions.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Text("Tray")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 36, alignment: .trailing)
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 6)], alignment: .leading, spacing: 6) {
                        ForEach(plugin.trayActions, id: \.self) { action in
                            Button {
                                onRunAction(action)
                            } label: {
                                Text(action)
                                    .font(.system(size: 10))
                                    .lineLimit(1)
                            }
                            .buttonStyle(.borderless)
                            .help("Run plugin action")
                        }
                    }
                }
            }

            if !plugin.error.isEmpty {
                Text(plugin.error)
                    .font(.system(size: 11))
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: NSColor.controlBackgroundColor))
        )
    }
}

private struct PluginTagLine: View {
    var title: String
    var values: [String]

    var body: some View {
        if !values.isEmpty {
            HStack(alignment: .top, spacing: 8) {
                Text(title)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .frame(width: 36, alignment: .trailing)
                FlowTags(values: values)
            }
        }
    }
}

private struct FlowTags: View {
    var values: [String]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 82), spacing: 6)], alignment: .leading, spacing: 6) {
            ForEach(values, id: \.self) { value in
                Text(value)
                    .font(.system(size: 10))
                    .lineLimit(1)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color(nsColor: NSColor.windowBackgroundColor))
                    )
            }
        }
    }
}
