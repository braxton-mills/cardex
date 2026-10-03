import CoreGraphics
import Foundation
import ImageIO
import Synchronization
import Testing
@testable import CampiKit

/// URLProtocol stub: records requests, answers with canned responses. Each test uses its own host, so suites can
/// run in parallel without sharing handlers.
final class StubProtocol: URLProtocol {
    typealias Handler = @Sendable (URLRequest, Data?) -> (Int, Data)
    static let handlers = Mutex<[String: Handler]>([:])
    static let log = Mutex<[String: [(URLRequest, Data?)]]>([:])

    static func session(host: String, _ handler: @escaping Handler) -> URLSession {
        handlers.withLock { $0[host] = handler }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [StubProtocol.self]
        return URLSession(configuration: cfg)
    }

    static func requests(host: String) -> [(URLRequest, Data?)] { log.withLock { $0[host] ?? [] } }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let host = request.url?.host() ?? ""
        let body = request.httpBody ?? request.httpBodyStream.map(Self.drain)
        Self.log.withLock { $0[host, default: []].append((request, body)) }
        guard let handler = Self.handlers.withLock({ $0[host] }) else {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotConnectToHost))
            return
        }
        let (status, data) = handler(request, body)
        let resp = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1",
                                   headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    static func drain(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var out = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let n = stream.read(&buf, maxLength: buf.count)
            if n <= 0 { break }
            out.append(buf, count: n)
        }
        return out
    }
}

@Suite struct APIClientTests {
    func client(_ host: String, _ handler: @escaping StubProtocol.Handler) -> APIClient {
        APIClient(baseURL: URL(string: "https://\(host)")!, token: "tok123", session: StubProtocol.session(host: host, handler))
    }

    @Test func sendsBearerAndDecodesStatus() async throws {
        let fixture = try Fixtures.data("status.json")
        let c = client("status.test") { _, _ in (200, fixture) }
        let s = try await c.status()
        #expect(s.serverName == "CAMPI-PC")
        let req = try #require(StubProtocol.requests(host: "status.test").first?.0)
        #expect(req.value(forHTTPHeaderField: "Authorization") == "Bearer tok123")
        #expect(req.url?.path() == "/api/status")
    }

    @Test func encodesSightingQuery() async throws {
        let fixture = try Fixtures.data("sightings_page.json")
        let c = client("query.test") { _, _ in (200, fixture) }
        var q = SightingQuery(from: PCDay("2026-10-01"), to: PCDay("2026-10-03"), limit: 20)
        q.makes = ["Toyota", "Mercedes-Benz"]
        q.decidedBy = [.cloud, .user]
        q.hideUnsure = true
        q.cursor = "abc+def"
        _ = try await c.sightings(q)
        let url = try #require(StubProtocol.requests(host: "query.test").first?.0.url)
        let items = try #require(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems)
        #expect(items.filter { $0.name == "make" }.map(\.value) == ["Toyota", "Mercedes-Benz"])
        #expect(items.filter { $0.name == "decided_by" }.map(\.value) == ["cloud", "user"])
        #expect(items.contains(.init(name: "hide_unsure", value: "true")))
        #expect(items.contains(.init(name: "from", value: "2026-10-01")))
        #expect(url.query()?.contains("abc%2Bdef") == true, "plus must be escaped")
    }

    @Test func labelNullIsSentExplicitly() async throws {
        let fixture = try Fixtures.data("sighting_corrected.json")
        let c = client("label.test") { _, _ in (200, fixture) }
        _ = try await c.setLabel(nil, sightingID: "abc")
        _ = try await c.setLabel("Toyota GR86", sightingID: "abc")
        let reqs = StubProtocol.requests(host: "label.test")
        #expect(reqs.map { $0.0.httpMethod } == ["POST", "POST"])
        #expect(reqs[0].0.url?.path() == "/api/sightings/abc/label")
        #expect(String(decoding: reqs[0].1 ?? Data(), as: UTF8.self) == #"{"label":null}"#)
        #expect(String(decoding: reqs[1].1 ?? Data(), as: UTF8.self) == #"{"label":"Toyota GR86"}"#)
    }

    @Test func pushBodyUsesSnakeCaseAndNull() async throws {
        let fixture = try Fixtures.data("device.json")
        let c = client("push.test") { _, _ in (200, fixture) }
        _ = try await c.updatePush(apnsToken: nil, environment: .sandbox, prefs: PushPrefs())
        let body = try #require(StubProtocol.requests(host: "push.test").first?.1)
        let obj = try JSONSerialization.jsonObject(with: body) as! [String: Any]
        #expect(obj["apns_token"] is NSNull)
        #expect(obj["environment"] as? String == "sandbox")
        #expect((obj["prefs"] as? [String: Bool])?["service_alerts"] == true)
    }

