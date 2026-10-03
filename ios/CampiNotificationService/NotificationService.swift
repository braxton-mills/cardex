import CampiKit
import Foundation
import UserNotifications
import WidgetKit

/// Attaches the sighting's crop to Campi pushes (api-contract §9.3). `campi.crop` is an unsigned path: it's fetched
/// with the bearer token from the shared Keychain, so late deliveries still work after a signed URL would expire.
/// Also hands the catch to the widget.
final class NotificationService: UNNotificationServiceExtension, @unchecked Sendable {
    private let lock = NSLock()
    private var contentHandler: ((UNNotificationContent) -> Void)?
    private var content: UNMutableNotificationContent?
    private var work: Task<Void, Never>?

    /// The extension has ~24 MB of memory: keep decoded images small.
    static let attachmentPixels = 1000
    static let widgetPixels = 400

    override func didReceive(_ request: UNNotificationRequest,
                             withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        guard let content = request.content.mutableCopy() as? UNMutableNotificationContent else {
            contentHandler(request.content)
            return
        }
        lock.withLock {
            self.contentHandler = contentHandler
            self.content = content
        }
        guard let info = PushInfo(userInfo: content.userInfo), let crop = info.crop,
              let (conn, token) = SharedConfig.connectionStore.load() else {
            deliver(attachment: nil)
            return
        }
        let client = APIClient(baseURL: conn.baseURL, token: token)
        work = Task {
            guard let data = try? await client.mediaData(crop), !Task.isCancelled else {
                self.deliver(attachment: nil)
                return
            }
            let attachment = Self.attachment(data)
            Self.updateWidget(info, crop: data)
            self.deliver(attachment: attachment)
        }
    }

    override func serviceExtensionTimeWillExpire() {
        work?.cancel()
        deliver(attachment: nil)   // the push as sent, without the image
    }

    private func deliver(attachment: UNNotificationAttachment?) {
        let (handler, content) = lock.withLock {
            defer { contentHandler = nil }
            return (contentHandler, self.content)
        }
        guard let handler, let content else { return }   // already delivered
        if let attachment { content.attachments = [attachment] }
        handler(content)
    }

    static func attachment(_ data: Data) -> UNNotificationAttachment? {
        guard let jpeg = ImageLoader.downsampledJPEG(data, maxPixelSize: attachmentPixels) else { return nil }
        let url = FileManager.default.temporaryDirectory.appending(path: "campi-\(UUID().uuidString).jpg")
        guard (try? jpeg.write(to: url)) != nil else { return nil }
        return try? UNNotificationAttachment(identifier: "crop", url: url,
                                             options: [UNNotificationAttachmentOptionsTypeHintKey: "public.jpeg"])
    }

    /// The pushed sighting becomes the widget's latest catch, so it shows even if the widget can't reach the PC.
    static func updateWidget(_ info: PushInfo, crop: Data) {
        let store = WidgetStore()
        guard var snap = store.load(), let id = info.sightingId, let label = info.label else { return }
        let file = ImageLoader.downsampledJPEG(crop, maxPixelSize: widgetPixels)
            .flatMap { try? store.saveCrop($0, sightingID: id) }
        snap.latest = .init(sightingID: id, label: label,
                            at: PCDate(date: .now, utcOffsetSeconds: snap.utcOffsetSeconds), cropFile: file)
        try? store.save(snap)
        WidgetCenter.shared.reloadAllTimelines()
    }
}
