import AppKit
import CoreFoundation
import Foundation
import WebKit

private let bridgeName = "opportunityRadar"
private let themeDefaultsKey = "OpportunityRadarTheme"

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
            configuredRoot.hasPrefix("/")
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
        return AppConfiguration(runtimeRoot: root)
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

private final class OutputDrainer {
    let pipe = Pipe()

    init() {
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
            }
        }
    }

    func stop() {
        pipe.fileHandleForReading.readabilityHandler = nil
        try? pipe.fileHandleForReading.close()
        try? pipe.fileHandleForWriting.close()
    }
}

private enum CommandPurpose {
    case bridge(BridgeAction)
    case initialDashboard
}

private struct RunningCommand {
    let process: Process
    let purpose: CommandPurpose
    let standardOutput: OutputDrainer
    let standardError: OutputDrainer
}

private enum RadarIcon {
    private static let pine = NSColor(
        calibratedRed: 18.0 / 255.0,
        green: 58.0 / 255.0,
        blue: 50.0 / 255.0,
        alpha: 1
    )
    private static let coral = NSColor(
        calibratedRed: 216.0 / 255.0,
        green: 105.0 / 255.0,
        blue: 76.0 / 255.0,
        alpha: 1
    )
    private static let cobalt = NSColor(
        calibratedRed: 51.0 / 255.0,
        green: 93.0 / 255.0,
        blue: 168.0 / 255.0,
        alpha: 1
    )
    private static let warm = NSColor(
        calibratedRed: 251.0 / 255.0,
        green: 250.0 / 255.0,
        blue: 247.0 / 255.0,
        alpha: 1
    )

    private static func asymmetricTile(in rect: NSRect, size: CGFloat) -> NSBezierPath {
        let topLeft = size * 0.29
        let topRight = size * 0.38
        let bottomRight = size * 0.27
        let bottomLeft = size * 0.39
        let curve: CGFloat = 0.552_284_75
        let path = NSBezierPath()

        path.move(to: NSPoint(x: rect.minX + bottomLeft, y: rect.minY))
        path.line(to: NSPoint(x: rect.maxX - bottomRight, y: rect.minY))
        path.curve(
            to: NSPoint(x: rect.maxX, y: rect.minY + bottomRight),
            controlPoint1: NSPoint(
                x: rect.maxX - bottomRight + bottomRight * curve,
                y: rect.minY
            ),
            controlPoint2: NSPoint(
                x: rect.maxX,
                y: rect.minY + bottomRight - bottomRight * curve
            )
        )
        path.line(to: NSPoint(x: rect.maxX, y: rect.maxY - topRight))
        path.curve(
            to: NSPoint(x: rect.maxX - topRight, y: rect.maxY),
            controlPoint1: NSPoint(
                x: rect.maxX,
                y: rect.maxY - topRight + topRight * curve
            ),
            controlPoint2: NSPoint(
                x: rect.maxX - topRight + topRight * curve,
                y: rect.maxY
            )
        )
        path.line(to: NSPoint(x: rect.minX + topLeft, y: rect.maxY))
        path.curve(
            to: NSPoint(x: rect.minX, y: rect.maxY - topLeft),
            controlPoint1: NSPoint(
                x: rect.minX + topLeft - topLeft * curve,
                y: rect.maxY
            ),
            controlPoint2: NSPoint(
                x: rect.minX,
                y: rect.maxY - topLeft + topLeft * curve
            )
        )
        path.line(to: NSPoint(x: rect.minX, y: rect.minY + bottomLeft))
        path.curve(
            to: NSPoint(x: rect.minX + bottomLeft, y: rect.minY),
            controlPoint1: NSPoint(
                x: rect.minX,
                y: rect.minY + bottomLeft - bottomLeft * curve
            ),
            controlPoint2: NSPoint(
                x: rect.minX + bottomLeft - bottomLeft * curve,
                y: rect.minY
            )
        )
        path.close()
        return path
    }

    private static func fillSector(
        center: NSPoint,
        radius: CGFloat,
        startAngle: CGFloat,
        endAngle: CGFloat,
        color: NSColor
    ) {
        let sector = NSBezierPath()
        sector.move(to: center)
        sector.appendArc(
            withCenter: center,
            radius: radius,
            startAngle: startAngle,
            endAngle: endAngle,
            clockwise: false
        )
        sector.close()
        color.setFill()
        sector.fill()
    }

