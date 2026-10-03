import XCTest

/// End-to-end smoke tests against the mock PC. Start it first:
///     python3 tools/mock_server.py
/// Skipped when the mock isn't running, so `xcodebuild test` still works without it.
final class SmokeTests: XCTestCase {
    static let mock = "http://127.0.0.1:8765"

    override func setUp() async throws {
        continueAfterFailure = false
        try await Self.requireMock()
    }

    static func requireMock() async throws {
        var req = URLRequest(url: URL(string: mock + "/api/status")!, timeoutInterval: 2)
        req.httpMethod = "GET"
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 401 else { throw XCTSkip("unexpected answer from \(mock)") }
        } catch let skip as XCTSkip {
            throw skip
        } catch {
            throw XCTSkip("mock PC not running at \(mock): python3 tools/mock_server.py")
        }
    }

    /// A fresh single-use pairing code from the mock (POST /mock/pair-code exists only on the mock).
    static func freshCode() async throws -> String {
        var req = URLRequest(url: URL(string: mock + "/mock/pair-code")!, timeoutInterval: 5)
        req.httpMethod = "POST"
        let (data, _) = try await URLSession.shared.data(for: req)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: String]
        return try XCTUnwrap(obj?["code"])
    }

    @MainActor
    func testPairManuallyThenTodayStatusSettingsUnpair() async throws {
        let pairCode = try await Self.freshCode()
        let app = XCUIApplication()
        app.launchArguments = ["-resetPairing"]
        app.launch()

        XCTAssertTrue(app.staticTexts["Pair with your PC"].waitForExistence(timeout: 10))
        attach(app, "1-pairing")

        let server = app.textFields["pair.server"]
        server.tap()
        server.typeText(Self.mock)
        let code = app.textFields["pair.code"]
        code.tap()
        code.typeText(pairCode)
        app.buttons["pair.submit"].tap()

        XCTAssertTrue(app.navigationBars["CAMPI-PC"].waitForExistence(timeout: 15), "Today didn't appear after pairing")
        XCTAssertTrue(app.buttons["today.status"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Latest sightings"].exists)
        sleep(2)   // let crops load for the screenshot
        attach(app, "2-today")

        app.buttons["today.status"].tap()
        XCTAssertTrue(app.navigationBars["Status"].waitForExistence(timeout: 5))
        attach(app, "3-status")
        app.navigationBars["Status"].buttons["Done"].tap()

        app.buttons["Settings"].tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 5))
        attach(app, "4-settings")
        app.buttons["Unpair this iPhone"].tap()
        app.buttons["Unpair"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Pair with your PC"].waitForExistence(timeout: 10), "didn't return to pairing")
    }

    @MainActor
    func testPairingDeepLinkRejectsWrongCode() throws {
        // ZZZZ9999 is never issued, so the PC must refuse it.
        let app = XCUIApplication()
        app.launchArguments = ["-resetPairing"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Pair with your PC"].waitForExistence(timeout: 10))

        let link = "campi://pair?u=\(Self.mock.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!)&c=ZZZZ9999"
        XCUIDevice.shared.system.open(URL(string: link)!)
        XCTAssertTrue(app.navigationBars["Pair this iPhone?"].waitForExistence(timeout: 10))
        attach(app, "5-deeplink-confirm")
        app.navigationBars["Pair this iPhone?"].buttons["Pair"].tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS 'wrong, already used, or expired'"))
            .firstMatch.waitForExistence(timeout: 10))
        attach(app, "6-deeplink-bad-code")
    }

    /// Launches fresh and pairs with the mock by typing a fresh code; ends on Today.
    @MainActor
    private func launchPaired(_ extraArguments: [String] = []) async throws -> XCUIApplication {
        let pairCode = try await Self.freshCode()
        let app = XCUIApplication()
        app.launchArguments = ["-resetPairing"] + extraArguments
        app.launch()
        XCTAssertTrue(app.textFields["pair.server"].waitForExistence(timeout: 10))
        app.textFields["pair.server"].tap()
        app.textFields["pair.server"].typeText(Self.mock)
        app.textFields["pair.code"].tap()
        app.textFields["pair.code"].typeText(pairCode)
        app.buttons["pair.submit"].tap()
        XCTAssertTrue(app.navigationBars["CAMPI-PC"].waitForExistence(timeout: 15), "pairing failed")
        return app
    }

    @MainActor
    func testSightingsDetailCollectionHighlights() async throws {
        let app = try await launchPaired()

        // Sightings grid -> detail
        app.tabBars.buttons["Sightings"].tap()
        let card = app.buttons.matching(identifier: "sightings.card").firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        sleep(1)
        attach(app, "07-sightings")
        card.tap()
        let star = app.buttons["detail.star"]
        XCTAssertTrue(star.waitForExistence(timeout: 10))
        let before = star.value as? String
        star.tap()
        XCTAssertTrue(waitFor(star, value: before == "starred" ? "not starred" : "starred"), "star didn't toggle")
        attach(app, "08-detail")
        star.tap()   // restore
        XCTAssertTrue(waitFor(star, value: before ?? "not starred"))

        // Label picker opens with the collection
        app.buttons["detail.correct"].tap()
        XCTAssertTrue(app.navigationBars["Correct Label"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Toyota Camry'")).firstMatch
            .waitForExistence(timeout: 10))
        attach(app, "09-label-picker")
        app.navigationBars["Correct Label"].buttons["Cancel"].tap()

        // View in timelapse: a player, or an explanation (e.g. the window isn't rendered yet)
        app.buttons["detail.timelapse"].tap()
        let done = app.buttons["player.done"]
        let alert = app.alerts.firstMatch
        XCTAssertTrue(waitForAny([done, alert], timeout: 15), "View in timelapse showed nothing")
        sleep(1)
        attach(app, "10-view-in-timelapse")
        if done.exists { done.tap() } else { alert.buttons["OK"].tap() }
        app.navigationBars.buttons.element(boundBy: 0).tap()   // back to the grid

        // Filters: starred only, then reset
        app.buttons["Filters"].tap()
        XCTAssertTrue(app.navigationBars["Filters"].waitForExistence(timeout: 5))
        app.switches["Starred only"].switches.firstMatch.tap()
        app.navigationBars["Filters"].buttons["Apply"].tap()
        sleep(2)
        attach(app, "11-starred-filter")
        app.buttons["Filters"].tap()
        app.buttons["Reset All"].tap()
        app.navigationBars["Filters"].buttons["Apply"].tap()

        // Collection -> one label's sightings
        app.tabBars.buttons["Collection"].tap()
        let tile = app.buttons.matching(identifier: "collection.tile").firstMatch
        XCTAssertTrue(tile.waitForExistence(timeout: 10))
        sleep(1)
        attach(app, "12-collection")
        tile.tap()
        XCTAssertTrue(app.buttons.matching(identifier: "sightings.card").firstMatch.waitForExistence(timeout: 10))
        sleep(1)
        attach(app, "13-collection-item")

        // Highlights -> daily video plays
        app.tabBars.buttons["Highlights"].tap()
        XCTAssertTrue(app.buttons.matching(identifier: "highlight.row").firstMatch.waitForExistence(timeout: 10))
        sleep(1)
        attach(app, "14-highlights")
        app.buttons["Daily video"].firstMatch.tap()
        let row = app.buttons.matching(identifier: "highlight.row").firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        XCTAssertTrue(done.waitForExistence(timeout: 15), "daily video didn't open")
        sleep(2)
        attach(app, "15-daily-player")
        done.tap()
    }

    @MainActor
    func testTimelapseSaveAndShare() async throws {
        // fallback if the simulator wasn't pre-granted (xcrun simctl privacy <sim> grant photos-add <bundle>)
        addUIInterruptionMonitor(withDescription: "Photos permission") { alert in
            for title in ["Allow Full Access", "Allow", "OK"] where alert.buttons[title].exists {
                alert.buttons[title].tap()
                return true
            }
            return false
        }
        let app = try await launchPaired()
        XCTAssertTrue(app.buttons["today.newestClip"].waitForExistence(timeout: 10), "no newest clip on Today")
        sleep(1)
        attach(app, "16-today-newest-clip")

        app.tabBars.buttons["Timelapse"].tap()
        let clip = app.buttons.matching(identifier: "timelapse.clip").firstMatch
        XCTAssertTrue(clip.waitForExistence(timeout: 10))
        sleep(1)
        attach(app, "17-clips")
        clip.tap()
        let done = app.buttons["player.done"]
        XCTAssertTrue(done.waitForExistence(timeout: 10))
        sleep(2)
        attach(app, "18-clip-player")
        done.tap()

        // Save to Photos from the context menu
        openMenu(on: clip, item: "Save to Photos", in: app).tap()
        XCTAssertTrue(app.staticTexts["Saved to Photos"].waitForExistence(timeout: 30), "save didn't finish")
        attach(app, "19-saved-to-photos")

        // Share sheet (after the "Saved" banner goes away)
        _ = app.staticTexts["Saved to Photos"].waitForNonExistence(timeout: 6)
        openMenu(on: clip, item: "Share…", in: app).tap()
        let sheet = app.otherElements["ActivityListView"]
        XCTAssertTrue(waitForAny([sheet, app.buttons["Copy"], app.cells["Copy"]], timeout: 20), "share sheet didn't open")
        sleep(1)
        attach(app, "20-share-sheet")
        if app.buttons["Close"].exists { app.buttons["Close"].tap() } else { app.swipeDown(velocity: .fast) }

        // Daily and archive lists
        XCTAssertTrue(app.buttons["Daily"].waitForExistence(timeout: 5))
        app.buttons["Daily"].tap()
        XCTAssertTrue(app.buttons.matching(identifier: "timelapse.daily").firstMatch.waitForExistence(timeout: 10))
        sleep(1)
        attach(app, "21-daily")
        app.buttons["Archive"].tap()
        XCTAssertTrue(app.buttons.matching(identifier: "timelapse.archive").firstMatch.waitForExistence(timeout: 10))
        attach(app, "22-archive")
    }

    /// Long-presses for the context menu and returns `item`, retrying the press once (it can miss while
    /// another animation is finishing).
    // MARK: Cardex (M4.5)

    /// Opens the first caught label's page and waits for its card to finish (generated or fallback).
    @MainActor
    private func openFirstCard(_ app: XCUIApplication) -> XCUIElement {
        app.tabBars.buttons["Collection"].tap()
        let tile = app.buttons.matching(identifier: "collection.tile").firstMatch
        XCTAssertTrue(tile.waitForExistence(timeout: 10))
        tile.tap()
        let card = app.descendants(matching: .any)["cardex.card"]
        XCTAssertTrue(card.waitForExistence(timeout: 10), "no Cardex card on the collection item page")
        let writing = app.staticTexts["Writing card…"]
        let done = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: writing)
        XCTAssertEqual(XCTWaiter.wait(for: [done], timeout: 90), .completed, "card never finished")
        return card
    }

    @MainActor
    func testCardexCardsOnCollectionAndDetail() async throws {
        let app = try await launchPaired()
        _ = openFirstCard(app)
        // Generated cards have rating rows ("Speed, 7 of 10"); fallback cards say why there's no text.
        let rating = app.descendants(matching: .any).matching(NSPredicate(format: "label ENDSWITH ' of 10'")).firstMatch
        let generated = rating.exists
        print("Cardex: \(generated ? "generated on device" : "fallback (Apple Intelligence unavailable here)")")
        XCTAssertTrue(generated || app.images["apple.intelligence"].exists
                      || app.staticTexts.containing(NSPredicate(format: "label CONTAINS 'Apple Intelligence'")).firstMatch.exists)
        attach(app, "27-cardex-collection-item")

        // The same card on one of that label's sightings.
        let sighting = app.buttons.matching(identifier: "sightings.card").firstMatch
        XCTAssertTrue(sighting.waitForExistence(timeout: 10))
        sighting.tap()
        XCTAssertTrue(app.buttons["detail.timelapse"].waitForExistence(timeout: 10))
        let detailCard = app.descendants(matching: .any)["cardex.card"]
        XCTAssertTrue(detailCard.waitForExistence(timeout: 15), "no Cardex card on the sighting detail")
        app.swipeUp()
        attach(app, "28-cardex-detail")

        // Regenerate from the ⋯ menu keeps a card on screen.
        app.buttons["More"].tap()
        app.buttons["Regenerate Card"].tap()
        XCTAssertTrue(detailCard.waitForExistence(timeout: 5))
    }

    @MainActor
    func testCardexFallbackWithoutAppleIntelligence() async throws {
        let app = try await launchPaired(["-cardexFallback"])
        _ = openFirstCard(app)
        XCTAssertTrue(app.staticTexts["Card text is turned off for this test run."].waitForExistence(timeout: 5))
        XCTAssertFalse(app.descendants(matching: .any).matching(NSPredicate(format: "label ENDSWITH ' of 10'")).firstMatch.exists)
        attach(app, "29-cardex-fallback")
    }

    // MARK: Live (M4)

    struct LiveCounters: Decodable { var state: String; var viewers: Int; var opened: Int; var closed: Int }

    /// Mock-only `/mock/live`: reads the viewer counters; with `state`, changes how live answers.
    @discardableResult
    static func mockLive(state: String? = nil, rotation: Int? = nil) async throws -> LiveCounters {
        var req = URLRequest(url: URL(string: mock + "/mock/live")!, timeoutInterval: 5)
        if state != nil || rotation != nil {
            var body: [String: Any] = [:]
            body["state"] = state
            body["rotation"] = rotation
            req.httpMethod = "POST"
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, _) = try await URLSession.shared.data(for: req)
        return try JSONDecoder().decode(LiveCounters.self, from: data)
    }

    /// Polls the mock until `check` holds for its live counters.
    @MainActor
    func waitForLive(_ what: String, timeout: TimeInterval = 10,
                     _ check: @Sendable (LiveCounters) -> Bool) async throws {
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            if check(try await Self.mockLive()) { return }
            try await Task.sleep(for: .milliseconds(250))
        }
        XCTFail("mock live counters never showed: \(what) (now \(try await Self.mockLive()))")
    }

    @MainActor
    func testLiveVideoSnapshotsErrorsAndDisconnect() async throws {
        try await Self.mockLive(state: "ok", rotation: 90)
        addTeardownBlock { _ = try? await Self.mockLive(state: "ok", rotation: 0) }
        let app = try await launchPaired()

        let tile = app.buttons["today.live"]
        XCTAssertTrue(tile.waitForExistence(timeout: 10))
        tile.tap()
        let image = app.images["live.image"]
        XCTAssertTrue(image.waitForExistence(timeout: 15), "no live frame")
        try await waitForLive("one viewer") { $0.viewers == 1 }
        sleep(1)
        // rotation 90: the landscape MJPEG frames are shown portrait
        XCTAssertGreaterThan(image.frame.height, image.frame.width, "video frame not rotated")
        attach(app, "23-live-video")

        // Leaving the foreground closes the stream (and the PC's upstream); coming back reopens it.
        let before = try await Self.mockLive()
        XCUIDevice.shared.press(.home)
        try await waitForLive("stream closed after backgrounding", timeout: 6) { $0.viewers == 0 && $0.closed > before.closed }
        app.activate()
        try await waitForLive("stream reopened", timeout: 15) { $0.viewers == 1 }

        // Snapshots: no MJPEG viewer, a frame time instead of fps.
        app.buttons["Snapshots"].tap()
        try await waitForLive("video closed for snapshots") { $0.viewers == 0 }
        let status = app.descendants(matching: .any)["live.status"]
        XCTAssertTrue(waitForAny([app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'Frame from'")).firstMatch],
                                 timeout: 10), "snapshot frame time never shown")
        XCTAssertTrue(status.exists)
        attach(app, "24-live-snapshots")

        // 503 live_busy, then "Show Snapshots Instead".
        try await Self.mockLive(state: "busy")
        app.buttons["Video"].tap()
        XCTAssertTrue(app.staticTexts["Live is busy"].waitForExistence(timeout: 10))
        attach(app, "25-live-busy")
        app.buttons["Show Snapshots Instead"].tap()
        XCTAssertTrue(waitForAny([app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'Frame from'")).firstMatch],
                                 timeout: 10))

        // 502 pi_unreachable.
        try await Self.mockLive(state: "unreachable")
        app.buttons["Video"].tap()
        XCTAssertTrue(app.staticTexts["The camera isn't answering"].waitForExistence(timeout: 10))
        attach(app, "26-live-pi-unreachable")

        try await Self.mockLive(state: "ok")
        app.buttons["live.close"].tap()
        try await waitForLive("stream closed on dismiss") { $0.viewers == 0 }
        XCTAssertTrue(tile.waitForExistence(timeout: 5))
    }

    @MainActor
    private func openMenu(on el: XCUIElement, item: String, in app: XCUIApplication) -> XCUIElement {
        for _ in 0..<2 {
            el.press(forDuration: 1.2)
            if app.buttons[item].waitForExistence(timeout: 3) { return app.buttons[item] }
            app.navigationBars.firstMatch.tap()   // dismiss whatever came up, without hitting a row
        }
        XCTFail("context menu item \(item) never appeared")
        return app.buttons[item]
    }

    @MainActor
    private func waitFor(_ el: XCUIElement, value: String, timeout: TimeInterval = 10) -> Bool {
        let exp = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", value), object: el)
        return XCTWaiter.wait(for: [exp], timeout: timeout) == .completed
    }

    @MainActor
    private func waitForAny(_ els: [XCUIElement], timeout: TimeInterval) -> Bool {
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            if els.contains(where: \.exists) { return true }
            usleep(250_000)
        }
        return false
    }

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }
}
