import CampiKit
import SwiftUI

/// An API image (crop, frame, poster) through `ImageLoader` (signature-free cache key, bearer auth, downsampled).
struct RemoteImage: View {
    @Environment(AppModel.self) private var model
    let path: MediaPath?
    var maxPixelSize = 600
    var contentMode: ContentMode = .fill

    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        ZStack {
            Rectangle().fill(.quaternary)
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
        .clipped()
        .task(id: path?.cacheKey) { await load() }
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
