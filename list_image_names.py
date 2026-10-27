#!/usr/bin/env python3
"""
Save the names of all files in an images folder to one CSV file.

Upload that CSV in the Ninos upload tool (box 2) instead of a ZIP of all images:
the tool only needs the file names to match images and build the image URLs.

Usage:
    python list_image_names.py                         # pick the folder (and where to save) in a dialog
    python list_image_names.py <images_folder> [output.csv]

Output: a CSV with one column "filename", one row per file (e.g. "N557 2.jpg").
Files in sub-folders are included by name only, same as with a ZIP upload.
Needs only the Python standard library.
"""
import csv
import os
import sys

OUTPUT_NAME = "image_names.csv"
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}


def list_names(folder, skip=None):
    """All file names under `folder` (sub-folders included), sorted, junk files skipped."""
    names = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__MACOSX" and not d.startswith(".")]
        for f in files:
            path = os.path.join(root, f)
            if f in JUNK or f.startswith("._") or (skip and os.path.abspath(path) == skip):
                continue
            names.append(f)
    return sorted(names, key=str.casefold)


def write_csv(names, output):
    with open(output, "w", newline="", encoding="utf-8-sig") as fh:   # BOM so Excel shows names correctly
        w = csv.writer(fh)
        w.writerow(["filename"])
        w.writerows([n] for n in names)


def main(argv):
    gui = len(argv) == 0
    if gui:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Select the folder that contains the product images")
        if not folder:
            return 1
        output = filedialog.asksaveasfilename(
            title="Save image names as", initialfile=OUTPUT_NAME,
            initialdir=os.path.dirname(os.path.abspath(folder)),
            defaultextension=".csv", filetypes=[("CSV file", "*.csv")])
        if not output:
            return 1
    else:
        folder = argv[0]
        output = argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(folder)), OUTPUT_NAME)

    if not os.path.isdir(folder):
        print(f"Folder not found: {folder}")
        return 1
    names = list_names(folder, skip=os.path.abspath(output))
    write_csv(names, output)

    msg = f"Saved {len(names)} file name(s) from:\n{folder}\n\nto:\n{output}"
    if not names:
        msg = f"The folder has no files:\n{folder}\n\nAn empty list was saved to:\n{output}"
    print(msg)
    if gui:
        messagebox.showinfo("Image names saved", msg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
