import CampiKit
import SwiftUI

/// Everything `campi status` shows, structured.
struct StatusDetailView: View {
    let status: Status
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                let health = status.health()
                if !health.issues.isEmpty {
                    Section("Issues") {
                        ForEach(health.issues, id: \.self) { issue in
                            Label(issue.text, systemImage: issue.level == .error ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                                .foregroundStyle(issue.level.color)
                        }
                    }
                }
                Section("Service") {
                    row("State", status.service.state == .running ? "Running" : (status.service.disabled ? "Stopped (campi stop)" : "Stopped"))
                    if let pid = status.service.pid { row("PID", "\(pid)") }
                    if let started = status.service.startedAt { row("Started", started.dateTimeText) }
                    if let hb = status.service.heartbeatAt { row("Heartbeat", hb.agoText()) }
                }
                Section("Capture") {
                    row("Stream", status.capture.connected ? "Connected" : "Disconnected")
                    if let host = status.capture.host { row("Pi", host) }
                    row("Last frame", status.capture.lastFrameAt?.agoText() ?? "never")
                    row("Saved / rejected", "\(status.capture.saved.formatted()) / \(status.capture.rejected.formatted())")
                    row("Reconnects", "\(status.capture.reconnects)")
                    if let r = status.capture.restarts { row("Restarts", "\(r)") }
                    if let e = status.capture.lastError { row("Last error", e) }
                }
                Section("Clips") {
                    if let last = status.clips.last { renderRow("Last clip", last) }
                    if let next = status.clips.nextAt { row("Next clip", next.timeText) }
                    if status.clips.running == true { row("Rendering", "now") }
                    if let q = status.clips.queue {
                        row("Render queue", q.length == 0 ? "empty" : "\(q.length)" + (q.deferred ? " (deferred: \(q.deferredReason == .gaming ? "gaming" : "yes"))" : ""))
                    }
                }
                Section("Daily video") {
                    if let last = status.daily.last { renderRow("Last daily", last) }
                    if status.daily.running == true { row("Rendering", "now") }
                }
                sightingsSection
                if let g = status.gaming {
                    Section("Gaming") {
                        row("Mode", g.mode.rawValue)
                        row("Game running", g.active ? (g.exe ?? "yes") : "no")
                        if g.rendersDeferred { row("Renders", "deferred") }
                        if g.detection == .pathOnly { row("Detection", "path match only (GPU counters unavailable)") }
                    }
                }
                Section("Disk") {
                    row("Free", String(format: "%.1f GB (%@)", status.disk.freeGB, status.disk.drive))
                }
                Section("PC") {
                    row("Name", status.serverName)
                    row("API version", "\(status.apiVersion)")
                    row("PC time", status.serverTime.dateTimeText)
                }
            }
            .navigationTitle("Status")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }

    @ViewBuilder private var sightingsSection: some View {
        let s = status.sightings
        Section("Sightings") {
            row("Worker", sightingsStateText(s))
            if let phase = s.phase { row("Phase", phaseText(phase)) }
            if let backend = s.backend {
                row("Backend", backend.rawValue + (s.cpuFallback == true ? " (CPU FALLBACK)" : ""))
            }
            if let device = s.device { row("Device", device) }
            if let q = s.classifyQueue { row("Classify queue", "\(q)") }
            if let cloud = s.cloud, cloud.enabled { row("Gemini today", "\(cloud.callsToday) / \(cloud.cap)") }
            row("Today / total", "\(s.today) / \(s.total)")
            if let last = s.lastSightingAt { row("Last sighting", last.dateTimeText) }
            if let r = s.restarts { row("Restarts", "\(r)") }
            if let e = s.lastError { row("Last error", e) }
        }
    }

    private func sightingsStateText(_ s: SightingsStatus) -> String {
        switch s.state {
        case .disabled: "Disabled"
        case .notInstalled: "Not installed (install.ps1 -Sightings)"
        case .serviceStopped: "Service stopped"
        case .notRunning: "Not running"
        case .starting: "Starting"
        case .running: "Running"
        case .crashLooping: "CRASH-LOOPING" + (s.nextRetryAt.map { ", retry \($0.timeText)" } ?? "")
        case .pausedGaming: "Paused while gaming"
        case .unknown: "Unknown"
        }
    }

    private func phaseText(_ p: SightingsStatus.Phase) -> String {
        switch p {
        case .loading: "Loading models"
        case .disconnected: "Disconnected from the stream"
        case .running: "Watching"
        case .pausedDark: "Paused (dark)"
        case .error: "Error"
        case .unknown: "Unknown"
        }
    }

    private func renderRow(_ title: String, _ r: RenderResult) -> some View {
        let what = switch r.status {
        case .ok: "OK" + (r.finishedAt.map { " · \($0.agoText())" } ?? "")
        case .skipped: "Skipped: \(r.detail ?? "")"
        case .error: "Failed: \(r.detail ?? "")"
        case .unknown: "Unknown"
        }
        return row(title, what)
    }

    private func row(_ title: String, _ value: String) -> some View {
        LabeledContent(title) { Text(value).multilineTextAlignment(.trailing).textSelection(.enabled) }
    }
}
