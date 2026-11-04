// Exercise production dashboard functions with anonymous in-memory state.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../dashboard/app.js"), "utf8");
const sandbox = {
  opportunityRows: [],
  state: {view: "discover", filter: "all", query: "", sort: "fit", kind: "all", deadline: "all"},
  searchIndex: new Map(),
  duplicateIndex: new Map(),
  URL,
  effective: (item) => ({status: item.status || "new", bookmarked: Boolean(item.bookmarked)}),
  humanizeProfileValue: (value) => String(value).replaceAll("_", " "),
  matchComponents: () => [],
  element: (tag, className, text) => ({tag, className, textContent: text || "", childNodes: [],
    setAttribute(name, value) { this[name] = value; },
    appendChild(child) { this.childNodes.push(child); }}),
};
vm.createContext(sandbox);
for (const name of ["TYPE_LABELS", "ID_PATTERN"]) {
  const start = source.indexOf("  const " + name + " =");
  const end = source.indexOf(";", start) + 1;
  assert(start >= 0 && end > start, name);
  vm.runInContext(source.slice(start, end), sandbox);
}
for (const name of ["boundedInlineText", "normalizeSearchText", "buildSearchFields",
  "normalizedType", "typeLabel", "isAvailable", "isCollected", "isTracked", "belongsInView",
  "localCalendarDate", "matchesDeadline", "scheduleValue", "dateEntry", "dateSortValue", "compareNewestItems",
  "compareItems", "validId", "safeUrl", "discoveryIdentity", "addDateMeta", "formatDate",
  "isApplication", "hasWholeTerm", "valueRank", "queryRank", "filteredItems", "filtersForView",
  "appendListingDetails", "appendMatchSummary", "appendMatchAudit", "sourceResourceSearchText",
  "matchingSourceResources", "matchingCompanyResources"]) {
  const start = source.indexOf("  function " + name + "(");
  const end = source.indexOf("\n  function ", start + 1);
  assert(start >= 0 && end > start, name);
  vm.runInContext(source.slice(start, end), sandbox);
}
const record = (id, kind, extra = {}) => ({id, title: "Research Engineer", organization: "Example",
  opportunity_type: kind, active: 1, source_enabled: 1, score: 70, tier: "strong", ...extra});
sandbox.opportunityRows = [record("a", "research_program"), record("b", "co_op"),
  record("c", "scholarship"), record("d", "fellowship", {tier: "skip", bookmarked: true}),
  record("e", "internship", {status: "applied", active: 0}),
  record("f", "job", {title: "Data Scientist", organization: "Museum of Modern Art", tier: "skip"}),
  record("g", "event", {title: "Civic Coding Weekend", facts: {kind: "hackathon", availability: "listed"},
    deadline_at: sandbox.localCalendarDate(4)}),
  record("h", "event", {facts: {availability: "closed"}, deadline_at: sandbox.localCalendarDate(-1)}),
  record("i", "grant", {status: "skip", tier: "skip"}),
];
const ids = () => Array.from(sandbox.filteredItems(), (item) => item.id);
assert.deepEqual(ids(), ["g", "a", "b", "c"]);
const filters = Array.from(sandbox.filtersForView(), (entry) => entry[0]);
assert(filters.includes("collected"));
sandbox.state.kind = "co_op";
assert.deepEqual(ids(), ["b"]);
sandbox.state.kind = "hackathon";
assert.deepEqual(ids(), ["g"], "Canonical opportunity type drives the facet");
sandbox.state.kind = "all";
sandbox.state.deadline = "week";
assert.deepEqual(ids(), ["g"]);
sandbox.state.deadline = "all";
sandbox.state.view = "applications";
sandbox.state.filter = "saved";
assert.deepEqual(ids(), ["d"], "Saved-only listings survive a changed fit in the tracker");
sandbox.state.filter = "all";
assert.deepEqual(ids(), ["d", "e"], "Inactive applications remain available");
sandbox.state.filter = "dismissed";
assert.deepEqual(ids(), ["i"], "Dismissed entries remain available for restoration");
sandbox.state.view = "discover";
sandbox.state.filter = "all";
sandbox.state.query = "example research";
assert.deepEqual(ids(), ["a", "b", "c"]);
sandbox.state.query = "Museum of Modern Art";
assert.deepEqual(ids(), []);
sandbox.state.filter = "collected";
assert.deepEqual(ids(), ["f"], "A role at an unrelated organization is discoverable outside profile fit");
sandbox.state.query = "example";
sandbox.state.sort = "deadline";
assert.equal(ids()[0], "h", "The selected sort is respected during search");
sandbox.state.query = "missing";
assert.deepEqual(ids(), []);
sandbox.state.query = "";
sandbox.state.sort = "fit";
const sharedUrl = "https://example.org/details?posting=2";
sandbox.opportunityRows = [
  record("duplicate-a", "job", {url: sharedUrl}),
  record("duplicate-b", "job", {url: sharedUrl + "&utm_source=feed"}),
  record("distinct", "job", {url: "https://example.org/details?posting=3"}),
];
assert.deepEqual(ids(), ["duplicate-a", "distinct"], "Only known tracking parameters are ignored");
const article = sandbox.element("article", "");
const savedMismatch = {tier: "skip", match: {engine: "structured_v2",
  visibility: {anchor_matched: false, interest_matched: false, ceilings: [{id: "no_anchor", score: 49}]},
  gates: [{id: "degree_stage", state: "unknown", evidence: ["Details incomplete"]}]}};
