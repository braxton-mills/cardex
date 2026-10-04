import CampiKit
import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let status: Status?
    @State private var confirmUnpair = false
    @State private var cacheCleared = false
    @State private var pushError: String?
    @State private var tokenCopied = false

    var body: some View {
        NavigationStack {
            Form {
                if let conn = app.connection {
                    Section("Paired PC") {
                        LabeledContent("Name", value: conn.serverName)
                        LabeledContent("Address") {
                            Text(conn.baseURL.host() ?? conn.baseURL.absoluteString).textSelection(.enabled)
                        }
                        LabeledContent("This iPhone", value: conn.deviceName)
                        if let status { LabeledContent("API version", value: "\(status.apiVersion)") }
                    }
                }
                notifications
                Section {
                    Button(cacheCleared ? "Image cache cleared" : "Clear image cache") {
                        Task {
                            await ImageLoader.shared.clear()
                            cacheCleared = true
                        }
                    }
                    .disabled(cacheCleared)
                }
                Section {
                    Button("Unpair this iPhone", role: .destructive) { confirmUnpair = true }
                } footer: {
                    Text("Revokes this iPhone's access on the PC. You can pair again with campi pair.")
                }
                Section {
                    LabeledContent("Version", value: Bundle.main.versionText)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task {
                await app.push.refreshPermission()
                guard let client = app.client else { return }
                do { try await app.push.loadPrefs(client: client) } catch {
                    app.report(error)
                    pushError = "Couldn't load notification settings from the PC."
                }
            }
            .confirmationDialog("Unpair this iPhone?", isPresented: $confirmUnpair, titleVisibility: .visible) {
                Button("Unpair", role: .destructive) {
                    Task {
                        await app.unpair()
                        dismiss()
                    }
                }
            } message: {
                Text("Campi will stop showing \(app.connection?.serverName ?? "the PC") until you pair again.")
            }
        }
    }
}

extension SettingsView {
    @ViewBuilder fileprivate var notifications: some View {
        let push = app.push
        Section {
            switch push.permission {
            case .notDetermined, .unknown:
                Button("Turn On Notifications") { Task { await app.enablePush() } }
                    .accessibilityIdentifier("settings.enablePush")
            case .denied:
                Text("Notifications for Campi are off in iOS Settings.").foregroundStyle(.secondary)
                Button("Open iOS Settings") {
                    if let url = URL(string: UIApplication.openNotificationSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
            case .allowed:
                EmptyView()
            }
            if push.prefs != nil {
                prefToggle("New catches", \.newCatch, id: "newCatch")
                prefToggle("Rare sightings", \.rare, id: "rare")
                prefToggle("Gemini discoveries", \.discovered, id: "discovered")
                prefToggle("Service alerts", \.serviceAlerts, id: "serviceAlerts")
            } else if pushError == nil, app.isPaired {
                ProgressView()
            }
            if let pushError { Text(pushError).font(.footnote).foregroundStyle(.red) }
            #if DEBUG
            if let token = push.token {
                Button(tokenCopied ? "Device token copied" : "Copy device token (\(push.environment.rawValue))") {
                    UIPasteboard.general.string = token
                    tokenCopied = true
                }
            }
            #endif
        } header: {
            Text("Notifications")
        } footer: {
            Text("Sent by \(app.connection?.serverName ?? "the PC"). New catch: the first time a label is seen. Rare: its 2nd or 3rd sighting. Service alerts: the stream drops, the sightings worker crash-loops, the disk runs low or renders keep failing, and when it recovers.")
        }
    }

    private func prefToggle(_ title: String, _ key: WritableKeyPath<PushPrefs, Bool>, id: String) -> some View {
        Toggle(title, isOn: Binding(
            get: { app.push.prefs?[keyPath: key] ?? false },
            set: { value in
                Task {
                    guard let client = app.client else { return }
                    do {
                        try await app.push.set(key, value, client: client)
                        pushError = nil
                    } catch {
                        app.report(error)
                        pushError = "The PC didn't take that change. Try again when it's reachable."
                    }
                }
            }))
        .disabled(app.push.permission == .denied)
        .accessibilityIdentifier("settings.push.\(id)")
    }
}

extension Bundle {
    var versionText: String {
        let v = object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let b = object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        return "\(v) (\(b))"
    }
}
