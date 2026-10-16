"""
Montney & Duvernay oil-well production map for Alberta, built from public data.

One command downloads the public data, tags Montney and Duvernay wells, computes
per-well production metrics, ranks licensees, and writes CSVs plus a
self-contained interactive map (map.html) you can open by double-clicking.

    python pipeline.py --start 2026-01 --end 2026-08

Data sources (all public, no login needed):
  Petrinex Conventional Volumetrics (monthly)  well-level production volumes
  Petrinex Well Infrastructure                 pool / formation, licensee, depth, spud date
  Petrinex Well Licence                        terminating formation (fallback formation tag)
  AER ST37 well shapefiles                     surface and bottom-hole latitude / longitude

See README.md for the method and its limits.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import date

import numpy as np
import pandas as pd
import requests

PETRINEX = "https://www.petrinex.gov.ab.ca/publicdata/API/Files/AB"
ST37_URL = "https://static.aer.ca/prd/documents/sts/st37/ST37_Shapefiles.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "map_template.html")

BBL_PER_M3 = 6.2898      # oil / condensate / water
MCF_PER_E3M3 = 35.3147   # gas: 1 e3m3 = 35.3147 mcf
MCF_PER_BOE = 6.0        # 6 mcf gas = 1 BOE

# AER's file server rejects the default python-requests user agent, so identify the tool plainly.
HTTP = requests.Session()
HTTP.headers["User-Agent"] = "Mozilla/5.0 (compatible; montney-duvernay-well-map)"

WI_COLS = ["WellID", "WellIdentifier", "LicenceNumber", "WellName", "LicenseeName",
           "PoolDeposit", "PoolDepositName", "FieldName", "WellStatusFluid", "WellStatusMode",
           "HorizontalDrill", "SpudDate", "FinalTotalDepth", "MaxTrueVerticalDepth"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- downloads
def _download(url: str, retries: int = 3) -> bytes:
    for attempt in range(1, retries + 1):
        try:
            r = HTTP.get(url, timeout=1800)
            if r.status_code == 404:
                raise FileNotFoundError(url)
            r.raise_for_status()
            return r.content
        except FileNotFoundError:
            raise
        except Exception as exc:  # network hiccup: retry
            if attempt == retries:
                raise
            log(f"  retrying ({exc})")
            time.sleep(5 * attempt)
    raise RuntimeError("unreachable")


def petrinex_csv(url: str, dest: str) -> str:
    """Download a Petrinex file (a zip holding a zipped CSV) once; return the CSV path."""
    found = glob.glob(os.path.join(dest, "*.[cC][sS][vV]"))
    if found:
        return found[0]
    log(f"  downloading {url}")
    os.makedirs(dest, exist_ok=True)
    outer = zipfile.ZipFile(io.BytesIO(_download(url)))
    for name in outer.namelist():
        data = outer.read(name)
        if name.lower().endswith(".zip"):
            zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
        else:
            with open(os.path.join(dest, os.path.basename(name)), "wb") as fh:
                fh.write(data)
    return glob.glob(os.path.join(dest, "*.[cC][sS][vV]"))[0]


def _read_dbf(path: str, columns: list[str]) -> pd.DataFrame:
    """Read selected attribute columns from a shapefile table (no geopandas needed)."""
    from pyogrio.raw import read

    meta, _fids, _geom, values = read(path, columns=columns, read_geometry=False)
    return pd.DataFrame({name: col for name, col in zip(meta["fields"], values)})


def st37_tables(cache: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Surface- and bottom-hole coordinate tables from the AER ST37 shapefile package.

    Only the two attribute tables (.dbf) are kept; the coordinates are stored as attributes.
    """
    dest = os.path.join(cache, "st37")
    want = {"SurfaceHole": None, "BottomHole": None}
    for key in want:
        hits = glob.glob(os.path.join(dest, "**", f"ST37_{key}_*.dbf"), recursive=True)
        want[key] = hits[0] if hits else None
    if not all(want.values()):
        log(f"  downloading AER ST37 well locations (~565 MB, one time only): {ST37_URL}")
        os.makedirs(dest, exist_ok=True)
        tmp = os.path.join(dest, "st37.zip")
        with HTTP.get(ST37_URL, stream=True, timeout=3600) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                shutil.copyfileobj(r.raw, fh, length=1 << 20)
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                base = os.path.basename(name)
                if base.startswith(("ST37_SurfaceHole_", "ST37_BottomHole_")):
                    with open(os.path.join(dest, base), "wb") as fh:
                        fh.write(z.read(name))
        os.remove(tmp)
        for key in want:
            want[key] = glob.glob(os.path.join(dest, "**", f"ST37_{key}_*.dbf"), recursive=True)[0]
    sh = _read_dbf(want["SurfaceHole"], ["Well_Lic", "SH_Lat", "SH_Long"])
    bh = _read_dbf(want["BottomHole"], ["Well_UWI", "BH_Latitud", "BH_Longitu"])
    return (sh.drop_duplicates("Well_Lic").set_index("Well_Lic"),
            bh.drop_duplicates("Well_UWI").set_index("Well_UWI"))


