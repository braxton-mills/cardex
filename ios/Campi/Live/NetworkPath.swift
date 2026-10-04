import Network
import Observation

/// The current network path, for picking Live's mode: video on Wi-Fi, snapshots on cellular or Low Data Mode.
@MainActor @Observable
final class NetworkPath {
    private(set) var isExpensive = false
    private(set) var isConstrained = false
    private let monitor = NWPathMonitor()

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let expensive = path.isExpensive, constrained = path.isConstrained
            Task { @MainActor in
                self?.isExpensive = expensive
                self?.isConstrained = constrained
            }
        }
        monitor.start(queue: DispatchQueue(label: "campi.network-path", qos: .utility))
    }

    deinit { monitor.cancel() }
}
