# Ninos WooCommerce Upload Tool

Turns the raw Ninos inventory Excel file + the image file names into a WooCommerce product import CSV.

## Run

```
pip install -r requirements.txt
streamlit run app.py
```

Then open the link Streamlit prints (usually http://localhost:8501):

1. Upload the raw inventory Excel file (`.xlsx`, first sheet, header in row 1).
2. Click **Select images folder** and pick the folder with the product images. The browser reads only the
   file names and sends those; the images are not uploaded, so thousands of images take a second.
   (Alternatives in the same box: upload an `image_names.csv`, or a ZIP of the images.)
   Images must be named `"<article> <colorcode>.jpg"`, then `"<article> <colorcode> (2).jpg"`, `(3)`, ...
3. Click **Process** and download the import CSV and the reports.

Result: **Success** = all checks passed; **Partial** = some product groups were quarantined and left out
(see the validation report); **Failed** = unreadable input or failed self-check, no CSV produced.

## Image names file (optional alternative)

The **Select images folder** button is the normal way. If a browser can't select folders, make a names
file instead. On the computer that has the images folder, run:

```
python list_image_names.py
```

Pick the images folder, choose where to save, and upload the saved `image_names.csv` in box 2.
(Command-line form: `python list_image_names.py "<images folder>" [output.csv]`.) It needs only Python,
no extra packages. Files in sub-folders are included. The images themselves still have to be uploaded to
the WordPress media library (`2026/07`) so the URLs in the import CSV work.

## Files

| File | Role |
|---|---|
| `product_sorting_engine.py` | Processing engine (all rules). Also usable from the command line: `python product_sorting_engine.py input.xlsx output.csv images_dir` |
| `upload_runner.py` | Runs the engine on uploaded files in a temporary folder |
| `app.py` | Streamlit front end |
| `folder_picker.py` | The "Select images folder" button (reads file names in the browser) |
| `list_image_names.py` | Saves all file names of an images folder to `image_names.csv` |
| `context.md` | Background, decisions and open items |
