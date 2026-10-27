#!/usr/bin/env python3
"""
Ninos product sorting engine: inventory (.xlsx / .csv) -> WooCommerce import CSV.

Usage (unchanged):
    python new_risk_file.py [input_file] [output_file] [images_dir]
    defaults: website_products_inventory.csv  woocommerce_import_new.csv  images

Input:   .xlsx (first sheet, header in row 1) or .csv. Needs pandas; .xlsx also needs openpyxl.
Outputs: <output_file>                 import CSV (valid products only)
         <output>_validation.csv       data-integrity findings (ERROR / WARNING / INFO)
         <output>_images.csv           image diagnostics
Exit code: 0 = clean, 2 = partial (some groups quarantined; CSV holds valid groups only),
           1 = fatal (unreadable input / failed structural self-check; no CSV written).

Processing rules (barcode layout, description parsing, naming, image naming, URL building,
aggregation) are those of the previous version of this script. What is new: file loading,
data-integrity validation with quarantine, a structural self-check, and image diagnostics.
"""
import os
import re
import sys
from collections import OrderedDict, defaultdict
from itertools import cycle
from urllib.parse import quote

import pandas as pd

BASE_URL = "https://api.ninosfootwear.com/wp-content/uploads/2026/07"

REQUIRED_COLUMNS = [
    'barcode*', 'Item name*', 'long Desc', 'unit of measurement*',
    'sell_price*(numbers)', 'item_category', 'Batch No*', 'total qty*(numbers)*',
]

OUTPUT_COLUMNS = [
    'ID', 'Type', 'SKU', 'Name', 'Published', 'Visibility in catalog', 'In stock?', 'Stock',
    'Regular price', 'Categories', 'Tags', 'Images', 'Parent',
    'Attribute 1 name', 'Attribute 1 value(s)', 'Attribute 1 visible', 'Attribute 1 global',
    'Attribute 2 name', 'Attribute 2 value(s)', 'Attribute 2 visible', 'Attribute 2 global',
    'Attribute 3 name', 'Attribute 3 value(s)', 'Attribute 3 visible', 'Attribute 3 global',
]

# ── Categories / names (unchanged) ───────────────────────────────────────────
CATEGORIES = [
    'Formal Shoe', 'Shoe Chu Chu', 'Heel Pump',
    'Shoe', 'Sketcher', 'Loafer', 'Sandal',
    'Pump', 'Peshawari', 'Crocs', 'Slipper'
]
cat_pattern = '|'.join(re.escape(c) for c in CATEGORIES)

ADJECTIVES = {
    'Shoe':         ['Classic', 'Casual', 'Everyday', 'Active', 'Comfy', 'Breezy', 'Fresh', 'Snappy', 'Easy', 'Cool', 'Trendy', 'Light', 'Sharp', 'Bold', 'Sleek'],
    'Sketcher':     ['Sporty', 'Active', 'Zippy', 'Bold', 'Energetic', 'Dynamic'],
    'Formal Shoe':  ['Elegant', 'Premium', 'Sharp', 'Polished', 'Refined', 'Distinguished'],
    'Loafer':       ['Sleek', 'Smart', 'Urban', 'Dapper', 'Suave', 'Smooth'],
    'Sandal':       ['Breezy', 'Summer', 'Light', 'Easy', 'Airy', 'Sunny', 'Relaxed', 'Casual', 'Open', 'Fresh'],
    'Pump':         ['Chic', 'Stylish', 'Pretty', 'Dainty', 'Lovely', 'Sweet'],
    'Heel Pump':    ['Glam', 'Graceful', 'Lovely', 'Radiant', 'Stunning'],
    'Shoe Chu Chu': ['Tiny', 'Adorable', 'Sweet', 'Cute', 'Precious'],
    'Peshawari':    ['Traditional', 'Heritage', 'Classic', 'Ethnic', 'Artisan'],
    'Crocs':        ['Fun', 'Playful', 'Bouncy', 'Breezy', 'Cheerful', 'Bubbly'],
    'Slipper':      ['Cozy', 'Soft', 'Comfy', 'Snug', 'Cushy', 'Plush'],
}
LIGHT_ORDER = ['No Light', 'Sole Light', 'Upper Light']

