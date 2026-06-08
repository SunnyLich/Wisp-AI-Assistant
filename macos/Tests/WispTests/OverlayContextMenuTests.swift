import XCTest
import Foundation

final class OverlayContextMenuTests: XCTestCase {

    func testOverlayForwardsRightClickFromPanelAndHostedView() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/OverlayPanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("onRightClick: @escaping (NSEvent) -> Void"))
        XCTAssertTrue(source.contains("contentView = OverlayHostingView(rootView: OverlayView(model: model), onRightClick: onRightClick)"))
        XCTAssertTrue(source.contains("override func rightMouseDown(with event: NSEvent)"))
        XCTAssertTrue(source.contains("onRightClick(event)"))
    }

    func testOverlayHostingViewKeepsRequiredRootViewInitializer() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/OverlayPanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private final class OverlayHostingView: NSHostingView<OverlayView>"))
        XCTAssertTrue(source.contains("required init(rootView: OverlayView)"))
        XCTAssertTrue(source.contains("self.onRightClick = { _ in }"))
        XCTAssertTrue(source.contains("required dynamic init?(coder: NSCoder)"))
    }

    func testAppDelegateOverlayMenuMatchesPythonTrayOrder() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private func showOverlayMenu(_ event: NSEvent)"))
        XCTAssertTrue(source.contains("menu.popUp(positioning: nil, at: point, in: view)"))

        assertContainsInOrder(
            [
                "addItem(\"Ask Wisp\"",
                "addItem(\"Start agent task...\"",
                "addItem(\"Agent task history...\"",
                "addItem(\"New chat\"",
                "addItem(\"Last chat\"",
                "addItem(\"Hide icon\"",
                "addItem(\"Memory\"",
                "addItem(\"Plugin Manager\"",
                "addItem(\"Settings\"",
                "addItem(\"Snip Screen Region\"",
                "addItem(\"Open Run Logs\"",
                "addItem(\"Open Config Folder\"",
                "NSMenuItem(title: \"Quit\"",
            ],
            in: source
        )
    }

    func testStatusMenuKeepsPythonCoreOrderBeforeMacUtilities() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Tray/StatusItem.swift"),
            encoding: .utf8
        )

        assertContainsInOrder(
            [
                "addItem(\"Ask Wisp\"",
                "addItem(\"Run Echo Smoke\"",
                "addItem(\"Context Snapshot\"",
                "addItem(\"Capture Screen Smoke\"",
                "addItem(\"Start agent task...\"",
                "addItem(\"Agent task history...\"",
                "addItem(\"New chat\"",
                "addItem(\"Last chat\"",
                "addItem(\"Hide icon\"",
                "addItem(\"Memory\"",
                "addItem(\"Plugin Manager\"",
                "addItem(\"Settings\"",
                "addItem(\"Snip Screen Region\"",
                "addItem(\"Permissions\"",
                "NSMenuItem(title: \"Quit\"",
            ],
            in: source
        )
    }

    func testStatusItemUsesNativeTemplateIconWithAsciiFallback() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Tray/StatusItem.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private func configureStatusButton()"))
        XCTAssertTrue(source.contains("NSImage(systemSymbolName: \"sparkles\", accessibilityDescription: \"Wisp\")"))
        XCTAssertTrue(source.contains("image.isTemplate = true"))
        XCTAssertTrue(source.contains("button.image = image"))
        XCTAssertTrue(source.contains("button.title = \"\""))
        XCTAssertTrue(source.contains("button.title = \"W\""))
        XCTAssertFalse(source.contains("statusItem.button?.title = \"✦\""))
    }

    private func sourceRoot() -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let direct = currentDirectory.appendingPathComponent("Sources/Wisp")
        if FileManager.default.fileExists(atPath: direct.path) {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }

    private func assertContainsInOrder(
        _ needles: [String],
        in source: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        var searchStart = source.startIndex
        for needle in needles {
            guard let range = source.range(of: needle, range: searchStart..<source.endIndex) else {
                XCTFail("Missing or out-of-order menu marker: \(needle)", file: file, line: line)
                return
            }
            searchStart = range.upperBound
        }
    }
}
