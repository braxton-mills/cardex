import Foundation

/// What the Home Screen widget shows, saved in the App Group as the last good state so the widget still has
/// something when the PC can't be reached. Written by the widget's timeline provider and the notification extension.
public struct WidgetSnapshot: Codable, Hashable, Sendable {
    public struct Catch: Codable, Hashable, Sendable {
        public var sightingID: String
        public var label: String
        public var at: PCDate
        /// File name of the downscaled crop in the widget store.
        public var cropFile: String?

        public init(sightingID: String, label: String, at: PCDate, cropFile: String?) {
            self.sightingID = sightingID
            self.label = label
            self.at = at
            self.cropFile = cropFile
        }
    }

    public var serverName: String
    public var fetchedAt: Date
    /// The PC-local day `todayCount` belongs to.
    public var day: PCDay
    /// The PC's UTC offset when fetched, to tell when `day` is no longer today.
    public var utcOffsetSeconds: Int
    /// `status.sightings.today`, the same number as the app's Today tile.
    public var todayCount: Int
    /// `HealthReport.Level` raw value.
    public var healthLevel: Int
    public var healthText: String
    public var latest: Catch?

    public init(serverName: String, fetchedAt: Date, day: PCDay, utcOffsetSeconds: Int, todayCount: Int,
                healthLevel: Int, healthText: String, latest: Catch?) {
        self.serverName = serverName
        self.fetchedAt = fetchedAt
        self.day = day
        self.utcOffsetSeconds = utcOffsetSeconds
        self.todayCount = todayCount
        self.healthLevel = healthLevel
        self.healthText = healthText
        self.latest = latest
    }

    public init(status: Status, latest: Catch?, fetchedAt: Date = .now) {
        let health = status.health(now: fetchedAt)
        serverName = status.serverName
        self.fetchedAt = fetchedAt
        day = status.serverTime.day
        utcOffsetSeconds = status.serverTime.utcOffsetSeconds
        todayCount = status.sightings.today
        healthLevel = health.level.rawValue
        healthText = health.headline
        self.latest = latest
    }

    public var health: HealthReport.Level { HealthReport.Level(rawValue: healthLevel) ?? .ok }

    /// Today's count, or `nil` once the PC's day has moved on since this was fetched.
    public func todayCount(now: Date = .now) -> Int? {
        PCDay(date: now, timeZone: TimeZone(secondsFromGMT: utcOffsetSeconds) ?? .gmt) == day ? todayCount : nil
    }
}

/// What the widget should show.
public enum WidgetDisplay: Hashable, Sendable {
    /// Open the app to pair.
    case unpaired
    /// Fetched just now.
    case fresh(WidgetSnapshot)
    /// The PC didn't answer: the last good snapshot.
    case unreachable(WidgetSnapshot)
    /// The PC didn't answer and there's nothing saved yet.
    case unreachableNoData

    public static func choose(paired: Bool, fetched: WidgetSnapshot?, saved: WidgetSnapshot?) -> WidgetDisplay {
        guard paired else { return .unpaired }
        if let fetched { return .fresh(fetched) }
        if let saved { return .unreachable(saved) }
        return .unreachableNoData
    }
}

/// The widget's files in the App Group: `snapshot.json` and the latest crop.
public struct WidgetStore: Sendable {
    public let directory: URL

    public init(directory: URL = SharedConfig.containerURL.appending(path: "Widget", directoryHint: .isDirectory)) {
        self.directory = directory
    }

    private var snapshotURL: URL { directory.appending(path: "snapshot.json") }

    public func load() -> WidgetSnapshot? {
        guard let data = try? Data(contentsOf: snapshotURL) else { return nil }
        return try? JSONDecoder().decode(WidgetSnapshot.self, from: data)
    }

    public func save(_ snapshot: WidgetSnapshot) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try JSONEncoder().encode(snapshot).write(to: snapshotURL, options: .atomic)
        removeCrops(except: snapshot.latest?.cropFile)
    }

    /// Saves a (downscaled) crop JPEG for a sighting and returns its file name.
    public func saveCrop(_ jpeg: Data, sightingID: String) throws -> String {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let safe = sightingID.filter { $0.isLetter || $0.isNumber || $0 == "-" }.prefix(64)
        let name = "crop-\(safe).jpg"
        try jpeg.write(to: directory.appending(path: name), options: .atomic)
        return name
    }

    public func cropURL(_ file: String) -> URL { directory.appending(path: file) }

    /// Unpairing: the next widget render shows "pair" with no stale data.
    public func clear() { try? FileManager.default.removeItem(at: directory) }

    private func removeCrops(except keep: String?) {
        let files = (try? FileManager.default.contentsOfDirectory(atPath: directory.path())) ?? []
        for f in files where f.hasPrefix("crop-") && f != keep {
            try? FileManager.default.removeItem(at: directory.appending(path: f))
        }
    }
}
