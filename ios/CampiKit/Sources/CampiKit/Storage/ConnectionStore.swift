import Foundation
import Security

/// Which PC this phone is paired with. The token lives in the Keychain; the rest in App Group defaults,
/// so the widget and notification extension can read both.
public struct Connection: Codable, Hashable, Sendable {
    public var baseURL: URL
    public var serverName: String
    public var deviceID: String
    public var deviceName: String

    public init(baseURL: URL, serverName: String, deviceID: String, deviceName: String) {
        self.baseURL = baseURL
        self.serverName = serverName
        self.deviceID = deviceID
        self.deviceName = deviceName
    }
}

public protocol TokenStore: Sendable {
    func readToken() throws -> String?
    func writeToken(_ token: String) throws
    func deleteToken() throws
}

/// Keychain storage, readable after first unlock so the widget and notification extension work while locked.
public struct KeychainTokenStore: TokenStore {
    public let service: String
    public let accessGroup: String?

    public init(service: String = "com.braxtonmills.campi.token", accessGroup: String?) {
        self.service = service
        self.accessGroup = accessGroup
    }

    private func query() -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: "pc",
                                kSecUseDataProtectionKeychain as String: true]
        if let accessGroup { q[kSecAttrAccessGroup as String] = accessGroup }
        return q
    }

    public func readToken() throws -> String? {
        var q = query()
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = out as? Data else { throw KeychainError(status: status) }
        return String(data: data, encoding: .utf8)
    }

    public func writeToken(_ token: String) throws {
        let data = Data(token.utf8)
        let attrs: [String: Any] = [kSecValueData as String: data,
                                    kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock]
        var status = SecItemUpdate(query() as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query().merging(attrs) { $1 } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }

    public func deleteToken() throws {
        let status = SecItemDelete(query() as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status: status) }
    }
}

public struct KeychainError: Error, CustomStringConvertible {
    public let status: OSStatus
    public var description: String {
        "Keychain error \(status): \(SecCopyErrorMessageString(status, nil) as String? ?? "unknown")"
    }
}

/// In-memory store for tests and previews.
public final class MemoryTokenStore: TokenStore, @unchecked Sendable {
    private let lock = NSLock()
    private var token: String?

    public init(token: String? = nil) { self.token = token }

    public func readToken() throws -> String? { lock.withLock { token } }
    public func writeToken(_ token: String) throws { lock.withLock { self.token = token } }
    public func deleteToken() throws { lock.withLock { token = nil } }
}

/// Persists the pairing: connection details in (App Group) defaults, token in a `TokenStore`.
public struct ConnectionStore: Sendable {
    let defaultsSuite: String?
    let tokens: any TokenStore
    static let key = "campi.connection"

    public init(appGroup: String?, tokens: any TokenStore) {
        self.defaultsSuite = appGroup
        self.tokens = tokens
    }

    private var defaults: UserDefaults { defaultsSuite.flatMap(UserDefaults.init(suiteName:)) ?? .standard }

    /// The saved pairing, or `nil` if not paired (or the token is missing).
    public func load() -> (Connection, token: String)? {
        guard let data = defaults.data(forKey: Self.key),
              let conn = try? JSONDecoder().decode(Connection.self, from: data),
              let token = try? tokens.readToken() else { return nil }
        return (conn, token)
    }

    public func save(_ connection: Connection, token: String) throws {
        try tokens.writeToken(token)
        defaults.set(try JSONEncoder().encode(connection), forKey: Self.key)
    }

    public func clear() {
        try? tokens.deleteToken()
        defaults.removeObject(forKey: Self.key)
    }
}
