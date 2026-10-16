# Montney & Duvernay Oil Well Map

An interactive map and data set of every Alberta oil well producing from the **Montney** and **Duvernay** formations, built entirely from public data. It shows each well's lateral, its average monthly production (BOE), the top 20 oil licensees in each formation, and how each well ranks against its same-formation neighbours.

One command downloads the public data, processes it and writes:

| File | What it is |
|---|---|
| `map.html` | The interactive map. Self-contained: double-click to open in any browser. |
| `wells_header.csv` | One row per well: UWI, licensee, formation, pool, surface and bottom-hole lat/long (NAD83), TVD, measured depth, reach, spud date, production summary. |
| `wells_monthly.csv` | One row per well per month: oil, gas, condensate, water, BOE, hours on, BOE/day. |
| `top20_oil_licensees.csv` | Top 20 licensees per formation, ranked by crude oil volume. |

The two well CSVs load directly into SeisWare Mercury (or any GIS / geoscience tool) as a well header and monthly production table keyed on UWI.

A finished run for **Jan–Aug 2026** is already in `output/2026-01_2026-08/`. Open its `map.html` to see the result without installing anything.

---

## Quick start

You need **Python 3.9 or newer** and an internet connection.

### Windows

```bat
git clone https://github.com/<your-account>/montney-duvernay-well-map.git
cd montney-duvernay-well-map
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python pipeline.py --start 2026-01 --end 2026-08
```

### macOS / Linux

```bash
git clone https://github.com/<your-account>/montney-duvernay-well-map.git
cd montney-duvernay-well-map
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python pipeline.py --start 2026-01 --end 2026-08
```

The results land in `output/2026-01_2026-08/`. Open `map.html` there.

### Other date ranges

```bash
python pipeline.py --start 2021-01 --end 2026-08     # Jan 2021 to Aug 2026
python pipeline.py --start 2021-01                   # up to the latest published month
python pipeline.py --start 2025-01 --end 2025-12 --top 30 --out output/2025_top30
```

| Option | Default | Meaning |
|---|---|---|
| `--start` | `2026-01` | First production month (YYYY-MM) |
| `--end` | last month | Last production month. Months Petrinex hasn't published yet are skipped. |
| `--top` | `20` | How many licensees to rank per formation |
| `--out` | `output/<start>_<end>` | Output folder |
| `--cache` | `data_cache/` | Where downloads are kept |

### Run time and disk space

The first run downloads about **750 MB** of public data, mostly the AER well-location file (~565 MB) plus ~8 MB per production month. Everything is cached in `data_cache/`, so later runs only download months they haven't seen. A run takes 1–3 minutes after the downloads finish; six years of months takes longer. The cache is excluded from git.

Petrinex publishes each production month around the 20th–31st of the following month or later, with revisions for several months after.

---

## Using the map

- **Dots** sit at each well's bottom-hole location; the **thin line** is the surface-to-bottom-hole trace (the horizontal lateral). Colour and size show average BOE per producing month. Duvernay wells have a blue outline.
- **Filters** along the top: formation, licensee, top-20 only, and laterals on/off.
- **Well tab:** click a well to see its monthly production, depth, reach, GOR and water cut. It also shows where the well ranks among other wells in the **same formation within 2–20 km**. That ranking is the core comparison: wells at similar depth in the same zone that still produce very differently.
- **Licensees tab:** the top 20 per formation, with per-well median BOE to show how consistent each program is.
- **Compare tab:** pin up to six wells to plot side by side.

The map loads its mapping library (Leaflet) from cdnjs.cloudflare.com, so it needs internet access to display.

### Sharing the map

- **Email or Teams:** send `map.html`. It holds all its data, so it works on its own.
- **GitHub Pages:** the latest map is copied to `docs/index.html`. In the repository's **Settings → Pages**, choose *Deploy from a branch*, branch `main`, folder `/docs`. The map is then live at `https://<your-account>.github.io/montney-duvernay-well-map/`. Pages on a private repository requires a paid GitHub plan, and a Pages site is public.

