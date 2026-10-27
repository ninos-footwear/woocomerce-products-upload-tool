# Ninos WooCommerce Upload Tool

Turns the raw Ninos inventory Excel file + an images ZIP into a WooCommerce product import CSV.

## Run

```
pip install -r requirements.txt
streamlit run app.py
```

Then open the link Streamlit prints (usually http://localhost:8501):

1. Upload the raw inventory Excel file (`.xlsx`, first sheet, header in row 1).
2. Upload a ZIP with all product images, named `"<article> <colorcode>.jpg"`, then `"<article> <colorcode> (2).jpg"`, `(3)`, ... (sub-folders inside the ZIP are fine).
3. Click **Process** and download the import CSV and the reports.

Result: **Success** = all checks passed; **Partial** = some product groups were quarantined and left out
(see the validation report); **Failed** = unreadable input or failed self-check, no CSV produced.

## Files

| File | Role |
|---|---|
| `product_sorting_engine.py` | Processing engine (all rules). Also usable from the command line: `python product_sorting_engine.py input.xlsx output.csv images_dir` |
| `upload_runner.py` | Runs the engine on uploaded files in a temporary folder |
| `app.py` | Streamlit front end |
| `context.md` | Background, decisions and open items |
