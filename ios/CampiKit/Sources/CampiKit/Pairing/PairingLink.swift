import Foundation

/// `campi://pair?u=<base URL>&c=<code>` from `campi pair` (api-contract §3.2), or the same typed by hand.
public struct PairingLink: Hashable, Sendable {
    public let baseURL: URL
    /// Normalized: upper case, no dashes or spaces, 8 Crockford base32 chars.
    public let code: String

    public init?(url: URL) {
        guard url.scheme?.lowercased() == "campi", url.host()?.lowercased() == "pair",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let u = items.first(where: { $0.name == "u" })?.value,
              let c = items.first(where: { $0.name == "c" })?.value else { return nil }
        self.init(server: u, code: c)
    }

    /// From a typed server address (scheme optional, https assumed) and code.
    public init?(server: String, code: String) {
        guard let base = Self.normalizeServer(server), let code = Self.normalizeCode(code) else { return nil }
        self.baseURL = base
        self.code = code
    }

    /// `XXXX-XXXX`, for display.
    public var displayCode: String { "\(code.prefix(4))-\(code.suffix(4))" }

    /// Plain HTTP is only acceptable to a local mock; the real PC is always behind Tailscale HTTPS.
    public var isInsecure: Bool { baseURL.scheme == "http" }

    static let crockford = Set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

    public static func normalizeCode(_ raw: String) -> String? {
        let c = raw.uppercased().filter { !$0.isWhitespace && $0 != "-" }
        return c.count == 8 && c.allSatisfy({ crockford.contains($0) }) ? c : nil
    }

    public static func normalizeServer(_ raw: String) -> URL? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "https://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        guard let url = URL(string: s), let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "http",
              url.host() != nil, url.query == nil else { return nil }
        return url
    }
}
