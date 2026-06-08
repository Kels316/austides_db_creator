# ANTT Tide DB

A Python tool that parses the **Australian National Tide Tables (ANTT)** PDF published annually by the [Australian Hydrographic Office](https://www.hydro.gov.au) and produces a compact JavaScript tide database (`tide-db.js`) covering every primary and secondary port in Australia.

The output database is self-contained and requires no server or API — drop it into any web project and read tide predictions with a few lines of JavaScript.

---

## What's included

| File | Description |
|---|---|
| `build_tide_db.py` | Parses an ANTT PDF and writes `tide-db.js` |
| `check-tides.mjs` | CLI tool to inspect DB entries for a named coastal bar |
| `.github/workflows/refresh-tide-db.yml` | GitHub Actions workflow to rebuild the DB each January |
| `tide-db.js` | Pre-built database for the current year |

---

## Input required

You need the **ANTT PDF** for the relevant year — for example `ANTT_2026.pdf`. This is published annually by the Australian Hydrographic Office and available from [hydro.gov.au](https://www.hydro.gov.au).

No other input is required. Everything — primary predictions, secondary port offsets, datum levels — is read directly from the PDF.

---

## Setup

```bash
# Python 3.9+ required
pip install pypdf

# Build the database
python3 build_tide_db.py /path/to/ANTT_2026.pdf
```

This writes `tide-db.js` alongside the script. The build takes about 30 seconds and produces a ~13 MB file covering all 640 stations for the full year.

### Optional: inspect the output

```bash
# Node.js required
node check-tides.mjs "Noosa Bar"
node check-tides.mjs "Sydney Heads"
node check-tides.mjs "Darwin Bar"
```

Prints the next 48 hours of HW/LW extremes for the named bar in AEST, plus the first few entries in the DB for verification.

---

## How it works

### Primary ports (79 stations)

The ANTT PDF includes pre-computed HW/LW predictions for 79 primary ports across Australia. The script extracts each entry directly from the tide table pages using `pypdf`. Times are stored as UTC; the local time zone for each port is read from the page header.

### Secondary ports (561 stations)

Secondary port predictions are calculated from the nearest standard port using the **official ANTT method** described in AHP11 (included in the ANTT as a reference form):

**Heights:**
```
secondary_height = (std_height − std_MSL) × range_ratio + sec_MSL
```
where:
```
range_ratio = (sec_MHWS − sec_MLWS) / (std_MHWS − std_MLWS)
```

**Times:**
```
secondary_time = std_time + mean_time_difference
```

All values — `std_MSL`, `sec_MSL`, `sec_MHWS`, `sec_MLWS`, and `mean_time_difference` — are read from the ANTT Chapter 4 datum tables in the same PDF. No external data is required.

The mean time difference is extracted from a compact column at the bottom of each datum table page, in the same order as the secondary ports listed on that page. The standard port (reference) for each secondary is the nearest NA-flagged port on the same table page.

### Timestamp encoding

Extremes are stored as `[min_offset, height_m, is_hw]` triples where `min_offset` is minutes since `2026-01-01T00:00:00Z`. This keeps the file compact (6–7 digit integers vs 13-digit millisecond timestamps).

To convert back to a Unix timestamp in JavaScript:
```javascript
const epochMs = db.epoch_ms;  // e.g. 1767225600000
const tsMs = epochMs + min_offset * 60000;
```

---

## Database format

`tide-db.js` assigns a global:
```javascript
window.TIDE_DB = { ... }
```

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `epoch_ms` | number | Unix ms for `2026-01-01T00:00:00Z` — add to all `min_offset` values |
| `generated` | string | ISO timestamp when the DB was built |
| `valid_until` | string | `2026-12-31T23:59:59Z` |
| `source` | string | Attribution string |
| `stations` | object | Keyed by station ID |
| `barToStation` | object | Maps 39 coastal bar names to their nearest station ID |

### Station object — primary port

```javascript
{
  "name": "Mooloolaba",
  "lat": -26.6833,
  "lon": 153.1333,
  "type": "primary",
  "extremes": [
    [min_offset, height_m, 1],   // 1 = HW
    [min_offset, height_m, 0],   // 0 = LW
    ...
  ]
}
```

### Station object — secondary port

```javascript
{
  "name": "Noosa Head",
  "lat": -26.3833,
  "lon": 153.1,
  "type": "secondary",
  "ref": "p_mooloolaba",         // reference primary port ID
  "ref_dist_km": 32.1,
  "hw_factor": 1,                // range_ratio (same for HW and LW in semi-diurnal)
  "lw_factor": 1,
  "time_offset_min": -10,        // mean time difference (minutes)
  "extremes": [ ... ]            // already shifted by time offset and scaled
}
```

### barToStation

```javascript
{
  "Noosa Bar": "s_noosa_head",
  "Mooloolaba Bar": "p_mooloolaba",
  "Sydney Heads": "p_sydney_fort_denison_",
  ...
}
```

---

## Using the DB in a web project

```html
<script src="tide-db.js"></script>
<script>
  const db = window.TIDE_DB;
  const epochMs = db.epoch_ms;
  const toMs = t => epochMs + t * 60000;

  // Look up a bar by name
  const stationId = db.barToStation['Noosa Bar'];
  const station = db.stations[stationId];

  // Get all extremes as { t (UTC ms), h (metres), isHW }
  const extremes = station.extremes.map(([t, h, w]) => ({
    t: toMs(t),
    h,
    isHW: w === 1
  }));

  // Filter to next 24 hours
  const now = Date.now();
  const next24h = extremes.filter(e => e.t >= now && e.t < now + 86400000);
  console.log(next24h);
</script>
```

---

## Coastal bar lookup

The DB includes 39 Australian coastal bar locations pre-mapped to their nearest tide station, covering QLD, NSW, VIC, SA, WA, and NT. To find the nearest bar to a given coordinate at runtime, compute the Haversine distance from each entry in `barToStation` to your target position and pick the closest.

---

## Accuracy

**Primary ports** — predictions match the ANTT published tables exactly (parsed directly from the PDF).

**Secondary ports** — the ANTT method is an approximation. Expected accuracy for well-characterised secondary ports (e.g. Noosa Head from Mooloolaba):
- Heights: within ±0.05 m of published tables
- Times: within ±10 minutes (the mean time difference is fixed; actual offsets vary slightly tide by tide)

This is consistent with the stated accuracy of the ANTT secondary port method.

---

## Annual update

Each year when the new ANTT is published:

1. Download the new PDF from [hydro.gov.au](https://www.hydro.gov.au)
2. Run `python3 build_tide_db.py /path/to/ANTT_YYYY.pdf`
3. Commit the new `tide-db.js`

The included GitHub Actions workflow (`refresh-tide-db.yml`) automates this on 1 January each year. You'll need to place the new PDF alongside the script (or adjust the workflow to download it) for the automation to work.

---

## Licence

Tide prediction data is sourced from the **Australian National Tide Tables**, © Commonwealth of Australia (Australian Hydrographic Office). Reproduction for non-commercial purposes is permitted with attribution.

The scripts in this repository are MIT licensed.
