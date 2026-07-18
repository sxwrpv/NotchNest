import Foundation
import AppKit
import ApplicationServices
import Network
import CryptoKit
import Combine

/// Spotify Web API client backing the Like button. AppleScript can control
/// playback but cannot touch the user's library, so saving to Liked Songs goes
/// through the Web API with OAuth 2.0 Authorization Code + PKCE (no client
/// secret): we open the browser and catch the redirect on a loopback listener.
///
/// One-time setup (personal use): create an app at developer.spotify.com,
/// add redirect URI http://127.0.0.1:27381/callback, paste its Client ID into
/// NotchNest's Settings, click Connect.
@MainActor
final class SpotifyService: ObservableObject {
    enum ConnectionState: Equatable {
        case disconnected, connecting, connected
    }

    @Published private(set) var connection: ConnectionState = .disconnected
    /// Liked-state per Spotify track URI. nil = unknown / not fetched yet.
    @Published private(set) var likedCache: [String: Bool] = [:]
    @Published private(set) var lastError: String?
    /// Spotify's Web API rejects free-tier developer apps ("Premium required",
    /// policy since 2025). Once we see that 403 we stop calling the API and
    /// drive the Spotify desktop app directly instead (menu bar / ⌥⇧B).
    @Published private(set) var webAPIBlocked = false

    static let redirectPort: UInt16 = 27381
    static var redirectURI: String { "http://127.0.0.1:\(redirectPort)/callback" }

    private let settings: SettingsStore
    private let defaults = UserDefaults.standard
    private var listener: NWListener?
    private var pendingVerifier: String?
    private var pendingState: String?
    private var timeoutWork: DispatchWorkItem?

    private enum Keys {
        static let access = "spotifyAccessToken"
        static let refresh = "spotifyRefreshToken"
        static let expiry = "spotifyTokenExpiry"
        static let webBlocked = "spotifyWebAPIBlocked"
    }

    init(settings: SettingsStore) {
        self.settings = settings
        if UserDefaults.standard.string(forKey: Keys.refresh) != nil {
            connection = .connected
        }
        webAPIBlocked = UserDefaults.standard.bool(forKey: Keys.webBlocked)
    }

