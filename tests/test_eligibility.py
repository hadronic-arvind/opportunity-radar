import unittest

from monitor.models import Opportunity
from monitor.profile import _normalize_candidate, ProfileValidationError, portable_profile_editor
from monitor.scoring import score_opportunity, profile_fingerprint


class EligibilityTests(unittest.TestCase):
    def score(self, text='', title='Research internship', **candidate):
        profile = {'candidate': {'current_stage': 'phd_student', **candidate},
                   'matching': {'engine': 'structured_v2', 'base_score': 80, 'rules': []}}
        item = Opportunity('test', 'one', title, 'Example', 'https://example.com', description=text)
        score_opportunity(item, profile)
        return item.metadata['match']

    def test_degree_restrictions_hide_through_public_scoring_path(self):
        for text in ["Applicants must be pursuing a bachelor's or master's degree.",
                     'Must be enrolled in a BS/MS program.',
                     'Only undergraduate students are eligible.',
                     'Reserved for bachelor’s and master’s holders.',
                     'Ph.D. students are not eligible.',
                     "Currently pursuing a master's degree. Collaborate with PhD scientists."]:
            with self.subTest(text=text):
                result = self.score(text)
                self.assertEqual(result['eligibility'], 'ineligible')
                self.assertNotEqual(result['visibility']['state'], 'visible')

    def test_title_audience_and_completed_doctorate(self):
        self.assertEqual(self.score(title='Undergraduate Research Fellowship')['eligibility'], 'ineligible')
        self.assertEqual(self.score('Only master’s degree holders may apply.', current_stage='early_career',
                                    completed_degrees=['Doctoral degree in Physics'])['eligibility'], 'ineligible')

    def test_alternatives_minimums_preferences_and_unrelated_mentions_remain(self):
        for text in ["Pursuing a bachelor's, master's, or Ph.D. degree.",
                     "Bachelor's degree or higher required.", "Master's degree required.",
                     'Work with undergraduate students.', 'Master’s preferred.',
                     'Currently enrolled graduate students may apply.',
                     'Undergraduate students are not eligible.',
                     'No citizenship requirement.']:
            with self.subTest(text=text):
                self.assertNotEqual(self.score(text)['eligibility'], 'ineligible')

    def test_exact_enrollment_levels(self):
        for stage, text, fail in [('masters_student', 'Must be pursuing a PhD.', True),
                                  ('undergraduate_student', 'Must be enrolled in a master’s program.', True),
                                  ('masters_student', 'Pursuing a BS or MS degree.', False),
                                  ('undergraduate_student', 'Undergraduate students only.', False)]:
            with self.subTest(stage=stage, text=text):
                self.assertEqual(self.score(text, current_stage=stage)['eligibility'] == 'ineligible', fail)

    def test_grfp_sparse_listing_and_status_exceptions(self):
        for candidate, expected in [({'citizenships': ['India']}, 'ineligible'),
                                    ({'citizenships': ['IN'], 'permanent_residencies': ['US']}, 'compatible'),
                                    ({'citizenships': ['IN', 'US']}, 'compatible'),
                                    ({'citizenships': ['IN'], 'us_national': True}, 'compatible'),
                                    ({}, 'unknown')]:
            with self.subTest(candidate=candidate):
                self.assertEqual(self.score(title='NSF GRFP', filter_nationality=True, **candidate)['eligibility'], expected)
        self.assertEqual(self.score(title='NSF GRFP', citizenships=['IN'])['eligibility'], 'compatible')

    def test_citizenship_clauses(self):
        cases = [('Applicants must be U.S. citizens.', ['IN'], [], 'ineligible'),
                 ('Applicants must be US citizens only.', ['IN'], ['US'], 'ineligible'),
                 ('Only Canadian citizens or permanent residents are eligible.', ['IN'], ['CA'], 'compatible'),
                 ('Citizenship is not required. International students welcome.', ['IN'], [], 'compatible'),
                 ('US citizenship preferred.', ['IN'], [], 'unknown'),
                 ('Must be citizens of Canada.', ['IN'], [], 'ineligible'),
                 ('Only US or Canadian citizens are eligible.', ['IN'], [], 'unknown')]
        for text, citizens, residents, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.score(text, filter_nationality=True, citizenships=citizens,
                                            permanent_residencies=residents)['eligibility'], expected)

    def test_international_alternative_is_not_a_false_exclusion(self):
        for text in ['US citizens and international students are eligible.',
                     'Applicants must be US citizens or have work authorization.',
                     'Applicants must not be US citizens.', '']:
            self.assertEqual(self.score(text, filter_nationality=True, citizenships=['IN'])['eligibility'], 'unknown')

    def test_sentence_boundaries_and_explicit_degree_alternatives(self):
        for text in ["Must be pursuing a PhD. Undergraduate students are not eligible.",
                     "Must be pursuing a Ph.D. Undergraduate students are not eligible.",
                     "Must be pursuing a Masters degree; PhD students may also apply.",
                     "Currently enrolled in a bachelor's program. Doctoral students are also eligible."]:
            with self.subTest(text=text):
                self.assertNotEqual(self.score(text)["eligibility"], "ineligible")

    def test_graduate_only_excludes_undergraduate_students(self):
        self.assertEqual(self.score("Only graduate students may apply.",
                                    current_stage="undergraduate_student")["eligibility"], "ineligible")

    def test_adjacent_residency_exception_and_conflicting_requirements(self):
        self.assertEqual(self.score(
            "Applicants must be US citizens. Permanent residents are also eligible.",
            filter_nationality=True, citizenships=["IN"], permanent_residencies=["US"]
        )["eligibility"], "compatible")
        self.assertEqual(self.score(
            "Applicants must be US citizens; citizenship is not required for international applicants.",
            filter_nationality=True, citizenships=["IN"]
        )["eligibility"], "unknown")

    def test_capitalized_abbreviation_continuations(self):
        self.assertEqual(self.score("Only Ph.D. Students may apply.",
                                    current_stage="undergraduate_student")["eligibility"], "ineligible")
        self.assertEqual(self.score("U.S. Citizenship Required.", filter_nationality=True,
                                    citizenships=["IN"])["eligibility"], "ineligible")

    def test_profile_validation_and_fingerprint(self):
        candidate = _normalize_candidate({'filter_nationality': True, 'citizenships': ['India', 'IN'],
                                           'permanent_residencies': ['United States'], 'us_national': False})
        self.assertEqual(candidate['citizenships'], ['IN'])
        self.assertEqual(candidate['permanent_residencies'], ['US'])
        for invalid in [{'filter_nationality': 'yes'}, {'citizenships': ['Imaginary']}, {'us_national': 1}]:
            with self.assertRaises(ProfileValidationError):
                _normalize_candidate(invalid)
        base = {'matching': {'engine': 'structured_v2'}, 'candidate': candidate}
        for key, value in [('citizenships', ['US']), ('permanent_residencies', []),
                           ('filter_nationality', False), ('us_national', True)]:
            changed = {**base, 'candidate': {**candidate, key: value}}
            self.assertNotEqual(profile_fingerprint(base), profile_fingerprint(changed))

    def test_portable_profile_preserves_status(self):
        _, editor = portable_profile_editor({'version': 1, 'name': 'Example', 'candidate': {
            'filter_nationality': True, 'citizenships': ['India'], 'permanent_residencies': ['US']}})
        self.assertTrue(editor['candidate']['filter_nationality'])
        self.assertEqual(editor['candidate']['citizenships'], ['IN'])
