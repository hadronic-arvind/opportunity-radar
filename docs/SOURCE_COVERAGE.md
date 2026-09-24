# Cross-industry source coverage

Automatic and named profiles share the `cross-industry` pack of supported structured employer feeds.
Field-specific packs add specialist sources; they do not define which occupations an employer can offer.
The scorer uses listing evidence and user preferences; employer domain and pack metadata do not award fit points.
Manual coverage and explicit source disabling remain available for users who want a narrower scan.
Custom private sources keep their existing enable/disable behavior.

## Cost and limits

The catalog contains 352 resources, including 189 feeds in the shared cross-industry set.
The unconfigured starter still enables five feeds.
New feeds use a 24-hour cadence; existing source cadences remain unchanged.
Collection is sequential, with the existing timeout, response-size, and 5,000-listing per-source limits.
A failed fetch never removes prior listings.
The first broad scan and an explicit Scan all take longer than a due-only refresh.
This is a curated set of supported public employer boards, not a claim to cover every company or every opening worldwide.
Manual directories and unsupported endpoints are excluded from the shared pack.

## Verified additions

On 2026-09-21, 110 candidate boards were requested sequentially through the production Greenhouse, Lever, and Ashby adapters.
Only the 69 successful nonempty feeds below were added; 35 failed requests and six empty boards were excluded.
No personal profiles, databases, credentials, or application submissions were used.
Counts are observations at verification time and naturally change as employers update their boards.
These are additions to the existing catalog; this audit does not claim to have revalidated all older feeds.