sandbox.appendMatchSummary(article, savedMismatch);
sandbox.appendMatchAudit(article, savedMismatch);
const text = (node) => node.textContent + node.childNodes.map(text).join(" ");
assert(text(article).includes("Outside your current profile"));
assert(text(article).includes("No evidence for your preferred roles or fields"));
assert(text(article).includes("Needs verification"));
assert(text(article).includes("score limited to 49"));

const listing = sandbox.element("article", "");
sandbox.appendListingDetails(listing, {
  id: "a".repeat(24), description: "Evaluate public services and receive a monthly stipend.",
  eligibility: "Open to enrolled graduate students.",
  facts: {
    activities: [{label: "Policy research", evidence: "Evaluate public services."}],
    benefits: [{label: "Stipend", evidence: "Monthly stipend"}],
    requirements: [{label: "Enrolled students", evidence: "Open to enrolled graduate students."}],
    source: {name: "Boston programs", url: "javascript:alert(1)"},
    schedule: {starts_at: "2027-03-01", attendance: "https://schema.org/MixedEventAttendanceMode"},
  },
});
for (const label of ["monthly stipend", "What you would do", "Enrolled students", "Boston programs", "Attendance · Hybrid"]) {
  assert(text(listing).includes(label), label);
}
const attribution = listing.childNodes[1].childNodes[1].childNodes.find((node) => node.className === "listing-attribution");
assert.equal(attribution.childNodes[0].tag, "strong", "Unsafe source URLs do not produce links");
const preview = sandbox.element("article", "");
sandbox.appendListingDetails(preview, {id: "b".repeat(24), detail_available: false});
assert(text(preview).includes("python3 -m monitor opportunities show"));

sandbox.sourceResources = [{name: "Bose", source_type: "manual_page", support_level: "manual", packs: ["engineering"], domains: ["acoustics"], role_families: ["software engineer", "accountant"], opportunity_types: ["internship"], career_levels: ["new_grad"]}];
sandbox.sourceResourceView = {query: "software engineer"};
assert.equal(sandbox.matchingSourceResources().length, 1);
sandbox.sourceResourceView.query = "internship";
assert.equal(sandbox.matchingSourceResources().length, 1);
sandbox.sourceResourceView.query = "new grad";
assert.equal(sandbox.matchingSourceResources().length, 1);
sandbox.sourceResourceView.query = "acoustics";
assert.equal(sandbox.matchingSourceResources().length, 1);
sandbox.sourceResourceView.query = "accountant";
assert.equal(sandbox.matchingSourceResources().length, 1);
sandbox.sourceResourceView.query = "clinical residency";
assert.equal(sandbox.matchingSourceResources().length, 0);
sandbox.state.view = "discover";
sandbox.state.query = "bose";
assert.equal(sandbox.matchingCompanyResources().length, 1);
sandbox.state.query = "not a company";
assert.equal(sandbox.matchingCompanyResources().length, 0);
sandbox.state.query = "bose";
sandbox.state.view = "saved";
assert.equal(sandbox.matchingCompanyResources().length, 0);

console.log("Dashboard behavior checks passed");
