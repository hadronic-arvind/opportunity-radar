# Anonymous profile examples

These portable version-1 JSON files are fictional starting points for testing and customization.
They are not recommendations to change an existing user's saved preferences.

| File | Intended search |
| --- | --- |
| `medical-student.json` | Medical student seeking clinical research, not licensed physician roles |
| `mba-student.json` | MBA student seeking business analysis, strategy, and operations |
| `biomedical-engineering.json` | Biomedical engineering graduate seeking engineering and device quality roles |
| `accounting-graduate.json` | Accounting graduate seeking accounting, audit, and tax roles |
| `education-graduate.json` | Education graduate seeking teaching and curriculum roles |
| `law-student.json` | Law student seeking legal internships and public policy research |
| `design-graduate.json` | Design graduate seeking graphic, UX, and product design |
| `skilled-technician.json` | Early-career electrical and maintenance technician |
| `international-phd.json` | Indian citizen pursuing a PhD, seeking research internships and fellowships |

Edit completed degrees, current stage, citizenship, residency, experience limits, locations, and role terms before using an example as your own profile.
The medical and law student examples record a completed bachelor's degree rather than claiming a completed professional degree.
Most examples use US citizenship solely as a test input; the PhD example uses Indian citizenship and no permanent residency.
No location, licensing, visa sponsorship, graduation date, or availability is inferred from nationality.

Validate without importing:

```bash
python3 -m monitor profile import --file examples/profiles/medical-student.json --dry-run
```

Omit `--dry-run` only when you intend to create and activate a separate saved profile.
The audit command in [the audit report](../../docs/AUDIENCE_AUDIT.md) evaluates all examples without saving profiles or changing the active profile.
