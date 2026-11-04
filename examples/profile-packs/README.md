# Example profile packs

[Career pathways](career-pathways.json) contains 28 fictional profiles, with a student and a professional in each of 14 fields.
It is a portable profile pack for the current Opportunity Radar desktop dashboard and terminal.
The [older single-profile examples](../profiles/README.md) use the legacy monitor format.

Each profile has a goal, ranked preferences, optional hard requirements, exclusions and adjacent search terms.
Hard requirements express priorities that a listing must show directly in its text.
Use them sparingly when a specific activity matters more than coverage.
Eligibility may stay unknown until the publisher's requirements can be checked against the applicant's circumstances.

## Try the pack in a sandbox

Run from the repository root.

```sh
sandbox="$(mktemp -d)"
python3 -m radar --workspace "$sandbox/workspace.sqlite3" import-profiles \
  examples/profile-packs/career-pathways.json --preview
python3 -m radar --workspace "$sandbox/workspace.sqlite3" import-profiles \
  examples/profile-packs/career-pathways.json
python3 -m radar --workspace "$sandbox/workspace.sqlite3" import \
  tests/fixtures/career-pathway-listings.json
python3 -m radar --workspace "$sandbox/workspace.sqlite3" feed
```

On macOS, build and open a separate copy of the dashboard with the same workspace.

```sh
python3 extras/radar-desktop/install.py build --app "$sandbox/Opportunity Radar Sandbox.app"
RADAR_DESKTOP_APP="$sandbox/Opportunity Radar Sandbox.app" \
  RADAR_WORKSPACE="$sandbox/workspace.sqlite3" ./scripts/open_radar_desktop.sh
```

The pack selects **Medicine - student** after import.
Switch profiles in For You and use Explore to see the wider collection.
The [sandbox listings](../../tests/fixtures/career-pathway-listings.json) are fictional.
The [expected results](../../tests/fixtures/career-pathway-expectations.json) map each profile to the publisher IDs of its two leading examples.
To use a different profile as a starting point, copy its entry into a separate pack and set `active_profile` to its name.

## Medicine research

