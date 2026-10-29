import AppKit
// Live Dictation icon: microphone over a waveform on a deep blue-violet rounded square.
let size = 1024.0
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
let rect = NSRect(x: 80, y: 80, width: size-160, height: size-160)
let path = NSBezierPath(roundedRect: rect, xRadius: 190, yRadius: 190)
NSGradient(starting: NSColor(red: 0.36, green: 0.30, blue: 0.95, alpha: 1), ending: NSColor(red: 0.10, green: 0.12, blue: 0.42, alpha: 1))!.draw(in: path, angle: -90)
let wave = NSImage.SymbolConfiguration(pointSize: 300, weight: .semibold).applying(.init(paletteColors: [NSColor(white: 1, alpha: 0.28)]))
if let w = NSImage(systemSymbolName: "waveform", accessibilityDescription: nil)?.withSymbolConfiguration(wave) {
  let s = w.size; w.draw(in: NSRect(x: (size-s.width)/2, y: 210, width: s.width, height: s.height))
}
let mic = NSImage.SymbolConfiguration(pointSize: 400, weight: .medium).applying(.init(paletteColors: [NSColor.white]))
if let m = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: nil)?.withSymbolConfiguration(mic) {
  let s = m.size; m.draw(in: NSRect(x: (size-s.width)/2, y: (size-s.height)/2 + 40, width: s.width, height: s.height))
}
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
