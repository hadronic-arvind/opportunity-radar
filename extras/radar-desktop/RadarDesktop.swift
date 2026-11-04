import AppKit
import Foundation
import UniformTypeIdentifiers
import WebKit

private let maximumRequests = 4
private let maximumRequestBytes = 2 * 1024 * 1024
private let maximumResponseBytes = 8 * 1024 * 1024

private struct DesktopConfiguration: Decodable {
    let projectRoot: String
    let python: String
}

private final class Request {
    let process: Process
    let input: Pipe
    var cancelled = false
    var timedOut = false

    init(_ process: Process, input: Pipe) {
        self.process = process
        self.input = input
    }
}

private final class Desktop: NSObject, NSApplicationDelegate, NSWindowDelegate,
    WKNavigationDelegate, WKScriptMessageHandler
{
    private var window: NSWindow!
    private var web: WKWebView!
    private var configuration: DesktopConfiguration!
    private var interface: URL!
    private var requests: [String: Request] = [:]
    private var panel: NSSavePanel?
    private var terminating = false
    private let environment = ProcessInfo.processInfo.environment
    private var smoke: Bool { environment["RADAR_DESKTOP_SMOKE"] == "1" }

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            guard let resource = Bundle.main.url(forResource: "desktop", withExtension: "json")
            else { throw CocoaError(.fileNoSuchFile) }
            configuration = try JSONDecoder().decode(
                DesktopConfiguration.self, from: Data(contentsOf: resource)
            )
            interface = URL(fileURLWithPath: configuration.projectRoot)
                .appendingPathComponent("radar/desktop/index.html").standardizedFileURL
            guard FileManager.default.isReadableFile(atPath: interface.path),
                FileManager.default.isExecutableFile(atPath: configuration.python)
            else { throw CocoaError(.fileNoSuchFile) }
        } catch {
            failStartup("The Opportunity Radar checkout or its Python runtime is unavailable.")
            return
        }

        configureMenu()
        let browser = WKWebViewConfiguration()
        browser.websiteDataStore = .nonPersistent()
        browser.userContentController.add(self, name: "radar")
        web = WKWebView(frame: .zero, configuration: browser)
        web.navigationDelegate = self
        web.allowsBackForwardNavigationGestures = false

        let size = initialWindowSize()
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: size.width, height: size.height),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false
        )
        window.title = "Opportunity Radar"
        window.contentMinSize = NSSize(width: 680, height: 500)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        web.loadFileURL(interface, allowingReadAccessTo: interface.deletingLastPathComponent())
        if environment["RADAR_DESKTOP_WINDOW_NUMBER"] == "1" {
            FileHandle.standardOutput.write(Data(("Window " + String(window.windowNumber) + "\n").utf8))
        }

        if smoke {
            DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
                self?.failStartup("The desktop bridge did not become ready.")
            }
        }
    }

    private func initialWindowSize() -> NSSize {
        if let setting = environment["RADAR_DESKTOP_WINDOW_SIZE"] {
            let sizes = setting.split(separator: "x").compactMap { Double($0) }
            if sizes.count == 2, (680...2000).contains(sizes[0]), (500...1400).contains(sizes[1]) {
                return NSSize(width: sizes[0], height: sizes[1])
            }
        }
        let visible = NSScreen.main?.visibleFrame.size ?? NSSize(width: 1200, height: 800)
        return NSSize(
            width: min(1180, max(680, visible.width - 32)),
            height: min(780, max(500, visible.height - 60))
        )
    }

    private func configureMenu() {
        let menuBar = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(
            withTitle: "Quit Opportunity Radar", action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        appItem.submenu = appMenu
        menuBar.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(
            withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"
        )
        editItem.submenu = editMenu
        menuBar.addItem(editItem)

        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(
            withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)),
            keyEquivalent: "w"
        )
        windowMenu.addItem(
            withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)),
            keyEquivalent: "m"
        )
        windowItem.submenu = windowMenu
        menuBar.addItem(windowItem)
        NSApp.windowsMenu = windowMenu
        NSApp.mainMenu = menuBar
    }

    private func isInterface(_ url: URL?) -> Bool {
        guard let url, url.isFileURL else { return false }
        return url.standardizedFileURL.path == interface.path
    }

    func webView(
        _ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        let allow = action.targetFrame?.isMainFrame == true && isInterface(action.request.url)
        decisionHandler(allow ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard smoke else { return }
        webView.evaluateJavaScript("""
            if (typeof window.radarReceive !== 'function') {
                throw new Error('Desktop JavaScript did not load.');
            }
            window.webkit.messageHandlers.radar.postMessage({
                id: 'native-smoke', op: 'ping', args: {}
            });
            true;
            """) { [weak self] _, error in
                if let error {
                    let detail = (error as NSError).userInfo["WKJavaScriptExceptionMessage"]
                        as? String ?? error.localizedDescription
                    self?.failStartup("The desktop JavaScript smoke test failed: " + detail)
                }
            }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        failStartup("The local Opportunity Radar interface could not be opened.")
    }

    func userContentController(
        _ controller: WKUserContentController, didReceive message: WKScriptMessage
    ) {
        guard message.frameInfo.isMainFrame, isInterface(message.frameInfo.request.url),
            !terminating, let payload = message.body as? [String: Any],
            let id = payload["id"] as? String, !id.isEmpty, id.utf8.count <= 100,
            let op = payload["op"] as? String, !op.isEmpty, op.utf8.count <= 64,
            let args = payload["args"] as? [String: Any],
            requests[id] == nil
        else { return }

        switch op {
        case "cancel":
            let existing = (args["id"] as? String).flatMap { requests[$0] }
            if let existing { stop(existing, cancelled: true, grace: 3) }
            reply(id, result: existing != nil)
        case "pickPath":
            pickPath(id, args: args)
        case "openExternal":
            guard let address = args["url"] as? String, let url = Self.externalURL(address)
            else { reply(id, error: "Choose a public HTTPS link."); return }
            let opened = NSWorkspace.shared.open(url)
            reply(id, result: opened, error: opened ? nil : "The link could not be opened.")
        case "openLegacy":
            openLegacy(id)
        default:
            launch(id, op: op, args: args)
        }
    }

    private func pickPath(_ id: String, args: [String: Any]) {
        guard panel == nil, let mode = args["mode"] as? String,
            ["open", "save", "directory"].contains(mode)
        else { reply(id, error: "A file picker is already open or the mode is unsupported."); return }
        let picker: NSSavePanel
        if mode == "save" {
            picker = NSSavePanel()
            picker.canCreateDirectories = true
            if let name = args["suggestedName"] as? String,
                !name.contains("/"), name.utf8.count <= 200
            {
                picker.nameFieldStringValue = name
            }
        } else {
            let open = NSOpenPanel()
            open.canChooseDirectories = mode == "directory"
            open.canChooseFiles = mode == "open"
            open.allowsMultipleSelection = false
            picker = open
        }
        if let title = args["title"] as? String { picker.title = String(title.prefix(150)) }
        if mode != "directory", let ext = args["extension"] as? String,
            ext.range(of: #"^[A-Za-z0-9]{1,16}$"#, options: .regularExpression) != nil,
            let type = UTType(filenameExtension: ext)
        {
            picker.allowedContentTypes = [type]
        }
        panel = picker
        picker.beginSheetModal(for: window) { [weak self, weak picker] result in
            self?.panel = nil
            self?.reply(id, result: result == .OK ? picker?.url?.path : nil)
        }
    }

    private static func externalURL(_ address: String) -> URL? {
        guard address.utf8.count <= 2048, !address.contains("\\"),
            address.unicodeScalars.allSatisfy({
                $0.value > 32 && !(127...159).contains($0.value)
            }),
            let parts = URLComponents(string: address), parts.scheme?.lowercased() == "https",
            parts.user == nil, parts.password == nil, parts.port == nil || parts.port == 443,
            let host = parts.percentEncodedHost?.lowercased(),
            !host.contains("%"), !host.contains(":")
        else { return nil }
        let trimmed = host.hasSuffix(".") ? String(host.dropLast()) : host
        let labels = trimmed.split(separator: ".", omittingEmptySubsequences: false)
        guard labels.count >= 2,
            labels.allSatisfy({
                $0.range(of: #"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"#,
                         options: .regularExpression) != nil
            }),
            let last = labels.last, !["local", "internal", "localhost", "invalid"].contains(String(last)),
            last.first?.isLetter == true, !last.hasPrefix("0x")
        else { return nil }
        return parts.url
    }

    private func openLegacy(_ id: String) {
        let project = URL(fileURLWithPath: configuration.projectRoot)
        let script = project.appendingPathComponent("scripts/open_radar.sh")
        let process = Process()
        process.executableURL = script
        process.currentDirectoryURL = project
        process.environment = childEnvironment()
        process.arguments = []
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            reply(id, result: true)
        } catch {
            reply(id, error: "The classic workspace could not be launched.")
        }
    }

    private func childEnvironment() -> [String: String] {
        var result = [
            "HOME": NSHomeDirectory(), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LC_CTYPE": "UTF-8", "PYTHONUTF8": "1"
        ]
        for key in ["RADAR_WORKSPACE", "TMPDIR"] {
            if let value = environment[key] { result[key] = value }
        }
        return result
    }

    private func launch(_ id: String, op: String, args: [String: Any]) {
        guard requests.count < maximumRequests
        else { reply(id, error: "Too many actions are running. Try again in a moment."); return }
        guard var body = try? JSONSerialization.data(withJSONObject: ["op": op, "args": args]),
            body.count < maximumRequestBytes
        else { reply(id, error: "The request is too large or could not be read."); return }
        body.append(10)

        let input = Pipe()
        let output = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: configuration.python)
        process.currentDirectoryURL = URL(fileURLWithPath: configuration.projectRoot)
        process.arguments = ["-B", "-s", "-m", "radar.desktop_bridge"]
        var childEnv = childEnvironment()
        childEnv["RADAR_DESKTOP_WATCH_OWNER"] = "1"
        process.environment = childEnv
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice

        let request = Request(process, input: input)
        do {
            try process.run()
        } catch {
            reply(id, error: "The local workspace service could not start.")
            return
        }
        requests[id] = request

        let budget: TimeInterval = ["refresh", "collect", "migrate", "restore", "backup"].contains(op)
            ? 155 : 30
        DispatchQueue.main.asyncAfter(deadline: .now() + budget) { [weak self, weak request] in
            guard let self, let request, self.requests[id] === request else { return }
            request.timedOut = true
            self.stop(request, cancelled: false, grace: 0.5)
        }
        let bytes = body
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var data = Data()
            var tooLarge = false
            do {
                try input.fileHandleForWriting.write(contentsOf: bytes)
                while let chunk = try output.fileHandleForReading.read(upToCount: 65_536),
                    !chunk.isEmpty
                {
                    if data.count + chunk.count > maximumResponseBytes {
                        tooLarge = true
                        break
                    }
                    data.append(chunk)
                }
            } catch {
                // A canceled or crashed request can close either end of its pipe.
            }
            try? input.fileHandleForWriting.close()
            try? output.fileHandleForReading.close()
            process.waitUntilExit()

            let response = data
            let exceeded = tooLarge
            DispatchQueue.main.async { [weak self] in
                self?.finished(id, request: request, response: response, tooLarge: exceeded)
            }
        }
    }

    private func stop(_ request: Request, cancelled: Bool, grace: TimeInterval) {
        guard request.process.isRunning else { return }
        request.cancelled = request.cancelled || cancelled
        request.process.interrupt()
        DispatchQueue.main.asyncAfter(deadline: .now() + grace) { [weak request] in
            guard let request, request.process.isRunning else { return }
            kill(request.process.processIdentifier, SIGKILL)
        }
    }

    private func finished(_ id: String, request: Request, response: Data, tooLarge: Bool) {
        guard requests[id] === request else { return }
        requests[id] = nil
        if terminating {
            if requests.isEmpty { NSApp.reply(toApplicationShouldTerminate: true) }
            return
        }
        if !tooLarge, let object = (try? JSONSerialization.jsonObject(with: response)) as? [String: Any] {
            if object["ok"] as? Bool == true {
                reply(id, result: object["result"])
            } else {
                reply(id, error: object["error"] as? String ?? "The workspace could not finish that action.")
            }
        } else if request.timedOut {
            reply(id, error: "The workspace took too long to respond.")
        } else if request.cancelled {
            reply(id, error: "Action canceled.")
        } else {
            reply(id, error: tooLarge ? "The response exceeded its size limit." :
                  "The local workspace service closed unexpectedly.")
        }
    }

    private func reply(_ id: String, result: Any? = nil, error: String? = nil) {
        if smoke && id == "native-smoke" {
            guard error == nil, let info = result as? [String: Any], info["ready"] as? Bool == true
            else { failStartup("The desktop bridge failed its readiness check."); return }
            print("Radar desktop loaded its local interface and verified its workspace bridge.")
            exit(EXIT_SUCCESS)
        }
        guard !terminating else { return }
        web.callAsyncJavaScript(
            "window.radarReceive(id, result, error)",
            arguments: ["id": id, "result": result ?? NSNull(), "error": error as Any? ?? NSNull()],
            in: nil, in: .page, completionHandler: nil
        )
    }

    private func failStartup(_ text: String) {
        if smoke {
            FileHandle.standardError.write(Data((text + "\n").utf8))
            exit(EXIT_FAILURE)
        }
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Opportunity Radar could not open"
        alert.informativeText = text
        alert.runModal()
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !requests.isEmpty else { return .terminateNow }
        terminating = true
        for request in requests.values { stop(request, cancelled: true, grace: 2) }
        return .terminateLater
    }
}

signal(SIGPIPE, SIG_IGN)
let application = NSApplication.shared
private let desktop = Desktop()
application.setActivationPolicy(.regular)
application.delegate = desktop
application.run()
