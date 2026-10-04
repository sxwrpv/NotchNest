import AppKit
import CryptoKit
import Security
import UserNotifications

/// Keeps NotchNest current from its GitHub releases.
///
/// Once a day (Settings → General → Check for updates automatically) it makes
/// one anonymous request to api.github.com for the latest release. Updating
/// downloads that release's zip, checks it against the SHA-256 GitHub
/// publishes for it, and swaps the app in place only if the new copy is
/// signed with the same certificate as this one. So a release someone else
/// managed to upload can't replace NotchNest, and the Microphone and
/// Accessibility grants carry over to the new version.
@MainActor
final class UpdateChecker: ObservableObject {
    enum Phase: Equatable {
        case idle
        case checking
        case upToDate
        case available(UpdateFeed.Release)
        case installing(UpdateFeed.Release)
        case failed(String)
    }

    @Published private(set) var phase: Phase = .idle
    @Published var checksAutomatically: Bool {
        didSet { UserDefaults.standard.set(checksAutomatically, forKey: Keys.automatic) }
    }
    /// Whether this copy can replace itself: signed with a real certificate
    /// (an ad-hoc build matches only itself) and allowed to write its folder.
    let canInstall = UpdateInstaller.canReplaceRunningApp

    private enum Keys {
        static let automatic = "checkForUpdates"
        static let lastCheck = "lastUpdateCheck"
        static let notified = "notifiedUpdateVersion"
    }
    private var timer: Timer?

    init() {
        checksAutomatically = UserDefaults.standard.object(forKey: Keys.automatic) as? Bool ?? true
    }

    var currentVersion: String { UpdateFeed.currentVersion }

    /// The newer release found by the last check, if any.
    var release: UpdateFeed.Release? {
        switch phase {
        case .available(let release), .installing(let release): return release
        default: return nil
        }
    }

    /// A minute after launch, then hourly; a check only goes out once the
    /// last one is most of a day old.
    func start() {
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 60 * 1_000_000_000)
            self?.checkIfDue()
        }
        timer = Timer.scheduledTimer(withTimeInterval: 3600, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.checkIfDue() }
        }
    }

    private func checkIfDue() {
        guard checksAutomatically else { return }
        let last = UserDefaults.standard.object(forKey: Keys.lastCheck) as? Date ?? .distantPast
        guard Date().timeIntervalSince(last) >= 22 * 3600 else { return }
        Task { await check(userInitiated: false) }
    }

    func check(userInitiated: Bool) async {
        switch phase {
        case .checking, .installing: return
        default: break
        }
        phase = .checking
        do {
            let latest = try await UpdateFeed.latest()
            UserDefaults.standard.set(Date(), forKey: Keys.lastCheck)
            if let latest, UpdateFeed.isNewer(latest.version, than: currentVersion) {
                phase = .available(latest)
                if !userInitiated { notifyOnce(latest) }
            } else {
                phase = .upToDate
            }
        } catch {
            fileLog("update: check failed: \(error.localizedDescription)")
            // A failed background check isn't worth an error on screen.
            phase = userInitiated ? .failed("Couldn't check for updates: \(error.localizedDescription)") : .idle
        }
    }

    /// Downloads, verifies and installs the available release, then relaunches.
    func install() {
        guard case .available(let release) = phase else { return }
        phase = .installing(release)
        Task {
            do {
                let staged = try await UpdateInstaller.downloadAndVerify(release)
                try UpdateInstaller.replaceRunningApp(with: staged)
                fileLog("update: installed \(release.version), relaunching")
                UpdateInstaller.relaunchWhenQuit()
                NSApp.terminate(nil)
            } catch {
                fileLog("update: install of \(release.version) failed: \(error.localizedDescription)")
                phase = .failed(error.localizedDescription)
            }
        }
    }

    func openReleasePage() {
        NSWorkspace.shared.open(release?.page ?? UpdateFeed.releasesPage)
    }

    private func notifyOnce(_ release: UpdateFeed.Release) {
        let defaults = UserDefaults.standard
        guard defaults.string(forKey: Keys.notified) != release.version else { return }
        defaults.set(release.version, forKey: Keys.notified)
        let content = UNMutableNotificationContent()
        content.title = "NotchNest \(release.version) is available"
        content.body = "Update from the NotchNest menu-bar icon or Settings → General."
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "update-\(release.version)", content: content, trigger: nil))
    }
}

