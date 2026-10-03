import Foundation
import Testing
@testable import CampiKit

@Suite struct LiveTests {
    /// Fake JPEGs: SOI, varied bytes (including CR/LF and dashes), EOI.
    static let frames: [Data] = (0..<3).map { i in
        var d = Data([0xFF, 0xD8])
        for j in 0..<(500 + i * 137) { d.append(UInt8((j * 31 + i) % 256)) }
        d.append(contentsOf: Array("\r\n--campi".utf8))   // looks like a boundary, isn't one
        d.append(contentsOf: [0xFF, 0xD9])
        return d
    }

    static func body(contentLength: Bool) -> Data {
        var out = Data()
        for f in frames {
            out.append(Data("--campiframe\r\nContent-Type: image/jpeg\r\n".utf8))
            if contentLength { out.append(Data("Content-Length: \(f.count)\r\n".utf8)) }
            out.append(Data("\r\n".utf8))
            out.append(f)
            out.append(Data("\r\n".utf8))
        }
        out.append(Data("--campiframe\r\n".utf8))   // the next part's start; lets the last length-less part end
        return out
    }

    static func parse(_ body: Data, chunk: Int) -> [Data] {
        var p = MJPEGParser(boundary: "campiframe")
        var out: [Data] = []
        var i = body.startIndex
        while i < body.endIndex {
            let j = min(i + chunk, body.endIndex)
            out += p.append(Data(body[i..<j]))
            i = j
        }
        return out
    }

    @Test(arguments: [1, 2, 7, 64, 1000, 100_000])
    func parsesWithContentLength(chunk: Int) {
        #expect(Self.parse(Self.body(contentLength: true), chunk: chunk) == Self.frames)
    }

    @Test(arguments: [1, 13, 4096])
    func parsesWithoutContentLength(chunk: Int) {
        #expect(Self.parse(Self.body(contentLength: false), chunk: chunk) == Self.frames)
    }

    @Test func skipsPreambleAndReadsBoundary() {
        #expect(MJPEGParser.boundary(fromContentType: "multipart/x-mixed-replace; boundary=campiframe") == "campiframe")
        #expect(MJPEGParser.boundary(fromContentType: "multipart/x-mixed-replace;boundary=\"abc\"") == "abc")
        #expect(MJPEGParser.boundary(fromContentType: "image/jpeg") == nil)
        var body = Data("garbage before the first part\r\n".utf8)
        body.append(Self.body(contentLength: true))
        #expect(Self.parse(body, chunk: 50) == Self.frames)
    }

    @Test func assemblerUsesURLSessionParts() {
        var a = MJPEGFrameAssembler()
        #expect(a.response(mimeType: "multipart/x-mixed-replace",
                           contentType: "multipart/x-mixed-replace; boundary=campiframe", expectedLength: -1).isEmpty)
        var out: [Data] = []
        for f in Self.frames {
            out += a.response(mimeType: "image/jpeg", contentType: "image/jpeg", expectedLength: Int64(f.count))
            out += a.data(Data(f.prefix(100)))
            out += a.data(Data(f.dropFirst(100)))
        }
        out += a.finish()
        #expect(out == Self.frames)
    }

    @Test func assemblerPartsWithoutLengthEndAtNextPart() {
        var a = MJPEGFrameAssembler()
        var out: [Data] = []
        for f in Self.frames {
            out += a.response(mimeType: "image/jpeg", contentType: nil, expectedLength: -1)
            out += a.data(f)
        }
        #expect(out == Array(Self.frames.prefix(2)))
        out += a.finish()
        #expect(out == Self.frames)
    }

    @Test func assemblerFallsBackToRawParsing() {
        var a = MJPEGFrameAssembler()
        _ = a.response(mimeType: "multipart/x-mixed-replace",
                       contentType: "multipart/x-mixed-replace; boundary=campiframe", expectedLength: -1)
        #expect(a.data(Self.body(contentLength: true)) == Self.frames)
    }

    @Test func modeFollowsNetwork() {
        #expect(LiveMode.choose(isExpensive: false, isConstrained: false) == .video)
        #expect(LiveMode.choose(isExpensive: true, isConstrained: false) == .snapshots)
        #expect(LiveMode.choose(isExpensive: false, isConstrained: true) == .snapshots)
        #expect(LiveMode.choose(isExpensive: true, isConstrained: false, override: .video) == .video)
        #expect(LiveMode.choose(isExpensive: false, isConstrained: false, override: .snapshots) == .snapshots)
    }

    @Test func snapshotSendsWidthAndReadsFrameTime() async throws {
        let session = StubProtocol.session(host: "live.test") { req, _ in (200, Data([0xFF, 0xD8, 0xFF, 0xD9])) }
        let c = APIClient(baseURL: URL(string: "http://live.test")!, token: "t", session: session)
        let snap = try await c.liveSnapshot(MediaPath("/live.jpg?d=dev_x&exp=1&sig=s"), width: 1080)
        #expect(snap.data.count == 4)
        let req = try #require(StubProtocol.requests(host: "live.test").last?.0)
        #expect(req.url?.absoluteString == "http://live.test/live.jpg?d=dev_x&exp=1&sig=s&w=1080")
        #expect(req.value(forHTTPHeaderField: "Authorization") == "Bearer t")
    }
}
