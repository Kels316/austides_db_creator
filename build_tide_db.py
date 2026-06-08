#!/usr/bin/env python3
"""
build_tide_db.py
─────────────────────────────────────────────────────────────────────
Builds tide-db.js from the ANTT 2026 PDF.

Sources:
  - Primary port tide predictions  (PDF pages 1–237)
  - Secondary port tidal datums    (PDF pages 280–319)

Output: tide-db.js (same directory)

Usage:
  python3 build_tide_db.py [path/to/ANTT_2026.pdf]

Dependencies:
  pip install pypdf --break-system-packages

If no PDF path is given, looks for 'All_data_2026_aus copy.pdf' in the
same directory as this script.

Timestamp format: minutes since 2026-01-01 00:00 UTC (compact integer).
─────────────────────────────────────────────────────────────────────
"""

import sys, json, re, math, os
from datetime import date, timedelta, datetime, timezone
from pypdf import PdfReader

# ─── Config ──────────────────────────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PDF   = os.path.join(SCRIPT_DIR, 'All_data_2026_aus copy.pdf')
PDF_PATH      = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
EPOCH         = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
EPOCH_MS      = int(EPOCH.timestamp() * 1000)

# Only include ports within (roughly) the Australian region
AUS_LAT_RANGE = (-48, -9)     # south to north
AUS_LON_RANGE = (112, 154)    # west to east (excluding external territories)

def in_aus_region(lat, lon):
    return AUS_LAT_RANGE[0] <= lat <= AUS_LAT_RANGE[1] and \
           AUS_LON_RANGE[0] <= lon <= AUS_LON_RANGE[1]

if not os.path.exists(PDF_PATH):
    print(f"ERROR: PDF not found at {PDF_PATH}")
    print(f"Pass the PDF path as an argument: python3 build_tide_db.py /path/to/ANTT_2026.pdf")
    sys.exit(1)

print(f"Reading: {PDF_PATH}")
r = PdfReader(PDF_PATH)
print(f"Pages: {len(r.pages)}\n")

