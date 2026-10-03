import CoreGraphics
import CryptoKit
import Foundation
import ImageIO

/// Loads API images (crops, frames, posters) with a memory + disk cache keyed on the path without its signature
/// (`MediaPath.cacheKey`), so a re-signed URL for the same file is still a cache hit. Downsamples on decode.
public actor ImageLoader {
    public static let shared = ImageLoader()

    private let memory = NSCache<NSString, CGImageBox>()
    private let diskDir: URL?
    private var inflight: [String: Task<CGImage, any Error>] = [:]

    public init(diskDirectory: URL? = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
        .appending(path: "CampiImages", directoryHint: .isDirectory)) {
        memory.countLimit = 400
        diskDir = diskDirectory
        if let diskDirectory {
            try? FileManager.default.createDirectory(at: diskDirectory, withIntermediateDirectories: true)
        }
    }

    /// The image at `path`, at most `maxPixelSize` on its long side.
    public func image(_ path: MediaPath, client: APIClient, maxPixelSize: Int = 800) async throws -> CGImage {
        let key = "\(path.cacheKey)#\(maxPixelSize)"
        if let hit = memory.object(forKey: key as NSString) { return hit.image }
        if let task = inflight[key] { return try await task.value }
        let task = Task { [diskDir] in
            let file = diskDir?.appending(path: Self.hash(path.cacheKey))
            let data: Data
            if let file, let cached = try? Data(contentsOf: file) {
                data = cached
            } else {
                data = try await client.mediaData(path)
                if let file { try? data.write(to: file, options: .atomic) }
            }
            guard let img = Self.downsample(data, maxPixelSize: maxPixelSize) else {
                throw APIError.decoding("not an image: \(path.cacheKey)")
            }
            return img
        }
        inflight[key] = task
        defer { inflight[key] = nil }
        let img = try await task.value
        memory.setObject(CGImageBox(img), forKey: key as NSString)
        return img
    }

    /// Drops everything cached on disk and in memory (Settings → clear cache, or after unpairing).
    public func clear() {
        memory.removeAllObjects()
        if let diskDir {
            try? FileManager.default.removeItem(at: diskDir)
            try? FileManager.default.createDirectory(at: diskDir, withIntermediateDirectories: true)
        }
    }

    static func hash(_ key: String) -> String {
        SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    public static func downsample(_ data: Data, maxPixelSize: Int) -> CGImage? {
        guard let src = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary)
        else { return nil }
        let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                     kCGImageSourceCreateThumbnailWithTransform: true,
                                     kCGImageSourceShouldCacheImmediately: true,
                                     kCGImageSourceThumbnailMaxPixelSize: maxPixelSize]
        return CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary)
    }
}

final class CGImageBox: @unchecked Sendable {
    let image: CGImage
    init(_ image: CGImage) { self.image = image }
}
