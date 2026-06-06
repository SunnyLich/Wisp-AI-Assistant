import Foundation
import Darwin

enum ScreenshotMode: String {
    case off
    case auto
    case model

    static func normalized(_ raw: String?, default fallback: ScreenshotMode = .off) -> ScreenshotMode {
        let value = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch value {
        case "auto", "on", "true", "1", "yes", "always":
            return .auto
        case "model", "decide", "ask", "tool", "tools":
            return .model
        case "off", "false", "0", "no", "none", "":
            return .off
        default:
            return fallback
        }
    }
}

struct IntentConfig: Equatable {
    var key: String
    var label: String
    var hint: String
    var prompt: String
}

struct CallerConfig: Equatable {
    var hotkey: String
    var label: String
    var pasteBack: Bool
    var customKey: String
    var contextAmbient: Bool
    var contextDocuments: Bool
    var contextTools: Bool
    var contextScreenshot: ScreenshotMode
    var contextClipboard: Bool
    var intents: [IntentConfig]
}

struct WispConfig: Equatable {
    var callers: [CallerConfig]
    var snip: SnipConfig

    static func load(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        readDotEnv: Bool = true
    ) -> WispConfig {
        let values = loadValues(environment: environment, readDotEnv: readDotEnv)
        return WispConfig(callers: loadCallers(values), snip: loadSnip(values))
    }

    static func repoRoot(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        resourceURL: URL? = Bundle.main.resourceURL,
        applicationSupportBaseDirectory: URL? = nil,
        fileManager: FileManager = .default
    ) -> URL {
        if let path = environment["WISP_REPO_ROOT"]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        if let resourceURL,
           let devRoot = repoRootForDevBundle(resourceURL: resourceURL, fileManager: fileManager) {
            return devRoot
        }
        if currentDirectory.lastPathComponent == "macos" {
            return currentDirectory.deletingLastPathComponent()
        }
        if fileManager.fileExists(atPath: currentDirectory.appendingPathComponent("macos/brain").path) {
            return currentDirectory
        }
        return userConfigRoot(baseDirectory: applicationSupportBaseDirectory, fileManager: fileManager)
    }

    static func seedProcessEnvironmentIfMissing() {
        let environment = ProcessInfo.processInfo.environment
        guard missingEnvironmentValue(environment["WISP_REPO_ROOT"]) else { return }
        let root = repoRoot(environment: environment)
        setenv("WISP_REPO_ROOT", root.path, 0)
        NSLog("[wisp] inferred config root: %@", root.path)
    }

    static func loadValues(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        readDotEnv: Bool = true,
        currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        resourceURL: URL? = Bundle.main.resourceURL,
        applicationSupportBaseDirectory: URL? = nil,
        fileManager: FileManager = .default
    ) -> [String: String] {
        let root = repoRoot(
            environment: environment,
            currentDirectory: currentDirectory,
            resourceURL: resourceURL,
            applicationSupportBaseDirectory: applicationSupportBaseDirectory,
            fileManager: fileManager
        )
        let fileValues = readDotEnv ? DotEnvFile.read(root.appendingPathComponent(".env")) : [:]
        return fileValues.merging(environment) { _, environmentValue in environmentValue }
    }

    private static func repoRootForDevBundle(resourceURL: URL, fileManager: FileManager) -> URL? {
        let repoRoot = resourceURL
            .deletingLastPathComponent() // Contents
            .deletingLastPathComponent() // Wisp.app
            .deletingLastPathComponent() // WispNative
            .deletingLastPathComponent() // build
            .deletingLastPathComponent()
        let brain = repoRoot.appendingPathComponent("macos/brain")
        guard fileManager.fileExists(atPath: brain.path) else {
            return nil
        }
        return repoRoot
    }

    private static func userConfigRoot(baseDirectory: URL?, fileManager: FileManager) -> URL {
        let applicationSupport = baseDirectory
            ?? fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
        return applicationSupport.appendingPathComponent("Wisp", isDirectory: true)
    }

    private static func missingEnvironmentValue(_ value: String?) -> Bool {
        value?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true
    }