# ─── Helpers ─────────────────────────────────────────────────────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    R  = 6371.0
    d  = math.radians
    dl = d(lat2 - lat1)
    dm = d(lon2 - lon1)
    a  = math.sin(dl/2)**2 + math.cos(d(lat1))*math.cos(d(lat2))*math.sin(dm/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def to_min_offset(ts_ms):
    """Convert UTC millisecond timestamp to minutes since EPOCH."""
    return (ts_ms - EPOCH_MS) // 60000

def clean_name(raw):
    """Remove state prefix, column-header bleed-in, tidy whitespace."""
    s = str(raw).strip('. \t\n')
    # Remove column header text that bleeds into port names
    s = re.sub(r'\b(MHWS|MHWN|MLWN|MLWS|MSL)\s*', '', s)
    # Remove state prefixes
    prefixes = (
        r'^(QUEENSLAND|NEW SOUTH WALES|VICTORIA|SOUTH AUSTRALIA|'
        r'WESTERN AUSTRALIA|NORTHERN TERRITORY|TASMANIA|'
        r'EXTERNAL TERRITORIES)\s*-\s*'
    )
    s = re.sub(prefixes, '', s)
    s = re.sub(r'^(AUSTRALIA\s*[-,]\s*)', '', s)
    s = re.sub(r'^\d+', '', s)           # leading digits from ID bleed
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def title_case(s):
    """Title-case a string, keeping common abbreviations."""
    keep = {'(', ')', '-', "'"}
    words = s.split()
    result = []
    for w in words:
        if w in ('OF', 'THE', 'AND', 'AT', 'IN', 'ON', 'DE') and result:
            result.append(w.lower())
        else:
            result.append(w.capitalize())
    return ' '.join(result)

# ─── 1. Parse primary port tide predictions ───────────────────────────────────

Y2026_STARTS = [date(2026, 1, 1), date(2026, 5, 1), date(2026, 9, 1)]

def parse_port_header(text):
    """Return (name, lat, lon, tz_offset_min)."""
    m = re.match(r'^(.*?)TIME ZONE', text.replace('\n', ' '), re.DOTALL)
    raw = m.group(1) if m else ''
    raw = re.sub(r'\x00', '', raw)
    raw = re.sub(r'^\d+', '', raw.strip())
    raw = re.sub(r'^AUSTRALIA,\s*', '', raw.strip())
    name = title_case(clean_name(raw))

    loc = re.search(
        r'LAT\s+(\d+)[\x00°\s]+(\d+)[^\dSN]*([SN])\s+LONG\s+(\d+)[\x00°\s]+(\d+)[^\dEW]*([EW])',
        text)
    lat = lon = None
    if loc:
        lat = round(int(loc.group(1)) + int(loc.group(2))/60, 4)
        if loc.group(3) == 'S': lat = -lat
        lon = round(int(loc.group(4)) + int(loc.group(5))/60, 4)
        if loc.group(6) == 'W': lon = -lon

    tz = re.search(r'TIME ZONE\s*[-]?\s*(\d{4})', text)
    tz_str = tz.group(1) if tz else '1000'
    tz_min = int(tz_str[:2]) * 60 + int(tz_str[2:])
    return name, lat, lon, tz_min

ENTRY_RE = re.compile(r'(?<!\d)(\d{4})\s+(\d+\.\d+)\s*(TH|FR|SA|SU|MO|TU|WE)?')

def parse_tide_predictions(pages_text, tz_offset_min):
    """
    Parse 3 pages of tide data (4 months each) for one port.
    Returns list of (ts_ms_utc, height_m) — HW/LW classified later.
    """
    results = []
    for text, start_date in zip(pages_text, Y2026_STARTS):
        current = start_date
        for m in ENTRY_RE.finditer(text):
            hhmm = m.group(1)
            h    = float(m.group(2))
            dow  = m.group(3)
            hh   = int(hhmm[:2])
            mm   = int(hhmm[2:])
            if hh > 23 or mm > 59 or h > 20 or h < 0:
                continue
            # Local→UTC
            base_dt = datetime(current.year, current.month, current.day,
                               tzinfo=timezone.utc)
            ts_ms = int(base_dt.timestamp() * 1000) + (hh * 60 + mm - tz_offset_min) * 60000
            results.append((ts_ms, h))
            if dow:
                current += timedelta(days=1)
    return results

def classify_hw_lw(entries):
    """Tag each entry as HW (1) or LW (0) by local min/max."""
    if not entries: return []
    n = len(entries)
    out = []
    for i, (ts, h) in enumerate(entries):
        ph = entries[i-1][1] if i > 0 else h - 1
        nh = entries[i+1][1] if i < n-1 else h - 1
        out.append((ts, h, 1 if (h >= ph and h >= nh) else 0))
    return out

print("=== Parsing primary port predictions ===")
primary_port_data = {}   # pid -> {name, lat, lon, mhws, mlws, entries}

for i, page in enumerate(r.pages):
    text = page.extract_text() or ''
    if 'JANUARY' not in text or 'TIMES AND HEIGHTS' not in text:
        continue

    name, lat, lon, tz_min = parse_port_header(text)
    if lat is None or lon is None or len(name) < 3:
        continue
    if not in_aus_region(lat, lon):
        continue   # skip PNG, Solomons, etc.

    pages_text  = [r.pages[j].extract_text() or '' for j in range(i, min(i+3, len(r.pages)))]
    raw_entries = parse_tide_predictions(pages_text, tz_min)
    if len(raw_entries) < 100:
        continue

    raw_entries.sort(key=lambda x: x[0])
    classified = classify_hw_lw(raw_entries)

    pid = 'p_' + re.sub(r'[^a-z0-9]', '_', name.lower())[:40]
    while pid in primary_port_data:
        pid += '_x'

    primary_port_data[pid] = {
        'name': name, 'lat': lat, 'lon': lon,
        'mhws': None, 'mlws': None,
        'entries': classified,
    }
    print(f"  {name[:52]:<52}  {len(classified):>5}  ({lat:.2f}, {lon:.2f})")

print(f"\nTotal primary ports: {len(primary_port_data)}\n")

# ─── 2. Parse secondary port tidal datum tables + time offsets ───────────────

print("=== Parsing secondary port tables ===")

SEC_RE = re.compile(
    r'([A-Z][A-Z\s&\.\(\)\-\'/,]+?)(\d{4,6})\s*'
    r'(\d{2})[\x00°\s]+(\d{2})[´\']\s*([SN])\s+'
    r'(\d{2,3})[\x00°\s]+(\d{2})[´\']\s*([EW])\s+'
    r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+'
    r'(NA|UNK|[-\d.]+)'
)
# Time offset column at bottom of each page: ±HHMM followed by HAT float
# e.g. -0010   2.3   or  +0006   1.4
TIME_OFF_RE = re.compile(r'([+-])(\d{2})(\d{2})\s+([\d.]+)')
JUNK_NAME_RE = re.compile(r'^(MSL|MHHW|MHLW|MLHW|MLLW|\d)', re.I)

secondary_entries = []   # final list: each entry has 'time_offset_min' filled in

# Auto-detect datum table pages — must contain MHWS/MLWS column headers AND
# at least one port entry with a station ID (5–6 digit number) and coordinates.
# This excludes front-matter/glossary pages that mention MHWS in prose.
_DATUM_PORT_RE = re.compile(r'\d{5,6}\s+\d{2}[^\d]+\d{2}[´\']')
datum_pages = [
    i for i in range(len(r.pages))
    if all(k in (r.pages[i].extract_text() or '') for k in ('MHWS', 'MLWS', 'MSL'))
    and _DATUM_PORT_RE.search(r.pages[i].extract_text() or '')
]
if not datum_pages:
    print("ERROR: Could not find secondary port datum tables in PDF")
    sys.exit(1)
print(f"Datum table pages detected: {datum_pages[0]}–{datum_pages[-1]} ({len(datum_pages)} pages)")

for page_idx in datum_pages:
    text = (r.pages[page_idx].extract_text() or '').replace('\n', ' ')

    # ── a. Collect standard ports and secondary ports on this page, in order ──
    page_standards  = []   # (lat, lon, mhws, mlws) — for reference assignment
    page_secondaries = []  # entries without time offset yet

    for m in SEC_RE.finditer(text):
        raw_name = m.group(1).strip('. \t')
        name     = clean_name(raw_name)
        if len(name) < 3 or JUNK_NAME_RE.match(name):
            continue
        if re.search(r'\b(MHWS|MHWN|MLWN|MLWS)\b', name):
            continue

        lat = round(int(m.group(3)) + int(m.group(4))/60, 4)
        if m.group(5) == 'S': lat = -lat
        lon = round(int(m.group(6)) + int(m.group(7))/60, 4)
        if m.group(8) == 'W': lon = -lon
        if not in_aus_region(lat, lon):
            continue

        mhws = float(m.group(9));  mlws = float(m.group(13))
        flag = m.group(14)

        msl = float(m.group(11))
        if flag == 'NA':
            page_standards.append({'lat': lat, 'lon': lon, 'mhws': mhws, 'msl': msl, 'mlws': mlws})
        else:
            page_secondaries.append({
                'name':  title_case(name),
                'lat': lat, 'lon': lon,
                'mhws': mhws, 'mhwn': float(m.group(10)),
                'msl':  msl,
                'mlwn': float(m.group(12)), 'mlws': mlws,
                'time_offset_min': 0,   # filled in below
                'ref_lat': None, 'ref_lon': None,
            })

    # ── b. Extract time offsets from the page footer (one per secondary port) ─
    time_offsets = []
    for m in TIME_OFF_RE.finditer(text):
        sign = m.group(1)
        hh   = int(m.group(2))
        mm   = int(m.group(3))
        offset_min = (hh * 60 + mm) * (1 if sign == '+' else -1)
        time_offsets.append(offset_min)

    # ── c. Pair time offsets with secondary ports ─────────────────────────────
    # Counts should match; if not, use what we have and default rest to 0
    for i, entry in enumerate(page_secondaries):
        if i < len(time_offsets):
            entry['time_offset_min'] = time_offsets[i]

    # ── d. Assign each secondary to nearest standard port on this page ─────────
    for entry in page_secondaries:
        if page_standards:
            nearest_std = min(page_standards,
                key=lambda s: haversine_km(entry['lat'], entry['lon'], s['lat'], s['lon']))
            entry['ref_lat'] = nearest_std['lat']
            entry['ref_lon'] = nearest_std['lon']
            entry['ref_mhws'] = nearest_std['mhws']
            entry['ref_msl']  = nearest_std['msl']
            entry['ref_mlws'] = nearest_std['mlws']

    secondary_entries.extend(page_secondaries)

    # ── e. Fill mhws/mlws into primary_port_data ─────────────────────────────
    for std in page_standards:
        best_pid, best_d = None, 999
        for pid, pd in primary_port_data.items():
            d = haversine_km(pd['lat'], pd['lon'], std['lat'], std['lon'])
            if d < best_d:
                best_d = d;  best_pid = pid
        if best_d < 10 and best_pid:
            if not primary_port_data[best_pid].get('mhws'):
                primary_port_data[best_pid]['mhws'] = std['mhws']
                primary_port_data[best_pid]['msl']  = std['msl']
                primary_port_data[best_pid]['mlws'] = std['mlws']

print(f"Secondary table entries (Australian): {len(secondary_entries)}")

# Build primary list for reference lookup (by any lat/lon, not just page standards)
primary_list = [
    (pid, pd['lat'], pd['lon'], pd.get('mhws'), pd.get('msl'), pd.get('mlws'))
    for pid, pd in primary_port_data.items()
    if pd.get('mhws') and pd.get('mlws')
]

def find_ref_primary(lat, lon):
    """Return (pid, mhws, msl, mlws, dist_km) for nearest primary port with known datums."""
    best_pid, best_d, best_mhws, best_msl, best_mlws = None, 9999, None, None, None
    for pid, plat, plon, mhws, msl, mlws in primary_list:
        d = haversine_km(lat, lon, plat, plon)
        if d < best_d:
            best_d = d;  best_pid = pid;  best_mhws = mhws;  best_msl = msl;  best_mlws = mlws
    return best_pid, best_mhws, best_msl, best_mlws, best_d

def find_ref_primary_by_coords(ref_lat, ref_lon):
    """Find the primary port closest to the given reference coordinates."""
    best_pid, best_d = None, 9999
    for pid, plat, plon, mhws, msl, mlws in primary_list:
        d = haversine_km(ref_lat, ref_lon, plat, plon)
        if d < best_d:
            best_d = d;  best_pid = pid
    return best_pid, best_d

# ─── 3. Build secondary port predictions ────────────────────────────────────

print("=== Building secondary port predictions ===")
secondary_port_data = {}
skipped = 0

for entry in secondary_entries:
    name = entry['name']
    lat, lon = entry['lat'], entry['lon']
    sec_mhws, sec_mlws = entry['mhws'], entry['mlws']
    if sec_mhws <= 0 or sec_mlws <= 0:
        skipped += 1;  continue

    # Prefer reference port identified from same page; fall back to nearest primary
    ref_pid = None
    ref_mhws = ref_msl = ref_mlws = None
    dist_km = 9999

    if entry.get('ref_lat') is not None:
        ref_pid, dist_km = find_ref_primary_by_coords(entry['ref_lat'], entry['ref_lon'])
        if ref_pid:
            ref_mhws = entry.get('ref_mhws')
            ref_msl  = entry.get('ref_msl')
            ref_mlws = entry.get('ref_mlws')
            # Fall back to primary_port_data if page datums missing
            if not ref_mhws:
                ref_mhws = primary_port_data[ref_pid].get('mhws')
                ref_msl  = primary_port_data[ref_pid].get('msl')
                ref_mlws = primary_port_data[ref_pid].get('mlws')

    if not ref_pid or not ref_mhws or not ref_mlws:
        ref_pid, ref_mhws, ref_msl, ref_mlws, dist_km = find_ref_primary(lat, lon)

    if not ref_pid or dist_km > 500 or not ref_mhws or not ref_mlws:
        skipped += 1;  continue

    # ANTT official method:
    #   range_ratio = (sec_mhws - sec_mlws) / (std_mhws - std_mlws)
    #   secondary_height = (std_height - std_msl) × range_ratio + sec_msl
    # Fall back to separate HW/LW factors if MSL missing
    sec_range = sec_mhws - sec_mlws
    ref_range = ref_mhws - ref_mlws
    sec_msl   = entry['msl']

    if ref_msl and ref_range > 0 and sec_range > 0:
        range_ratio = sec_range / ref_range
        use_antt = True
    else:
        # Legacy fallback
        hw_factor = round(sec_mhws / ref_mhws, 4) if ref_mhws else 1.0
        lw_factor = round(sec_mlws / ref_mlws, 4) if ref_mlws else 1.0
        use_antt = False

    time_offset_ms = entry.get('time_offset_min', 0) * 60000

    ref_entries = primary_port_data[ref_pid]['entries']
    if use_antt:
        scaled = [
            [ts + time_offset_ms,
             round((h - ref_msl) * range_ratio + sec_msl, 2),
             isHW]
            for ts, h, isHW in ref_entries
        ]
        hw_factor = round(range_ratio, 4)
        lw_factor = round(range_ratio, 4)
    else:
        scaled = [
            [ts + time_offset_ms,
             round(h * (hw_factor if isHW else lw_factor), 2),
             isHW]
            for ts, h, isHW in ref_entries
        ]

    sid = 's_' + re.sub(r'[^a-z0-9]', '_', name.lower())[:40]
    while sid in secondary_port_data or sid in primary_port_data:
        sid += '_x'

    secondary_port_data[sid] = {
        'name': name, 'lat': lat, 'lon': lon,
        'ref': ref_pid, 'ref_dist_km': round(dist_km, 1),
        'hw_factor': hw_factor, 'lw_factor': lw_factor,
        'range_ratio': round(range_ratio if use_antt else 0, 4),
        'sec_msl': sec_msl, 'ref_msl': ref_msl,
        'time_offset_min': entry.get('time_offset_min', 0),
        'entries': scaled,
    }

print(f"Secondary ports built: {len(secondary_port_data)}  (skipped: {skipped})")

# ─── 4. Assemble all stations ────────────────────────────────────────────────

print("\n=== Assembling stations ===")
all_stations = {}

for pid, pd in primary_port_data.items():
    # Store extremes as [minutes_offset, height_2dp, isHW]
    extremes = [
        [to_min_offset(ts), round(h, 2), isHW]
        for ts, h, isHW in pd['entries']
    ]
    all_stations[pid] = {
        'name': pd['name'], 'lat': pd['lat'], 'lon': pd['lon'],
        'type': 'primary',
        'extremes': extremes,
    }

for sid, sd in secondary_port_data.items():
    extremes = [
        [to_min_offset(ts), h, isHW]
        for ts, h, isHW in sd['entries']
    ]
    all_stations[sid] = {
        'name': sd['name'], 'lat': sd['lat'], 'lon': sd['lon'],
        'type': 'secondary',
        'ref': sd['ref'],
        'ref_dist_km': sd['ref_dist_km'],
        'hw_factor': sd['hw_factor'],
        'lw_factor': sd['lw_factor'],
        'time_offset_min': sd.get('time_offset_min', 0),
        'extremes': extremes,
    }

# ─── 5. Map the 39 coastal bars to nearest station ───────────────────────────

COASTAL_BARS = [
    ('Noosa Bar',           -26.382, 153.098),
    ('Mooloolaba Bar',      -26.684, 153.143),
    ('Caloundra Bar',       -26.807, 153.148),
    ('Southport Seaway',    -27.952, 153.430),
    ('Jumpinpin Bar',       -27.853, 153.426),
    ('Brunswick Bar',       -28.541, 153.551),
    ('Ballina Bar',         -28.866, 153.577),
    ('Evans Head Bar',      -29.119, 153.438),
    ('Yamba Bar',           -29.433, 153.362),
    ('Bundaberg Bar',       -24.763, 152.387),
    ('Gladstone Bar',       -23.843, 151.266),
    ('Mackay Bar',          -21.149, 149.201),
    ('Bowen Bar',           -20.013, 148.250),
    ('Townsville Bar',      -19.257, 146.818),
    ('Cairns Bar',          -16.924, 145.777),
    ('Rainbow Beach Bar',   -25.904, 153.092),
    ('Coffs Harbour Bar',   -30.303, 153.140),
    ('Port Macquarie Bar',  -31.433, 152.921),
    ('Crowdy Head Bar',     -31.834, 152.741),
    ('Forster Bar',         -32.178, 152.516),
    ('Newcastle Bar',       -32.925, 151.798),
    ('Swansea Bar',         -33.088, 151.641),
    ('Sydney Heads',        -33.835, 151.281),
    ('Port Kembla Bar',     -34.474, 150.901),
    ('Ulladulla Bar',       -35.354, 150.472),
    ('Batemans Bay Bar',    -35.710, 150.178),
    ('Eden Bar',            -37.068, 149.907),
    ('Lakes Entrance Bar',  -37.879, 147.980),
    ('Port Phillip Heads',  -38.308, 144.617),
    ('Port Adelaide Bar',   -34.808, 138.502),
    ('Fremantle Bar',       -32.051, 115.745),
    ('Mandurah Bar',        -32.530, 115.727),
    ('Bunbury Bar',         -33.327, 115.636),
    ('Albany Bar',          -34.953, 117.895),
    ('Geraldton Bar',       -28.777, 114.612),
    ('Carnarvon Bar',       -24.871, 113.665),
    ('Port Hedland Bar',    -20.313, 118.571),
    ('Broome Bar',          -17.954, 122.236),
    ('Darwin Bar',          -12.455, 130.844),
]

bar_to_station = {}
print("\nBar → Station mapping:")
for bar_name, blat, blon in COASTAL_BARS:
    best_sid, best_d = None, 9999
    for sid, sd in all_stations.items():
        d = haversine_km(blat, blon, sd['lat'], sd['lon'])
        if d < best_d:
            best_d = d;  best_sid = sid
    if best_sid:
        bar_to_station[bar_name] = best_sid
        t = all_stations[best_sid]['type'][0].upper()
        print(f"  {bar_name:<25} [{t}] → {all_stations[best_sid]['name'][:38]:<38} ({best_d:.1f} km)")

# ─── 6. Write tide-db.js ─────────────────────────────────────────────────────

print("\n=== Writing tide-db.js ===")

db = {
    'generated':   datetime.utcnow().isoformat() + 'Z',
    'valid_until': '2026-12-31T23:59:59Z',
    'epoch_ms':    EPOCH_MS,           # needed to convert min_offset back to ms
    'source':      'ANTT 2026 — Australian Hydrographic Office',
    'stations':    all_stations,
    'barToStation': bar_to_station,
}

out_path = os.path.join(SCRIPT_DIR, 'tide-db.js')
gen_str  = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

js = (
    f"// tide-db.js — Marine SAR tide database\n"
    f"// Generated : {gen_str}\n"
    f"// Valid until: 2026-12-31\n"
    f"// Source    : ANTT 2026 — Australian Hydrographic Office\n"
    f"// Primary stations : {len(primary_port_data)}\n"
    f"// Secondary stations: {len(secondary_port_data)}\n"
    f"// Extremes stored as [min_offset_from_epoch, height_m, 1_if_hw]\n"
    f"// epoch_ms = {EPOCH_MS}  (2026-01-01 00:00 UTC)\n"
    f"/* eslint-disable */\n"
    f"window.TIDE_DB = {json.dumps(db, separators=(',', ':'))};\n"
)

with open(out_path, 'w', encoding='utf-8') as f:
    f.write(js)

size_mb       = os.path.getsize(out_path) / 1024 / 1024
total_extremes = sum(len(sd['extremes']) for sd in all_stations.values())

print(f"\n✓ tide-db.js written")
print(f"  Primary stations   : {len(primary_port_data)}")
print(f"  Secondary stations : {len(secondary_port_data)}")
print(f"  Total stations     : {len(all_stations)}")
print(f"  Total extremes     : {total_extremes:,}")
print(f"  File size          : {size_mb:.2f} MB")
print(f"\n  Run: open marine_sar.html\n")
