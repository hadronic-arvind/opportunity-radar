"""Public profile imports and cross-discipline matching regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitor.models import Opportunity
from monitor.scoring import score_opportunity
from monitor.text import clean_text
from scripts.audit_profiles import prepare_profile, audit

ROOT = Path(__file__).resolve().parents[1]


class AudienceProfileTests(unittest.TestCase):
    def profile(self, slug):
        catalog = json.loads((ROOT / 'config/sources.json').read_text())
        payload = json.loads((ROOT / 'examples/profiles' / (slug + '.json')).read_text())
        return prepare_profile(payload, catalog)

    def test_anonymous_imports_select_relevant_listing_feeds(self):
        expected = {
            'medical-student': 'medicine-clinical',
            'mba-student': 'business-operations',
            'biomedical-engineering': 'biomedical-devices',
            'accounting-graduate': 'accounting-finance',
            'education-graduate': 'education-teaching',
            'law-student': 'legal-policy',
            'design-graduate': 'product-design',
            'skilled-technician': 'skilled-technical',
            'international-phd': 'ai-research',
        }
        for slug, pack in expected.items():
            with self.subTest(profile=slug):
                _, profile, packs, sources = self.profile(slug)
                self.assertIn(pack, packs)
                feeds = [s for s in sources if pack in s['packs']]
                self.assertGreaterEqual(len(feeds), 2)
                self.assertTrue(all(s['source_type'] == 'listing_feed' for s in sources))
                self.assertTrue(profile['candidate']['filter_nationality'])

    def test_staff_accountant_is_not_automatically_senior(self):
        _, profile, _, _ = self.profile('accounting-graduate')
        for title, senior in [('Staff Accountant', False), ('Senior Staff Accountant', True),
                              ('Staff Software Engineer', True), ('Sr. Accountant', True)]:
            with self.subTest(title=title):
                item = Opportunity('test', title, title, 'Example', 'https://example.org/job',
                                   opportunity_type='job', description='Accounting and audit.')
                score_opportunity(item, profile)
                failed = [g['id'] for g in item.metadata['match']['gates'] if g['state'] == 'fail']
                self.assertEqual('career_stage' in failed, senior)

    def test_formatted_experience_requirements_exclude_student(self):
        _, profile, _, _ = self.profile('medical-student')
        for text in ['Minimum required: 2 (two) years or more as a CRA.',
                     'Requires &lt;b&gt;3+ years&lt;/b&gt; of clinical research experience.',
                     'At least 3-5 years of clinical research experience required.',
                     'Minimum 3–5 years in public accounting or industry accounting function.']:
            with self.subTest(description=text):
                item = Opportunity('test', 'cra', 'Clinical Research Associate', 'Example',
                                   'https://example.org/job', opportunity_type='job', description=text)
                score_opportunity(item, profile)
                self.assertEqual(item.tier, 'skip')

    def test_preferred_experience_does_not_become_required(self):
        _, profile, _, _ = self.profile('medical-student')
        item = Opportunity('test', 'cra', 'Clinical Research Assistant', 'Example',
                           'https://example.org/job', opportunity_type='job',
                           description='3+ years of clinical research experience preferred.')
        score_opportunity(item, profile)
        self.assertNotEqual(item.tier, 'skip')

    def test_encoded_feed_html_is_normalized(self):
        self.assertEqual(clean_text('&lt;p&gt;Requires &lt;b&gt;3+ years&lt;/b&gt; experience&lt;/p&gt;'),
                         'Requires 3+ years experience')

    def test_offline_audit_never_fetches_or_writes_profile(self):
        before = (ROOT / 'config/profile.json').read_bytes()
        with tempfile.TemporaryDirectory() as directory, patch('scripts.audit_profiles.fetch_source') as fetch:
            result = audit(ROOT / 'examples/profiles', Path(directory))
            fetch.assert_not_called()
            self.assertEqual(len(result['profiles']), 9)
            self.assertTrue(all(s['status'] == 'not_fetched' for s in result['sources'].values()))
        self.assertEqual((ROOT / 'config/profile.json').read_bytes(), before)
