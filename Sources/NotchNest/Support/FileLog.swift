import Foundation

/// Appends a line to ~/Library/Logs/NotchNest.log. The unified log redacts
/// dynamic NSLog strings as <private>, so paths we need to debug log here.
func fileLog(_ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    let line = "\(stamp) \(message)\n"
    let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/NotchNest.log")
    if let handle = FileHandle(forWritingAtPath: url.path) {
        handle.seekToEndOfFile()
        handle.write(Data(line.utf8))
        try? handle.close()
    } else {
        try? line.write(to: url, atomically: true, encoding: .utf8)
    }
}
