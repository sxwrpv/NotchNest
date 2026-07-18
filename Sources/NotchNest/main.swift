import AppKit

// NotchNest — a local, editable notch utility. AppKit-driven so we fully control
// the floating overlay panel; SwiftUI is hosted inside it.
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // menu-bar / notch app, no Dock icon
app.run()
