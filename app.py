"""
Ninos -> WooCommerce upload tool (Streamlit front end).

Run:  streamlit run app.py
All processing rules live in product_sorting_engine.py; this file only handles uploads, display and downloads.
"""
import pandas as pd
import streamlit as st

import upload_runner as runner
from folder_picker import pick_folder_names

st.set_page_config(page_title="Ninos WooCommerce Upload Tool", page_icon="👟", layout="wide")

st.title("Ninos WooCommerce Upload Tool")
st.caption("Upload the raw inventory and select the images folder. The tool checks the data and builds the "
           "WooCommerce import CSV (parent products + variations).")

FOLDER, NAMES_FILE, ZIP = "Select images folder", "Upload image names file", "Upload ZIP of images"

# ── Inputs ───────────────────────────────────────────────────────────────────
c1, c2 = st.columns(2)
with c1:
    st.subheader("1. Raw inventory")
    inventory = st.file_uploader("Excel file (.xlsx) — first sheet, header in row 1",
                                 type=["xlsx", "xlsm", "csv"], key="inventory")
with c2:
    st.subheader("2. Product images")
    source = st.radio("Images from", [FOLDER, NAMES_FILE, ZIP], horizontal=True, label_visibility="collapsed",
                      help='Images must be named like "N557 2.jpg", "N557 2 (2).jpg". Only the names are '
                           'needed to build the image URLs.')
    picked, images_file = None, None
    if source == FOLDER:
        picked = pick_folder_names()
        if picked:
            where = f' in folder "{picked["folder"]}"' if picked.get("folder") else ""
            st.success(f'{len(picked["names"])} file name(s) read{where}. Pick again to change.')
    elif source == NAMES_FILE:
        images_file = st.file_uploader("image_names.csv (made by list_image_names.py) or a .txt, one name per line",
                                       type=["csv", "txt"], key="names_file")
    else:
        images_file = st.file_uploader("ZIP of all images (sub-folders are fine)", type=["zip"], key="images_zip")

have_images = bool(picked) or images_file is not None
if not have_images:
    st.info("No images selected yet: you can still run, but every Images field will be blank.")

# Forget old results when the inputs change.
upload_key = (inventory.file_id if inventory else None, source,
              picked.get("at") if picked else None, images_file.file_id if images_file else None)
if st.session_state.get("upload_key") != upload_key:
    st.session_state.upload_key = upload_key
    st.session_state.result = None

if st.button("Process", type="primary", disabled=inventory is None):
    with st.spinner("Checking data and building the import file..."):
        st.session_state.result = runner.process(
            inventory.getvalue(), inventory.name,
            images_bytes=images_file.getvalue() if images_file else None,
            images_name=images_file.name if images_file else "images.zip",
            image_names=picked["names"] if picked else None)

result = st.session_state.get("result")
if result is None:
    st.stop()

# ── Status ───────────────────────────────────────────────────────────────────
st.divider()
if result.status == "success":
    st.success(f"✅ Success — {result.message}")
elif result.status == "partial":
    st.warning(f"⚠️ Partial result — {result.message}")
else:
    st.error(f"❌ Failed — {result.message}")

for note in result.image_notes:
    st.warning(f"Images: {note}")

s = result.summary
if s:
    m = st.columns(6)
    m[0].metric("Parent products", s["valid_parents"])
    m[1].metric("Variations", s["valid_variations"])
    m[2].metric("Quarantined groups", len(s["quarantined_articles"]))
    m[3].metric("Errors", s["errors"])
    m[4].metric("Warnings", s["warnings"])
    m[5].metric("Missing images", len(s["missing_images"]) if result.images_provided else "—")

    if s["quarantined_articles"]:
        errs = result.findings[result.findings["severity"] == "ERROR"]
        reasons = errs.groupby("article")["rule"].agg(lambda r: ", ".join(sorted(set(r))))
        st.markdown(f"**Quarantined (not in the import CSV):** {len(s['quarantined_articles'])} group(s), "
                    f"{s['quarantined_rows']} row(s)")
        st.dataframe(pd.DataFrame({"article": reasons.index, "reason": reasons.values}),
                     hide_index=True, width="content")
    if s["skipped_rows"]:
        st.markdown(f"**Skipped rows (no barcode):** {s['skipped_rows']}")
    if s["self_check_problems"]:
        st.markdown("**Self-check problems:**")
        for p in s["self_check_problems"][:20]:
            st.markdown(f"- {p}")

# ── Downloads ────────────────────────────────────────────────────────────────
if result.files:
    st.subheader("Downloads")
    stem = runner.OUTPUT_NAME[:-4]
    buttons = [(runner.OUTPUT_NAME, "Import CSV"),
               (f"{stem}_validation.csv", "Validation report"),
               (f"{stem}_images.csv", "Image report")]
    d = st.columns(4)
    for col, (name, label) in zip(d, buttons):
        if name in result.files:
            col.download_button(label, result.files[name], name, "text/csv",
                                type="primary" if name == runner.OUTPUT_NAME else "secondary")
    d[3].download_button("All files (ZIP)", runner.bundle(result.files), "ninos_outputs.zip", "application/zip")
    if result.images_provided:
        st.caption("Image URLs point to the WordPress uploads folder (2026/07). Upload the same image files "
                   "to the WordPress media library before importing the CSV.")

# ── Details ──────────────────────────────────────────────────────────────────
tabs = st.tabs(["Import CSV preview", "Validation report", "Image report", "Log"])
with tabs[0]:
    if result.output is not None and len(result.output):
        st.dataframe(result.output, hide_index=True)
    else:
        st.write("No import rows were produced.")
with tabs[1]:
    if result.findings is not None and len(result.findings):
        st.dataframe(result.findings, hide_index=True)
    else:
        st.write("No findings.")
with tabs[2]:
    if result.images is not None and len(result.images):
        st.dataframe(result.images, hide_index=True)
    else:
        st.write("No image findings.")
with tabs[3]:
    st.code(result.log or "(no output)", language=None)
