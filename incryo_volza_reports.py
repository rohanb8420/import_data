#!/usr/bin/env python3
"""
INCRYO – Volza HSN 7311 & 8419 Market Intelligence Reports
-----------------------------------------------------------
Reads two Excel files (7311_tanks_only.xlsx and 84195010 (1).xlsx), cleans & normalizes,
and outputs Plotly charts (PNG + interactive HTML) and CSV summary tables under ./outputs.

Author: ChatGPT (Rohan's co‑pilot)
"""

import os
import re
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

# Plotly
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

# ======== USER INPUTS ========
# Put your files in the same folder as this script, or update the paths here.
FILES = [
    "7311_tanks_only.xlsx",
    "84195010 (1).xlsx"
]

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

# How many items to show in "Top N" charts
TOP_N = 15

# Focus countries (Middle East & Africa short list provided)
FOCUS_COUNTRIES_RAW = [
    "Ghana", "Kenya", "Namibia", "Oman", "Qatar", "Saudi Arabia", "Senegal",
    "South Sudan", "Uganda", "United Arab Emirates"
]

# Country synonym map (extend as needed)
COUNTRY_NORMALIZATION = {
    "UAE": "United Arab Emirates",
    "U.A.E.": "United Arab Emirates",
    "United Arab Emirate": "United Arab Emirates",
    "KSA": "Saudi Arabia",
    "S. Arabia": "Saudi Arabia",
    "South Sudan, Republic of": "South Sudan",
    "Cote d'Ivoire": "Côte d’Ivoire",
    "Ivory Coast": "Côte d’Ivoire",
}

# =============================