The student profile models a third-year medical student who wants supervised clinical experience and a research year.
AAMC's VSLO includes clinical electives, away rotations and research placements.
Home-school participation and host requirements affect access.
NIH's Medical Research Scholars Program covers mentored clinical, basic and translational research.
The current Clinical Center FAQ gives year-of-study, degree-program, citizenship or permanent-residency, and graduation-deferral conditions that materially affect fit.
[AAMC Visiting Student Learning Opportunities](https://students-residents.aamc.org/visiting-student-learning-opportunities/visiting-student-learning-opportunities-vslo), [NIH Clinical Center MRSP FAQ](https://www.cc.nih.gov/training/mrsp/faq)

Internal medicine residency is a later-stage search for this student.
The profile includes it as an exploratory term, alongside Students to Service funding for a student interested in a qualifying primary-care service commitment.
The latter is intended for eligible final-year students.
[AAMC ERAS residency applications](https://students-residents.aamc.org/applying-residencies-eras/apply-residencies-eras-system), [HRSA Students to Service](https://nhsc.hrsa.gov/loan-repayment/nhsc-students-to-service-loan-repayment-program)

The professional profile models a practicing internist looking for academic hospital medicine, resident supervision and protected research time.
Subspecialty fellowships and service-linked loan repayment are related paths, with separate training, license and site conditions.
[ABIM internal medicine certification](https://www.abim.org/certification/policies/internal-medicine-subspecialty-policies/internal-medicine/), [ACGME internal medicine and subspecialty program requirements](https://www.acgme.org/specialties/internal-medicine/program-requirements-and-faqs-and-applications/), [HRSA clinician loan repayment](https://nhsc.hrsa.gov/loan-repayment)

A separate sandbox check used short summaries of publisher pages as of November 4, 2026.
The student feed led with NIH's research year and AAMC's visiting electives, followed by the later-stage Students to Service program.
The professional feed surfaced Michigan Medicine's faculty recruitment.
A UCSF hospitalist posting with a passed final deadline stayed visible in Explore and was excluded from For You.
These were manual imports for the sandbox check.
[Michigan Medicine faculty recruitment](https://medschool.umich.edu/departments/internal-medicine/divisions/hospital-medicine/faculty-recruitment), [UCSF academic hospitalist posting](https://aprecruit.ucsf.edu/JPF05553/)

## Other profile pairs

The sources below informed role vocabulary and the distinction between supervised placements and work for experienced candidates.
The example objectives and sandbox listings are fictional.

| Field | Student emphasis | Professional emphasis | Primary sources |
| --- | --- | --- | --- |
| Cybersecurity | Supervised alert triage, forensics and service-linked scholarships | Detection engineering, incident response and playbook ownership | [OPM CyberCorps](https://sfs.opm.gov/Student/Overview), [NIST NICE work roles](https://www.nist.gov/itl/applied-cybersecurity/nice/nice-framework-work-role-videos) |
| Epidemiology | MPH practicum, applied statistics and surveillance | Field epidemiology, outbreak response and evaluation | [CDC master's student opportunities](https://www.cdc.gov/fellowships/php/opportunities/applying-for-masters-degree-students-opportunities.html), [CDC EIS eligibility](https://www.cdc.gov/eis/php/participants/eligibility.html) |
| Clean energy | Grid modeling, storage research and applied fellowships | Distribution planning, interconnection and efficiency verification | [DOE Energy Storage Grand Challenge](https://www.energy.gov/documents/energy-storage-grand-challenge-roadmappdf), [SEI Climate Corps fellowship](https://www.climatecorps.org/fellow.html) |
| Accessibility design | Mentored prototyping, research and usability testing | Accessible design systems, conformance and testing ownership | [GSA accessibility roles](https://www.section508.gov/manage/roles/), [GSA accessibility testing](https://www.section508.gov/test/) |
| Robotics | Mechatronics, prototyping and supervised integration | Controls, system integration and production testing | [NASA internship programs](https://www.nasa.gov/learning-resources/internship-programs/), [BLS mechanical engineers](https://www.bls.gov/ooh/architecture-and-engineering/mechanical-engineers.htm) |
| Accounting and audit | Supervised assurance and financial statement testing | Audit ownership and internal controls assessment | [GAO student opportunities](https://www.gao.gov/about/careers/students-and-career-paths), [GAO financial management and assurance](https://www.gao.gov/about/careers/our-teams/FMA), [NASBA licensure](https://nasba.org/licensure/gettingacpalicense/howtogetlicensed/) |
| Public interest law | Legal aid clinics, compensated internships and supervised writing | Representation, impact litigation and host-backed projects | [DOJ Summer Law Intern Program](https://www.justice.gov/legal-careers/summer-law-intern-program), [Equal Justice Works fellowship](https://www.equaljusticeworks.org/opportunities/design-your-own-fellowship/) |
| K-12 education | Teacher residency and supervised classroom practice | Instructional coaching, curriculum and assessment | [Institute of Education Sciences teacher residency evaluation](https://ies.ed.gov/use-work/evaluations/evaluation-teacher-residency-programs), [BLS instructional coordinators](https://www.bls.gov/OOH/education-training-and-library/instructional-coordinators.htm) |
| Museum education | Object-based learning and visitor-facing practice | Interpretation programs, partnerships and evaluation | [National Museum of American History internship pathways](https://www.americanhistory.si.edu/about/careers/internship/opportunities), [Smithsonian learning programs specialist example](https://nationalzoo.si.edu/sites/default/files/documents/learning_programs_specialist.pdf) |
| Water infrastructure | Sampling and supervised utility or laboratory work | Treatment design and utility capital projects | [EPA research opportunities](https://www.epa.gov/careers/research-opportunities-recent-students-and-postdocs), [EPA water infrastructure workforce](https://www.epa.gov/sustainable-water-infrastructure/water-infrastructure-sector-workforce) |
| AI and ML research | Doctoral internships, mentoring and reproducible evaluation | Research ownership, empirical rigor and scientific applications | [OpenAI emerging talent](https://openai.com/careers/emerging-talent/), [OpenAI research scientist example](https://openai.com/careers/research-scientist-san-francisco/) |
| Quantitative finance | Market microstructure projects, pricing and supervised model research | Signal ownership, portfolio construction and transaction costs | [Citadel Securities internships](https://www.citadelsecurities.com/careers/internships), [Citadel Securities quantitative research](https://www.citadelsecurities.com/careers/quantitative-research/) |
| Experimental particle physics | Dissertation placements, detector calibration and collider analysis | Postdoctoral or staff research, reconstruction and trigger development | [CERN Doctoral Studentship](https://careers.cern/programmes/doctoral-studentship/), [CERN Research Fellowship](https://careers.cern/programmes/research-fellowship/), [Fermilab computational science internships](https://internships.fnal.gov/fermilab-computational-science-internship/) |