BARCODE_RE = re.compile(r'^[A-Za-z]\d{6,7}$')      # letter+3 digits (article), 1-2 digit color, 2 digit size
DESC_RE = re.compile(
    rf'^(Boys|Girls|NewBorn(?: Boys| Girls)?)\s+({cat_pattern})\s+(?:(Sole Light|Upper Light)\s+)?(.+?)\s+color\s+size\s+(\d+)$',
    re.IGNORECASE
)
IMAGE_EXTS = ('.jpg', '.jpeg', '.png')


class InputError(Exception):
    """Fatal, user-facing input problem."""


class NameGenerator:
    """Same behaviour as the previous generate_name(); state is per run."""

    def __init__(self):
        self.used_names = set()
        self.adj_cycles = {cat: cycle(adjs) for cat, adjs in ADJECTIVES.items()}

    def generate(self, gender, category):
        adjs = self.adj_cycles.get(category, cycle(['Classic']))
        for _ in range(50):
            name = f"{gender} {next(adjs)} {category}"
            if name not in self.used_names:
                self.used_names.add(name)
                return name
        return f"{gender} {category}"


# ── Barcode extraction (unchanged) ───────────────────────────────────────────
def extract_article(barcode):
    return str(barcode)[:4]


def extract_color_code(barcode):
    barcode = str(barcode)
    if len(barcode) == 7:
        return barcode[4:5]
    elif len(barcode) == 8:
        return barcode[4:6]
    return ""


def extract_size(barcode):
    try:
        return int(str(barcode)[-2:])
    except Exception:
        return None


def parse_desc(desc):
    match = DESC_RE.match(str(desc))
    if match:
        light_raw = match.group(3)
        return {
            'gender': match.group(1),
            'sub_type': match.group(2),
            'light_type': light_raw.title() if light_raw else 'No Light',
            'color': match.group(4),
            'desc_size': int(match.group(5)),
        }
    return {'gender': None, 'sub_type': None, 'light_type': None, 'color': None, 'desc_size': None}


def normalize_desc(desc):
    if not isinstance(desc, str):
        return desc
    desc = re.sub(r'\s+', ' ', desc).strip()
    return desc.replace('NewBornBoys', 'NewBorn Boys').replace('NewBornGirls', 'NewBorn Girls')


# ── Images (matching / URL logic unchanged) ──────────────────────────────────
def wp_sanitize_filename(fname):
    """Mimic WordPress's upload sanitization: strip parentheses, collapse spaces to hyphens."""
    name, ext = os.path.splitext(fname)
    name = name.replace('(', '').replace(')', '')
    name = re.sub(r'\s+', '-', name.strip())
    return f"{name}{ext}"


class ImageIndex:
    def __init__(self, images_dir):
        self.images_dir = images_dir
        self.folder_found = os.path.isdir(images_dir)
        if self.folder_found:
            self.existing_files = set(os.listdir(images_dir))
            print(f"Found {len(self.existing_files)} files in: {images_dir}")
        else:
            self.existing_files = set()
            print(f"⚠️  Images folder not found: {images_dir} — all image URLs will be blank")
        self.missing_images = set()

    def build_image_urls(self, article, color_code):
        base = f"{article} {color_code}"
        filenames = []
        first = f"{base}.jpg"
        if first in self.existing_files:
            filenames.append(first)
            n = 2
            while True:
                fname = f"{base} ({n}).jpg"
                if fname not in self.existing_files:
                    break
                filenames.append(fname)
                n += 1
        else:
            self.missing_images.add(base)
        return ', '.join(f"{BASE_URL}/{quote(wp_sanitize_filename(f))}" for f in filenames)


def _norm_base(text):
    return re.sub(r'\s+', ' ', text.strip()).casefold()