    private static func loadCallers(_ values: [String: String]) -> [CallerConfig] {
        let defaults = defaultCallers
        let count = intValue(values["CALLER_COUNT"], default: defaults.count)
        return (0..<max(0, count)).map { index in
            let n = index + 1
            let defaultCaller = index < defaults.count ? defaults[index] : CallerConfig.empty
            let intentCount = intValue(values["CALLER_\(n)_INTENT_COUNT"], default: defaultCaller.intents.count)
            let intents = (0..<max(0, intentCount)).map { intentIndex in
                let m = intentIndex + 1
                let defaultIntent = intentIndex < defaultCaller.intents.count ? defaultCaller.intents[intentIndex] : IntentConfig.empty
                return IntentConfig(
                    key: values["CALLER_\(n)_INTENT_\(m)_KEY"] ?? defaultIntent.key,
                    label: values["CALLER_\(n)_INTENT_\(m)_LABEL"] ?? defaultIntent.label,
                    hint: values["CALLER_\(n)_INTENT_\(m)_HINT"] ?? defaultIntent.hint,
                    prompt: values["CALLER_\(n)_INTENT_\(m)_PROMPT"] ?? defaultIntent.prompt
                )
            }

            return CallerConfig(
                hotkey: values["CALLER_\(n)_HOTKEY"] ?? defaultCaller.hotkey,
                label: values["CALLER_\(n)_LABEL"] ?? defaultCaller.label,
                pasteBack: boolValue(values["CALLER_\(n)_PASTE_BACK"], default: defaultCaller.pasteBack),
                customKey: values["CALLER_\(n)_CUSTOM_KEY"] ?? defaultCaller.customKey,
                contextAmbient: boolValue(values["CALLER_\(n)_CONTEXT_AMBIENT"], default: defaultCaller.contextAmbient),
                contextDocuments: boolValue(values["CALLER_\(n)_CONTEXT_DOCUMENTS"], default: defaultCaller.contextDocuments),
                contextTools: boolValue(values["CALLER_\(n)_CONTEXT_TOOLS"], default: defaultCaller.contextTools),
                contextScreenshot: ScreenshotMode.normalized(
                    values["CALLER_\(n)_CONTEXT_SCREENSHOT"],
                    default: defaultCaller.contextScreenshot
                ),
                contextClipboard: boolValue(values["CALLER_\(n)_CONTEXT_CLIPBOARD"], default: defaultCaller.contextClipboard),
                intents: intents
            )
        }
    }

    private static func loadSnip(_ values: [String: String]) -> SnipConfig {
        SnipConfig(
            hotkey: values["HOTKEY_SNIP"] ?? "ctrl+alt+q",
            contextAmbient: boolValue(values["SNIP_CONTEXT_AMBIENT"], default: true),
            contextDocuments: boolValue(values["SNIP_CONTEXT_DOCUMENTS"], default: false),
            contextTools: boolValue(values["SNIP_CONTEXT_TOOLS"], default: false)
        )
    }

    private static func boolValue(_ raw: String?, default fallback: Bool) -> Bool {
        guard let raw else { return fallback }
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "1", "true", "yes", "on":
            return true
        case "0", "false", "no", "off":
            return false
        default:
            return fallback
        }
    }

    private static func intValue(_ raw: String?, default fallback: Int) -> Int {
        guard let raw, let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            return fallback
        }
        return value
    }

    static let defaultCallers: [CallerConfig] = [
        CallerConfig(
            hotkey: "ctrl+q",
            label: "General",
            pasteBack: false,
            customKey: "s",
            contextAmbient: true,
            contextDocuments: true,
            contextTools: true,
            contextScreenshot: .off,
            contextClipboard: false,
            intents: [
                IntentConfig(
                    key: "w",
                    label: "What is this?",
                    hint: "Quick explanation, plain English",
                    prompt: "What is this? Give me a clear, plain-English explanation in 2-3 sentences."
                ),
                IntentConfig(
                    key: "a",
                    label: "Explain simply",
                    hint: "ELI5 - no jargon",
                    prompt: "Explain this as simply as possible. Assume I have no technical background whatsoever."
                ),
                IntentConfig(
                    key: "d",
                    label: "How do I fix this?",
                    hint: "Debug, fix, or rewrite it",
                    prompt: "How do I fix this? Give me: 1, what error is this in 1 sentence; 2, concise, actionable steps I can follow right now."
                ),
            ]
        ),
        CallerConfig(
            hotkey: "ctrl+shift+q",
            label: "Rewrite & Paste",
            pasteBack: true,
            customKey: "s",
            contextAmbient: true,
            contextDocuments: false,
            contextTools: false,
            contextScreenshot: .off,
            contextClipboard: false,
            intents: [
                IntentConfig(
                    key: "w",
                    label: "Fix grammar",
                    hint: "Correct spelling and grammar",
                    prompt: "Fix the grammar and spelling of the following text. Output ONLY the corrected text."
                ),
                IntentConfig(
                    key: "a",
                    label: "Simplify",
                    hint: "Make it easier to read",
                    prompt: "Simplify the following text for a general audience. Output ONLY the simplified text."
                ),
                IntentConfig(
                    key: "d",
                    label: "Improve tone",
                    hint: "Polish for clarity and style",
                    prompt: "Rewrite the following text to sound more professional and polished. Output ONLY the rewritten text."
                ),
            ]
        ),
    ]
}

