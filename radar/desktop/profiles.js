"use strict";

(() => {
  const prompt = `Using the source files and preferences I provide, draft one Opportunity Radar profile per distinct career goal. Return a single valid UTF-8 JSON object, ready to save as a .json file. Use this structure:

{
  "version": 1,
  "active_profile": "Scientific software internships",
  "profiles": [
    {
      "name": "Scientific software internships",
      "rules": {
        "objective": "Find internships building software for research teams.",
        "prefer": ["scientific computing", "research software", "graduate internship"],
        "require": [],
        "avoid": ["unpaid internship"],
        "explore": ["open source fellowship", "research infrastructure"],
        "facts": {}
      }
    }
  ]
}

Replace all sample values with the goals and evidence from my materials. Keep different levels and fields in separate named profiles. Include relevant role titles, methods, settings, opportunity types, and adjacent paths I might miss. Where the materials leave a preference unclear, leave the field empty or put the option in explore. Omit contact details, sensitive identifiers, and unsupported claims about eligibility, location, credentials, or work authorization. Use facts only for explicitly confirmed booleans with exact Radar requirement keys supplied in my materials.

Use short, distinct phrases a publisher might write in a listing. Put the strongest preferred phrase first. Use require only for true must-haves that will be stated in listing text. Use avoid for strong negative signals and explore for adjacent opportunities. If I specify location preferences, you may add rules.location with places (up to 12 unambiguous names like "Baltimore, Maryland, United States"), modes (remote, hybrid, or onsite), policy (prefer or require), and include_unknown (true or false). Include only confirmed preferences. Keep the objective to 2,000 characters, use up to 30 phrases of up to 120 characters per list, and keep unique profile names to 100 characters. The pack can contain 1 to 100 profiles and be at most 256 KiB. Set active_profile to a name in this pack, use null to show recent listings, or omit it to preserve the current selection.`;

  const phraseFields = [
    ["prefer", "Prioritize"], ["require", "Require"], ["avoid", "Avoid"], ["explore", "Explore"]
  ];

  function renderProfileImport({el,button,call,onBack,onComplete}) {
    const panel = el("div",{class:"profile-import"});
    const status = el("p",{class:"caption profile-status",role:"status"});
    const json = el("textarea",{rows:"6",class:"profile-json",spellcheck:"false",
      placeholder:'{"version":1,"profiles":[…]}' });
    const picked = el("p",{class:"caption",role:"status",hidden:""});
    const replace = el("input",{type:"checkbox"});
    let path = "";

    const choose = button("Choose JSON file…","secondary",async () => {
      lock(true,"Choose a profile file.");
      let message = "";
      try {
        const selected = await call("pickPath",{mode:"open",title:"Choose a Radar profile file",extension:"json"});
        if (selected) {
          path = selected;
          json.value = "";
          picked.textContent = `Selected file · ${path.split("/").pop()}`;
          picked.hidden = false;
        }
      } catch (error) { message = error.message; }
      finally { lock(false,message); }
    });
    json.addEventListener("input",() => {
      path = "";
      picked.hidden = true;
      status.textContent = "";
    });

    const preview = el("button",{type:"submit",class:"primary"},"Review profiles");
    function lock(busy,message) {
      choose.disabled = preview.disabled = replace.disabled = busy;
      json.readOnly = busy;
      status.textContent = message;
    }

    const form = el("form",{class:"form-row",onSubmit:async (event) => {
      event.preventDefault();
      const text = json.value.trim();
      if (!path && !text) { status.textContent = "Choose a JSON file or paste your profile JSON."; return; }
      const args = {...(path ? {path} : {text}),replace:replace.checked};
      lock(true,"Checking profiles…");
      try {
        const result = await call("preview_profiles",args);
        if (panel.isConnected) showReview(result,args);
        lock(false,"");
      } catch (error) { lock(false,error.message); }
    }},el("div",{class:"actions"},choose),picked,
      el("label",{class:"field"},"Or paste profile JSON",json),
      el("label",{class:"profile-option"},replace,"Remove existing profiles outside this file or draft"),
      el("div",{class:"actions"},preview),status);

    const draftPrompt = el("textarea",{rows:"6",readonly:"",value:prompt,spellcheck:"false"});
    const copyStatus = el("p",{class:"caption profile-status",role:"status"});
    const authoring = el("details",{},el("summary",{},"Draft profiles with AI"),
      el("p",{class:"caption"},"Open your preferred AI assistant. Share the résumé, CV, or notes you choose alongside this prompt, then paste its JSON below. Radar reads the profile draft locally."),
      el("label",{class:"field"},"Prompt for your AI assistant",draftPrompt),
      el("div",{class:"actions"},button("Copy AI prompt","secondary",async () => {
        try {
          await call("copyText",{text:prompt});
          copyStatus.textContent = "AI prompt copied.";
        } catch (error) {
          draftPrompt.focus();
          draftPrompt.select();
          copyStatus.textContent = `${error.message} Press Command-C to copy the selected prompt.`;
        }
      })),copyStatus);
    const introduction = el("div",{class:"profile-import"},
      el("h2",{tabindex:"-1"},"Import feed profiles"),
      el("p",{class:"muted"},"Add named profiles with preferences you can switch between in For You."),
      authoring,form,onBack ? el("div",{},button("Back to tune feed","quiet",onBack)) : null);
    panel.append(introduction);

    function showReview(result,args) {
      const count = result.profiles.length;
      const commitStatus = el("p",{class:"caption profile-status",role:"status"});
      const back = button("Back","secondary",() => {
        panel.replaceChildren(introduction);
        preview.focus();
      });
      const apply = button(result.removed.length ? "Replace profiles" : "Import profiles","primary",async () => {
        apply.disabled = back.disabled = true;
        commitStatus.textContent = "Importing profiles…";
        try {
          await call("import_profiles",{...args,expected_revision:result.revision});
          onComplete(count);
        } catch (error) {
          apply.disabled = back.disabled = false;
          commitStatus.textContent = error.message;
        }
      });
      const heading = el("h2",{tabindex:"-1"},`Review ${count} ${count === 1 ? "profile" : "profiles"}`);
      const selection = Object.prototype.hasOwnProperty.call(result,"active_profile")
        ? (result.active_profile ? `For You will use ${result.active_profile}.` : "For You will show recently collected listings.")
        : "For You keeps its current selection when available.";
      const effects = el("div",{class:"profile-effects"},el("p",{},selection),
        result.conflicts.length ? el("p",{},`Update existing ${result.conflicts.length === 1 ? "profile" : "profiles"} · ${result.conflicts.join(", ")}`) : null,
        result.removed.length ? el("p",{},`Remove ${result.removed.length === 1 ? "profile" : "profiles"} · ${result.removed.join(", ")}`) : null);
      const locationLabels = [];
      const profiles = result.profiles.map(({name,rules}) => {
        const fields = phraseFields.filter(([key]) => rules[key]?.length).flatMap(([key,label]) => [
          el("dt",{},label),el("dd",{},rules[key].join(" · "))
        ]);
        const facts = Object.entries(rules.facts || {});
        if (facts.length) fields.push(el("dt",{},"Self-reported facts"),
          el("dd",{},facts.map(([key,value]) => `${key} = ${value}`).join(" · ")));
        if (rules.location) {
          const {places,modes,policy,include_unknown} = rules.location;
          const labels = el("span",{},places.join(" · "));
          if (places.length) locationLabels.push([places,labels]);
          fields.push(el("dt",{},"Location preference"),
            el("dd",{},policy === "require" ? "Require" : "Prioritize",
              places.length ? [" · ",labels] : null,
              modes.length ? " · " + modes.join(", ") : null,
              include_unknown ? " · Include unknown" : " · Unknown remains unconfirmed"));
        }
        return el("details",{open:count === 1},el("summary",{},name),
          rules.objective ? el("p",{class:"muted"},rules.objective) : null,
          fields.length ? el("dl",{class:"profile-rules"},fields) : null);
      });
      panel.replaceChildren(heading,effects,el("div",{class:"profile-list"},profiles),
        el("div",{class:"actions"},apply,back),commitStatus);
      heading.focus();
      if (locationLabels.length) {
        const ids = [...new Set(locationLabels.flatMap(([places]) => places))];
        call("location_labels",{ids}).then((labels) => {
          for (const [places,node] of locationLabels) node.textContent = places.map((id) => labels[id] || id).join(" · ");
        }).catch(() => {});
      }
    }

    return panel;
  }

  window.renderProfileImport = renderProfileImport;
})();
