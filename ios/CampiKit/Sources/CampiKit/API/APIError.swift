import Foundation

/// Error codes from the error envelope (api-contract §2.4).
public enum APIErrorCode: String, TolerantEnum {
    case invalidParam = "invalid_param", unauthorized, invalidCode = "invalid_code", expired, notFound = "not_found",
         invalidRange = "invalid_range", unknownLabel = "unknown_label", rateLimited = "rate_limited",
         piUnreachable = "pi_unreachable", liveBusy = "live_busy", `internal`, unknown
}

public enum APIError: Error, Hashable, Sendable, LocalizedError {
    /// The server answered with the error envelope.
    case server(status: Int, code: APIErrorCode, message: String)
    /// A non-2xx answer without a readable envelope (e.g. a proxy error page).
    case http(status: Int)
    /// The PC couldn't be reached (offline, Tailscale off, PC asleep, timeout).
    case unreachable(String)
    case decoding(String)
    case invalidURL(String)

    public var code: APIErrorCode? {
        if case .server(_, let code, _) = self { return code }
        return nil
    }

    /// The token was revoked or is unknown: the phone has to pair again.
    public var needsPairing: Bool {
        switch self {
        case .server(401, .unauthorized, _), .http(401): true
        default: false
        }
    }

    public var isUnreachable: Bool {
        if case .unreachable = self { return true }
        return false
    }

    public var errorDescription: String? {
        switch self {
        case .server(_, _, let message): message
        case .http(let status): "The PC answered HTTP \(status)."
        case .unreachable(let why): "Can't reach the PC: \(why)"
        case .decoding(let why): "Unexpected answer from the PC: \(why)"
        case .invalidURL(let s): "Invalid URL: \(s)"
        }
    }

    public static func from(transport error: any Error) -> APIError {
        if let e = error as? APIError { return e }
        if let u = error as? URLError { return .unreachable(u.localizedDescription) }
        return .unreachable(error.localizedDescription)
    }
}

struct ErrorEnvelope: Decodable {
    struct Body: Decodable {
        var code: APIErrorCode
        var message: String
    }
    var error: Body
}
