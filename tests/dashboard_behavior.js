// Exercise production dashboard functions with anonymous in-memory state.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../dashboard/app.js"), "utf8");
const sandbox = {
  opportunityRows: [],
  state: {view: "discover", filter: "all", query: "", sort: "fit", kind: "all", deadline: "all", activity: "all", benefit: "all"},
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
for (const name of ["TYPE_LABELS", "ID_PATTERN", "SORT_VALUES", "DEADLINE_VALUES", "DISCOVERY_FACETS", "FACT_FACET_GROUPS"]) {
  const start = source.indexOf("  const " + name + " =");
  const end = source.indexOf(";", start) + 1;
  assert(start >= 0 && end > start, name);
  vm.runInContext(source.slice(start, end), sandbox);
}
for (const name of ["boundedInlineText", "normalizeSearchText", "buildSearchFields", "viewControls",
  "normalizedType", "typeLabel", "listingAvailability", "isAvailable", "isCollected", "isTracked", "belongsInView",
  "localCalendarDate", "matchesDeadline", "facetEntries", "matchesFactFacet", "scheduleValue",
  "dateEntry", "dateSortValue", "compareNewestItems",
  "compareItems", "validId", "safeUrl", "discoveryIdentity", "addDateMeta", "formatDate",
  "isApplication", "hasWholeTerm", "valueRank", "queryRank", "filteredItems", "filtersForView",
  "hasRefinements", "initializeDiscoveryFacets", "listingEvidence", "appendListingDetails",
  "appendMatchSummary", "appendMatchAudit", "sourceResourceSearchText",
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
  record("h", "event", {facts: {availability: "listed"}, deadline_at: sandbox.localCalendarDate(-1)}),
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

const research = {id: "research", label: "Research"};
const compensation = {id: "compensation", label: "Compensation"};
const training = {id: "learn", label: "Courses and training"};
sandbox.opportunityRows = [
  record("research-fellowship", "fellowship", {facts: {facets: {activities: [research], benefits: [compensation]}}}),
  record("research-internship", "internship", {title: "Summer program", tier: "skip",
    detail_available: false, facts: {facets: {activities: [research], benefits: []}}}),
  record("paid-training", "apprenticeship", {facts: {facets: {activities: [training], benefits: [compensation]}}}),
  record("unknown", "program", {bookmarked: true}),
];
const facetControls = Object.fromEntries(["kind", "activity", "benefit"].map((key) => [
  key + "-filter", sandbox.element("select", ""),
]));
sandbox.document = {getElementById: (id) => facetControls[id]};
sandbox.initializeDiscoveryFacets();
assert.deepEqual(facetControls["activity-filter"].childNodes.map((entry) => entry.textContent), ["Courses and training", "Research"]);
assert.deepEqual(facetControls["benefit-filter"].childNodes.map((entry) => entry.textContent), ["Compensation"]);

sandbox.state.activity = "research";
assert.deepEqual(ids(), ["research-fellowship", "research-internship"], "Activity spans kinds and includes compact catalog rows");
sandbox.state.kind = "internship";
assert.deepEqual(ids(), ["research-internship"], "Kind and activity compose");
sandbox.state.kind = "all";
sandbox.state.benefit = "compensation";
assert.deepEqual(ids(), ["research-fellowship"], "Activity and benefit compose");
sandbox.state.activity = "all";
assert.deepEqual(ids(), ["research-fellowship", "paid-training"], "A benefit spans kinds and excludes missing evidence");
sandbox.state.benefit = "all";
sandbox.state.query = "summer research";
assert.deepEqual(ids(), ["research-internship"], "Compact facet labels contribute to keyword search");
sandbox.state.query = "";
sandbox.state.activity = "research";
sandbox.state.view = "applications";
sandbox.state.filter = "saved";
assert.deepEqual(ids(), ["unknown"], "Discovery facets do not apply to the Tracker");
Object.assign(sandbox.state, {view: "discover", filter: "all"});
assert(sandbox.hasRefinements(), "A facet alone enables Clear filters");
assert.equal(sandbox.viewControls(sandbox.state).activity, "research");
assert.equal(sandbox.viewControls({sort: "deadline"}).activity, "all", "Reset clears facets without changing sort");

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
    facets: {benefits: [{...compensation, evidence: "Monthly stipend"}]},
    requirements: [{label: "Enrolled students", evidence: "Open to enrolled graduate students."}],
    source: {name: "Boston programs", url: "javascript:alert(1)"},
    schedule: {starts_at: "2027-03-01", attendance: "https://schema.org/MixedEventAttendanceMode"},
  },
});
for (const label of ["monthly stipend", "Activities indicated by the listing", "Enrolled students", "Boston programs", "Attendance · Hybrid"]) {
  assert(text(listing).includes(label), label);
}
const attribution = listing.childNodes[1].childNodes[1].childNodes.find((node) => node.className === "listing-attribution");
assert.equal(attribution.childNodes[0].tag, "strong", "Unsafe source URLs do not produce links");
const benefitEvidence = listing.childNodes[1].childNodes[1].childNodes
  .find((node) => node.className === "listing-evidence" && text(node).includes("Compensation"));
assert.equal(benefitEvidence.childNodes.length, 1, "A facet and older label share one listing evidence excerpt");
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
