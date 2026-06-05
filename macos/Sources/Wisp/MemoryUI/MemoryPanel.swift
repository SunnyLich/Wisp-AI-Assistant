import AppKit
import SwiftUI

struct MemoryFact: Identifiable, Equatable {
    var id: String
    var text: String
    var category: String
    var source: String
    var createdAt: String
    var lastSeen: String

    init(id: String, text: String, category: String, source: String = "", createdAt: String = "", lastSeen: String = "") {
        self.id = id
        self.text = text
        self.category = MemoryCategory.normalized(category)
        self.source = source
        self.createdAt = createdAt
        self.lastSeen = lastSeen
    }

    init?(payload: [String: Any]) {
        guard let id = payload["id"] as? String, !id.isEmpty else { return nil }
        self.id = id
        text = payload["text"] as? String ?? ""
        category = MemoryCategory.normalized(payload["category"] as? String ?? "general")
        source = payload["source"] as? String ?? ""
        createdAt = payload["created_at"] as? String ?? ""
        lastSeen = payload["last_seen"] as? String ?? ""
    }
}

struct MemoryCategoryOption: Identifiable, Equatable {
    let key: String
    let label: String
    var id: String { key }
}

enum MemoryCategory {
    static let general = "general"
    static let project = "project_context"
    static let all: [MemoryCategoryOption] = [
        MemoryCategoryOption(key: general, label: "General"),
        MemoryCategoryOption(key: project, label: "Project"),
    ]

    static func normalized(_ raw: String) -> String {
        let cleaned = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if cleaned == project || cleaned == "project" {
            return project
        }
        return general
    }

    static func label(for key: String) -> String {
        all.first(where: { $0.key == normalized(key) })?.label ?? "General"
    }
}

@MainActor
final class MemoryPanel: NSPanel {

    private let model: MemoryModel

    init(
        onRefresh: @escaping () -> Void,
        onAdd: @escaping (String, String) -> Void,
        onUpdate: @escaping (MemoryFact) -> Void,
        onDelete: @escaping (MemoryFact) -> Void,
        onSearch: @escaping (String) -> Void
    ) {
        self.model = MemoryModel(
            onRefresh: onRefresh,
            onAdd: onAdd,
            onUpdate: onUpdate,
            onDelete: onDelete,
            onSearch: onSearch
        )

        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 680, height: 540),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Memory"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 560, height: 420)
        contentView = NSHostingView(rootView: MemoryPanelView(model: model))
        center()
    }

    func showMemory() {
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

    func setFacts(_ facts: [MemoryFact]) {
        model.facts = facts
        model.isLoading = false
        model.status = facts.isEmpty ? "No saved facts" : "\(facts.count) saved fact\(facts.count == 1 ? "" : "s")"
    }

    func setSearchResult(query: String, text: String) {
        model.isLoading = false
        model.searchResultTitle = query
        model.searchResult = text.trimmingCharacters(in: .whitespacesAndNewlines)
        model.status = model.searchResult.isEmpty ? "No relevant memory found" : "Memory search complete"
    }

    func setStatus(_ status: String) {
        model.isLoading = false
        model.status = status
    }

    func fail(_ message: String) {
        model.isLoading = false
        model.status = "Memory error"
        model.errorText = message
    }
}

@MainActor
private final class MemoryModel: ObservableObject {
    @Published var facts: [MemoryFact] = []
    @Published var newText = ""
    @Published var newCategory = MemoryCategory.general
    @Published var searchText = ""
    @Published var searchResult = ""
    @Published var searchResultTitle = ""
    @Published var status = "Ready"
    @Published var errorText = ""
    @Published var isLoading = false

    private let onRefresh: () -> Void
    private let onAdd: (String, String) -> Void
    private let onUpdate: (MemoryFact) -> Void
    private let onDelete: (MemoryFact) -> Void
    private let onSearch: (String) -> Void

    init(
        onRefresh: @escaping () -> Void,
        onAdd: @escaping (String, String) -> Void,
        onUpdate: @escaping (MemoryFact) -> Void,
        onDelete: @escaping (MemoryFact) -> Void,
        onSearch: @escaping (String) -> Void
    ) {
        self.onRefresh = onRefresh
        self.onAdd = onAdd
        self.onUpdate = onUpdate
        self.onDelete = onDelete
        self.onSearch = onSearch
    }

    var groupedFacts: [MemoryFactGroup] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let visible = facts.filter { fact in
            guard !query.isEmpty else { return true }
            return fact.text.lowercased().contains(query)
                || MemoryCategory.label(for: fact.category).lowercased().contains(query)
                || fact.source.lowercased().contains(query)
        }

        return MemoryCategory.all.compactMap { category in
            let groupFacts = visible
                .filter { $0.category == category.key }
                .sorted { lhs, rhs in
                    let left = lhs.lastSeen.isEmpty ? lhs.createdAt : lhs.lastSeen
                    let right = rhs.lastSeen.isEmpty ? rhs.createdAt : rhs.lastSeen
                    return left > right
                }
            return groupFacts.isEmpty ? nil : MemoryFactGroup(
                key: category.key,
                label: category.label,
                facts: groupFacts
            )
        }
    }

    func refresh() {
        errorText = ""
        onRefresh()
    }

    func addFact() {
        let text = newText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isLoading else { return }
        errorText = ""
        newText = ""
        onAdd(text, newCategory)
    }

    func updateFact(_ fact: MemoryFact) {
        guard !fact.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !isLoading else { return }
        errorText = ""
        onUpdate(fact)
    }

    func deleteFact(_ fact: MemoryFact) {
        guard !isLoading else { return }
        errorText = ""
        onDelete(fact)
    }

    func search() {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, !isLoading else { return }
        errorText = ""
        onSearch(query)
    }
}