/// What GitHub says the latest release is.
enum UpdateFeed {
    static let repository = "sxwrpv/NotchNest"
    static let releasesPage = URL(string: "https://github.com/\(repository)/releases/latest")!

    struct Release: Equatable {
        let version: String
        let page: URL
        let zip: URL
        /// The zip's SHA-256 as GitHub reports it (hex), if it does.
        let sha256: String?
        /// The release's `NotchNest-<version>.sha256` file, the fallback.
        let checksums: URL?
    }

    static var currentVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    }

    /// No cookies, no cache, nothing that identifies this Mac beyond the request itself.
    static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 30
        config.httpAdditionalHeaders = ["User-Agent": "NotchNest/\(currentVersion)"]
        return URLSession(configuration: config)
    }()

    private struct GitHubRelease: Decodable {
        let tag_name: String
        let html_url: URL
        let assets: [Asset]

        struct Asset: Decodable {
            let name: String
            let browser_download_url: URL
            let digest: String?
        }
    }

    /// The latest published release, or nil when it has no installable zip.
    static func latest() async throws -> Release? {
        var request = URLRequest(url: URL(string: "https://api.github.com/repos/\(repository)/releases/latest")!)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw UpdateError("GitHub answered \(status).") }
        let release = try JSONDecoder().decode(GitHubRelease.self, from: data)

        let version = release.tag_name.hasPrefix("v") ? String(release.tag_name.dropFirst()) : release.tag_name
        guard version.range(of: #"^[0-9]+(\.[0-9]+){1,3}$"#, options: .regularExpression) != nil,
              let zip = release.assets.first(where: { $0.name == "NotchNest-\(version).zip" }),
              isReleaseDownload(zip.browser_download_url, version: version, file: zip.name)
        else { return nil }
        let checksums = release.assets.first { $0.name == "NotchNest-\(version).sha256" }
        return Release(
            version: version,
            page: release.html_url,
            zip: zip.browser_download_url,
            sha256: zip.digest.flatMap { $0.hasPrefix("sha256:") ? String($0.dropFirst(7)).lowercased() : nil },
            checksums: checksums.flatMap {
                isReleaseDownload($0.browser_download_url, version: version, file: $0.name)
                    ? $0.browser_download_url : nil
            })
    }

    /// Only files attached to this repository's release for `version`.
    static func isReleaseDownload(_ url: URL, version: String, file: String) -> Bool {
        url.scheme == "https" && url.host == "github.com"
            && url.path == "/\(repository)/releases/download/v\(version)/\(file)"
    }

    /// Numeric comparison of dotted versions ("1.10.0" is newer than "1.9.2").
    static func isNewer(_ candidate: String, than current: String) -> Bool {
        let a = candidate.split(separator: ".").map { Int($0) ?? 0 }
        let b = current.split(separator: ".").map { Int($0) ?? 0 }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }
}

/// Downloads, verifies and swaps in a new NotchNest.
enum UpdateInstaller {
    static var canReplaceRunningApp: Bool {
        guard let requirement = ownRequirement(),
              let text = requirementText(requirement), !text.contains("cdhash")
        else { return false }
        let app = Bundle.main.bundleURL
        return FileManager.default.isWritableFile(atPath: app.deletingLastPathComponent().path)
            && FileManager.default.isWritableFile(atPath: app.path)
    }

    /// The release's app, unpacked next to the running one (same volume, so
    /// the swap is a rename) and verified.
    static func downloadAndVerify(_ release: UpdateFeed.Release) async throws -> URL {
        let (download, response) = try await UpdateFeed.session.download(from: release.zip)
        defer { try? FileManager.default.removeItem(at: download) }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw UpdateError("The download failed (HTTP \(status)).") }

