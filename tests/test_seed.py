import tempfile
import unittest
from pathlib import Path

from monitor.dates import extract_deadline
from monitor.seed import parse_pipeline


class SeedTests(unittest.TestCase):
    def test_parses_curated_table_metadata(self):
        content = """# Pipeline
## Apply first
| Priority | Opportunity | Why | Condition | Commitment | Resume |
|---|---|---|---|---|---|
| 1 | [Example Labs Internship](https://example.com/labs) | Data systems | Deadline is February 26, 2028. | `Standard`: ten weeks | `Research` |
## High-value programs opening soon
| Date | Program | Why | Commitment | Action |
|---|---|---|---|---|
| Fall | [Community Fellowship](https://example.com/community) | Public-interest work | `Low`: funding | Prepare |
"""
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "pipeline.md"
            path.write_text(content)
            items = parse_pipeline(path, resume_codes=["Research"], default_year="2028")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].deadline_at, "2028-02-26")
        self.assertEqual(items[0].recommended_resume, "Research")
        self.assertTrue(items[0].metadata["curated"])
        self.assertEqual(
            items[0].metadata["document_routing"],
            {"provenance": "curated_explicit"},
        )
        self.assertEqual(items[0].metadata["dates"]["deadline"]["state"], "date")
        self.assertEqual(
            items[0].metadata["dates"]["deadline"]["provenance"],
            "text.explicit_deadline",
        )
        self.assertEqual(
            items[1].metadata["document_routing"]["provenance"], "profile"
        )
        self.assertEqual(items[1].opportunity_type, "fellowship")

    def test_organization_inference_is_generic(self):
        content = """# Pipeline
## Programs
| Opportunity | Notes |
|---|---|
| [Acme Research Labs Internship](https://example.com/acme) | Systems work |
| [Research Fellow at Example Institute](https://example.com/fellow) | Research |
"""
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "pipeline.md"
            path.write_text(content)
            items = parse_pipeline(path)
        self.assertEqual(items[0].organization, "Acme Research Labs")
        self.assertEqual(items[1].organization, "Example Institute")

    def test_structured_seed_columns_drive_organization_and_type(self):
        content = """# Pipeline
## Programs
| Opportunity | Organization | Opportunity type | Notes |
|---|---|---|---|
| [Summer Scholars](https://example.com/scholars) | Example Institute | Internship | Laboratory research |
"""
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "pipeline.md"
            path.write_text(content)
            items = parse_pipeline(path)
        self.assertEqual(items[0].organization, "Example Institute")
        self.assertEqual(items[0].opportunity_type, "internship")

    def test_seed_heading_provides_type_evidence_when_title_is_generic(self):
        content = """# Pipeline
## Fellowships
| Opportunity | Notes |
|---|---|
| [Example Scholars Program](https://example.com/program) | Public-interest research |
"""
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "pipeline.md"
            path.write_text(content)
            items = parse_pipeline(path)
        self.assertEqual(items[0].opportunity_type, "fellowship")

    def test_explicit_default_document_label_is_pinned(self):
        content = """# Pipeline
## Programs
| Opportunity | Document |
|---|---|
| [Example Program](https://example.com/default) | `General` |
"""
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "pipeline.md"
            path.write_text(content)
            items = parse_pipeline(path, default_resume="General")
        self.assertEqual(items[0].recommended_resume, "General")
        self.assertEqual(
            items[0].metadata["document_routing"]["provenance"],
            "curated_explicit",
        )

    def test_deadline_parser_supports_unambiguous_written_and_iso_dates(self):
        cases = {
            "Applications close Mar. 2nd, 2028": "2028-03-02",
            "Application deadline: 2 March 2028": "2028-03-02",
            "Apply by 2028-03-02": "2028-03-02",
            "Deadline is March 2": "2028-03-02",
            "Submit your application by Friday, March 2, 2028": "2028-03-02",
            "Applications will close at 11:59 p.m. ET on March 2, 2028": "2028-03-02",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                value, metadata = extract_deadline(text, default_year="2028")
                self.assertEqual(value, expected)
                self.assertEqual(metadata["state"], "date")

    def test_deadline_parser_rejects_ambiguous_or_unrelated_dates(self):
        for text in (
            "Complete the degree by June 2028.",
            "Application deadline 03/04/2028.",
            "Deadline is February 31, 2028.",
        ):
            with self.subTest(text=text):
                value, metadata = extract_deadline(text)
                self.assertIsNone(value)
                self.assertEqual(metadata["state"], "not_listed")

    def test_deadline_parser_distinguishes_non_date_deadline_states(self):
        rolling_value, rolling = extract_deadline("Applications are reviewed on a rolling basis.")
        open_value, open_state = extract_deadline("The position is open until filled.")
        missing_value, missing = extract_deadline("No application timing is provided.")
        self.assertIsNone(rolling_value)
        self.assertEqual(rolling["state"], "rolling")
        self.assertIsNone(open_value)
        self.assertEqual(open_state["state"], "open_until_filled")
        self.assertIsNone(missing_value)
        self.assertEqual(missing["state"], "not_listed")

    def test_deadline_parser_uses_an_explicit_numeric_date_order_only(self):
        us_value, _us = extract_deadline(
            "Apply by 03/04/2028",
            date_order="mdy",
        )
        international_value, _international = extract_deadline(
            "Apply by 03/04/2028",
            date_order="dmy",
        )
        self.assertEqual(us_value, "2028-03-04")
        self.assertEqual(international_value, "2028-04-03")


if __name__ == "__main__":
    unittest.main()
