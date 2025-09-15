#!/usr/bin/env python3
"""
INCRYO – Volza HSN 7311 & 8419 Market Intelligence Reports (v4, currency-safe)
-------------------------------------------------------------------------------
Fixes the "billions per unit" issue by detecting non-USD currencies (e.g., VND)
in Product Descriptions and converting to USD before all aggregations.

Key changes vs v3:
- Currency detection from text ("VND", "MYR", "IDR", "THB", "CNY", "JPY", "INR",
  "AED", "SAR", "QAR", "EUR", "GBP") + simple heuristics.
- FX map (FX_PER_USD): local units per USD (e.g., 1 USD = 24,000 VND).
- New columns: unit_rate_norm_usd, value_norm_usd (used in *all* charts).
- Audit CSV: 00_source/currency_audit_sample.csv (to review conversions).

Usage:
  Put '7311_tanks_only.xlsx' and '84195010 (1).xlsx' next to this file.
  pip install pandas plotly kaleido
  python incryo_volza_reports_v4.py
"""

from pathlib import Path
import re
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ========= USER CONFIG =========
FILES = [
    "7311_tanks_only.xlsx",
    "84195010 (1).xlsx",
]
OUTPUT_ROOT = Path("outputs")
TOP_N = 15

# MEA subset
FOCUS_COUNTRIES_RAW = [
    "Ghana", "Kenya", "Namibia", "Oman", "Qatar", "Saudi Arabia", "Senegal",
    "South Sudan", "Uganda", "United Arab Emirates"
]

COUNTRY_NORMALIZATION = {
    "UAE": "United Arab Emirates",
    "U.A.E.": "United Arab Emirates",
    "United Arab Emirate": "United Arab Emirates",
    "KSA": "Saudi Arabia",
    "S. Arabia": "Saudi Arabia",
    "South Sudan, Republic of": "South Sudan",
    "Cote d'Ivoire": "Côte d’Ivoire",
    "Ivory Coast": "Côte d’Ivoire",
    "OP India":"India",
    "Not Available": "",
    "N/A":"",
    "nan":""
}

# --- FX map: *local units per USD* (amount_local / FX_PER_USD -> USD)
# Adjust these to your preferred rates if needed.
FX_PER_USD = {
    "usd": 1.0,
    "vnd": 24000.0,
    "myr": 4.70,
    "thb": 36.0,
    "idr": 15500.0,
    "cny": 7.25,
    "jpy": 155.0,
    "inr": 84.0,
    "eur": 0.93,
    "gbp": 0.79,
    "aed": 3.67,
    "sar": 3.75,
    "qar": 3.64,
}

CURRENCY_TOKENS = tuple(FX_PER_USD.keys())  # lowercase tokens to scan for

# =================================

def ensure_dirs():
    for sf in [
        "00_source", "by_country", "by_country_supplier", "by_product",
        "flows", "ports", "trends", "scoring", "hsn_split", "MEA_focus"
    ]:
        (OUTPUT_ROOT / sf).mkdir(parents=True, exist_ok=True)

def install_kaleido_if_needed():
    try:
        import kaleido  # noqa
    except Exception:
        import subprocess, sys
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "kaleido"])
        except Exception as e:
            print("WARN: kaleido not available for PNG export:", e)

# ---------- robust reading ----------
EXPECTED_HEADERS = {
    "HS Code","Product Description","Consignee","Shipper","Std. Quantity","Std. Unit",
    "Quantity","Unit","Unit Rate $","Value $","Country of Origin","Port of Origin",
    "Country of Destination","Port of Destination","Date","HS Product Description","Source Country"
}

def find_header_row(df_head: pd.DataFrame) -> int:
    best_idx, best_score = 0, -1
    for i in range(min(10, len(df_head))):
        row = df_head.iloc[i].astype(str).str.strip().tolist()
        score = sum(1 for cell in row if cell in EXPECTED_HEADERS)
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx

