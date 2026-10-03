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

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }
}