    private var clientID: String {
        settings.spotifyClientID.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Public API

    func isLikeable(_ trackID: String?) -> Bool {
        trackID?.hasPrefix("spotify:track:") == true
    }

    /// Fetches (once per track) whether the current track is already saved.
    /// Only possible over the Web API; the local fallback can't query state.
    func refreshLikeStatus(for trackID: String) async {
        guard connection == .connected, !webAPIBlocked, isLikeable(trackID),
              likedCache[trackID] == nil else { return }
        let id = bareID(trackID)
        guard let (data, status) = await apiRequest(
            "GET", "https://api.spotify.com/v1/me/tracks/contains?ids=\(id)") else { return }
        if status == 403 { markWebAPIBlocked(); return }
        guard status == 200,
              let flags = try? JSONSerialization.jsonObject(with: data) as? [Bool],
              let liked = flags.first else { return }
        likedCache[trackID] = liked
    }

    /// Adds/removes the track from Liked Songs. Prefers the Web API (real state,
    /// works in background); falls back to driving the Spotify app directly when
    /// the API is unavailable (free-tier 403) or not connected.
    func toggleLike(for key: String) async {
        if connection == .connected, !webAPIBlocked, isLikeable(key) {
            let target = !(likedCache[key] ?? false)
            let method = target ? "PUT" : "DELETE"
            if let (_, status) = await apiRequest(
                "\(method)", "https://api.spotify.com/v1/me/tracks?ids=\(bareID(key))") {
                if (200...299).contains(status) {
                    likedCache[key] = target
                    lastError = nil
                    return
                }
                if status != 403 {
                    lastError = "Spotify API error (\(status))."
                    return
                }
                markWebAPIBlocked()   // Premium-gated → use local control from now on
            } else {
                lastError = "Couldn't reach the Spotify API."
                return
            }
        }
        localToggleLike(key: key)
    }

    private func markWebAPIBlocked() {
        webAPIBlocked = true
        defaults.set(true, forKey: Keys.webBlocked)
    }

    private func bareID(_ trackID: String) -> String {
        trackID.components(separatedBy: ":").last ?? trackID
    }

    // MARK: - Local fallback (no Premium needed)

    /// Likes the current track by driving the Spotify desktop app itself.
    /// Strategy 1: find a "Save to Your Library / Liked Songs" item in Spotify's
    /// native menu bar and press it via Accessibility — works in the background
    /// and its wording even tells us the current liked-state.
    /// Strategy 2: post Spotify's own Like shortcut (⌥⇧B) straight to its
    /// process, so Spotify never has to come to the front.
    private func localToggleLike(key: String) {
        let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        guard AXIsProcessTrustedWithOptions([promptKey: true] as CFDictionary) else {
            lastError = "Allow NotchNest in System Settings → Privacy & Security → Accessibility, then tap ♥ again."
            return
        }
        guard let app = NSRunningApplication
            .runningApplications(withBundleIdentifier: "com.spotify.client").first else {
            lastError = "Spotify isn't running."
            return
        }
        let pid = app.processIdentifier

        if let (item, willLike) = findLikeMenuItem(pid: pid),
           AXUIElementPerformAction(item, kAXPressAction as CFString) == .success {
            NSLog("NotchNest like: pressed Spotify menu item (nowLiked=\(willLike))")
            likedCache[key] = willLike
            lastError = nil
            return
        }
        NSLog("NotchNest like: no menu item — using ⌥⇧B keystroke")

        // Spotify's CEF layer ignores background-posted key events, so use the
        // same pattern the dictation engine's injector uses: focus Spotify,
        // post its Like shortcut (⌥⇧B), restore focus. macOS activation is
        // cooperative and not instant, so wait until Spotify is actually front.
        let previous = NSWorkspace.shared.frontmostApplication
        app.activate()
        waitUntilFrontmost(app, attempts: 14) { [weak self] focused in
            guard let self else { return }
            guard focused else {
                NSLog("NotchNest like: Spotify never became frontmost")
                self.lastError = "Couldn't focus Spotify to send the Like shortcut."
                return
            }
            Self.postLikeShortcut()
            NSLog("NotchNest like: sent ⌥⇧B to Spotify")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                if let previous, previous.bundleIdentifier != "com.spotify.client" {
                    previous.activate()
                }
            }
            self.likedCache[key] = !(self.likedCache[key] ?? false)
            self.lastError = nil
        }
    }

    /// Polls until `app` owns the foreground (or attempts run out); 0.1s steps.
    private func waitUntilFrontmost(_ app: NSRunningApplication, attempts: Int,
                                    completion: @escaping (Bool) -> Void) {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier {
            completion(true)
            return
        }
        guard attempts > 0 else {
            completion(false)
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            guard let self else { return }
            self.waitUntilFrontmost(app, attempts: attempts - 1, completion: completion)
        }
    }

    private static func postLikeShortcut() {
        let source = CGEventSource(stateID: .combinedSessionState)
        let keyB: CGKeyCode = 11  // kVK_ANSI_B
        for down in [true, false] {
            guard let event = CGEvent(keyboardEventSource: source,
                                      virtualKey: keyB, keyDown: down) else { continue }
            event.flags = [.maskAlternate, .maskShift]
            event.post(tap: .cghidEventTap)
        }
    }

    private var loggedMenuDump = false

    /// Scans Spotify's native menu bar for a like/save item. Returns the item
    /// and whether pressing it will LIKE (true) or UNLIKE (false) the track.
    /// Logs the full menu tree once per run so we can see what Spotify offers.
    private func findLikeMenuItem(pid: pid_t) -> (AXUIElement, Bool)? {
        let appElement = AXUIElementCreateApplication(pid)
        var barRef: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            appElement, kAXMenuBarAttribute as CFString, &barRef) == .success,
            let bar = barRef, CFGetTypeID(bar) == AXUIElementGetTypeID() else { return nil }

