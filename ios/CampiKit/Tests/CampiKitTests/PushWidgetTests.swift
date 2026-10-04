import Foundation
import Testing
@testable import CampiKit

@Suite struct PushWidgetTests {
    @Test func deepLinks() throws {
        #expect(DeepLink(url: URL(string: "campi://sighting/5f0c2a9e-1b7d-4c3e-9a51-0d6c8e2f4b11")!)
                == .sighting(id: "5f0c2a9e-1b7d-4c3e-9a51-0d6c8e2f4b11"))
        #expect(DeepLink(url: URL(string: "campi://today")!) == .today)
        #expect(DeepLink(url: URL(string: "CAMPI://status")!) == .status)
        #expect(DeepLink(url: URL(string: "campi://sighting/")!) == nil)
        #expect(DeepLink(url: URL(string: "https://sighting/abc")!) == nil)
        #expect(DeepLink(url: URL(string: "campi://nope")!) == nil)
        guard case .pair(let link)? = DeepLink(url: URL(string: "campi://pair?u=https%3A%2F%2Fpc.ts.net&c=K3J9-Q2M8")!)
        else { Issue.record("pair link"); return }
        #expect(link.code == "K3J9Q2M8")
        for l in [DeepLink.sighting(id: "abc-123"), .today, .status, .pair(link)] {
            #expect(DeepLink(url: l.url) == l)
        }
    }

    @Test func pushTapsGoToSightingOrStatus() throws {
        let catchObj = try JSONSerialization.jsonObject(with: Fixtures.data("push_new_catch.json")) as! [String: Any]
        let info = try #require(PushInfo(userInfo: catchObj))
        #expect(DeepLink(push: info) == .sighting(id: info.sightingId!))
        let svcObj = try JSONSerialization.jsonObject(with: Fixtures.data("push_service.json")) as! [String: Any]
        #expect(DeepLink(push: try #require(PushInfo(userInfo: svcObj))) == .status)
    }

    static func profile(_ aps: String?) -> Data {
        let ents = aps.map { "<key>aps-environment</key><string>\($0)</string>" } ?? ""
        let xml = """
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0"><dict><key>Name</key><string>iOS Team Provisioning Profile</string>
            <key>Entitlements</key><dict>\(ents)<key>application-identifier</key><string>X.com.braxtonmills.campi</string></dict>
            </dict></plist>
            """
        // CMS envelope bytes around the plist, as in a real .mobileprovision
        return Data([0x30, 0x82, 0x4e, 0x1f, 0x06, 0x09]) + Data(xml.utf8) + Data([0xa0, 0x82, 0x0d, 0x00, 0xff])
    }

    @Test func apsEnvironmentFollowsTheProfile() {
        #expect(APSEnvironment.fromProvisioningProfile(Self.profile("development")) == .sandbox)
        #expect(APSEnvironment.fromProvisioningProfile(Self.profile("production")) == .production)
        #expect(APSEnvironment.fromProvisioningProfile(Self.profile(nil)) == nil)
        #expect(APSEnvironment.fromProvisioningProfile(Data("garbage".utf8)) == nil)
        #expect(Data([0x0a, 0xff, 0x00]).apnsTokenString == "0aff00")
    }

    @Test func widgetSnapshotAndDisplay() throws {
        let status = try Fixtures.decode(Status.self, "status.json")
        let latest = WidgetSnapshot.Catch(sightingID: "abc", label: "Toyota GR86", at: status.serverTime, cropFile: nil)
        let snap = WidgetSnapshot(status: status, latest: latest, fetchedAt: status.serverTime.date)
        #expect(snap.todayCount == status.sightings.today)
        #expect(snap.day == status.today)
        #expect(snap.todayCount(now: status.serverTime.date) == status.sightings.today)
        #expect(snap.todayCount(now: status.serverTime.date.addingTimeInterval(86_400)) == nil)   // the day moved on

        #expect(WidgetDisplay.choose(paired: false, fetched: snap, saved: snap) == .unpaired)
        #expect(WidgetDisplay.choose(paired: true, fetched: snap, saved: nil) == .fresh(snap))
        #expect(WidgetDisplay.choose(paired: true, fetched: nil, saved: snap) == .unreachable(snap))
        #expect(WidgetDisplay.choose(paired: true, fetched: nil, saved: nil) == .unreachableNoData)
    }

    @Test func widgetStoreKeepsOnlyTheCurrentCrop() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "widget-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = WidgetStore(directory: dir)
        #expect(store.load() == nil)
        let status = try Fixtures.decode(Status.self, "status.json")
        let old = try store.saveCrop(Data([1]), sightingID: "old")
        let new = try store.saveCrop(Data([2]), sightingID: "new/../x")
        #expect(!new.contains("/"))
        let snap = WidgetSnapshot(status: status, latest: .init(sightingID: "new", label: "x", at: status.serverTime,
                                                                cropFile: new))
        try store.save(snap)
        #expect(store.load() == snap)
        #expect(FileManager.default.fileExists(atPath: store.cropURL(new).path()))
        #expect(!FileManager.default.fileExists(atPath: store.cropURL(old).path()))
        store.clear()
        #expect(store.load() == nil)
    }
}
