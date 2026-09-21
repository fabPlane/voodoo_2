import AppKit
import Foundation

final class DebugConsoleController: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var textView: NSTextView!
    private var summary: NSTextField!
    private var startButton: NSButton!
    private var stopButton: NSButton!
    private var process: Process?
    private var partialLine = ""

    private var monitorPath: String {
        if let index = CommandLine.arguments.firstIndex(of: "--monitor"),
           index + 1 < CommandLine.arguments.count {
            return CommandLine.arguments[index + 1]
        }
        return "run_debug_uart_monitor.sh"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let content = NSView()
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 980, height: 640),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Voodoo Tang Console Diagnostics"
        window.center()
        window.contentView = content

        summary = NSTextField(labelWithString: "Waiting for V2DBG telemetry")
        summary.font = .monospacedSystemFont(ofSize: 13, weight: .medium)
        summary.textColor = .systemOrange
        summary.translatesAutoresizingMaskIntoConstraints = false

        startButton = NSButton(title: "Start monitor", target: self, action: #selector(startMonitor))
        stopButton = NSButton(title: "Stop", target: self, action: #selector(stopMonitor))
        stopButton.isEnabled = false
        let clearButton = NSButton(title: "Clear", target: self, action: #selector(clearLog))
        let copyButton = NSButton(title: "Copy latest", target: self, action: #selector(copyLatest))
        let buttons = NSStackView(views: [startButton, stopButton, clearButton, copyButton])
        buttons.orientation = .horizontal
        buttons.spacing = 8
        buttons.translatesAutoresizingMaskIntoConstraints = false

        textView = NSTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.backgroundColor = NSColor(calibratedWhite: 0.06, alpha: 1)
        textView.textColor = NSColor(calibratedRed: 1.0, green: 0.88, blue: 0.25, alpha: 1)
        let scroll = NSScrollView()
        scroll.documentView = textView
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.borderType = .bezelBorder
        scroll.translatesAutoresizingMaskIntoConstraints = false

        let help = NSTextField(wrappingLabelWithString:
            "Hold the case MODE button while loading the Voodoo core. Alternating LEDs and DBG=1 confirm isolated diagnostics. The core is not halted; reloading it without MODE restores normal TangCore/BL616 control.")
        help.textColor = .secondaryLabelColor
        help.translatesAutoresizingMaskIntoConstraints = false

        content.addSubview(summary)
        content.addSubview(buttons)
        content.addSubview(scroll)
        content.addSubview(help)
        NSLayoutConstraint.activate([
            summary.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            summary.topAnchor.constraint(equalTo: content.topAnchor, constant: 16),
            buttons.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),
            buttons.centerYAnchor.constraint(equalTo: summary.centerYAnchor),
            summary.trailingAnchor.constraint(lessThanOrEqualTo: buttons.leadingAnchor, constant: -12),
            scroll.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            scroll.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),
            scroll.topAnchor.constraint(equalTo: summary.bottomAnchor, constant: 14),
            help.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            help.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),
            help.topAnchor.constraint(equalTo: scroll.bottomAnchor, constant: 10),
            help.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -14),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 400)
        ])

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startMonitor()
    }

    private func append(_ text: String) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.textView.textStorage?.append(NSAttributedString(
                string: text,
                attributes: [.foregroundColor: self.textView.textColor ?? .textColor,
                             .font: self.textView.font ?? .monospacedSystemFont(ofSize: 12, weight: .regular)]
            ))
            self.textView.scrollToEndOfDocument(nil)
            self.partialLine += text
            let lines = self.partialLine.components(separatedBy: "\n")
            self.partialLine = lines.last ?? ""
            for line in lines.dropLast()
                where line.hasPrefix("V2DBG ") || line.hasPrefix("V2BOOT ") {
                self.updateSummary(line)
            }
        }
    }

    private func updateSummary(_ line: String) {
        var fields: [String: String] = [:]
        for token in line.split(separator: " ") {
            let pair = token.split(separator: "=", maxSplits: 1)
            if pair.count == 2 { fields[String(pair[0])] = String(pair[1]) }
        }
        let names = ["dbg", "cal", "wed", "usb", "pl", "cur", "key", "hid", "uart", "rx", "hc"]
        summary.stringValue = names.compactMap { key in fields[key].map { "\(key.uppercased())=\($0)" } }.joined(separator: "  ")
        summary.textColor = fields["wed"] == "1" ? .systemRed : .systemGreen
    }

    @objc private func startMonitor() {
        guard process == nil else { return }
        let task = Process()
        let output = Pipe()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = [monitorPath]
        task.standardOutput = output
        task.standardError = output
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if !data.isEmpty { self?.append(String(decoding: data, as: UTF8.self)) }
        }
        task.terminationHandler = { [weak self] ended in
            output.fileHandleForReading.readabilityHandler = nil
            self?.append("\n[monitor exited: \(ended.terminationStatus)]\n")
            DispatchQueue.main.async {
                self?.process = nil
                self?.startButton.isEnabled = true
                self?.stopButton.isEnabled = false
            }
        }
        do {
            try task.run()
            process = task
            startButton.isEnabled = false
            stopButton.isEnabled = true
        } catch {
            append("Could not start monitor: \(error)\n")
        }
    }

    @objc private func stopMonitor() {
        process?.interrupt()
    }

    @objc private func clearLog() {
        textView.string = ""
        partialLine = ""
    }

    @objc private func copyLatest() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(summary.stringValue, forType: .string)
    }

    func applicationWillTerminate(_ notification: Notification) {
        process?.terminate()
    }
}

let application = NSApplication.shared
let controller = DebugConsoleController()
application.delegate = controller
application.setActivationPolicy(.regular)
application.run()