def diagnose_images(index, valid_bases, quarantined_bases):
    """Report-only. Never alters what ImageIndex.build_image_urls matches."""
    rows = []

    def add(sev, cat, item, article, detail, rec):
        rows.append(OrderedDict(severity=sev, category=cat, file_or_image=item, article=article,
                                detail=detail, recommended_action=rec))

    if not index.folder_found:
        add('INFO', 'FOLDER_NOT_FOUND', index.images_dir, '',
            'Image folder not found: no image matching was performed; all Images fields are blank.',
            'Re-run with the real image folder as the 3rd argument.')
        return rows

    valid_norm = {_norm_base(b): b for b in valid_bases}
    quar_norm = {_norm_base(b): b for b in quarantined_bases}
    primary, angles = set(), defaultdict(set)

    for name in sorted(index.existing_files):
        path = os.path.join(index.images_dir, name)
        if not os.path.isfile(path):
            add('INFO', 'NOT_A_FILE', name, '', 'Sub-folder; ignored.', 'None.')
            continue
        stem, ext = os.path.splitext(name)
        m = re.fullmatch(r'(.*?)(?: \((\d+)\))?', stem)
        base, n = m.group(1), (int(m.group(2)) if m.group(2) else None)
        if ext.lower() not in IMAGE_EXTS:
            add('INFO', 'NON_IMAGE_FILE', name, '', 'Not an image file; ignored.', 'None.')
            continue
        exact = ext == '.jpg' and (n is None or n >= 2)
        if exact and base in valid_bases:
            (primary.add(base) if n is None else angles[base].add(n))
            continue
        if exact and base in quarantined_bases:
            add('INFO', 'QUARANTINED_ARTICLE_IMAGE', name, base.split(' ')[0],
                'Image belongs to a quarantined (excluded) product group.', 'Resolve the group, then re-run.')
            continue
        m2 = re.fullmatch(r'\s*(.*?)\s*(?:\(\s*(\d+)\s*\))?\s*', stem)
        nb = _norm_base(m2.group(1))
        if nb in valid_norm:
            sug = valid_norm[nb] + (f" ({m2.group(2)})" if m2.group(2) else '') + '.jpg'
            if n == 1 and ext == '.jpg' and m.group(2):
                why = 'angle "(1)" is never used by the matcher'
            else:
                why = 'name differs from the expected pattern (case, spacing, extension or parentheses)'
            add('WARNING', 'NEAR_MISS_IGNORED', name, valid_norm[nb].split(' ')[0],
                f'{why}; the matcher ignores this file.', f'Rename to "{sug}" (or confirm it is intentionally unused).')
        elif nb in quar_norm:
            add('INFO', 'QUARANTINED_ARTICLE_IMAGE', name, quar_norm[nb].split(' ')[0],
                'Near-match for a quarantined (excluded) product group.', 'Resolve the group, then re-run.')
        else:
            add('WARNING', 'UNMATCHED_FILE', name, '',
                'No inventory article/color corresponds to this file.', 'Check for a typo or an unlisted product.')

    for base in sorted(valid_bases):
        art = base.split(' ')[0]
        angs = sorted(angles.get(base, ()))
        if base not in primary:
            add('WARNING', 'MISSING_IMAGE', f'{base}.jpg', art,
                'Expected primary image not found; product/variation Images will be blank.',
                f'Upload "{base}.jpg".')
            if angs:
                add('WARNING', 'ANGLES_WITHOUT_PRIMARY', base, art,
                    f'Angle(s) {angs} exist but are ignored because the primary image is missing.',
                    f'Upload "{base}.jpg".')
            continue
        if angs:
            expected = list(range(2, max(angs) + 1))
            gaps = [k for k in expected if k not in angs]
            if gaps:
                used = []
                k = 2
                while k in angs:
                    used.append(k)
                    k += 1
                dropped = [a for a in angs if a not in used]
                add('WARNING', 'ANGLE_GAP', base, art,
                    f'Angle(s) {gaps} missing between "(2)" and "({max(angs)})". '
                    f'Matcher uses {used or "none"} and ignores {dropped}.',
                    'Add the missing angle file(s) or renumber consecutively.')
    return rows


