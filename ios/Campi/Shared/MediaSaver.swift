import CampiKit
import Foundation
import Observation
import Photos
import SwiftUI
import UIKit

/// Downloads API media to a temp file and saves it to Photos or hands it to the share sheet.
/// One at a time; progress is shown by `SaveStatusBanner` at the root.
@MainActor @Observable
final class MediaSaver {
    enum Phase: Equatable {
        case idle
        case downloading(String, progress: Double)
        case done(String)
        case failed(String)
    }

    struct ShareItem: Identifiable {
        let id = UUID()
        let url: URL
    }

    private(set) var phase: Phase = .idle
    var shareItem: ShareItem?
    private var dismissTask: Task<Void, Never>?

    var isBusy: Bool { if case .downloading = phase { true } else { false } }

    func saveVideo(_ path: MediaPath, fileName: String, app: AppModel) {
        run(app: app, label: "Saving \(fileName)") { url in
            try await Self.addToPhotos { PHAssetCreationRequest.creationRequestForAssetFromVideo(atFileURL: url) }
            return "Saved to Photos"
        } download: { try await self.download(path, fileName: fileName, app: app) }
    }

    func saveImage(_ path: MediaPath, fileName: String, app: AppModel) {
        run(app: app, label: "Saving image") { url in
            try await Self.addToPhotos { PHAssetCreationRequest.creationRequestForAssetFromImage(atFileURL: url) }
            return "Saved to Photos"
        } download: { try await self.download(path, fileName: fileName, app: app) }
    }

    func share(_ path: MediaPath, fileName: String, app: AppModel) {
        run(app: app, label: "Preparing \(fileName)") { url in
            self.shareItem = ShareItem(url: url)
            return nil
        } download: { try await self.download(path, fileName: fileName, app: app) }
    }

    // MARK: plumbing

    private func run(app: AppModel, label: String, then finish: @escaping @MainActor (URL) async throws -> String?,
                     download: @escaping @MainActor () async throws -> URL) {
        guard !isBusy else { return }
        dismissTask?.cancel()
        phase = .downloading(label, progress: 0)
        // keep going for a while if the user leaves the app mid-download
        let bg = UIApplication.shared.beginBackgroundTask(withName: "campi.save")
        Task {
            defer { UIApplication.shared.endBackgroundTask(bg) }
            do {
                let url = try await download()
                if let message = try await finish(url) {
                    show(.done(message))
                } else {
                    phase = .idle
                }
            } catch {
                app.report(error)
                show(.failed((error as? LocalizedError)?.errorDescription ?? error.localizedDescription))
            }
        }
    }

    private func show(_ p: Phase) {
        phase = p
        dismissTask = Task {
            try? await Task.sleep(for: .seconds(3))
            if !Task.isCancelled { phase = .idle }
        }
    }

    /// Downloads to tmp/CampiShare/<fileName> (bearer auth, so an expired signature doesn't matter).
    private func download(_ path: MediaPath, fileName: String, app: AppModel) async throws -> URL {
        guard let client = app.client, let url = client.url(for: path) else { throw APIError.invalidURL(path.rawValue) }
        var req = URLRequest(url: url, timeoutInterval: 60)
        let auth = client.authorizationHeader
        req.setValue(auth.value, forHTTPHeaderField: auth.name)

        let tracker = DownloadTracker()
        let poll = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                if let f = tracker.fraction, case .downloading(let label, _) = self?.phase {
                    self?.phase = .downloading(label, progress: f)
                }
                try? await Task.sleep(for: .milliseconds(200))
            }
        }
        defer { poll.cancel() }

        let (tmp, response): (URL, URLResponse)
        do {
            (tmp, response) = try await URLSession.shared.download(for: req, delegate: tracker)
        } catch {
            throw APIError.from(transport: error)
        }
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            throw status == 404 ? SaveError.gone : APIError.http(status: status)
        }
        let dir = FileManager.default.temporaryDirectory.appending(path: "CampiShare", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let dest = dir.appending(path: fileName)
        try? FileManager.default.removeItem(at: dest)
        try FileManager.default.moveItem(at: tmp, to: dest)
        return dest
    }

    private static func addToPhotos(_ change: @escaping @Sendable () -> Void) async throws {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else { throw SaveError.photosDenied }
        try await PHPhotoLibrary.shared().performChanges(change)
    }

    enum SaveError: LocalizedError {
        case photosDenied, gone
        var errorDescription: String? {
            switch self {
            case .photosDenied: "Campi isn't allowed to add to your photo library. Turn it on in Settings › Privacy › Photos."
            case .gone: "That file is gone from the PC (it may have expired)."
            }
        }
    }
}

/// Exposes a download task's progress (URLSession creates the task; we only get it through the delegate).
private final class DownloadTracker: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var task: URLSessionTask?

    var fraction: Double? {
        lock.withLock {
            guard let t = task, t.countOfBytesExpectedToReceive > 0 else { return nil }
            return Double(t.countOfBytesReceived) / Double(t.countOfBytesExpectedToReceive)
        }
    }

    func urlSession(_ session: URLSession, didCreateTask task: URLSessionTask) {
        lock.withLock { self.task = task }
    }
}

/// Bottom banner for download / saved / failed.
struct SaveStatusBanner: View {
    let saver: MediaSaver

    var body: some View {
        Group {
            switch saver.phase {
            case .idle:
                EmptyView()
            case .downloading(let label, let progress):
                banner {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(label).font(.subheadline.weight(.medium)).lineLimit(1)
                        ProgressView(value: progress)
                    }
                }
            case .done(let text):
                banner { Label(text, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                    .accessibilityIdentifier("save.done")
            case .failed(let text):
                banner { Label(text, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange) }
            }
        }
        .animation(.snappy, value: saver.phase)
    }

    private func banner(@ViewBuilder _ content: () -> some View) -> some View {
        content()
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(.regularMaterial, in: .rect(cornerRadius: 16))
            .shadow(radius: 6, y: 2)
            .padding(.horizontal)
            .padding(.bottom, 70)   // above the tab bar
            .transition(.move(edge: .bottom).combined(with: .opacity))
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }

    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
