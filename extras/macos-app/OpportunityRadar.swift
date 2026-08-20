import AppKit
import CoreFoundation
import Foundation
import WebKit

private let bridgeName = "opportunityRadar"
private let themeDefaultsKey = "OpportunityRadarTheme"
private let maximumProfilePayloadBytes = 256 * 1024
private let maximumCapturedOutputBytes = 32 * 1024
private let commandIOWaitSeconds = 2.0
private let terminationDeferralSeconds = 30.0

private enum AppConfigurationError: LocalizedError {
    case unavailable

    var errorDescription: String? {
        "The private Opportunity Radar runtime is unavailable. Reinstall the optional macOS app."
    }
}

private func ownerControls(_ url: URL) -> Bool {
    guard
        let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
        let owner = (attributes[.ownerAccountID] as? NSNumber)?.uint32Value,
        let permissions = (attributes[.posixPermissions] as? NSNumber)?.intValue,
        owner == getuid(),
        permissions & 0o022 == 0
    else {
        return false
    }
    return true
}

private func regularFile(_ url: URL, allowSymbolicLink: Bool) -> Bool {
    guard let values = try? url.resourceValues(
        forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
    ) else {
        return false
    }
    if !allowSymbolicLink && values.isSymbolicLink == true {
        return false
    }
    if values.isRegularFile == true {
        return true
    }
    if allowSymbolicLink && values.isSymbolicLink == true {
        let resolved = url.resolvingSymlinksInPath()
        return (try? resolved.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true
    }
    return false
}

private func pathEntryExists(_ url: URL) -> Bool {
    if FileManager.default.fileExists(atPath: url.path) {
        return true
    }
    return (try? FileManager.default.destinationOfSymbolicLink(atPath: url.path)) != nil
}

private func isBridgeVersionOne(_ value: Any?) -> Bool {
    guard
        let number = value as? NSNumber,
        CFGetTypeID(number) != CFBooleanGetTypeID()
    else {
        return false
    }
    return number.intValue == 1 && number.doubleValue == 1
}

private func bridgeBoolean(_ value: Any?) -> Bool? {
    guard
        let number = value as? NSNumber,
        CFGetTypeID(number) == CFBooleanGetTypeID()
    else {
        return nil
    }
    return number.boolValue
}

private struct AppConfiguration {
    let runtimeRoot: URL
    let bundledPythonExecutable: URL

    static func load() throws -> AppConfiguration {
        guard let resource = Bundle.main.url(forResource: "config", withExtension: "plist") else {
            throw AppConfigurationError.unavailable
        }
        let data = try Data(contentsOf: resource, options: [.mappedIfSafe])
        guard
            data.count <= 16_384,
            let payload = try PropertyListSerialization.propertyList(
                from: data,
                options: [],
                format: nil
            ) as? [String: Any],
            let configuredRoot = payload["runtimeRoot"] as? String,
            configuredRoot.hasPrefix("/"),
            let configuredPython = payload["pythonExecutable"] as? String,
            configuredPython.hasPrefix("/"),
            !configuredPython.contains("\n"),
            !configuredPython.contains("\r")
        else {
            throw AppConfigurationError.unavailable
        }

        let unresolvedRoot = URL(
            fileURLWithPath: configuredRoot,
            isDirectory: true
        ).standardizedFileURL
        let unresolvedValues = try unresolvedRoot.resourceValues(
            forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        )
        guard
            unresolvedValues.isDirectory == true,
            unresolvedValues.isSymbolicLink != true,
            ownerControls(unresolvedRoot)
        else {
            throw AppConfigurationError.unavailable
        }

        let root = unresolvedRoot.resolvingSymlinksInPath().standardizedFileURL
        let module = root
            .appendingPathComponent("monitor", isDirectory: true)
            .appendingPathComponent("__main__.py", isDirectory: false)
        guard
            regularFile(module, allowSymbolicLink: false),
            ownerControls(module)
        else {
            throw AppConfigurationError.unavailable
        }
        let bundledPython = URL(fileURLWithPath: configuredPython, isDirectory: false)
            .standardizedFileURL
            .resolvingSymlinksInPath()
        guard
            regularFile(bundledPython, allowSymbolicLink: true),
            FileManager.default.isExecutableFile(atPath: bundledPython.path)
        else {
            throw AppConfigurationError.unavailable
        }
        return AppConfiguration(
            runtimeRoot: root,
            bundledPythonExecutable: bundledPython
        )
    }

    func dashboardURLIfPresent() throws -> URL? {
        let dashboardDirectory = runtimeRoot
            .appendingPathComponent("dashboard", isDirectory: true)
            .standardizedFileURL
        guard pathEntryExists(dashboardDirectory) else {
            return nil
        }
        let directoryValues = try dashboardDirectory.resourceValues(
            forKeys: [.isDirectoryKey, .isSymbolicLinkKey]
        )
        guard
            directoryValues.isDirectory == true,
            directoryValues.isSymbolicLink != true,
            ownerControls(dashboardDirectory)
        else {
            throw AppConfigurationError.unavailable
        }

        let candidate = dashboardDirectory
            .appendingPathComponent("index.html", isDirectory: false)
            .standardizedFileURL
        guard pathEntryExists(candidate) else {
            return nil
        }
        guard
            regularFile(candidate, allowSymbolicLink: false),
            ownerControls(candidate),
            candidate.deletingLastPathComponent().standardizedFileURL == dashboardDirectory
        else {
            throw AppConfigurationError.unavailable
        }
        return candidate.resolvingSymlinksInPath().standardizedFileURL
    }

    func dashboardURL() throws -> URL {
        guard let dashboard = try dashboardURLIfPresent() else {
            throw AppConfigurationError.unavailable
        }
        return dashboard
    }

    func pythonExecutable() throws -> URL {
        let pathFile = runtimeRoot
            .appendingPathComponent("config", isDirectory: true)
            .appendingPathComponent("python-path", isDirectory: false)
        guard pathEntryExists(pathFile) else {
            return bundledPythonExecutable
        }
        let attributes = try FileManager.default.attributesOfItem(atPath: pathFile.path)
        let size = (attributes[.size] as? NSNumber)?.intValue ?? Int.max
        guard
            size <= 4_096,
            regularFile(pathFile, allowSymbolicLink: false),
            ownerControls(pathFile)
        else {
            throw AppConfigurationError.unavailable
        }

        let raw = try String(contentsOf: pathFile, encoding: .utf8)
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
            !value.isEmpty,
            value.hasPrefix("/"),
            !value.contains("\n"),
            !value.contains("\r")
        else {
            throw AppConfigurationError.unavailable
        }

        let executable = URL(fileURLWithPath: value, isDirectory: false)
            .standardizedFileURL
            .resolvingSymlinksInPath()
        guard
            regularFile(executable, allowSymbolicLink: true),
            FileManager.default.isExecutableFile(atPath: executable.path)
        else {
            throw AppConfigurationError.unavailable
        }
        return executable
    }

    func curatedSeedURL() -> URL? {
        let seed = runtimeRoot
            .appendingPathComponent("seed", isDirectory: true)
            .appendingPathComponent("curated-pipeline.md", isDirectory: false)
            .standardizedFileURL
        guard regularFile(seed, allowSymbolicLink: false), ownerControls(seed) else {
            return nil
        }
        return seed
    }

    func isExactDashboardURL(_ candidate: URL?) -> Bool {
        guard
            let candidate,
            candidate.isFileURL,
            candidate.query == nil,
            let trusted = try? dashboardURL()
        else {
            return false
        }
        return candidate.standardizedFileURL.resolvingSymlinksInPath().path == trusted.path
    }

}

private enum BridgeAction: String {
    case scan
    case status
    case bookmark
    case profile
    case source
    case theme
}

private enum Theme: String, CaseIterable {
    case system
    case light
    case dark

    static var current: Theme {
        guard
            let value = UserDefaults.standard.string(forKey: themeDefaultsKey),
            let theme = Theme(rawValue: value)
        else {
            return .system
        }
        return theme
    }

    func persist() {
        UserDefaults.standard.set(rawValue, forKey: themeDefaultsKey)
    }
}

private enum ApplicationStatus: String, CaseIterable {
    case new
    case reviewed
    case apply
    case applied
    case skip
}

private final class BoundedOutputCapture {
    let pipe = Pipe()
    private let maximumBytes: Int
    private let lock = NSLock()
    private let readerGroup = DispatchGroup()
    private let readerQueue = DispatchQueue(
        label: "org.openai.opportunity-radar.command-output",
        qos: .utility
    )
    private var captured = Data()

    init(maximumBytes: Int = maximumCapturedOutputBytes) {
        self.maximumBytes = maximumBytes
        readerGroup.enter()
        readerQueue.async { [self] in
            defer { readerGroup.leave() }
            let handle = pipe.fileHandleForReading
            while true {
                do {
                    guard
                        let data = try handle.read(upToCount: 8_192),
                        !data.isEmpty
                    else {
                        return
                    }
                    append(data)
                } catch {
                    return
                }
            }
        }
    }

    private func append(_ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        if data.count >= maximumBytes {
            captured = Data(data.suffix(maximumBytes))
            return
        }
        let overflow = captured.count + data.count - maximumBytes
        if overflow > 0 {
            captured.removeFirst(overflow)
        }
        captured.append(data)
    }

    func closeParentWriteEnd() {
        try? pipe.fileHandleForWriting.close()
    }

    func finish() -> String {
        closeParentWriteEnd()
        if readerGroup.wait(timeout: .now() + commandIOWaitSeconds) == .timedOut {
            try? pipe.fileHandleForReading.close()
        }
        lock.lock()
        let snapshot = captured
        lock.unlock()
        try? pipe.fileHandleForReading.close()
        return String(decoding: snapshot, as: UTF8.self)
    }

    func cancel() {
        try? pipe.fileHandleForWriting.close()
        try? pipe.fileHandleForReading.close()
    }
}

private final class AsyncInputWriter {
    let pipe = Pipe()
    private let input: Data
    private let lock = NSLock()
    private let writerGroup = DispatchGroup()
    private let writerQueue = DispatchQueue(
        label: "org.openai.opportunity-radar.command-input",
        qos: .utility
    )
    private var started = false
    private var failed = false

    init(input: Data) {
        self.input = input
        writerGroup.enter()
    }

    private func closeParentReadEnd() {
        try? pipe.fileHandleForReading.close()
    }

    func start() {
        lock.lock()
        guard !started else {
            lock.unlock()
            return
        }
        started = true
        lock.unlock()
        closeParentReadEnd()
        writerQueue.async { [self] in
            defer {
                try? pipe.fileHandleForWriting.close()
                writerGroup.leave()
            }
            do {
                try pipe.fileHandleForWriting.write(contentsOf: input)
            } catch {
                lock.lock()
                failed = true
                lock.unlock()
            }
        }
    }

    func finish() -> Bool {
        if writerGroup.wait(timeout: .now() + commandIOWaitSeconds) == .timedOut {
            lock.lock()
            failed = true
            lock.unlock()
            try? pipe.fileHandleForWriting.close()
        }
        lock.lock()
        let succeeded = !failed
        lock.unlock()
        return succeeded
    }

    func cancel() {
        lock.lock()
        let shouldLeave = !started
        started = true
        failed = true
        lock.unlock()
        try? pipe.fileHandleForWriting.close()
        try? pipe.fileHandleForReading.close()
        if shouldLeave {
            writerGroup.leave()
        }
    }
}

private struct CommandDiagnostics {
    let standardOutput: String
    let standardError: String
    let inputSucceeded: Bool
}

private enum CommandPurpose {
    case bridge(BridgeAction, String)
    case initialDashboard
}

private struct RunningCommand {
    let process: Process
    let purpose: CommandPurpose
    let standardOutput: BoundedOutputCapture
    let standardError: BoundedOutputCapture
    let standardInput: AsyncInputWriter?
}

private struct QueuedProfileCommand {
    let requestID: String
    let input: Data
}

private enum RadarIcon {
    private static let navy = NSColor(
        calibratedRed: 4.0 / 255.0,
        green: 32.0 / 255.0,
        blue: 71.0 / 255.0,
        alpha: 1
    )
    private static let cobalt = NSColor(
        calibratedRed: 18.0 / 255.0,
        green: 92.0 / 255.0,
        blue: 237.0 / 255.0,
        alpha: 1
    )
    private static let cyan = NSColor(
        calibratedRed: 7.0 / 255.0,
        green: 171.0 / 255.0,
        blue: 213.0 / 255.0,
        alpha: 1
    )
    private static let mint = NSColor(
        calibratedRed: 3.0 / 255.0,
        green: 218.0 / 255.0,
        blue: 143.0 / 255.0,
        alpha: 1
    )
    private static let frost = NSColor(
        calibratedRed: 241.0 / 255.0,
        green: 247.0 / 255.0,
        blue: 1,
        alpha: 1
    )

    private static func radarSweep(center: NSPoint, radius: CGFloat) -> NSBezierPath {
        let sweep = NSBezierPath()
        sweep.move(to: center)
        sweep.appendArc(
            withCenter: center,
            radius: radius,
            startAngle: -38,
            endAngle: 28,
            clockwise: false
        )
        sweep.close()
        return sweep
    }

    private static func circle(center: NSPoint, radius: CGFloat) -> NSBezierPath {
        NSBezierPath(
            ovalIn: NSRect(
                x: center.x - radius,
                y: center.y - radius,
                width: radius * 2,
                height: radius * 2
            )
        )
    }

    static func image(size: CGFloat) -> NSImage {
        let image = NSImage(size: NSSize(width: size, height: size))
        image.lockFocus()
        defer { image.unlockFocus() }

        let canvas = NSRect(
            x: size * 0.035,
            y: size * 0.035,
            width: size * 0.93,
            height: size * 0.93
        )
        let center = NSPoint(x: size * 0.5, y: size * 0.5)
        let background = NSBezierPath(
            roundedRect: canvas,
            xRadius: size * 0.22,
            yRadius: size * 0.22
        )

        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = navy.withAlphaComponent(0.10)
        shadow.shadowBlurRadius = max(0.8, size * 0.022)
        shadow.shadowOffset = NSSize(width: 0, height: -size * 0.006)
        shadow.set()
        NSColor.white.setFill()
        background.fill()
        NSGraphicsContext.restoreGraphicsState()

        let backgroundGradient = NSGradient(colors: [
            NSColor.white,
            frost,
            NSColor(
                calibratedRed: 230.0 / 255.0,
                green: 241.0 / 255.0,
                blue: 1,
                alpha: 1
            ),
        ])
        backgroundGradient?.draw(in: background, angle: -55)

        let outerRing = NSRect(
            x: size * 0.135,
            y: size * 0.135,
            width: size * 0.73,
            height: size * 0.73
        )
        let ringWidth = max(1.0, size * 0.041)
        let ring = NSBezierPath(ovalIn: outerRing)
        ring.appendOval(in: outerRing.insetBy(dx: ringWidth, dy: ringWidth))
        ring.windingRule = .evenOdd

        let sweepRadius = outerRing.width * 0.5 - ringWidth * 0.62
        let sweep = radarSweep(center: center, radius: sweepRadius)
        NSGraphicsContext.saveGraphicsState()
        sweep.addClip()
        let sweepEnd = NSPoint(
            x: center.x + sweepRadius,
            y: center.y - sweepRadius * 0.18
        )
        let sweepGradient = NSGradient(colors: [
            cyan.withAlphaComponent(0.34),
            mint.withAlphaComponent(0.19),
            mint.withAlphaComponent(0.015),
        ])
        sweepGradient?.draw(from: center, to: sweepEnd, options: [])
        NSGraphicsContext.restoreGraphicsState()

        let spectrum = NSGradient(colors: [cobalt, cyan, mint])
        spectrum?.draw(in: ring, angle: 42)

        let handWidth = max(1.15, size * 0.036)
        let handTop = outerRing.maxY - ringWidth * 0.38
        let hand = NSBezierPath(
            roundedRect: NSRect(
                x: center.x - handWidth * 0.5,
                y: center.y - handWidth * 0.5,
                width: handWidth,
                height: handTop - center.y + handWidth * 0.5
            ),
            xRadius: handWidth * 0.5,
            yRadius: handWidth * 0.5
        )
        let handGradient = NSGradient(colors: [cobalt, cyan, mint])
        handGradient?.draw(in: hand, angle: 90)

        let signalDots: [(x: CGFloat, y: CGFloat, radius: CGFloat, color: NSColor)] = [
            (0.064, 0.244, 0.013, cyan),
            (
                0.126,
                0.250,
                0.017,
                NSColor(
                    calibratedRed: 6.0 / 255.0,
                    green: 188.0 / 255.0,
                    blue: 188.0 / 255.0,
                    alpha: 1
                )
            ),
            (
                0.194,
                0.214,
                0.020,
                NSColor(
                    calibratedRed: 5.0 / 255.0,
                    green: 205.0 / 255.0,
                    blue: 162.0 / 255.0,
                    alpha: 1
                )
            ),
            (0.253, 0.156, 0.023, mint),
        ]
        for dot in signalDots {
            let dotCenter = NSPoint(
                x: center.x + size * dot.x,
                y: center.y + size * dot.y
            )
            let dotRadius = max(0.48, size * dot.radius)
            dot.color.setFill()
            circle(center: dotCenter, radius: dotRadius).fill()
        }

        let centerDotRadius = max(0.55, size * 0.0105)
        cyan.setFill()
        circle(center: center, radius: centerDotRadius).fill()
        return image
    }

    static func writePNG(path: String, pixels: Int) throws {
        _ = NSApplication.shared
        let rendered = image(size: CGFloat(pixels))
        guard
            let tiff = rendered.tiffRepresentation,
            let bitmap = NSBitmapImageRep(data: tiff),
            let png = bitmap.representation(using: .png, properties: [:])
        else {
            throw CocoaError(.fileWriteUnknown)
        }
        try png.write(to: URL(fileURLWithPath: path), options: .atomic)
    }
}

private final class AppDelegate: NSObject,
    NSApplicationDelegate,
    NSWindowDelegate,
    WKNavigationDelegate,
    WKScriptMessageHandler
{
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusView: NSView!
    private var statusLabel: NSTextField!
    private var statusSpinner: NSProgressIndicator!
    private var configuration: AppConfiguration!
    private var runningCommand: RunningCommand?
    private var queuedProfileCommand: QueuedProfileCommand?
    private var scanCompletionPending = false
    private var pendingScanSucceeded = false
    private var terminationPending = false
    private var terminationDeadline: DispatchWorkItem?
    private var attemptedInitialDashboardGeneration = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.applicationIconImage = RadarIcon.image(size: 512)
        do {
            configuration = try AppConfiguration.load()
        } catch {
            showFatalConfigurationError()
            return
        }

        buildMainMenu()
        buildWindow()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        loadDashboardOrGenerate()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationShouldTerminate(
        _ sender: NSApplication
    ) -> NSApplication.TerminateReply {
        guard runningCommand != nil || queuedProfileCommand != nil else {
            return .terminateNow
        }
        if !terminationPending {
            beginDeferredTermination()
        }
        return .terminateLater
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard runningCommand != nil || queuedProfileCommand != nil else {
            return true
        }
        NSApp.terminate(sender)
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        terminationDeadline?.cancel()
        terminationDeadline = nil
        webView?.configuration.userContentController.removeScriptMessageHandler(
            forName: bridgeName
        )
    }

    private func beginDeferredTermination() {
        terminationPending = true
        window.title = "Opportunity Radar - Finishing current operation"
        let deadline = DispatchWorkItem { [weak self] in
            self?.cancelDeferredTerminationAfterTimeout()
        }
        terminationDeadline = deadline
        DispatchQueue.main.asyncAfter(
            deadline: .now() + terminationDeferralSeconds,
            execute: deadline
        )
    }

    private func completeDeferredTerminationIfNeeded() -> Bool {
        guard terminationPending else {
            window.title = "Opportunity Radar"
            return false
        }
        terminationPending = false
        terminationDeadline?.cancel()
        terminationDeadline = nil
        NSApp.reply(toApplicationShouldTerminate: true)
        return true
    }

    private func cancelDeferredTerminationAfterTimeout() {
        guard terminationPending else { return }
        terminationPending = false
        terminationDeadline = nil
        NSApp.reply(toApplicationShouldTerminate: false)
        window.title = "Opportunity Radar - Operation still running"
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        guard window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Opportunity Radar is still working"
        alert.informativeText = "Quit was canceled to avoid interrupting the current operation. The app stayed open to protect its data; try quitting again after the operation finishes."
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window)
    }

    private func buildWindow() {
        let webConfiguration = WKWebViewConfiguration()
        webConfiguration.websiteDataStore = .nonPersistent()
        webConfiguration.preferences.javaScriptCanOpenWindowsAutomatically = false
        webConfiguration.userContentController.add(self, name: bridgeName)

        webView = WKWebView(frame: .zero, configuration: webConfiguration)
        webView.navigationDelegate = self
        webView.translatesAutoresizingMaskIntoConstraints = false
        webView.isHidden = true

        statusLabel = NSTextField(labelWithString: "Opening your dashboard...")
        statusLabel.alignment = .center
        statusLabel.font = .systemFont(ofSize: 15, weight: .medium)
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.lineBreakMode = .byWordWrapping
        statusLabel.maximumNumberOfLines = 0

        statusSpinner = NSProgressIndicator()
        statusSpinner.style = .spinning
        statusSpinner.controlSize = .regular
        statusSpinner.isIndeterminate = true
        statusSpinner.startAnimation(nil)

        let statusStack = NSStackView(views: [statusSpinner, statusLabel])
        statusStack.orientation = .vertical
        statusStack.alignment = .centerX
        statusStack.spacing = 14
        statusStack.translatesAutoresizingMaskIntoConstraints = false

        statusView = NSView()
        statusView.translatesAutoresizingMaskIntoConstraints = false
        statusView.addSubview(statusStack)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1240, height: 800),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Opportunity Radar"
        window.minSize = NSSize(width: 760, height: 560)
        window.center()
        window.isReleasedWhenClosed = false
        window.isRestorable = false
        window.delegate = self

        let content = NSView(frame: window.contentView?.bounds ?? .zero)
        window.contentView = content
        content.addSubview(webView)
        content.addSubview(statusView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            webView.topAnchor.constraint(equalTo: content.topAnchor),
            webView.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            statusView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            statusView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            statusView.topAnchor.constraint(equalTo: content.topAnchor),
            statusView.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            statusStack.centerXAnchor.constraint(equalTo: statusView.centerXAnchor),
            statusStack.centerYAnchor.constraint(equalTo: statusView.centerYAnchor),
            statusLabel.widthAnchor.constraint(lessThanOrEqualToConstant: 430),
        ])
    }

    private func buildMainMenu() {
        let mainMenu = NSMenu()
        let applicationItem = NSMenuItem()
        let applicationMenu = NSMenu()
        applicationMenu.addItem(
            withTitle: "Quit Opportunity Radar",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        applicationItem.submenu = applicationMenu
        mainMenu.addItem(applicationItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(
            withTitle: "Select All",
            action: #selector(NSText.selectAll(_:)),
            keyEquivalent: "a"
        )
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)
        NSApp.mainMenu = mainMenu
    }

    private func loadDashboardOrGenerate() {
        do {
            if let dashboard = try configuration.dashboardURLIfPresent() {
                loadDashboard(dashboard)
                return
            }
            guard !attemptedInitialDashboardGeneration else {
                showDashboardPreparationFailure()
                return
            }
            attemptedInitialDashboardGeneration = true
            showPreparingDashboard()
            startInitialDashboardGeneration()
        } catch {
            showFatalConfigurationError()
        }
    }

    private func loadDashboard(_ dashboard: URL) {
        statusSpinner.stopAnimation(nil)
        statusView.isHidden = true
        webView.isHidden = true
        webView.loadFileURL(
            dashboard,
            allowingReadAccessTo: dashboard.deletingLastPathComponent()
        )
    }

    private func showPreparingDashboard() {
        webView.isHidden = true
        statusView.isHidden = false
        statusLabel.stringValue = "Preparing your dashboard..."
        statusSpinner.isHidden = false
        statusSpinner.startAnimation(nil)
    }

    private func showDashboardPreparationFailure() {
        webView.isHidden = true
        statusView.isHidden = false
        statusSpinner.stopAnimation(nil)
        statusSpinner.isHidden = true
        statusLabel.stringValue = "The dashboard could not be prepared. Quit and reopen Opportunity Radar to try again."
    }

    private func reloadDashboard(after action: BridgeAction) {
        do {
            let dashboard = try configuration.dashboardURL()
            loadDashboard(dashboard)
        } catch {
            complete(
                action: action,
                requestID: nil,
                ok: false,
                message: "The dashboard could not be reloaded."
            )
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        setPageTheme(Theme.current) {
            webView.isHidden = false
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        loadDashboardOrGenerate()
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if configuration.isExactDashboardURL(url) {
            decisionHandler(.allow)
            return
        }

        let scheme = url.scheme?.lowercased()
        if
            navigationAction.navigationType == .linkActivated,
            ["http", "https"].contains(scheme ?? ""),
            url.host?.isEmpty == false,
            url.user == nil,
            url.password == nil
        {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard
            message.name == bridgeName,
            message.frameInfo.isMainFrame,
            configuration.isExactDashboardURL(message.frameInfo.request.url),
            let payload = message.body as? [String: Any],
            isBridgeVersionOne(payload["version"]),
            let actionValue = payload["action"] as? String,
            let action = BridgeAction(rawValue: actionValue)
        else {
            return
        }

        switch action {
        case .scan:
            handleScan(payload)
        case .status:
            handleStatus(payload)
        case .bookmark:
            handleBookmark(payload)
        case .profile:
            handleProfile(payload)
        case .source:
            handleSource(payload)
        case .theme:
            handleTheme(payload)
        }
    }

    private func handleScan(_ payload: [String: Any]) {
        guard
            let requestID = payload["request"] as? String,
            validRequestID(requestID)
        else {
            return
        }
        guard
            Set(payload.keys) == Set(["version", "action", "mode", "request"]),
            let mode = payload["mode"] as? String,
            ["due", "all"].contains(mode)
        else {
            complete(
                action: .scan,
                requestID: requestID,
                ok: false,
                message: "The refresh request was rejected."
            )
            return
        }
        let arguments = mode == "all"
            ? ["-m", "monitor", "scan", "--quiet", "--force"]
            : ["-m", "monitor", "scan", "--quiet"]
        startCommand(action: .scan, requestID: requestID, arguments: arguments)
    }

    private func handleStatus(_ payload: [String: Any]) {
        guard
            let requestID = payload["request"] as? String,
            validRequestID(requestID)
        else {
            return
        }
        guard
            Set(payload.keys) == Set(["version", "action", "id", "status", "request"]),
            let identifier = payload["id"] as? String,
            validIdentifier(identifier),
            let value = payload["status"] as? String,
            ApplicationStatus(rawValue: value) != nil
        else {
            complete(
                action: .status,
                requestID: requestID,
                ok: false,
                message: "The status request was rejected."
            )
            return
        }
        startCommand(
            action: .status,
            requestID: requestID,
            arguments: ["-m", "monitor", "status", identifier, value, "--quiet"]
        )
    }

    private func handleBookmark(_ payload: [String: Any]) {
        guard
            let requestID = payload["request"] as? String,
            validRequestID(requestID)
        else {
            return
        }
        guard
            Set(payload.keys) == Set(["version", "action", "id", "bookmarked", "request"]),
            let identifier = payload["id"] as? String,
            validIdentifier(identifier),
            let bookmarked = bridgeBoolean(payload["bookmarked"])
        else {
            complete(
                action: .bookmark,
                requestID: requestID,
                ok: false,
                message: "The bookmark request was rejected."
            )
            return
        }
        startCommand(
            action: .bookmark,
            requestID: requestID,
            arguments: [
                "-m",
                "monitor",
                "bookmark",
                identifier,
                bookmarked ? "true" : "false",
                "--quiet",
            ]
        )
    }

    private func handleProfile(_ payload: [String: Any]) {
        guard
            let requestID = payload["request"] as? String,
            validRequestID(requestID)
        else {
            return
        }
        guard
            Set(payload.keys) == Set(["version", "action", "profile", "request"]),
            let profile = payload["profile"] as? [String: Any],
            boundedProfileValue(profile),
            JSONSerialization.isValidJSONObject(profile),
            let input = try? JSONSerialization.data(withJSONObject: profile),
            input.count <= maximumProfilePayloadBytes
        else {
            complete(
                action: .profile,
                requestID: requestID,
                ok: false,
                message: "The profile update was rejected."
            )
            return
        }
        let scanIsRunning: Bool
        if let runningCommand, case .bridge(.scan, _) = runningCommand.purpose {
            scanIsRunning = true
        } else {
            scanIsRunning = false
        }
        if scanIsRunning || scanCompletionPending {
            guard queuedProfileCommand == nil else {
                complete(
                    action: .profile,
                    requestID: requestID,
                    ok: false,
                    message: "A profile update is already saved for after this scan."
                )
                return
            }
            queuedProfileCommand = QueuedProfileCommand(
                requestID: requestID,
                input: input
            )
            return
        }
        startCommand(
            action: .profile,
            requestID: requestID,
            arguments: [
                "-m",
                "monitor",
                "profile",
                "apply",
                "--stdin",
                "--quiet",
            ],
            input: input
        )
    }

    private func handleSource(_ payload: [String: Any]) {
        guard
            let requestID = payload["request"] as? String,
            validRequestID(requestID)
        else {
            return
        }
        guard
            Set(payload.keys) == Set(["version", "action", "name", "url", "request"]),
            let name = payload["name"] as? String,
            validSourceName(name),
            let url = payload["url"] as? String,
            validPublicHTTPSURL(url)
        else {
            complete(
                action: .source,
                requestID: requestID,
                ok: false,
                message: "The source request was rejected."
            )
            return
        }
        startCommand(
            action: .source,
            requestID: requestID,
            arguments: [
                "-m",
                "monitor",
                "sources",
                "add",
                "--name",
                name,
                "--url",
                url,
            ]
        )
    }

    private func handleTheme(_ payload: [String: Any]) {
        guard
            Set(payload.keys) == Set(["version", "action", "theme"]),
            let value = payload["theme"] as? String,
            let theme = Theme(rawValue: value)
        else {
            complete(
                action: .theme,
                requestID: nil,
                ok: false,
                message: "The theme request was rejected."
            )
            return
        }
        theme.persist()
        setPageTheme(theme)
        complete(action: .theme, requestID: nil, ok: true, message: "Theme updated.")
    }

    private func startCommand(
        action: BridgeAction,
        requestID: String,
        arguments: [String],
        input: Data? = nil
    ) {
        guard runningCommand == nil else {
            complete(
                action: action,
                requestID: requestID,
                ok: false,
                message: "Another action is already running."
            )
            return
        }
        launchCommand(
            purpose: .bridge(action, requestID),
            arguments: arguments,
            input: input
        )
    }

    private func startInitialDashboardGeneration() {
        guard runningCommand == nil else {
            showDashboardPreparationFailure()
            return
        }
        launchCommand(
            purpose: .initialDashboard,
            arguments: ["-m", "monitor", "dashboard", "--quiet"]
        )
    }

    private func launchCommand(
        purpose: CommandPurpose,
        arguments: [String],
        input: Data? = nil
    ) {
        let process = Process()
        let standardOutput = BoundedOutputCapture()
        let standardError = BoundedOutputCapture()
        let standardInput = input.map(AsyncInputWriter.init(input:))
        do {
            process.executableURL = try configuration.pythonExecutable()
            process.arguments = arguments
            process.currentDirectoryURL = configuration.runtimeRoot
            process.environment = commandEnvironment()
            process.standardOutput = standardOutput.pipe
            process.standardError = standardError.pipe
            if let standardInput {
                process.standardInput = standardInput.pipe
            }
            let command = RunningCommand(
                process: process,
                purpose: purpose,
                standardOutput: standardOutput,
                standardError: standardError,
                standardInput: standardInput
            )
            runningCommand = command
            process.terminationHandler = { [weak self, weak process] finished in
                guard let process else { return }
                let diagnostics = CommandDiagnostics(
                    standardOutput: standardOutput.finish(),
                    standardError: standardError.finish(),
                    inputSucceeded: standardInput?.finish() ?? true
                )
                let succeeded = diagnostics.inputSucceeded
                    && finished.terminationReason == .exit
                    && finished.terminationStatus == 0
                DispatchQueue.main.async {
                    self?.finishCommand(
                        process: process,
                        succeeded: succeeded,
                        diagnostics: diagnostics
                    )
                }
            }
            try process.run()
            standardOutput.closeParentWriteEnd()
            standardError.closeParentWriteEnd()
            standardInput?.start()
        } catch {
            runningCommand = nil
            standardOutput.cancel()
            standardError.cancel()
            standardInput?.cancel()
            switch purpose {
            case .bridge(let action, let requestID):
                if completeDeferredTerminationIfNeeded() {
                    return
                }
                complete(
                    action: action,
                    requestID: requestID,
                    ok: false,
                    message: "The action could not be started."
                )
            case .initialDashboard:
                showDashboardPreparationFailure()
            }
        }
    }

    private func finishCommand(
        process: Process,
        succeeded: Bool,
        diagnostics: CommandDiagnostics
    ) {
        guard
            let command = runningCommand,
            command.process === process
        else {
            return
        }
        let queuedProfileWillFollow: Bool
        if case .bridge(.scan, _) = command.purpose {
            queuedProfileWillFollow = queuedProfileCommand != nil
        } else {
            queuedProfileWillFollow = false
        }
        runningCommand = nil
        if !queuedProfileWillFollow && completeDeferredTerminationIfNeeded() {
            return
        }

        switch command.purpose {
        case .initialDashboard:
            guard succeeded else {
                showDashboardPreparationFailure()
                return
            }
            do {
                guard let dashboard = try configuration.dashboardURLIfPresent() else {
                    showDashboardPreparationFailure()
                    return
                }
                loadDashboard(dashboard)
            } catch {
                showDashboardPreparationFailure()
            }
        case .bridge(let action, let requestID):
            finishBridgeCommand(
                action: action,
                requestID: requestID,
                succeeded: succeeded,
                diagnostics: diagnostics
            )
        }
    }

    private func finishBridgeCommand(
        action: BridgeAction,
        requestID: String,
        succeeded: Bool,
        diagnostics: CommandDiagnostics
    ) {
        let message: String
        switch action {
        case .scan:
            message = succeeded ? "Refresh complete." : "The refresh could not be completed."
        case .status:
            message = succeeded ? "Application status updated." : "The status could not be updated."
        case .bookmark:
            message = succeeded ? "Bookmark updated." : "The bookmark could not be updated."
        case .profile:
            message = succeeded
                ? "Profile updated."
                : profileFailureMessage(diagnostics)
        case .source:
            message = succeeded
                ? "Source added. Reloading..."
                : sourceFailureMessage(diagnostics)
        case .theme:
            message = succeeded ? "Theme updated." : "The theme could not be updated."
        }

        if action == .scan {
            scanCompletionPending = true
            pendingScanSucceeded = succeeded
            complete(
                action: action,
                requestID: requestID,
                ok: succeeded,
                message: message
            ) { [weak self] in
                self?.finishScanCompletion()
            }
            return
        }

        complete(
            action: action,
            requestID: requestID,
            ok: succeeded,
            message: message
        ) { [weak self] in
            if succeeded && (action == .scan || action == .profile || action == .source) {
                self?.reloadDashboard(after: action)
            }
        }
    }

    private func finishScanCompletion() {
        let scanSucceeded = pendingScanSucceeded
        scanCompletionPending = false
        pendingScanSucceeded = false
        guard let queued = queuedProfileCommand else {
            if scanSucceeded {
                reloadDashboard(after: .scan)
            }
            return
        }
        queuedProfileCommand = nil
        launchCommand(
            purpose: .bridge(.profile, queued.requestID),
            arguments: [
                "-m",
                "monitor",
                "profile",
                "apply",
                "--stdin",
                "--quiet",
            ],
            input: queued.input
        )
    }

    private func profileFailureMessage(_ diagnostics: CommandDiagnostics) -> String {
        guard diagnostics.inputSucceeded else {
            return "The profile data could not be sent to the helper. Try saving again."
        }
        let detail = (diagnostics.standardError + "\n" + diagnostics.standardOutput)
            .lowercased()
        if detail.contains("profile changed after it was opened")
            || detail.contains("stale revision")
        {
            return "Your profile changed after this editor opened. Reload the dashboard and try again."
        }
        if detail.contains("already running")
            || detail.contains("database is locked")
            || detail.contains("resource temporarily unavailable")
        {
            return "Opportunity Radar is busy with another scan or profile update. Try again in a moment."
        }
        if detail.contains("environment configuration override") {
            return "Profile editing is unavailable while a configuration override is active."
        }
        if detail.contains("unsafe")
            || detail.contains("symbolic link")
            || detail.contains("not owned by the current user")
        {
            return "Profile storage failed a safety check. Reinstall the optional app before trying again."
        }
        if detail.contains("threshold") {
            return "Keep fit thresholds ordered from priority to strong to watch, then try again."
        }
        if detail.contains("source pack") || detail.contains("selected_packs") {
            return "Choose at least one available source pack, then try again."
        }
        if detail.contains("matching rule") || detail.contains("matching.rules") {
            return "One of the matching rules is incomplete or invalid. Review its terms and fields."
        }
        if detail.contains("document route") || detail.contains("default document") {
            return "One of the application document routes is incomplete or duplicated."
        }
        let validationMarkers = [
            " must ",
            " invalid",
            "unsupported",
            "too large",
            "too many",
            "bounded",
            "needs at least",
            "select at least",
            "not be empty",
        ]
        if validationMarkers.contains(where: detail.contains) {
            return "The profile contains a value that could not be validated. Review the fields and try again."
        }
        return "The profile could not be saved. Try again in a moment."
    }

    private func sourceFailureMessage(_ diagnostics: CommandDiagnostics) -> String {
        let detail = (diagnostics.standardError + "\n" + diagnostics.standardOutput)
            .lowercased()
        if detail.contains("already exists") || detail.contains("already configured") {
            return "That source has already been added."
        }
        if detail.contains("already running")
            || detail.contains("database is locked")
            || detail.contains("resource temporarily unavailable")
        {
            return "Opportunity Radar is busy with another update. Try again in a moment."
        }
        if detail.contains("timed out")
            || detail.contains("temporary failure")
            || detail.contains("name or service not known")
            || detail.contains("could not fetch")
            || detail.contains("source check failed")
        {
            return "The official page could not be reached. Check the URL and try again."
        }
        if detail.contains("unsafe")
            || detail.contains("symbolic link")
            || detail.contains("not owned by the current user")
        {
            return "Source storage failed a safety check. Reinstall the optional app before trying again."
        }
        if detail.contains("https")
            || detail.contains("url")
            || detail.contains("unsupported")
            || detail.contains("invalid")
        {
            return "Enter the public HTTPS URL for an official jobs or careers page."
        }
        return "The source could not be added. Check the official page and try again."
    }

    private func commandEnvironment() -> [String: String] {
        var environment = [
            "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
            "LANG": "en_US.UTF-8",
            "LC_CTYPE": "UTF-8",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": configuration.runtimeRoot
                .appendingPathComponent("data/pycache", isDirectory: true)
                .path,
        ]
        if let seed = configuration.curatedSeedURL() {
            environment["OPPORTUNITY_RADAR_CURATED_PATH"] = seed.path
        }
        return environment
    }

    private func complete(
        action: BridgeAction,
        requestID: String?,
        ok: Bool,
        message: String,
        completion: (() -> Void)? = nil
    ) {
        let payload: [String: Any] = [
            "action": action.rawValue,
            "ok": ok,
            "message": message,
        ]
        var response = payload
        if let requestID {
            response["request"] = requestID
        }
        guard let json = jsonLiteral(response) else {
            completion?()
            return
        }
        webView.evaluateJavaScript(
            "window.OpportunityRadarNative?.complete(\(json));"
        ) { _, _ in
            completion?()
        }
    }

    private func setPageTheme(_ theme: Theme, completion: (() -> Void)? = nil) {
        guard let json = jsonLiteral(theme.rawValue) else {
            completion?()
            return
        }
        webView.evaluateJavaScript(
            "window.OpportunityRadarNative?.setTheme(\(json));",
            completionHandler: { _, _ in completion?() }
        )
    }

    private func jsonLiteral(_ value: Any) -> String? {
        guard let data = try? JSONSerialization.data(
            withJSONObject: value,
            options: [.fragmentsAllowed]
        ),
            let string = String(data: data, encoding: .utf8)
        else {
            return nil
        }
        return string
    }

    private func validIdentifier(_ value: String) -> Bool {
        guard value.utf8.count == 24 else { return false }
        return value.utf8.allSatisfy { byte in
            (48...57).contains(byte) || (97...102).contains(byte)
        }
    }

    private func validRequestID(_ value: String) -> Bool {
        guard (2...32).contains(value.utf8.count), value.first == "r" else {
            return false
        }
        return value.dropFirst().utf8.allSatisfy { byte in
            (48...57).contains(byte)
        }
    }

    private func validSourceName(_ value: String) -> Bool {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return value == trimmed
            && (1...120).contains(value.count)
            && value.utf8.count <= 240
            && !value.unicodeScalars.contains {
                CharacterSet.controlCharacters.contains($0)
            }
    }

    private func validPublicHTTPSURL(_ value: String) -> Bool {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
            value == trimmed,
            (12...2_000).contains(value.utf8.count),
            !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
            let components = URLComponents(string: value),
            components.scheme?.lowercased() == "https",
            components.user == nil,
            components.password == nil,
            components.port == nil || components.port == 443,
            components.url != nil,
            let rawHost = components.host?.lowercased()
        else {
            return false
        }
        let host = rawHost.hasSuffix(".") ? String(rawHost.dropLast()) : rawHost
        guard
            host.utf8.count <= 253,
            host.contains("."),
            !host.contains(":"),
            !host.utf8.allSatisfy({ (48...57).contains($0) || $0 == 46 })
        else {
            return false
        }
        let reservedSuffixes = [
            "local" + "host", "local", "internal", "test", "invalid", "example", "onion",
        ]
        guard !reservedSuffixes.contains(where: {
            host == $0 || host.hasSuffix("." + $0)
        }) else {
            return false
        }
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        guard labels.count >= 2 else { return false }
        return labels.allSatisfy { label in
            guard (1...63).contains(label.utf8.count) else { return false }
            let bytes = Array(label.utf8)
            let allowed = bytes.allSatisfy { byte in
                (48...57).contains(byte)
                    || (97...122).contains(byte)
                    || byte == 45
            }
            return allowed && bytes.first != 45 && bytes.last != 45
        }
    }

    private func boundedProfileValue(_ value: Any, depth: Int = 0) -> Bool {
        guard depth <= 8 else { return false }
        if value is NSNull { return true }
        if let text = value as? String {
            return text.utf8.count <= 2_000
                && !text.unicodeScalars.contains { $0.value < 0x20 && $0 != "\n" && $0 != "\t" }
        }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return true }
            let numeric = number.doubleValue
            return numeric.isFinite && abs(numeric) <= 1_000_000
        }
        if let values = value as? [Any] {
            return values.count <= 200
                && values.allSatisfy { boundedProfileValue($0, depth: depth + 1) }
        }
        if let values = value as? [String: Any] {
            return values.count <= 40
                && values.allSatisfy { key, child in
                    !key.isEmpty
                        && key.utf8.count <= 80
                        && key.unicodeScalars.allSatisfy { scalar in
                            scalar.value >= 0x20 && scalar.value != 0x7f
                        }
                        && boundedProfileValue(child, depth: depth + 1)
                }
        }
        return false
    }

    private func showFatalConfigurationError() {
        let alert = NSAlert()
        alert.messageText = "Opportunity Radar could not open"
        alert.informativeText = AppConfigurationError.unavailable.localizedDescription
        alert.alertStyle = .critical
        alert.runModal()
        NSApp.terminate(nil)
    }
}

if CommandLine.arguments.count == 4, CommandLine.arguments[1] == "--render-icon" {
    do {
        guard let pixels = Int(CommandLine.arguments[3]), pixels > 0 else {
            throw CocoaError(.fileWriteInvalidFileName)
        }
        try RadarIcon.writePNG(path: CommandLine.arguments[2], pixels: pixels)
        exit(EXIT_SUCCESS)
    } catch {
        FileHandle.standardError.write(Data("Unable to render the application icon.\n".utf8))
        exit(EXIT_FAILURE)
    }
}

let application = NSApplication.shared
private let delegate = AppDelegate()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
