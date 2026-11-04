"use strict";

(() => {
  const modes = [["remote","Remote"],["hybrid","Hybrid"],["onsite","On-site"]];
  const fallback = (id) => {
    const parts = id.split(":");
    return parts[0] === "city" ? [parts.slice(3).join(" ").replaceAll("-", " "), parts[2], parts[1]].join(", ")
      : parts.length > 1 ? parts.slice(1).reverse().join(", ") : id;
  };

  function makeLocationPicker({el,button,call,initial={},profile=false}) {
    const places = new Map((initial.places || []).map((id) => [id,fallback(id)]));
    const selected = el("div",{class:"actions","aria-label":"Selected locations"});
    const options = el("div",{class:"actions","aria-label":"Location suggestions"});
    const status = el("p",{class:"caption",role:"status"});
    const input = el("input",{type:"search",maxlength:"120",placeholder:"Country, region, or city"});
    let revision = 0;
    let timer;
    const policy = el("select",{},
      el("option",{value:"prefer",selected:initial.policy !== "require"},"Prioritize matching places"),
      el("option",{value:"require",selected:initial.policy === "require"},"Require a match"));
    const unknown = el("input",{type:"checkbox",checked:initial.include_unknown});
    const checks = modes.map(([value,label]) => [
      value,label,el("input",{type:"checkbox",checked:initial.modes?.includes(value)})
    ]);

    function renderPlaces() {
      selected.replaceChildren(...[...places].map(([id,label]) =>
        button(label + " ×","chip",() => {
          places.delete(id);
          renderPlaces();
          input.focus();
        },null,{"aria-label":"Remove " + label})));
    }
    if (places.size) call("location_labels",{ids:[...places.keys()]}).then((labels) => {
      for (const [id,label] of Object.entries(labels)) if (places.has(id)) places.set(id,label);
      renderPlaces();
    }).catch(() => {});
    renderPlaces();

    function clearSuggestions() {
      ++revision;
      clearTimeout(timer);
      options.replaceChildren();
      status.textContent = "";
    }

    input.addEventListener("keydown",(event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        options.querySelector("button")?.focus();
      }
    });
    input.addEventListener("input",() => {
      clearSuggestions();
      const query = input.value.trim();
      if (query.length < 2) return;
      const version = revision;
      timer = setTimeout(async () => {
        try {
          const found = await call("locations",{query,limit:8});
          if (version !== revision || !input.isConnected) return;
          const available = found.filter(({id}) => !places.has(id));
          status.textContent = available.length ? "Choose a place below."
            : "No additional places found. Try a broader name or include the region.";
          options.replaceChildren(...available.map(({id,label,level}) => button(label,"secondary",() => {
            if (places.size >= 12) { status.textContent = "Choose up to 12 places."; return; }
            places.set(id,label);
            renderPlaces();
            input.value = "";
            clearSuggestions();
            input.focus();
          },null,{title:level})));
        } catch (error) {
          if (version === revision) status.textContent = error.message;
        }
      },180);
    });

    const element = el("div",{class:"form-row location-picker"},
      el("label",{class:"field"},"Find a place",input),options,status,selected,
      el("fieldset",{class:"location-modes"},el("legend",{},"Work arrangement"),
        el("div",{class:"actions"},checks.map(([,label,control]) =>
          el("label",{class:"profile-option"},control,label)))),
      el("p",{class:"caption"},"Countries and regions include their recognized places. A remote listing needs a stated geographic scope to match a place."),
      profile ? el("label",{class:"field"},"How to use this preference",policy) : null,
      el("label",{class:"profile-option"},unknown,"Include listings with unknown location or work arrangement"));
    const value = () => {
      const workModes = checks.filter(([, , control]) => control.checked).map(([mode]) => mode);
      return places.size || workModes.length ? {
        places:[...places.keys()],modes:workModes,policy:profile ? policy.value : "require",
        include_unknown:unknown.checked
      } : null;
    };
    return {element,value};
  }

  window.makeLocationPicker = makeLocationPicker;
})();
