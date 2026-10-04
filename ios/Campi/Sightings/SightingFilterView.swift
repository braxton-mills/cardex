import CampiKit
import SwiftUI

struct SightingFilterView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var draft: SightingQuery
    @State private var limitDates: Bool
    @State private var fromDate: Date
    @State private var toDate: Date
    @State private var makes: [String] = []
    let apply: (SightingQuery) -> Void

    static let classes = [("car", "car.side"), ("truck", "truck.pickup.side"), ("bus", "bus"),
                          ("motorcycle", "motorcycle")]

    init(query: SightingQuery, apply: @escaping (SightingQuery) -> Void) {
        _draft = State(initialValue: query)
        _limitDates = State(initialValue: query.from != nil || query.to != nil)
        _fromDate = State(initialValue: query.from?.noon(in: .current) ?? Calendar.current.date(byAdding: .day, value: -7, to: .now)!)
        _toDate = State(initialValue: query.to?.noon(in: .current) ?? .now)
        self.apply = apply
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("Only these dates", isOn: $limitDates)
                    if limitDates {
                        DatePicker("From", selection: $fromDate, in: ...toDate, displayedComponents: .date)
                        DatePicker("To", selection: $toDate, in: fromDate..., displayedComponents: .date)
                    }
                }
                Section("Type") {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack {
                            ForEach(Self.classes, id: \.0) { cls, symbol in
                                Chip(title: cls.capitalized, symbol: symbol, selected: draft.classes.contains(cls)) {
                                    toggle(&draft.classes, cls)
                                }
                            }
                        }
                    }
                }
                Section("Make") {
                    NavigationLink {
                        MakePicker(all: makes, selected: $draft.makes)
                    } label: {
                        LabeledContent("Makes", value: draft.makes.isEmpty ? "Any" : draft.makes.joined(separator: ", "))
                    }
                    .disabled(makes.isEmpty)
                }
                Section("Label decided by") {
                    HStack {
                        ForEach([Sighting.DecidedBy.siglip, .cloud, .user], id: \.self) { d in
                            Chip(title: d.badge, symbol: d.symbol, selected: draft.decidedBy.contains(d)) {
                                toggle(&draft.decidedBy, d)
                            }
                        }
                    }
                }
                Section {
                    Toggle("Starred only", isOn: $draft.starredOnly)
                    Toggle("Hide unsure guesses", isOn: $draft.hideUnsure)
                    Toggle("Hide parked vehicles", isOn: $draft.hideStationary)
                    Toggle("Show hidden sightings", isOn: $draft.includeHidden)
                } footer: {
                    Text("Unsure guesses that Gemini or you decided are always shown.")
                }
            }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        var q = draft
                        q.from = limitDates ? PCDay(date: fromDate, timeZone: .current) : nil
                        q.to = limitDates ? PCDay(date: toDate, timeZone: .current) : nil
                        apply(q)
                        dismiss()
                    }
                }
                ToolbarItem(placement: .bottomBar) {
                    Button("Reset All", role: .destructive) {
                        draft = SightingQuery(limit: draft.limit)
                        limitDates = false
                    }
                    .disabled(draft.activeFilterCount == 0 && !limitDates)
                }
            }
            .task { makes = (try? await app.loadCollection().makes) ?? [] }
        }
    }

    private func toggle<T: Equatable>(_ list: inout [T], _ value: T) {
        if let i = list.firstIndex(of: value) { list.remove(at: i) } else { list.append(value) }
    }
}

private struct MakePicker: View {
    let all: [String]
    @Binding var selected: [String]
    @State private var search = ""

    var body: some View {
        List {
            ForEach(all.filter { search.isEmpty || $0.localizedCaseInsensitiveContains(search) }, id: \.self) { make in
                Button {
                    if let i = selected.firstIndex(of: make) { selected.remove(at: i) } else { selected.append(make) }
                } label: {
                    HStack {
                        Text(make).foregroundStyle(.primary)
                        Spacer()
                        if selected.contains(make) { Image(systemName: "checkmark").foregroundStyle(.tint) }
                    }
                }
            }
        }
        .searchable(text: $search)
        .navigationTitle("Makes")
        .toolbar {
            if !selected.isEmpty { Button("Clear") { selected = [] } }
        }
    }
}
