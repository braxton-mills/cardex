import CampiKit
import SwiftUI

/// Shown until the phone is paired: instructions for `campi pair`, plus manual entry.
struct PairingView: View {
    @Environment(AppModel.self) private var model
    @State private var server = ""
    @State private var code = ""
    @State private var deviceName = AppModel.defaultDeviceName
    @FocusState private var focused: Field?

    enum Field { case server, code, name }

    private var link: PairingLink? { PairingLink(server: server, code: code) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Pair with your PC", systemImage: "desktopcomputer.and.iphone")
                            .font(.title2.bold())
                        Text("On the PC, run **campi pair**, then scan the QR code it shows with the Camera app.")
                        Text("Or type the address and code it prints below.")
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 6)
                }

                if let reason = model.unpairedReason {
                    Section {
                        Label(reason, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }

                Section("Enter manually") {
                    TextField("campi-pc.your-tailnet.ts.net", text: $server)
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .focused($focused, equals: .server)
                        .accessibilityIdentifier("pair.server")
                    TextField("Code (XXXX-XXXX)", text: $code)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .font(.body.monospaced())
                        .focused($focused, equals: .code)
                        .accessibilityIdentifier("pair.code")
                    TextField("This iPhone's name", text: $deviceName)
                        .focused($focused, equals: .name)
                }

                if let error = model.pairingError {
                    Section {
                        Text(error).foregroundStyle(.red)
                    }
                }

                Section {
                    Button {
                        guard let link else { return }
                        focused = nil
                        Task { await model.pair(link, deviceName: deviceName) }
                    } label: {
                        HStack {
                            Text("Pair")
                            if model.isPairing { Spacer(); ProgressView() }
                        }
                    }
                    .accessibilityIdentifier("pair.submit")
                    .disabled(link == nil || model.isPairing || deviceName.trimmingCharacters(in: .whitespaces).isEmpty)
                } footer: {
                    if link?.isInsecure == true {
                        Text("Plain http:// is only for a test server. The real PC is reached over Tailscale (https).")
                    }
                }
            }
            .navigationTitle("Campi")
        }
    }
}

/// Confirmation for a `campi://pair` link opened from the Camera app.
struct PairConfirmView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let link: PairingLink
    @State private var deviceName = AppModel.defaultDeviceName

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("PC", value: link.baseURL.host() ?? link.baseURL.absoluteString)
                    LabeledContent("Code", value: link.displayCode)
                    TextField("This iPhone's name", text: $deviceName)
                } footer: {
                    if link.isInsecure {
                        Text("This is a plain http:// address: only pair with a test server you started.")
                    }
                }
                if let error = model.pairingError {
                    Section { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Pair this iPhone?")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { model.pairingError = nil; dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if model.isPairing {
                        ProgressView()
                    } else {
                        Button("Pair") { Task { await model.pair(link, deviceName: deviceName) } }
                            .disabled(deviceName.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
        }
    }
}
