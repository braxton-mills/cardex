import CampiKit
import Foundation
import Observation
import SwiftData

/// A generated card, cached on the phone: one per label (or per sighting, keyed by `CardexPrompt.sightingKey`), kept
/// until regenerated or the prompt version changes.
@Model
final class CardexRecord {
    @Attribute(.unique) var label: String
    var displayName: String
    var type: String
    var ratingNames: [String]
    var ratingValues: [Int]
    var flavor: String?
    /// `CarBodyStyle` raw value; nil on cards written before trading cards (prompt version 1).
    var bodyStyle: String?
    var generatedAt: Date
    var promptVersion: Int

    init(label: String, text: CardexText) {
        self.label = label
        displayName = text.displayName
        type = text.type
        ratingNames = text.ratings.map(\.name)
        ratingValues = text.ratings.map(\.value)
        flavor = text.flavor
        bodyStyle = text.bodyStyle.rawValue
        generatedAt = .now
        promptVersion = CardexPrompt.version
    }

    func update(_ text: CardexText) {
        displayName = text.displayName
        type = text.type
        ratingNames = text.ratings.map(\.name)
        ratingValues = text.ratings.map(\.value)
        flavor = text.flavor
        bodyStyle = text.bodyStyle.rawValue
        generatedAt = .now
        promptVersion = CardexPrompt.version
    }

    var text: CardexText {
        CardexText(displayName: displayName, type: type,
                   ratings: zip(ratingNames, ratingValues).map { .init(name: $0, value: $1) },
                   flavor: flavor, bodyStyle: bodyStyle.flatMap(CarBodyStyle.init) ?? .sedan, generated: true)
    }
}

/// The paint color of a label's 3D car: the cover sighting's color, looked up once per cover.
@Model
final class CardexPaint {
    @Attribute(.unique) var label: String
    var coverSightingID: String
    /// The cloud's color word, or nil when the cover sighting has none.
    var colorName: String?

    init(label: String, coverSightingID: String, colorName: String?) {
        self.label = label
        self.coverSightingID = coverSightingID
        self.colorName = colorName
    }
}

/// Card text for collection labels: from the cache, else generated once (one generation per label at a time),
/// else the plain fallback, which isn't cached so a real card appears once Apple Intelligence is ready.
@MainActor @Observable
final class CardexStore {
    struct Card: Equatable {
        var text: CardexText
        /// Why this is the plain fallback.
        var note: String?
    }

    private(set) var cards: [String: Card] = [:]
    private(set) var generating: Set<String> = []
    /// Paint per label; a label without an entry hasn't been looked up yet.
    private(set) var paints: [String: PaintColor?] = [:]
    private var paintLookups: Set<String> = []
    private var inflight: [String: Task<Void, Never>] = [:]
    /// The binder asks for many cards at once; the on-device model writes one at a time.
    private var writing = false
    private var queue: [CheckedContinuation<Void, Never>] = []
    private let context: ModelContext?

    init() {
        #if DEBUG
        let inMemory = ProcessInfo.processInfo.arguments.contains("-cardexFallback")
        #else
        let inMemory = false
        #endif
        context = (try? ModelContainer(for: CardexRecord.self, CardexPaint.self,
                                       configurations: ModelConfiguration(isStoredInMemoryOnly: inMemory)))
            .map { ModelContext($0) }
    }

    func card(for label: String) -> Card? { cards[label] }

    /// The 3D car's paint: the cover sighting's color, else neutral.
    func paint(for label: String) -> PaintColor { (paints[label] ?? nil) ?? .neutral }