struct SnipConfig: Equatable {
    var hotkey: String
    var contextAmbient: Bool
    var contextDocuments: Bool
    var contextTools: Bool
}

extension CallerConfig {
    static let empty = CallerConfig(
        hotkey: "",
        label: "",
        pasteBack: false,
        customKey: "s",
        contextAmbient: true,
        contextDocuments: true,
        contextTools: true,
        contextScreenshot: .off,
        contextClipboard: false,
        intents: []
    )
}

private extension IntentConfig {
    static let empty = IntentConfig(key: "", label: "", hint: "", prompt: "")
}

enum DotEnvFile {
    static func read(_ url: URL) -> [String: String] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [:] }
        return readValues(fromText: text)
    }

    static func readValues(fromText text: String) -> [String: String] {
        var values: [String: String] = [:]
        for rawLine in text.split(whereSeparator: { $0.isNewline }) {
            let line = String(rawLine).trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") || !line.contains("=") {
                continue
            }
            let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard parts.count == 2 else { continue }
            let key = String(parts[0]).trimmingCharacters(in: .whitespaces)
            let value = parseValue(String(parts[1]))
            if !key.isEmpty {
                values[key] = value
            }
        }
        return values
    }

    static func write(
        _ updates: [String: String],
        removing removeKeys: Set<String> = [],
        removingPrefixes removePrefixes: [String] = [],
        to url: URL
    ) throws {
        let current = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        let rendered = renderUpdating(
            current,
            updates: updates,
            removing: removeKeys,
            removingPrefixes: removePrefixes
        )
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try rendered.write(to: url, atomically: true, encoding: .utf8)
    }

    static func renderUpdating(
        _ text: String,
        updates: [String: String],
        removing removeKeys: Set<String> = [],
        removingPrefixes removePrefixes: [String] = []
    ) -> String {
        var seen: Set<String> = []
        var lines: [String] = []

        for rawLine in text.components(separatedBy: .newlines) {
            guard let key = key(in: rawLine) else {
                lines.append(rawLine)
                continue
            }

            if let value = updates[key] {
                lines.append("\(key)=\(formatValue(value))")
                seen.insert(key)
            } else if shouldRemove(key, removeKeys: removeKeys, removePrefixes: removePrefixes) {
                continue
            } else {
                lines.append(rawLine)
            }
        }

        let newKeys = updates.keys
            .filter { !seen.contains($0) }
            .sorted()

        if !newKeys.isEmpty, lines.contains(where: { !$0.isEmpty }) {
            while lines.last == "" {
                lines.removeLast()
            }
            lines.append("")
        }

        for key in newKeys {
            lines.append("\(key)=\(formatValue(updates[key] ?? ""))")
        }

        return lines.joined(separator: "\n") + "\n"
    }

    private static func parseValue(_ raw: String) -> String {
        var value = raw.trimmingCharacters(in: .whitespaces)
        if let hash = value.firstIndex(of: "#"), !value.hasPrefix("\""), !value.hasPrefix("'") {
            value = String(value[..<hash]).trimmingCharacters(in: .whitespaces)
        }
        if value.count >= 2 {
            let first = value.first
            let last = value.last
            if (first == "\"" && last == "\"") || (first == "'" && last == "'") {
                value = String(value.dropFirst().dropLast())
            }
        }
        return value
            .replacingOccurrences(of: "\\n", with: "\n")
            .replacingOccurrences(of: "\\\"", with: "\"")
            .replacingOccurrences(of: "\\\\", with: "\\")
    }

    private static func key(in line: String) -> String? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, !trimmed.hasPrefix("#"), trimmed.contains("=") else {
            return nil
        }
        let parts = trimmed.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
        guard let rawKey = parts.first else { return nil }
        let key = String(rawKey).trimmingCharacters(in: .whitespaces)
        return key.isEmpty ? nil : key
    }

    private static func shouldRemove(_ key: String, removeKeys: Set<String>, removePrefixes: [String]) -> Bool {
        removeKeys.contains(key) || removePrefixes.contains { key.hasPrefix($0) }
    }

    private static func formatValue(_ value: String) -> String {
        let needsQuotes = value.isEmpty
            || value.contains(where: { $0.isWhitespace })
            || value.contains("#")
            || value.contains("\"")
            || value.contains("'")
            || value.contains("\\")
        guard needsQuotes else { return value }
        let escaped = value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
        return "\"\(escaped)\""
    }
}
