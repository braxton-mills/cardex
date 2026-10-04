import CampiKit
import SwiftUI

/// Pick the correct label from the collection (the only labels the PC accepts, api-contract §4.5).
struct LabelPickerView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let current: String?
    let corrected: Bool
    let pick: (String?) -> Void

    @State private var search = ""
    @State private var collection: LabelCollection?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if corrected && search.isEmpty {
                    Section {
                        Button("Remove my correction", systemImage: "arrow.uturn.backward") {
                            pick(nil)
                            dismiss()
                        }
                    } footer: {
                        Text("Goes back to the machine's label.")
                    }
                }
                if let collection {
                    ForEach(collection.sections(matching: search)) { section in
                        Section(section.title) {
                            ForEach(section.items) { item in
                                Button {
                                    pick(item.label)
                                    dismiss()
                                } label: {
                                    HStack {
                                        Text(item.label).foregroundStyle(.primary)
                                        Spacer()
                                        if item.count > 0 {
                                            Text("\(item.count)").foregroundStyle(.secondary).monospacedDigit()
                                        }
                                        if item.label == current {
                                            Image(systemName: "checkmark").foregroundStyle(.tint)
                                        }
                                    }
                                }
                                .disabled(item.label == current)
                            }
                        }
                    }
                }
            }
            .overlay {
                if collection == nil {
                    if let error {
                        ContentUnavailableView("Can't load labels", systemImage: "exclamationmark.triangle",
                                               description: Text(error))
                    } else {
                        ProgressView()
                    }
                } else if collection?.sections(matching: search).isEmpty == true {
                    ContentUnavailableView.search(text: search)
                }
            }
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always), prompt: "Make or model")
            .navigationTitle("Correct Label")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .task {
                do {
                    collection = try await app.loadCollection()
                } catch {
                    self.error = (error as? APIError)?.localizedDescription ?? error.localizedDescription
                }
            }
        }
    }
}