| Employer | Adapter | Listings observed |
| --- | --- | ---: |
| [Affirm](https://job-boards.greenhouse.io/affirm) | greenhouse | 200 |
| [Airbnb](https://job-boards.greenhouse.io/airbnb) | greenhouse | 161 |
| [Airtable](https://job-boards.greenhouse.io/airtable) | greenhouse | 16 |
| [Akuna Capital](https://job-boards.greenhouse.io/akunacapital) | greenhouse | 40 |
| [Asana](https://job-boards.greenhouse.io/asana) | greenhouse | 104 |
| [Automattic](https://job-boards.greenhouse.io/automatticcareers) | greenhouse | 16 |
| [Axon](https://job-boards.greenhouse.io/axon) | greenhouse | 528 |
| [Block](https://job-boards.greenhouse.io/block) | greenhouse | 221 |
| [Brex](https://job-boards.greenhouse.io/brex) | greenhouse | 251 |
| [Chan Zuckerberg Initiative](https://job-boards.greenhouse.io/chanzuckerberginitiative) | greenhouse | 11 |
| [Chime](https://job-boards.greenhouse.io/chime) | greenhouse | 70 |
| [Clear Street](https://job-boards.greenhouse.io/clearstreet) | greenhouse | 32 |
| [Crunchyroll](https://job-boards.greenhouse.io/crunchyroll) | greenhouse | 75 |
| [Crusoe](https://jobs.ashbyhq.com/crusoe) | ashby | 353 |
| [Databricks](https://job-boards.greenhouse.io/databricks) | greenhouse | 876 |
| [DonorsChoose](https://job-boards.greenhouse.io/donorschoose) | greenhouse | 6 |
| [DoorDash](https://job-boards.greenhouse.io/doordashusa) | greenhouse | 460 |
| [Dropbox](https://job-boards.greenhouse.io/dropbox) | greenhouse | 44 |
| [DRW](https://job-boards.greenhouse.io/drweng) | greenhouse | 166 |
| [Duolingo](https://job-boards.greenhouse.io/duolingo) | greenhouse | 80 |
| [Elastic](https://job-boards.greenhouse.io/elastic) | greenhouse | 366 |
| [ElevenLabs](https://jobs.ashbyhq.com/elevenlabs) | ashby | 225 |
| [FanDuel](https://job-boards.greenhouse.io/fanduel) | greenhouse | 84 |
| [Flexport](https://job-boards.greenhouse.io/flexport) | greenhouse | 192 |
| [Glossier](https://job-boards.greenhouse.io/glossier) | greenhouse | 28 |
| [Gusto](https://job-boards.greenhouse.io/gusto) | greenhouse | 92 |
| [Harvey](https://jobs.ashbyhq.com/harvey) | ashby | 302 |
| [HubSpot](https://job-boards.greenhouse.io/hubspotjobs) | greenhouse | 140 |
| [Human Rights Watch](https://job-boards.greenhouse.io/humanrightswatch) | greenhouse | 13 |
| [IMC Trading](https://job-boards.greenhouse.io/imc) | greenhouse | 173 |
| [Included Health](https://jobs.lever.co/includedhealth) | lever | 57 |
| [Indigo Ag](https://job-boards.greenhouse.io/indigo) | greenhouse | 2 |
| [Instacart](https://job-boards.greenhouse.io/instacart) | greenhouse | 110 |
| [Jane Street](https://job-boards.greenhouse.io/janestreet) | greenhouse | 231 |
| [Linear](https://jobs.ashbyhq.com/linear) | ashby | 32 |
| [Lucid Motors](https://job-boards.greenhouse.io/lucidmotors) | greenhouse | 400 |
| [Modern Health](https://job-boards.greenhouse.io/modernhealth) | greenhouse | 13 |
| [MongoDB](https://job-boards.greenhouse.io/mongodb) | greenhouse | 401 |
| [NewLimit](https://job-boards.greenhouse.io/newlimit) | greenhouse | 14 |
| [Notion](https://jobs.ashbyhq.com/notion) | ashby | 128 |
| [Nuro](https://job-boards.greenhouse.io/nuro) | greenhouse | 107 |
| [One Medical](https://job-boards.greenhouse.io/onemedical) | greenhouse | 376 |
| [Perplexity](https://jobs.ashbyhq.com/perplexity) | ashby | 119 |
| [Pinterest](https://job-boards.greenhouse.io/pinterest) | greenhouse | 162 |
| [Pivot Bio](https://job-boards.greenhouse.io/pivotbio) | greenhouse | 9 |
| [Ramp](https://jobs.ashbyhq.com/ramp) | ashby | 149 |
| [Relativity Space](https://job-boards.greenhouse.io/relativity) | greenhouse | 334 |
| [Replit](https://jobs.ashbyhq.com/replit) | ashby | 77 |
| [Ro](https://jobs.lever.co/ro) | lever | 53 |
| [Roblox](https://job-boards.greenhouse.io/roblox) | greenhouse | 249 |
| [Rubrik](https://job-boards.greenhouse.io/rubrik) | greenhouse | 131 |
| [Samsara](https://job-boards.greenhouse.io/samsara) | greenhouse | 269 |
| [SentinelOne](https://job-boards.greenhouse.io/sentinellabs) | greenhouse | 234 |
| [Sierra](https://jobs.ashbyhq.com/sierra) | ashby | 210 |
| [Spotify](https://jobs.lever.co/spotify) | lever | 71 |
| [Spring Health](https://job-boards.greenhouse.io/springhealth66) | greenhouse | 70 |
| [Squarespace](https://job-boards.greenhouse.io/squarespace) | greenhouse | 33 |
| [Stripe](https://job-boards.greenhouse.io/stripe) | greenhouse | 671 |
| [Talkspace](https://job-boards.greenhouse.io/talkspace) | greenhouse | 19 |
| [Toast](https://job-boards.greenhouse.io/toast) | greenhouse | 319 |
| [Twitch](https://job-boards.greenhouse.io/twitch) | greenhouse | 48 |
| [Vanta](https://jobs.ashbyhq.com/vanta) | ashby | 93 |
| [Vast](https://job-boards.greenhouse.io/vast) | greenhouse | 204 |
| [Virtu Financial](https://job-boards.greenhouse.io/virtu) | greenhouse | 50 |
| [Waabi](https://jobs.lever.co/waabi) | lever | 85 |
| [Wealthfront](https://jobs.lever.co/wealthfront) | lever | 24 |
| [Zipline](https://job-boards.greenhouse.io/flyzipline) | greenhouse | 341 |
| [ZoomInfo](https://job-boards.greenhouse.io/zoominfo) | greenhouse | 110 |
| [Zoox](https://jobs.lever.co/zoox) | lever | 236 |

## Maintain the catalog

1. Verify the employer's public careers board and use its supported structured feed, with no occupation filter on the board request.
2. Use an isolated checkout and `python3 -m monitor sources test SOURCE_ID` to exercise the production adapter before publishing the source.
3. Keep sector packs and domains descriptive; include every supported, auto-enabled structured employer feed in `cross-industry`.
4. Use a conservative cadence and retain the existing response/time limits; do not increase limits just to admit a failing feed.
5. Run `./scripts/dev_check.sh discover -s tests -p 'test_cross_industry.py'`, the source-catalog tests, and the release gate.

The cross-industry tests enforce pack membership and exercise profile selection, job matching, explicit exclusions, cadence, and saved application status through the CLI.
Schema details and private source controls remain in [Configuration](CONFIGURATION.md).
