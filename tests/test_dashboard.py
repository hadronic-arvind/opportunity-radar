import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.dashboard import APP_MARKER, DATA_MARKER, STYLE_MARKER, render_dashboard, safe_external_url
from monitor.config import PRIVATE_RUNTIME_MARKERS


class DashboardTests(unittest.TestCase):
    def copy_dashboard_assets(self, destination):
        source = Path(__file__).resolve().parents[1] / "dashboard"
        for name in ("template.html", "styles.css", "app.js"):
            (destination / name).write_text(
                (source / name).read_text(encoding="utf-8"), encoding="utf-8"
            )

    def test_external_url_policy(self):
        self.assertEqual(safe_external_url("https://example.com/job"), "https://example.com/job")
        self.assertEqual(safe_external_url("HTTP://example.com/job"), "HTTP://example.com/job")
        for value in ("javascript:alert(1)", "data:text/html,x", "file:///tmp/x", "//example.com", ""):
            self.assertEqual(safe_external_url(value), "")

    def test_render_is_atomic_private_and_script_safe(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "dashboard").mkdir()
            self.copy_dashboard_assets(root / "dashboard")
            payload = {
                "generated_at": "2026-08-09T00:00:00+00:00",
                "counts": {},
                "runs": [],
                "sources": [],
                "events": [{"title": "Changed", "url": "file:///tmp/private"}],
                "opportunities": [
                    {
                        "id": "x",
                        "title": "</script><img src=x onerror=alert(1)>",
                        "organization": "Example",
                        "url": "javascript:alert(1)",
                    }
                ],
            }
            with patch("monitor.dashboard.project_path", side_effect=lambda *parts: root.joinpath(*parts)):
                output = render_dashboard(payload, profile={"dashboard": {"title": "Test"}})
            rendered = output.read_text(encoding="utf-8")
            self.assertIn('http-equiv="Content-Security-Policy"', rendered)
            self.assertIn("connect-src 'none'", rendered)
            self.assertNotIn("'unsafe-inline'", rendered)
            self.assertIn("\\u003c/script>\\u003cimg", rendered)
            self.assertNotIn('"url":"javascript:', rendered)
            self.assertNotIn('"url":"file:', rendered)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            for marker in (STYLE_MARKER, DATA_MARKER, APP_MARKER):
                self.assertNotIn(marker, rendered)
            self.assertNotIn("innerHTML", rendered)
            nonces = re.findall(r'nonce="([A-Za-z0-9_-]+)"', rendered)
            self.assertGreaterEqual(len(nonces), 3)
            self.assertEqual(len(set(nonces)), 1)
            self.assertIn("'nonce-{}'".format(nonces[0]), rendered)

    def test_template_requires_exactly_one_marker(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "dashboard").mkdir()
            (root / "dashboard" / "template.html").write_text("no marker", encoding="utf-8")
            with patch("monitor.dashboard.project_path", side_effect=lambda *parts: root.joinpath(*parts)):
                with self.assertRaises(ValueError):
                    render_dashboard({}, profile={})

    def test_explicit_empty_profile_does_not_load_local_profile(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "dashboard").mkdir()
            self.copy_dashboard_assets(root / "dashboard")
            with (
                patch("monitor.dashboard.project_path", side_effect=lambda *parts: root.joinpath(*parts)),
                patch("monitor.dashboard.load_profile", side_effect=AssertionError("local profile read")),
            ):
                output = render_dashboard({}, profile={})
            self.assertIn(
                '"title":"Opportunity Radar"',
                output.read_text(encoding="utf-8"),
            )

    def test_render_preserves_installed_dashboard_symlink(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime = root / "runtime" / "dashboard"
            project = root / "project" / "dashboard"
            runtime.mkdir(parents=True)
            project.mkdir(parents=True)
            runtime.parent.chmod(0o700)
            runtime.chmod(0o700)
            for marker in PRIVATE_RUNTIME_MARKERS:
                marker_path = runtime.parent / marker
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text("runtime marker", encoding="utf-8")
                marker_path.chmod(0o600)
            self.copy_dashboard_assets(project)
            target = runtime / "index.html"
            target.write_text("old", encoding="utf-8")
            target.chmod(0o600)
            output = project / "index.html"
            output.symlink_to(target)
            with patch("monitor.dashboard.project_path", side_effect=lambda *parts: root.joinpath("project", *parts)):
                rendered = render_dashboard({}, profile={})
            self.assertEqual(rendered, target.resolve())
            self.assertTrue(output.is_symlink())
            self.assertIn('id="opportunity-data"', target.read_text(encoding="utf-8"))

    def test_dashboard_pagination_search_and_native_actions_are_bounded(self):
        source = Path(__file__).resolve().parents[1] / "dashboard" / "app.js"
        script = source.read_text(encoding="utf-8")
        self.assertIn("const PAGE_SIZE = 24", script)
        self.assertIn("const SEARCH_DEBOUNCE_MS = 90", script)
        self.assertIn("function renderPagination()", script)
        self.assertIn("function paginationValues(total, current)", script)
        self.assertIn("function changePage(nextPage)", script)
        self.assertIn("state.visibleItems.slice(start, end)", script)
        self.assertIn('button.setAttribute("aria-current", "page")', script)
        self.assertIn("state.page = 1", script)
        self.assertNotIn("WINDOW_SIZE", script)
        self.assertNotIn("virtual-spacer", script)
        self.assertNotIn("IntersectionObserver", script)
        self.assertNotIn("appendNextPage", script)

        self.assertIn("function buildSearchFields(item)", script)
        self.assertIn("function queryRank(item, phrase, terms)", script)
        self.assertIn("const searchIndex = new Map", script)
        self.assertIn("valueRank(fields.organization, term, [1200, 1050, 850, 740])", script)
        self.assertIn("valueRank(fields.title, term, [1180, 1030, 830, 720])", script)
        self.assertIn("valueRank(fields.description, term, [140, 130, 115, 100])", script)
        self.assertIn("window.setTimeout(renderList, SEARCH_DEBOUNCE_MS)", script)

        self.assertIn("function focusedListControl()", script)
        self.assertIn("function restoreListFocus(focus, fallbackIndex)", script)
        self.assertIn('return {kind: "link", id: linkCard.dataset.id}', script)
        self.assertIn("const focus = focusedListControl();\n      const request = startNativeAction", script)
        self.assertIn("renderAll(mutationFocus)", script)
        self.assertIn("list.replaceChildren(fragment)", script)
        self.assertIn('action: "scan"', script)
        self.assertIn('action: "status"', script)
        self.assertIn('action: "bookmark"', script)
        self.assertIn("pendingRequest", script)
        self.assertIn("pendingAction", script)
        self.assertIn("finishPendingMutation(ok)", script)
        self.assertIn("saveTransientView()", script)
        self.assertIn('{action: "profile", profile: payload}', script)
        self.assertIn('profileCommitted = cloneProfile(settings.profile_editor)', script)
        self.assertIn("function profileValidationMessage(profile)", script)
        self.assertIn("function profileRangeField(", script)
        self.assertIn("function setProfilePage(page, focusTab)", script)
        self.assertIn('button.textContent = isEmpty ? "Set up profile" : "Edit profile"', script)
        self.assertIn("const profilePackOptions = Array.isArray(settings.source_packs)", script)
        self.assertIn("String(pack && pack.description || \"\").trim().slice(0, 240)", script)
        self.assertIn('["students-early-career", "Students and early career"]', script)
        self.assertIn('["space-aerospace", "Space and aerospace"]', script)
        self.assertIn('["robotics-autonomy", "Robotics and autonomy"]', script)
        self.assertIn('["education-social-impact", "Education and social impact"]', script)
        self.assertIn('input.setAttribute("aria-describedby", description.id)', script)
        self.assertIn("choice.title = option.description", script)
        self.assertIn('button.setAttribute("aria-disabled", String(state.busy))', script)
        self.assertNotIn("button.disabled = state.busy", script)
        self.assertIn("replaceChildren", script)
        self.assertNotIn("innerHTML", script)

    def test_dashboard_template_has_accessible_navigation_and_site_controls(self):
        root = Path(__file__).resolve().parents[1] / "dashboard"
        template = (root / "template.html").read_text(encoding="utf-8")
        self.assertIn('<h1 id="intro-title">Your Opportunities</h1>', template)
        self.assertIn('<a class="skip-link" href="#results">', template)
        self.assertLess(template.index('class="skip-link"'), template.index('class="app-shell"'))
        self.assertIn('id="results" aria-label="Opportunities" tabindex="-1"', template)
        self.assertIn('id="pagination" aria-label="Opportunity result pages"', template)
        self.assertIn('id="page-previous"', template)
        self.assertIn('id="page-next"', template)
        self.assertIn('id="filter-row" role="group" aria-label="Opportunity filters"', template)
        self.assertIn('id="theme-select" aria-label="Color theme"', template)
        self.assertIn('id="source-search" type="search"', template)
        self.assertIn('id="source-status-filter" aria-label="Filter sources by status"', template)
        self.assertIn('id="source-show-more" type="button" aria-controls="source-list"', template)
        self.assertIn('id="source-add-details"', template)
        self.assertIn('id="source-add-form" novalidate', template)
        self.assertIn('id="source-add-name" name="name"', template)
        self.assertIn('id="source-add-url" name="url" type="url"', template)
        self.assertIn('id="source-add-status" role="status" aria-live="polite"', template)
        self.assertIn("macOS app only", template)
        self.assertIn('id="profile-card" hidden', template)
        self.assertIn('id="edit-profile-button"', template)
        self.assertIn('id="profile-dialog" aria-labelledby="profile-dialog-title"', template)
        self.assertIn('id="profile-form"', template)
        self.assertIn('role="tablist" aria-label="Profile settings"', template)
        self.assertIn('id="profile-basics-tab"', template)
        self.assertIn('id="profile-advanced-tab"', template)
        self.assertIn("python3 -m monitor profile set --help", template)
        for theme in ("system", "light", "dark"):
            self.assertIn('<option value="{}">'.format(theme), template)
        self.assertNotIn('data-theme-option=', template)

    def test_dashboard_uses_structured_dates_matches_and_contrast_safe_tokens(self):
        root = Path(__file__).resolve().parents[1] / "dashboard"
        script = (root / "app.js").read_text(encoding="utf-8")
        template = (root / "template.html").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")
        self.assertIn('const dates = item.dates && typeof item.dates === "object"', script)
        self.assertIn('const legacy = {', script)
        self.assertIn('raw.status || raw.state || raw.kind', script)
        self.assertIn('if (structured.value || !legacy[name]) return structured', script)
        self.assertIn('function compareNewestItems(left, right)', script)
        self.assertIn(
            'if (Boolean(leftPosted) !== Boolean(rightPosted)) return leftPosted ? -1 : 1',
            script,
        )
        self.assertIn('return compareNewestItems(left, right)', script)
        self.assertNotIn('dateSortValue(right, "posted", right.first_seen_at || "")', script)
        self.assertIn('<option value="newest">Latest posted</option>', template)
        self.assertIn('const timeNode = element("time"', script)
        self.assertIn('element("span", "meta-label", value.prefix)', script)
        self.assertIn('item.match && Array.isArray(item.match.components)', script)
        self.assertIn("function matchEvidenceLabel(value, detailed)", script)
        self.assertIn('normalizeSearchText(field) !== "description"', script)
        self.assertIn('matchEvidenceLabel(value, true)', script)
        self.assertIn('addTag(tags, "Eligibility details incomplete", "eligibility")', script)
        self.assertIn('if (!String(item.description || "").trim()) addTag(tags, "Limited listing details", "limited-details")', script)
        self.assertIn(".tag.limited-details {", styles)
        self.assertIn('settings.timeframes', script)
        self.assertNotIn('settings.target_season + " search"', script)
        self.assertIn('@media (min-width: 981px) and (min-height: 1000px)', styles)
        self.assertIn('.side-column { position: sticky; top: 92px;', styles)
        self.assertIn('.date-meta { gap: .35em; }', styles)
        self.assertIn('clip-path: inset(50%);', styles)
        self.assertIn('color: var(--primary-action-text)', styles)
        self.assertIn('background: var(--primary-action)', styles)
        self.assertIn(".tag.eligibility {", styles)

        def luminance(color):
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            channels = [
                value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                for value in channels
            ]
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

        backgrounds = re.findall(r"--primary-action:\s*(#[0-9a-fA-F]{6})", styles)
        foregrounds = re.findall(r"--primary-action-text:\s*(#[0-9a-fA-F]{6})", styles)
        self.assertGreaterEqual(len(backgrounds), 2)
        self.assertGreaterEqual(len(foregrounds), 2)
        for background, foreground in zip(backgrounds[:2], foregrounds[:2]):
            lighter, darker = sorted(
                (luminance(background), luminance(foreground)), reverse=True
            )
            self.assertGreaterEqual((lighter + 0.05) / (darker + 0.05), 4.5)

        faint = re.search(r":root \{.*?--faint:\s*(#[0-9a-fA-F]{6})", styles, re.DOTALL)
        amber = re.search(r":root \{.*?--amber:\s*(#[0-9a-fA-F]{6})", styles, re.DOTALL)
        self.assertIsNotNone(faint)
        self.assertIsNotNone(amber)
        for foreground, background in (
            (faint.group(1), "#f5f3ee"),
            (faint.group(1), "#ffffff"),
            (amber.group(1), "#f6e8c9"),
        ):
            lighter, darker = sorted(
                (luminance(background), luminance(foreground)), reverse=True
            )
            self.assertGreaterEqual((lighter + 0.05) / (darker + 0.05), 4.5)

    def test_profile_editor_is_hybrid_bounded_and_native_only_for_writes(self):
        root = Path(__file__).resolve().parents[1] / "dashboard"
        script = (root / "app.js").read_text(encoding="utf-8")
        template = (root / "template.html").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function profileChoiceField(", script)
        self.assertIn("function profileTagField(", script)
        self.assertIn("function profileRangeField(", script)
        self.assertIn("function renderRangeMap(", script)
        self.assertIn("function renderProfileRules(", script)
        self.assertIn("function renderDocumentRoutes(", script)
        self.assertIn('profileChoiceField("Current stage"', script)
        self.assertIn('profileChoiceField("Opportunity types"', script)
        self.assertIn('profileTagField("Role families"', script)
        self.assertIn('profileTagField("Domains"', script)
        self.assertIn('profileTagField("Demonstrated skills"', script)
        self.assertIn('profileChoiceField("Remote preference"', script)
        self.assertIn('profileTagField("Priority organizations"', script)
        self.assertIn('profileRangeField("Minimum score to display"', script)
        self.assertIn('profileRangeField("Minimum anchor strength"', script)
        self.assertIn('advanced.appendChild(scoring.section)', script)
        self.assertIn('basics.appendChild(defaultDocument.section)', script)
        self.assertIn('advanced.appendChild(documentSection.section)', script)
        self.assertIn("fields.append(basics, advanced)", script)
        self.assertIn("setProfilePage(profileActivePage, false)", script)
        self.assertIn("existing.length >= limit", script)
        self.assertIn("profileDraft.matching.rules.length >= 100", script)
        self.assertIn("profileDraft.documents.routes.length >= 50", script)
        self.assertIn('if (!hasNativeBridge()) {', script)
        self.assertIn('profile-save-button" type="submit"', template)
        self.assertIn('data-profile-page-tab="basics"', template)
        self.assertIn('data-profile-page-tab="advanced"', template)
        self.assertIn(".profile-dialog {", styles)
        self.assertIn(".profile-choice input:checked + span", styles)
        self.assertIn(".profile-range-row {", styles)
        self.assertIn(".profile-page {", styles)
        self.assertNotIn("innerHTML", script)

    def test_dashboard_scales_source_details_and_marks_opened_new_items_reviewed(self):
        root = Path(__file__).resolve().parents[1] / "dashboard"
        script = (root / "app.js").read_text(encoding="utf-8")
        template = (root / "template.html").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")

        self.assertIn("const SOURCE_PREVIEW_LIMIT = 12", script)
        self.assertIn("function renderSourceList()", script)
        self.assertIn("statusOrder[sourceStatus(left)]", script)
        self.assertIn('sourceView.status !== "all"', script)
        self.assertIn('sourceView.expanded ? matching : matching.slice(0, SOURCE_PREVIEW_LIMIT)', script)
        self.assertIn('id="source-list-note" role="status" aria-live="polite"', template)
        self.assertIn(".source-tools {", styles)
        self.assertIn(".source-show-more {", styles)
        self.assertIn(".source-add {", styles)
        self.assertIn(".native-only-badge {", styles)

        self.assertIn("const MAX_SOURCE_NAME_LENGTH = 120", script)
        self.assertIn("const MAX_SOURCE_URL_LENGTH = 2000", script)
        self.assertIn("function publicHttpsUrl(value)", script)
        self.assertIn("function updateSourceFormAvailability()", script)
        self.assertIn("function addSource(event)", script)
        self.assertIn('{action: "source", name, url}', script)
        self.assertIn('source-add-form").addEventListener("submit", addSource)', script)
        self.assertIn("Adding sources is available in the macOS app.", script)
        self.assertIn('String(source.last_error || "").replace(/\\s+/g, " ").trim().slice(0, 240)', script)
        self.assertIn('element("span", "source-error", lastError)', script)

        self.assertIn('link.dataset.id = item.id', script)
        self.assertIn('const reviewedLink = event.target.closest("a.official-link[data-id]")', script)
        self.assertIn('if (item && effective(item).status === "new") updateWorkflow(item.id, {status: "reviewed"});', script)

    def test_profile_editor_stays_available_during_scan_and_queues_one_save(self):
        script = (
            Path(__file__).resolve().parents[1] / "dashboard" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn('if (state.busy && state.pendingAction !== "scan")', script)
        self.assertIn('state.queuedProfile = {request, profile: cloneProfile(payload)}', script)
        self.assertIn('postNative({action: "profile", profile: payload, request})', script)
        self.assertIn('"Scanning... Profile saved for the next scan."', script)
        self.assertIn('state.pendingAction === "scan" && !profileQueued', script)
        self.assertIn('document.getElementById("profile-fields").inert = profileActionBusy || profileQueued', script)
        self.assertIn('if (action === "scan" && state.queuedProfile)', script)
        self.assertIn('setBusy(true, "Applying saved profile...", state.queuedProfile.request)', script)
        self.assertIn('state.profileRetryDraft = cloneProfile(state.queuedProfile.profile)', script)


if __name__ == "__main__":
    unittest.main()
