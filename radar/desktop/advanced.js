(() => {
"use strict";

const localKinds = new Set(["legacy", "manual", "sample"]);
const labels = {
    complete: "Complete",
    partial: "Partial",
    failed: "Failed",
    timed_out: "Timed out",
    cancelled: "Cancelled",
    imported: "Imported",
};

let running = null;
let refreshTools = null;

function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function button(text, action, primary = false) {
    const node = make("button", text, primary ? "button primary" : "button secondary");
    node.type = "button";
    node.addEventListener("click", action);
    return node;
}

function dateLabel(value) {
    if (!value) return "Never";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Unknown date" :
        new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "short"}).format(date);
}

function itemList() {
    return make("ul", "", "tools-list");
}

function row(title, summary, actions = []) {
    const item = make("li", "", "tools-row");
    const text = make("div", "", "tools-row-text");
    text.append(make("h3", title), make("p", summary, "tools-meta"));
    item.append(text);
    if (actions.length) {
        const controls = make("div", "", "tools-actions");
        controls.append(...actions);
        item.append(controls);
    }
    return item;
}

function receiptText(receipt) {
    const state = labels[receipt.state] || "Unknown state";
    const count = Number(receipt.seen) || 0;
    return `${state} · ${count.toLocaleString()} observed · ${dateLabel(receipt.finished_at)}`;
}

function sourceDescription(source) {
    const coverage = [...(source.regions || []), ...(source.opportunity_types || []).map(
        (kind) => kind.replaceAll("_", " ")
    )];
    return coverage.join(" · ");
}

function empty(text) {
    return make("p", text, "tools-empty");
}

function section(title, description) {
    const content = make("section", "", "tools-section");
    content.append(make("h2", title), make("p", description, "tools-meta"));
    return content;
}

function fileControls(call, notice, openLegacy, nativeFiles, openProfiles) {
    const content = section("Workspace files", "Back up or recover your workspace, or bring in profiles and listings.");
    const list = itemList();
    const confirmation = make("div", "", "tools-confirm");
    confirmation.hidden = true;
    const buttons = [];
    let busy = false;

    function lock(value) {
        busy = value;
        buttons.forEach((control) => { control.disabled = value; });
    }

    if (openProfiles) {
        list.append(row("Import profiles", "Review and add feed profiles from a JSON file or an AI draft.",
            [button("Import profiles…", openProfiles)]));
    }

    async function complete(operation, args, message) {
        try {
            await call(operation, args);
            notice(message);
        } catch (error) {
            notice(error?.message || "Could not complete the workspace operation.");
        } finally {
            lock(false);
        }
    }

    for (const task of [
        {
            title: "Create a backup",
            copy: "Save a portable copy of your listings, goals, and tracking history.",
            label: "Back up…",
            op: "backup",
            picker: {mode: "save", title: "Save Opportunity Radar backup", extension: "sqlite3", suggestedName: "opportunity-radar-backup.sqlite3"},
            success: "Backup saved.",
        },
        {
            title: "Import listings",
            copy: "Add opportunities from a Radar listings JSON file.",
            label: "Import…",
            op: "importListings",
            picker: {mode: "open", title: "Select a listings file", extension: "json"},
            success: "Listings imported. Reopen For You to see the updated feed.",
        },
        {
            title: "Restore a backup",
            copy: "Replace this workspace with a verified Opportunity Radar backup.",
            label: "Restore…",
            op: "restore",
            picker: {mode: "open", title: "Select an Opportunity Radar backup", extension: "sqlite3"},
            success: "Backup restored. Reopen For You to see the recovered feed.",
        },
    ]) {
        if (nativeFiles === false) break;
        const control = button(task.label, async () => {
            if (busy) return;
            lock(true);
            let path;
            try {
                path = await call("pickPath", task.picker);
            } catch (error) {
                lock(false);
                notice(error?.message || "Could not open the file picker.");
                return;
            }
            if (typeof path !== "string" || !path) {
                lock(false);
                return;
            }
            if (task.op !== "restore") {
                await complete(task.op, {path}, task.success);
                return;
            }
            if (!confirmation.isConnected) {
                lock(false);
                return;
            }
            const cancel = button("Cancel", () => {
                confirmation.hidden = true;
                lock(false);
                control.focus();
            });
            const restore = button("Replace workspace", async () => {
                confirmation.hidden = true;
                await complete(task.op, {path}, task.success);
                if (control.isConnected) control.focus();
            }, true);
            confirmation.replaceChildren(
                make("h3", "Replace your current workspace?"),
                make("p", "This will replace your current listings, goals, and tracking history with the selected backup."),
                cancel,
                restore,
            );
            confirmation.hidden = false;
            cancel.focus();
        });
        buttons.push(control);
        list.append(row(task.title, task.copy, [control]));
    }

    if (nativeFiles === false) {
        list.append(row("Local files", "Back up, import, and restore from the full workspace."));
    }
    if (openLegacy) {
        list.append(row(
            "Full workspace",
            "Open the detailed tools for legacy imports, custom sources, and recovery.",
            [button("Open full workspace", async () => {
                try {
                    await openLegacy();
                } catch (error) {
                    notice(error?.message || "Could not open the full workspace.");
                }
            })],
        ));
    }
    content.append(list, confirmation);
    return content;
}

