import tempfile
import unittest
from pathlib import Path

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
        self.assertEqual(items[0].metadata, {"curated": True})
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


if __name__ == "__main__":
    unittest.main()
