import Foundation

/// URLs the app opens: pairing QR codes, and taps on notifications and the widget.
public enum DeepLink: Hashable, Sendable {
    /// `campi://pair?u=...&c=...` (api-contract §3.2)
    case pair(PairingLink)
    /// `campi://sighting/<id>`
    case sighting(id: String)
    /// `campi://today`
    case today
    /// `campi://status`: the service status sheet (service-alert pushes)
    case status

    public init?(url: URL) {
        guard url.scheme?.lowercased() == "campi" else { return nil }
        switch url.host()?.lowercased() {
        case "pair":
            guard let link = PairingLink(url: url) else { return nil }
            self = .pair(link)
        case "sighting":
            let id = url.pathComponents.dropFirst().first ?? ""
            guard !id.isEmpty, id.count <= 128 else { return nil }
            self = .sighting(id: id)
        case "today": self = .today
        case "status": self = .status
        default: return nil
        }
    }

    public var url: URL {
        switch self {
        case .pair(let link):
            var c = URLComponents()
            c.scheme = "campi"
            c.host = "pair"
            c.queryItems = [.init(name: "u", value: link.baseURL.absoluteString), .init(name: "c", value: link.code)]
            return c.url!
        case .sighting(let id):
            let seg = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(.init(charactersIn: "/"))) ?? id
            return URL(string: "campi://sighting/\(seg)")!
        case .today: return URL(string: "campi://today")!
        case .status: return URL(string: "campi://status")!
        }
    }

    /// Where tapping a push goes (api-contract §9.3): the sighting, or the status for service alerts.
    public init?(push: PushInfo) {
        if push.type == .service {
            self = .status
        } else if let id = push.sightingId {
            self = .sighting(id: id)
        } else {
            return nil
        }
    }
}
