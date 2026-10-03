import Foundation
import Security

/// Secrets in the login keychain, as generic passwords under one service.
/// Only this app (by its code signature) can read them without a prompt, and
/// they stay out of preferences files, backups of them and diagnostics.
enum Keychain {
    static let service = "com.notchnest.local"

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

    private static func baseQuery(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }
}
