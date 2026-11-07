# PETs Transformer — protect the data, keep its value

**Since AI Hackathon 2026 · TrustED challenge · team Cowork (Mert Yünlü, solo)**

**Demo video (1:54):** https://youtu.be/0QSOFyT7YBw

**At a glance:** linkage attacks 18–57 % → 0 % · composite 0.90 against baselines of 0.48 · 94 % of sensitive columns
found on six unseen datasets · free-text redaction F2 0.97 · differentially private release at ε = 3.2 · every release
published to a FIWARE NGSI-LD broker as a DCAT-AP catalogue, run live on Antares. Running the code needs the
organisers' team-only Proving Ground package (see [Reproduce](#reproduce)).

A working tool that takes a dataset with personal data, **detects** what every column and text contains,
**selects** a privacy-enhancing technique (PET) per field for a chosen sharing scenario, **transforms** the data,
and **demonstrates** — with numbers — how much re-identification risk was removed and how much analytical value
survived. It does not pick one magic setting: it searches the privacy–utility trade-off and shows the whole frontier,
then states honestly what risk remains.

Everything below is measured with the organisers' `pets-eval` toolkit on the synthetic PETs Proving Ground package
(nine domains, 4.7 M rows, 475 catalogued columns, 354 000 labelled PII spans, attacker files with ground truth).
Self-assessment scores are not the official judging score.

## The TrustED checklist, answered with evidence

The organisers asked every team to show five things (Discord, 7 Nov). Where each one is:

