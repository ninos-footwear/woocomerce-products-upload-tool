"""
Glue between the Streamlit app and the engine (product_sorting_engine.py).

Takes the uploaded inventory file and the optional images input as bytes, runs the
engine's own pipeline unchanged in a temporary folder, and returns everything the
UI needs (status, summary, tables, console log, output files as bytes).

Images input is either a ZIP of the images, or a list of image file names
(CSV with a "filename" column, made by list_image_names.py, or a .txt with one name
per line). For a names list, empty placeholder files with those names are created,
so the engine matches them exactly as it would match the real images.
"""
import csv
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
MAX_NAMES = 200_000                         # refuse absurdly long name lists
NAME_LIST_EXTS = ('.csv', '.txt')
BAD_NAME_CHARS = set('<>:"/\\|?*') | {chr(i) for i in range(32)}


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
    image_notes: list = field(default_factory=list)
    images_provided: bool = False
    files: dict = field(default_factory=dict)  # file name -> bytes, ready to download


def _is_junk(path):
    parts = path.replace('\\', '/').split('/')
    name = parts[-1]
    return (not name or '__MACOSX' in parts or name.startswith('._')
            or name in ('.DS_Store', 'Thumbs.db', 'desktop.ini'))


def _place(entries, dest, kind):
    """Write (source_label, name, read_bytes) entries flat into `dest`.

    Names clashing case-insensitively are skipped with a note (Windows folders ignore case,
    so the same rule is used everywhere). Only base names are used, so nothing can be
    written outside `dest`.
    """
    notes, seen = [], {}                     # casefolded name -> source label
    for label, name, read in entries:
        key = name.casefold()
        if key in seen:
            notes.append(f'"{label}" skipped: clashes with "{seen[key]}" (same name ignoring '
                         f'upper/lower case or sub-folders). Rename or remove one of them.')
            continue
        try:
            with open(os.path.join(dest, name), 'wb') as out:
                out.write(read())
        except OSError as e:
            notes.append(f'"{label}" skipped: cannot be saved under that name ({e.strerror}).')
            continue
        seen[key] = label
    if not seen:
        notes.append(f'The {kind} contains no files.')
    return notes


def extract_images(zip_bytes, dest):
    """Flatten every file of the ZIP into `dest` (the engine expects one flat folder)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise engine.InputError("The images file is not a valid ZIP archive.")
    with zf:
        members = [m for m in zf.infolist() if not m.is_dir() and not _is_junk(m.filename)]
        total = sum(m.file_size for m in members)
        if total > MAX_UNZIPPED_BYTES:
            raise engine.InputError(f"The images ZIP expands to {total / 1024 ** 3:.1f} GB; the limit is 2 GB.")
        entries = ((m.filename, os.path.basename(m.filename.replace('\\', '/')), (lambda m=m: zf.read(m)))
                   for m in members)
        return _place(entries, dest, 'ZIP')


def read_name_list(data, filename):
    """Image file names from a CSV ("filename" column) or a .txt (one name per line)."""
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise engine.InputError(f'"{filename}" is not a UTF-8 text file. Create it with list_image_names.py.')
    lines = text.splitlines()
    if filename.lower().endswith('.csv'):
        rows = list(csv.reader(lines))
        if not rows or [c.strip().casefold() for c in rows[0]][:1] != ['filename']:
            raise engine.InputError(f'"{filename}" must have a "filename" column in the first row '
                                    f'(create it with list_image_names.py).')
        names = [r[0] for r in rows[1:] if r]
    else:
        names = lines
    names = [n for n in names if n.strip()]
    if len(names) > MAX_NAMES:
        raise engine.InputError(f'"{filename}" lists {len(names)} names; the limit is {MAX_NAMES}.')
    return names


def create_placeholders(names, dest):
    """Empty files named like the real images, so the engine can match them."""
    notes, ok = [], []
    for n in names:
        base = os.path.basename(n.replace('\\', '/'))
        if _is_junk(n):
            continue
        if not base or base in ('.', '..') or any(c in BAD_NAME_CHARS for c in base):
            notes.append(f'"{n}" skipped: not a valid file name.')
            continue
        ok.append((n, base, lambda: b''))
    return notes + _place(ok, dest, 'names list')


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def process(inventory_bytes, inventory_name, images_bytes=None, images_name='images.zip', image_names=None):
    """Run the engine on uploaded data. Never raises for user-input problems.

    Images come from ONE of:
      image_names   list of file names (e.g. read from a folder in the browser), or
      images_bytes  a ZIP of the images, or a names list file (.csv / .txt) - picked by images_name.
    """
    log = io.StringIO()
    with tempfile.TemporaryDirectory(prefix='ninos_') as tmp:
        ext = os.path.splitext(inventory_name)[1].lower()
        input_path = os.path.join(tmp, 'inventory' + ext)
        with open(input_path, 'wb') as f:
            f.write(inventory_bytes)

        images_dir = os.path.join(tmp, 'images')
        image_notes = []
        try:
            if image_names is not None:
                os.makedirs(images_dir)
                if len(image_names) > MAX_NAMES:
                    raise engine.InputError(f'The folder has {len(image_names)} files; the limit is {MAX_NAMES}.')
                image_notes = create_placeholders([str(n) for n in image_names], images_dir)
            elif images_bytes is not None:
                os.makedirs(images_dir)
                if images_name.lower().endswith(NAME_LIST_EXTS):
                    image_notes = create_placeholders(read_name_list(images_bytes, images_name), images_dir)
                else:
                    image_notes = extract_images(images_bytes, images_dir)
            output_path = os.path.join(tmp, OUTPUT_NAME)
            # Capturing stdout also keeps the engine's emoji output away from the Windows console.
            with redirect_stdout(log):
                summary, out, findings, img_rows, code = engine.run(input_path, output_path, images_dir)
        except engine.InputError as e:
            message = str(e).replace(input_path, inventory_name)     # hide the temp path
            return RunResult(status='failed', exit_code=1, message=message, log=log.getvalue(), image_notes=image_notes)

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
                     log=log.getvalue(), image_notes=image_notes, files=files,
                     images_provided=image_names is not None or images_bytes is not None)


def bundle(files):
    """All output files in one ZIP (bytes)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()
