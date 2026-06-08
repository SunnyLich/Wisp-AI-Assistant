import XCTest
import Foundation

final class PluginManagerPanelTests: XCTestCase {

    func testPluginManagerExposesNativePluginContract() throws {
        let panel = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/PluginsUI/PluginManagerPanel.swift"),
            encoding: .utf8
        )
        let appDelegate = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "struct PluginSummary",
            "var hooks: [String]",
            "var trayActions: [String]",
            "var tools: [String]",
            "payload[\"tray_actions\"]",
            "PluginTagLine(title: \"Hooks\"",
            "PluginTagLine(title: \"Tools\"",
            "ForEach(plugin.trayActions",
            "onRunAction(action)",
            "model.openFolder(plugin.path)",
            "model.refresh()",
        ] {
            XCTAssertTrue(panel.contains(expected), "PluginManagerPanel is missing \(expected).")
        }

        for expected in [
            "pluginPanel = PluginManagerPanel",
            "brain.plugins.list",
            "brain.plugins.run_action",
            "showNativePluginManager()",
            "pluginPanel?.setPlugins",
            "pluginPanel?.fail",
        ] {
            XCTAssertTrue(appDelegate.contains(expected), "AppDelegate plugin wiring is missing \(expected).")
        }
    }

    func testPluginManagerStaysGenericPythonPluginHost() throws {
        let panel = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/PluginsUI/PluginManagerPanel.swift"),
            encoding: .utf8
        )
        let appDelegate = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )
        let repoRoot = sourceRoot().deletingLastPathComponent()
        let overview = try String(
            contentsOf: repoRoot.appendingPathComponent("docs/OVERVIEW.md"),
            encoding: .utf8
        )
        let parity = try String(
            contentsOf: repoRoot.appendingPathComponent("docs/MACOS_PARITY.md"),
            encoding: .utf8
        )
        let readme = try String(
            contentsOf: sourceRoot().appendingPathComponent("README.md"),
            encoding: .utf8
        )

        for expected in [
            "Plugin authors should not write Swift",
            "plugins/<name>/__init__.py",
            "brain.plugins.list",
            "brain.plugins.run_action",
            "generic host",
        ] {
            XCTAssertTrue(overview.contains(expected), "Overview plugin contract is missing \(expected).")
        }

        for expected in [
            "Plugins are shared Python/runtime extensions",
            "Plugin authors should implement hooks, tray actions, and tools once",
            "Swift must stay a generic metadata/action host",
        ] {
            XCTAssertTrue(parity.contains(expected), "Parity plugin rule is missing \(expected).")
        }

        for expected in [
            "Plugins remain shared Python/runtime extensions",
            "Plugin authors should not write Swift for macOS support",
            "The native Swift plugin manager reads generic metadata from `brain.plugins.list`",
            "runs declared tray actions through `brain.plugins.run_action`",
        ] {
            XCTAssertTrue(readme.contains(expected), "macOS README plugin contract is missing \(expected).")
        }

        for expected in [
            "struct PluginSummary",
            "payload[\"hooks\"]",
            "payload[\"tray_actions\"]",
            "payload[\"tools\"]",
            "onRunAction(plugin, label)",
            "Button",
        ] {
            XCTAssertTrue(panel.contains(expected), "PluginManagerPanel generic host is missing \(expected).")
        }

        XCTAssertFalse(panel.contains("import PythonKit"), "Swift plugin UI must not import plugin Python directly.")
        XCTAssertFalse(appDelegate.contains("import PythonKit"), "AppDelegate must use the brain sidecar, not direct plugin Python imports.")
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