| TrustED asks for | What we show | Evidence (HEALTH-S1 unless stated) |
|---|---|---|
| Identified data types and privacy risks | Every column labelled by role and subtype from its values; the risk measured on the *original* data before anything is changed | 98.1 % of visits are unique on (municipality, arrival time); the three shipped linkage attacks re-identify **18.5 % / 54.2 % / 57.0 %** of their targets in the original table; detector held-out macro-F1 0.74, 94 % of sensitive columns found |
| Selected PETs and the reasoning | One action per field with a written reason, chosen from the scenario's needs | `decisions.json`: 38 field decisions (keyed pseudonym, age band, week rounding, ICD-10 truncation, k ≥ 5 suppression, drop…), also published as `FieldPolicy` entities on the FIWARE broker |
| The transformed / protected output | Protected tables for HEALTH-S1, a differentially private release for HEALTH-S2, redacted free text | `pets_submission/` in the source zip (row maps for scoring only, never published) |
| Measurable privacy and utility results | `pets-eval` scorecard before/after, the frontier of 81 policies | privacy **1.00**, utility **0.80**, composite **0.90** vs organisers' baselines ≈0.48; attacks **0 %** after; DP release ε = 3.2 → utility 0.61 |
| A defined data-sharing scenario and remaining limitations | Recipient, purpose, trust level and threat model stated up front; what was sacrificed and what risk remains said plainly | HEALTH-S1: university research group, controlled environment; hour-of-day curve (H-U8) sacrificed, shown on the frontier; [Limitations](#limitations-stated-plainly) |

Risk before and after, HEALTH-S1 `ed_visits` (46 398 visits), measured by `pets-eval scorecard`:

| Measure | Original table | Protected release |
|---|---|---|
| Re-identified by `news_clips_health` attacker (200 targets) | 18.5 % | **0 %** |
| Re-identified by `social_posts_health` attacker (850 targets) | 54.2 % | **0 %** |
| Re-identified by `public_directory` attacker (739 targets) | 57.0 % | **0 %** |
| Visits unique on (municipality, arrival time) | 98.1 % | **0 %** (smallest group 5, median 75) |
| Direct identifiers (national ID, name, phone, address) | present | removed or keyed pseudonym |
| Utility tasks answered (H-U2, H-U4, H-U6, H-U7) | – | 4 of 4 at score 1.00; H-U8 hour curve sacrificed |

## What it does (the five things the brief asks for)

| Step | What the tool does | How it is measured |
|---|---|---|
| 1 Detect | Classifies every column by **values first, names second**: check-digit validators (HETU, IBAN, Luhn cards), formats (IP, MAC, plates, postcodes, coordinates, timestamps, dates of birth), gazetteers built only from the *public* attacker files (names, streets, municipalities), statistics (cardinality, length, token shape), and whether the table is a *person table* or an *event table*. Optional second opinion from a local LLM (Ollama) or the Gemini API for low-confidence columns. | `pets-eval detect` against the field catalog. Rules were tuned on 3 datasets; **6 datasets were held out**. The unlabelled `unseen` export (26 obfuscated columns) is labelled and submitted for organiser scoring. |
| 2 Select | A policy maps (role, subtype, scenario needs) → action: keyed pseudonym (HMAC-SHA256), age band, timestamp rounding, postcode prefix, code truncation, rare-category merging, free-text redaction, drop. Every decision carries a reason. | `decisions.json` per release; shown in the HTML report. |
| 3 Transform | Produces the protected tables, local suppression to k ≥ 5 on the released quasi-identifiers municipality and arrival time (see Limitations for the age band), shuffled rows, row maps for scoring only. | Reproducible with one command. |
| 4 Demonstrate | `pets-eval scorecard`: linkage attacks with the shipped attacker knowledge, k-anonymity, hash reversal, free-text leakage; utility tasks of the scenario (medians, mixes, revisit rates, joins, hour curves, model AUC). Plus a **frontier search**: ~80 policies scored, Pareto front drawn, chosen point and baselines marked. | privacy / utility / composite per release, frontier chart. |
| 5 Scenario | One of the 27 sharing scenarios (recipient, purpose, trust level, threat model) is chosen up front; the report states which questions the recipient can still answer and which risks remain. | report section "What remains, honestly". |

Free text (Finnish, Swedish, English clinical notes and contact-centre chats) is handled by a hybrid redactor:
validators, spoken-number sequences ("nolla neljä viisi …", "noll fyra …"), spelled e-mails, cue words that type a
nearby value (hetu, asiakasnumero, salasana, IMEI), gazetteers, dates, ages, addresses, organisations, signatures,
and conversation context (a number read out over several turns keeps its label). Output: typed placeholders
(`[PERSON]`, `[PHONE]`, …) and span files that `pets-eval text` scores.

## Results (measured, Saturday 7 Nov 2026)

Health, scenario HEALTH-S1 (secondary-use research in a controlled environment), `pets-eval` self-assessment:

| Release | Privacy | Utility | Composite |
|---|---|---|---|
| B2 classic SDC (organisers' baseline, from BENCHMARK.md) | 0.36 | 0.79 | ≈0.48 |
| B2-k, k ≥ 5 (organisers' baseline) | 0.52 | 0.40 | ≈0.48 |
| quickstart example | 0.37 | 0.79 | 0.49 |
| **PETs Transformer, catalog labels, week resolution** | **1.00** | **0.80** | **0.90** |
| **PETs Transformer, its own detector's labels (no catalog)** | **1.00** | **0.80** | **0.90** |

The one utility task sacrificed is the hour-of-day arrival curve (H-U8): keeping the hour in the release makes
almost every row unique on (municipality, arrival time) and local suppression then destroys the municipality mix.
The frontier search (81 policies, `outputs/search/health/HEALTH-S1/catalog/frontier.png`) makes this trade-off
visible instead of hiding it.

Health, scenario HEALTH-S2 (public weekly dashboard, open data): a **differentially private** release — person-level
Laplace mechanism, contribution bound 4 visits, noisy (week × age band × diagnosis group) histogram expanded to
synthetic rows, hours sampled from a separate noisy histogram, and the 254-query workload answered with noise.
No row map: nothing links a released row to a person.

| ε (total) | utility (mean of H-U1 weekly ILI, H-U8 hour curve, H-DP workload) | H-U1 | H-U8 | H-DP |
|---|---|---|---|---|
| 0.8 | 0.47 | 0.00 | 0.89 | 0.51 |
| 3.2 (default release) | 0.61 | 0.08 | 0.96 | 0.79 |
| 6.4 | 0.77 | 0.47 | 0.96 | 0.86 |
| 12.8 | 0.85 | 0.69 | 0.97 | 0.88 |

B3 (organisers' DP baseline, workload only) reaches 0.80 at ε = 2; the weekly ILI-by-age cells are small, so they
are the price of a strict budget — the curve `outputs/search/health/HEALTH-S2/eps_frontier.png` shows it.

Detector (`pets-eval detect`, privacy-role macro-F1 / recall of sensitive columns):

| Datasets | macro-F1 | sensitive recall | subtype accuracy |
|---|---|---|---|
| B1 rule baseline (organisers, all datasets) | 0.65 | 0.73 | – |
| Ours, development datasets (health, finance, cyber) | 0.89 | 0.98 | 0.81 |
| **Ours, six held-out datasets** | **0.74** | **0.94** | **0.76** |
| Ours, all nine | 0.82 | 0.96 | 0.78 |
| + local LLM second opinion (qwen3:8b via Ollama) on the 31 columns below confidence 0.6, held-out | 0.75 (+0.002) | 0.94 | 0.76 |
| + **Gemini API** second opinion (gemini-3.5-flash) on the same low-confidence columns (24 in the nine catalogued datasets; 3 calls failed on the free-tier quota), held-out | **0.75 (+0.009)** | 0.94 | 0.78 |

The LLM pass is a small, honest gain, not a different detector: the rules already cover the formatted identifiers, and
most remaining errors are convention questions (is a transaction amount a quasi-identifier?). The local model changed
almost nothing; Gemini changed 12 of the 24 roles it was asked about (mostly quasi-identifier → non-sensitive or
free text: station names, ticket titles, transfer messages) and lifted held-out macro-F1 by 0.009, development-set
macro-F1 from 0.89 to 0.91 and subtype accuracy by 0.02. Letting a model override more columns (threshold 0.75 instead
of 0.6, local model) made things worse: held-out macro-F1 0.72, sensitive recall 0.86. It is therefore kept as a narrow,
explainable option: the rule evidence stays next to the model's reason, and only a dozen sample values per column leave
the machine.

Free-text redaction (`pets-eval text`, overlap F2 / identifying-value leakage):

| Field | records | micro F2 (exact) | identifying leakage |
|---|---|---|---|
| health / clinical_notes.note_text (fi, sv, en) | 30 103 | 0.97 | 0.01 % |
| customer_service / turns.text (train split) | 26 000+ | 0.87 | 0.7 % |

## FIWARE: the context broker as a catalogue of privacy-assessed releases

A protected release is only useful if the recipient can find it and judge it before asking for access. PETs
Transformer publishes each release to an **NGSI-LD context broker** as linked entities, never the rows:

| NGSI-LD entity | Model | What it carries |
|---|---|---|
| `Dataset` | Smart Data Model **DCAT-AP Dataset** | title, description, keywords, applicable legislation (GDPR Art. 9 / Recital 26), temporal resolution, provenance, distributions |
| `Distribution` | Smart Data Model **DCAT-AP Distribution** | one per released file (format, size, where it is delivered) → `belongsToDataset` |
| `DataSharingScenario` | NGSI-LD default context | recipient, purpose, trust level, threat model, legal context, required analyses |
| `PrivacyAssessment` | NGSI-LD default context | pets-eval privacy / utility / composite, each linkage attack before → after, k-anonymity, utility tasks, sacrificed tasks, DP budget → `refDataset`, `refScenario` |
| `FieldPolicy` | NGSI-LD default context | one per source column: role, subtype, PET applied, reason → `refDataset` |

The controller writes them with one batch upsert; the recipient side queries the broker, e.g. "every
`PrivacyAssessment` with `compositeScore >= 0.85`", follows `refDataset` to the DCAT-AP catalogue entry and reads
the `FieldPolicy` entities to see which technique was applied to which field and why. The DCAT-AP entities are validated
against the official Smart Data Models JSON schemas before publishing (the check catches a missing title, a wrong type
or a malformed date; it passes for all 48 entities of the two releases).

**Data classification on the data itself (NGSI-LD `scope`, recipients filter with `scopeQ`).** Every entity carries a
hierarchical scope derived from the scenario's trust level and the protection applied, so the broker itself only shows
a recipient what its trust level allows (suggested by Chandra Challagonda, FIWARE Foundation, at the hackathon):

| Scope | On | Example query |
|---|---|---|
| `/health/research`, `/health/public` | `DataSharingScenario` | |
| `/health/research/pseudonymised`, `/health/public/dp` | `Dataset`, `Distribution`, `PrivacyAssessment` | public recipient `scopeQ=/health/public/#` → only the DP release; research recipient in a controlled environment `scopeQ=/health/research/#,/health/public/#` → both |
| `/health/research/pseudonymised/<privacy role>` | `FieldPolicy` | `scopeQ=/health/+/+/direct_identifier` → the 5 direct identifiers and what was done to each |

**Who the recipient is: signed credential + policy enforcement point (`pets_transformer/access.py`).** Scope labels
what may be seen; this layer makes sure a recipient only sees what its *verified* trust level allows. The controller
issues each recipient a data-access credential (Verifiable-Credential-shaped claims with `trustLevel`, signed ES256, with
an expiry). A read-only PEP in front of the broker verifies signature, issuer and expiry (401 otherwise), maps the trust
level to the scopes it allows (`public` → `/+/public/#`; `controlled_research` → `/+/research/#`, `/+/public/#`),
narrows the recipient's own `scopeQ` to them (403 when nothing is left), checks entities read by id against the same
scopes and refuses writes. Tested cases (mock selftest, 12 of 12 pass; the same 9 query cases also run live on Antares in
the browser with WebCrypto):

| Recipient | Result |
|---|---|
| no credential | 401 |
| public credential | 1 release (`/health/public/dp`) |
| research credential | 2 releases |
| public credential asking `scopeQ=/health/research/#` | 403 |
| public credential edited to say `controlled_research` | 401 (signature invalid) |
| expired credential, or one signed by an unknown key | 401 |
| research credential, `scopeQ=/health/+/+/direct_identifier` | PEP sends `/health/research/+/direct_identifier,/health/public/+/direct_identifier` → 5 fields |
| public credential, same query | 0 fields |
| public credential reading the research `Dataset` by id | 403 |
| research recipient end to end (`consume` through the PEP) | 2 releases with their field policies |
| recipient trying to write through the PEP | 403 |

This is a small, tested stand-in for the FIWARE pattern (Keyrock + Wilma PEP proxy, or VCVerifier + PEP + ODRL in a
data space), not a replacement: one issuer key, no revocation list, no consent or purpose checks. In the Antares
playground the PEP is a function in the same page; in a deployment it is the only network path to the broker.

**Live run on a FIWARE broker (Saturday 7 Nov):** the Antares NGSI-LD context broker (Rust, ETSI CIM 009 V1.9.1,
the live demo broker linked from the FIWARE award page) running in its browser playground: batch upsert of all 48
entities → HTTP 201; the recipient query `PrivacyAssessment` with `compositeScore >= 0.85` returned the HEALTH-S1
release (composite 0.898, worst linkage attack 0.57 → 0, sacrificed H-U8) with its 38 field policies and 2
distributions, and `FieldPolicy` with `column == "date_of_birth"` answered "age_band — birth date → age at event in
10-year bands". The in-browser broker cannot fetch remote `@context` documents, so for it the DCAT-AP terms we use are
inlined with the same IRIs as the official context, next to the NGSI-LD core context v1.9. Same evening, with `scope`
added: `scopeQ=/health/public/#` → 1 release (the DP dashboard, composite 0.777), `scopeQ=/health/research/#,/health/public/#`
→ 2 releases, `scopeQ=/health/+/+/direct_identifier` → 5 field policies (hetu → keyed pseudonym, name, phone, address
and physician name → dropped).

```bash
python run.py fiware                                        # dry run: 48 entities, schema validation, outputs/fiware/
                                                            #   + outputs/fiware/antares_console.js: paste into the console of
                                                            #     https://antaresbroker.joinedcontext.com/demo/ (publish + recipient query)
python run.py fiware --broker https://<ngsi-ld-broker>      # batch upsert + recipient-side query
python run.py fiware --broker https://<ngsi-ld-broker> --consume --min-composite 0.85
python tools/ngsi_ld_mock.py --selftest                     # offline test of publish/query/scopeQ + the 12 PEP access cases (a test double, not FIWARE)
```

## Reproduce

```bash
pip install -e materials/PROVING_GROUND/TRUSTED_PETS_PROVING_GROUND_2026/toolkit   # pets-eval (MIT, Since AI ry)
pip install numpy pandas scipy scikit-learn matplotlib jsonschema rfc3339-validator cryptography
python run.py detect --dataset all                       # field classification + scores
python run.py detect --dataset all --llm gemini          # optional second opinion (GEMINI_API_KEY in .env) or --llm ollama
python run.py protect --dataset health --scenario HEALTH-S1 --variant week --time-res week
python run.py protect --dataset health --scenario HEALTH-S1 --variant detected_week --time-res week --labels outputs/detect/health_labels.csv
python run.py search  --dataset health --scenario HEALTH-S1 --budget 90       # frontier (≈40 min)
python run.py redact  --dataset health --table clinical_notes --column note_text
python run.py redact  --dataset customer_service --table turns --column text
python run.py report  --dataset health --scenario HEALTH-S1 --variant week    # HTML report
python run.py dp --variant dp_eps3 && python run.py dp --sweep                  # HEALTH-S2 DP release + ε curve
python run.py submission --release health:HEALTH-S1:week --release health:HEALTH-S2:dp_eps3   # pets_submission/ (organisers only)
python run.py fiware                                                            # NGSI-LD catalogue entities (see above)
python tools/build_video.py                                                     # demo video (Edge headless + edge-tts + ffmpeg)
python tools/build_zip.py                                                       # source zip for the submission form
```

The proving-ground package is team-only material and is not in this repository (`materials/` is git-ignored);
set `PETS_ROOT` to its location. Outputs go to `outputs/` (git-ignored: they contain row maps). The organiser folder
`pets_submission/` is git-ignored too: it holds the row maps (`rowmap_*.csv`, scoring only, never published) and our
outputs on the organisers' unseen data, so it travels only inside the source zip.

## Limitations, stated plainly

- Privacy scores are against the attacker files shipped with the package; a stronger or differently informed attacker
  can do better. A low re-identification rate here is evidence, not proof.
- The detector's rules were written looking at three datasets' labels; numbers on those three are optimistic. The
  held-out numbers are the honest ones.
- Free text is template-generated synthetic text; the redactor's gazetteers come from the public attacker files, which
  share the synthetic population's names. On real text, recall of names would be lower; validators and cues transfer.
- The HMAC key is a fixed demo constant. In production it lives in the controller's vault.
- Only HEALTH-S1 was searched end to end in the time available; the engine is generic and other scenarios run through
  the same commands, but their policies were not tuned.
- k ≥ 5 holds on the quasi-identifiers the scorecard checks in this release (municipality, arrival week: smallest group
  5). The scenario's suggested target also includes the age band; with it, 28 % of visits are in groups smaller than 5
  (smallest 1). The three linkage attacks, which use age, still re-identify 0 %, but that target is not met yet.
- HEALTH-S2 suggests ε ≤ 1 per weekly release. Our default is one release of the whole year at ε = 3.2 in total; the
  sweep shows the price of a stricter budget (ε = 0.8 in total: utility 0.47).
- The FIWARE layer publishes catalogue metadata and scores, not data. It ran live on the Antares broker's in-browser
  build; the Python HTTP publisher (`--broker`) is tested against a test double only, because the build laptop has no
  Docker or Linux to host a broker server. Custom entity types
  (`PrivacyAssessment`, `FieldPolicy`, `DataSharingScenario`) use the NGSI-LD default context rather than a published
  Smart Data Model. The credential + PEP layer (`access.py`) is tested against the test double and, as page code, on
  Antares; it is not Keyrock or a data-space connector, has one issuer key and no revocation list.

## Declarations

- **AI tools:** Claude Code (Claude Fable 5.1, later Claude Opus 5.5) wrote most of the code and this text under the author's direction; the
  author set goals, chose the challenge, approved the plan, verified results and runs the demo. Optional LLM second
  opinion in the detector uses Ollama (qwen3:8b, local) or the Gemini API (gemini-3.5-flash). The demo video's
  narration is a synthetic voice (Microsoft Edge neural TTS via edge-tts); slides and script were written with Claude Code.
- **Third-party code:** `pets-eval` evaluation toolkit (Since AI ry, MIT) — used unmodified for all measurements;
  pandas, numpy, scipy, scikit-learn, matplotlib, jsonschema, cryptography (ES256 credentials); FIWARE Smart Data Models schemas (dataModel.DCAT-AP) and
  the ETSI NGSI-LD core context. Everything in `pets_transformer/` was written during the hackathon.
- **Data:** only the synthetic Proving Ground package and its public attacker files. No real personal data.

## Licence

MIT for the code in this repository (see `LICENSE`). Data and evaluation toolkit: see the Proving Ground package licences.
