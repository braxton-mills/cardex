import CampiKit
import Foundation
import SwiftUI

extension PCDate {
    /// Wall-clock time on the PC ("2:05 PM"), matching the video overlays.
    var timeText: String {
        date.formatted(Date.FormatStyle(date: .omitted, time: .shortened, timeZone: timeZone))
    }

    var dateTimeText: String {
        date.formatted(Date.FormatStyle(date: .abbreviated, time: .shortened, timeZone: timeZone))
    }

    /// "12s ago", "5 min ago", "3 hr ago", relative to now.
    func agoText(now: Date = .now) -> String {
        let s = max(0, now.timeIntervalSince(date))
        if s < 60 { return "\(Int(s))s ago" }
        if s < 3600 { return "\(Int(s / 60)) min ago" }
        if s < 86_400 { return "\(Int(s / 3600)) hr ago" }
        return "\(Int(s / 86_400)) d ago"
    }
}

extension PCDay {
    /// "Fri, Oct 3", in the PC's zone.
    func text(timeZone: TimeZone) -> String {
        noon(in: timeZone).formatted(Date.FormatStyle(timeZone: timeZone).weekday(.abbreviated).month(.abbreviated).day())
    }
}

func percentText(_ p: Double?) -> String {
    guard let p else { return "–" }
    return p.formatted(.percent.precision(.fractionLength(0)))
}

extension Sighting.DecidedBy {
    var badge: String {
        switch self {
        case .siglip: "SigLIP"
        case .cloud: "Gemini"
        case .user: "You"
        case .unknown: "?"
        }
    }

    var symbol: String {
        switch self {
        case .siglip: "cpu"
        case .cloud: "sparkle"
        case .user: "person.fill"
        case .unknown: "questionmark"
        }
    }
}

extension Sighting.Direction {
    var symbol: String {
        switch self {
        case .leftToRight: "arrow.right"
        case .rightToLeft: "arrow.left"
        case .unknown: "arrow.left.and.right"
        }
    }
}

extension HealthReport.Level {
    var color: Color {
        switch self {
        case .ok: .green
        case .warning: .orange
        case .error: .red
        }
    }
}
