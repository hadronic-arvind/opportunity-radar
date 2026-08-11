(function () {
  "use strict";

  const PAGE_SIZE = 24;
  const SEARCH_DEBOUNCE_MS = 90;
  const MAX_LOCAL_RECORDS = 500;
  const TRANSIENT_VIEW_KEY = "opportunity-radar-transient-view-v1";
  const ID_PATTERN = /^[a-f0-9]{24}$/;
  const STATUS_VALUES = new Set(["new", "reviewed", "apply", "applied", "skip"]);
  const THEME_VALUES = new Set(["system", "light", "dark"]);
  const VIEW_VALUES = new Set(["discover", "applications"]);
  const SORT_VALUES = new Set(["fit", "newest", "deadline", "organization"]);
  const TYPE_LABELS = {
    apprenticeship: "Apprenticeship",
    co_op: "Co-op",
    fellowship: "Fellowship",
    internship: "Internship",
    job: "Job",
    opportunity: "Opportunity",
    postdoc: "Postdoc",
    program: "Program",
    research_program: "Research program",
    residency: "Residency",
    scholarship: "Scholarship",
    training: "Training",
  };
  const NON_EMPLOYMENT_COMMITMENTS = new Set([
    "high", "low", "medium", "standard", "very high", "very low",
  ]);
  const CAREER_STAGE_OPTIONS = [
    ["", "Not specified"],
    ["student", "Student"],
    ["undergraduate_student", "Undergraduate"],
    ["graduate_student", "Graduate student"],
    ["masters_student", "Master's student"],
    ["phd_student", "PhD student"],
    ["postdoc", "Postdoc"],
    ["new_grad", "New graduate"],
    ["early_career", "Early career"],
    ["experienced", "Experienced"],
  ];
  const OPPORTUNITY_TYPE_OPTIONS = [
    ["internship", "Internships"],
    ["job", "Jobs"],
    ["fellowship", "Fellowships"],
    ["postdoc", "Postdocs"],
    ["research_program", "Research programs"],
    ["scholarship", "Scholarships"],
    ["apprenticeship", "Apprenticeships"],
    ["co_op", "Co-ops"],
  ];
  const WORK_ARRANGEMENT_OPTIONS = [
    ["onsite", "On-site"],
    ["hybrid", "Hybrid"],
    ["remote", "Remote"],
  ];
  const REMOTE_PREFERENCE_OPTIONS = [
    ["", "No preference"],
    ["remote_preferred", "Prefer remote"],
    ["remote_required", "Remote only"],
    ["hybrid_preferred", "Prefer hybrid"],
    ["onsite_preferred", "Prefer on-site"],
  ];
  const MATCH_FIELD_OPTIONS = [
    ["title", "Title"],
    ["organization", "Organization"],
    ["location", "Location"],
    ["description", "Description"],
    ["eligibility", "Eligibility"],
    ["category", "Category"],
    ["opportunity_type", "Opportunity type"],
  ];
  const MATCH_MODE_OPTIONS = [
    ["any", "Any term"],
    ["all", "All terms"],
  ];
  const PACK_OPTIONS = [
    ["starter-diverse", "Starter diverse"],
    ["engineering", "Engineering"],
    ["data-software", "Data and software"],
    ["cybersecurity", "Cybersecurity"],
    ["product-design", "Product and design"],
    ["biotech-health", "Biotech and health"],
    ["climate-energy", "Climate and energy"],
    ["public-interest", "Public interest"],
    ["academia-research", "Academia and research"],
    ["fellowships", "Fellowships"],
    ["finance-quant", "Finance and quantitative work"],
    ["ai-research", "AI and research"],
    ["skilled-technical", "Skilled technical"],
    ["national-labs", "National laboratories"],
    ["national-security", "National security"],
  ];
  const dataNode = document.getElementById("opportunity-data");
  let data = {opportunities: [], sources: [], runs: [], counts: {}, display: {}, settings: {}};
  try {
    const parsed = JSON.parse(dataNode ? dataNode.textContent : "{}");
    if (parsed && typeof parsed === "object") data = parsed;
  } catch (_error) {
    data = {opportunities: [], sources: [], runs: [], counts: {}, display: {}, settings: {}};
  }

  const settings = data.settings || {};
  const profilePackOptions = Array.isArray(settings.source_packs) && settings.source_packs.length
    ? settings.source_packs.map((pack) => [
      String(pack && pack.id || "").trim(),
      String(pack && (pack.name || pack.id) || "").trim(),
    ]).filter(([id, label]) => id && label).slice(0, 64)
    : PACK_OPTIONS;
  const restoredView = loadTransientView();
  const byId = new Map((data.opportunities || []).map((item) => [String(item.id), item]));
  const searchIndex = new Map(
    (data.opportunities || []).map((item) => [String(item.id), buildSearchFields(item)])
  );
  const state = {
    view: restoredView.view,
    filter: restoredView.filter,
    query: restoredView.query,
    sort: restoredView.sort,
    page: restoredView.page,
    visibleItems: [],
    busy: false,
    pendingRequest: null,
    pendingAction: null,
    pendingMutation: null,
    workflow: loadWorkflow(),
  };
  let profileCommitted = cloneProfile(settings.profile_editor);
  let profileDraft = cloneProfile(profileCommitted);
  let toastTimer = null;
  let searchTimer = null;
  let requestSequence = 0;
  let profileFieldSequence = 0;

  function loadTransientView() {
    const fallback = {view: "discover", filter: "all", query: "", sort: "fit", page: 1};
    try {
      const parsed = JSON.parse(sessionStorage.getItem(TRANSIENT_VIEW_KEY) || "{}");
      sessionStorage.removeItem(TRANSIENT_VIEW_KEY);
      if (!parsed || typeof parsed !== "object") return fallback;
      return {
        view: VIEW_VALUES.has(parsed.view) ? parsed.view : fallback.view,
        filter: typeof parsed.filter === "string" ? parsed.filter.slice(0, 40) : fallback.filter,
        query: typeof parsed.query === "string" ? parsed.query.slice(0, 240) : fallback.query,
        sort: SORT_VALUES.has(parsed.sort) ? parsed.sort : fallback.sort,
        page: Number.isInteger(parsed.page) ? Math.max(1, parsed.page) : fallback.page,
      };
    } catch (_error) {
      return fallback;
    }
  }

  function saveTransientView() {
    try {
      sessionStorage.setItem(TRANSIENT_VIEW_KEY, JSON.stringify({
        view: state.view,
        filter: state.filter,
        query: state.query,
        sort: state.sort,
        page: state.page,
      }));
    } catch (_error) {
      // View restoration is a convenience and never blocks a scan.
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function normalizeSearchText(value) {
    return String(value || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function buildSearchFields(item) {
    return {
      organization: normalizeSearchText(item.organization),
      title: normalizeSearchText(item.title),
      location: normalizeSearchText(item.location),
      metadata: normalizeSearchText([
        item.category,
        item.opportunity_type,
        item.commitment,
        item.recommended_resume,
      ].join(" ")),
      description: normalizeSearchText(item.description),
    };
  }

  function cloneProfile(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    try {
      const parsed = JSON.parse(JSON.stringify(value));
      return parsed && parsed.version === 1 ? parsed : null;
    } catch (_error) {
      return null;
    }
  }

  function profileStrings(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value.map((entry) => String(entry || "").trim().slice(0, 120)).filter((entry) => {
      const key = normalizeSearchText(entry);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 200);
  }

  function setProfilePath(root, path, value) {
    const keys = String(path || "").split(".").filter(Boolean);
    if (!keys.length) return;
    let cursor = root;
    keys.slice(0, -1).forEach((key, index) => {
      const nextIsIndex = /^\d+$/.test(keys[index + 1]);
      if (!cursor[key] || typeof cursor[key] !== "object") cursor[key] = nextIsIndex ? [] : {};
      cursor = cursor[key];
    });
    cursor[keys[keys.length - 1]] = value;
  }

  function deleteProfilePath(root, path) {
    const keys = String(path || "").split(".").filter(Boolean);
    if (!keys.length) return;
    const parent = keys.slice(0, -1).reduce((value, key) => (
      value && typeof value === "object" ? value[key] : undefined
    ), root);
    if (parent && typeof parent === "object") delete parent[keys[keys.length - 1]];
  }

  function humanizeProfileValue(value) {
    return String(value || "")
      .replaceAll("_", " ")
      .replaceAll("-", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function profileHasContent(profile) {
    if (!profile) return false;
    const candidate = profile.candidate || {};
    const targets = profile.targets || {};
    return Boolean(
      profileStrings(profile.timeframes).length
      || String(candidate.current_stage || "").trim()
      || String(candidate.expected_graduation || "").trim()
      || profileStrings(candidate.completed_degrees).length
      || profileStrings(candidate.skills).length
      || [
        "opportunity_types", "role_families", "domains", "supporting_skills",
        "locations", "exclusions", "work_arrangements",
      ].some((key) => profileStrings(targets[key]).length)
      || String(targets.remote_preference || "").trim()
      || profileStrings(profile.priority_organizations).length
    );
  }

  function nextProfileFieldId(path) {
    profileFieldSequence += 1;
    return "profile-field-" + String(path || "field").replace(/[^a-z0-9]+/gi, "-") + "-" + profileFieldSequence;
  }

  function profileFieldShell(label, path, options) {
    const config = options || {};
    const field = element("div", "profile-field" + (config.wide ? " wide" : ""));
    const id = nextProfileFieldId(path);
    const labelNode = element("label", "profile-label", label);
    labelNode.htmlFor = id;
    field.appendChild(labelNode);
    return {field, id};
  }

  function profileTextField(label, path, value, options) {
    const config = options || {};
    const shell = profileFieldShell(label, path, config);
    const input = element(config.multiline ? "textarea" : "input", config.multiline ? "profile-textarea" : "profile-input");
    input.id = shell.id;
    if (!config.multiline) input.type = config.type || "text";
    input.value = value === undefined || value === null ? "" : String(value);
    input.placeholder = config.placeholder || "";
    if (config.type !== "number") {
      input.maxLength = config.maxLength || (config.multiline ? 1200 : 160);
    }
    input.dataset.profilePath = path;
    input.dataset.profileKind = config.type === "number" ? "number" : "string";
    if (config.type === "number") {
      if (config.min !== undefined) input.min = String(config.min);
      if (config.max !== undefined) input.max = String(config.max);
      input.step = String(config.step || 1);
    }
    input.disabled = Boolean(config.disabled);
    shell.field.appendChild(input);
    if (config.help) shell.field.appendChild(element("span", "profile-help", config.help));
    return shell.field;
  }

  function profileBooleanField(label, path, checked, disabled) {
    const field = element("div", "profile-field");
    const choice = element("label", "profile-choice");
    const input = element("input");
    input.type = "checkbox";
    input.checked = Boolean(checked);
    input.disabled = Boolean(disabled);
    input.dataset.profilePath = path;
    input.dataset.profileKind = "boolean";
    choice.append(input, element("span", "", label));
    field.appendChild(choice);
    return field;
  }

  function profileChoiceField(label, path, selectedValue, options, multiple, disabled, wide) {
    const field = element("div", "profile-field" + (wide ? " wide" : ""));
    field.appendChild(element("span", "profile-label", label));
    const choices = element("div", "profile-choice-list");
    choices.dataset.profilePath = path;
    choices.dataset.profileKind = multiple ? "choices" : "single-choice";
    choices.setAttribute("role", multiple ? "group" : "radiogroup");
    choices.setAttribute("aria-label", label);
    const selected = new Set(multiple ? profileStrings(selectedValue) : [String(selectedValue || "")]);
    const available = new Map((options || []).map(([value, copy]) => [String(value), String(copy)]));
    selected.forEach((value) => {
      if (value && !available.has(value)) available.set(value, humanizeProfileValue(value));
    });
    available.forEach((copy, value) => {
      const choice = element("label", "profile-choice");
      const input = element("input");
      input.type = multiple ? "checkbox" : "radio";
      input.name = multiple ? nextProfileFieldId(path + "-choice") : "profile-choice-" + path;
      input.value = value;
      input.checked = selected.has(value);
      input.disabled = Boolean(disabled);
      choice.append(input, element("span", "", copy));
      choices.appendChild(choice);
    });
    field.appendChild(choices);
    return field;
  }

  function appendProfileTag(editor, value, disabled) {
    const clean = String(value || "").trim().slice(0, 120);
    if (!clean) return;
    const tag = element("span", "profile-tag-value");
    tag.dataset.profileTagValue = clean;
    tag.appendChild(element("span", "", clean));
    if (!disabled) {
      const remove = element("button", "profile-tag-remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + clean);
      remove.addEventListener("click", () => tag.remove());
      tag.appendChild(remove);
    }
    editor.querySelector(".profile-tag-list").appendChild(tag);
  }

  function profileTagField(label, path, values, options) {
    const config = options || {};
    const field = element("div", "profile-field" + (config.wide ? " wide" : ""));
    field.dataset.profilePath = path;
    field.dataset.profileKind = "tags";
    field.appendChild(element("span", "profile-label", label));
    const tags = element("div", "profile-tag-list");
    field.appendChild(tags);
    profileStrings(values).forEach((value) => appendProfileTag(field, value, config.disabled));
    if (!config.disabled) {
      const entry = element("div", "profile-tag-entry");
      const input = element("input", "profile-input");
      input.type = "text";
      input.maxLength = 120;
      input.placeholder = config.placeholder || "Type a value";
      input.setAttribute("aria-label", "Add " + label.toLowerCase());
      const add = element("button", "control subtle profile-tag-add", "Add");
      add.type = "button";
      const addValue = () => {
        const value = input.value.trim();
        const existing = Array.from(field.querySelectorAll("[data-profile-tag-value]"))
          .map((tag) => normalizeSearchText(tag.dataset.profileTagValue));
        const limit = Number.isInteger(config.limit) ? config.limit : 100;
        if (!value || existing.includes(normalizeSearchText(value)) || existing.length >= limit) return;
        appendProfileTag(field, value, false);
        input.value = "";
        input.focus();
      };
      add.addEventListener("click", addValue);
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          addValue();
        }
      });
      entry.append(input, add);
      field.appendChild(entry);
    }
    if (config.help) field.appendChild(element("span", "profile-help", config.help));
    return field;
  }

  function profileSection(title, description) {
    const section = element("section", "profile-section");
    const heading = element("div", "profile-section-heading");
    heading.appendChild(element("h3", "", title));
    if (description) heading.appendChild(element("p", "", description));
    const grid = element("div", "profile-grid");
    section.append(heading, grid);
    return {section, grid};
  }

  function collectProfileForm() {
    const draft = cloneProfile(profileDraft);
    if (!draft) return null;
    document.querySelectorAll("#profile-fields [data-profile-path]").forEach((control) => {
      const path = control.dataset.profilePath;
      const kind = control.dataset.profileKind;
      if (!path || !kind) return;
      if (kind === "tags") {
        const values = Array.from(control.querySelectorAll("[data-profile-tag-value]"))
          .map((tag) => tag.dataset.profileTagValue);
        setProfilePath(draft, path, profileStrings(values));
      } else if (kind === "choices") {
        const values = Array.from(control.querySelectorAll("input:checked")).map((input) => input.value);
        setProfilePath(draft, path, profileStrings(values));
      } else if (kind === "single-choice") {
        const selected = control.querySelector("input:checked");
        setProfilePath(draft, path, selected ? selected.value : "");
      } else if (kind === "boolean") {
        setProfilePath(draft, path, Boolean(control.checked));
      } else if (kind === "number") {
        if (control.value === "") deleteProfilePath(draft, path);
        else setProfilePath(draft, path, Number(control.value));
      } else {
        setProfilePath(draft, path, String(control.value || "").trim().slice(0, 1200));
      }
    });
    const timeframes = profileStrings(draft.timeframes);
    const existingCycles = Array.isArray(draft.targets && draft.targets.cycles) ? draft.targets.cycles : [];
    if (!draft.targets || typeof draft.targets !== "object") draft.targets = {};
    draft.targets.cycles = timeframes.map((label) => {
      const key = normalizeSearchText(label);
      const existing = existingCycles.find((cycle) => (
        cycle && normalizeSearchText(cycle.label) === key
      ));
      return existing ? Object.assign({}, existing, {label}) : null;
    }).filter(Boolean);
    (draft.matching && Array.isArray(draft.matching.rules) ? draft.matching.rules : []).forEach((rule) => {
      ["dimension", "match"].forEach((key) => {
        if (rule[key] === "") delete rule[key];
      });
      if (rule.max_hits === null) delete rule.max_hits;
    });
    draft.version = 1;
    return draft;
  }

  function renderProfileRules(section, rules, disabled) {
    const list = element("div", "profile-rule-list");
    (Array.isArray(rules) ? rules : []).slice(0, 100).forEach((rule, index) => {
      const details = element("details", "profile-rule");
      const label = String(rule && rule.label || "Matching rule " + (index + 1)).slice(0, 100);
      details.appendChild(element("summary", "", label));
      const grid = element("div", "profile-grid");
      grid.append(
        profileTextField("Rule name", "matching.rules." + index + ".label", label, {maxLength: 100, disabled}),
        profileTextField("Weight", "matching.rules." + index + ".weight", rule && rule.weight, {type: "number", min: -100, max: 100, disabled}),
        profileTagField("Keywords and phrases", "matching.rules." + index + ".terms", rule && rule.terms, {wide: true, disabled, placeholder: "Add a keyword"}),
        profileChoiceField("Listing fields", "matching.rules." + index + ".fields", rule && rule.fields, MATCH_FIELD_OPTIONS, true, disabled, true),
        profileChoiceField("Match mode", "matching.rules." + index + ".match", rule && rule.match || "any", MATCH_MODE_OPTIONS, false, disabled, false),
        profileTextField("Dimension", "matching.rules." + index + ".dimension", rule && rule.dimension, {placeholder: "Optional", maxLength: 40, disabled}),
        profileTextField("Maximum hits", "matching.rules." + index + ".max_hits", rule && rule.max_hits, {type: "number", min: 1, max: 100, disabled}),
        profileBooleanField("Score each term", "matching.rules." + index + ".per_term", rule && rule.per_term, disabled),
        profileBooleanField("Anchor rule", "matching.rules." + index + ".anchor", rule && rule.anchor, disabled),
        profileBooleanField("Hard requirement", "matching.rules." + index + ".hard_gate", rule && rule.hard_gate, disabled)
      );
      details.appendChild(grid);
      if (!disabled) {
        const remove = element("button", "profile-remove", "Remove rule");
        remove.type = "button";
        remove.setAttribute("aria-label", "Remove matching rule: " + label);
        remove.addEventListener("click", () => {
          profileDraft = collectProfileForm();
          profileDraft.matching.rules.splice(index, 1);
          renderProfileForm();
        });
        details.appendChild(remove);
      }
      list.appendChild(details);
    });
    section.appendChild(list);
    if (!disabled) {
      const row = element("div", "profile-add-row");
      const add = element("button", "control subtle", "Add matching rule");
      add.type = "button";
      add.addEventListener("click", () => {
        profileDraft = collectProfileForm();
        if (!profileDraft.matching || typeof profileDraft.matching !== "object") profileDraft.matching = {};
        if (!Array.isArray(profileDraft.matching.rules)) profileDraft.matching.rules = [];
        if (profileDraft.matching.rules.length >= 100) return;
        const ids = new Set(profileDraft.matching.rules.map((rule) => String(rule && rule.id || "")));
        let number = profileDraft.matching.rules.length + 1;
        while (ids.has("custom_rule_" + number)) number += 1;
        profileDraft.matching.rules.push({
          id: "custom_rule_" + number,
          label: "New matching rule",
          weight: 5,
          fields: ["title", "description"],
          terms: [],
          match: "any",
        });
        renderProfileForm();
      });
      row.appendChild(add);
      section.appendChild(row);
    }
  }

  function renderDocumentRoutes(section, routes, disabled) {
    const list = element("div", "profile-route-list");
    (Array.isArray(routes) ? routes : []).slice(0, 50).forEach((route, index) => {
      const card = element("div", "profile-route");
      const heading = element("div", "profile-item-heading");
      heading.appendChild(element("strong", "", String(route && route.label || "Document route " + (index + 1))));
      if (!disabled) {
        const remove = element("button", "profile-remove", "Remove");
        remove.type = "button";
        remove.setAttribute("aria-label", "Remove document route: " + String(route && route.label || index + 1));
        remove.addEventListener("click", () => {
          profileDraft = collectProfileForm();
          profileDraft.documents.routes.splice(index, 1);
          renderProfileForm();
        });
        heading.appendChild(remove);
      }
      const grid = element("div", "profile-grid");
      grid.append(
        profileTextField("Document label", "documents.routes." + index + ".label", route && route.label, {maxLength: 120, disabled}),
        profileTagField("Route keywords", "documents.routes." + index + ".terms", route && route.terms, {wide: true, disabled, placeholder: "Add a keyword"}),
        profileChoiceField("Listing fields", "documents.routes." + index + ".fields", route && route.fields, MATCH_FIELD_OPTIONS, true, disabled, true)
      );
      card.append(heading, grid);
      list.appendChild(card);
    });
    section.appendChild(list);
    if (!disabled) {
      const row = element("div", "profile-add-row");
      const add = element("button", "control subtle", "Add document route");
      add.type = "button";
      add.addEventListener("click", () => {
        profileDraft = collectProfileForm();
        if (!profileDraft.documents || typeof profileDraft.documents !== "object") profileDraft.documents = {};
        if (!Array.isArray(profileDraft.documents.routes)) profileDraft.documents.routes = [];
        if (profileDraft.documents.routes.length >= 50) return;
        profileDraft.documents.routes.push({label: "New document route", terms: [], fields: ["title", "description"]});
        renderProfileForm();
      });
      row.appendChild(add);
      section.appendChild(row);
    }
  }

  function renderNumericMap(grid, label, path, value, disabled, options) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return;
    const config = options || {};
    Object.keys(value).sort().slice(0, 30).forEach((key) => {
      grid.appendChild(profileTextField(
        label + " · " + humanizeProfileValue(key),
        path + "." + key,
        value[key],
        {type: "number", min: config.min, max: config.max, step: config.step, disabled}
      ));
    });
  }

  function renderProfileForm() {
    const fields = document.getElementById("profile-fields");
    fields.replaceChildren();
    if (!profileDraft) return;
    profileFieldSequence = 0;
    const disabled = !hasNativeBridge();
    const candidate = profileDraft.candidate && typeof profileDraft.candidate === "object" ? profileDraft.candidate : {};
    const targets = profileDraft.targets && typeof profileDraft.targets === "object" ? profileDraft.targets : {};
    const matching = profileDraft.matching && typeof profileDraft.matching === "object" ? profileDraft.matching : {};
    const thresholds = matching.tier_thresholds && typeof matching.tier_thresholds === "object" ? matching.tier_thresholds : {};
    const documents = profileDraft.documents && typeof profileDraft.documents === "object" ? profileDraft.documents : {};

    const focus = profileSection("Search focus", "Set the time frames and broad source collections you want to follow.");
    focus.grid.append(
      profileTagField("Time frames", "timeframes", profileDraft.timeframes, {wide: true, disabled, limit: 12, placeholder: "Summer 2028", help: "Add more than one if you are considering several cycles."}),
      profileChoiceField("Source packs", "selected_packs", profileDraft.selected_packs, profilePackOptions, true, disabled, true)
    );
    fields.appendChild(focus.section);

    const person = profileSection("About you", "These facts help detect eligibility and experience mismatches.");
    person.grid.append(
      profileChoiceField("Current stage", "candidate.current_stage", candidate.current_stage, CAREER_STAGE_OPTIONS, false, disabled, true),
      profileTextField("Expected graduation", "candidate.expected_graduation", candidate.expected_graduation, {placeholder: "May 2028", maxLength: 80, disabled}),
      profileTextField("Maximum required experience", "candidate.max_required_experience_years", candidate.max_required_experience_years, {type: "number", min: 0, max: 50, help: "Hide roles requiring more years than this.", disabled}),
      profileTagField("Completed degrees", "candidate.completed_degrees", candidate.completed_degrees, {wide: true, disabled, placeholder: "B.S. Physics"}),
      profileTagField("Demonstrated skills", "candidate.skills", candidate.skills, {wide: true, disabled, placeholder: "Python"})
    );
    fields.appendChild(person.section);

    const goals = profileSection("What you want", "Use broad interests here. Detailed matching keywords live in the next section.");
    goals.grid.append(
      profileChoiceField("Opportunity types", "targets.opportunity_types", targets.opportunity_types, OPPORTUNITY_TYPE_OPTIONS, true, disabled, true),
      profileTagField("Role families", "targets.role_families", targets.role_families, {wide: true, disabled, placeholder: "Machine learning research"}),
      profileTagField("Domains", "targets.domains", targets.domains, {wide: true, disabled, placeholder: "Scientific computing"}),
      profileTagField("Supporting skills", "targets.supporting_skills", targets.supporting_skills, {wide: true, disabled, placeholder: "C++"}),
      profileTagField("Locations", "targets.locations", targets.locations, {wide: true, disabled, placeholder: "New York"}),
      profileChoiceField("Work arrangements", "targets.work_arrangements", targets.work_arrangements, WORK_ARRANGEMENT_OPTIONS, true, disabled, true),
      profileChoiceField("Remote preference", "targets.remote_preference", targets.remote_preference, REMOTE_PREFERENCE_OPTIONS, false, disabled, true),
      profileTagField("Exclude", "targets.exclusions", targets.exclusions, {wide: true, disabled, placeholder: "Sales internship", help: "Listings matching these phrases can be penalized or removed."}),
      profileTagField("Priority organizations", "priority_organizations", profileDraft.priority_organizations, {wide: true, disabled, placeholder: "NASA"})
    );
    fields.appendChild(goals.section);

    const scoring = profileSection("Fit settings", "Control visibility and the score bands shown on each listing.");
    scoring.grid.append(
      profileTextField("Starting score", "matching.base_score", matching.base_score, {type: "number", min: 0, max: 100, disabled}),
      profileTextField("Priority organization bonus", "matching.priority_organization_bonus", matching.priority_organization_bonus, {type: "number", min: -100, max: 100, disabled}),
      profileTextField("Minimum score to display", "matching.minimum_display_score", matching.minimum_display_score, {type: "number", min: 0, max: 100, disabled}),
      profileTextField("Priority threshold", "matching.tier_thresholds.priority", thresholds.priority, {type: "number", min: 0, max: 100, disabled}),
      profileTextField("Strong threshold", "matching.tier_thresholds.strong", thresholds.strong, {type: "number", min: 0, max: 100, disabled}),
      profileTextField("Watch threshold", "matching.tier_thresholds.watch", thresholds.watch, {type: "number", min: 0, max: 100, disabled}),
      profileTextField("Minimum anchor strength", "matching.anchor_min_strength", matching.anchor_min_strength, {type: "number", min: 0, max: 1, step: "any", disabled}),
      profileTextField("Target type bonus", "matching.target_type_bonus", matching.target_type_bonus, {type: "number", min: -100, max: 100, disabled}),
      profileTextField("Target time-frame bonus", "matching.target_timeframe_bonus", matching.target_timeframe_bonus, {type: "number", min: -100, max: 100, disabled})
    );
    renderNumericMap(scoring.grid, "Field weight", "matching.field_weights", matching.field_weights, disabled, {min: 0, max: 1, step: "any"});
    renderNumericMap(scoring.grid, "Score ceiling", "matching.score_ceilings", matching.score_ceilings, disabled, {min: 0, max: 100, step: 1});
    fields.appendChild(scoring.section);

    const rules = profileSection("Matching rules", "Each rule connects phrases with listing fields and a score adjustment.");
    rules.grid.remove();
    renderProfileRules(rules.section, matching.rules, disabled);
    fields.appendChild(rules.section);

    const documentSection = profileSection("Application documents", "Choose a default document and route specialized versions by listing terms.");
    documentSection.grid.appendChild(profileTextField("Default document", "documents.default", documents.default, {wide: true, placeholder: "General", maxLength: 120, disabled}));
    renderDocumentRoutes(documentSection.section, documents.routes, disabled);
    fields.appendChild(documentSection.section);
  }

  function renderProfileEditor() {
    const card = document.getElementById("profile-card");
    card.hidden = !profileDraft;
    if (!profileDraft) return;
    const isEmpty = !profileHasContent(profileDraft);
    const button = document.getElementById("edit-profile-button");
    button.textContent = isEmpty ? "Set up profile" : "Edit profile";
    document.getElementById("profile-summary-title").textContent = isEmpty
      ? "Make matches personal"
      : "Your matching preferences";
    const timeframes = profileStrings(profileDraft.timeframes);
    const organizations = profileStrings(profileDraft.priority_organizations);
    const targets = profileDraft.targets && typeof profileDraft.targets === "object" ? profileDraft.targets : {};
    const focusValues = profileStrings([
      ...profileStrings(targets.role_families),
      ...profileStrings(targets.domains),
      ...profileStrings(targets.supporting_skills),
      ...profileStrings(targets.opportunity_types).map(humanizeProfileValue),
    ]);
    const ruleCount = Array.isArray(profileDraft.matching && profileDraft.matching.rules)
      ? profileDraft.matching.rules.length
      : 0;
    const summaryParts = [];
    if (timeframes.length) summaryParts.push(timeframes.length + (timeframes.length === 1 ? " time frame" : " time frames"));
    if (focusValues.length) summaryParts.push(focusValues.length + (focusValues.length === 1 ? " focus" : " focus areas"));
    if (organizations.length) summaryParts.push(organizations.length + (organizations.length === 1 ? " priority organization" : " priority organizations"));
    if (ruleCount) summaryParts.push(ruleCount + (ruleCount === 1 ? " matching rule" : " matching rules"));
    document.getElementById("profile-summary").textContent = isEmpty
      ? "Add time frames, interests, and constraints before your next scan."
      : summaryParts.join(" · ");
    const tags = document.getElementById("profile-summary-tags");
    tags.replaceChildren();
    profileStrings([
      ...timeframes,
      ...focusValues,
      ...organizations,
    ]).slice(0, 4).forEach((value) => tags.appendChild(element("span", "profile-summary-tag", value)));
    const readonly = !hasNativeBridge();
    document.getElementById("profile-readonly-note").hidden = !readonly;
    document.getElementById("profile-save-button").hidden = readonly;
    document.getElementById("profile-cancel-button").textContent = readonly ? "Close" : "Cancel";
    document.getElementById("profile-dialog-title").textContent = isEmpty ? "Set up your profile" : readonly ? "Your search profile" : "Edit your profile";
    document.getElementById("profile-dialog-kicker").textContent = isEmpty ? "First-time setup" : "Search profile";
    renderProfileForm();
  }

  function profileValidationMessage(profile) {
    if (!profileStrings(profile && profile.selected_packs).length) {
      return "Choose at least one source pack.";
    }
    if (profileStrings(profile.timeframes).length > 12) {
      return "Choose no more than 12 time frames.";
    }
    const matching = profile.matching && typeof profile.matching === "object" ? profile.matching : {};
    const thresholds = matching.tier_thresholds && typeof matching.tier_thresholds === "object"
      ? matching.tier_thresholds
      : {};
    const priority = Number(thresholds.priority === undefined ? 75 : thresholds.priority);
    const strong = Number(thresholds.strong === undefined ? 55 : thresholds.strong);
    const watch = Number(thresholds.watch === undefined ? 25 : thresholds.watch);
    if (![priority, strong, watch].every(Number.isFinite) || priority < strong || strong < watch) {
      return "Keep fit thresholds ordered: priority, then strong, then watch.";
    }
    const rules = Array.isArray(matching.rules) ? matching.rules : [];
    let termCount = 0;
    for (let index = 0; index < rules.length; index += 1) {
      const rule = rules[index] || {};
      const terms = profileStrings(rule.terms);
      termCount += terms.length;
      if (!String(rule.label || "").trim()) return "Give matching rule " + (index + 1) + " a name.";
      if (!terms.length) return "Add a keyword or phrase to matching rule " + (index + 1) + ".";
      if (!profileStrings(rule.fields).length) return "Choose a listing field for matching rule " + (index + 1) + ".";
    }
    if (termCount > 1200) return "Use no more than 1,200 matching-rule terms in total.";
    const documents = profile.documents && typeof profile.documents === "object" ? profile.documents : {};
    if (!String(documents.default || "").trim()) return "Name the default application document.";
    const routeLabels = new Set();
    const routes = Array.isArray(documents.routes) ? documents.routes : [];
    for (let index = 0; index < routes.length; index += 1) {
      const route = routes[index] || {};
      const label = normalizeSearchText(route.label);
      if (!label) return "Give document route " + (index + 1) + " a name.";
      if (routeLabels.has(label)) return "Give every document route a different name.";
      routeLabels.add(label);
      if (!profileStrings(route.terms).length) return "Add a keyword or phrase to document route " + (index + 1) + ".";
      if (!profileStrings(route.fields).length) return "Choose a listing field for document route " + (index + 1) + ".";
    }
    return "";
  }

  function resetProfileDraft() {
    profileDraft = cloneProfile(profileCommitted);
    renderProfileEditor();
  }

  function openProfileDialog() {
    if (!profileCommitted) return;
    if (state.busy) {
      showToast("Wait for the current action to finish.");
      return;
    }
    profileDraft = cloneProfile(profileCommitted);
    renderProfileEditor();
    document.getElementById("profile-save-note").textContent = "";
    const dialog = document.getElementById("profile-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function closeProfileDialog() {
    const dialog = document.getElementById("profile-dialog");
    if (typeof dialog.close === "function" && dialog.open) dialog.close();
    else dialog.removeAttribute("open");
    resetProfileDraft();
  }

  function saveProfile(event) {
    event.preventDefault();
    const note = document.getElementById("profile-save-note");
    if (!hasNativeBridge()) {
      note.textContent = "Profile changes are available in the app or CLI.";
      return;
    }
    const payload = collectProfileForm();
    if (!payload) {
      note.textContent = "The profile could not be prepared.";
      return;
    }
    const error = profileValidationMessage(payload);
    if (error) {
      note.textContent = error;
      return;
    }
    profileDraft = payload;
    const request = startNativeAction(
      {action: "profile", profile: payload},
      "Saving profile..."
    );
    note.textContent = request ? "Saving your profile..." : "The profile could not be sent to the app.";
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
        const entry = {};
        if (status) entry.status = status;
        if (Object.prototype.hasOwnProperty.call(value, "bookmarked")) {
          entry.bookmarked = Boolean(value.bookmarked);
        }
        if (Object.keys(entry).length) output[id] = entry;
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

  function focusedListControl() {
    const active = document.activeElement;
    if (!active || !active.closest) return null;
    const actionButton = active.closest("#opportunity-list button[data-action][data-id]");
    if (actionButton && validId(actionButton.dataset.id)) {
      return {kind: "action", id: actionButton.dataset.id, action: actionButton.dataset.action};
    }
    const cardLink = active.closest("#opportunity-list .opportunity a");
    const linkCard = cardLink && cardLink.closest(".opportunity[data-id]");
    if (linkCard && validId(linkCard.dataset.id)) {
      return {kind: "link", id: linkCard.dataset.id};
    }
    return null;
  }

  function restoreListFocus(focus, fallbackIndex) {
    if (!focus) return false;
    const list = document.getElementById("opportunity-list");
    let target = null;
    if (focus.kind === "action") {
      const card = Array.from(list.querySelectorAll(".opportunity"))
        .find((entry) => entry.dataset.id === focus.id);
      target = card && (
        Array.from(card.querySelectorAll("button[data-action]"))
          .find((button) => button.dataset.action === focus.action)
        || card.querySelector('button[data-action]:not([data-action="bookmark"])')
        || card.querySelector("button[data-action]")
      );
    } else if (focus.kind === "link") {
      const card = Array.from(list.querySelectorAll(".opportunity"))
        .find((entry) => entry.dataset.id === focus.id);
      target = card && card.querySelector("a");
    }
    if (!target && Number.isInteger(fallbackIndex)) {
      const item = state.visibleItems[fallbackIndex];
      const card = item && Array.from(list.querySelectorAll(".opportunity"))
        .find((entry) => entry.dataset.id === item.id);
      target = card && card.querySelector("a, button");
    }
    if (!target) return false;
    target.focus({preventScroll: true});
    return true;
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
    const profileActionBusy = state.busy && state.pendingAction === "profile";
    document.getElementById("refresh-button").disabled = state.busy;
    document.getElementById("scan-all-button").disabled = state.busy;
    document.getElementById("scan-state").textContent = state.busy ? String(label || "Working...") : "";
    document.getElementById("profile-save-button").disabled = state.busy;
    document.getElementById("profile-close-button").disabled = profileActionBusy;
    document.getElementById("profile-cancel-button").disabled = profileActionBusy;
    document.getElementById("profile-form").setAttribute("aria-busy", String(profileActionBusy));
    document.getElementById("profile-fields").inert = profileActionBusy;
    document.getElementById("opportunity-list").setAttribute("aria-busy", String(state.busy));
    document.querySelectorAll("#opportunity-list button[data-action]").forEach((button) => {
      button.setAttribute("aria-disabled", String(state.busy));
    });
  }

  function nextRequest() {
    requestSequence = requestSequence >= 999999999 ? 1 : requestSequence + 1;
    return "r" + requestSequence;
  }

  function startNativeAction(message, label) {
    if (state.busy) {
      showToast("Wait for the current action to finish.");
      return "";
    }
    const request = nextRequest();
    state.pendingAction = message.action;
    setBusy(true, label, request);
    if (!postNative(Object.assign({}, message, {request}))) {
      setBusy(false);
      state.pendingAction = null;
      return "";
    }
    return request;
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
      if (!message) {
        showToast("The update could not be sent to the app.");
        return;
      }
      const previousExists = Object.prototype.hasOwnProperty.call(state.workflow, id);
      const previous = Object.assign({}, state.workflow[id] || {});
      const focus = focusedListControl();
      const request = startNativeAction(message, "Updating...");
      if (!request) {
        showToast("The update could not be sent to the app.");
        return;
      }
      state.pendingMutation = {request, id, previousExists, previous, change: Object.assign({}, change), focus};
      state.workflow[id] = Object.assign({}, previous, change);
      renderAll(focus);
      return;
    }
    const previous = Object.assign({}, state.workflow[id] || {});
    const next = Object.assign({}, previous, change);
    state.workflow[id] = next;
    persistWorkflow();
    renderAll();
  }

  function finishPendingMutation(ok) {
    const mutation = state.pendingMutation;
    if (!mutation) return;
    const item = byId.get(mutation.id);
    if (!ok) {
      if (mutation.previousExists) state.workflow[mutation.id] = mutation.previous;
      else delete state.workflow[mutation.id];
      state.pendingMutation = null;
      return;
    }
    if (item && Object.prototype.hasOwnProperty.call(mutation.change, "status")) {
      const previousStatus = String(item.status || "new");
      const nextStatus = mutation.change.status;
      const counts = data.counts || (data.counts = {});
      const bump = (key, delta) => {
        counts[key] = Math.max(0, Number(counts[key] || 0) + delta);
      };
      if (isAvailable(item)) {
        bump("new", Number(nextStatus === "new") - Number(previousStatus === "new"));
      }
      bump("applying", Number(nextStatus === "apply") - Number(previousStatus === "apply"));
      bump("applied", Number(nextStatus === "applied") - Number(previousStatus === "applied"));
      item.status = mutation.change.status;
      item.status_updated_at = new Date().toISOString();
      if (mutation.change.status === "applied" && !item.applied_at) {
        item.applied_at = item.status_updated_at;
      }
    }
    if (item && Object.prototype.hasOwnProperty.call(mutation.change, "bookmarked")) {
      const nextBookmarked = Boolean(mutation.change.bookmarked);
      const previousBookmarked = Boolean(item.bookmarked);
      const counts = data.counts || (data.counts = {});
      counts.bookmarked = Math.max(
        0,
        Number(counts.bookmarked || 0)
          + Number(nextBookmarked)
          - Number(previousBookmarked)
      );
      item.bookmarked = mutation.change.bookmarked ? 1 : 0;
    }
    const remaining = Object.assign({}, state.workflow[mutation.id] || {});
    Object.keys(mutation.change).forEach((key) => { delete remaining[key]; });
    if (Object.keys(remaining).length) state.workflow[mutation.id] = remaining;
    else delete state.workflow[mutation.id];
    state.pendingMutation = null;
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
    if (!value) return "";
    const raw = String(value);
    const date = new Date(raw.length === 10 ? raw + "T12:00:00" : raw);
    return Number.isNaN(date.valueOf())
      ? raw.slice(0, 40)
      : date.toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"});
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
    const value = normalizeSearchText(item.opportunity_type || "opportunity")
      .replace(/[\s-]+/g, "_");
    return value === "full_time" ? "job" : value;
  }

  function typeLabel(item) {
    const value = normalizedType(item);
    if (TYPE_LABELS[value]) return TYPE_LABELS[value];
    return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
  }

  function commitmentLabel(item) {
    const raw = normalizeSearchText(item.commitment).replaceAll("_", " ");
    if (!raw || NON_EMPLOYMENT_COMMITMENTS.has(raw)) return "";
    const aliases = {
      contract: "Contract",
      contractor: "Contractor",
      "fixed term": "Fixed-term",
      "full time": "Full-time",
      internship: "Internship",
      "non exempt": "Non-exempt",
      "part time": "Part-time",
    };
    const label = aliases[raw] || raw.replace(/^./, (letter) => letter.toUpperCase());
    return normalizeSearchText(label) === normalizeSearchText(typeLabel(item)) ? "" : label;
  }

  function dateEntry(item, name) {
    const dates = item.dates && typeof item.dates === "object" ? item.dates : {};
    const aliases = {
      deadline: ["deadline", "deadline_at"],
      first_seen: ["first_seen", "found", "first_seen_at"],
      posted: ["posted", "posting", "posted_at"],
    };
    let raw = null;
    for (const key of aliases[name] || [name]) {
      if (Object.prototype.hasOwnProperty.call(dates, key)) {
        raw = dates[key];
        break;
      }
    }
    if (raw && typeof raw === "object") {
      return {
        value: String(raw.value || raw.at || raw.date || raw.iso || ""),
        status: normalizeSearchText(raw.status || raw.state || raw.kind),
        kind: normalizeSearchText(raw.kind || raw.status || raw.state),
        label: String(raw.label || "").trim().slice(0, 80),
      };
    }
    if (raw) return {value: String(raw), status: "", kind: "", label: ""};
    const legacy = {
      deadline: item.deadline_at,
      first_seen: item.first_seen_at,
      posted: item.posted_at,
    };
    return {value: String(legacy[name] || ""), status: "", kind: "", label: ""};
  }

  function postingMeta(item) {
    const posted = dateEntry(item, "posted");
    if (posted.value) {
      const labels = {posted: "Posted", published: "Posted", updated: "Updated"};
      const prefix = posted.label || labels[posted.kind] || labels[posted.status] || "Listing date";
      return {prefix, value: posted.value};
    }
    const found = dateEntry(item, "first_seen");
    return found.value ? {prefix: found.label || "First found", value: found.value} : null;
  }

  function deadlineMeta(item) {
    const deadline = dateEntry(item, "deadline");
    if (deadline.value) return {prefix: deadline.label || "Deadline", value: deadline.value};
    const labels = {
      date: "Deadline date unavailable",
      "not listed": "Deadline not listed",
      not_listed: "Deadline not listed",
      "open ended": "Open until filled",
      open_ended: "Open until filled",
      "open until filled": "Open until filled",
      open_until_filled: "Open until filled",
      rolling: "Rolling review",
      "rolling review": "Rolling review",
      rolling_review: "Rolling review",
    };
    return {text: deadline.label || labels[deadline.status] || "Deadline not listed"};
  }

  function dateSortValue(item, name, fallback) {
    return dateEntry(item, name).value || fallback;
  }

  function isApplication(status) {
    return status === "apply" || status === "applied";
  }

  function isAvailable(item) {
    return Boolean(item.active) && item.source_enabled !== 0 && item.tier !== "skip";
  }

  function hasWholeTerm(value, term) {
    let position = value.indexOf(term);
    while (position >= 0) {
      const before = position === 0 ? "" : value[position - 1];
      const afterPosition = position + term.length;
      const after = afterPosition >= value.length ? "" : value[afterPosition];
      if ((!before || !/[a-z0-9]/.test(before)) && (!after || !/[a-z0-9]/.test(after))) {
        return true;
      }
      position = value.indexOf(term, position + 1);
    }
    return false;
  }

  function valueRank(value, term, weights) {
    if (!value || !term) return 0;
    if (value === term) return weights[0];
    if (value.startsWith(term)) return weights[1];
    if (hasWholeTerm(value, term)) return weights[2];
    return value.includes(term) ? weights[3] : 0;
  }

  function queryRank(item, phrase, terms) {
    if (!phrase) return 0;
    const fields = searchIndex.get(String(item.id)) || buildSearchFields(item);
    const values = Object.values(fields);
    if (!terms.every((term) => values.some((value) => value.includes(term)))) return -1;
    let rank = 0;
    terms.forEach((term) => {
      rank += Math.max(
        valueRank(fields.organization, term, [1200, 1050, 850, 740]),
        valueRank(fields.title, term, [1180, 1030, 830, 720]),
        valueRank(fields.location, term, [430, 390, 350, 310]),
        valueRank(fields.metadata, term, [330, 300, 270, 240]),
        valueRank(fields.description, term, [140, 130, 115, 100])
      );
    });
    if (terms.length > 1) {
      rank += Math.max(
        valueRank(fields.organization, phrase, [1800, 1500, 1250, 1050]),
        valueRank(fields.title, phrase, [1780, 1480, 1230, 1030]),
        valueRank(fields.location, phrase, [650, 580, 520, 470]),
        valueRank(fields.metadata, phrase, [500, 450, 400, 360]),
        valueRank(fields.description, phrase, [220, 200, 180, 160])
      );
    }
    return rank;
  }

  function compareItems(left, right) {
    if (state.sort === "newest") {
      return dateSortValue(right, "posted", right.first_seen_at || "")
        .localeCompare(dateSortValue(left, "posted", left.first_seen_at || ""));
    }
    if (state.sort === "deadline") {
      return dateSortValue(left, "deadline", "9999")
        .localeCompare(dateSortValue(right, "deadline", "9999"));
    }
    if (state.sort === "organization") {
      return String(left.organization || "").localeCompare(String(right.organization || ""));
    }
    if (state.view === "applications") {
      const leftStatus = effective(left).status === "apply" ? 0 : 1;
      const rightStatus = effective(right).status === "apply" ? 0 : 1;
      if (leftStatus !== rightStatus) return leftStatus - rightStatus;
    }
    return Number(right.score || 0) - Number(left.score || 0)
      || dateSortValue(left, "deadline", "9999")
        .localeCompare(dateSortValue(right, "deadline", "9999"))
      || String(right.first_seen_at || "").localeCompare(String(left.first_seen_at || ""));
  }

  function filteredItems() {
    const phrase = normalizeSearchText(state.query).slice(0, 240);
    const terms = Array.from(new Set(phrase.split(" ").filter(Boolean))).slice(0, 12);
    const ranked = [];
    (data.opportunities || []).forEach((item) => {
      const workflow = effective(item);
      if (state.view === "discover" && isApplication(workflow.status)) return;
      if (state.view === "discover" && !isAvailable(item) && !["saved", "dismissed"].includes(state.filter)) return;
      if (state.view === "discover" && workflow.status === "skip" && state.filter !== "dismissed") return;
      if (state.view === "applications" && !isApplication(workflow.status)) return;
      if (state.filter === "saved" && !workflow.bookmarked) return;
      if (state.filter === "dismissed" && workflow.status !== "skip") return;
      if (state.filter === "priority" && item.tier !== "priority") return;
      if (state.filter === "internship" && !normalizedType(item).includes("intern")) return;
      if (state.filter === "fellowship" && !normalizedType(item).includes("fellow")) return;
      if (state.filter === "job" && normalizedType(item) !== "job") return;
      if (state.filter === "apply" && workflow.status !== "apply") return;
      if (state.filter === "applied" && workflow.status !== "applied") return;
      const relevance = queryRank(item, phrase, terms);
      if (relevance >= 0) ranked.push({item, relevance});
    });
    ranked.sort((left, right) => {
      if (phrase && left.relevance !== right.relevance) return right.relevance - left.relevance;
      return compareItems(left.item, right.item);
    });
    return ranked.map((entry) => entry.item);
  }

  function addMeta(container, text, className) {
    if (text) container.appendChild(element("span", "meta-item" + (className ? " " + className : ""), text));
  }

  function addDateMeta(container, value) {
    if (!value) return;
    if (value.text) {
      addMeta(container, value.text, "date-meta muted-date");
      return;
    }
    if (!value.value) return;
    const wrap = element("span", "meta-item date-meta");
    wrap.appendChild(document.createTextNode(value.prefix + " "));
    const timeNode = element("time", "", formatDate(value.value));
    timeNode.dateTime = String(value.value).slice(0, 100);
    wrap.appendChild(timeNode);
    container.appendChild(wrap);
  }

  function addTag(container, text, className) {
    if (text) container.appendChild(element("span", "tag" + (className ? " " + className : ""), text));
  }

  function matchEvidenceLabel(value) {
    if (value && typeof value === "object") {
      const term = String(value.term || value.label || value.value || value.text || "").trim();
      const field = String(value.field || "").trim();
      if (term && field) return term + " · " + humanizeProfileValue(field);
      return term || field;
    }
    return String(value || "").trim();
  }

  function matchComponents(item) {
    const raw = item.match && Array.isArray(item.match.components)
      ? item.match.components
      : [];
    const structured = raw.filter((component) => (
      component
      && typeof component === "object"
      && component.id !== "base"
      && Number(component.points || 0) > 0
      && String(component.label || "").trim()
    )).map((component) => ({
      label: String(component.label).trim().slice(0, 80),
      evidence: Array.isArray(component.evidence)
        ? component.evidence.map(matchEvidenceLabel).filter(Boolean).slice(0, 6)
        : [],
    }));
    if (structured.length) return structured.slice(0, 8);
    return (Array.isArray(item.reasons) ? item.reasons : []).slice(0, 6).map((reason) => {
      const text = String(reason || "").trim();
      const separator = text.indexOf(":");
      return separator > 0
        ? {label: text.slice(0, separator).trim(), evidence: text.slice(separator + 1).split(",").map((value) => value.trim()).filter(Boolean)}
        : {label: text, evidence: []};
    }).filter((component) => component.label);
  }

  function componentChip(component) {
    const chip = element("span", "match-chip");
    chip.appendChild(element("strong", "", component.label));
    if (component.evidence.length) {
      const evidence = component.evidence.slice(0, 3).join(", ");
      chip.appendChild(element("span", "", evidence));
      chip.title = component.label + ": " + component.evidence.join(", ");
    }
    return chip;
  }

  function appendMatchSummary(article, item) {
    const components = matchComponents(item);
    if (!components.length) {
      article.appendChild(element("p", "reason", settings.default_reason || "Matches your current profile."));
      return;
    }
    const block = element("div", "match-summary");
    block.setAttribute("role", "group");
    block.setAttribute("aria-label", "Why this opportunity matched");
    block.appendChild(element("p", "match-heading", "Why it matched"));
    const chips = element("div", "match-chips");
    components.slice(0, 3).forEach((component) => chips.appendChild(componentChip(component)));
    block.appendChild(chips);
    if (components.length > 3) {
      const details = element("details", "match-more");
      details.appendChild(element("summary", "", "+" + (components.length - 3) + " more"));
      const list = element("ul", "match-more-list");
      components.slice(3).forEach((component) => {
        const copy = component.evidence.length
          ? component.label + ": " + component.evidence.join(", ")
          : component.label;
        list.appendChild(element("li", "", copy));
      });
      details.appendChild(list);
      block.appendChild(details);
    }
    article.appendChild(block);
  }

  function cardFor(item) {
    const workflow = effective(item);
    const accessibleTitle = String(item.title || "opportunity").slice(0, 120);
    const accessibleOrganization = String(item.organization || "organization").slice(0, 80);
    const actionContext = accessibleTitle + " at " + accessibleOrganization;
    const article = element("article", "opportunity");
    article.dataset.id = item.id;
    const head = element("div", "op-head");
    const titleGroup = element("div");
    titleGroup.appendChild(element("p", "organization", item.organization || "Organization not listed"));
    titleGroup.appendChild(element("h2", "", item.title || "Untitled opportunity"));
    const tier = ["priority", "strong", "watch", "skip"].includes(item.tier) ? item.tier : "watch";
    const score = element("div", "fit-score " + tier);
    score.setAttribute("role", "img");
    score.setAttribute("aria-label", tier.replace(/^./, (letter) => letter.toUpperCase()) + " fit, score " + Number(item.score || 0) + " out of 100");
    score.title = "Configured fit score, not acceptance probability";
    score.appendChild(element("strong", "", Number(item.score || 0)));
    score.appendChild(element("span", "", tier));
    head.append(titleGroup, score);
    article.appendChild(head);

    const meta = element("div", "meta");
    addMeta(meta, item.location || "Location not listed");
    addDateMeta(meta, postingMeta(item));
    addDateMeta(meta, deadlineMeta(item));
    if (workflow.status === "applied" && item.applied_at) {
      addDateMeta(meta, {prefix: "Applied", value: item.applied_at});
    }
    article.appendChild(meta);

    const tags = element("div", "tags");
    addTag(tags, typeLabel(item), "kind");
    addTag(tags, commitmentLabel(item), "commitment");
    if (normalizeSearchText(item.match && item.match.eligibility) === "unknown") {
      addTag(tags, "Eligibility details incomplete", "eligibility");
    }
    if (item.recommended_resume) addTag(tags, (settings.document_label || "Application track") + " · " + item.recommended_resume, "track");
    if (workflow.status === "apply") addTag(tags, "Preparing application", "stage");
    if (workflow.status === "applied") addTag(tags, "Applied", "stage");
    if (!isAvailable(item)) addTag(tags, "Listing no longer active", "warning");
    const warning = Array.isArray(item.warnings) ? item.warnings[0] : "";
    if (warning) addTag(tags, String(warning).slice(0, 120), "warning");
    if (tags.childNodes.length) article.appendChild(tags);

    appendMatchSummary(article, item);

    const actions = element("div", "card-actions");
    const official = safeUrl(item.url);
    if (official) {
      const link = element("a", "official-link", "Open listing");
      link.href = official;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", "Open listing: " + actionContext);
      actions.appendChild(link);
    }
    const save = element("button", "card-button" + (workflow.bookmarked ? " selected" : ""), workflow.bookmarked ? "Saved" : "Save");
    save.type = "button";
    save.dataset.action = "bookmark";
    save.dataset.id = item.id;
    save.setAttribute("aria-pressed", String(workflow.bookmarked));
    save.setAttribute("aria-label", (workflow.bookmarked ? "Remove saved listing: " : "Save listing: ") + actionContext);
    actions.appendChild(save);

    if (workflow.status === "apply") {
      const applied = element("button", "card-button selected", "Mark applied");
      applied.type = "button";
      applied.dataset.action = "applied";
      applied.dataset.id = item.id;
      applied.setAttribute("aria-label", "Mark applied: " + actionContext);
      actions.appendChild(applied);
    } else if (workflow.status === "applied") {
      const planned = element("button", "card-button", "Move to preparing");
      planned.type = "button";
      planned.dataset.action = "apply";
      planned.dataset.id = item.id;
      planned.setAttribute("aria-label", "Move to preparing: " + actionContext);
      actions.appendChild(planned);
    } else {
      const plan = element("button", "card-button", "Plan application");
      plan.type = "button";
      plan.dataset.action = "apply";
      plan.dataset.id = item.id;
      plan.setAttribute("aria-label", "Plan application: " + actionContext);
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
    remove.setAttribute("aria-label", (isDismissed ? "Restore listing: " : state.view === "applications" ? "Remove from applications: " : "Dismiss listing: ") + actionContext);
    actions.appendChild(remove);
    actions.querySelectorAll("button[data-action]").forEach((button) => {
      button.setAttribute("aria-disabled", String(state.busy));
    });
    article.appendChild(actions);
    return article;
  }

  function updateListMeta() {
    const items = state.visibleItems;
    const display = data.display || {};
    const countLabel = items.length + (items.length === 1 ? " opportunity" : " opportunities");
    const start = items.length ? (state.page - 1) * PAGE_SIZE + 1 : 0;
    const end = Math.min(items.length, state.page * PAGE_SIZE);
    const rangeLabel = items.length ? start + "-" + end + " of " + countLabel : countLabel;
    document.getElementById("results-note").textContent = (
      display.discovery_truncated && state.view === "discover" && state.filter === "all" && !state.query
        ? rangeLabel + " loaded from " + Number(display.discovery_total || items.length) + ". Showing the highest-fit records."
        : rangeLabel
    );
    document.getElementById("clear-filters").hidden = !state.query && state.filter === "all";
  }

  function pageCount() {
    return Math.max(1, Math.ceil(state.visibleItems.length / PAGE_SIZE));
  }

  function paginationValues(total, current) {
    if (total <= 7) return Array.from({length: total}, (_value, index) => index + 1);
    const pages = new Set([1, total, current - 1, current, current + 1]);
    if (current <= 3) [2, 3, 4].forEach((page) => pages.add(page));
    if (current >= total - 2) [total - 3, total - 2, total - 1].forEach((page) => pages.add(page));
    const sorted = Array.from(pages).filter((page) => page > 0 && page <= total).sort((left, right) => left - right);
    const values = [];
    sorted.forEach((page, index) => {
      if (index && page - sorted[index - 1] > 1) values.push(null);
      values.push(page);
    });
    return values;
  }

  function renderPagination() {
    const nav = document.getElementById("pagination");
    const total = pageCount();
    nav.hidden = state.visibleItems.length === 0 || total <= 1;
    document.getElementById("page-previous").disabled = state.page <= 1;
    document.getElementById("page-next").disabled = state.page >= total;
    const numbers = document.getElementById("page-numbers");
    numbers.replaceChildren();
    paginationValues(total, state.page).forEach((page) => {
      if (page === null) {
        const ellipsis = element("span", "page-ellipsis", "…");
        ellipsis.setAttribute("aria-hidden", "true");
        numbers.appendChild(ellipsis);
        return;
      }
      const button = element("button", "page-button" + (page === state.page ? " active" : ""), page);
      button.type = "button";
      button.dataset.page = String(page);
      button.setAttribute("aria-label", "Page " + page);
      if (page === state.page) button.setAttribute("aria-current", "page");
      numbers.appendChild(button);
    });
    document.getElementById("page-status").textContent = "Page " + state.page + " of " + total;
  }

  function renderPage() {
    const list = document.getElementById("opportunity-list");
    const fragment = document.createDocumentFragment();
    const start = (state.page - 1) * PAGE_SIZE;
    const end = Math.min(state.visibleItems.length, start + PAGE_SIZE);
    state.visibleItems.slice(start, end).forEach((item, index) => {
      const card = cardFor(item);
      card.setAttribute("role", "listitem");
      card.setAttribute("aria-posinset", String(start + index + 1));
      card.setAttribute("aria-setsize", String(state.visibleItems.length));
      fragment.appendChild(card);
    });
    list.replaceChildren(fragment);
    renderPagination();
  }

  function renderList() {
    const list = document.getElementById("opportunity-list");
    state.visibleItems = filteredItems();
    state.page = Math.min(Math.max(1, state.page), pageCount());
    list.replaceChildren();
    if (!state.visibleItems.length) {
      list.removeAttribute("role");
      document.getElementById("pagination").hidden = true;
      const empty = element("div", "empty");
      const firstRun = !(data.opportunities || []).length && !(data.runs || []).length;
      const heading = state.view === "applications"
        ? "No applications here yet."
        : firstRun
          ? "Ready for your first scan."
          : state.query || state.filter !== "all"
            ? "Nothing matches this view."
            : "No opportunities found yet.";
      const guidance = state.view === "applications"
        ? " Choose Plan application on a listing to start tracking it."
        : firstRun
          ? " Use Refresh above, or run python3 -m monitor scan in a terminal."
          : state.query || state.filter !== "all"
            ? " Try a broader search or clear the filters."
            : " Check another source pack or run Scan all.";
      empty.append(element("strong", "", heading));
      empty.append(document.createTextNode(guidance));
      list.appendChild(empty);
      updateListMeta();
      return;
    }
    list.setAttribute("role", "list");
    renderPage();
    updateListMeta();
  }

  function changePage(nextPage) {
    const total = pageCount();
    const page = Math.min(total, Math.max(1, Number(nextPage) || 1));
    if (page === state.page) return;
    state.page = page;
    renderPage();
    updateListMeta();
    const results = document.getElementById("results");
    results.focus({preventScroll: true});
    results.scrollIntoView({block: "start"});
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
    const open = all.filter((item) => isAvailable(item)).length;
    const newest = all.filter((item) => effective(item).status === "new" && isAvailable(item)).length;
    const saved = all.filter((item) => effective(item).bookmarked).length;
    const applications = all.filter((item) => isApplication(effective(item).status));
    const applied = applications.filter((item) => effective(item).status === "applied").length;
    const counts = data.counts || {};
    const adjusted = (key, originalPredicate, effectivePredicate, fallback) => {
      const base = Number.isFinite(Number(counts[key])) ? Number(counts[key]) : fallback;
      return Math.max(0, base + all.reduce((delta, item) => (
        delta + Number(Boolean(effectivePredicate(item))) - Number(Boolean(originalPredicate(item)))
      ), 0));
    };
    const originalAvailable = (item) => Boolean(item.active) && item.source_enabled !== 0 && item.tier !== "skip";
    const currentNew = (item) => originalAvailable(item) && effective(item).status === "new";
    const originalNew = (item) => originalAvailable(item) && String(item.status || "new") === "new";
    const currentSaved = (item) => effective(item).bookmarked;
    const originalSaved = (item) => Boolean(item.bookmarked);
    const currentApplied = (item) => effective(item).status === "applied";
    const originalApplied = (item) => String(item.status || "new") === "applied";
    const currentApplication = (item) => isApplication(effective(item).status);
    const originalApplication = (item) => isApplication(String(item.status || "new"));
    document.getElementById("stat-active").textContent = String(Number.isFinite(Number(counts.active)) ? Number(counts.active) : open);
    document.getElementById("stat-new").textContent = String(adjusted("new", originalNew, currentNew, newest));
    document.getElementById("stat-saved").textContent = String(adjusted("bookmarked", originalSaved, currentSaved, saved));
    document.getElementById("stat-applied").textContent = String(adjusted("applied", originalApplied, currentApplied, applied));
    const storedApplications = Number.isFinite(Number(counts.applying))
      && Number.isFinite(Number(counts.applied))
      ? Number(counts.applying) + Number(counts.applied)
      : all.filter(originalApplication).length;
    document.getElementById("application-count").textContent = String(
      Math.max(0, storedApplications + all.reduce((delta, item) => (
        delta + Number(currentApplication(item)) - Number(originalApplication(item))
      ), 0))
    );
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
      : "Run your first scan to initialize source health.";
    const mark = document.getElementById("health-mark");
    mark.className = "health-mark" + (errors.length ? " attention" : healthy.length ? " ok" : "");
    const list = document.getElementById("source-list");
    list.replaceChildren();
    sources.forEach((source) => {
      const row = element("div", "source");
      const dot = element("i", "source-dot " + (["ok", "error", "blocked"].includes(source.last_status) ? source.last_status : "never"));
      const copy = element("div");
      const url = safeUrl(source.url);
      const name = element(url ? "a" : "div", "source-name", source.name || source.id);
      if (url) {
        name.href = url;
        name.target = "_blank";
        name.rel = "noopener noreferrer";
        name.title = "Open official source";
      }
      copy.appendChild(name);
      const count = Number(source.item_count || 0);
      const kind = source.kind === "watch_page" ? "manual page" : "listing feed";
      copy.appendChild(element("span", "source-detail", kind + " - " + count + (count === 1 ? " item" : " items") + " - " + relative(source.last_checked_at)));
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

  function renderAll(preferredFocus) {
    const focus = preferredFocus || focusedListControl();
    renderFilters();
    renderCounts();
    renderList();
    if (!focus) return;
    if (restoreListFocus(focus)) return;
    if (focus.kind === "action" && ["apply", "applied"].includes(focus.action)) {
      document.querySelector('.view-button[data-view="applications"]').focus({preventScroll: true});
      return;
    }
    const note = document.getElementById("results-note");
    note.tabIndex = -1;
    note.focus({preventScroll: true});
  }

  function setTheme(theme, notifyNative) {
    const value = THEME_VALUES.has(theme) ? theme : "system";
    document.documentElement.dataset.theme = value;
    document.querySelectorAll("[data-theme-option]").forEach((button) => {
      const active = button.dataset.themeOption === value;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    try { localStorage.setItem("opportunity-radar-theme", value); } catch (_error) { /* optional */ }
    if (notifyNative) postNative({action: "theme", theme: value});
  }

  function configuredTimeframes() {
    const configured = Array.isArray(settings.timeframes)
      ? settings.timeframes
      : settings.timeframes
        ? [settings.timeframes]
        : [];
    const raw = configured.length
      ? configured
      : settings.target_season
        ? [settings.target_season]
        : [];
    return raw.map((value) => {
      if (value && typeof value === "object") return value.label || value.name || value.value || "";
      return value;
    }).map((value) => String(value || "").trim().slice(0, 80)).filter(Boolean).slice(0, 8);
  }

  window.OpportunityRadarNative = {
    complete(result) {
      if (
        !result
        || String(result.request || "") !== state.pendingRequest
        || String(result.action || "") !== state.pendingAction
      ) return;
      const action = state.pendingAction;
      const ok = result.ok === true;
      const mutationFocus = state.pendingMutation && state.pendingMutation.focus;
      if (state.pendingMutation && state.pendingMutation.request === state.pendingRequest) {
        finishPendingMutation(ok);
      }
      if (["scan", "profile"].includes(action) && ok) saveTransientView();
      state.pendingAction = null;
      setBusy(false);
      if (action === "profile") {
        const note = document.getElementById("profile-save-note");
        if (ok) {
          profileCommitted = cloneProfile(result.profile) || cloneProfile(profileDraft);
          closeProfileDialog();
        } else {
          note.textContent = String(result.message || "The profile could not be saved.").slice(0, 240);
        }
      }
      renderAll(mutationFocus);
      if (result && result.message) showToast(result.message);
    },
    setTheme(theme) {
      setTheme(String(theme || "system"), false);
    },
  };

  const title = settings.title || "Opportunity Radar";
  document.title = title;
  document.getElementById("dashboard-title").textContent = title;
  document.getElementById("dashboard-subtitle").textContent = settings.subtitle || "Review matches and track applications from the sources you follow.";
  const timeframes = configuredTimeframes();
  const context = document.getElementById("search-context");
  context.textContent = timeframes.length > 3
    ? timeframes.slice(0, 2).join(" · ") + " · +" + (timeframes.length - 2) + " more"
    : timeframes.length
      ? timeframes.join(" · ")
      : "Opportunity search";
  if (timeframes.length > 1) {
    const timeframeLabel = "Time frames: " + timeframes.join(", ");
    context.setAttribute("aria-label", timeframeLabel);
    context.title = timeframeLabel;
  }
  document.getElementById("last-scan").textContent = "Updated " + relative(data.generated_at);

  let initialTheme = "system";
  try { initialTheme = localStorage.getItem("opportunity-radar-theme") || "system"; } catch (_error) { /* optional */ }
  setTheme(initialTheme, false);

  document.getElementById("search").value = state.query;
  document.getElementById("sort").value = state.sort;
  document.querySelectorAll(".view-button").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });

  document.getElementById("theme-switcher").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-theme-option]");
    if (button) setTheme(button.dataset.themeOption, true);
  });
  document.getElementById("refresh-button").addEventListener("click", () => scan("due"));
  document.getElementById("scan-all-button").addEventListener("click", () => {
    const dialog = document.getElementById("scan-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else if (window.confirm("Check every enabled source now?")) scan("all");
  });
  document.getElementById("scan-dialog").addEventListener("close", (event) => {
    if (event.target.returnValue === "confirm") scan("all");
  });
  document.getElementById("edit-profile-button").addEventListener("click", openProfileDialog);
  document.getElementById("profile-close-button").addEventListener("click", closeProfileDialog);
  document.getElementById("profile-cancel-button").addEventListener("click", closeProfileDialog);
  document.getElementById("profile-form").addEventListener("submit", saveProfile);
  document.getElementById("profile-dialog").addEventListener("close", resetProfileDraft);
  document.getElementById("search").addEventListener("input", (event) => {
    state.query = String(event.target.value || "").trim();
    state.page = 1;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(renderList, SEARCH_DEBOUNCE_MS);
  });
  document.getElementById("sort").addEventListener("change", (event) => {
    state.sort = SORT_VALUES.has(event.target.value) ? event.target.value : "fit";
    state.page = 1;
    renderList();
  });
  document.getElementById("filter-row").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-filter]");
    if (!button) return;
    state.filter = button.dataset.filter;
    state.page = 1;
    renderAll();
    const replacement = Array.from(document.querySelectorAll("#filter-row button[data-filter]"))
      .find((entry) => entry.dataset.filter === state.filter);
    if (replacement) replacement.focus();
  });
  document.getElementById("clear-filters").addEventListener("click", () => {
    state.filter = "all";
    state.query = "";
    state.page = 1;
    window.clearTimeout(searchTimer);
    document.getElementById("search").value = "";
    renderAll();
  });
  document.querySelector(".view-switcher").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (!button || !["discover", "applications"].includes(button.dataset.view)) return;
    state.view = button.dataset.view;
    state.filter = "all";
    state.page = 1;
    document.querySelectorAll(".view-button").forEach((entry) => {
      const active = entry === button;
      entry.classList.toggle("active", active);
      entry.setAttribute("aria-pressed", String(active));
    });
    renderAll();
  });
  document.getElementById("pagination").addEventListener("click", (event) => {
    const pageButton = event.target.closest("button[data-page]");
    if (pageButton) {
      changePage(pageButton.dataset.page);
      return;
    }
    const direction = event.target.closest("button[data-page-direction]");
    if (!direction) return;
    changePage(state.page + (direction.dataset.pageDirection === "next" ? 1 : -1));
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
  document.querySelector(".skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    const results = document.getElementById("results");
    results.focus({preventScroll: true});
    results.scrollIntoView({block: "start"});
  });

  renderProfileEditor();
  renderSources();
  renderEvents();
  renderAll();
}());