# --------------------------------------------------------------------------- processing
def tag_wells(cache: str) -> pd.DataFrame:
    """Return well events whose pool (or, if no pool, licence target) is Montney or Duvernay."""
    wi_path = petrinex_csv(f"{PETRINEX}/Infra/Well%20Infrastructure/CSV", os.path.join(cache, "well_infrastructure"))
    wl_path = petrinex_csv(f"{PETRINEX}/Infra/Well%20Licence/CSV", os.path.join(cache, "well_licence"))
    wi = pd.read_csv(wi_path, dtype=str, usecols=WI_COLS, low_memory=False).drop_duplicates("WellID")
    wl = (pd.read_csv(wl_path, dtype=str, usecols=["LicenceNumber", "TerminatingFormation"])
          .drop_duplicates("LicenceNumber"))
    wi = wi.merge(wl, on="LicenceNumber", how="left")

    pool = wi.PoolDepositName.fillna("").str.upper().str.strip()
    is_duv = pool.str.contains("DUVERNAY")
    is_mont = pool.str.contains(r"(?:^|[\s-])MONT(?:NEY)?(?:$|[\s-])", regex=True)
    wi["Formation"] = np.select([is_duv, is_mont], ["Duvernay", "Montney"], default="")
    wi["Formation_Source"] = np.where(wi.Formation != "", "Pool", "")
    wi["Commingled"] = (wi.Formation != "") & ~pool.str.match(r"^(MONTNEY|DUVERNAY) ")

    term = wi.TerminatingFormation.fillna("").str.upper()
    no_pool = wi.PoolDeposit.isna() & (wi.Formation == "")
    for key, name in (("MONTNEY", "Montney"), ("DUVERNAY", "Duvernay")):
        hit = no_pool & term.str.startswith(key)
        wi.loc[hit, "Formation"] = name
        wi.loc[hit, "Formation_Source"] = "Licence terminating formation"
    tagged = wi[wi.Formation != ""].copy()
    log(f"  tagged {len(tagged):,} Montney/Duvernay well events")
    return tagged


def monthly_production(months: list[str], wells: pd.DataFrame, cache: str) -> pd.DataFrame:
    keep = set(wells.WellID)
    parts = []
    for m in months:
        try:
            path = petrinex_csv(f"{PETRINEX}/Vol/{m}/CSV", os.path.join(cache, "volumetrics", m))
        except FileNotFoundError:
            log(f"  {m}: not published on Petrinex yet, skipped")
            continue
        v = pd.read_csv(path, dtype=str, usecols=["ProductionMonth", "ActivityID", "ProductID",
                                                  "FromToID", "FromToIDType", "Volume", "Hours"])
        v = v[(v.ActivityID == "PROD") & (v.FromToIDType == "WI") & v.FromToID.isin(keep)]
        parts.append(v)
        log(f"  {m}: {v.FromToID.nunique():,} wells reporting")
    if not parts:
        sys.exit("No production months were available for that date range.")
    v = pd.concat(parts, ignore_index=True)
    v["Volume"] = pd.to_numeric(v.Volume, errors="coerce").fillna(0.0)
    v["Hours"] = pd.to_numeric(v.Hours, errors="coerce")
    p = (v.pivot_table(index=["FromToID", "ProductionMonth"], columns="ProductID", values="Volume",
                       aggfunc="sum", fill_value=0.0).reset_index())
    hrs = v.groupby(["FromToID", "ProductionMonth"]).Hours.max().reset_index()
    p = p.merge(hrs, on=["FromToID", "ProductionMonth"])
    for col in ("OIL", "GAS", "COND", "WATER"):
        if col not in p:
            p[col] = 0.0
    p["Oil_bbl"] = p.OIL * BBL_PER_M3
    p["Cond_bbl"] = p.COND * BBL_PER_M3
    p["Water_bbl"] = p.WATER * BBL_PER_M3
    p["Gas_mcf"] = p.GAS * MCF_PER_E3M3
    p["BOE"] = p.Oil_bbl + p.Cond_bbl + p.Gas_mcf / MCF_PER_BOE
    return p.rename(columns={"FromToID": "WellID", "ProductionMonth": "Month"})


