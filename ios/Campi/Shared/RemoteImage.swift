import CampiKit
import SwiftUI

/// An API image (crop, frame, poster) through `ImageLoader` (signature-free cache key, bearer auth, downsampled).
struct RemoteImage: View {
    @Environment(AppModel.self) private var model
    let path: MediaPath?
    var maxPixelSize = 600
    var contentMode: ContentMode = .fill
    /// VoiceOver description ("Photo of Toyota GR86"); without one the image is left to its container.
    var label: String?

    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        Group {
            if contentMode == .fill {
                // The rectangle takes exactly the offered size; a filled image in a ZStack would grow the
                // layout past it (cards spilling into the next grid column).
                Rectangle().fill(.quaternary).overlay { content }
            } else {
                ZStack {
                    Rectangle().fill(.quaternary)
                    content
                }
            }
        }
        .clipped()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label ?? "")
        .accessibilityAddTraits(label == nil ? [] : .isImage)
        .accessibilityHidden(label == nil)
        .task(id: path?.cacheKey) { await load() }
    }

    @ViewBuilder private var content: some View {
        if let image {
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: contentMode)
                .transition(.opacity)
        } else if failed || path == nil {
            Image(systemName: "photo")
                .foregroundStyle(.secondary)
        }
    }

    private func load() async {
        guard let path, let client = model.client else { return }
        do {
            let cg = try await ImageLoader.shared.image(path, client: client, maxPixelSize: maxPixelSize)
            withAnimation(.easeIn(duration: 0.15)) { image = UIImage(cgImage: cg) }
        } catch {
            failed = true
        }
    }
}
