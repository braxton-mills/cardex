import Foundation

/// How Live shows the camera (api-contract §8).
public enum LiveMode: String, Hashable, Sendable, CaseIterable {
    /// `/live.mjpg?max_fps=N`: smooth video, for Wi-Fi.
    case video
    /// `/live.jpg?w=1080` every 2 s: the newest saved frame, for cellular and Low Data Mode.
    case snapshots

    /// Video only on a network that is neither expensive (cellular, hotspot) nor constrained (Low Data Mode),
    /// unless the user picked a mode.
    public static func choose(isExpensive: Bool, isConstrained: Bool, override: LiveMode? = nil) -> LiveMode {
        if let override { return override }
        return isExpensive || isConstrained ? .snapshots : .video
    }
}

/// One `/live.jpg` answer.
public struct LiveSnapshot: Sendable {
    public var data: Data
    /// `X-Frame-Time`: when capture saved this frame.
    public var frameTime: PCDate?
}

extension APIClient {
    /// `GET /live.jpg?w=N` (api-contract §8.2). Already rotated by the PC.
    public func liveSnapshot(_ path: MediaPath, width: Int = 1080) async throws -> LiveSnapshot {
        guard let url = url(for: path.adding("w", String(width))) else { throw APIError.invalidURL(path.rawValue) }
        var req = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10)
        req.setValue(authorizationHeader.value, forHTTPHeaderField: authorizationHeader.name)
        let (data, http) = try await self.data(for: req)
        let time = (http.value(forHTTPHeaderField: "X-Frame-Time")).flatMap(PCDate.init(rfc3339:))
        return LiveSnapshot(data: data, frameTime: time)
    }

    /// `GET /live.mjpg?max_fps=N` (api-contract §8.1) as a stream of JPEG frames. Only the newest frame is buffered,
    /// so a slow consumer skips frames. Ending the iteration (or cancelling its task) closes the connection, which
    /// closes the PC's upstream to the Pi. Errors before the first frame are `APIError`s (`live_busy`,
    /// `pi_unreachable`, `unauthorized`, ...).
    public func liveFrames(_ path: MediaPath, maxFPS: Int) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            guard let url = url(for: path.adding("max_fps", String(maxFPS))) else {
                continuation.finish(throwing: APIError.invalidURL(path.rawValue))
                return
            }
            var req = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
            req.setValue(authorizationHeader.value, forHTTPHeaderField: authorizationHeader.name)
            let reader = MJPEGReader(continuation: continuation)
            let cfg = URLSessionConfiguration.ephemeral
            cfg.timeoutIntervalForRequest = 15      // max silence between bytes
            cfg.timeoutIntervalForResource = 24 * 3600
            let session = URLSession(configuration: cfg, delegate: reader, delegateQueue: nil)
            let task = session.dataTask(with: req)
            continuation.onTermination = { _ in
                task.cancel()
                session.invalidateAndCancel()
            }
            task.resume()
        }
    }
}

/// URLSession delegate behind `liveFrames`. Callbacks arrive serially on the session's queue.
private final class MJPEGReader: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    let continuation: AsyncThrowingStream<Data, any Error>.Continuation
    private var assembler = MJPEGFrameAssembler()
    private var errorStatus: Int?
    private var errorBody = Data()

    init(continuation: AsyncThrowingStream<Data, any Error>.Continuation) {
        self.continuation = continuation
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void) {
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            errorStatus = http.statusCode
        } else if errorStatus == nil {
            let ctype = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Type")
            for frame in assembler.response(mimeType: response.mimeType, contentType: ctype,
                                            expectedLength: response.expectedContentLength) {
                continuation.yield(frame)
            }
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if errorStatus != nil {
            errorBody.append(data)
            return
        }
        for frame in assembler.data(data) { continuation.yield(frame) }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        defer { session.finishTasksAndInvalidate() }
        if let status = errorStatus {
            if let env = try? JSONDecoder.campi.decode(ErrorEnvelope.self, from: errorBody) {
                continuation.finish(throwing: APIError.server(status: status, code: env.error.code,
                                                              message: env.error.message))
            } else {
                continuation.finish(throwing: APIError.http(status: status))
            }
            return
        }
        for frame in assembler.finish() { continuation.yield(frame) }
        if let error, (error as? URLError)?.code != .cancelled {
            continuation.finish(throwing: APIError.from(transport: error))
        } else {
            continuation.finish()
        }
    }
}