private struct MemoryFactGroup: Identifiable {
    let key: String
    let label: String
    let facts: [MemoryFact]
    var id: String { key }
}

private struct MemoryPanelView: View {
    @ObservedObject var model: MemoryModel
    @FocusState private var addFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            searchBar
            Divider()
            factList
            Divider()
            addBar
        }
        .frame(minWidth: 560, minHeight: 420)
        .onAppear { addFocused = true }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("Memory")
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

    private var searchBar: some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                TextField("Search memory", text: $model.searchText)
                    .textFieldStyle(.plain)
                    .onSubmit { model.search() }
                Button {
                    model.search()
                } label: {
                    Image(systemName: "return")
                }
                .buttonStyle(.borderless)
                .disabled(model.searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isLoading)
                .help("Search relevant memory")
            }

            if !model.searchResult.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text(model.searchResultTitle)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Text(model.searchResult)
                        .font(.system(size: 12))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(10)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color(nsColor: NSColor.controlBackgroundColor))
                )
            }

            if !model.errorText.isEmpty {
                Text(model.errorText)
                    .font(.system(size: 12))
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var factList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if model.groupedFacts.isEmpty {
                    Text("No saved facts.")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, minHeight: 230)
                } else {
                    ForEach(model.groupedFacts) { group in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(group.label)
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(.secondary)
                            ForEach(group.facts) { fact in
                                MemoryFactRow(
                                    fact: fact,
                                    isLoading: model.isLoading,
                                    onSave: { model.updateFact($0) },
                                    onDelete: { model.deleteFact($0) }
                                )
                                .id("\(fact.id)-\(fact.text)-\(fact.category)")
                            }
                        }
                    }
                }
            }
            .padding(14)
        }
        .background(Color(nsColor: NSColor.windowBackgroundColor))
    }

    private var addBar: some View {
        HStack(spacing: 10) {
            TextField("Add fact", text: $model.newText, axis: .vertical)
                .lineLimit(1...3)
                .textFieldStyle(.roundedBorder)
                .focused($addFocused)
                .onSubmit { model.addFact() }

            Picker("", selection: $model.newCategory) {
                ForEach(MemoryCategory.all) { category in
                    Text(category.label).tag(category.key)
                }
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .frame(width: 160)

            Button {
                model.addFact()
            } label: {
                Image(systemName: "plus")
            }
            .help("Add fact")
            .disabled(model.newText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isLoading)
        }
        .padding(12)
    }
}

private struct MemoryFactRow: View {
    var fact: MemoryFact
    var isLoading: Bool
    var onSave: (MemoryFact) -> Void
    var onDelete: (MemoryFact) -> Void

    @State private var draftText: String
    @State private var draftCategory: String
    @State private var confirmingDelete = false

    init(
        fact: MemoryFact,
        isLoading: Bool,
        onSave: @escaping (MemoryFact) -> Void,
        onDelete: @escaping (MemoryFact) -> Void
    ) {
        self.fact = fact
        self.isLoading = isLoading
        self.onSave = onSave
        self.onDelete = onDelete
        _draftText = State(initialValue: fact.text)
        _draftCategory = State(initialValue: fact.category)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .top, spacing: 8) {
                TextField("Fact", text: $draftText, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                    .disabled(isLoading)

                Picker("", selection: $draftCategory) {
                    ForEach(MemoryCategory.all) { category in
                        Text(category.label).tag(category.key)
                    }
                }
                .labelsHidden()
                .frame(width: 110)
                .disabled(isLoading)

                Button {
                    onSave(updatedFact)
                } label: {
                    Image(systemName: "checkmark")
                }
                .buttonStyle(.borderless)
                .help("Save")
                .disabled(!hasChanges || draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isLoading)

                Button {
                    confirmingDelete = true
                } label: {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
                .help("Delete")
                .disabled(isLoading)
                .confirmationDialog("Delete this memory fact?", isPresented: $confirmingDelete) {
                    Button("Delete", role: .destructive) {
                        onDelete(fact)
                    }
                    Button("Cancel", role: .cancel) {}
                }
            }

            HStack(spacing: 8) {
                Text(MemoryCategory.label(for: fact.category))
                if !fact.source.isEmpty {
                    Text(fact.source)
                }
                if !fact.lastSeen.isEmpty {
                    Text(fact.lastSeen)
                }
            }
            .font(.system(size: 10))
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: NSColor.controlBackgroundColor))
        )
    }

    private var hasChanges: Bool {
        draftText.trimmingCharacters(in: .whitespacesAndNewlines) != fact.text
            || draftCategory != fact.category
    }

    private var updatedFact: MemoryFact {
        MemoryFact(
            id: fact.id,
            text: draftText.trimmingCharacters(in: .whitespacesAndNewlines),
            category: draftCategory,
            source: fact.source,
            createdAt: fact.createdAt,
            lastSeen: fact.lastSeen
        )
    }
}
