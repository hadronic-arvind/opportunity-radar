(function () {
  "use strict";

  const PAGE_SIZE = 36;
  const MAX_LOCAL_RECORDS = 500;
  const ID_PATTERN = /^[a-f0-9]{24}$/;
  const STATUS_VALUES = new Set(["new", "reviewed", "apply", "applied", "skip"]);
  const THEME_VALUES = new Set(["system", "light", "dark"]);
  const dataNode = document.getElementById("opportunity-data");
  let data = {opportunities: [], sources: [], runs: [], counts: {}, display: {}, settings: {}};
  try {
    const parsed = JSON.parse(dataNode ? dataNode.textContent : "{}");
    if (parsed && typeof parsed === "object") data = parsed;
  } catch (_error) {
    data = {opportunities: [], sources: [], runs: [], counts: {}, display: {}, settings: {}};
  }

  const settings = data.settings || {};
  const byId = new Map((data.opportunities || []).map((item) => [String(item.id), item]));
  const state = {
    view: "discover",
    filter: "all",
    query: "",
    sort: "fit",
    visibleItems: [],
    rendered: 0,
    busy: false,
    pendingRequest: null,
    workflow: loadWorkflow(),
  };
  let observer = null;
  let toastTimer = null;
  let requestSequence = 0;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function validId(value) {
    return ID_PATTERN.test(String(value || ""));
  }

  function loadWorkflow() {
    try {
      const parsed = JSON.parse(localStorage.getItem("opportunity-radar-workflow-v2") || "{}");
      const output = {};
      Object.entries(parsed || {}).slice(-MAX_LOCAL_RECORDS).forEach(([id, value]) => {
        if (!validId(id) || !value || typeof value !== "object") return;
        const status = STATUS_VALUES.has(value.status) ? value.status : undefined;
        output[id] = {status, bookmarked: Boolean(value.bookmarked)};
      });
      if (!Object.keys(output).length) {
        const saved = JSON.parse(localStorage.getItem("opportunity-radar-saved") || "[]");
        const dismissed = JSON.parse(localStorage.getItem("opportunity-radar-dismissed") || "[]");
        if (Array.isArray(saved)) {
          saved.slice(-MAX_LOCAL_RECORDS).forEach((id) => {
            if (validId(id)) output[id] = {bookmarked: true};
          });
        }
        if (Array.isArray(dismissed)) {
          dismissed.slice(-MAX_LOCAL_RECORDS).forEach((id) => {
            if (validId(id)) output[id] = Object.assign({}, output[id], {status: "skip"});
          });
        }
        if (Object.keys(output).length) {
          localStorage.setItem("opportunity-radar-workflow-v2", JSON.stringify(output));
        }
      }
      return output;
    } catch (_error) {
      return {};
    }
  }

  function persistWorkflow() {
    try {
      const entries = Object.entries(state.workflow).slice(-MAX_LOCAL_RECORDS);
      localStorage.setItem("opportunity-radar-workflow-v2", JSON.stringify(Object.fromEntries(entries)));
    } catch (_error) {
      // The native app uses a nonpersistent web data store and SQLite remains authoritative.
    }
  }

  function effective(item) {
    const local = state.workflow[item.id] || {};
    return {
      status: STATUS_VALUES.has(local.status) ? local.status : String(item.status || "new"),
      bookmarked: local.bookmarked === undefined ? Boolean(item.bookmarked) : local.bookmarked,
    };
  }

  function hasNativeBridge() {
    return Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.opportunityRadar);
  }

  function postNative(message) {
    if (!hasNativeBridge()) return false;
    try {
      window.webkit.messageHandlers.opportunityRadar.postMessage(Object.assign({version: 1}, message));
      return true;
    } catch (_error) {
      return false;
    }
  }

  function showToast(message) {
    const toast = document.getElementById("toast");
    toast.textContent = String(message || "").slice(0, 240);
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4800);
  }

  function setBusy(value, label, request) {
    state.busy = Boolean(value);
    state.pendingRequest = state.busy ? String(request || "") : null;
    document.getElementById("refresh-button").disabled = state.busy;
    document.getElementById("scan-all-button").disabled = state.busy;
    document.getElementById("scan-state").textContent = state.busy ? String(label || "Working...") : "";
    document.getElementById("opportunity-list").setAttribute("aria-busy", String(state.busy));
    document.querySelectorAll("#opportunity-list button[data-action]").forEach((button) => {
      button.disabled = state.busy;
    });
  }

  function nextRequest() {
    requestSequence = requestSequence >= 999999999 ? 1 : requestSequence + 1;
    return "r" + requestSequence;
  }

  function startNativeAction(message, label) {
    if (state.busy) {
      showToast("Wait for the current action to finish.");
      return false;
    }
    const request = nextRequest();
    setBusy(true, label, request);
    if (!postNative(Object.assign({}, message, {request}))) {
      setBusy(false);
      return false;
    }
    return true;
  }

  function scan(mode) {
    if (state.busy) {
      showToast("Wait for the current action to finish.");
      return;
    }
    if (!hasNativeBridge()) {
      showToast(mode === "all" ? "Terminal: python3 -m monitor scan --force" : "Terminal: python3 -m monitor scan");
      return;
    }
    startNativeAction(
      {action: "scan", mode},
      mode === "all" ? "Scanning all..." : "Refreshing..."
    );
  }

  function updateWorkflow(id, change) {
    if (!validId(id) || !byId.has(id)) return;
    const item = byId.get(id);
    if (hasNativeBridge()) {
      if (state.busy) {
        showToast("Wait for the current action to finish.");
        return;
      }
      let message = null;
      if (Object.prototype.hasOwnProperty.call(change, "status")) {
        if (STATUS_VALUES.has(change.status)) {
          message = {action: "status", id, status: change.status};
        }
      } else if (Object.prototype.hasOwnProperty.call(change, "bookmarked")) {
        message = {action: "bookmark", id, bookmarked: Boolean(change.bookmarked)};
      }
      if (!message || !startNativeAction(message, "Updating...")) {
        showToast("The update could not be sent to the app.");
      }
      return;
    }
    const previous = Object.assign({}, state.workflow[id] || {});
    const next = Object.assign({}, previous, change);
    state.workflow[id] = next;
    persistWorkflow();
    renderAll();
  }

  function safeUrl(value) {
    try {
      const parsed = new URL(String(value || ""));
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
    } catch (_error) {
      return "";
    }
  }

  function formatDate(value) {
    if (!value) return "No deadline listed";
    const raw = String(value);
    const date = new Date(raw.length === 10 ? raw + "T12:00:00" : raw);
    return Number.isNaN(date.valueOf()) ? raw.slice(0, 40) : date.toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"});
  }

  function relative(value) {
    if (!value) return "never";
    const time = new Date(value).valueOf();
    if (Number.isNaN(time)) return "unknown";
    const hours = Math.max(0, Math.round((Date.now() - time) / 3600000));
    if (hours < 1) return "just now";
    if (hours < 24) return hours + "h ago";
    const days = Math.round(hours / 24);
    return days + (days === 1 ? " day ago" : " days ago");
  }

  function normalizedType(item) {
    return String(item.opportunity_type || "opportunity").toLowerCase().replaceAll("_", " ");
  }

  function isApplication(status) {
    return status === "apply" || status === "applied";
  }

  function isAvailable(item) {
    return Boolean(item.active) && item.source_enabled !== 0 && item.tier !== "skip";
  }

  function filteredItems() {
    const query = state.query.toLowerCase();
    const items = (data.opportunities || []).filter((item) => {
      const workflow = effective(item);
      if (state.view === "discover" && isApplication(workflow.status)) return false;
      if (state.view === "discover" && !isAvailable(item) && !["saved", "dismissed"].includes(state.filter)) return false;
      if (state.view === "discover" && workflow.status === "skip" && state.filter !== "dismissed") return false;
      if (state.view === "applications" && !isApplication(workflow.status)) return false;
      if (state.filter === "saved" && !workflow.bookmarked) return false;
      if (state.filter === "dismissed" && workflow.status !== "skip") return false;
      if (state.filter === "priority" && item.tier !== "priority") return false;
      if (state.filter === "internship" && !normalizedType(item).includes("intern")) return false;
      if (state.filter === "fellowship" && !normalizedType(item).includes("fellow")) return false;
      if (state.filter === "job" && normalizedType(item) !== "job") return false;
      if (state.filter === "apply" && workflow.status !== "apply") return false;
      if (state.filter === "applied" && workflow.status !== "applied") return false;
      if (!query) return true;
      return [item.title, item.organization, item.location, item.description, item.category, item.opportunity_type, item.recommended_resume]
        .join(" ").toLowerCase().includes(query);
    });
    items.sort((left, right) => {
      if (state.sort === "newest") return String(right.first_seen_at || "").localeCompare(String(left.first_seen_at || ""));
      if (state.sort === "deadline") return String(left.deadline_at || "9999").localeCompare(String(right.deadline_at || "9999"));
      if (state.sort === "organization") return String(left.organization || "").localeCompare(String(right.organization || ""));
      if (state.view === "applications") {
        const leftStatus = effective(left).status === "apply" ? 0 : 1;
        const rightStatus = effective(right).status === "apply" ? 0 : 1;
        if (leftStatus !== rightStatus) return leftStatus - rightStatus;
      }
      return Number(right.score || 0) - Number(left.score || 0) || String(left.deadline_at || "9999").localeCompare(String(right.deadline_at || "9999"));
    });
    return items;
  }

  function addMeta(container, text) {
    if (text) container.appendChild(element("span", "", text));
  }

  function addTag(container, text, className) {
    if (text) container.appendChild(element("span", "tag" + (className ? " " + className : ""), text));
  }

  function cardFor(item) {
    const workflow = effective(item);
    const article = element("article", "opportunity");
    article.dataset.id = item.id;
    const head = element("div", "op-head");
    const titleGroup = element("div");
    titleGroup.appendChild(element("p", "organization", item.organization || "Organization not listed"));
    titleGroup.appendChild(element("h2", "", item.title || "Untitled opportunity"));
    const score = element("div", "fit-score " + (["priority", "strong", "watch", "skip"].includes(item.tier) ? item.tier : "watch"), Number(item.score || 0));
    score.setAttribute("aria-label", "Fit score " + Number(item.score || 0) + " out of 100");
    score.title = "Configured fit score, not acceptance probability";
    head.append(titleGroup, score);
    article.appendChild(head);

    const meta = element("div", "meta");
    addMeta(meta, item.location || "Location not listed");
    addMeta(meta, normalizedType(item));
    addMeta(meta, "Deadline: " + formatDate(item.deadline_at));
    if (workflow.status === "applied" && item.applied_at) addMeta(meta, "Applied " + formatDate(item.applied_at));
    addMeta(meta, item.commitment ? String(item.commitment) : "");
    article.appendChild(meta);

    const tags = element("div", "tags");
    if (item.recommended_resume) addTag(tags, (settings.document_label || "Application track") + ": " + item.recommended_resume, "track");
    if (workflow.status === "apply") addTag(tags, "Preparing application", "stage");
    if (workflow.status === "applied") addTag(tags, "Applied", "stage");
    if (!isAvailable(item)) addTag(tags, "Listing no longer active", "warning");
    const warning = Array.isArray(item.warnings) ? item.warnings[0] : "";
    if (warning) addTag(tags, String(warning).slice(0, 120), "warning");
    if (tags.childNodes.length) article.appendChild(tags);

    const reasons = Array.isArray(item.reasons) ? item.reasons.slice(0, 3).join(". ") : "";
    article.appendChild(element("p", "reason", reasons || settings.default_reason || "Matched by your configured preferences."));

    const actions = element("div", "card-actions");
    const official = safeUrl(item.url);
    if (official) {
      const link = element("a", "official-link", "Open listing");
      link.href = official;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      actions.appendChild(link);
    }
    const save = element("button", "card-button" + (workflow.bookmarked ? " selected" : ""), workflow.bookmarked ? "Saved" : "Save");
    save.type = "button";
    save.dataset.action = "bookmark";
    save.dataset.id = item.id;
    save.setAttribute("aria-pressed", String(workflow.bookmarked));
    actions.appendChild(save);

    if (workflow.status === "apply") {
      const applied = element("button", "card-button selected", "Mark applied");
      applied.type = "button";
      applied.dataset.action = "applied";
      applied.dataset.id = item.id;
      actions.appendChild(applied);
    } else if (workflow.status === "applied") {
      const planned = element("button", "card-button", "Move to preparing");
      planned.type = "button";
      planned.dataset.action = "apply";
      planned.dataset.id = item.id;
      actions.appendChild(planned);
    } else {
      const plan = element("button", "card-button", "Plan application");
      plan.type = "button";
      plan.dataset.action = "apply";
      plan.dataset.id = item.id;
      actions.appendChild(plan);
    }

    const isDismissed = workflow.status === "skip";
    const remove = element(
      "button",
      "card-button" + (isDismissed ? "" : " danger"),
      isDismissed ? "Restore" : state.view === "applications" ? "Remove from applications" : "Dismiss"
    );
    remove.type = "button";
    remove.dataset.action = isDismissed || state.view === "applications" ? "new" : "skip";
    remove.dataset.id = item.id;
    actions.appendChild(remove);
    actions.querySelectorAll("button[data-action]").forEach((button) => {
      button.disabled = state.busy;
    });
    article.appendChild(actions);
    return article;
  }

  function updateListMeta() {
    const items = state.visibleItems;
    const display = data.display || {};
    const countLabel = items.length + (items.length === 1 ? " opportunity" : " opportunities");
    document.getElementById("results-note").textContent = (
      display.discovery_truncated && state.view === "discover" && state.filter === "all" && !state.query
        ? countLabel + " loaded from " + Number(display.discovery_total || items.length) + ". Showing the highest-fit records."
        : countLabel
    );
    const zone = document.getElementById("load-zone");
    const remaining = Math.max(0, items.length - state.rendered);
    zone.hidden = remaining === 0;
    document.getElementById("load-note").textContent = remaining ? remaining + " more" : "";
    document.getElementById("load-more").disabled = remaining === 0;
    document.getElementById("clear-filters").hidden = !state.query && state.filter === "all";
  }

  function appendNextPage() {
    if (state.rendered >= state.visibleItems.length) {
      updateListMeta();
      installObserver();
      return;
    }
    const end = Math.min(state.rendered + PAGE_SIZE, state.visibleItems.length);
    const fragment = document.createDocumentFragment();
    state.visibleItems.slice(state.rendered, end).forEach((item) => {
      fragment.appendChild(cardFor(item));
    });
    document.getElementById("opportunity-list").appendChild(fragment);
    state.rendered = end;
    updateListMeta();
    installObserver();
  }

  function renderList() {
    if (observer) observer.disconnect();
    const list = document.getElementById("opportunity-list");
    state.visibleItems = filteredItems();
    state.rendered = 0;
    list.replaceChildren();
    if (!state.visibleItems.length) {
      const empty = element("div", "empty");
      empty.append(element("strong", "", state.view === "applications" ? "No applications here yet." : "Nothing matches this view."));
      empty.append(document.createTextNode(state.view === "applications" ? " Choose Plan application on a listing to start tracking it." : " Try a broader search or clear the filters."));
      list.appendChild(empty);
      updateListMeta();
      return;
    }
    appendNextPage();
  }

  function installObserver() {
    if (observer) observer.disconnect();
    if (!("IntersectionObserver" in window) || document.getElementById("load-zone").hidden) return;
    observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        appendNextPage();
      }
    }, {rootMargin: "500px 0px"});
    observer.observe(document.getElementById("load-zone"));
  }

  function filtersForView() {
    return state.view === "applications"
      ? [["all", "All"], ["apply", "Preparing"], ["applied", "Applied"], ["saved", "Saved"]]
      : [["all", "All"], ["saved", "Saved"], ["priority", "Priority"], ["internship", "Internships"], ["fellowship", "Fellowships"], ["job", "Jobs"], ["dismissed", "Dismissed"]];
  }

  function renderFilters() {
    const row = document.getElementById("filter-row");
    if (!filtersForView().some(([value]) => value === state.filter)) state.filter = "all";
    row.replaceChildren();
    filtersForView().forEach(([value, label]) => {
      const button = element("button", "filter-button" + (value === state.filter ? " active" : ""), label);
      button.type = "button";
      button.dataset.filter = value;
      button.setAttribute("aria-pressed", String(value === state.filter));
      row.appendChild(button);
    });
  }

  function renderCounts() {
    const all = data.opportunities || [];
    const open = all.filter((item) => item.active && item.tier !== "skip").length;
    const newest = all.filter((item) => effective(item).status === "new" && item.active).length;
    const saved = all.filter((item) => effective(item).bookmarked).length;
    const applications = all.filter((item) => isApplication(effective(item).status));
    const applied = applications.filter((item) => effective(item).status === "applied").length;
    document.getElementById("stat-active").textContent = String((data.counts || {}).active ?? open);
    document.getElementById("stat-new").textContent = String((data.counts || {}).new ?? newest);
    document.getElementById("stat-saved").textContent = String((data.counts || {}).bookmarked ?? saved);
    document.getElementById("stat-applied").textContent = String((data.counts || {}).applied ?? applied);
    document.getElementById("application-count").textContent = String(applications.length);
  }

  function renderSources() {
    const sources = data.sources || [];
    const errors = sources.filter((source) => source.last_status === "error");
    const blocked = sources.filter((source) => source.last_status === "blocked");
    const healthy = sources.filter((source) => source.last_status === "ok");
    const heading = errors.length ? errors.length + " need attention" : blocked.length ? blocked.length + " access-limited" : healthy.length ? "Sources are current" : "Ready for first scan";
    document.getElementById("health-summary").textContent = heading;
    document.getElementById("source-overview").textContent = sources.length
      ? healthy.length + " healthy, " + blocked.length + " limited, " + errors.length + " failed, " + (sources.length - healthy.length - blocked.length - errors.length) + " waiting."
      : "Choose a source pack to begin collecting opportunities.";
    const mark = document.getElementById("health-mark");
    mark.className = "health-mark" + (errors.length ? " attention" : healthy.length ? " ok" : "");
    const list = document.getElementById("source-list");
    list.replaceChildren();
    sources.forEach((source) => {
      const row = element("div", "source");
      const dot = element("i", "source-dot " + (["ok", "error", "blocked"].includes(source.last_status) ? source.last_status : "never"));
      const copy = element("div");
      copy.appendChild(element("div", "source-name", source.name || source.id));
      const count = Number(source.item_count || 0);
      copy.appendChild(element("span", "source-detail", count + (count === 1 ? " item" : " items") + " - " + relative(source.last_checked_at)));
      row.append(dot, copy);
      list.appendChild(row);
    });
  }

  function renderEvents() {
    const events = Array.isArray(data.events) ? data.events.slice(0, 6) : [];
    const section = document.getElementById("event-section");
    const list = document.getElementById("event-list");
    section.hidden = events.length === 0;
    list.replaceChildren();
    events.forEach((event) => {
      const url = safeUrl(event.url);
      const row = element(url ? "a" : "div", "event-link");
      if (url) {
        row.href = url;
        row.target = "_blank";
        row.rel = "noopener noreferrer";
      }
      row.appendChild(document.createTextNode(event.source_name || event.title || "Source changed"));
      row.appendChild(element("span", "event-time", relative(event.occurred_at)));
      list.appendChild(row);
    });
  }

  function renderAll() {
    renderFilters();
    renderCounts();
    renderList();
  }

  function setTheme(theme, notifyNative) {
    const value = THEME_VALUES.has(theme) ? theme : "system";
    document.documentElement.dataset.theme = value;
    document.getElementById("theme-select").value = value;
    try { localStorage.setItem("opportunity-radar-theme", value); } catch (_error) { /* optional */ }
    if (notifyNative) postNative({action: "theme", theme: value});
  }

  window.OpportunityRadarNative = {
    complete(result) {
      if (!result || String(result.request || "") !== state.pendingRequest) return;
      setBusy(false);
      if (result && result.message) showToast(result.message);
    },
    setTheme(theme) {
      setTheme(String(theme || "system"), false);
    },
  };

  const title = settings.title || "Opportunity Radar";
  document.title = title;
  document.getElementById("dashboard-title").textContent = title;
  document.getElementById("dashboard-subtitle").textContent = settings.subtitle || "Find and track opportunities from the sources you choose.";
  document.getElementById("search-context").textContent = settings.target_season ? settings.target_season + " search" : "Opportunity search";
  document.getElementById("last-scan").textContent = "Updated " + relative(data.generated_at);

  let initialTheme = "system";
  try { initialTheme = localStorage.getItem("opportunity-radar-theme") || "system"; } catch (_error) { /* optional */ }
  setTheme(initialTheme, false);

  document.getElementById("theme-select").addEventListener("change", (event) => setTheme(event.target.value, true));
  document.getElementById("refresh-button").addEventListener("click", () => scan("due"));
  document.getElementById("scan-all-button").addEventListener("click", () => {
    const dialog = document.getElementById("scan-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else if (window.confirm("Check every enabled source now?")) scan("all");
  });
  document.getElementById("scan-dialog").addEventListener("close", (event) => {
    if (event.target.returnValue === "confirm") scan("all");
  });
  document.getElementById("load-more").addEventListener("click", () => {
    appendNextPage();
  });
  document.getElementById("search").addEventListener("input", (event) => {
    state.query = String(event.target.value || "").trim();
    renderList();
  });
  document.getElementById("sort").addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderList();
  });
  document.getElementById("filter-row").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-filter]");
    if (!button) return;
    state.filter = button.dataset.filter;
    renderAll();
  });
  document.getElementById("clear-filters").addEventListener("click", () => {
    state.filter = "all";
    state.query = "";
    document.getElementById("search").value = "";
    renderAll();
  });
  document.querySelector(".view-switcher").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (!button || !["discover", "applications"].includes(button.dataset.view)) return;
    state.view = button.dataset.view;
    state.filter = "all";
    document.querySelectorAll(".view-button").forEach((entry) => {
      const active = entry === button;
      entry.classList.toggle("active", active);
      entry.setAttribute("aria-pressed", String(active));
    });
    renderAll();
  });
  document.getElementById("opportunity-list").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action][data-id]");
    if (!button || !validId(button.dataset.id)) return;
    const item = byId.get(button.dataset.id);
    if (!item) return;
    if (button.dataset.action === "bookmark") {
      updateWorkflow(item.id, {bookmarked: !effective(item).bookmarked});
    } else if (STATUS_VALUES.has(button.dataset.action)) {
      updateWorkflow(item.id, {status: button.dataset.action});
    }
  });

  renderSources();
  renderEvents();
  renderAll();
}());