    /// Looks up the cover sighting's color once per cover (cached across launches).
    func loadPaint(_ item: CollectionItem, app: AppModel) async {
        guard let cover = item.cover else { paints[item.label] = .some(nil); return }
        let label = item.label
        var d = FetchDescriptor<CardexPaint>(predicate: #Predicate { $0.label == label })
        d.fetchLimit = 1
        let saved = try? context?.fetch(d).first
        if let saved, saved.coverSightingID == cover.sightingId {
            paints[label] = PaintColor.from(saved.colorName)
            return
        }
        guard !paintLookups.contains(label), let client = app.client else { return }
        paintLookups.insert(label)
        defer { paintLookups.remove(label) }
        guard let s = try? await client.sighting(id: cover.sightingId) else { return }   // retried next time
        let color = app.sightings.resolve(s).color
        paints[label] = PaintColor.from(color)
        if let saved {
            saved.coverSightingID = cover.sightingId
            saved.colorName = color
        } else {
            context?.insert(CardexPaint(label: label, coverSightingID: cover.sightingId, colorName: color))
        }
        try? context?.save()
    }

    /// Makes sure `item` has a card: cached, generated, or the fallback.
    func load(_ item: CollectionItem) async {
        await load(key: item.label) { try await CardexGenerator.generate(for: item) }
            fallback: { CardexPrompt.fallback(for: item) }
    }

    /// Writes the card again, replacing the cached one.
    func regenerate(_ item: CollectionItem) async {
        await generate(key: item.label, force: true) { try await CardexGenerator.generate(for: item) }
            fallback: { CardexPrompt.fallback(for: item) }
    }

    // MARK: sighting cards

    /// One sighting's own card, written for that car (its color, when it passed). Cached like label cards.
    func card(for s: Sighting) -> Card? { cards[CardexPrompt.sightingKey(s)] }
    func isGenerating(_ s: Sighting) -> Bool { generating.contains(CardexPrompt.sightingKey(s)) }

    /// The sighting's own paint: its color, else neutral.
    func paint(for s: Sighting) -> PaintColor { PaintColor.from(s.color) ?? .neutral }

    func load(_ s: Sighting, item: CollectionItem?) async {
        await load(key: CardexPrompt.sightingKey(s)) { try await CardexGenerator.generate(for: s, item: item) }
            fallback: { CardexPrompt.fallback(for: s, item: item) }
    }

    func regenerate(_ s: Sighting, item: CollectionItem?) async {
        await generate(key: CardexPrompt.sightingKey(s), force: true) {
            try await CardexGenerator.generate(for: s, item: item)
        } fallback: {
            CardexPrompt.fallback(for: s, item: item)
        }
    }

    // MARK: generation

    /// `key` is a collection label or a `CardexPrompt.sightingKey`.
    private func load(key: String, write: @escaping @MainActor () async throws -> CardexText,
                      fallback: @escaping @MainActor () -> CardexText) async {
        if let c = cards[key], c.text.generated || generating.contains(key) { return }
        if let record = record(for: key), record.promptVersion == CardexPrompt.version {
            cards[key] = Card(text: record.text)
            return
        }
        await generate(key: key, write: write, fallback: fallback)
    }

    private func generate(key: String, force: Bool = false,
                          write: @escaping @MainActor () async throws -> CardexText,
                          fallback: @escaping @MainActor () -> CardexText) async {
        if let running = inflight[key] {
            await running.value
            return
        }
        if cards[key] == nil || force { generating.insert(key) }
        let task = Task { @MainActor in
            defer {
                generating.remove(key)
                inflight[key] = nil
            }
            await acquire()
            defer { release() }
            do {
                let text = try await write()
                cards[key] = Card(text: text)
                save(text, label: key)
            } catch {
                let note: String = switch error as? CardexGenerator.Failure {
                case .unavailable(let why), .failed(let why): why
                case nil: "Couldn't write this card."
                }
                // keep a good card we already have; otherwise show the plain one
                if cards[key]?.text.generated != true {
                    cards[key] = Card(text: fallback(), note: note)
                }
            }
        }
        inflight[key] = task
        await task.value
    }

    private func acquire() async {
        if !writing {
            writing = true
            return
        }
        await withCheckedContinuation { queue.append($0) }
    }

    private func release() {
        if queue.isEmpty { writing = false } else { queue.removeFirst().resume() }
    }

    private func record(for label: String) -> CardexRecord? {
        var d = FetchDescriptor<CardexRecord>(predicate: #Predicate { $0.label == label })
        d.fetchLimit = 1
        return try? context?.fetch(d).first
    }

    private func save(_ text: CardexText, label: String) {
        guard let context else { return }
        if let r = record(for: label) {
            r.update(text)
        } else {
            context.insert(CardexRecord(label: label, text: text))
        }
        try? context.save()
    }
}