        let expected = try await expectedSHA256(release)
        let actual = SHA256.hash(data: try Data(contentsOf: download, options: .mappedIfSafe))
            .map { String(format: "%02x", $0) }.joined()
        guard actual == expected else {
            throw UpdateError("The download didn't match its checksum. Try again later.")
        }

        let stage = try FileManager.default.url(
            for: .itemReplacementDirectory, in: .userDomainMask,
            appropriateFor: Bundle.main.bundleURL, create: true)
        try run("/usr/bin/ditto", "-x", "-k", download.path, stage.path)
        let app = stage.appendingPathComponent("NotchNest.app")
        try? run("/usr/bin/xattr", "-dr", "com.apple.quarantine", app.path)
        try verify(app, version: release.version)
        return app
    }

    private static func expectedSHA256(_ release: UpdateFeed.Release) async throws -> String {
        if let sha = release.sha256 { return sha }
        guard let url = release.checksums else { throw UpdateError("The release lists no checksum.") }
        let (data, _) = try await UpdateFeed.session.data(from: url)
        // shasum output: "<hex>  <file>" per line.
        let name = release.zip.lastPathComponent
        for line in String(decoding: data, as: UTF8.self).split(separator: "\n") {
            let fields = line.split(separator: " ", omittingEmptySubsequences: true)
            if fields.count == 2, fields[1] == name { return fields[0].lowercased() }
        }
        throw UpdateError("The release's checksum file doesn't list \(name).")
    }

    /// Same bundle id, the promised version, and a valid signature that meets
    /// this app's own designated requirement (identifier + signing certificate).
    static func verify(_ app: URL, version: String) throws {
        guard let bundle = Bundle(url: app),
              bundle.bundleIdentifier == Bundle.main.bundleIdentifier,
              bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String == version
        else { throw UpdateError("The download isn't NotchNest \(version).") }
        guard let requirement = ownRequirement() else {
            throw UpdateError("This copy of NotchNest has no signature to compare against.")
        }
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(app as CFURL, [], &code) == errSecSuccess, let code else {
            throw UpdateError("The downloaded app has no signature.")
        }
        let flags = SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate | kSecCSCheckNestedCode)
        let status = SecStaticCodeCheckValidity(code, flags, requirement)
        guard status == errSecSuccess else {
            throw UpdateError("The downloaded app isn't signed with NotchNest's certificate (\(status)).")
        }
    }

    static func replaceRunningApp(with staged: URL) throws {
        do {
            _ = try FileManager.default.replaceItemAt(Bundle.main.bundleURL, withItemAt: staged)
        } catch {
            throw UpdateError("Couldn't replace \(Bundle.main.bundleURL.path): \(error.localizedDescription)")
        }
    }

    /// Opens the (new) app once this process has exited.
    static func relaunchWhenQuit() {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", "while /bin/kill -0 \(getpid()) 2>/dev/null; do /bin/sleep 0.2; done; /usr/bin/open \"$0\"",
                          Bundle.main.bundleURL.path]
        try? task.run()
    }

    private static func ownRequirement() -> SecRequirement? {
        var code: SecCode?
        var staticCode: SecStaticCode?
        var requirement: SecRequirement?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code,
              SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess, let staticCode,
              SecCodeCopyDesignatedRequirement(staticCode, [], &requirement) == errSecSuccess
        else { return nil }
        return requirement
    }

    private static func requirementText(_ requirement: SecRequirement) -> String? {
        var text: CFString?
        guard SecRequirementCopyString(requirement, [], &text) == errSecSuccess else { return nil }
        return text as String?
    }

    private static func run(_ tool: String, _ arguments: String...) throws {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: tool)
        task.arguments = arguments
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        try task.run()
        task.waitUntilExit()
        guard task.terminationStatus == 0 else {
            throw UpdateError("\(URL(fileURLWithPath: tool).lastPathComponent) failed (\(task.terminationStatus)).")
        }
    }
}

struct UpdateError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