        var found: (AXUIElement, Bool)?
        for barItem in axChildren(bar as! AXUIElement) {
            let menuName = axTitle(barItem) ?? "?"
            var titles: [String] = []
            for menu in axChildren(barItem) {
                for item in axChildren(menu) {
                    guard let rawTitle = axTitle(item), !rawTitle.isEmpty else { continue }
                    titles.append(rawTitle)
                    let title = rawTitle.lowercased()
                    guard found == nil,
                          title.contains("liked songs") || title.contains("your library")
                    else { continue }
                    if title.contains("save") || title.contains("add") {
                        found = (item, true)
                    } else if title.contains("remove") || title.contains("delete") {
                        found = (item, false)
                    }
                }
            }
            if !loggedMenuDump {
                NSLog("NotchNest spotify-menu [%@]: %@", menuName, titles.joined(separator: " | "))
            }
        }
        loggedMenuDump = true
        return found
    }

    private func axChildren(_ element: AXUIElement) -> [AXUIElement] {
        var ref: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            element, kAXChildrenAttribute as CFString, &ref) == .success,
            let array = ref as? [AXUIElement] else { return [] }
        return array
    }

    private func axTitle(_ element: AXUIElement) -> String? {
        var ref: CFTypeRef?
        guard AXUIElementCopyAttributeValue(
            element, kAXTitleAttribute as CFString, &ref) == .success else { return nil }
        return ref as? String
    }

    // MARK: - OAuth (Authorization Code + PKCE)

    func connect() {
        lastError = nil
        // A fresh connect is the "try the Web API again" gesture (e.g. after
        // upgrading to Premium), so clear the blocked flag.
        webAPIBlocked = false
        defaults.removeObject(forKey: Keys.webBlocked)
        guard !clientID.isEmpty else {
            lastError = "Enter your Spotify app's Client ID first."
            return
        }
        let verifier = Self.randomURLSafe(bytes: 64)
        let state = Self.randomURLSafe(bytes: 24)
        pendingVerifier = verifier
        pendingState = state

        do { try startListener() } catch {
            lastError = "Couldn't listen on port \(Self.redirectPort): \(error.localizedDescription)"
            return
        }

        var comps = URLComponents(string: "https://accounts.spotify.com/authorize")!
        comps.queryItems = [
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "redirect_uri", value: Self.redirectURI),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "code_challenge", value: Self.challenge(for: verifier)),
            URLQueryItem(name: "scope", value: "user-library-read user-library-modify"),
            URLQueryItem(name: "state", value: state),
        ]
        connection = .connecting
        NSWorkspace.shared.open(comps.url!)

        // Give up after 5 minutes if the user abandons the browser flow.
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.connection == .connecting else { return }
            self.failConnect("Authorization timed out.")
        }
        timeoutWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 300, execute: work)
    }

    func disconnect() {
        defaults.removeObject(forKey: Keys.access)
        defaults.removeObject(forKey: Keys.refresh)
        defaults.removeObject(forKey: Keys.expiry)
        likedCache = [:]
        stopListener()
        connection = .disconnected
    }

    private func failConnect(_ message: String) {
        stopListener()
        pendingVerifier = nil
        pendingState = nil
        connection = defaults.string(forKey: Keys.refresh) != nil ? .connected : .disconnected
        lastError = message
    }

    // MARK: - Loopback redirect listener

    private func startListener() throws {
        stopListener()
        let l = try NWListener(using: .tcp, on: NWEndpoint.Port(rawValue: Self.redirectPort)!)
        l.newConnectionHandler = { [weak self] conn in
            conn.start(queue: .main)
            conn.receive(minimumIncompleteLength: 1, maximumLength: 16384) { data, _, _, _ in
                Task { @MainActor [weak self] in
                    self?.handleHTTPRequest(data, on: conn)
                }
            }
        }
        l.start(queue: .main)
        listener = l
    }

    private func stopListener() {
        timeoutWork?.cancel()
        timeoutWork = nil
        listener?.cancel()
        listener = nil
    }

    private func handleHTTPRequest(_ data: Data?, on conn: NWConnection) {
        guard let data, let text = String(data: data, encoding: .utf8),
              let firstLine = text.components(separatedBy: "\r\n").first else {
            conn.cancel(); return
        }
        let parts = firstLine.split(separator: " ")
        guard parts.count >= 2 else { conn.cancel(); return }
        let target = String(parts[1])

        // Ignore stray requests (favicon etc.) without tearing the flow down.
        guard target.hasPrefix("/callback") else {
            respond(conn, status: "404 Not Found", html: "")
            return
        }

        let query = URLComponents(string: "http://127.0.0.1\(target)")?.queryItems ?? []
        func value(_ name: String) -> String? { query.first { $0.name == name }?.value }

        if let error = value("error") {
            respond(conn, status: "200 OK", html: resultPage(ok: false))
            failConnect(error == "access_denied" ? "Authorization was denied." : "Spotify error: \(error)")
            return
        }
        guard let code = value("code"), value("state") == pendingState else {
            respond(conn, status: "200 OK", html: resultPage(ok: false))
            failConnect("Authorization state mismatch — try again.")
            return
        }

        respond(conn, status: "200 OK", html: resultPage(ok: true))
        stopListener()
        Task { await self.exchange(code: code) }
    }

    private func respond(_ conn: NWConnection, status: String, html: String) {
        let body = Data(html.utf8)
        let head = "HTTP/1.1 \(status)\r\nContent-Type: text/html; charset=utf-8\r\n" +
                   "Content-Length: \(body.count)\r\nConnection: close\r\n\r\n"
        conn.send(content: Data(head.utf8) + body, completion: .contentProcessed { _ in
            conn.cancel()
        })
    }

    private func resultPage(ok: Bool) -> String {
        """
        <html><body style="font-family:-apple-system;background:#111;color:#eee;\
        display:flex;align-items:center;justify-content:center;height:100vh">
        <div style="text-align:center"><h2>\(ok ? "✓ Spotify connected" : "✗ Authorization failed")</h2>
        <p>\(ok ? "You can close this tab and go back to NotchNest." : "Close this tab and try again from NotchNest.")</p></div>
        </body></html>
        """
    }

    // MARK: - Token handling

    private func exchange(code: String) async {
        guard let verifier = pendingVerifier else { return }
        pendingVerifier = nil
        pendingState = nil
        let result = await tokenRequest([
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": Self.redirectURI,
            "client_id": clientID,
            "code_verifier": verifier,
        ])
        guard let result else {
            failConnect("Token exchange failed — check the Client ID and redirect URI.")
            return
        }
        store(result)
        connection = .connected
        lastError = nil
    }

    private func refreshAccessToken() async -> String? {
        guard let refresh = defaults.string(forKey: Keys.refresh) else { return nil }
        let result = await tokenRequest([
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": clientID,
        ])
        guard let result else {
            disconnect()
            lastError = "Spotify session expired — connect again."
            return nil
        }
        store(result)
        return result.access
    }

    private func validAccessToken() async -> String? {
        if let token = defaults.string(forKey: Keys.access),
           Date().timeIntervalSince1970 < defaults.double(forKey: Keys.expiry) - 60 {
            return token
        }
        return await refreshAccessToken()
    }

    private struct TokenSet {
        var access: String
        var refresh: String?
        var expiresIn: Double
    }

    private func store(_ tokens: TokenSet) {
        defaults.set(tokens.access, forKey: Keys.access)
        defaults.set(Date().timeIntervalSince1970 + tokens.expiresIn, forKey: Keys.expiry)
        if let refresh = tokens.refresh {
            defaults.set(refresh, forKey: Keys.refresh)
        }
    }

    private func tokenRequest(_ params: [String: String]) async -> TokenSet? {
        var req = URLRequest(url: URL(string: "https://accounts.spotify.com/api/token")!)
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Self.formBody(params)
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let access = obj["access_token"] as? String,
              let expires = obj["expires_in"] as? Double else { return nil }
        return TokenSet(access: access,
                        refresh: obj["refresh_token"] as? String,
                        expiresIn: expires)
    }

    private func apiRequest(_ method: String, _ urlString: String) async -> (Data, Int)? {
        guard let token = await validAccessToken(), let url = URL(string: urlString) else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        guard let (data, resp) = try? await URLSession.shared.data(for: req) else { return nil }
        return (data, (resp as? HTTPURLResponse)?.statusCode ?? 0)
    }

    // MARK: - PKCE helpers

    private static func randomURLSafe(bytes count: Int) -> String {
        var bytes = [UInt8](repeating: 0, count: count)
        _ = SecRandomCopyBytes(kSecRandomDefault, count, &bytes)
        return Data(bytes).base64URLEncoded
    }

    private static func challenge(for verifier: String) -> String {
        Data(SHA256.hash(data: Data(verifier.utf8))).base64URLEncoded
    }

    private static func formBody(_ params: [String: String]) -> Data {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        let pairs = params.map { key, value in
            "\(key)=\(value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value)"
        }
        return Data(pairs.joined(separator: "&").utf8)
    }
}

private extension Data {
    var base64URLEncoded: String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
