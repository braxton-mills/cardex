import Foundation

/// Loads the PC's 3D car scans (`CollectionItem.Mesh`) with a memory + disk cache keyed on the path without its
/// signature, like `ImageLoader`. A scan's file name changes when the PC rescans the car, so a hit never goes stale.
public actor CarScanLoader {
    public static let shared = CarScanLoader()

    /// The most recently used scans, newest last: each is a few MB, and a binder page can ask for dozens.
    private var memory: [(key: String, scan: CarScan)] = []
    private let memoryLimit = 12
    private let diskDir: URL?
    private var inflight: [String: Task<CarScan, any Error>] = [:]

    public init(diskDirectory: URL? = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
        .appending(path: "CampiScans", directoryHint: .isDirectory)) {
        diskDir = diskDirectory
        if let diskDirectory {
            try? FileManager.default.createDirectory(at: diskDirectory, withIntermediateDirectories: true)
        }
    }

    public func scan(_ mesh: CollectionItem.Mesh, client: APIClient) async throws -> CarScan {
        let key = mesh.url.cacheKey
        if let i = memory.firstIndex(where: { $0.key == key }) {
            let hit = memory.remove(at: i)
            memory.append(hit)
            return hit.scan
        }
        if let task = inflight[key] { return try await task.value }
        let task = Task { [diskDir] in
            let file = diskDir?.appending(path: ImageLoader.hash(key))
            if let file, let cached = try? Data(contentsOf: file), let scan = try? CarScan(glb: cached) {
                return scan
            }
            let data = try await client.mediaData(mesh.url)
            do {
                let scan = try CarScan(glb: data)
                if let file { try? data.write(to: file, options: .atomic) }
                return scan
            } catch {
                throw APIError.decoding("not a car scan: \(key)")
            }
        }
        inflight[key] = task
        defer { inflight[key] = nil }
        let scan = try await task.value
        memory.append((key, scan))
        if memory.count > memoryLimit { memory.removeFirst() }
        return scan
    }

    /// Drops everything cached on disk and in memory (Settings → clear cache, or after unpairing).
    public func clear() {
        memory.removeAll()
        if let diskDir {
            try? FileManager.default.removeItem(at: diskDir)
            try? FileManager.default.createDirectory(at: diskDir, withIntermediateDirectories: true)
        }
    }
}