    static func image(size: CGFloat) -> NSImage {
        let image = NSImage(size: NSSize(width: size, height: size))
        image.lockFocus()
        defer { image.unlockFocus() }

        let frame = NSRect(
            x: size * 0.08,
            y: size * 0.08,
            width: size * 0.84,
            height: size * 0.84
        )
        let center = NSPoint(x: size * 0.5, y: size * 0.5)
        let tile = asymmetricTile(in: frame, size: size)

        NSGraphicsContext.saveGraphicsState()
        let rotation = NSAffineTransform()
        rotation.translateX(by: center.x, yBy: center.y)
        rotation.rotate(byDegrees: -5)
        rotation.translateX(by: -center.x, yBy: -center.y)
        rotation.concat()

        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor(calibratedWhite: 0.05, alpha: 0.20)
        shadow.shadowBlurRadius = max(0.7, size * 0.045)
        shadow.shadowOffset = NSSize(width: 0, height: -size * 0.018)
        shadow.set()
        pine.setFill()
        tile.fill()
        NSGraphicsContext.restoreGraphicsState()

        NSGraphicsContext.saveGraphicsState()
        tile.addClip()
        pine.setFill()
        tile.fill()
        fillSector(
            center: center,
            radius: size,
            startAngle: -34,
            endAngle: 38,
            color: cobalt
        )
        fillSector(
            center: center,
            radius: size,
            startAngle: 38,
            endAngle: 96,
            color: coral
        )
        NSGraphicsContext.restoreGraphicsState()

        tile.lineWidth = max(0.5, size * 0.012)
        NSColor(calibratedWhite: 1, alpha: 0.18).setStroke()
        tile.stroke()

        let cutoutRadius = size * 0.27
        let cutout = NSBezierPath(
            ovalIn: NSRect(
                x: center.x - cutoutRadius,
                y: center.y - cutoutRadius,
                width: cutoutRadius * 2,
                height: cutoutRadius * 2
            )
        )
        warm.setFill()
        cutout.fill()

        let signalCenter = NSPoint(
            x: center.x + size * 0.012,
            y: center.y + size * 0.018
        )
        let signalHaloRadius = size * 0.15
        let signalHalo = NSBezierPath(
            ovalIn: NSRect(
                x: signalCenter.x - signalHaloRadius,
                y: signalCenter.y - signalHaloRadius,
                width: signalHaloRadius * 2,
                height: signalHaloRadius * 2
            )
        )
        coral.withAlphaComponent(0.18).setFill()
        signalHalo.fill()

        let signalRadius = size * 0.105
        let signal = NSBezierPath(
            ovalIn: NSRect(
                x: signalCenter.x - signalRadius,
                y: signalCenter.y - signalRadius,
                width: signalRadius * 2,
                height: signalRadius * 2
            )
        )
        coral.setFill()
        signal.fill()
        NSGraphicsContext.restoreGraphicsState()
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

    func applicationWillTerminate(_ notification: Notification) {
        webView?.configuration.userContentController.removeScriptMessageHandler(
            forName: bridgeName
        )
        if let process = runningCommand?.process, process.isRunning {
            process.terminate()
        }
        runningCommand?.standardOutput.stop()
        runningCommand?.standardError.stop()
        runningCommand = nil
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
        webView.isHidden = false
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
                ok: false,
                message: "The dashboard could not be reloaded."
            )
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        setPageTheme(Theme.current)
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
        case .theme:
            handleTheme(payload)
        }
    }

    private func handleScan(_ payload: [String: Any]) {
        guard
            Set(payload.keys) == Set(["version", "action", "mode"]),
            let mode = payload["mode"] as? String,
            ["due", "all"].contains(mode)
        else {
            complete(action: .scan, ok: false, message: "The refresh request was rejected.")
            return
        }
        let arguments = mode == "all"
            ? ["-m", "monitor", "scan", "--quiet", "--force"]
            : ["-m", "monitor", "scan", "--quiet"]
        startCommand(action: .scan, arguments: arguments)
    }

    private func handleStatus(_ payload: [String: Any]) {
        guard
            Set(payload.keys) == Set(["version", "action", "id", "status"]),
            let identifier = payload["id"] as? String,
            validIdentifier(identifier),
            let value = payload["status"] as? String,
            ApplicationStatus(rawValue: value) != nil
        else {
            complete(action: .status, ok: false, message: "The status request was rejected.")
            return
        }
        startCommand(
            action: .status,
            arguments: ["-m", "monitor", "status", identifier, value, "--quiet"]
        )
    }

    private func handleBookmark(_ payload: [String: Any]) {
        guard
            Set(payload.keys) == Set(["version", "action", "id", "bookmarked"]),
            let identifier = payload["id"] as? String,
            validIdentifier(identifier),
            let bookmarked = bridgeBoolean(payload["bookmarked"])
        else {
            complete(action: .bookmark, ok: false, message: "The bookmark request was rejected.")
            return
        }
        startCommand(
            action: .bookmark,
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

    private func handleTheme(_ payload: [String: Any]) {
        guard
            Set(payload.keys) == Set(["version", "action", "theme"]),
            let value = payload["theme"] as? String,
            let theme = Theme(rawValue: value)
        else {
            complete(action: .theme, ok: false, message: "The theme request was rejected.")
            return
        }
        theme.persist()
        setPageTheme(theme)
        complete(action: .theme, ok: true, message: "Theme updated.")
    }

    private func startCommand(action: BridgeAction, arguments: [String]) {
        guard runningCommand == nil else {
            complete(action: action, ok: false, message: "Another action is already running.")
            return
        }
        launchCommand(purpose: .bridge(action), arguments: arguments)
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

    private func launchCommand(purpose: CommandPurpose, arguments: [String]) {
        let process = Process()
        let standardOutput = OutputDrainer()
        let standardError = OutputDrainer()
        do {
            process.executableURL = try configuration.pythonExecutable()
            process.arguments = arguments
            process.currentDirectoryURL = configuration.runtimeRoot
            process.environment = commandEnvironment()
            process.standardOutput = standardOutput.pipe
            process.standardError = standardError.pipe
            let command = RunningCommand(
                process: process,
                purpose: purpose,
                standardOutput: standardOutput,
                standardError: standardError
            )
            runningCommand = command
            process.terminationHandler = { [weak self, weak process] finished in
                guard let process else { return }
                let succeeded = finished.terminationReason == .exit
                    && finished.terminationStatus == 0
                DispatchQueue.main.async {
                    self?.finishCommand(process: process, succeeded: succeeded)
                }
            }
            try process.run()
        } catch {
            runningCommand = nil
            standardOutput.stop()
            standardError.stop()
            switch purpose {
            case .bridge(let action):
                complete(action: action, ok: false, message: "The action could not be started.")
            case .initialDashboard:
                showDashboardPreparationFailure()
            }
        }
    }

    private func finishCommand(process: Process, succeeded: Bool) {
        guard
            let command = runningCommand,
            command.process === process
        else {
            return
        }
        command.standardOutput.stop()
        command.standardError.stop()
        runningCommand = nil

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
        case .bridge(let action):
            finishBridgeCommand(action: action, succeeded: succeeded)
        }
    }

    private func finishBridgeCommand(action: BridgeAction, succeeded: Bool) {
        let message: String
        switch action {
        case .scan:
            message = succeeded ? "Refresh complete." : "The refresh could not be completed."
        case .status:
            message = succeeded ? "Application status updated." : "The status could not be updated."
        case .bookmark:
            message = succeeded ? "Bookmark updated." : "The bookmark could not be updated."
        case .theme:
            message = succeeded ? "Theme updated." : "The theme could not be updated."
        }

        complete(action: action, ok: succeeded, message: message) { [weak self] in
            if succeeded && action != .theme {
                self?.reloadDashboard(after: action)
            }
        }
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
        ok: Bool,
        message: String,
        completion: (() -> Void)? = nil
    ) {
        let payload: [String: Any] = [
            "action": action.rawValue,
            "ok": ok,
            "message": message,
        ]
        guard let json = jsonLiteral(payload) else {
            completion?()
            return
        }
        webView.evaluateJavaScript(
            "window.OpportunityRadarNative?.complete(\(json));"
        ) { _, _ in
            completion?()
        }
    }

    private func setPageTheme(_ theme: Theme) {
        guard let json = jsonLiteral(theme.rawValue) else { return }
        webView.evaluateJavaScript(
            "window.OpportunityRadarNative?.setTheme(\(json));",
            completionHandler: nil
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