def petrinex_to_aer_uwi(s: str) -> str:
    """100160704327W400 -> 00/16-07-043-27W4/0 (the format AER ST37 uses)."""
    return f"{s[1:3]}/{s[3:5]}-{s[5:7]}-{s[7:10]}-{s[10:12]}{s[12:14]}/{int(s[14:16])}"


def num(x):
    x = pd.to_numeric(x, errors="coerce")
    return None if pd.isna(x) else float(x)


def clean(x) -> str:
    return x.strip() if isinstance(x, str) else ""


# --------------------------------------------------------------------------- main build
def build(start: str, end: str, out_dir: str | None, cache: str, top: int) -> None:
    months = [str(p) for p in pd.period_range(start, end, freq="M")]
    log(f"Building {months[0]} to {months[-1]} ({len(months)} months)")

    log("Step 1/4  Tagging Montney and Duvernay wells")
    wells = tag_wells(cache)

    log("Step 2/4  Reading monthly production")
    prod = monthly_production(months, wells, cache)
    months = sorted(prod.Month.unique())  # drop months Petrinex has not published
    days = {m: pd.Period(m).days_in_month for m in months}
    if out_dir is None:
        out_dir = os.path.join(HERE, "output", f"{months[0]}_{months[-1]}")
    total_days = sum(days.values())

    prod = prod.merge(wells, on="WellID")
    oil_ids = set(prod.groupby("WellID").Oil_bbl.sum().loc[lambda s: s > 0].index)
    prod = prod[prod.WellID.isin(oil_ids)].copy()
    log(f"  {len(oil_ids):,} wells reported crude oil in the window")

    log("Step 3/4  Attaching coordinates and computing metrics")
    sh, bh = st37_tables(cache)

    rank = {}
    for f in ("Montney", "Duvernay"):
        order = prod[prod.Formation == f].groupby("LicenseeName").Oil_bbl.sum().sort_values(ascending=False)
        rank[f] = {lic: i + 1 for i, lic in enumerate(order.index)}

    wells_json, header_rows, monthly_rows = [], [], []
    for wid, d in prod.groupby("WellID", sort=False):
        r = d.iloc[0]
        uwi = petrinex_to_aer_uwi(r.WellIdentifier)
        d = d.set_index("Month").reindex(months)
        boe, oil, gas = d.BOE.fillna(0), d.Oil_bbl.fillna(0), d.Gas_mcf.fillna(0)
        cond, water, hours = d.Cond_bbl.fillna(0), d.Water_bbl.fillna(0), d.Hours.fillna(0)
        on_months = [m for m in months if boe[m] > 0]
        n_on = len(on_months)
        tot_boe, tot_oil, tot_gas, tot_w = boe.sum(), oil.sum(), gas.sum(), water.sum()
        on_days = sum(days[m] for m in on_months)
        prod_days = hours.sum() / 24

        lat0 = num(sh.SH_Lat.get(r.LicenceNumber)) if r.LicenceNumber in sh.index else None
        lon0 = num(sh.SH_Long.get(r.LicenceNumber)) if r.LicenceNumber in sh.index else None
        lat1 = num(bh.BH_Latitud.get(uwi)) if uwi in bh.index else None
        lon1 = num(bh.BH_Longitu.get(uwi)) if uwi in bh.index else None
        if lat0 is None and lat1 is not None:
            lat0, lon0 = lat1, lon1
        if lat0 is None:
            continue  # no location: cannot map
        reach = None
        if lat1 is not None:
            dy = (lat1 - lat0) * 111_320
            dx = (lon1 - lon0) * 111_320 * np.cos(np.radians(lat0))
            reach = round(float(np.hypot(dx, dy)))

        f, lic = r.Formation, r.LicenseeName
        tvd, md = num(r.MaxTrueVerticalDepth), num(r.FinalTotalDepth)
        gor = round(tot_gas * 1000 / tot_oil) if tot_oil > 0 else None
        liquids = tot_w + tot_oil + cond.sum()
        wc = round(100 * tot_w / liquids, 1) if liquids > 0 else None
        amb = round(tot_boe / n_on) if n_on else 0
        bpd = round(tot_boe / on_days, 1) if on_days else 0

        wells_json.append(dict(
            u=uwi, id=wid, n=clean(r.WellName), l=lic, f=f, rk=rank[f].get(lic), fd=clean(r.FieldName),
            p=clean(r.PoolDepositName), ts=r.Formation_Source, cm=bool(r.Commingled),
            st=f"{r.WellStatusFluid}/{r.WellStatusMode}", hz=clean(r.HorizontalDrill).title(),
            sp=clean(r.SpudDate)[:10], tvd=None if tvd is None else round(tvd), md=None if md is None else round(md),
            reach=reach, sh=[round(lat0, 5), round(lon0, 5)],
            bh=None if lat1 is None else [round(lat1, 5), round(lon1, 5)],
            on=n_on, boe=[round(x) for x in boe], amb=amb, bpd=bpd,
            to=round(tot_oil), tg=round(tot_gas), gor=gor, wc=wc))
        header_rows.append(dict(
            UWI=uwi, Petrinex_WellID=wid, Well_Name=clean(r.WellName), Licensee=lic,
            Licensee_Oil_Rank_in_Formation=rank[f].get(lic), Top20=(rank[f].get(lic) or 999) <= top,
            Formation=f, Formation_Source=r.Formation_Source, Commingled_Pool=bool(r.Commingled),
            Pool=clean(r.PoolDepositName), Field=clean(r.FieldName),
            Status=f"{r.WellStatusFluid}/{r.WellStatusMode}", Drill_Type=clean(r.HorizontalDrill).title(),
            Spud_Date=clean(r.SpudDate)[:10], SH_Lat_NAD83=lat0, SH_Long_NAD83=lon0,
            BH_Lat_NAD83=lat1, BH_Long_NAD83=lon1, Max_TVD_m=tvd, Total_Depth_MD_m=md,
            SH_to_BH_Reach_m=reach, Months_Producing=n_on, Avg_BOE_per_Producing_Month=amb,
            Avg_BOE_per_Day=bpd, Oil_bbl=round(tot_oil), Gas_mcf=round(tot_gas),
            GOR_scf_per_bbl=gor, Water_Cut_pct=wc))
        for m in months:
            monthly_rows.append(dict(
                UWI=uwi, Petrinex_WellID=wid, Month=m, Formation=f, Licensee=lic,
                Oil_bbl=round(oil[m], 1), Gas_mcf=round(gas[m], 1), Condensate_bbl=round(cond[m], 1),
                Water_bbl=round(water[m], 1), BOE=round(boe[m], 1), Hours_On=hours[m],
                BOE_per_day=round(boe[m] / days[m], 2)))

    log("Step 4/4  Writing outputs")
    os.makedirs(out_dir, exist_ok=True)
    header = pd.DataFrame(header_rows).sort_values(["Formation", "Licensee_Oil_Rank_in_Formation", "UWI"])
    header.to_csv(os.path.join(out_dir, "wells_header.csv"), index=False)
    pd.DataFrame(monthly_rows).to_csv(os.path.join(out_dir, "wells_monthly.csv"), index=False)

    ranks = []
    for f in ("Montney", "Duvernay"):
        g = (prod[prod.Formation == f].groupby("LicenseeName")
             .agg(Oil_bbl=("Oil_bbl", "sum"), Gas_mcf=("Gas_mcf", "sum"), BOE=("BOE", "sum"),
                  Wells=("WellID", "nunique"))
             .sort_values("Oil_bbl", ascending=False).head(top).reset_index())
        g.insert(0, "Rank", range(1, len(g) + 1))
        g.insert(1, "Formation", f)
        g["Oil_bbl_per_day"] = g.Oil_bbl / total_days
        g["BOE_per_day"] = g.BOE / total_days
        ranks.append(g.round(1))
    pd.concat(ranks).rename(columns={"LicenseeName": "Licensee"}).to_csv(
        os.path.join(out_dir, f"top{top}_oil_licensees.csv"), index=False)

    data = dict(months=months, days=[days[m] for m in months], total_days=total_days, top=top,
                generated=date.today().isoformat(), wells=wells_json)
    write_map(data, os.path.join(out_dir, "map.html"))
    log(f"Done: {len(wells_json):,} wells -> {os.path.abspath(out_dir)}")
    for f, g in zip(("Montney", "Duvernay"), ranks):
        n = sum(1 for w in wells_json if w["f"] == f)
        top_name = g.LicenseeName.iloc[0] if len(g) else "none"
        log(f"  {f}: {n:,} oil wells; top oil licensee {top_name}")


def write_map(data: dict, path: str) -> None:
    """Inline the data into the map template so the result opens with a double-click."""
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*__WELL_DATA__*/null", payload, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-01", help="first production month, YYYY-MM (default 2026-01)")
    ap.add_argument("--end", default=None, help="last production month, YYYY-MM (default: latest published)")
    ap.add_argument("--out", default=None, help="output folder (default output/<start>_<end>)")
    ap.add_argument("--cache", default=os.path.join(HERE, "data_cache"),
                    help="where downloads are kept between runs (default ./data_cache)")
    ap.add_argument("--top", type=int, default=20, help="licensees to rank per formation (default 20)")
    a = ap.parse_args()
    end = a.end or (pd.Period(date.today(), freq="M") - 1).strftime("%Y-%m")
    build(a.start, end, a.out, a.cache, a.top)


if __name__ == "__main__":
    main()
