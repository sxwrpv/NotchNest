import Foundation
import Security

/// Secrets in the login keychain, as generic passwords under one service,
/// kept out of preferences files, their backups and diagnostics.
///
/// Reading or changing a secret can block until the user approves: without
/// an Apple Team ID, macOS ties an item to the exact build that created it
/// (its cdhash), so every update has to be allowed once. Never call `string`,
/// `set` or `delete` on the main thread; `exists` needs no approval.
enum Keychain {
    static let service = "com.notchnest.local"

    /// Whether the item is there, from its attributes alone: no decryption,
    /// so no approval prompt.
    static func exists(_ account: String) -> Bool {
        var query = baseQuery(account)
        query[kSecReturnAttributes as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        return SecItemCopyMatching(query as CFDictionary, nil) == errSecSuccess
    }

    static func string(for account: String) -> String? {
        var query = baseQuery(account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func set(_ value: String, for account: String) -> Bool {
        let data = Data(value.utf8)
        let update = [kSecValueData as String: data] as CFDictionary
        let status = SecItemUpdate(baseQuery(account) as CFDictionary, update)
        if status == errSecSuccess { return true }
        guard status == errSecItemNotFound else {
            fileLog("keychain: update \(account) failed (\(status))")
            return false
        }
        var add = baseQuery(account)
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        add[kSecAttrLabel as String] = "NotchNest (\(account))"
        let added = SecItemAdd(add as CFDictionary, nil)
        if added != errSecSuccess { fileLog("keychain: add \(account) failed (\(added))") }
        return added == errSecSuccess
    }

    static func delete(_ account: String) {
        SecItemDelete(baseQuery(account) as CFDictionary)
    }

    /// Every item NotchNest stored (uninstall).
    static func deleteAll() {
        let query = [kSecClass as String: kSecClassGenericPassword,
                     kSecAttrService as String: service] as CFDictionary
        for _ in 0..<16 where SecItemDelete(query) == errSecSuccess {}
    }

    private static func baseQuery(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }
}
