// Live Dictation launcher: runs dictation.py as a CHILD process (not exec), so macOS
// treats this app as responsible and Microphone / Input Monitoring / Accessibility are granted to
// "Live Dictation" instead of to python3.12. Restarts the script if it exits unexpectedly.
import Foundation

// build_app.sh fills in the project folder and the Python interpreter.
let dir = "__PROJECT_DIR__"
let logPath = dir + "/dictation.log"
if !FileManager.default.fileExists(atPath: logPath) { FileManager.default.createFile(atPath: logPath, contents: nil) }

var failures = 0
while true {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "__PYTHON__")
    p.arguments = ["dictation.py"]
    p.currentDirectoryURL = URL(fileURLWithPath: dir)
    if let log = FileHandle(forWritingAtPath: logPath) {
        log.seekToEndOfFile(); p.standardOutput = log; p.standardError = log
    }
    let started = Date()
    do { try p.run() } catch { exit(1) }
    p.waitUntilExit()
    if p.terminationStatus == 0 { exit(0) }                        // normal quit
    failures = Date().timeIntervalSince(started) < 30 ? failures + 1 : 0
    if failures >= 3 { exit(p.terminationStatus) }                 // e.g. missing permission: stop retrying
    Thread.sleep(forTimeInterval: 3)
}
