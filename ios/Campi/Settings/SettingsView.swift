import CampiKit
import SwiftUI

struct SettingsView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let status: Status?
    @State private var confirmUnpair = false
    @State private var cacheCleared = false

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

extension Bundle {
    var versionText: String {
        let v = object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let b = object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        return "\(v) (\(b))"
    }
}
