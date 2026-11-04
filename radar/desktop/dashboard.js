"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const feed = byId("feed");
  const rail = byId("context");
  const drawer = byId("drawer");
  const content = byId("drawer-content");
  const status = byId("status");
  const bridge = window.webkit?.messageHandlers?.radar;
  const pending = new Map();
  let requestId = 0;
  let timer;
  let undoAction = null;
  let loadVersion = 0;
  let drawerVersion = 0;
  let returnFocus;
  let returnListing;
  let openingId;
  let feedRequest;
  const compareIds = new Set();
  const viewFilters = new Map();
  const state = {view:"for-you", kind:"", query:"", draft:"", data:null, items:[], loading:false, error:""};
  const tabs = [
    ["for-you", "For You", "spark"], ["explore", "Explore", "compass"],
    ["saved", "Saved", "bookmark"], ["applications", "Applications", "case"],
    ["tools", "Tools", "sliders"]
  ];
  const types = [["", "All"], ["job", "Jobs"], ["internship", "Internships"],
    ["fellowship", "Fellowships"], ["grant", "Funding"]];
  const statusNames = {new:"New", reviewed:"Reviewed", apply:"Plan to apply", applied:"Applied", skip:"Dismissed"};
  const paths = {
    spark:"M12 2l1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2z M20 15v6 M17 18h6",
    compass:"M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M16 8l-2.5 5.5L8 16l2.5-5.5L16 8z",
    bookmark:"M6 4h12v16l-6-4-6 4z",
    case:"M3 7h18v13H3z M8 7V4h8v3 M3 12h18 M10 12v2h4v-2",
    sliders:"M4 7h16 M4 17h16 M8 4v6 M16 14v6",
    hide:"M6 6l12 12 M18 6L6 18",
    arrow:"M5 12h14 M13 6l6 6-6 6",
    refresh:"M20 7v5h-5 M19 11a7 7 0 1 0-1 6",
    check:"M5 12l4 4L19 6",
    pin:"M12 22s6-6 6-12a6 6 0 0 0-12 0c0 6 6 12 6 12z M12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"
  };

  function icon(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    const path = document.createElementNS(ns, "path");
    for (const [key, value] of Object.entries({viewBox:"0 0 24 24", width:"18", height:"18", fill:"none",
      stroke:"currentColor", "stroke-width":"1.65", "stroke-linecap":"round", "stroke-linejoin":"round",
      "aria-hidden":"true"})) svg.setAttribute(key,value);
    path.setAttribute("d", paths[name]);
    svg.append(path);
    return svg;
  }

  function el(tag, attrs = {}, ...children) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key.startsWith("on")) element.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === "value") element.value = value;
      else if (key === "checked" || key === "disabled" || key === "selected") element[key] = Boolean(value);
      else element.setAttribute(key, String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child !== null && child !== undefined && child !== false) element.append(child);
    }
    return element;
  }

  function button(label, style, action, symbol, attrs = {}) {
    return el("button", {type:"button", class:style, onClick:action, ...attrs}, symbol ? icon(symbol) : null, label);
  }

  function call(op, args = {}, options = {}) {
    if (!bridge) return Promise.reject(new Error("Open Opportunity Radar from its desktop app."));
    const id = String(++requestId);
    return new Promise((resolve, reject) => {
      if (options.signal?.aborted) {
        reject(new DOMException("Cancelled", "AbortError"));
        return;
      }
      const abort = () => {
        pending.delete(id);
        bridge.postMessage({id:String(++requestId),op:"cancel",args:{id}});
        reject(new DOMException("Cancelled", "AbortError"));
      };
      pending.set(id, {
        resolve:(result) => { options.signal?.removeEventListener("abort", abort); resolve(result); },
        reject:(error) => { options.signal?.removeEventListener("abort", abort); reject(error); }
      });
      options.signal?.addEventListener("abort", abort, {once:true});
      try { bridge.postMessage({id,op,args}); }
      catch (error) {
        pending.get(id)?.reject(error);
        pending.delete(id);
      }
    });
  }

  window.radarReceive = (id, result, error) => {
    const request = pending.get(String(id));
    if (!request) return;
    pending.delete(String(id));
    if (error) request.reject(new Error(typeof error === "string" ? error : "The operation could not finish."));
    else request.resolve(result);
  };

  function notice(message, undo) {
    clearTimeout(timer);
    const action = typeof undo === "function" ? undo : null;
    undoAction = action;
    const label = el("span", {}, message);
    const localStatus = drawer.open ? byId("drawer-notice") : null;
    if (localStatus && !action) {
      localStatus.replaceChildren(label);
      localStatus.hidden = false;
      timer = setTimeout(() => { localStatus.hidden = true; }, 4200);
      return;
    }
    status.replaceChildren(label);
    if (action) status.append(button("Undo", "", async () => {
      const restore = undoAction;
      undoAction = null;
      try { await restore(); notice("Opportunity restored."); }
      catch (error) { notice(error.message); }
    }));
    status.hidden = false;
    timer = setTimeout(() => { status.hidden = true; undoAction = null; }, action ? 12000 : 4200);
  }

  function renderNav() {
    const nav = byId("nav");
    if (!nav.childElementCount) {
      const buttons = tabs.map(([id, label, symbol]) =>
        button(label,"nav-button",() => navigate(id),symbol,{"data-view":id}));
      nav.replaceChildren(el("div",{class:"nav-group"},buttons.slice(0,4)),
        el("div",{class:"nav-divider"}), el("div",{class:"nav-group"},buttons.slice(4)));
    }
    for (const control of nav.querySelectorAll("[data-view]")) {
      if (control.dataset.view === state.view) control.setAttribute("aria-current","page");
      else if (state.view === "hidden" && control.dataset.view === "saved") control.setAttribute("aria-current","location");
      else control.removeAttribute("aria-current");
    }
    byId("breadcrumb").replaceChildren("Workspace ", el("span",{"aria-hidden":"true"},"/"),
      " " + (tabs.find(([id]) => id === state.view)?.[1] || "Hidden"));
  }

  function navigate(view, focusSearch = false) {
    if (state.view !== view) {
      viewFilters.set(state.view,{kind:state.kind,query:state.query,draft:state.draft});
      state.view = view;
      const filters = viewFilters.get(view);
      state.kind = filters?.kind || "";
      state.query = filters?.query || "";
      state.draft = filters?.draft || "";
      state.data = null;
      state.items = [];
      state.error = "";
      window.scrollTo(0,0);
    }
    renderNav();
    if (view === "tools") {
      ++loadVersion;
      feedRequest?.abort();
      state.loading = false;
      byId("layout").classList.add("single");
      rail.replaceChildren();
      if (window.renderTools) window.renderTools(feed, {call,el,notice,openLegacy,nativeFiles:!!bridge,
        openProfiles:() => openProfiles()});
      else feed.replaceChildren(empty("Tools", "Use the full workspace to manage your sources.", button("Open full workspace","primary",openLegacy)));
      return;
    }
    if (view === "explore" && focusSearch) {
      render();
      byId("query")?.focus();
    }
    load();
  }

  function argumentsFor(offset) {
    const args = {view:state.view, kind:state.kind, query:state.query, offset, limit:12};
    return args;
  }

  async function load({append = false, preserve = false} = {}) {
    const version = ++loadVersion;
    feedRequest?.abort();
    feedRequest = new AbortController();
    const before = state.data;
    state.loading = true;
    state.error = "";
    if (!append && !preserve) state.items = [];
    if (!preserve) render();
    try {
      const data = await call("feed",argumentsFor(append ? before?.next_offset || 0 : 0),
        {signal:feedRequest.signal});
      if (version !== loadVersion) return;
      state.data = data;
      const prior = new Set(state.items.map(({id}) => id));
      state.items = append ? [...state.items,...data.items.filter(({id}) => !prior.has(id))] : data.items;
    } catch (error) {
      if (version !== loadVersion) return;
      state.error = error.message;
    } finally {
      if (version === loadVersion) {
        state.loading = false;
        render();
      }
    }
  }

  function hero(kicker, title, description, actions) {
    return el("header",{class:"hero"},el("span",{class:"eyebrow"},kicker),
      el("h1",{},title),el("p",{},description),el("div",{class:"hero-tools"},actions));
  }

  function empty(title, text, action) {
    return el("div",{class:"empty"},el("h2",{},title),el("p",{class:"muted"},text),action);
  }

  function render() {
    if (state.view === "tools") return;
    const focus = document.activeElement;
    const queryFocused = focus?.id === "query";
    const focusKey = focus?.dataset?.focus;
    const listingKey = focus?.closest("[data-listing]")?.dataset.listing;
    const inputValue = state.draft;
    const data = state.data;
    const onFeed = state.view === "for-you";
    const explore = state.view === "explore";
    const withRail = onFeed;
    byId("layout").classList.toggle("single", !withRail);
    const label = tabs.find(([id]) => id === state.view)?.[1] || "Hidden";
    const heading = onFeed ? hero("Your opportunity feed","For You",
      "A place to find work worth a closer look.",
      [button("Tune feed","primary",tune,"sliders"), button("Refresh feed","secondary",() => load({preserve:true}),"refresh",
        {title:"Reload from your local collection",disabled:state.loading})])
      : hero("Your workspace",label,({
        explore:"Search every collected opportunity, even outside your usual interests.",
        saved:"Keep the opportunities you want to come back to in one place.",
        applications:"Pick up where you left off with opportunities you plan to pursue.",
        hidden:"Restore opportunities you previously hid from your feed."
      })[state.view], state.view === "saved" ? [
        button("Compare" + (compareIds.size ? " (" + compareIds.size + ")" : ""), "primary",
          compare, "sliders", {disabled:compareIds.size < 2, id:"compare-selected"}),
        button("View hidden","secondary",() => navigate("hidden"))
      ] : state.view === "hidden" ? button("Back to saved","secondary",() => navigate("saved")) : null);
    const filters = (onFeed || explore) ? el("div",{class:"toolbar"},
      el("div",{class:"chips","aria-label":"Opportunity type"},types.map(([kind,text]) =>
        button(text,"chip",() => { state.kind=kind; load(); },null,{"aria-pressed":String(state.kind === kind),"data-focus":"kind-"+kind})))) : null;
    let searchForm = null;
    if (explore) {
      const input = el("input",{id:"query",class:"search-input",type:"search",placeholder:"Role, skill, employer, or location",
        "aria-label":"Search opportunities", value:inputValue,
        onInput:(event) => { state.draft = event.currentTarget.value; }});
      searchForm = el("div",{class:"results-search"},el("form",{onSubmit:(event) => {
        event.preventDefault(); state.draft=input.value; state.query=input.value.trim(); load();
      }}, input, el("button",{type:"submit",class:"primary"},"Search")));
    }
    const results = [];
    const sectionTitle = onFeed ? data?.mode === "personalized" ? "Recommended for you" : "Recently collected" : label;
    results.push(el("div",{class:"section-heading"},el("h2",{},sectionTitle),
      el("span",{class:"caption"},state.loading ? "Loading…" : state.items.length ? `${state.items.length} shown` : "")));
    if (state.error) results.push(empty("Couldn’t load this view",state.error,button("Try again","secondary",() => load())));
    if (state.items.length) results.push(el("div",{class:"stack"},state.items.map(card)));
    else if (state.loading) results.push(el("div",{class:"stack","aria-hidden":"true"},
      [0,1,2].map(() => el("div",{class:"card skeleton"},
        el("div",{class:"skeleton-title"}),el("div",{class:"skeleton-line"}),el("div",{class:"skeleton-short"})))));
    else if (!state.error) {
      let title = "Nothing to show yet";
      let detail = "New opportunities will appear here when available.";
      let next;
      if (onFeed && !data?.summary?.total) {
        title = "Start with your first opportunities";
        detail = "Choose a source to collect public listings, or try a small fictional sample offline.";
        next = el("div",{class:"actions"},button("Choose sources","primary",() => navigate("tools")),
          button("Try the sample","secondary",async () => {
            try { await call("sample"); await load(); } catch (error) { notice(error.message); }
          }));
      } else if (onFeed || explore) {
        title = data?.has_more ? "No opportunities on this page" : "No opportunities in this view";
        detail = data?.has_more ? "Check the next page or clear your filters." : "Try a different category or a broader search.";
        next = button("Clear filters","secondary",() => {state.query=""; state.draft=""; state.kind=""; load();});
      } else if (state.view === "saved") {
        detail = "Save an opportunity from For You or Explore to see it here.";
        next = button("Go to For You","primary",() => navigate("for-you"));
      }
      results.push(empty(title,detail,next));
    }
    if (data?.has_more)
      results.push(el("div",{class:"load-row"},button("Show more","secondary",() => load({append:true}),null,{disabled:state.loading})));
    if (onFeed && data?.mode !== "personalized" && data?.summary?.total && !state.loading) {
      results.splice(1,0,el("p",{class:"caption feed-explainer"},data.goal
        ? `No matching recommendations for ${data.goal} in this view. These are recent listings.`
        : "Recent listings are shown until you choose a feed goal."));
    }
    feed.setAttribute("aria-busy",String(state.loading));
    const compactGoal = onFeed && data?.goal
      ? el("p",{class:"caption compact-goal"},"Feed goal · " + data.goal) : null;
    feed.replaceChildren(...[heading,compactGoal,searchForm,filters,
      el("section",{"aria-label":sectionTitle},results)].filter(Boolean));
    if (queryFocused && byId("query")) {
      byId("query").focus({preventScroll:true});
    } else if (focusKey && !drawer.open) {
      [...feed.querySelectorAll("[data-focus]")].find((node) => node.dataset.focus === focusKey &&
        node.closest("[data-listing]")?.dataset.listing === listingKey)?.focus({preventScroll:true});
    }
    if (withRail) renderRail(data);
  }

  function formatKind(value) {
    const words = value.replace(/[_-]/g," ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  function card(item) {
    const employer = item.organization || item.source_name || "Publisher not provided";
    const monogram = employer.split(/\s+/).slice(0,2).map((part) => [...part][0] || "").join("").toUpperCase();
    const tint = [...employer].reduce((sum, char) => sum + char.codePointAt(0),0) % 4;
    const reason = item.reason_kind === "match" || item.match?.recommended ? item.reason : "";
    const deadline = item.deadline_at && /^\d{4}-\d{2}-\d{2}/.test(item.deadline_at)
      ? item.deadline_at.slice(0,10) : "";
    const hasPast = /passed/i.test(item.availability || "");
    const actions = [
      state.view === "saved" ? button("Compare","quiet",(event) => {
        if (compareIds.has(item.id)) compareIds.delete(item.id);
        else if (compareIds.size >= 3) { notice("Choose up to three opportunities."); return; }
        else compareIds.add(item.id);
        event.currentTarget.setAttribute("aria-pressed",String(compareIds.has(item.id)));
        const compareButton = byId("compare-selected");
        compareButton.disabled = compareIds.size < 2;
        compareButton.lastChild.textContent = "Compare" + (compareIds.size ? " (" + compareIds.size + ")" : "");
      },null,{"aria-pressed":String(compareIds.has(item.id)), "aria-label":"Compare " + item.title}) : null,
      button(item.saved ? "Saved" : "Save",item.saved ? "action is-saved" : "action",
        (event) => save(item,event.currentTarget),item.saved ? "check" : "bookmark",
        {"data-focus":"save","aria-pressed":String(!!item.saved),"aria-label":`${item.saved ? "Unsave" : "Save"} ${item.title}`}),
      state.view === "hidden"
        ? button("Restore","quiet",() => restoreItem(item),"refresh")
        : item.status === "skip"
          ? button("Restore","quiet",() => restoreItem(item),"refresh")
          : state.view === "for-you" || state.view === "explore"
            ? button("Hide","quiet",(event) => hide(item,event.currentTarget),"hide",{"aria-label":"Hide "+item.title}) : null
    ];
    return el("article",{class:"card","data-listing":item.id},
      el("div",{class:"card-head"},el("div",{class:`monogram m${tint}`,"aria-hidden":"true"},monogram),
        el("div",{},button(item.title,"title-button",() => details(item.id)),el("div",{class:"card-meta"},
          [employer,item.location].filter(Boolean).join(" · ")))),
      item.excerpt ? el("p",{class:"card-text"},item.excerpt) : null,
      reason ? el("div",{class:"why"},el("strong",{},"Why it fits"),reason) : null,
      el("div",{class:"card-footer"},el("div",{class:"inline"},
        (item.kinds || []).slice(0,2).map((kind) => el("span",{class:"tag"},formatKind(kind))),
        /conflict/i.test(item.availability || "") ? el("span",{class:"caption"},"Conflicting dates")
          : deadline ? el("span",{class:"caption"},(hasPast ? "Deadline passed " : "Recorded deadline ")+deadline) : null,
        item.status === "skip" ? el("span",{class:"tag"},"Hidden") : null,
        !deadline && /^stale/i.test(item.availability || "") ? el("span",{class:"caption"},"Check availability") : null,
        state.view === "applications" ? el("span",{class:"tag"},statusNames[item.status] || item.status) : null),
        el("div",{class:"actions"},actions)));
  }

  function renderRail(data) {
    const saved = data?.summary?.saved;
    const hasProfiles = !!data?.goals?.length;
    rail.replaceChildren(
      el("section",{class:"side-card"},el("span",{class:"eyebrow"},"Feed settings"),
        el("h3",{},data?.goal || "Find your direction"),
        el("p",{},data?.goal ? "Your selected goal helps order opportunities using evidence in the listing."
          : hasProfiles ? "Choose a profile to get recommendations. You can switch back to recent listings any time."
            : "Add a few interests to get recommendations. You can switch back to recent listings any time."),
        button(data?.goal ? "Edit feed" : hasProfiles ? "Choose profile" : "Set interests","secondary",tune,"sliders")),
      el("section",{class:"side-card"},el("span",{class:"eyebrow"},"Your shortlist"),
        el("h3",{},saved === undefined ? "Saved opportunities" : `${saved.toLocaleString()} saved`),
        el("p",{},"Keep good finds close. Decide when you’re ready."),
        button("Open saved","action",() => navigate("saved"),"arrow")),
      el("section",{class:"side-card"},el("span",{class:"eyebrow"},"Public sources"),
        el("h3",{},"Collection"),
        el("p",{},"See where listings came from and collect updates when you choose."),
        button("Manage sources","action",() => navigate("tools"),"arrow")));
  }

  async function save(item, control) {
    if (control) control.disabled = true;
    try {
      const result = await call("track",{id:item.id,saved:!item.saved});
      item.saved = !!result.saved;
      const visible = state.items.find(({id}) => id === item.id);
      if (visible) visible.saved = item.saved;
      if (!item.saved) {
        compareIds.delete(item.id);
        if (state.view === "saved") state.items = state.items.filter(({id}) => id !== item.id);
      }
      if (state.data?.summary) state.data.summary.saved = Math.max(0,state.data.summary.saved + (item.saved ? 1 : -1));
      render();
      const card = [...feed.querySelectorAll("[data-listing]")].find((node) => node.dataset.listing === item.id);
      if (!drawer.open) (card?.querySelector('[data-focus="save"]') || feed).focus({preventScroll:true});
      notice(item.saved ? "Saved to your shortlist." : "Removed from your shortlist.");
      if (drawer.open && openingId === item.id && control?.isConnected) {
        control.lastChild.textContent = item.saved ? "Unsave" : "Save";
        control.disabled = false;
      }
    } catch (error) {
      if (control) control.disabled = false;
      notice(error.message);
    }
  }

  async function hide(item, control) {
    control.disabled = true;
    try {
      const result = await call("hide",{id:item.id});
      const index = state.items.findIndex(({id}) => id === item.id);
      state.items = state.items.filter(({id}) => id !== item.id);
      compareIds.delete(item.id);
      if (state.data?.summary && item.saved) state.data.summary.saved = Math.max(0,state.data.summary.saved - 1);
      // Explore browse removes hidden rows. For You cursors include their stable positions.
      if (index >= 0 && state.view === "explore" && !state.query.trim() && state.data?.next_offset > 0)
        state.data.next_offset -= 1;
      render();
      if (drawer.open) drawer.close();
      (feed.querySelectorAll(".title-button")[Math.max(0,Math.min(index,state.items.length-1))] || feed).focus({preventScroll:true});
      notice("Hidden from your feed.",async () => {
        await call("undo_hide",{id:item.id,status:result.previous_status,token:result.token});
        await load({preserve:true});
      });
    } catch (error) { control.disabled = false; notice(error.message); }
  }

  async function restoreItem(item) {
    try { await call("undo_hide",{id:item.id}); notice("Opportunity restored."); await load({preserve:true}); }
    catch (error) { notice(error.message); }
  }

  function showDrawer(title, body) {
    const alreadyOpen = drawer.open;
    if (!alreadyOpen) {
      returnFocus = document.activeElement;
      returnListing = returnFocus?.closest("[data-listing]")?.dataset.listing;
    }
    content.replaceChildren(el("div",{class:"dialog-head"},el("strong",{id:"drawer-title"},title),
      button("Close","quiet",() => drawer.close(),"hide")),
      el("div",{id:"drawer-notice",class:"inline-notice",role:"status",hidden:""}),
      el("div",{class:"dialog-body"},body));
    drawer.setAttribute("aria-labelledby","drawer-title");
    drawer.scrollTop = 0;
    if (!alreadyOpen) drawer.showModal();
    else content.querySelector(".dialog-head button").focus({preventScroll:true});
  }

  function disclosure(title, ...body) {
    return el("details",{},el("summary",{},title),body);
  }

  async function details(id) {
    const version = ++drawerVersion;
    openingId = id;
    showDrawer("Opportunity details",el("p",{class:"muted"},"Loading source evidence…"));
    try {
      const args = {id};
      if (state.data?.goal) args.goal = state.data.goal;
      const item = await call("inspect",args);
      if (!drawer.open || version !== drawerVersion) return;
      const controls = [button(item.saved ? "Unsave" : "Save","primary",(event) => save(item,event.currentTarget),"bookmark"),
        item.url ? button("Publisher listing","secondary",async () => {
          try { await call("openExternal",{url:item.url}); } catch (error) { notice(error.message); }
        },"arrow") : null];
      const matches = (item.match?.rules || []).filter((rule) => rule.quote);
      const evidence = Object.entries(item.brief || {}).filter(([,entries]) => entries.length);
      const history = item.history || [];
      const sources = (item.observations || []).map((entry) => el("p",{},
        [entry.source_name || entry.source_id,entry.observed_at?.slice(0,10)].filter(Boolean).join(" · "),
        entry.url ? el("br") : null, entry.url ? button("Open source","action",async () => {
          try { await call("openExternal",{url:entry.url}); } catch (error) { notice(error.message); }
        },"arrow") : null));
      const statusSelect = el("select",{"aria-label":"Application status"},Object.entries(statusNames)
        .filter(([key]) => key !== "skip").map(([key,name]) => el("option",{value:key,selected:item.status === key},name)));
      const note = el("textarea",{rows:"3",maxlength:"8000",placeholder:"What do you want to check before applying?"},item.note || "");
      const form = el("form",{class:"form-row",onSubmit:async (event) => {
        event.preventDefault();
        try { await call("track",{id,status:statusSelect.value,note:note.value}); notice("Application updated.");
          if (state.view === "applications" || state.view === "saved") load({preserve:true}); }
        catch (error) { notice(error.message); }
      }}, el("label",{class:"field"},"Application status",statusSelect),
        el("label",{class:"field"},"Private note",note),el("div",{},el("button",{class:"secondary",type:"submit"},"Update tracking")));
      showDrawer("Opportunity details",[
        el("span",{class:"eyebrow"},(item.kinds || []).map(formatKind).join(" · ") || "Opportunity"),
        el("h2",{},item.title),el("p",{class:"muted"},[item.organization,item.location].filter(Boolean).join(" · ")),
        el("div",{class:"actions"},controls),el("p",{class:"caption"},[
          item.deadline_at ? "Recorded deadline " + item.deadline_at.slice(0,10) : null,
          item.availability, item.match?.eligibility?.state ? "Eligibility: "+item.match.eligibility.state : null
        ].filter(Boolean).join(" · ")),
        matches.length ? disclosure("Why it fits",matches.map((match) =>
          el("p",{},el("strong",{},match.term+" "),match.quote))) : null,
        item.description ? el("p",{class:"listing-description"},item.description) : el("p",{class:"muted"},"No description was collected."),
        evidence.length ? disclosure("Evidence from the listing",evidence.map(([group,entries]) =>
          el("section",{class:"evidence-group"},el("h3",{},group),entries.map((entry) =>
            el("p",{},entry.quote,entry.conditional ? el("span",{class:"tag"},"Conditional wording") : null))))) : null,
        disclosure("Sources and requirements",item.requirements ? el("p",{},item.requirements) : null,
          sources.length ? sources : el("p",{},"No additional source evidence.")),
        history.length ? disclosure("Previous source observations",history.slice(0,10).map((entry) =>
          el("p",{},[entry.observed_at?.slice(0,10),entry.title,entry.source_name].filter(Boolean).join(" · ")))) : null,
        disclosure("Track an application",form)
      ]);
    } catch (error) {
      if (drawer.open && version === drawerVersion) showDrawer("Opportunity details",empty("Couldn’t load details",error.message));
    }
  }

  async function compare() {
    if (compareIds.size < 2) return;
    const version = ++drawerVersion;
    openingId = null;
    showDrawer("Compare opportunities",el("p",{class:"muted"},"Collecting evidence…"));
    try {
      const args = {ids:[...compareIds]};
      if (state.data?.goal) args.goal = state.data.goal;
      const result = await call("compare",args);
      if (!drawer.open || version !== drawerVersion) return;
      showDrawer("Compare opportunities",el("div",{class:"comparison"},result.map((item) =>
        el("article",{class:"panel"},el("h3",{},item.title),el("p",{class:"muted"},item.organization),
          el("p",{class:"caption"},[item.location,item.availability].filter(Boolean).join(" · ")),
          item.deadline_at ? el("p",{class:"caption"},"Recorded deadline " + item.deadline_at.slice(0,10)) : null,
          item.description ? el("p",{class:"compare-description"},item.description) : null,
          button("View details","secondary",() => details(item.id),"arrow")))));
    } catch (error) {
      if (drawer.open && version === drawerVersion) showDrawer("Compare opportunities",empty("Couldn’t compare",error.message));
    }
  }

  function tune() {
    ++drawerVersion;
    openingId = null;
    const data = state.data;
    const select = el("select",{"aria-label":"Feed goal"},
      el("option",{value:"",selected:!data?.goal},"Recently collected"),
      (data?.goals || []).map(({name}) => el("option",{value:name,selected:data.goal === name},name)));
    const preference = el("form",{class:"form-row",onSubmit:async (event) => {
      event.preventDefault();
      await applyGoal(select.value);
    }},el("label",{class:"field"},"Show opportunities for",select),el("div",{},el("button",{type:"submit",class:"primary"},"Apply")));
    const name = el("input",{maxlength:"100",placeholder:"e.g. Research roles",required:""});
    const interests = el("textarea",{rows:"3",required:"",placeholder:"scientific computing, robotics, applied research"});
    const quick = el("form",{class:"form-row",onSubmit:async (event) => {
      event.preventDefault();
      const prefer = interests.value.split(/[,\n]/).map((part) => part.trim()).filter(Boolean);
      if (!name.value.trim() || !prefer.length) { notice("Enter a name and at least one interest."); return; }
      if ((data?.goals || []).some((goal) => goal.name.toLowerCase() === name.value.trim().toLowerCase())) {
        notice("Choose a new name, or edit that goal in the full workspace."); return;
      }
      try {
        await call("set_goal",{name:name.value.trim(),rules:{prefer}});
        await applyGoal(name.value.trim());
      } catch (error) { notice(error.message); }
    }},el("label",{class:"field"},"Name",name),el("label",{class:"field"},"Interests, separated by commas",interests),
      el("div",{},el("button",{type:"submit",class:"secondary"},"Create and use goal")));
    showDrawer("Tune feed",[
      el("h2",{},"Make it yours"),
      el("p",{class:"muted"},"Choose the work you want to see. Your preferences stay on this device."),
      preference,
      el("div",{class:"profile-entry"},el("h3",{},"Bring in profiles"),
        el("p",{class:"caption"},"Start from a profile file or use AI to draft profiles from your materials."),
        button("Import profiles…","secondary",() => openProfiles(tune))),
      disclosure("Create a quick goal",quick),
      data?.goals?.length ? disclosure("Edit an existing goal",editGoalForm(data.goals,data.goal)) : null
    ]);
  }

  function openProfiles(onBack) {
    ++drawerVersion;
    openingId = null;
    const version = drawerVersion;
    showDrawer("Import profiles",window.renderProfileImport({el,button,call,onBack,onComplete:(count) => {
      if (drawer.open && version === drawerVersion) {
        drawer.close();
        if (state.view === "tools") navigate("for-you");
        else load({preserve:true});
      } else if (state.view === "for-you") {
        load({preserve:true});
      }
      notice(`Imported ${count} ${count === 1 ? "profile" : "profiles"}.`);
    }}));
  }

  function editGoalForm(goals, current) {
    const fields = [["prefer","Prioritize"],["require","Require"],["avoid","Avoid"],["explore","Explore"]];
    const inputs = {};
    const selector = el("select",{},goals.map(({name}) =>
      el("option",{value:name,selected:current === name},name)));
    const controls = fields.map(([key,label]) => {
      const field = el("textarea",{rows:"2",placeholder:"One phrase per line"});
      inputs[key] = field;
      return el("label",{class:"field"},label,field);
    });
    const populate = () => {
      const goal = goals.find(({name}) => name === selector.value);
      for (const [key] of fields) inputs[key].value = (goal.rules?.[key] || []).join("\n");
    };
    selector.addEventListener("change",populate);
    populate();
    return el("form",{class:"form-row",onSubmit:async (event) => {
      event.preventDefault();
      const goal = goals.find(({name}) => name === selector.value);
      const rules = {...goal.rules};
      for (const [key] of fields) rules[key] = inputs[key].value.split("\n").map((part) => part.trim()).filter(Boolean);
      try { await call("set_goal",{name:goal.name,rules}); await applyGoal(goal.name); }
      catch (error) { notice(error.message); }
    }},el("label",{class:"field"},"Goal to edit",selector),controls,
      el("p",{class:"caption"},"Use one phrase per line. Prioritize the most important phrase first."),
      el("div",{class:"actions"},el("button",{type:"submit",class:"secondary"},"Save and use goal"),
        button("Undo last goal edit","quiet",async () => {
          try { await call("undo_goal",{name:selector.value}); drawer.close(); notice("Last goal edit undone."); await load(); }
          catch (error) { notice(error.message); }
        })));
  }

  async function applyGoal(goal) {
    try {
      await call("set_preferences",{goal});
      drawer.close();
      notice(goal ? "Feed goal updated." : "Showing recent opportunities.");
      await load();
    } catch (error) { notice(error.message); }
  }

  async function openLegacy() {
    try { await call("openLegacy"); } catch (error) { notice(error.message); }
  }

  drawer.addEventListener("close",() => {
    if (drawer.open) return;
    ++drawerVersion;
    openingId = null;
    const replacement = [...feed.querySelectorAll("[data-listing]")].find(
      (node) => node.dataset.listing === returnListing)?.querySelector(".title-button");
    (returnFocus?.isConnected ? returnFocus : replacement || feed).focus({preventScroll:true});
  });
  byId("search-shortcut").addEventListener("click",() => navigate("explore",true));
  document.addEventListener("keydown",(event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && !drawer.open) {
      event.preventDefault(); navigate("explore",true);
    }
  });
  renderNav();
  load();
})();
