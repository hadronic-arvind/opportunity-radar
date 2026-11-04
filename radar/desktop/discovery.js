"use strict";

(() => {
  function renderSearches({el,button,call,notice,criteria,onOpen}) {
    const entries = el("div",{class:"stack"});
    const status = el("p",{class:"caption",role:"status"});
    const name = el("input",{required:"",maxlength:"100",placeholder:"e.g. Graduate research in Maryland"});
    const save = el("button",{type:"submit",class:"primary"},"Save current search");
    async function refresh() {
      try {
        const searches = await call("list_saved_searches");
        entries.replaceChildren(...searches.map((search) => {
          const {query,kind,location} = search.criteria;
          const places = location?.places?.length || 0;
          const summary = [query,kind,places ? `${places} ${places === 1 ? "place" : "places"}` : "", ...(location?.modes || [])]
            .filter(Boolean).join(" · ") || "All opportunities";
          return el("article",{class:"panel"},
            el("h3",{},search.name),el("p",{class:"caption"},summary),
            el("div",{class:"actions"},
              button("Open search","secondary",() => onOpen(search,false)),
              button("Check what’s new","primary",() => onOpen(search,true)),
              button("Remove","quiet",async (event) => {
                const control = event.currentTarget;
                if (control.dataset.confirm !== "yes") {
                  control.dataset.confirm = "yes";
                  control.textContent = "Confirm remove";
                  return;
                }
                control.disabled = true;
                try { await call("remove_saved_search",{name:search.name}); await refresh(); }
                catch (error) { status.textContent = error.message; control.disabled = false; }
              },null,{"aria-label":"Remove saved search " + search.name})));
        }));
        if (!searches.length) entries.append(el("p",{class:"muted"},"Save a search to return to it or review new matches later."));
      } catch (error) {
        status.textContent = error.message;
      }
    }
    const form = el("form",{class:"form-row",onSubmit:async (event) => {
      event.preventDefault();
      save.disabled = true;
      status.textContent = "";
      try {
        const search = await call("save_saved_search",{name:name.value,...criteria});
        notice("Search saved.");
        onOpen(search,false);
      } catch (error) {
        status.textContent = error.message;
        save.disabled = false;
      }
    }},el("label",{class:"field"},"Name this search",name),el("div",{},save));
    refresh();
    return [el("h2",{},"Saved searches"),
      el("p",{class:"muted"},"Keep keywords, opportunity types and location filters together. New matches are counted from when you save or review a search."),
      form,status,entries];
  }

  function renderCapture({el,call,onCaptured}) {
    const status = el("p",{class:"caption",role:"status"});
    const fields = {};
    const field = (key,label,attrs={},tag="input") => {
      fields[key] = el(tag,attrs);
      return el("label",{class:"field"},label,fields[key]);
    };
    const kinds = [
      ["opportunity","Other"],["job","Job"],["internship","Internship"],["fellowship","Fellowship"],
      ["grant","Grant"],["training","Training"],["competition","Competition"],["research","Research"]
    ];
    fields.kind = el("select",{},kinds.map(([value,label]) => el("option",{value},label)));
    const submit = el("button",{type:"submit",class:"primary"},"Save opportunity");
    return [el("h2",{},"Capture an opportunity"),
      el("p",{class:"muted"},"Record a role, fellowship or other opportunity you found elsewhere. Radar saves the details you enter and keeps the publisher link with them."),
      el("form",{class:"form-row",onSubmit:async (event) => {
        event.preventDefault();
        submit.disabled = true;
        status.textContent = "Saving…";
        try {
          const result = await call("capture_opportunity",Object.fromEntries(
            Object.entries(fields).map(([key,control]) => [key,control.value])
          ));
          onCaptured(result.id);
        } catch (error) {
          status.textContent = error.message;
          submit.disabled = false;
        }
      }},
      field("title","Title",{required:"",maxlength:"500"}),
      field("url","Publisher link (optional)",{type:"url",maxlength:"2048",placeholder:"https://"}),
      field("organization","Organization",{maxlength:"300"}),
      field("location","Location as written by the publisher",{maxlength:"500"}),
      el("label",{class:"field"},"Opportunity type",fields.kind),
      field("deadline_at","Deadline (optional)",{type:"date"}),
      field("description","Listing excerpt",{rows:"4",maxlength:"20000"},"textarea"),
      field("requirements","Requirements",{rows:"3",maxlength:"5000"},"textarea"),
      el("div",{},submit),status)];
  }

  window.renderSavedSearches = renderSearches;
  window.renderOpportunityCapture = renderCapture;
})();
