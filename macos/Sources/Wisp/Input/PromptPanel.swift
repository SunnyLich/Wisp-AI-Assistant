import AppKit
import SwiftUI

enum PromptMode: String, CaseIterable, Identifiable {
    case echo = "Echo"
    case query = "Query"
    case queryScreen = "Query+Screen"

    var id: String { rawValue }

    var method: String {
        switch self {
        case .echo:  return "brain.echo"
        case .query, .queryScreen: return "brain.query"
        }
    }
}

@MainActor
final class PromptPanel: NSPanel {

    private let model: PromptModel

    init(onSubmit: @escaping (String, PromptMode) -> Void) {
        self.model = PromptModel(onSubmit: onSubmit)

        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 300),
            styleMask: [.titled, .closable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Wisp"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        isMovableByWindowBackground = true
        hidesOnDeactivate = false

        contentView = NSHostingView(rootView: PromptView(model: model))
        center()
    }

    func showPrompt() {
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        model.focusToken += 1
    }

    func beginRequest(mode: PromptMode) {
        model.isBusy = true
        model.response = "\(mode.rawValue) started..."
    }

    func appendChunk(_ text: String) {
        model.response += text
    }

    func setResponse(_ text: String) {
        model.response = text
    }

    func setPrompt(_ text: String) {
        model.prompt = text
    }

    func currentPrompt() -> String {
        model.prompt
    }

    func currentResponse() -> String {
        model.response
    }

    func finishRequest() {
        model.isBusy = false
    }

    func failRequest(_ message: String) {
        model.isBusy = false
        model.response = "Error: \(message)"
    }
}

@MainActor
final class PromptModel: ObservableObject {
    @Published var prompt = "the brain seam works"
    @Published var mode: PromptMode = .echo
    @Published var response = ""
    @Published var isBusy = false
    @Published var focusToken = 0

    private let onSubmit: (String, PromptMode) -> Void

    init(onSubmit: @escaping (String, PromptMode) -> Void) {
        self.onSubmit = onSubmit
    }

    func submit() {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isBusy else { return }
        response = ""
        onSubmit(trimmed, mode)
    }
}

private struct PromptView: View {
    @ObservedObject var model: PromptModel
    @FocusState private var promptFocused: Bool

    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                Picker("", selection: $model.mode) {
                    ForEach(PromptMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .frame(width: 280)

                Spacer()

                Button("Send") {
                    model.submit()
                }
                .keyboardShortcut(.return, modifiers: [.command])
                .disabled(model.isBusy || model.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }

            TextField("Ask Wisp", text: $model.prompt)
                .textFieldStyle(.roundedBorder)
                .focused($promptFocused)
                .onSubmit { model.submit() }

            ScrollView {
                Text(model.response.isEmpty ? " " : model.response)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(10)
            }
            .frame(minHeight: 150)
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 6))

            HStack {
                ProgressView()
                    .opacity(model.isBusy ? 1 : 0)
                    .controlSize(.small)

                Spacer()

                Text(caption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(width: 460, height: 300)
        .onChange(of: model.focusToken) { _ in
            promptFocused = true
        }
        .onAppear {
            promptFocused = true
        }
    }

    private var caption: String {
        switch model.mode {
        case .echo:
            return "Local streaming smoke"
        case .query:
            return "Full brain.query path"
        case .queryScreen:
            return "brain.query with screenshot"
        }
    }
}