def install_kaleido_if_needed():
    """
    Ensure kaleido is available for static image exports.
    """
    try:
        import kaleido  # noqa: F401
    except Exception:
        try:
            import sys, subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "kaleido"])
        except Exception as e:
            print("WARNING: Could not install kaleido automatically. Images will not be written.\n", e)

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Uniform column names
    df = df.copy()
    df.columns = [c.strip().replace("\n", " ").replace("  ", " ") for c in df.columns]
    # Standard column aliases (be tolerant to variants)
    aliases = {
        "HS Code": "hs_code",
        "HS Product Description": "hs_description",
        "Product Description": "product_description",
        "Consignee": "consignee",
        "Shipper": "shipper",
        "Std. Quantity": "std_qty",
        "Std. Unit": "std_unit",
        "Quantity": "qty",
        "Unit": "unit",
        "Unit Rate $": "unit_rate_usd",
        "Value $": "value_usd",
        "Country of Origin": "origin_country",
        "Port of Origin": "origin_port",
        "Country of Destination": "dest_country",
        "Port of Destination": "dest_port",
        "Shipment Mode": "shipment_mode",
        "Source Country": "source_country_field",
        "Date": "date",
        "Gross Weight": "gross_weight",
    }
    rename = {k: v for k, v in aliases.items() if k in df.columns}
    df = df.rename(columns=rename)

    # Date
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()

    # Numeric cleaning
    for col in ["std_qty","qty","unit_rate_usd","value_usd","gross_weight"]:
        if col in df.columns:
            df[col] = (
                df[col].astype(str)
                .str.replace(",","", regex=False)
                .str.extract(r"([-+]?\d*\.?\d+)")[0]
                .astype(float)
            )

    # Normalize countries
    for col in ["origin_country","dest_country"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace(COUNTRY_NORMALIZATION)
            # Remove junk like "OP India", "Not Available" etc.
            df[col] = df[col].replace({"OP India":"India", "N/A":"", "nan":"", "Not Available":""})
            df[col] = df[col].replace("", np.nan)

    # Direction (import/export) from "source_country_field" if available
    if "source_country_field" in df.columns:
        lowered = df["source_country_field"].astype(str).str.lower()
        df["direction"] = np.where(
            lowered.str.contains("export"), "Export",
            np.where(lowered.str.contains("import"), "Import", None)
        )
        df["direction"] = df["direction"].astype("object")

    # HS family label
    if "hs_code" in df.columns:
        df["hs2"] = df["hs_code"].astype(str).str[:2]
        df["hs4"] = df["hs_code"].astype(str).str[:4]

    # Commodity label shortcut
    df["commodity"] = np.where(df.get("hs2","").astype(str).str.startswith("73"),
                               "HSN 7311 – Tanks/Cylinders",
                               np.where(df.get("hs4","").astype(str).str.startswith("8419"),
                                        "HSN 8419 – Vaporizers & Heat‑exchange",
                                        "Other"))

    # Clean party fields
    for col in ["shipper","consignee"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.title()

    # Product/HS description
    if "product_description" in df.columns:
        df["product_description"] = df["product_description"].astype(str).str.strip().str.title()
    if "hs_description" in df.columns:
        df["hs_description"] = df["hs_description"].astype(str).str.strip()

    return df

def read_all(files):
    frames = []
    for f in files:
        if not Path(f).exists():
            print(f"WARNING: File not found -> {f}")
            continue
        # Many Volza files have the real header on second row – handle gracefully
        try:
            df0 = pd.read_excel(f, header=0)
            # Heuristic: if first row contains many NaNs in required columns, try header=1
            if df0.head(1).isna().sum().sum() > len(df0.columns) * 0.5:
                df0 = pd.read_excel(f, header=1)
        except Exception:
            df0 = pd.read_excel(f, header=1)
        df0["source_file"] = Path(f).name
        frames.append(df0)
    if not frames:
        raise FileNotFoundError("No input files could be read. Check FILES list/paths.")
    df = pd.concat(frames, ignore_index=True)
    return clean_columns(df)

def top_countries(df, by="value_usd", role="origin"):
    col = "origin_country" if role=="origin" else "dest_country"
    g = (df.groupby(col, dropna=True)[by]
           .sum()
           .sort_values(ascending=False)
           .head(TOP_N)
           .reset_index())
    g.columns = [col, by]
    title = f"Top {TOP_N} Countries by {by.replace('_',' ').title()} – {'Export (Origin)' if role=='origin' else 'Import (Destination)'}"
    fig = px.bar(g, x=col, y=by, title=title, text_auto=".2s")
    fig.update_layout(xaxis_title="Country", yaxis_title=by.replace("_"," ").title())
    return fig, g

def top_suppliers_by_country(df, role="dest_country", by="value_usd"):
    """
    role: group suppliers by 'dest_country' (import market view) or 'origin_country' (export view)
    Returns a treemap destination->shipper sized by by.
    """
    if "shipper" not in df.columns: return None, None
    keep = df.dropna(subset=[role, "shipper"])
    g = keep.groupby([role, "shipper"], dropna=True)[by].sum().reset_index()
    g = g.sort_values(by=by, ascending=False)
    title = f"Supplier Footprint by {role.replace('_',' ').title()} (Treemap)"
    fig = px.treemap(g, path=[role, "shipper"], values=by, title=title)
    return fig, g

def top_products(df, by="value_usd"):
    label = "product_description" if "product_description" in df.columns else "hs_description"
    g = (df.groupby(label, dropna=True)[by]
           .sum()
           .sort_values(ascending=False)
           .head(TOP_N)
           .reset_index())
    title = f"Top {TOP_N} Products by {by.replace('_',' ').title()}"
    fig = px.bar(g, x=label, y=by, title=title, text_auto=".2s")
    fig.update_layout(xaxis_title="Product", yaxis_title=by.replace("_"," ").title(), xaxis_tickangle=35)
    return fig, g

def monthly_trend(df, by="value_usd"):
    if "month" not in df.columns: return None, None
    g = df.groupby(["month","commodity"], dropna=True)[by].sum().reset_index()
    title = f"Monthly Trend by Commodity – {by.replace('_',' ').title()}"
    fig = px.line(g, x="month", y=by, color="commodity", markers=True, title=title)
    fig.update_layout(xaxis_title="Month", yaxis_title=by.replace("_"," ").title())
    return fig, g

def flow_sankey(df, origins="origin_country", destinations="dest_country", by="value_usd", title="Origin \u2192 Destination Flows"):
    keep = df.dropna(subset=[origins, destinations, by])
    g = keep.groupby([origins, destinations])[by].sum().reset_index()

    # Build Sankey nodes and links
    origin_nodes = list(g[origins].unique())
    dest_nodes = list(g[destinations].unique())
    nodes = origin_nodes + dest_nodes
    node_index = {n:i for i,n in enumerate(nodes)}
    source = g[origins].map(node_index).tolist()
    target = g[destinations].map(lambda d: node_index[d]).tolist()
    value = g[by].tolist()

    sankey = go.Figure(go.Sankey(
        node=dict(pad=10, thickness=15, line=dict(width=0.5, color="gray"),
                  label=nodes),
        link=dict(source=source, target=target, value=value)
    ))
    sankey.update_layout(title=title)
    return sankey, g

def price_box_by_country(df):
    if "unit_rate_usd" not in df.columns: return None, None
    keep = df.dropna(subset=["dest_country","unit_rate_usd"])
    g = keep.copy()
    title = "Unit Price Distribution by Destination Country (USD)"
    fig = px.box(g, x="dest_country", y="unit_rate_usd", points=False, title=title)
    fig.update_layout(xaxis_title="Destination Country", yaxis_title="Unit Rate (USD)")
    return fig, g

def supplier_market_reach(df):
    if "shipper" not in df.columns: return None, None
    g = (df.dropna(subset=["shipper","dest_country"])[["shipper","dest_country"]]
           .drop_duplicates()
           .groupby("shipper").size().reset_index(name="num_countries"))
    g = g.sort_values("num_countries", ascending=False).head(TOP_N)
    fig = px.bar(g, x="shipper", y="num_countries", title="Suppliers by Number of Destination Countries (Reach)", text_auto=True)
    fig.update_layout(xaxis_title="Supplier", yaxis_title="# Destination Countries")
    return fig, g

def port_analysis(df, role="dest_port", by="value_usd"):
    if role not in df.columns: return None, None
    g = (df.dropna(subset=[role])[by]
           .groupby(df[role])
           .sum()
           .sort_values(ascending=False)
           .head(TOP_N).reset_index())
    g.columns = [role, by]
    title = f"Top {TOP_N} Ports by {by.replace('_',' ').title()} – {role.replace('_',' ').title()}"
    fig = px.bar(g, x=role, y=by, title=title, text_auto=".2s")
    fig.update_layout(xaxis_title=role.replace("_"," ").title(), yaxis_title=by.replace("_"," ").title())
    return fig, g

def attractiveness_scoring(df):
    """
    Country attractiveness for outreach (Import market view):
    Score = z(growth) + z(avg_price) + z(log(value)) - z(supplier_concentration)
    where supplier_concentration = HHI of supplier shares in that country.
    """
    if "month" not in df.columns:
        return None, None
    by = "value_usd"
    d = df.dropna(subset=["dest_country"])
    # Monthly totals by country
    m = d.groupby(["dest_country","month"])[by].sum().reset_index()
    # CAGR (approx via first vs last month)
    first = m.sort_values("month").groupby("dest_country")[by].first()
    last  = m.sort_values("month").groupby("dest_country")[by].last()
    growth = ((last - first) / (first.replace(0, np.nan))).replace([np.inf, -np.inf], np.nan)

    # Avg price by country
    if "unit_rate_usd" in d.columns:
        avg_price = d.groupby("dest_country")["unit_rate_usd"].mean()
    else:
        avg_price = pd.Series(index=growth.index, dtype=float)

    # Total import value
    total_val = d.groupby("dest_country")[by].sum()

    # Supplier concentration (HHI)
    shares = (d.groupby(["dest_country","shipper"])[by].sum()
                .groupby(level=0).apply(lambda s: (s/s.sum())**2).groupby(level=0).sum())
    hhi = shares.reindex(growth.index)

    # Combine
    score = pd.DataFrame({
        "growth": growth,
        "avg_price": avg_price,
        "total_value": total_val,
        "supplier_hhi": hhi
    })

    # Z-scores
    for c in ["growth","avg_price","total_value","supplier_hhi"]:
        score[c+"_z"] = (score[c] - score[c].mean()) / (score[c].std(ddof=0) + 1e-9)

    score["attractiveness"] = score["growth_z"] + score["avg_price_z"] + score["total_value_z"] - score["supplier_hhi_z"]
    score = score.dropna(subset=["attractiveness"]).sort_values("attractiveness", ascending=False)

    # Plot
    fig = px.bar(
        score.head(TOP_N).reset_index(), x="dest_country", y="attractiveness",
        title="Top Countries to Target – Attractiveness Score",
        text_auto=".2f"
    )
    fig.update_layout(xaxis_title="Destination Country", yaxis_title="Attractiveness (Z‑score composite)")

    return fig, score.reset_index()

def focus_mea(df, focus_countries):
    # Normalize focus list through our normalization map
    fc = [COUNTRY_NORMALIZATION.get(x, x) for x in focus_countries]
    d = df[df["dest_country"].isin(fc)].copy()
    return d

def save_fig(fig, name):
    if fig is None: 
        return
    # Save HTML and PNG (if kaleido available)
    html_path = OUTPUT_DIR / f"{name}.html"
    png_path  = OUTPUT_DIR / f"{name}.png"
    fig.write_html(str(html_path))
    try:
        fig.write_image(str(png_path), scale=2, width=1400, height=800)
    except Exception as e:
        print(f"NOTE: Could not save PNG for {name}. Install 'kaleido' if you need static images. Error: {e}")
    print("Saved:", html_path.name, "and", png_path.name)

def save_table(df, name):
    if df is None: return
    out = OUTPUT_DIR / f"{name}.csv"
    df.to_csv(out, index=False)
    print("Saved table:", out.name)

def main():
    install_kaleido_if_needed()
    df = read_all(FILES)

    # ---------- CORE REPORTS ----------
    fig1, tab1 = top_countries(df, by="value_usd", role="origin")
    save_fig(fig1, "top_origin_countries_by_value")
    save_table(tab1, "top_origin_countries_by_value")

    fig2, tab2 = top_countries(df, by="value_usd", role="dest")
    save_fig(fig2, "top_destination_countries_by_value")
    save_table(tab2, "top_destination_countries_by_value")

    fig3, tab3 = top_suppliers_by_country(df, role="dest_country", by="value_usd")
    save_fig(fig3, "supplier_treemap_by_destination")
    save_table(tab3, "supplier_treemap_by_destination")

    fig4, tab4 = top_products(df, by="value_usd")
    save_fig(fig4, "top_products_by_value")
    save_table(tab4, "top_products_by_value")

    fig5, tab5 = monthly_trend(df, by="value_usd")
    save_fig(fig5, "monthly_trend_by_commodity_value")
    save_table(tab5, "monthly_trend_by_commodity_value")

    fig6, tab6 = flow_sankey(df, origins="origin_country", destinations="dest_country",
                             by="value_usd", title="Trade Flows: Origin → Destination (by Value)")
    save_fig(fig6, "sankey_origin_to_destination_value")
    save_table(tab6, "sankey_origin_to_destination_value")

    fig7, tab7 = price_box_by_country(df)
    save_fig(fig7, "unit_price_distribution_by_destination")
    save_table(tab7, "unit_price_distribution_by_destination")

    fig8, tab8 = supplier_market_reach(df)
    save_fig(fig8, "supplier_market_reach")
    save_table(tab8, "supplier_market_reach")

    fig9, tab9 = port_analysis(df, role="origin_port", by="value_usd")
    save_fig(fig9, "top_origin_ports_by_value")
    save_table(tab9, "top_origin_ports_by_value")

    fig10, tab10 = port_analysis(df, role="dest_port", by="value_usd")
    save_fig(fig10, "top_destination_ports_by_value")
    save_table(tab10, "top_destination_ports_by_value")

    fig11, tab11 = attractiveness_scoring(df)
    save_fig(fig11, "country_attractiveness_for_outreach")
    save_table(tab11, "country_attractiveness_scores")

    # ---------- MEA SPECIAL ----------
    df_mea = focus_mea(df, FOCUS_COUNTRIES_RAW)

    # Top importing countries (MEA subset)
    mea_top_import_fig, mea_top_import_tab = top_countries(df_mea, by="value_usd", role="dest")
    mea_top_import_fig.update_layout(title="MEA Focus – Top Importing Countries (by Value)")
    save_fig(mea_top_import_fig, "MEA_top_importing_countries_by_value")
    save_table(mea_top_import_tab, "MEA_top_importing_countries_by_value")

    # Who they import from (origin → destination Sankey, restricted to MEA destinations)
    sankey_mea, tab_sankey_mea = flow_sankey(df_mea, "origin_country", "dest_country",
                                             by="value_usd", title="MEA Focus – Origins → MEA Destinations")
    save_fig(sankey_mea, "MEA_sankey_origin_to_dest")
    save_table(tab_sankey_mea, "MEA_sankey_origin_to_dest")

    # Top suppliers serving each MEA country
    if "shipper" in df.columns:
        tm_mea, tab_tm_mea = top_suppliers_by_country(df_mea, role="dest_country", by="value_usd")
        if tm_mea:
            tm_mea.update_layout(title="MEA Focus – Supplier Footprint by Destination (Treemap)")
        save_fig(tm_mea, "MEA_supplier_treemap_by_destination")
        save_table(tab_tm_mea, "MEA_supplier_treemap_by_destination")

    # Optional: CSV for outreach – Top consignees per MEA country
    if "consignee" in df.columns:
        cons = (df_mea.dropna(subset=["dest_country","consignee","value_usd"])
                    .groupby(["dest_country","consignee"])["value_usd"]
                    .sum().reset_index()
                    .sort_values(["dest_country","value_usd"], ascending=[True, False]))
        cons_out = OUTPUT_DIR / "MEA_top_consignees_by_country.csv"
        cons.to_csv(cons_out, index=False)
        print("Saved table:", cons_out.name)

    print("\nAll done. Check the 'outputs' folder for PNGs, HTML, and CSVs.\n")

if __name__ == "__main__":
    main()
