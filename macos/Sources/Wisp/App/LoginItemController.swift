import Foundation
import ServiceManagement

enum LoginItemStatus: Equatable {
    case enabled
    case notRegistered
    case requiresApproval
    case unavailable
    case unknown

    var displayText: String {
        switch self {
        case .enabled:
            return "on"
        case .notRegistered:
            return "off"
        case .requiresApproval:
            return "approval needed"
        case .unavailable:
            return "unavailable"
        case .unknown:
            return "unknown"
        }
    }

    var menuTitle: String {
        "Launch at Login: \(displayText)"
    }

    var isChecked: Bool {
        self == .enabled
    }

    var shouldRegisterOnToggle: Bool {
        switch self {
        case .notRegistered, .requiresApproval:
            return true
        case .enabled, .unavailable, .unknown:
            return false
        }
    }

    var isActionable: Bool {
        switch self {
        case .enabled, .notRegistered, .requiresApproval:
            return true
        case .unavailable, .unknown:
            return false
        }
    }
}

@MainActor
enum LoginItemController {

    static var status: LoginItemStatus {
        LoginItemStatus(serviceStatus: SMAppService.mainApp.status)
    }

    @discardableResult
    static func toggle() throws -> LoginItemStatus {
        guard status.isActionable else { return status }
        if status.shouldRegisterOnToggle {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
        return status
    }
}

private extension LoginItemStatus {
    init(serviceStatus: SMAppService.Status) {
        switch serviceStatus {
        case .enabled:
            self = .enabled
        case .notRegistered:
            self = .notRegistered
        case .requiresApproval:
            self = .requiresApproval
        case .notFound:
            self = .unavailable
        @unknown default:
            self = .unknown
        }
    }
}