function renderTools(root, {call, notice, openLegacy, nativeFiles, openProfiles}) {
    const view = make("div", "", "tools-view");
    const tabs = make("nav", "", "tools-tabs");
    tabs.setAttribute("aria-label", "Workspace tools");
    const panel = make("div", "", "tools-panel");
    const status = make("p", "", "tools-note");
    status.setAttribute("role", "status");
    const tabButtons = {};
    let active = "sources";
    let data = null;
    let problem = "";
    let query = "";
    let showArchived = false;
    let request = 0;
    let changing = false;

    function begin(pending) {
        status.textContent = pending || "";
        panel.setAttribute("aria-busy", pending ? "true" : "false");
    }

    function paint() {
        if (!view.isConnected) return;
        for (const [name, tab] of Object.entries(tabButtons)) {
            tab.setAttribute("aria-pressed", String(name === active));
        }
        if (active === "data") {
            panel.replaceChildren(fileControls(call, notice, openLegacy, nativeFiles, openProfiles));
            return;
        }
        if (problem) {
            const explanation = empty(problem);
            explanation.setAttribute("role", "alert");
            panel.replaceChildren(explanation, button("Try again", load));
            return;
        }
        if (!data) {
            panel.replaceChildren(empty("Loading workspace tools…"));
            return;
        }
        const sourceNames = new Map(data.configured.map((source) => [source.id, source.name || source.id]));
        if (active === "activity") {
            const sectionBody = section("Collection activity", "See which sources completed, returned partial results, or need another try.");
            const list = itemList();
            for (const receipt of data.receipts) {
                const entry = row(sourceNames.get(receipt.source_id) || receipt.source_id, receiptText(receipt));
                const totals = [
                    ["new", "new"], ["updated", "updated"], ["duplicates", "duplicates"],
                ].filter(([field]) => receipt[field] !== undefined).map(
                    ([field, label]) => `${(Number(receipt[field]) || 0).toLocaleString()} ${label}`
                );
                if (totals.length) entry.firstChild.append(make("p", totals.join(" · "), "tools-meta"));
                if (receipt.message) entry.firstChild.append(make("p", receipt.message, "tools-meta"));
                list.append(entry);
            }
            sectionBody.append(data.receipts.length ? list : empty("No collection runs yet."));
            panel.replaceChildren(sectionBody);
            return;
        }

        const sectionBody = section("Sources", "Choose which publishers supply your feed. Collection only runs when you ask.");
        const form = make("div", "", "tools-actions");
        const filter = make("input");
        filter.type = "search";
        filter.placeholder = "Filter sources";
        filter.setAttribute("aria-label", "Filter sources");
        filter.value = query;
        const archiveLabel = make("label", "", "tools-check");
        const archiveToggle = make("input");
        archiveToggle.type = "checkbox";
        archiveToggle.checked = showArchived;
        archiveLabel.append(archiveToggle, document.createTextNode(" Include archived"));
        form.append(filter, archiveLabel);
        const list = itemList();
        const merged = new Map(data.catalog.map((source) => [source.id, {...source, catalog: true}]));
        for (const source of data.configured) {
            merged.set(source.id, {...merged.get(source.id), ...source, configured: true});
        }
        const sources = Array.from(merged.values()).sort((left, right) => {
            const leftPriority = left.enabled ? 0 : (localKinds.has(left.kind) ? 2 : 1);
            const rightPriority = right.enabled ? 0 : (localKinds.has(right.kind) ? 2 : 1);
            return leftPriority - rightPriority || (left.name || left.id).localeCompare(right.name || right.id);
        });
        const archived = sources.filter((source) => source.kind === "legacy").length;
        const listStatus = make("p", "", "tools-meta");
        const runInfo = make("div", "", "tools-run");
        if (running) {
            runInfo.append(make("span", `Checking ${running.name}…`));
            const cancel = button("Cancel collection", () => {
                if (running) {
                    running.controller.abort();
                    cancel.disabled = true;
                    notice("Cancellation requested.");
                }
            });
            runInfo.append(cancel);
        }

        function refreshList() {
            list.replaceChildren();
            const match = query.trim().toLocaleLowerCase();
            const matching = sources.filter((source) => {
                if (source.kind === "legacy" && !showArchived) return false;
                const name = `${source.name || ""} ${source.id} ${source.kind}`.toLocaleLowerCase();
                return !match || name.includes(match);
            });
            for (const source of matching) {
                const local = localKinds.has(source.kind);
                const actions = [];
                let state = "Available in catalog";
                if (source.kind === "legacy") state = "Archived · listings remain in your workspace";
                else if (local) state = "Local listings";
                else if (source.configured) state = source.enabled ? "Enabled" : "Disabled";
                if (source.last_receipt) state += ` · ${receiptText(source.last_receipt)}`;
                if (!local) {
                    const enabled = !source.enabled;
                    const toggleLabel = source.enabled ? "Disable" : source.configured ? "Enable" : "Add source";
                    const toggle = button(toggleLabel, async () => {
                        if (changing || running) return;
                        changing = true;
                        toggle.disabled = true;
                        try {
                            await call("addSource", {id: source.id, enabled});
                            notice(`${source.name || source.id} ${enabled ? "enabled" : "disabled"}.`);
                        } catch (error) {
                            notice(error?.message || "Could not update this source.");
                        } finally {
                            changing = false;
                            await load();
                        }
                    });
                    toggle.setAttribute("aria-label", `${toggleLabel} ${source.name || source.id}`);
                    toggle.disabled = changing || Boolean(running);
                    actions.push(toggle);
                }
                if (!local && source.enabled) {
                    const collect = button("Collect", async () => {
                        if (running || changing) return;
                        const controller = new AbortController();
                        running = {name: source.name || source.id, controller};
                        paint();
                        view.querySelector(".tools-run button")?.focus();
                        try {
                            const receipt = await call("collect", {id: source.id, seconds: 15}, {signal: controller.signal});
                            notice(receipt?.state ? receiptText(receipt) : "Collection finished.");
                        } catch (error) {
                            if (error?.name !== "AbortError") {
                                notice(error?.message || "Collection failed.");
                            }
                        } finally {
                            running = null;
                            await refreshTools?.();
                        }
                    }, true);
                    collect.setAttribute("aria-label", `Collect from ${source.name || source.id}`);
                    collect.disabled = changing || Boolean(running);
                    actions.push(collect);
                }
                const entry = row(source.name || source.id, state, actions);
                const coverage = sourceDescription(source);
                if (coverage && !local) entry.firstChild.append(make("p", coverage, "tools-meta"));
                list.append(entry);

            }
            const summary = [];
            if (!showArchived && archived) summary.push(`${archived.toLocaleString()} archived sources hidden`);
            if (match || showArchived) summary.push(`${matching.length.toLocaleString()} sources shown`);
            listStatus.textContent = summary.join(" · ");
            if (!matching.length) list.append(row("No matching sources", "Change your search or show archived sources."));
        }

        filter.addEventListener("input", () => {
            query = filter.value;
            refreshList();
        });
        archiveToggle.addEventListener("change", () => {
            showArchived = archiveToggle.checked;
            refreshList();
        });
        refreshList();
        sectionBody.append(form, listStatus, runInfo, list);
        panel.replaceChildren(sectionBody);
    }

    async function load() {
        if (!view.isConnected) return;
        const version = ++request;
        begin("Loading workspace tools…");
        try {
            const result = await call("sources", {});
            if (version !== request) return;
            data = {
                configured: result?.configured || [],
                catalog: result?.catalog || [],
                receipts: result?.receipts || [],
            };
            problem = "";
        } catch (error) {
            if (version !== request) return;
            problem = error?.message || "Could not load sources.";
        } finally {
            if (version === request) {
                begin("");
                if (active !== "data") paint();
            }
        }
    }

    for (const [name, title] of [["sources", "Sources"], ["activity", "Activity"], ["data", "Data"]]) {
        const tab = button(title, () => {
            active = name;
            paint();
        });
        tabButtons[name] = tab;
        tabs.append(tab);
    }
    view.append(tabs, status, panel);
    root.replaceChildren(view);
    refreshTools = load;
    paint();
    void load();
}

window.renderTools = renderTools;
})();
