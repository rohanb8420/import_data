"""
Streamlit dashboard for HSN 7311 and related datasets.

This application loads two Excel files (each containing
"Global Search Data" in a sheet, with the actual header on
the second row) and concatenates them.  It normalizes and
standardizes columns, then provides interactive filters and
several views:

1. **Holistic List** – a full table with all records, filterable
   by date, HS code, destination country, supplier/shipper,
   consignee, value, quantity, and free‑text search in
   description.  The table supports pagination and download.

2. **Country Analysis** – totals by destination country with
   a table and bar charts for top countries by value and
   quantity.

3. **Suppliers & Consignees** – tables and charts for
   aggregated totals by shipper (supplier) and consignee,
   along with supplier footprint across countries and
   consignee mix per country.

4. **Descriptions (Deep Dive)** – totals by product/description
   with bar charts for the top descriptions by value and
   quantity.

5. **Per‑Country Description Mix** – lets you drill into a
   country and explore its product mix by value and quantity,
   with chart and table, and download option.

To run the dashboard:

    pip install streamlit pandas openpyxl plotly
    streamlit run app.py

Place the Excel files in the same directory (e.g., 7311.xlsx
and 84195010 (1).xlsx).  You can also override file paths
using the sidebar inputs.
"""

import re
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

SHEET_NAME = "Global Search Data"
HEADER_ROW = 1  # second row (index=1) contains column names


def hs4_from_any(x: str) -> str | None:
    """Extract the first four digits from an HS code string."""
    if pd.isna(x):
        return None
    s = str(x)
    digits = re.findall(r"\d+", s)
    return digits[0][:4] if digits else None


