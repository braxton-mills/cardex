import Foundation
import Testing
@testable import CampiKit

@Suite struct PCDateTests {
    @Test func parsesOffsetAndMilliseconds() throws {
        let d = try #require(PCDate(rfc3339: "2026-10-03T14:05:12.345-05:00"))
        #expect(d.utcOffsetSeconds == -18_000)
        #expect(abs(d.date.timeIntervalSince1970 - 1_791_054_312.345) < 0.0005)
        #expect(d.rfc3339 == "2026-10-03T14:05:12.345-05:00")
        #expect(d.day == PCDay("2026-10-03"))
    }

    @Test func acceptsZAndNoFraction() throws {
        let z = try #require(PCDate(rfc3339: "2026-10-03T19:05:12Z"))
        #expect(z.utcOffsetSeconds == 0)
        #expect(z.rfc3339 == "2026-10-03T19:05:12.000+00:00")
        let plus = try #require(PCDate(rfc3339: "2026-10-03T23:35:12.000+05:30"))
        #expect(plus.utcOffsetSeconds == 19_800)
    }

    @Test func rejectsGarbage() {
        #expect(PCDate(rfc3339: "yesterday") == nil)
        #expect(PCDate(rfc3339: "2026-10-03 14:05:12") == nil)
    }

    /// The PC-local day uses the offset carried by each instant, so DST changes bucket correctly.
    @Test func dayAcrossDSTChange() throws {
        // 2026-11-01 01:30 happens twice in Chicago: once at -05:00 (CDT), once at -06:00 (CST)
        let cdt = try #require(PCDate(rfc3339: "2026-11-01T01:30:00.000-05:00"))
        let cst = try #require(PCDate(rfc3339: "2026-11-01T01:30:00.000-06:00"))
        #expect(cst.date.timeIntervalSince(cdt.date) == 3600)
        #expect(cdt.day == cst.day)
        // 23:30 CST on Oct 31 would be Nov 1 in UTC; the day stays the PC's
        let late = try #require(PCDate(rfc3339: "2026-10-31T23:30:00.000-05:00"))
        #expect(late.day == PCDay("2026-10-31"))
    }

    @Test func dayValidation() {
        #expect(PCDay("2026-10-03") != nil)
        #expect(PCDay("2026-1-3") == nil)
        #expect(PCDay("20261003xx") == nil)
        #expect(PCDay("2026-10-02")! < PCDay("2026-10-03")!)
    }
}

@Suite struct MediaPathTests {
    @Test func cacheKeyDropsSignatureOnly() {
        let a = MediaPath("/media/sightings/2026-10-03/x_crop.jpg?d=dev_a&exp=1&sig=AAA")
        let b = MediaPath("/media/sightings/2026-10-03/x_crop.jpg?d=dev_b&exp=999&sig=BBB")
        #expect(a.cacheKey == b.cacheKey)
        #expect(a.cacheKey == "/media/sightings/2026-10-03/x_crop.jpg")
        let w = MediaPath("/live.jpg?d=dev_a&exp=1&sig=A").adding("w", "1080")
        #expect(w.cacheKey == "/live.jpg?w=1080")
        #expect(w.rawValue.hasSuffix("&w=1080"))
    }

    @Test func resolvesAgainstBase() throws {
        let base = try #require(URL(string: "https://campi-pc.tail1234.ts.net"))
        let url = MediaPath("/media/clips/campi_2026-10-03_1410.mp4?d=x&exp=1&sig=s").url(relativeTo: base)
        #expect(url?.absoluteString == "https://campi-pc.tail1234.ts.net/media/clips/campi_2026-10-03_1410.mp4?d=x&exp=1&sig=s")
    }
}

@Suite struct PairingLinkTests {
    @Test func parsesQRLink() throws {
        let url = try #require(URL(string: "campi://pair?u=https%3A%2F%2Fcampi-pc.tail1234.ts.net&c=k3j9q2m8"))
        let link = try #require(PairingLink(url: url))
        #expect(link.baseURL.absoluteString == "https://campi-pc.tail1234.ts.net")
        #expect(link.code == "K3J9Q2M8")
        #expect(link.displayCode == "K3J9-Q2M8")
        #expect(!link.isInsecure)
    }

    @Test func manualEntryNormalizes() throws {
        let link = try #require(PairingLink(server: " campi-pc.tail1234.ts.net/ ", code: "k3j9-q2m8"))
        #expect(link.baseURL.absoluteString == "https://campi-pc.tail1234.ts.net")
        let mock = try #require(PairingLink(server: "http://127.0.0.1:8765", code: "K3J9 Q2M8"))
        #expect(mock.isInsecure)
    }

    @Test func rejectsBadInput() throws {
        #expect(PairingLink(server: "campi-pc", code: "SHORT") == nil)
        #expect(PairingLink(server: "campi-pc", code: "ILOUILOU") == nil)   // I, L, O, U aren't Crockford
        #expect(PairingLink(server: "ftp://campi-pc", code: "K3J9Q2M8") == nil)
        #expect(PairingLink(server: "", code: "K3J9Q2M8") == nil)
        #expect(PairingLink(url: try #require(URL(string: "campi://sighting/abc"))) == nil)
    }
}

@Suite struct HealthTests {
    func status(_ mutate: (inout [String: Any]) -> Void) throws -> Status {
        var obj = try JSONSerialization.jsonObject(with: Fixtures.data("status.json")) as! [String: Any]
        mutate(&obj)
        return try JSONDecoder.campi.decode(Status.self, from: JSONSerialization.data(withJSONObject: obj))
    }

    func at(_ s: Status) -> Date { s.serverTime.date }

    @Test func healthyFixtureIsOK() throws {
        let s = try status { _ in }
        #expect(s.health(now: at(s)).level == .ok)
        #expect(s.health(now: at(s)).headline == "All systems normal")
    }

    @Test func stoppedServiceIsAnError() throws {
        let s = try status { o in
            var svc = o["service"] as! [String: Any]
            svc["state"] = "stopped"; svc["disabled"] = true
            o["service"] = svc
        }
        let h = s.health(now: at(s))
        #expect(h.level == .error)
        #expect(h.headline.contains("campi stop"))
    }

    @Test func staleFramesAndCrashLoop() throws {
        let s = try status { o in
            var sg = o["sightings"] as! [String: Any]
            sg["state"] = "crash_looping"
            o["sightings"] = sg
        }
        let h = s.health(now: at(s).addingTimeInterval(600))
        #expect(h.level == .error)
        #expect(h.headline.hasPrefix("No frame saved"))
        #expect(h.issues.contains { $0.text.contains("crash-looping") && $0.level == .warning })
    }
}