# ── Loading ──────────────────────────────────────────────────────────────────
def load_inventory(path):
    if not os.path.isfile(path):
        raise InputError(f"Input file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in ('.xlsx', '.xlsm'):
            df = pd.read_excel(path, dtype=str)           # first worksheet
        elif ext in ('.csv', '.txt'):
            df = pd.read_csv(path, dtype=str)
        else:
            raise InputError(f"Unsupported input type '{ext}'. Use .xlsx or .csv.")
    except InputError:
        raise
    except ImportError as e:
        raise InputError(f"Reading '{ext}' needs an extra package: {e}. Try: pip install openpyxl")
    except Exception as e:
        raise InputError(f"Could not read {path}: {e}")

    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise InputError(f"Input is missing required column(s): {missing}. Found: {list(df.columns)}")
    df['_row'] = df.index + 2                              # header is row 1 (Excel row / CSV line)
    for c in REQUIRED_COLUMNS:
        df[c] = df[c].apply(lambda v: (v.strip() or None) if isinstance(v, str) else v)
    df = df.dropna(how='all', subset=REQUIRED_COLUMNS).reset_index(drop=True)
    if df.empty:
        raise InputError("Input has no data rows.")
    return df


def parse_int(v):
    """-> (int, None) or (None, reason)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None, 'missing'
    try:
        f = float(str(v))
    except ValueError:
        return None, f'not a number ({v!r})'
    if f != f or f in (float('inf'), float('-inf')):
        return None, f'not a number ({v!r})'
    if f != int(f):
        return None, f'not a whole number ({v!r})'
    if f < 0:
        return None, f'negative ({v!r})'
    return int(f), None


def prepare(df):
    """Add parsed fields. Original text is kept; nothing is guessed."""
    w = pd.DataFrame({
        'row': df['_row'],
        'barcode': df['barcode*'],
        'desc': df['long Desc'].apply(normalize_desc),
        'category': df['item_category'],
        'price_raw': df['sell_price*(numbers)'],
        'qty_raw': df['total qty*(numbers)*'],
    })
    parsed = pd.DataFrame([parse_desc(d) for d in w['desc']], index=w.index)
    w = pd.concat([w, parsed], axis=1)
    has_bc = w['barcode'].notna()
    w['article'] = w['barcode'].apply(lambda b: extract_article(b) if isinstance(b, str) else None)
    w['color_code'] = w['barcode'].apply(lambda b: extract_color_code(b) if isinstance(b, str) else None)
    w['size'] = w['barcode'].apply(lambda b: extract_size(b) if isinstance(b, str) else None)
    w['barcode_ok'] = w['barcode'].apply(lambda b: bool(isinstance(b, str) and BARCODE_RE.match(b)))
    w['sell_price'] = [parse_int(v)[0] for v in w['price_raw']]
    w['total_qty'] = [parse_int(v)[0] for v in w['qty_raw']]
    w.loc[~has_bc, ['article', 'color_code']] = None
    return w


# ── Validation ───────────────────────────────────────────────────────────────
def fmt_ranges(nums):
    nums = sorted(set(int(n) for n in nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ', '.join(out)


def summarize(pairs):
    """[(row, value)] -> 'v1 (rows a-b) | v2 (rows c)'; grouped case-insensitively."""
    groups = OrderedDict()
    for r, v in pairs:
        groups.setdefault(str(v).casefold(), [str(v), []])[1].append(r)
    return ' | '.join(f"{disp} (rows {fmt_ranges(rs)})" for disp, rs in groups.values())


def validate(w, ignore_rules=()):
    """Returns (findings, quarantined_articles). ignore_rules is a test hook (never set by the CLI)."""
    findings, quarantined = [], {}

    def add(sev, rule, article, sub, conflict, rec, scope='group'):
        action = ''
        if sev == 'ERROR':
            if scope == 'row':
                action = 'ROW SKIPPED'
            elif rule in ignore_rules:
                action = 'NOT quarantined (rule ignored by test hook)'
            else:
                action = 'GROUP QUARANTINED'
                for a in ([article] if isinstance(article, str) else article):
                    quarantined.setdefault(a, set()).add(rule)
        elif sev == 'WARNING':
            action = 'kept (review)'
        findings.append(OrderedDict(
            severity=sev, rule=rule,
            article=article if isinstance(article, str) else ';'.join(sorted(article)),
            action=action,
            barcodes='; '.join(str(b) for b in sub['barcode'].tolist()) if sub is not None else '',
            source_rows=fmt_ranges(sub['row'].tolist()) if sub is not None and len(sub) else '',
            conflicting_values=conflict, recommended_action=rec))

    # missing barcode: cannot be assigned to any group
    for _, r in w[w['barcode'].isna()].iterrows():
        add('ERROR', 'MISSING_BARCODE', '(unknown)', w.loc[[r.name]], f'row {r["row"]} has no barcode',
            'Fill in the barcode in the source file.', scope='row')

    wb = w[w['barcode'].notna()]

    # row-level rules, aggregated per (rule, article)
    issues = defaultdict(list)                              # (rule, article) -> [(idx, detail)]
    for idx, r in wb.iterrows():
        a = r['article']
        if not r['barcode_ok']:
            issues[('BARCODE_FORMAT', a)].append((idx, f'{r["barcode"]!r}: expected letter + 6-7 digits (article 4 chars, color 1-2 digits, size 2 digits)'))
        if pd.isna(r['gender']):
            issues[('DESC_UNPARSED', a)].append((idx, f'{r["desc"]!r}'))
        elif not pd.isna(r['size']) and r['desc_size'] != r['size']:
            issues[('SIZE_MISMATCH', a)].append((idx, f'barcode size {int(r["size"])} vs description size {int(r["desc_size"])}'))
        if not isinstance(r['category'], str):
            issues[('CATEGORY_MISSING', a)].append((idx, 'item_category is blank'))
        elif not pd.isna(r['sub_type']) and r['sub_type'].casefold() != r['category'].casefold():
            issues[('CATEGORY_MISMATCH', a)].append((idx, f'description "{r["sub_type"]}" vs item_category "{r["category"]}"'))
        for fld, raw, rule in (('price', r['price_raw'], 'BAD_PRICE'), ('qty', r['qty_raw'], 'BAD_QTY')):
            val, why = parse_int(raw)
            if why:
                issues[(rule, a)].append((idx, f'{fld}: {why}'))
    recs = {
        'BARCODE_FORMAT': 'Correct the barcode in the source file.',
        'DESC_UNPARSED': 'Rewrite the description as "<Gender> <Category> [Sole Light|Upper Light] <color> color size <NN>".',
        'SIZE_MISMATCH': 'Make the barcode size and the description size agree.',
        'CATEGORY_MISSING': 'Fill in item_category.',
        'CATEGORY_MISMATCH': 'Make the description category and item_category agree.',
        'BAD_PRICE': 'Enter a whole-number sell price.',
        'BAD_QTY': 'Enter a whole-number, non-negative quantity.',
    }
    for (rule, a), items in issues.items():
        sub = w.loc[[i for i, _ in items]]
        add('ERROR', rule, a, sub, summarize([(w.at[i, 'row'], d) for i, d in items]), recs[rule])

    # duplicate barcodes (case-insensitive)
    key = wb['barcode'].str.casefold()
    for k in sorted(key[key.duplicated(keep=False)].unique()):
        sub = wb[key == k]
        add('ERROR', 'DUP_BARCODE', sub['article'].iloc[0], sub, f'barcode {sub["barcode"].iloc[0]} appears on rows {fmt_ranges(sub["row"])}',
            'Remove the duplicate row or correct the barcode.')

    # conflicting product identity inside one article
    parsed = wb[wb['gender'].notna()]
    for a, g in parsed.groupby('article'):
        for col, rule, label in (('gender', 'MIXED_GENDER', 'gender'),
                                 ('sub_type', 'MIXED_SUBTYPE', 'product type'),
                                 ('category', 'MIXED_CATEGORY', 'item_category')):
            vals = g[col].dropna()
            if vals.str.casefold().nunique() > 1:
                add('ERROR', rule, a, g, f'{label}: ' + summarize(list(zip(g['row'], g[col].fillna('(blank)')))),
                    f'One article number must have one {label}. Correct the barcodes/descriptions of the wrong rows in the source '
                    f'file (no automatic split is applied).')

    # color code <-> color name must be one-to-one across the file
    pc = parsed[parsed['barcode_ok']]
    code_names = pc.groupby('color_code')['color'].apply(lambda s: sorted(set(s.str.casefold())))
    name_codes = pc.groupby(pc['color'].str.casefold())['color_code'].apply(lambda s: sorted(set(s)))
    for code, names in code_names.items():
        if len(names) > 1:
            sub = pc[pc['color_code'] == code]
            add('ERROR', 'COLOR_CODE_MULTIPLE_NAMES', set(sub['article']), sub,
                f'color code {code}: ' + summarize(list(zip(sub['row'], sub['color']))),
                'Use one color name per color code, or correct the barcode color digits.')
    for name, codes in name_codes.items():
        if len(codes) > 1:
            sub = pc[pc['color'].str.casefold() == name]
            add('ERROR', 'COLOR_NAME_MULTIPLE_CODES', set(sub['article']), sub,
                f'color "{name}": ' + summarize(list(zip(sub['row'], sub['color_code'].map(lambda c: f'code {c}')))),
                'Use one color code per color name, or correct the description/barcode.')

    # warnings
    for (a, cc), g in pc.groupby(['article', 'color_code']):
        if g['color'].nunique() > 1 and g['color'].str.casefold().nunique() == 1:
            add('WARNING', 'COLOR_CASE_VARIANT', a, g, summarize(list(zip(g['row'], g['color']))), 'Use a single spelling/casing for the color.')
    for a, g in wb[wb['sell_price'].notna()].groupby('article'):
        if g['sell_price'].nunique() > 1:
            add('WARNING', 'PRICE_VARIES_IN_GROUP', a, g, summarize(list(zip(g['row'], g['sell_price']))),
                'Parent shows the lowest price; each variation keeps its own price. Confirm this is intended.')
    zero = wb[wb['sell_price'] == 0]
    for a, g in zero.groupby('article'):
        add('WARNING', 'PRICE_ZERO', a, g, 'sell price is 0', 'Confirm the price.')

    # info
    if len(pc):
        disp = pc.groupby('color_code')['color'].agg(lambda s: s.value_counts().index[0])
        table = '; '.join(f'{c}={n}' for c, n in sorted(disp.items(), key=lambda kv: (len(kv[0]), kv[0])))
        add('INFO', 'COLOR_TABLE', 'ALL', None, table, 'Review once; no authoritative color table exists, so none is enforced.')
        variants = pc.groupby(pc['color'].str.casefold())['color'].apply(lambda s: sorted(set(s)))
        mixed = {k: v for k, v in variants.items() if len(v) > 1}
        if mixed:
            add('INFO', 'COLOR_CASE_ACROSS_FILE', 'ALL', None, '; '.join('/'.join(v) for v in mixed.values()),
                'Same color spelled with different casing in different articles; values are kept as written.')
    return findings, quarantined


# ── Build WooCommerce rows (logic unchanged) ─────────────────────────────────
def build_rows(valid, images, namer):
    rows = []
    for article, group in valid.groupby('article'):
        gender = group['gender'].iloc[0] or ''
        sub_type = group['sub_type'].iloc[0] or ''
        category = group['category'].iloc[0] or ''
        total_qty = group['total_qty'].sum()
        in_stock = 1 if total_qty > 0 else 0
        all_colors = ', '.join(sorted(group['color'].dropna().unique()))
        all_light_types = ', '.join(sorted(
            group['light_type'].dropna().unique(),
            key=lambda x: LIGHT_ORDER.index(x) if x in LIGHT_ORDER else 99))
        all_sizes = ', '.join(str(int(x)) for x in sorted(group['size'].dropna().unique()))
        prices = sorted(group['sell_price'].dropna().unique())
        regular_price = int(prices[0]) if prices else ''
        product_name = namer.generate(gender, sub_type)
        woo_category = f"{gender} > {category}"
        # empty per-color results (missing images) are dropped so no stray ", , ," is written
        images_all = ', '.join(u for u in (images.build_image_urls(article, c) for c in group['color_code'].unique()) if u)

        rows.append({
            'ID': '', 'Type': 'variable', 'SKU': article, 'Name': product_name,
            'Published': 1, 'Visibility in catalog': 'visible',
            'In stock?': in_stock, 'Stock': int(total_qty), 'Regular price': regular_price,
            'Categories': woo_category, 'Tags': gender, 'Images': images_all, 'Parent': '',
            'Attribute 1 name': 'Color', 'Attribute 1 value(s)': all_colors, 'Attribute 1 visible': 1, 'Attribute 1 global': 1,
            'Attribute 2 name': 'Size', 'Attribute 2 value(s)': all_sizes, 'Attribute 2 visible': 1, 'Attribute 2 global': 1,
            'Attribute 3 name': 'Light Type', 'Attribute 3 value(s)': all_light_types, 'Attribute 3 visible': 1, 'Attribute 3 global': 1,
        })
        for _, var in group.iterrows():
            qty = int(var['total_qty'])
            rows.append({
                'ID': '', 'Type': 'variation', 'SKU': str(var['barcode']), 'Name': product_name,
                'Published': 1, 'Visibility in catalog': 'visible',
                'In stock?': 1 if qty > 0 else 0, 'Stock': qty, 'Regular price': int(var['sell_price']),
                'Categories': woo_category, 'Tags': gender,
                'Images': images.build_image_urls(article, var['color_code']), 'Parent': article,
                'Attribute 1 name': 'Color', 'Attribute 1 value(s)': var['color'], 'Attribute 1 visible': 1, 'Attribute 1 global': 1,
                'Attribute 2 name': 'Size', 'Attribute 2 value(s)': str(int(var['size'])), 'Attribute 2 visible': 1, 'Attribute 2 global': 1,
                'Attribute 3 name': 'Light Type', 'Attribute 3 value(s)': var['light_type'], 'Attribute 3 visible': 1, 'Attribute 3 global': 1,
            })
    return rows


def self_check(out, valid):
    """Structural integrity of the generated table. Returns a list of problems (empty = OK)."""
    p = []
    if list(out.columns) != OUTPUT_COLUMNS:
        p.append('output columns/order differ from the reference schema')
    if out['SKU'].duplicated().any():
        p.append(f"duplicate SKUs: {out.loc[out['SKU'].duplicated(), 'SKU'].tolist()[:5]}")
    parents = out[out['Type'] == 'variable'].set_index('SKU')
    vars_ = out[out['Type'] == 'variation']
    if not set(vars_['Parent']) <= set(parents.index):
        p.append(f"orphaned variations (unknown Parent): {sorted(set(vars_['Parent']) - set(parents.index))[:5]}")
    if not set(parents.index) <= set(vars_['Parent']):
        p.append(f"parents without variations: {sorted(set(parents.index) - set(vars_['Parent']))[:5]}")
    if len(vars_) != len(valid):
        p.append(f'variation count {len(vars_)} != valid source rows {len(valid)}')
    src = valid.set_index('barcode')
    for _, v in vars_.iterrows():
        if not v['SKU'].startswith(v['Parent']):
            p.append(f"variation {v['SKU']} does not belong to parent {v['Parent']}")
        s = src.loc[v['SKU']] if v['SKU'] in src.index else None
        if s is None:
            p.append(f"variation {v['SKU']} not in source")
            continue
        if (v['Attribute 1 value(s)'], v['Attribute 2 value(s)'], v['Attribute 3 value(s)']) != (s['color'], str(int(s['size'])), s['light_type']):
            p.append(f"variation {v['SKU']} attributes differ from source")
        if v['Stock'] != s['total_qty'] or v['Regular price'] != s['sell_price']:
            p.append(f"variation {v['SKU']} stock/price differ from source")
        if v['Parent'] not in parents.index:
            continue                                        # already reported as orphaned
        par = parents.loc[v['Parent']]
        if (v['Name'], v['Categories'], v['Tags']) != (par['Name'], par['Categories'], par['Tags']):
            p.append(f"variation {v['SKU']} name/category/tags differ from parent")
    for sku, par in parents.iterrows():
        kids = vars_[vars_['Parent'] == sku]
        if len(kids) == 0:
            continue
        if par['Stock'] != kids['Stock'].sum() or par['Regular price'] != kids['Regular price'].min():
            p.append(f'parent {sku} stock/price are not the aggregate of its variations')
        if set(par['Attribute 1 value(s)'].split(', ')) != set(kids['Attribute 1 value(s)']):
            p.append(f'parent {sku} colors differ from its variations')
        if set(par['Attribute 2 value(s)'].split(', ')) != set(kids['Attribute 2 value(s)']):
            p.append(f'parent {sku} sizes differ from its variations')
    return p


# ── Run ──────────────────────────────────────────────────────────────────────
def report_paths(output_file):
    stem = os.path.splitext(output_file)[0]
    return stem + '_validation.csv', stem + '_images.csv'


def run(input_file, output_file, images_dir, ignore_rules=()):
    """Full pipeline. ignore_rules is a test hook that disables quarantine for named rules."""
    images = ImageIndex(images_dir)
    print(f"Reading: {input_file}")
    df = load_inventory(input_file)
    w = prepare(df)

    findings, quarantined = validate(w, ignore_rules)
    qset = set(quarantined)
    usable = w[w['barcode'].notna() & ~w['article'].isin(qset)]
    valid = usable.sort_index()                             # row order preserved; groupby sorts articles

    rows = build_rows(valid, images, NameGenerator())
    out = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    problems = self_check(out, valid)

    quar_rows = w[w['barcode'].notna() & w['article'].isin(qset)]
    q_bases = {f"{r['article']} {r['color_code']}" for _, r in quar_rows.iterrows() if len(str(r['barcode'])) in (7, 8)}
    v_bases = {f"{r['article']} {r['color_code']}" for _, r in valid.iterrows()}
    img_rows = diagnose_images(images, v_bases, q_bases)

    val_path, img_path = report_paths(output_file)
    cols = ['severity', 'rule', 'article', 'action', 'barcodes', 'source_rows', 'conflicting_values', 'recommended_action']
    sev_rank = {'ERROR': 0, 'WARNING': 1, 'INFO': 2}
    ordered = sorted(findings, key=lambda f: (sev_rank[f['severity']], f['article'], f['rule']))
    pd.DataFrame(ordered, columns=cols).to_csv(val_path, index=False)
    pd.DataFrame(img_rows, columns=['severity', 'category', 'file_or_image', 'article', 'detail', 'recommended_action']).to_csv(img_path, index=False)

    summary = OrderedDict(
        source_rows=len(w), source_articles=int(w['article'].nunique()),
        quarantined_articles=sorted(qset), quarantined_rows=len(quar_rows),
        skipped_rows=int(w['barcode'].isna().sum()),
        valid_parents=int((out['Type'] == 'variable').sum()), valid_variations=int((out['Type'] == 'variation').sum()),
        errors=sum(f['severity'] == 'ERROR' for f in findings), warnings=sum(f['severity'] == 'WARNING' for f in findings),
        missing_images=sorted(images.missing_images), self_check_problems=problems)

    if problems:
        print("❌ Structural self-check FAILED — no import CSV written:")
        for x in problems[:20]:
            print(f"   - {x}")
        return summary, out, findings, img_rows, 1
    try:
        out.to_csv(output_file, index=False)
    except OSError as e:
        raise InputError(f"Could not write {output_file}: {e}")

    partial = bool(qset) or summary['skipped_rows'] > 0
    print(f"\nSource: {summary['source_rows']} rows in {summary['source_articles']} article group(s)")
    if partial:
        print(f"⚠️  PARTIAL RESULT — NOT a complete import. Quarantined {len(qset)} group(s) "
              f"({summary['quarantined_rows']} rows): {', '.join(sorted(qset))}; skipped rows: {summary['skipped_rows']}")
        for a in sorted(qset):
            print(f"   - {a}: {', '.join(sorted(quarantined[a]))}")
        print(f"   Review {val_path} before importing.")
    else:
        print("✅ Data-integrity checks passed: no groups quarantined.")
    print(f"✅ {len(out)} rows saved to {output_file}")
    print(f"   Parent products: {summary['valid_parents']}")
    print(f"   Variations: {summary['valid_variations']}")
    print(f"   Warnings: {summary['warnings']} (see {val_path})")
    if not images.folder_found:
        print("   Images: folder not provided — image fields are blank and UNVERIFIED.")
    else:
        if images.missing_images:
            print(f"⚠️  No images found for {len(images.missing_images)} article-color combo(s):")
            for base in sorted(images.missing_images):
                print(f"   - {base}.jpg")
        n_img = sum(1 for r in img_rows if r['severity'] == 'WARNING')
        print(f"   Image diagnostics: {n_img} warning(s) (see {img_path})")
    return summary, out, findings, img_rows, (2 if partial else 0)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    input_file = argv[0] if len(argv) > 0 else "website_products_inventory.csv"
    output_file = argv[1] if len(argv) > 1 else "woocommerce_import_new.csv"
    images_dir = argv[2] if len(argv) > 2 else "images"
    try:
        return run(input_file, output_file, images_dir)[4]
    except InputError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