@st.cache_data(show_spinner=False)
def load_file(path: Path) -> pd.DataFrame:
    """Load the specified sheet from an Excel file."""
    xls = pd.ExcelFile(path)
    if SHEET_NAME not in xls.sheet_names:
        st.warning(f"'{SHEET_NAME}' not found in {path.name}. Sheets: {xls.sheet_names}")
        return pd.DataFrame()
    df = xls.parse(SHEET_NAME, header=HEADER_ROW)
    return df


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and standardize raw data columns."""
    if df.empty:
        return df
    rename_map = {
        "Date": "date",
        "HS Code": "hs_code",
        "Product Description": "product_description",
        "HS Product Description": "hs_product_description",
        "Consignee": "consignee",
        "Consignee Address": "consignee_address",
        "Shipper": "shipper",
        "Shipper Address": "shipper_address",
        "Country of Origin": "country_origin",
        "Country of Destination": "country_destination",
        "Port of Origin": "port_origin",
        "Port of Destination": "port_destination",
        "Quantity": "quantity",
        "Std. Quantity": "std_quantity",
        "Unit": "unit",
        "Std. Unit": "std_unit",
        "Unit Rate $": "unit_rate_usd",
        "Value $": "value_usd",
        "Shipment Mode": "shipment_mode",
        "Bill of Lading": "bill_of_lading",
        "Source Country": "source_country",
        "Gross Weight": "gross_weight",
        "Container TEU": "container_teu",
        "Freight Term": "freight_term",
        "Marks Number": "marks_number",
        "Measurment": "measurement",
        "Actual Duty": "actual_duty",
        "Port of Delivery": "port_delivery",
        "Notify Party Name": "notify_party",
        "Notify Party Address": "notify_party_address",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    keep_cols = [
        "date", "hs_code", "product_description", "hs_product_description",
        "consignee", "consignee_address", "shipper", "shipper_address",
        "country_origin", "country_destination", "port_origin", "port_destination",
        "quantity", "std_quantity", "unit", "std_unit", "unit_rate_usd", "value_usd",
        "gross_weight", "shipment_mode", "bill_of_lading", "source_country",
        "container_teu", "freight_term", "marks_number", "measurement", "actual_duty",
        "port_delivery", "notify_party", "notify_party_address"
    ]
    present = [c for c in keep_cols if c in df.columns]
    df = df[present].copy()

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["year"] = df["date"].dt.year
        df["month"] = df["date"].dt.month

    for col in ["quantity", "std_quantity", "unit_rate_usd", "value_usd", "gross_weight"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in [
        "country_origin", "country_destination", "consignee", "shipper",
        "product_description", "hs_product_description", "unit", "std_unit",
        "port_origin", "port_destination", "source_country", "shipment_mode",
        "bill_of_lading", "freight_term", "container_teu"
    ]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            df.loc[df[col].str.lower().isin({"nan", "none", "not available", ""}), col] = np.nan

    if "hs_code" in df.columns:
        df["hs4"] = df["hs_code"].map(hs4_from_any)

    if "product_description" in df.columns and "hs_product_description" in df.columns:
        df["description_any"] = df["product_description"].fillna(df["hs_product_description"])
    elif "product_description" in df.columns:
        df["description_any"] = df["product_description"]
    elif "hs_product_description" in df.columns:
        df["description_any"] = df["hs_product_description"]

    row_key_cols = [c for c in ["date", "bill_of_lading", "consignee", "shipper", "quantity", "value_usd"] if c in df.columns]
    if row_key_cols:
        df["row_key"] = pd.util.hash_pandas_object(df[row_key_cols].astype(str), index=False).astype(str)
    else:
        df["row_key"] = pd.util.hash_pandas_object(df.astype(str), index=False).astype(str)
    return df


def concat_sources(files: list[Path]) -> pd.DataFrame:
    """Concatenate and clean multiple Excel datasets."""
    frames: list[pd.DataFrame] = []
    for p in files:
        if p and p.exists():
            frames.append(normalize_columns(load_file(p)))
    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.drop_duplicates(subset=["row_key"]) if "row_key" in all_df.columns else all_df
    return all_df


def filter_block(df: pd.DataFrame) -> pd.DataFrame:
    """Sidebar filter controls returning a filtered DataFrame."""
    if df.empty:
        return df

    st.sidebar.header("Filters")
    # File path overrides for convenience
    st.sidebar.caption("Point to files if different from defaults")
    f1 = st.sidebar.text_input("Excel File 1", "7311_tanks_only.xlsx")
    f2 = st.sidebar.text_input("Excel File 2", "84195010 (1).xlsx")
    st.sidebar.divider()

    # Date range filter
    min_date, max_date = (
        (df["date"].min(), df["date"].max()) if "date" in df.columns else (None, None)
    )
    if min_date and max_date:
        date_range = st.sidebar.date_input(
            "Date range", value=(min_date.date(), max_date.date())
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start, end = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
            df = df[(df["date"] >= start) & (df["date"] <= end)]

    # HS filter
    if "hs4" in df.columns:
        hs4_opts = sorted([x for x in df["hs4"].dropna().unique()])
        hs4_sel = st.sidebar.multiselect(
            "HS4 filter", hs4_opts, default=hs4_opts
        )
        if hs4_sel:
            df = df[df["hs4"].isin(hs4_sel)]

    # Destination country filter
    if "country_destination" in df.columns:
        countries = sorted([x for x in df["country_destination"].dropna().unique()])
        country_sel = st.sidebar.multiselect(
            "Countries (Destination)", countries, default=countries[: min(len(countries), 12)]
        )
        if country_sel:
            df = df[df["country_destination"].isin(country_sel)]

    # Supplier filter
    if "shipper" in df.columns:
        shippers = sorted([x for x in df["shipper"].dropna().unique()])
        shipper_sel = st.sidebar.multiselect("Suppliers/Shippers", shippers)
        if shipper_sel:
            df = df[df["shipper"].isin(shipper_sel)]

    # Consignee filter
    if "consignee" in df.columns:
        cons = sorted([x for x in df["consignee"].dropna().unique()])
        cons_sel = st.sidebar.multiselect("Consignees", cons)
        if cons_sel:
            df = df[df["consignee"].isin(cons_sel)]

    # Free text search in description
    search = st.sidebar.text_input("Search (in any description)")
    if search and "description_any" in df.columns:
        s = search.lower()
        df = df[df["description_any"].astype(str).str.lower().str.contains(s)]

    # Value range filter
    if "value_usd" in df.columns:
        vmin, vmax = float(df["value_usd"].min() or 0), float(df["value_usd"].max() or 0)
        vsel = st.sidebar.slider("Value USD range", vmin, vmax, (vmin, vmax))
        df = df[
            (df["value_usd"].fillna(0) >= vsel[0])
            & (df["value_usd"].fillna(0) <= vsel[1])
        ]

    # Quantity range filter
    if "quantity" in df.columns:
        qmin, qmax = float(df["quantity"].min() or 0), float(df["quantity"].max() or 0)
        qsel = st.sidebar.slider("Quantity range", qmin, qmax, (qmin, qmax))
        df = df[
            (df["quantity"].fillna(0) >= qsel[0])
            & (df["quantity"].fillna(0) <= qsel[1])
        ]

    # Sort options
    sort_cols = [c for c in df.columns if df[c].dtype != "object"] + ["date"]
    sort_col = st.sidebar.selectbox("Sort by", sort_cols)
    ascending = st.sidebar.checkbox("Ascending", value=False)
    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=ascending)

    # Persist file paths for re‑loading
    st.session_state["file1"] = f1
    st.session_state["file2"] = f2
    return df


def download_button(df: pd.DataFrame, label: str, filename: str) -> None:
    """Render a Streamlit download button for a DataFrame."""
    st.download_button(
        label=label,
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
    )


def topn_series(df: pd.DataFrame, group: str, value: str, n: int = 20) -> pd.Series:
    """Return the top n sums of a value column grouped by another column."""
    if df.empty or group not in df.columns or value not in df.columns:
        return pd.Series(dtype=float)
    return (
        df.groupby(group)[value].sum(min_count=1)
        .sort_values(ascending=False)
        .head(n)
    )


def paginate_df(df: pd.DataFrame, page_size: int = 50) -> None:
    """Display a DataFrame with pagination controls in Streamlit."""
    if "page" not in st.session_state:
        st.session_state.page = 1
    total = len(df)
    pages = max(1, int(np.ceil(total / page_size)))
    cols = st.columns([1, 1, 1, 4])
    with cols[0]:
        if st.button(
            "⟵ Prev", use_container_width=True, disabled=(st.session_state.page <= 1)
        ):
            st.session_state.page = max(1, st.session_state.page - 1)
    with cols[1]:
        st.markdown(f"**Page {st.session_state.page} / {pages}**")
    with cols[2]:
        if st.button(
            "Next ⟶", use_container_width=True, disabled=(st.session_state.page >= pages)
        ):
            st.session_state.page = min(pages, st.session_state.page + 1)
    start = (st.session_state.page - 1) * page_size
    end = start + page_size
    st.dataframe(df.iloc[start:end], use_container_width=True, height=480)


def main() -> None:
    st.set_page_config(
        page_title="HSN 7311 Global Import Dashboard", layout="wide"
    )
    st.title("HSN 7311 Global Import Dashboard")
    st.caption(
        "Holistic explorer + deep analysis for countries, suppliers, consignees, and product descriptions."
    )

    # Load default files; allow user override in sidebar
    default_f1 = Path(st.session_state.get("file1", "7311_tanks_only.xlsx"))
    default_f2 = Path(st.session_state.get("file2", "84195010 (1).xlsx"))
    df_all = concat_sources([default_f1, default_f2])

    if df_all.empty:
        st.warning(
            "No data loaded. Ensure the Excel files are present and named as above, or point to them in the sidebar."
        )
        return

    filtered = filter_block(df_all)

    # Tabs
    st.divider()
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        [
            "Holistic List",
            "Countries",
            "Suppliers & Consignees",
            "Descriptions (Deep Dive)",
            "Per‑Country Description Mix",
            "Trend Analysis",
        ]
    )

    # Tab 1: Holistic list
    with tab1:
        st.subheader("Holistic List (filterable, pageable, downloadable)")
        show_cols = [
            c
            for c in [
                "date",
                "hs_code",
                "hs4",
                "country_origin",
                "country_destination",
                "port_origin",
                "port_destination",
                "shipper",
                "consignee",
                "description_any",
                "product_description",
                "hs_product_description",
                "quantity",
                "std_quantity",
                "unit",
                "std_unit",
                "unit_rate_usd",
                "value_usd",
                "gross_weight",
                "shipment_mode",
                "bill_of_lading",
                "source_country",
                "container_teu",
                "freight_term",
            ]
            if c in filtered.columns
        ]
        holistic = filtered[show_cols].copy()
        if {"value_usd", "quantity"}.issubset(holistic.columns):
            holistic["implied_unit_rate"] = holistic["value_usd"] / holistic["quantity"]
        c1, c2 = st.columns([5, 1])
        with c1:
            paginate_df(holistic.fillna(""), page_size=50)
        with c2:
            download_button(holistic, "Download CSV", "holistic_list.csv")

    # Tab 2: Countries
    with tab2:
        st.subheader("Country Analysis")
        left, right = st.columns([2, 3], gap="large")
        by_country = (
            filtered.groupby("country_destination")[["value_usd", "quantity"]]
            .sum(min_count=1)
            .reset_index()
            .sort_values("value_usd", ascending=False)
        )
        if {"value_usd", "quantity"}.issubset(by_country.columns):
            by_country["avg_unit_rate"] = by_country["value_usd"] / by_country["quantity"]
        with left:
            st.markdown("**Totals by Country**")
            st.dataframe(by_country, use_container_width=True, height=420)
            download_button(by_country, "Download Country Totals", "by_country_totals.csv")
        with right:
            st.markdown("**Top Countries by Value**")
            s_val = topn_series(filtered, "country_destination", "value_usd", n=20)
            if not s_val.empty:
                fig = px.bar(s_val, title="Top Countries by Value (USD)")
                st.plotly_chart(fig, use_container_width=True)
            st.markdown("**Top Countries by Quantity**")
            s_qty = topn_series(filtered, "country_destination", "quantity", n=20)
            if not s_qty.empty:
                fig2 = px.bar(s_qty, title="Top Countries by Quantity")
                st.plotly_chart(fig2, use_container_width=True)

    # Tab 3: Suppliers & Consignees
    with tab3:
        st.subheader("Suppliers (Shippers) and Consignees")
        colL, colR = st.columns(2, gap="large")
        if "shipper" in filtered.columns:
            ship_tot = (
                filtered.groupby("shipper")[["value_usd", "quantity"]]
                .sum(min_count=1)
                .reset_index()
                .sort_values("value_usd", ascending=False)
            )
            if {"value_usd", "quantity"}.issubset(ship_tot.columns):
                ship_tot["avg_unit_rate"] = ship_tot["value_usd"] / ship_tot["quantity"]
            with colL:
                st.markdown("**Suppliers / Shippers – Totals**")
                st.dataframe(ship_tot, use_container_width=True, height=420)
                download_button(ship_tot, "Download Shipper Totals", "by_shipper_totals.csv")
                s = topn_series(filtered, "shipper", "value_usd", n=20)
                if not s.empty:
                    st.plotly_chart(px.bar(s, title="Top Shippers by Value"), use_container_width=True)
        if "consignee" in filtered.columns:
            cons_tot = (
                filtered.groupby("consignee")[["value_usd", "quantity"]]
                .sum(min_count=1)
                .reset_index()
                .sort_values("value_usd", ascending=False)
            )
            if {"value_usd", "quantity"}.issubset(cons_tot.columns):
                cons_tot["avg_unit_rate"] = cons_tot["value_usd"] / cons_tot["quantity"]
            with colR:
                st.markdown("**Consignees – Totals**")
                st.dataframe(cons_tot, use_container_width=True, height=420)
                download_button(cons_tot, "Download Consignee Totals", "by_consignee_totals.csv")
                s2 = topn_series(filtered, "consignee", "value_usd", n=20)
                if not s2.empty:
                    st.plotly_chart(px.bar(s2, title="Top Consignees by Value"), use_container_width=True)
        # --- Supplier Mix by Country ---
        st.markdown("**Supplier Mix by Country (select country to view suppliers)**")
        if {"shipper", "country_destination"}.issubset(filtered.columns):
            countries = sorted([x for x in filtered["country_destination"].dropna().unique()])
            pick_country = st.selectbox("Choose Country for Supplier Mix", countries, key="supplier_mix_country")
            sub_sup = filtered[filtered["country_destination"] == pick_country].copy()
            sup_mix = (
                sub_sup.groupby("shipper")[["value_usd", "quantity"]]
                .sum(min_count=1)
                .reset_index()
                .sort_values("value_usd", ascending=False)
            )
            if {"value_usd", "quantity"}.issubset(sup_mix.columns):
                sup_mix["avg_unit_rate"] = sup_mix["value_usd"] / sup_mix["quantity"]
            st.dataframe(sup_mix, use_container_width=True, height=420)
            download_button(sup_mix, f"Download Supplier Mix for {pick_country}", f"supplier_mix_{pick_country}.csv")
            if not sup_mix.empty:
                st.plotly_chart(
                    px.bar(sup_mix.head(30), x="shipper", y="value_usd", title=f"Top Suppliers by Value – {pick_country}"),
                    use_container_width=True,
                )
                st.plotly_chart(
                    px.bar(sup_mix.sort_values("quantity", ascending=False).head(30), x="shipper", y="quantity", title=f"Top Suppliers by Quantity – {pick_country}"),
                    use_container_width=True,
                )

        # --- Consignee Mix by Country ---
        st.markdown("**Consignee Mix by Country (select country to view consignees)**")
        if {"consignee", "country_destination"}.issubset(filtered.columns):
            countries2 = sorted([x for x in filtered["country_destination"].dropna().unique()])
            pick_country2 = st.selectbox("Choose Country for Consignee Mix", countries2, key="consignee_mix_country")
            sub_cons = filtered[filtered["country_destination"] == pick_country2].copy()
            cons_mix = (
                sub_cons.groupby("consignee")[["value_usd", "quantity"]]
                .sum(min_count=1)
                .reset_index()
                .sort_values("value_usd", ascending=False)
            )
            if {"value_usd", "quantity"}.issubset(cons_mix.columns):
                cons_mix["avg_unit_rate"] = cons_mix["value_usd"] / cons_mix["quantity"]
            st.dataframe(cons_mix, use_container_width=True, height=420)
            download_button(cons_mix, f"Download Consignee Mix for {pick_country2}", f"consignee_mix_{pick_country2}.csv")
            if not cons_mix.empty:
                st.plotly_chart(
                    px.bar(cons_mix.head(30), x="consignee", y="value_usd", title=f"Top Consignees by Value – {pick_country2}"),
                    use_container_width=True,
                )
                st.plotly_chart(
                    px.bar(cons_mix.sort_values("quantity", ascending=False).head(30), x="consignee", y="quantity", title=f"Top Consignees by Quantity – {pick_country2}"),
                    use_container_width=True,
                )

    # Tab 4: Descriptions (Deep Dive)
    with tab4:
        st.subheader("Deep Dive by Description / Product")
        if "description_any" in filtered.columns:
            by_desc = (
                filtered.groupby("description_any")[["value_usd", "quantity"]]
                .sum(min_count=1)
                .reset_index()
                .sort_values("value_usd", ascending=False)
            )
            if {"value_usd", "quantity"}.issubset(by_desc.columns):
                by_desc["avg_unit_rate"] = by_desc["value_usd"] / by_desc["quantity"]
            st.markdown("**Totals by Description**")
            st.dataframe(by_desc, use_container_width=True, height=420)
            download_button(by_desc, "Download Description Totals", "by_description_totals.csv")
            colA, colB = st.columns(2)
            with colA:
                top_val = by_desc.head(25)
                if not top_val.empty:
                    st.plotly_chart(
                        px.bar(top_val, x="description_any", y="value_usd", title="Top Descriptions by Value"),
                        use_container_width=True,
                    )
            with colB:
                by_desc_q = by_desc.sort_values("quantity", ascending=False).head(25)
                if not by_desc_q.empty:
                    st.plotly_chart(
                        px.bar(by_desc_q, x="description_any", y="quantity", title="Top Descriptions by Quantity"),
                        use_container_width=True,
                    )
        else:
            st.info("No description fields present.")

    # Tab 5: Per-Country Description Mix
    with tab5:
        st.subheader("Per-Country Description Mix (who buys what)")
        if {"country_destination", "description_any"}.issubset(filtered.columns):
            countries = sorted([x for x in filtered["country_destination"].dropna().unique()])
            pick = st.selectbox("Choose Country", countries)
            sub = filtered[filtered["country_destination"] == pick].copy()
            mix_val = (
                sub.groupby("description_any")[["value_usd", "quantity"]]
                .sum(min_count=1)
                .reset_index()
                .sort_values("value_usd", ascending=False)
            )
            if {"value_usd", "quantity"}.issubset(mix_val.columns):
                mix_val["avg_unit_rate"] = mix_val["value_usd"] / mix_val["quantity"]
            st.markdown(f"**{pick} – Description Mix**")
            st.dataframe(mix_val, use_container_width=True, height=420)
            download_button(mix_val, f"Download {pick} Mix", f"desc_mix_{pick}.csv")
            if not mix_val.empty:
                st.plotly_chart(
                    px.bar(mix_val.head(30), x="description_any", y="value_usd", title=f"Top Descriptions by Value – {pick}"),
                    use_container_width=True,
                )
                st.plotly_chart(
                    px.bar(
                        mix_val.sort_values("quantity", ascending=False).head(30),
                        x="description_any",
                        y="quantity",
                        title=f"Top Descriptions by Quantity – {pick}",
                    ),
                    use_container_width=True,
                )
                # --- Bubble chart: Value vs Quantity by Description ---
                bubble_df = mix_val.copy()
                bubble_df = bubble_df[
                    bubble_df["quantity"].notna() &
                    bubble_df["value_usd"].notna()
                ]
                # Clean avg_unit_rate for bubble size
                if "avg_unit_rate" in bubble_df.columns:
                    bubble_df = bubble_df[
                        bubble_df["avg_unit_rate"].notna() &
                        (bubble_df["avg_unit_rate"] > 0) &
                        np.isfinite(bubble_df["avg_unit_rate"])
                    ]
                    size_col = "avg_unit_rate"
                else:
                    size_col = None
                if not bubble_df.empty:
                    fig_bubble = px.scatter(
                        bubble_df,
                        x="quantity",
                        y="value_usd",
                        size=size_col,
                        color="description_any",
                        hover_name="description_any",
                        title=f"Value vs Quantity by Description – {pick}",
                        size_max=40,
                    )
                    fig_bubble.update_layout(
                        xaxis_title="Quantity",
                        yaxis_title="Value (USD)",
                        legend_title="Description",
                    )
                    st.plotly_chart(fig_bubble, use_container_width=True)
        else:
            st.info("Missing columns to compute per-country mix.")

    # Tab 6: Trend Analysis
    with tab6:
        st.subheader("Trend Analysis by Country, Supplier, and Description")
        # Ensure there is a date column to compute time series
        if "date" not in filtered.columns or filtered["date"].isna().all():
            st.info("Date column missing or empty; cannot compute trends.")
        else:
            # Create a year_month field if not present
            if "year_month" not in filtered.columns:
                filtered["year_month"] = filtered["date"].dt.to_period("M").astype(str)
            # Choose a country to analyze
            country_opts = sorted(
                [c for c in filtered["country_destination"].dropna().unique()]
            )
            if not country_opts:
                st.info("No country data available.")
            else:
                sel_country = st.selectbox(
                    "Select country for trend analysis", country_opts
                )
                data_country = filtered[
                    filtered["country_destination"] == sel_country
                ].copy()
                if data_country.empty:
                    st.info(f"No data available for {sel_country}.")
                else:
                    # Choose metric to plot
                    metric = st.radio(
                        "Metric", ["Quantity", "Value (USD)"], index=0
                    )
                    metric_col = "quantity" if metric == "Quantity" else "value_usd"
                    # Choose number of top series to display
                    top_n = st.slider(
                        "Number of top items", 2, 10, value=5
                    )
                    # Choose analysis granularity: Supplier or Description
                    analysis_type = st.radio(
                        "Trend by", ["Supplier", "Description"], index=0
                    )
                    if analysis_type == "Supplier":
                        # Aggregate by year_month and shipper
                        grouped = (
                            data_country.groupby(["year_month", "shipper"])[
                                metric_col
                            ]
                            .sum(min_count=1)
                            .reset_index()
                        )
                        # Determine top suppliers by total metric
                        top_entities = (
                            data_country.groupby("shipper")[metric_col]
                            .sum()
                            .sort_values(ascending=False)
                            .head(top_n)
                            .index.tolist()
                        )
                        grouped = grouped[grouped["shipper"].isin(top_entities)]
                        # Pivot for plotting
                        pivot = (
                            grouped.pivot(
                                index="year_month", columns="shipper", values=metric_col
                            )
                            .fillna(0)
                            .sort_index()
                        )
                        if not pivot.empty:
                            fig = px.line(
                                pivot,
                                x=pivot.index,
                                y=pivot.columns,
                                markers=True,
                                title=f"{metric} Trend by Top {top_n} Suppliers in {sel_country}",
                            )
                            fig.update_layout(
                                xaxis_title="Year-Month",
                                yaxis_title=metric,
                                legend_title="Supplier",
                            )
                            st.plotly_chart(fig, use_container_width=True)
                        st.markdown("**Top Suppliers (Total)**")
                        total_tbl = (
                            data_country.groupby("shipper")[metric_col]
                            .sum()
                            .reset_index()
                            .sort_values(metric_col, ascending=False)
                            .head(top_n)
                        )
                        st.dataframe(
                            total_tbl,
                            use_container_width=True,
                            height=300,
                        )
                    else:
                        # Aggregate by year_month and description
                        grouped = (
                            data_country.groupby(["year_month", "description_any"])[
                                metric_col
                            ]
                            .sum(min_count=1)
                            .reset_index()
                        )
                        # Determine top descriptions by total metric
                        top_entities = (
                            data_country.groupby("description_any")[metric_col]
                            .sum()
                            .sort_values(ascending=False)
                            .head(top_n)
                            .index.tolist()
                        )
                        grouped = grouped[
                            grouped["description_any"].isin(top_entities)
                        ]
                        pivot = (
                            grouped.pivot(
                                index="year_month", columns="description_any", values=metric_col
                            )
                            .fillna(0)
                            .sort_index()
                        )
                        if not pivot.empty:
                            fig = px.line(
                                pivot,
                                x=pivot.index,
                                y=pivot.columns,
                                markers=True,
                                title=f"{metric} Trend by Top {top_n} Descriptions in {sel_country}",
                            )
                            fig.update_layout(
                                xaxis_title="Year-Month",
                                yaxis_title=metric,
                                legend_title="Description",
                            )
                            st.plotly_chart(fig, use_container_width=True)
                        st.markdown("**Top Descriptions (Total)**")
                        total_tbl = (
                            data_country.groupby("description_any")[metric_col]
                            .sum()
                            .reset_index()
                            .sort_values(metric_col, ascending=False)
                            .head(top_n)
                        )
                        st.dataframe(
                            total_tbl,
                            use_container_width=True,
                            height=300,
                        )
                # Helper caption at the bottom
                st.caption(
                    "Change the number of top items or choose a different metric for deeper trend insights."
                )
    # Footer caption for entire app
    st.caption(
        "Tip: use the sidebar to refine the holistic list first, then explore drill-down tabs. All tables are downloadable."
    )


if __name__ == "__main__":
    main()