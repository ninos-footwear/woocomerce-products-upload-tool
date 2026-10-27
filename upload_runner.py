"""
Glue between the Streamlit app and the engine (product_sorting_engine.py).

Takes the uploaded inventory file and the optional images ZIP as bytes, runs the
engine's own pipeline unchanged in a temporary folder, and returns everything the
UI needs (status, summary, tables, console log, output files as bytes).
"""
import io
import os
import tempfile
import zipfile
from contextlib import redirect_stdout
from dataclasses import dataclass, field

import pandas as pd

import product_sorting_engine as engine

OUTPUT_NAME = "woocommerce_import_new.csv"
MAX_UNZIPPED_BYTES = 2 * 1024 ** 3          # refuse ZIPs that expand beyond 2 GB


@dataclass
class RunResult:
    status: str                              # 'success' | 'partial' | 'failed'
    exit_code: int
    message: str = ''
    summary: dict = field(default_factory=dict)
    output: pd.DataFrame = None              # WooCommerce rows (None if nothing produced)
    findings: pd.DataFrame = None            # validation report
    images: pd.DataFrame = None              # image diagnostics
    log: str = ''                            # engine console output
    zip_notes: list = field(default_factory=list)
    images_provided: bool = False
    files: dict = field(default_factory=dict)  # file name -> bytes, ready to download


def _is_junk(path):
    parts = path.replace('\\', '/').split('/')
    name = parts[-1]
    return (not name or '__MACOSX' in parts or name.startswith('._')
            or name in ('.DS_Store', 'Thumbs.db', 'desktop.ini'))


def extract_images(zip_bytes, dest):
    """Flatten every file of the ZIP into `dest` (the engine expects one flat folder).

    Only the base name of each entry is used, so nothing can be written outside `dest`.
    Returns a list of notes about skipped entries.
    """
    notes = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise engine.InputError("The images file is not a valid ZIP archive.")
    with zf:
        members = [m for m in zf.infolist() if not m.is_dir() and not _is_junk(m.filename)]
        total = sum(m.file_size for m in members)
        if total > MAX_UNZIPPED_BYTES:
            raise engine.InputError(f"The images ZIP expands to {total / 1024 ** 3:.1f} GB; the limit is 2 GB.")
        seen = {}                            # casefolded name -> ZIP path (Windows folders ignore case)
        for m in members:
            name = os.path.basename(m.filename.replace('\\', '/'))
            key = name.casefold()
            if key in seen:
                notes.append(f'"{m.filename}" skipped: clashes with "{seen[key]}" (same name ignoring '
                             f'upper/lower case or sub-folders). Rename or remove one of them.')
                continue
            seen[key] = m.filename
            with zf.open(m) as src, open(os.path.join(dest, name), 'wb') as out:
                out.write(src.read())
    if not seen:
        notes.append('The ZIP contains no files.')
    return notes


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def process(inventory_bytes, inventory_name, zip_bytes=None):
    """Run the engine on uploaded data. Never raises for user-input problems."""
    log = io.StringIO()
    with tempfile.TemporaryDirectory(prefix='ninos_') as tmp:
        ext = os.path.splitext(inventory_name)[1].lower()
        input_path = os.path.join(tmp, 'inventory' + ext)
        with open(input_path, 'wb') as f:
            f.write(inventory_bytes)

        images_dir = os.path.join(tmp, 'images')
        zip_notes = []
        try:
            if zip_bytes is not None:
                os.makedirs(images_dir)
                zip_notes = extract_images(zip_bytes, images_dir)
            output_path = os.path.join(tmp, OUTPUT_NAME)
            # Capturing stdout also keeps the engine's emoji output away from the Windows console.
            with redirect_stdout(log):
                summary, out, findings, img_rows, code = engine.run(input_path, output_path, images_dir)
        except engine.InputError as e:
            message = str(e).replace(input_path, inventory_name)     # hide the temp path
            return RunResult(status='failed', exit_code=1, message=message, log=log.getvalue(), zip_notes=zip_notes)

        val_path, img_path = engine.report_paths(output_path)
        files = {}
        for path in (output_path, val_path, img_path):
            if os.path.isfile(path):
                files[os.path.basename(path)] = _read(path)

    findings_df = pd.read_csv(io.BytesIO(files[os.path.basename(val_path)]), dtype=str).fillna('')
    images_df = pd.read_csv(io.BytesIO(files[os.path.basename(img_path)]), dtype=str).fillna('')
    if code == 0:
        status, message = 'success', 'All checks passed. The import CSV is complete.'
    elif code == 2:
        status, message = 'partial', ('Some product groups were quarantined. The import CSV contains only the '
                                      'valid products. Review the validation report before importing.')
    else:
        status, message = 'failed', 'The structural self-check failed, so no import CSV was written.'
    return RunResult(status=status, exit_code=code, message=message, summary=summary,
                     output=out if code != 1 else None, findings=findings_df, images=images_df,
                     log=log.getvalue(), zip_notes=zip_notes, files=files,
                     images_provided=zip_bytes is not None)


def bundle(files):
    """All output files in one ZIP (bytes)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()