def read_volza(file_path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(file_path)
    sheet = "Global Search Data" if "Global Search Data" in xl.sheet_names else xl.sheet_names[0]
    raw = pd.read_excel(file_path, sheet_name=sheet, header=None, dtype=object)
    h = find_header_row(raw.head(10))
    header = raw.iloc[h].tolist()
    data = raw.iloc[h+1:].copy()
    data.columns = header
    data = data.dropna(how="all")
    return data

# ---------- cleaning & mapping ----------
def to_lower_keys(names):
    return [str(x).strip().lower() for x in names]

def coalesce_cols(df: pd.DataFrame, candidates, newname):
    for c in df.columns:
        if str(c).strip().lower() in candidates:
            return df.rename(columns={c:newname})
    return df

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Flexible renames using lowercase matches
    lower_cols = to_lower_keys(df.columns)
    mapper = {}
    canonical = {
        "hs code":"hs_code",
        "hs product description":"hs_description",
        "product description":"product_description",
        "consignee":"consignee",
        "shipper":"shipper",
        "std. quantity":"std_qty",
        "std. unit":"std_unit",
        "quantity":"qty",
        "unit":"unit",
        "unit rate $":"unit_rate_usd",
        "value $":"value_usd",
        "country of origin":"origin_country",
        "port of origin":"origin_port",
        "country of destination":"dest_country",
        "port of destination":"dest_port",
        "shipment mode":"shipment_mode",
        "source country":"source_country_field",
        "date":"date",
        "gross weight":"gross_weight",
        "measurment":"measurement",
    }
    for i, c in enumerate(df.columns):
        lc = lower_cols[i]
        if lc in canonical:
            mapper[c] = canonical[lc]
    df = df.rename(columns=mapper)

    # Secondary aliases for value/price
    alias_map = {
        tuple(["estimated cif value $","landed value $","total value $","value usd","cif value $"]): "value_usd",
        tuple(["unit value $","price usd","usd unit price"]): "unit_rate_usd",
    }
    for keys, newname in alias_map.items():
        df = coalesce_cols(df, set(keys), newname)

    # parse date & month
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()

    # numeric cleanup
    def to_num(s):
        return pd.to_numeric(
            pd.Series(s).astype(str).str.replace(",","", regex=False).str.extract(r"([-+]?\d*\.?\d+)")[0],
            errors="coerce"
        )

    for col in ["std_qty","qty","unit_rate_usd","value_usd","gross_weight"]:
        if col in df.columns:
            df[col] = to_num(df[col])

    # normalize countries
    for col in ["origin_country","dest_country"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace(COUNTRY_NORMALIZATION)
            df[col] = df[col].replace("", np.nan)

    # direction from 'Source Country' text
    if "source_country_field" in df.columns:
        lower = df["source_country_field"].astype(str).str.lower()
        df["direction"] = np.where(lower.str.contains("export"), "Export",
                    np.where(lower.str.contains("import"), "Import", None))
    # HS helpers
    if "hs_code" in df.columns:
        df["hs_code"] = df["hs_code"].astype(str).str.strip()
        df["hs2"] = df["hs_code"].str[:2]
        df["hs4"] = df["hs_code"].str[:4]

    # commodity label
    df["commodity"] = np.where(df.get("hs2","").astype(str).str.startswith("73"),
                               "HSN 7311 – Tanks/Cylinders",
                               np.where(df.get("hs4","").astype(str).str.startswith("8419"),
                                        "HSN 8419 – Vaporizers & Heat-exchange",
                                        "Other"))

    # party & product text tidy
    for col in ["shipper","consignee"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.title()
    if "product_description" in df.columns:
        df["product_description"] = df["product_description"].astype(str).str.strip().str.title()
    if "hs_description" in df.columns:
        df["hs_description"] = df["hs_description"].astype(str).str.strip()

    return df

# ---------- currency normalization ----------
CURRENCY_REGEX = re.compile(r"\b(vnd|myr|thb|idr|cny|jpy|inr|aed|sar|qar|eur|gbp|usd)\b", re.IGNORECASE)

def detect_currency(row) -> str:
    """
    Inspect product/HS description & source text for currency tokens.
    Heuristics:
      - If 'vnd'/'myr'/... appears → use that.
      - Else if unit_rate looks astronomically high (>1e7) AND Vietnam appears → assume VND.
      - Else default to USD.
    """
    txt = " ".join([
        str(row.get("product_description","")),
        str(row.get("hs_description","")),
        str(row.get("source_country_field","")),
    ]).lower()

    m = CURRENCY_REGEX.search(txt)
    if m:
        return m.group(1).lower()

    # heuristic for common case (Vietnam numbers in VND)
    try:
        ur = float(row.get("unit_rate_usd", np.nan))
    except Exception:
        ur = np.nan
    is_vnm = any(str(row.get(col,"")).strip().lower()=="vietnam" for col in ["origin_country","dest_country"])
    if is_vnm and (not np.isnan(ur)) and ur > 1e7:
        return "vnd"

    return "usd"

def apply_currency_normalization(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df["currency_detected"] = []
        df["unit_rate_norm_usd"] = []
        df["value_norm_usd"] = []
        return df

    df = df.copy()
    df["currency_detected"] = df.apply(detect_currency, axis=1)

    # convert unit_rate to USD
    def to_usd(amount, code):
        if pd.isna(amount):
            return np.nan
        rate = FX_PER_USD.get(code.lower(), 1.0)
        if rate <= 0:
            return np.nan
        return amount / rate

    df["unit_rate_norm_usd"] = df.apply(
        lambda r: to_usd(r.get("unit_rate_usd", np.nan), r["currency_detected"]), axis=1
    )

    # preferred value: existing 'value_usd' if currency is USD; else recompute from normalized unit rate
    # compute fallback from qty when possible
    qty = df.get("qty", pd.Series([np.nan]*len(df)))
    # start with raw 'value_usd'
    value = df.get("value_usd", pd.Series([np.nan]*len(df))).copy()

    # If currency isn't USD or value is missing/zero, compute from normalized rate * qty
    mask_recalc = (df["currency_detected"] != "usd") | value.isna() | (value <= 0)
    value_calc = df["unit_rate_norm_usd"] * qty
    value.loc[mask_recalc & value_calc.notna()] = value_calc[mask_recalc & value_calc.notna()]
    df["value_norm_usd"] = value

    return df

# ---------- save helpers ----------
def save_fig(fig, subdir, name, width=1400, height=800):
    if fig is None: return
    out_dir = OUTPUT_ROOT / subdir
    out_dir.mkdir(exist_ok=True, parents=True)
    fig.write_html(str(out_dir / f"{name}.html"))
    try:
        fig.write_image(str(out_dir / f"{name}.png"), scale=2, width=width, height=height)
    except Exception as e:
        print(f"PNG save failed for {name}: {e} (install kaleido)")

def save_table(df, subdir, name):
    if df is None: return
    out_dir = OUTPUT_ROOT / subdir
    out_dir.mkdir(exist_ok=True, parents=True)
    df.to_csv(out_dir / f"{name}.csv", index=False)

# ---------- chart builders (now use value_norm_usd) ----------
def top_countries(df, by="value_norm_usd", role="origin"):
    col = "origin_country" if role=="origin" else "dest_country"
    g = df.groupby(col, dropna=True)[by].sum().sort_values(ascending=False).head(TOP_N).reset_index()
    g.columns = [col, by]
    title = f"Top {TOP_N} Countries by {by.replace('_',' ').title()} – {'Export (Origin)' if role=='origin' else 'Import (Destination)'}"
    fig = px.bar(g, x=col, y=by, title=title, text_auto=".2s")
    fig.update_layout(xaxis_title="Country", yaxis_title=by.replace('_',' ').title())
    return fig, g

def top_suppliers_by_country(df, role="dest_country", by="value_norm_usd"):
    if "shipper" not in df.columns: return None, None
    keep = df.dropna(subset=[role, "shipper"])
    g = keep.groupby([role, "shipper"], dropna=True)[by].sum().reset_index().sort_values(by=by, ascending=False)
    title = f"Supplier Footprint by {role.replace('_',' ').title()} (Treemap)"
    fig = px.treemap(g, path=[role, "shipper"], values=by, title=title)
    return fig, g

def top_products(df, by="value_norm_usd"):
    label = "product_description" if "product_description" in df.columns else "hs_description"
    g = df.groupby(label, dropna=True)[by].sum().sort_values(ascending=False).head(TOP_N).reset_index()
    title = f"Top {TOP_N} Products by {by.replace('_',' ').title()}"
    fig = px.bar(g, x=label, y=by, title=title, text_auto=".2s")
    fig.update_layout(xaxis_title="Product", yaxis_title=by.replace('_',' ').title(), xaxis_tickangle=35)
    return fig, g

def monthly_trend(df, by="value_norm_usd"):
    if "month" not in df.columns: return None, None
    g = df.groupby(["month","commodity"], dropna=True)[by].sum().reset_index()
    title = f"Monthly Trend by Commodity – {by.replace('_',' ').title()}"
    fig = px.line(g, x="month", y=by, color="commodity", markers=True, title=title)
    fig.update_layout(xaxis_title="Month", yaxis_title=by.replace('_',' ').title())
    return fig, g

def flow_sankey(df, origins="origin_country", destinations="dest_country", by="value_norm_usd", title="Origin → Destination Flows"):
    keep = df.dropna(subset=[origins, destinations, by])
    g = keep.groupby([origins, destinations])[by].sum().reset_index()
    origin_nodes = list(g[origins].unique())
    dest_nodes = list(g[destinations].unique())
    nodes = origin_nodes + dest_nodes
    idx = {n:i for i,n in enumerate(nodes)}
    source = g[origins].map(idx).tolist()
    target = g[destinations].map(idx).tolist()
    value = g[by].tolist()
    sankey = go.Figure(go.Sankey(
        node=dict(pad=10, thickness=15, line=dict(width=0.5, color="gray"), label=nodes),
        link=dict(source=source, target=target, value=value)
    ))
    sankey.update_layout(title=title)
    return sankey, g

def price_box_by_country(df):
    if "unit_rate_norm_usd" not in df.columns: return None, None
    keep = df.dropna(subset=["dest_country","unit_rate_norm_usd"])
    title = "Unit Price Distribution by Destination Country (USD, normalized)"
    fig = px.box(keep, x="dest_country", y="unit_rate_norm_usd", points=False, title=title)
    fig.update_layout(xaxis_title="Destination Country", yaxis_title="Unit Rate (USD, normalized)")
    return fig, keep

def supplier_market_reach(df):
    if "shipper" not in df.columns: return None, None
    g = (df.dropna(subset=["shipper","dest_country"])[["shipper","dest_country"]]
           .drop_duplicates().groupby("shipper").size().reset_index(name="num_countries"))
    g = g.sort_values("num_countries", ascending=False).head(TOP_N)
    fig = px.bar(g, x="shipper", y="num_countries", title="Suppliers by Number of Destination Countries (Reach)", text_auto=True)
    fig.update_layout(xaxis_title="Supplier", yaxis_title="# Destination Countries")
    return fig, g

def port_analysis(df, role="dest_port", by="value_norm_usd"):
    if role not in df.columns: return None, None
    g = (df.dropna(subset=[role])[by].groupby(df[role]).sum().sort_values(ascending=False).head(TOP_N).reset_index())
    g.columns = [role, by]
    title = f"Top {TOP_N} Ports by {by.replace('_',' ').title()} – {role.replace('_',' ').title()}"
    fig = px.bar(g, x=role, y=by, title=title, text_auto=".2s")
    fig.update_layout(xaxis_title=role.replace('_',' ').title(), yaxis_title=by.replace('_',' ').title())
    return fig, g

def attractiveness_scoring(df):
    if "month" not in df.columns: return None, None
    by = "value_norm_usd"
    d = df.dropna(subset=["dest_country"])
    m = d.groupby(["dest_country","month"])[by].sum().reset_index()
    first = m.sort_values("month").groupby("dest_country")[by].first()
    last  = m.sort_values("month").groupby("dest_country")[by].last()
    growth = ((last - first) / (first.replace(0, np.nan))).replace([np.inf, -np.inf], np.nan)
    avg_price = d.groupby("dest_country")["unit_rate_norm_usd"].mean() if "unit_rate_norm_usd" in d.columns else pd.Series(index=growth.index, dtype=float)
    total_val = d.groupby("dest_country")[by].sum()
    shares = (d.groupby(["dest_country","shipper"])[by].sum()
                .groupby(level=0).apply(lambda s: (s/s.sum())**2).groupby(level=0).sum())
    hhi = shares.reindex(growth.index)
    score = pd.DataFrame({"growth":growth, "avg_price":avg_price, "total_value":total_val, "supplier_hhi":hhi})
    for c in ["growth","avg_price","total_value","supplier_hhi"]:
        score[c+"_z"] = (score[c] - score[c].mean()) / (score[c].std(ddof=0) + 1e-9)
    score["attractiveness"] = score["growth_z"] + score["avg_price_z"] + score["total_value_z"] - score["supplier_hhi_z"]
    score = score.dropna(subset=["attractiveness"]).sort_values("attractiveness", ascending=False).reset_index().rename(columns={"index":"dest_country"})
    fig = px.bar(score.head(TOP_N), x="dest_country", y="attractiveness", title="Top Countries to Target – Attractiveness Score", text_auto=".2f")
    fig.update_layout(xaxis_title="Destination Country", yaxis_title="Attractiveness (Z-score composite)")
    return fig, score

def hsn_split_by_destination(df, top_k=15, by="value_norm_usd"):
    pivot = df.pivot_table(index="dest_country", columns="commodity", values=by, aggfunc="sum", fill_value=0.0)
    pivot["total"] = pivot.sum(axis=1)
    top = pivot.sort_values("total", ascending=False).head(top_k).drop(columns=["total"])
    long = top.reset_index().melt(id_vars="dest_country", var_name="commodity", value_name=by)
    bar = px.bar(long, x="dest_country", y=by, color="commodity", title=f"Top {top_k} Destination Countries – HSN Split (stacked)")
    heat = px.imshow(top.T, aspect="auto", title=f"HSN Split Heatmap – Destination (Top {top_k} by total)")
    return bar, heat, top

def hsn_split_by_origin(df, top_k=15, by="value_norm_usd"):
    pivot = df.pivot_table(index="origin_country", columns="commodity", values=by, aggfunc="sum", fill_value=0.0)
    pivot["total"] = pivot.sum(axis=1)
    top = pivot.sort_values("total", ascending=False).head(top_k).drop(columns=["total"])
    long = top.reset_index().melt(id_vars="origin_country", var_name="commodity", value_name=by)
    bar = px.bar(long, x="origin_country", y=by, color="commodity", title=f"Top {top_k} Origin Countries – HSN Split (stacked)")
    heat = px.imshow(top.T, aspect="auto", title=f"HSN Split Heatmap – Origin (Top {top_k} by total)")
    return bar, heat, top

# ---------- main ----------
def main():
    ensure_dirs()
    install_kaleido_if_needed()

    # Read + concat all files robustly
    frames = []
    for f in FILES:
        p = Path(f)
        if not p.exists():
            print("WARNING - missing:", f)
            continue
        df_raw = read_volza(p)
        df_clean = clean_columns(df_raw)
        df_clean["source_file"] = p.name
        frames.append(df_clean)
    if not frames:
        raise FileNotFoundError("No input files could be read.")
    df = pd.concat(frames, ignore_index=True)

    # Currency normalization
    df = apply_currency_normalization(df)

    # Save combined dataset + a currency audit sample
    src_dir = OUTPUT_ROOT / "00_source"
    src_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(src_dir / "combined_dataset.csv", index=False)

    audit_cols = ["date","hs_code","product_description","qty","unit","unit_rate_usd","value_usd",
                  "currency_detected","unit_rate_norm_usd","value_norm_usd","origin_country","dest_country","source_file"]
    (df.loc[(df["currency_detected"]!="usd") | (df["unit_rate_usd"]>1e7), audit_cols]
       .head(200)
       .to_csv(src_dir / "currency_audit_sample.csv", index=False))

    # ===== Reports (now use *_norm_usd) =====
    # by_country
    f1,t1 = top_countries(df, by="value_norm_usd", role="origin")
    save_fig(f1, "by_country", "top_origin_countries_by_value")
    save_table(t1, "by_country", "top_origin_countries_by_value")

    f2,t2 = top_countries(df, by="value_norm_usd", role="dest")
    save_fig(f2, "by_country", "top_destination_countries_by_value")
    save_table(t2, "by_country", "top_destination_countries_by_value")

    f7,t7 = price_box_by_country(df)
    save_fig(f7, "by_country", "unit_price_distribution_by_destination")
    save_table(t7, "by_country", "unit_price_distribution_by_destination_raw")

    # by_country_supplier
    f3,t3 = top_suppliers_by_country(df, role="dest_country", by="value_norm_usd")
    save_fig(f3, "by_country_supplier", "supplier_treemap_by_destination")
    save_table(t3, "by_country_supplier", "supplier_treemap_by_destination")

    f8,t8 = supplier_market_reach(df)
    save_fig(f8, "by_country_supplier", "supplier_market_reach")
    save_table(t8, "by_country_supplier", "supplier_market_reach")

    # by_product
    f4,t4 = top_products(df, by="value_norm_usd")
    save_fig(f4, "by_product", "top_products_by_value")
    save_table(t4, "by_product", "top_products_by_value")

    # flows
    f6,t6 = flow_sankey(df, origins="origin_country", destinations="dest_country", by="value_norm_usd",
                        title="Trade Flows: Origin → Destination (by Value, USD normalized)")
    save_fig(f6, "flows", "sankey_origin_to_destination_value")
    save_table(t6, "flows", "sankey_origin_to_destination_value")

    # ports
    f9,t9 = port_analysis(df, role="origin_port", by="value_norm_usd")
    save_fig(f9, "ports", "top_origin_ports_by_value")
    save_table(t9, "ports", "top_origin_ports_by_value")

    f10,t10 = port_analysis(df, role="dest_port", by="value_norm_usd")
    save_fig(f10, "ports", "top_destination_ports_by_value")
    save_table(t10, "ports", "top_destination_ports_by_value")

    # trends
    f5,t5 = monthly_trend(df, by="value_norm_usd")
    save_fig(f5, "trends", "monthly_trend_by_commodity_value")
    save_table(t5, "trends", "monthly_trend_by_commodity_value")

    # scoring (attractiveness)
    f11,t11 = attractiveness_scoring(df)
    save_fig(f11, "scoring", "country_attractiveness_for_outreach")
    save_table(t11, "scoring", "country_attractiveness_scores")

    # 7311 vs 8419 splits
    bd, hd, md = hsn_split_by_destination(df, by="value_norm_usd")
    save_fig(bd, "hsn_split", "dest_country_hsn_split_stacked")
    save_fig(hd, "hsn_split", "dest_country_hsn_split_heatmap")
    save_table(md.reset_index(), "hsn_split", "dest_country_hsn_split_table")

    bo, ho, mo = hsn_split_by_origin(df, by="value_norm_usd")
    save_fig(bo, "hsn_split", "origin_country_hsn_split_stacked")
    save_fig(ho, "hsn_split", "origin_country_hsn_split_heatmap")
    save_table(mo.reset_index(), "hsn_split", "origin_country_hsn_split_table")

    # MEA Focus subset
    fc = [COUNTRY_NORMALIZATION.get(x, x) for x in FOCUS_COUNTRIES_RAW]
    df_mea = df[df["dest_country"].isin(fc)].copy()

    m1, mt1 = top_countries(df_mea, by="value_norm_usd", role="dest")
    m1.update_layout(title="MEA Focus – Top Importing Countries (by Value, USD normalized)")
    save_fig(m1, "MEA_focus", "MEA_top_importing_countries_by_value")
    save_table(mt1, "MEA_focus", "MEA_top_importing_countries_by_value")

    sk_mea, tab_sk_mea = flow_sankey(df_mea, "origin_country", "dest_country", by="value_norm_usd",
                                     title="MEA Focus – Origins → MEA Destinations (USD normalized)")
    save_fig(sk_mea, "MEA_focus", "MEA_sankey_origin_to_dest")
    save_table(tab_sk_mea, "MEA_focus", "MEA_sankey_origin_to_dest")

    tm_mea, tab_tm_mea = top_suppliers_by_country(df_mea, role="dest_country", by="value_norm_usd")
    if tm_mea:
        tm_mea.update_layout(title="MEA Focus – Supplier Footprint by Destination (Treemap)")
    save_fig(tm_mea, "MEA_focus", "MEA_supplier_treemap_by_destination")
    save_table(tab_tm_mea, "MEA_focus", "MEA_supplier_treemap_by_destination")

    if "consignee" in df_mea.columns:
        cons = (df_mea.dropna(subset=["dest_country","consignee","value_norm_usd"])
                    .groupby(["dest_country","consignee"])["value_norm_usd"]
                    .sum().reset_index()
                    .sort_values(["dest_country","value_norm_usd"], ascending=[True, False]))
        save_table(cons, "MEA_focus", "MEA_top_consignees_by_country")

    bmd, hmd, mmd = hsn_split_by_destination(df_mea, top_k=len(fc), by="value_norm_usd")
    bmd.update_layout(title="MEA – HSN Split by Destination (USD normalized, stacked)")
    hmd.update_layout(title="MEA – HSN Split Heatmap (Destination, USD normalized)")
    save_fig(bmd, "MEA_focus", "MEA_dest_country_hsn_split_stacked")
    save_fig(hmd, "MEA_focus", "MEA_dest_country_hsn_split_heatmap")
    save_table(mmd.reset_index(), "MEA_focus", "MEA_dest_country_hsn_split_table")

    # Filtered dataset for cryogenic equipment keywords
    KEYWORDS = [
        "CRYOGENIC TANK", "LIN TANK", "LOX TANK", "LAR TANK", "LNG TANK", "LIQUID OXYGEN TANK",
        "LIQUID NITROGEN TANK", "LIQUID ARGON TANK", "LIQUID CO2 TANK", "CRYOGENIC PRESSURE VESSEL",
        "LMO TANK", "LIQUID MEDICAL OXYGEN TANK", "VACUUM INSULATED CRYOGENIC TANK",
        "CRYOGENIC VAPORIZERS", "CRYOGENIC EVAPORATORS", "AIR AMBIENT VAPORIZERS",
        "CRYOGENIC FORCED DRAFT VAPORIZERS", "CRYOGENIC STEAM BATH VAPORIZERS",
        "CRYOGENIC WATER CIRCULATING VAPORIZERS", "CRYOGENIC FUEL FIRED VAPORIZERS",
        "CRYOGENIC STEAM HEATED VAPORIZERS", "LIQUEFIED NATURAL GAS TANK",
        "CRYOGENIC SHELL N TUBE VAPORIZER", "CRYOGENIC WATER BATH VAPORIZER",
        "CRYOGENIC PUMP", "CRYOGENIC HIGH PRESSURE PUMP", "LIN PUMP", "LOX PUMP",
        "LAR PUMP", "CRYOGENIC CYLINDER MANIFOLD", "cryogenic", "lin","lox","lar", "lng","liquid oxygen","liquid nitrogen",
        "liquid argon","liquid co2","liquid carbon dioxide","medical oxygen","liquid medical oxygen",
    ]

    pattern = "|".join([re.escape(k) for k in KEYWORDS])
    mask = df["product_description"].str.contains(pattern, case=False, na=False)
    df_filtered = df[mask].copy()
    df_filtered.to_csv(src_dir / "combined_dataset.csv", index=False)

    print("\nDone. See organized outputs under:", OUTPUT_ROOT.resolve())

if __name__ == "__main__":
    main()