    @Test func errorEnvelopeMapsToTypedError() async throws {
        let c = client("err.test") { _, _ in
            (401, Data(#"{"error":{"code":"unauthorized","message":"unknown or revoked token"}}"#.utf8))
        }
        let error = await #expect(throws: APIError.self) { try await c.status() }
        #expect(error?.code == .unauthorized)
        #expect(error?.needsPairing == true)
    }

    @Test func nonEnvelopeErrorAndUnreachable() async throws {
        let c = client("proxy.test") { _, _ in (502, Data("<html>bad gateway</html>".utf8)) }
        let e1 = await #expect(throws: APIError.self) { try await c.status() }
        #expect(e1 == .http(status: 502))
        let none = APIClient(baseURL: URL(string: "https://nobody.test")!, token: "t",
                             session: StubProtocol.session(host: "other.test") { _, _ in (200, Data()) })
        let e2 = await #expect(throws: APIError.self) { try await none.status() }
        #expect(e2?.isUnreachable == true)
    }

    @Test func pairPostsCodeWithoutToken() async throws {
        let fixture = try Fixtures.data("pair_result.json")
        let session = StubProtocol.session(host: "pair.test") { _, _ in (201, fixture) }
        let r = try await APIClient.pair(baseURL: URL(string: "https://pair.test")!, code: "K3J9Q2M8",
                                         deviceName: "Test iPhone", session: session)
        #expect(r.token.count == 43)
        let (req, body) = try #require(StubProtocol.requests(host: "pair.test").first)
        #expect(req.value(forHTTPHeaderField: "Authorization") == nil)
        let obj = try JSONSerialization.jsonObject(with: body ?? Data()) as! [String: String]
        #expect(obj == ["code": "K3J9Q2M8", "device_name": "Test iPhone", "platform": "ios"])
    }
}

@Suite struct ConnectionStoreTests {
    @Test func roundTripAndClear() throws {
        let suite = "campi.tests.\(UUID().uuidString)"
        let store = ConnectionStore(appGroup: suite, tokens: MemoryTokenStore())
        #expect(store.load() == nil)
        let conn = Connection(baseURL: URL(string: "https://pc.ts.net")!, serverName: "PC", deviceID: "dev_x",
                              deviceName: "iPhone")
        try store.save(conn, token: "tok")
        let loaded = try #require(store.load())
        #expect(loaded.0 == conn && loaded.token == "tok")
        store.clear()
        #expect(store.load() == nil)
        UserDefaults().removePersistentDomain(forName: suite)
    }
}

@Suite struct ImageLoaderTests {
    static func makeJPEG(width: Int, height: Int) -> Data? {
        guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.setFillColor(red: 0.2, green: 0.5, blue: 0.8, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        guard let img = ctx.makeImage() else { return nil }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, "public.jpeg" as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, img, nil)
        return CGImageDestinationFinalize(dest) ? out as Data : nil
    }

    @Test func cachesBySignatureFreeKey() async throws {
        let jpeg = try #require(Self.makeJPEG(width: 480, height: 320))
        let counter = Mutex(0)
        let c = APIClient(baseURL: URL(string: "https://img.test")!, token: "t",
                          session: StubProtocol.session(host: "img.test") { _, _ in
                              counter.withLock { $0 += 1 }
                              return (200, jpeg)
                          })
        let dir = FileManager.default.temporaryDirectory.appending(path: "campi-img-\(UUID().uuidString)")
        let loader = ImageLoader(diskDirectory: dir)
        let a = try await loader.image(MediaPath("/media/sightings/x_crop.jpg?d=a&exp=1&sig=A"), client: c, maxPixelSize: 100)
        _ = try await loader.image(MediaPath("/media/sightings/x_crop.jpg?d=b&exp=2&sig=B"), client: c, maxPixelSize: 100)
        #expect(max(a.width, a.height) <= 100)
        #expect(counter.withLock { $0 } == 1)
        // a new loader (cold memory) still hits the disk cache
        _ = try await ImageLoader(diskDirectory: dir).image(MediaPath("/media/sightings/x_crop.jpg?sig=C"), client: c,
                                                            maxPixelSize: 200)
        #expect(counter.withLock { $0 } == 1)
        try? FileManager.default.removeItem(at: dir)
    }
}
