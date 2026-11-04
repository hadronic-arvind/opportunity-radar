"use strict";

(() => {
  function renderListingIntelligence(item,{el,button,call,details}) {
    function openSource(url) {
      const status = el("span",{class:"caption",role:"status"});
      return el("div",{class:"actions"},button("Open source","action",async () => {
        try { await call("openExternal",{url}); }
        catch (error) { status.textContent = error.message; }
      }),status);
    }

    const costs = el("details",{},el("summary",{},"Pay, costs and support"),
      el("p",{class:"caption"},"Source wording may be incomplete or conditional. Verify amounts, terms and what is covered with the publisher."),
      Object.entries(item.cost_evidence || {}).map(([group,entries]) => el("section",{class:"evidence-group"},
        el("h3",{},group),
        entries.length ? entries.map((entry) => el("blockquote",{},
          el("p",{},entry.quote),
          el("p",{class:"caption"},[entry.source_name || entry.source_id,entry.observed_at?.slice(0,10),
            entry.conditional ? "Conditional wording" : null,
            entry.negative_language ? "Negative wording" : null].filter(Boolean).join(" · ")),
          entry.url ? openSource(entry.url) : null)) : el("p",{class:"muted"},"No wording collected."))));

    function lazy(title,operation,render) {
      const result = el("div",{},el("p",{class:"muted"},"Loading…"));
      const section = el("details",{},el("summary",{},title),result);
      let loading = false, loaded = false;
      async function load() {
        if (loading || loaded) return;
        loading = true;
        result.replaceChildren(el("p",{class:"muted",role:"status"},"Loading…"));
        try {
          const data = await call(operation,{id:item.id});
          result.replaceChildren(...render(data));
          loaded = true;
        } catch (error) {
          result.replaceChildren(el("p",{class:"muted",role:"status"},error.message),
            button("Retry","secondary",load));
        } finally { loading = false; }
      }
      section.addEventListener("toggle",() => { if (section.open) load(); });
      return section;
    }

    const revisions = lazy("What changed","listing_revisions",(entries) => entries.length ? [
      el("p",{class:"caption"},"Comparisons use consecutive captures of the same source record. Observation dates show when Radar saw each version."),
      ...entries.map((entry) => el("article",{class:"evidence-group"},
        el("h3",{},entry.source_name || entry.source_id),
        el("p",{class:"caption"},[entry.before_observed_at,entry.after_observed_at]
          .map((date) => date?.slice(0,10)).filter(Boolean).join(" → ")),
        entry.changes.map((change) => el("details",{},el("summary",{},change.label),
          ["before","after"].map((key) => el("div",{},
            el("h4",{},(key === "before" ? "Earlier observation" : "Later observation") +
              (change[key].truncated ? " (excerpt)" : "")),
            el("p",{class:"listing-description"},change[key].text || "No value recorded.")))))))
    ] : [el("p",{class:"muted"},"No comparable changes were captured for this listing.")]);

    const related = lazy("Related opportunities","related_opportunities",(entries) => entries.length ? [
      el("p",{class:"caption"},"Suggestions use shared listing terms from your local collection."),
      ...entries.map((entry) => el("article",{class:"evidence-group"},
        el("h3",{},entry.title),el("p",{class:"muted"},[entry.organization,entry.location].filter(Boolean).join(" · ")),
        el("p",{class:"caption"},"Shared terms · " + entry.shared_terms.join(", ")),
        button("View details","secondary",() => details(entry.id),"arrow")))
    ] : [el("p",{class:"muted"},"No related listings with specific shared terms were found.")]);

    return [costs,revisions,related];
  }

  window.renderListingIntelligence = renderListingIntelligence;
})();
