# Ninos Product Sorting Engine: Context

## Goal
Adapt `new_risk_file.py` (Python, pandas) so it turns Ninos' **new raw Excel inventory** (`final-raw.xlsx`) into a **WooCommerce import CSV** (variable parents + variations), keeping the established parent-child, image, color and CSV rules. Deliverable is ONE main file. Out of scope until validated: Streamlit/web app, deployment, APIs, databases.

## Files
| File | Role |
|---|---|
| `new_risk_file.py` | Main engine (revised, delivered) |
| `final-raw.xlsx` | New input. Sheet1, header row 1, 78 rows, 37 columns (20 populated) |
| `final_products.csv` | Reference output for the OLD inventory (97 parents, 1082 variations, 25 columns) |
| `website_products_inventory.csv` | OLD inventory (1082 rows), used for regression |
| `check_product_images.py`, `check_parents.py`, `generate_color_map.py` | Project docs; not needed for the change. No older formatter and no authoritative color table were uploaded |
| `test_ninos.py` | 19 tests (unittest) |

## Input format (verified)
- Columns used: `barcode*`, `Item name*`, `long Desc`, `unit of measurement*`, `sell_price*(numbers)`, `item_category`, `Batch No*`, `total qty*(numbers)*`. Same names as the old format.
- `Variant Parent Item Barcode`, `sku`, `upc`, `item_brand` are empty, so parents are derived from barcodes.
- Barcode: `N` + 3 digits = article (`[:4]`), then color code (1 digit if barcode is 7 chars, 2 if 8), then 2-digit size (`[-2:]`).
- Description: `<Gender> <Category> [Sole Light|Upper Light] <color> color size <NN>`.
- New data: 8 articles (N557-N564), all NewBorn Boys/Girls, Shoe (66) and Pump (12), sizes 14-16, price 1500, qty 1 or 2.
- Color codes map one-to-one to names: brown 1, black 2, blue 5, beige 6, grey 8, silver 10, golden 11, mehroon 12, pink 13, ferozi 15. No overlap with old barcodes.
- **N559 is a source data-entry error:** Boys (Excel rows 23-31) and Girls (rows 32-34) share article N559.

## Decisions made by the user
1. N559 is invalid: quarantine it, do not invent parent SKUs, do not auto-split or pick a side.
2. Image folder is unavailable now. Keep the image logic intact, test with a simulated folder, accept the real folder as argument 3 later, and state that real matching is unverified.
3. Output = new valid inventory only (no merging with the old catalogue).
4. Keep the CLI compatible; smallest justified changes; new dependency only `openpyxl`.

## What the script does now
`python new_risk_file.py [input.xlsx|csv] [output.csv] [images_dir]` (defaults as before).
- **Pipeline:** load -> parse -> validate -> quarantine invalid groups -> build parents/variations -> structural self-check -> write CSV and reports.
- **Unchanged logic:** barcode extraction, description regex, adjective-cycle names, aggregation (stock summed, lowest parent price), image naming `"{article} {colorcode}.jpg"` then `" (2).jpg"`..., WordPress filename conversion, URL under `https://api.ninosfootwear.com/wp-content/uploads/2026/07`, 25-column schema.
- **Reports:** `<out>_validation.csv` (severity, rule, article, action, barcodes, source rows, conflicting values, recommended action) and `<out>_images.csv`.
- **Exit codes:** 0 clean, 2 partial (groups quarantined), 1 fatal.
- **Validation ERRORS (quarantine group):** mixed gender/sub-type/category, description unparsed, size mismatch, category mismatch/missing, bad price/qty, barcode format, duplicate barcode, color code with several names, color name with several codes. A missing barcode skips the row.
- **WARNINGS (kept):** color case variant, price varies in group, price zero.
- **INFO:** color table.
- **Image diagnostics (report-only):** missing image, angle gap, angles without primary, near-miss filenames the matcher ignores, unmatched files, non-image files, quarantined-article images.
- **Intentional change:** empty image entries are dropped from the parent Images join (the old code could write `", , ,"`).

## Results
- **New workbook:** 78 rows / 8 articles -> N559 quarantined (12 rows) -> **7 parents + 66 variations = 73 rows**. Self-check passed. Output CSV has blank image fields because no folder was supplied.
- **Tests:** 19/19 pass. They cover input handling, each validation rule, simulated-folder image cases, the CLI, self-check detection, and regression.
- **Regression (old inventory):**
  - With the gender rule disabled (test-only hook), output matches `final_products.csv` cell-for-cell, except the one deliberate Images fix (B331).
  - With the rule enabled, 12 old articles (A166, A202, A240, A242, A245, A246, A249, A250, A256, A263, B443, C024) are quarantined: 12 parents + 190 variations. The remaining 85 parents + 892 variations match the reference in all non-Name columns.
  - 28 of the 85 parent names shift because quarantined groups no longer use up adjective-cycle slots.
  - In the old reference output, 113 of those 190 variations were labelled with the opposite gender to their parent.

## Open items / limitations
- Real image folder not provided: actual file matching is **unverified** (only naming rules and URL pattern tested via simulation).
- No authoritative color map; `Mehroon` flagged for review only.
- Business question: are mixed-gender articles (12 in old data) always errors? Currently quarantined.
- Only the first Excel sheet is read; `.xls` unsupported. `.xlsx` needs `openpyxl`.
- Exit code 2 for partial results is new (old script always exited 0).
- Fix N559 in the source Excel (different article or gender), then re-run.
- Next phases after sign-off: supply the real image folder and re-run; only then consider the Streamlit app.
