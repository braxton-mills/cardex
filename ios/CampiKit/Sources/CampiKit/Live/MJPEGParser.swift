import Foundation

/// Splits a raw `multipart/x-mixed-replace` body into JPEG frames (api-contract §8.1). Feed it chunks of any size.
/// Uses each part's `Content-Length` when present; otherwise a part ends at the next boundary.
public struct MJPEGParser: Sendable {
    private let delimiter: Data      // "--<boundary>"
    private var buffer = Data()
    private var expected: Int?       // body length of the part being read; nil while reading headers
    private var inBody = false

    /// Drop everything if a part grows past this (a broken stream shouldn't eat memory).
    static let maxPartBytes = 16 << 20

    public init(boundary: String) {
        delimiter = Data(("--" + boundary).utf8)
    }

    /// The `boundary` parameter of a `multipart/x-mixed-replace; boundary=...` Content-Type.
    public static func boundary(fromContentType contentType: String) -> String? {
        for param in contentType.split(separator: ";").dropFirst() {
            let kv = param.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            guard kv.count == 2, kv[0].lowercased() == "boundary" else { continue }
            let value = kv[1].trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            return value.isEmpty ? nil : value
        }
        return nil
    }

    /// Appends a chunk and returns the frames it completed, oldest first.
    public mutating func append(_ chunk: Data) -> [Data] {
        buffer.append(chunk)
        var frames: [Data] = []
        while let frame = nextFrame() { frames.append(frame) }
        if buffer.count > Self.maxPartBytes {
            buffer.removeAll()
            inBody = false
            expected = nil
        }
        return frames
    }

    private mutating func nextFrame() -> Data? {
        while true {
            if !inBody {
                // Skip to the boundary, then read the part headers.
                guard let b = buffer.range(of: delimiter) else {
                    // keep a tail that could be the start of a split delimiter
                    if buffer.count > delimiter.count { buffer = Data(buffer.suffix(delimiter.count)) }
                    return nil
                }
                guard let end = buffer.range(of: Data("\r\n\r\n".utf8), in: b.upperBound..<buffer.endIndex) else {
                    return nil
                }
                let headers = String(decoding: buffer[b.upperBound..<end.lowerBound], as: UTF8.self)
                expected = Self.contentLength(headers)
                buffer = Data(buffer[end.upperBound...])
                inBody = true
            }
            if let n = expected {
                guard buffer.count >= n else { return nil }
                let frame = Data(buffer.prefix(n))
                buffer = Data(buffer.dropFirst(n))
                inBody = false
                return frame
            }
            // No Content-Length: the part ends at the next boundary.
            guard let next = buffer.range(of: delimiter) else { return nil }
            var frame = Data(buffer[..<next.lowerBound])
            if frame.suffix(2) == Data("\r\n".utf8) { frame.removeLast(2) }
            buffer = Data(buffer[next.lowerBound...])
            inBody = false
            if !frame.isEmpty { return frame }
        }
    }

    static func contentLength(_ headers: String) -> Int? {
        for line in headers.split(whereSeparator: \.isNewline) {
            let kv = line.split(separator: ":", maxSplits: 1)
            if kv.count == 2, kv[0].trimmingCharacters(in: .whitespaces).lowercased() == "content-length" {
                return Int(kv[1].trimmingCharacters(in: .whitespaces))
            }
        }
        return nil
    }
}

/// Turns URLSession's callbacks for an MJPEG response into frames.
///
/// URLSession normally splits `multipart/x-mixed-replace` itself: every part arrives as its own response
/// (`image/jpeg`, `expectedContentLength` = the part's Content-Length) followed by that part's bytes. If data
/// arrives without per-part responses (a proxy that hides the multipart type), the raw body goes through
/// `MJPEGParser` instead.
public struct MJPEGFrameAssembler: Sendable {
    private var boundary: String?
    private var parser: MJPEGParser?
    private var part = Data()
    private var partExpected: Int64 = -1
    private var sawPart = false

    public init() {}

    /// A response arrived: the top-level one, or the start of a part. Returns a frame the previous part completed.
    public mutating func response(mimeType: String?, contentType: String?, expectedLength: Int64) -> [Data] {
        if mimeType?.lowercased().hasPrefix("multipart/") == true {
            boundary = contentType.flatMap(MJPEGParser.boundary(fromContentType:))
            return []
        }
        let done = flushPart()
        sawPart = true
        partExpected = expectedLength
        return done
    }

    public mutating func data(_ chunk: Data) -> [Data] {
        if !sawPart {
            if parser == nil { parser = MJPEGParser(boundary: boundary ?? "campiframe") }
            return parser!.append(chunk)
        }
        part.append(chunk)
        if partExpected > 0, Int64(part.count) >= partExpected { return flushPart() }
        if part.count > MJPEGParser.maxPartBytes { part.removeAll() }
        return []
    }

    /// The stream ended: returns a last part that had no Content-Length.
    public mutating func finish() -> [Data] { flushPart() }

    private mutating func flushPart() -> [Data] {
        defer {
            part = Data()
            partExpected = -1
        }
        return part.isEmpty ? [] : [part]
    }
}
