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
    private func launchPaired() async throws -> XCUIApplication {
        let pairCode = try await Self.freshCode()
        let app = XCUIApplication()
        app.launchArguments = ["-resetPairing"]
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
