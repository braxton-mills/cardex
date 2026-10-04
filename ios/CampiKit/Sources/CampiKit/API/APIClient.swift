import Foundation

/// Client for the Campi PC API (api/api-contract.md). Stateless and Sendable: a new token means a new client.
public struct APIClient: Sendable {
    public let baseURL: URL
    let token: String
    let session: URLSession

    public init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    // MARK: Status and devices

    public func status() async throws -> Status { try await get("/api/status") }

    public func me() async throws -> Device { try await get("/api/devices/me") }

    public func updatePush(apnsToken: String?, environment: Device.Environment, prefs: PushPrefs) async throws -> Device {
        struct Body: Encodable, Sendable {
            var apnsToken: String?
            var environment: String
            var prefs: PushPrefs

            // apns_token must be sent as null, not omitted
            func encode(to encoder: any Encoder) throws {
                var c = encoder.container(keyedBy: CodingKeys.self)
                try c.encode(apnsToken, forKey: .apnsToken)
                try c.encode(environment, forKey: .environment)
                try c.encode(prefs, forKey: .prefs)
            }
            enum CodingKeys: String, CodingKey { case apnsToken, environment, prefs }
        }
        return try await send("PUT", "/api/devices/me/push",
                              body: Body(apnsToken: apnsToken, environment: environment.rawValue, prefs: prefs))
    }

    /// "Unpair this phone": revokes this device's token on the PC.
    public func unpair() async throws {
        let req = try request("DELETE", "/api/devices/me")
        _ = try await data(for: req)
    }

    // MARK: Sightings

    public func sightings(_ query: SightingQuery = .init()) async throws -> Page<Sighting> {
        try await get("/api/sightings", query.queryItems)
    }

    public func sighting(id: String) async throws -> Sighting {
        try await get("/api/sightings/\(Self.segment(id))")
    }

    public func setStarred(_ starred: Bool, sightingID id: String) async throws -> Sighting {
        try await send("POST", "/api/sightings/\(Self.segment(id))/star", body: ["starred": starred])
    }

    public func setHidden(_ hidden: Bool, sightingID id: String) async throws -> Sighting {
        try await send("POST", "/api/sightings/\(Self.segment(id))/hide", body: ["hidden": hidden])
    }

    /// Sets the label correction; `nil` removes it. Throws `unknown_label` for a label not in the collection.
    public func setLabel(_ label: String?, sightingID id: String) async throws -> Sighting {
        try await send("POST", "/api/sightings/\(Self.segment(id))/label", body: LabelBody(label: label))
    }

    public func collection() async throws -> LabelCollection { try await get("/api/collection") }

    public func highlights(_ query: HighlightQuery = .init()) async throws -> Page<Highlight> {
        try await get("/api/highlights", query.queryItems)
    }

    // MARK: Timelapse

    public func clips() async throws -> ClipList { try await get("/api/clips") }

    public func clip(id: String) async throws -> Clip { try await get("/api/clips/\(Self.segment(id))") }

    public func setStarred(_ starred: Bool, clipID id: String) async throws -> Clip {
        try await send("POST", "/api/clips/\(Self.segment(id))/star", body: ["starred": starred])
    }

    public func dailies(limit: Int? = nil, cursor: String? = nil) async throws -> Page<Daily> {
        var q: [URLQueryItem] = []
        if let limit { q.append(.init(name: "limit", value: String(limit))) }
        if let cursor { q.append(.init(name: "cursor", value: cursor)) }
        return try await get("/api/daily", q)
    }

    public func daily(day: PCDay) async throws -> Daily { try await get("/api/daily/\(day.rawValue)") }

    public func setStarred(_ starred: Bool, day: PCDay) async throws -> Daily {
        try await send("POST", "/api/daily/\(day.rawValue)/star", body: ["starred": starred])
    }

    public func archive() async throws -> ArchiveList { try await get("/api/archive") }

    public func seek(to instant: Date) async throws -> Seek {
        try await get("/api/seek", [.init(name: "ts", value: String(format: "%.3f", instant.timeIntervalSince1970))])
    }

    // MARK: Media

    /// Absolute URL for an API media path (signed, so it also works without the bearer header).
    public func url(for path: MediaPath) -> URL? { path.url(relativeTo: baseURL) }

    /// Header to add when fetching media with URLSession (keeps working after a signature expires).
    public var authorizationHeader: (name: String, value: String) { ("Authorization", "Bearer \(token)") }

    /// GET raw bytes of an API media path.
    public func mediaData(_ path: MediaPath) async throws -> Data {
        guard let url = url(for: path) else { throw APIError.invalidURL(path.rawValue) }
        var req = URLRequest(url: url)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await data(for: req).0
    }

    // MARK: Pairing (no token yet)

    /// `POST /api/pair` with a one-time code from `campi pair` (api-contract §3.2).
    public static func pair(baseURL: URL, code: String, deviceName: String,
                            session: URLSession = .shared) async throws -> PairResult {
        let client = APIClient(baseURL: baseURL, token: "", session: session)
        var req = try client.request("POST", "/api/pair", authorized: false)
        req.httpBody = try JSON.encoder().encode(PairBody(code: code, deviceName: deviceName, platform: "ios"))
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, _) = try await client.data(for: req)
        return try client.decode(PairResult.self, data)
    }

    // MARK: Plumbing

    func get<T: Decodable>(_ path: String, _ query: [URLQueryItem] = []) async throws -> T {
        let req = try request("GET", path, query: query)
        return try decode(T.self, try await data(for: req).0)
    }

    func send<T: Decodable, B: Encodable>(_ method: String, _ path: String, body: B) async throws -> T {
        var req = try request(method, path)
        req.httpBody = try JSON.encoder().encode(body)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try decode(T.self, try await data(for: req).0)
    }

    func request(_ method: String, _ path: String, query: [URLQueryItem] = [], authorized: Bool = true) throws -> URLRequest {
        guard var c = URLComponents(url: baseURL, resolvingAgainstBaseURL: true) else {
            throw APIError.invalidURL(baseURL.absoluteString)
        }
        c.path = (c.path.hasSuffix("/") ? String(c.path.dropLast()) : c.path) + path
        if !query.isEmpty {
            c.queryItems = query
            // URLComponents leaves "+" alone, which servers decode as a space (e.g. in RFC 3339 offsets).
            c.percentEncodedQuery = c.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        }
        guard let url = c.url else { throw APIError.invalidURL(path) }
        var req = URLRequest(url: url, timeoutInterval: 20)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if authorized { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return req
    }

    func data(for req: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let data: Data, response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.from(transport: error)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.decoding("not an HTTP response") }
        guard (200..<300).contains(http.statusCode) else {
            if let env = try? JSONDecoder.campi.decode(ErrorEnvelope.self, from: data) {
                throw APIError.server(status: http.statusCode, code: env.error.code, message: env.error.message)
            }
            throw APIError.http(status: http.statusCode)
        }
        return (data, http)
    }

    func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try JSONDecoder.campi.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error))
        }
    }

    static func segment(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? s
    }
}

private struct LabelBody: Encodable, Sendable {
    var label: String?

    // "label": null must be sent explicitly to clear a correction
    func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(label, forKey: .label)
    }
    enum CodingKeys: String, CodingKey { case label }
}

private struct PairBody: Encodable, Sendable {
    var code: String
    var deviceName: String
    var platform: String
}