To update the Pages copy after a new run:

```bash
cp output/2021-01_2026-08/map.html docs/index.html      # macOS / Linux
copy output\2021-01_2026-08\map.html docs\index.html    # Windows
```

---

## Method

### Data sources (public, no login)

| Source | Used for | Link |
|---|---|---|
| Petrinex Conventional Volumetrics (monthly) | Well-level production volumes | [petrinex.ca/public-data](https://www.petrinex.ca/public-data/) |
| Petrinex Well Infrastructure | Pool / formation, licensee, TVD, measured depth, spud date, status | same |
| Petrinex Well Licence | Terminating formation (fallback formation tag) | same |
| AER ST37 well list (shapefiles) | Surface and bottom-hole latitude / longitude | [aer.ca ST37](https://www.aer.ca/data-and-performance-reports/statistical-reports/st37) |

The Western Canada Energy Database ([registry.schematic.pro](https://registry.schematic.pro/)) is built on the same Petrinex data. Ten sample wells matched its August 2026 values exactly.

### Rules

- **Formation.** A well event is Montney if its Petrinex pool name contains MONTNEY (including commingled pools such as Bluesky-Gething-Montney), and Duvernay if it contains DUVERNAY. Many newer horizontals have no pool assigned yet; for those, the AER licence's terminating formation is used. The `Formation_Source` column records which rule tagged each well.
- **Oil wells.** Wells that reported crude oil (Petrinex product `OIL`) at any point in the date range. Condensate-only wells, which make up much of the Duvernay shale-gas window, are excluded.
- **Production.** Petrinex `PROD` activity reported against the well (`FromToIDType = WI`).
- **Units.** 1 m³ = 6.2898 bbl; 1 e³m³ gas = 35.3147 mcf; BOE = oil + condensate + gas ÷ 6 (6 mcf = 1 BOE).
- **Average BOE per producing month** = total BOE ÷ months with production. **BOE/day** = total BOE ÷ calendar days in those months.
- **Reach** = straight-line distance from surface to bottom hole, a rough proxy for lateral length.
- **Licensee** = the well's current licensee. When a company has been acquired, its wells' earlier production is credited to today's licensee. This matters most for multi-year ranges.
- **Licensee ranking** = total crude oil from that licensee's wells in the formation over the date range. bbl/d = total ÷ calendar days in the range.

### Limits

- Confidential wells are not in Petrinex public volumetrics.
- Petrinex revises recent months, so re-running later can change the latest few months slightly.
- Public data does not include completion design (stages, proppant, fluid), landing interval, petrophysics, reservoir pressure or seismic structure. Those are the main reasons neighbouring wells at similar depth perform differently, and they are the data to load alongside these files in Mercury.

---

## Repository layout

```
pipeline.py             download, process, write CSVs and map (the only script to run)
map_template.html       map page; pipeline.py fills in the data
requirements.txt        Python packages
output/2026-01_2026-08  sample results for Jan–Aug 2026
docs/index.html         latest map, for GitHub Pages
data_cache/             downloads (created on first run, not committed)
```

## Troubleshooting

- **`pip install` fails on `pyogrio`:** upgrade pip first (`python -m pip install --upgrade pip`), then retry. pyogrio ships ready-built packages for Windows, macOS and Linux, so no separate GDAL install is needed.
- **A download times out:** run the same command again. Finished downloads are cached and the script retries each one three times.
- **`map.html` shows a blank map:** the browser needs internet access to load Leaflet from cdnjs.cloudflare.com.
- **Opening `map_template.html` shows "No well data":** that's expected. Open the `map.html` in an output folder instead.

## Data terms

Petrinex and AER data are published by Alberta's regulators for public use. Review their terms before redistributing derived data: [Petrinex terms](https://www.petrinex.ca/terms) and [AER](https://www.aer.ca/).
