// Exercise production dashboard functions with anonymous in-memory state.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../dashboard/app.js"), "utf8");
const sandbox = {
  data: {opportunities: []},
  state: {view: "discover", filter: "all", query: "", sort: "fit"},
  searchIndex: new Map(),
  settings: {},
  effective: (item) => ({status: item.status || "new", bookmarked: Boolean(item.bookmarked)}),
  compareItems: (left, right) => right.score - left.score,
  humanizeProfileValue: (value) => String(value).replaceAll("_", " "),
  matchComponents: () => [],
  element: (tag, className, text) => ({tag, className, textContent: text || "", childNodes: [],
    appendChild(child) { this.childNodes.push(child); }}),
};
vm.createContext(sandbox);
for (const name of ["TYPE_LABELS", "OPPORTUNITY_TYPE_OPTIONS"]) {
  const start = source.indexOf("  const " + name + " =");
  const end = source.indexOf(";", start) + 1;
  assert(start >= 0 && end > start, name);
  vm.runInContext(source.slice(start, end), sandbox);
}
for (const name of ["normalizeSearchText", "buildSearchFields", "normalizedType", "isAvailable",
  "isApplication", "hasWholeTerm", "valueRank", "queryRank", "filteredItems", "filtersForView",
  "appendMatchSummary", "appendMatchAudit"]) {
  const start = source.indexOf("  function " + name + "(");
  const end = source.indexOf("\n  function ", start + 1);
  assert(start >= 0 && end > start, name);
  vm.runInContext(source.slice(start, end), sandbox);
}
const record = (id, kind, extra = {}) => ({id, title: "Research Engineer", organization: "Example",
  opportunity_type: kind, active: 1, source_enabled: 1, score: 70, tier: "strong", ...extra});
sandbox.data.opportunities = [record("a", "research_program"), record("b", "co_op"),
  record("c", "scholarship"), record("d", "fellowship", {tier: "skip", bookmarked: true}),
  record("e", "internship", {status: "applied", active: 0})];
const ids = () => Array.from(sandbox.filteredItems(), (item) => item.id);
assert.deepEqual(ids(), ["a", "b", "c"]);
const filters = Array.from(sandbox.filtersForView(), (entry) => entry[0]);
assert(filters.includes("research_program") && filters.includes("co_op") && filters.includes("scholarship"));
assert(!filters.includes("fellowship"));
sandbox.state.filter = "co_op";
assert.deepEqual(ids(), ["b"]);
sandbox.state.filter = "saved";
assert.deepEqual(ids(), ["d"], "Saved listings survive a changed fit");
sandbox.state.view = "applications";
sandbox.state.filter = "all";
assert.deepEqual(ids(), ["e"], "Inactive applications remain available");
sandbox.state.view = "discover";
sandbox.state.query = "example research";
assert.deepEqual(ids(), ["a", "b", "c"]);
sandbox.state.query = "missing";
assert.deepEqual(ids(), []);
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
console.log("Dashboard behavior checks passed");
