"""
Streamlit app for exploring the combined dataset.

- Reads a single CSV at `outputs/00_source/combined_dataset.csv`.
- Normalizes column names and types to a consistent schema.
- Provides filters and multiple analysis views (countries, suppliers, descriptions, trends).

Run:
    pip install -r requirements.txt
    streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st


# ---------- Paths ----------
ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = ROOT / "outputs" / "00_source" / "combined_dataset.csv"


# ---------- Helpers ----------
def _to_float(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    if s.isna().all():
        # attempt to strip commas and non-numeric tokens
        s = pd.to_numeric(
            series.astype(str).str.replace(",", "", regex=False).str.extract(r"([-+]?\d*\.?\d+)")[0],
            errors="coerce",
        )
    return s


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    # Map any Title Case columns to snake_case we use in the app
    rename_map = {
        "Notify Party Name": "notify_party",
        "Notify Party Address": "notify_party_address",
        "Consignee Address": "consignee_address",
        "Shipper Address": "shipper_address",
        "Bill of Lading": "bill_of_lading",
        "Actual Duty": "actual_duty",
        "Port of Delivery": "port_delivery",
        "Container TEU": "container_teu",
        "Freight Term": "freight_term",
        "Marks Number": "marks_number",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Some files already have normalized columns; ensure presence and types
    col_defaults: dict[str, object] = {
        "date": pd.NaT,
        "month": pd.NaT,
        "hs_code": np.nan,
        "hs2": np.nan,
        "hs4": np.nan,
        "product_description": None,
        "hs_description": None,
        "consignee": None,
        "shipper": None,
        "origin_country": None,
        "origin_port": None,
        "dest_country": None,
        "dest_port": None,
        "shipment_mode": None,
        "std_qty": np.nan,
        "std_unit": None,
        "qty": np.nan,
        "unit": None,
        "unit_rate_usd": np.nan,
        "unit_rate_norm_usd": np.nan,
        "value_usd": np.nan,
        "value_norm_usd": np.nan,
        "gross_weight": np.nan,
        "measurement": None,
        "direction": None,
        "commodity": None,
        "source_file": None,
    }
    for c, default in col_defaults.items():
        if c not in df.columns:
            df[c] = default

    # Parse dates
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "month" in df.columns:
        # normalize month to month-start Timestamp
        m = pd.to_datetime(df["month"], errors="coerce")
        df["month"] = m.dt.to_period("M").dt.to_timestamp()
    else:
        df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()

    # Ensure numeric types
    for c in [
        "std_qty",
        "qty",
        "unit_rate_usd",
        "unit_rate_norm_usd",
        "value_usd",
        "value_norm_usd",
        "gross_weight",
    ]:
        if c in df.columns:
            df[c] = _to_float(df[c])

    # Lowercase and strip text dims to avoid dup keys
    for c in [
        "origin_country",
        "dest_country",
        "shipper",
        "consignee",
        "unit",
        "std_unit",
        "shipment_mode",
        "commodity",
        "hs_code",
        "hs2",
        "hs4",
    ]:
        if c in df.columns:
            df[c] = (
                df[c]
                .astype(str)
                .str.strip()
                .replace({"": np.nan, "nan": np.nan, "None": np.nan})
            )

    # Any-description search field
    desc_cols = [
        c
        for c in ["product_description", "hs_description", "commodity"]
        if c in df.columns
    ]
    if desc_cols:
        df["description_any"] = (
            df[desc_cols].astype(str).agg(" | ".join, axis=1).str.replace("nan", "", regex=False)
        )

    return df


@st.cache_data(show_spinner=False)
def load_data(path: Path | None) -> pd.DataFrame:
    if path is None:
        path = DEFAULT_DATA_PATH
    try:
        df = pd.read_csv(path, encoding="utf-8", low_memory=False)
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="latin-1", low_memory=False)
    return _normalize_columns(df)


def _download_button(df: pd.DataFrame, label: str, filename: str) -> None:
    st.download_button(
        label=label,
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        use_container_width=True,
    )


def _topn(df: pd.DataFrame, by: str, metric: str, n: int = 20) -> pd.DataFrame:
    if df.empty:
        return df
    g = df.groupby(by, dropna=True)[metric].sum(min_count=1).sort_values(ascending=False)
    return g.head(n).reset_index(name=metric)


def _metric_col_exists(df: pd.DataFrame, pref: list[str]) -> str:
    for c in pref:
        if c in df.columns:
            return c
    return pref[-1]


def sidebar_filters(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filters")

    # File override (optional)
    st.sidebar.caption("Data source")
    default_path_str = str(DEFAULT_DATA_PATH.relative_to(ROOT)) if DEFAULT_DATA_PATH.exists() else str(DEFAULT_DATA_PATH)
    st.sidebar.text_input("Default CSV", value=default_path_str, disabled=True)
    uploaded = st.sidebar.file_uploader("Upload CSV to override", type=["csv"])
    data_path = None
    if uploaded is not None:
        # cache to temp and reload through pandas for consistency
        temp_path = ROOT / "_uploaded.csv"
        with temp_path.open("wb") as f:
            f.write(uploaded.getbuffer())
        data_path = temp_path

    if data_path is not None:
        df = load_data(data_path)

    # Date filter
    if "date" in df.columns and not df["date"].dropna().empty:
        dmin = pd.to_datetime(df["date"].min())
        dmax = pd.to_datetime(df["date"].max())
        date_range = st.sidebar.date_input("Date range", value=(dmin, dmax))
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start, end = [pd.to_datetime(x) for x in date_range]
            df = df[(df["date"] >= start) & (df["date"] <= end)]

    # HS filters
    if "hs4" in df.columns and not df["hs4"].dropna().empty:
        hs4_sel = st.sidebar.multiselect(
            "HS4", sorted(df["hs4"].dropna().unique().tolist())
        )
        if hs4_sel:
            df = df[df["hs4"].isin(hs4_sel)]

    # Destination country
    if "dest_country" in df.columns and not df["dest_country"].dropna().empty:
        countries = sorted(df["dest_country"].dropna().unique().tolist())
        country_sel = st.sidebar.multiselect(
            "Destination country", countries, default=countries[: min(12, len(countries))]
        )
        if country_sel:
            df = df[df["dest_country"].isin(country_sel)]

    # Shipper / Consignee
    if "shipper" in df.columns:
        ship_sel = st.sidebar.multiselect("Shippers", sorted(df["shipper"].dropna().unique().tolist()))
        if ship_sel:
            df = df[df["shipper"].isin(ship_sel)]
    if "consignee" in df.columns:
        cons_sel = st.sidebar.multiselect("Consignees", sorted(df["consignee"].dropna().unique().tolist()))
        if cons_sel:
            df = df[df["consignee"].isin(cons_sel)]

    # Text search
    search = st.sidebar.text_input("Search description")
    if search and "description_any" in df.columns:
        s = search.lower()
        df = df[df["description_any"].astype(str).str.lower().str.contains(s)]

    # Value and quantity ranges
    value_col = _metric_col_exists(df, ["value_norm_usd", "value_usd"])
    if value_col in df.columns:
        vmin, vmax = float(df[value_col].min() or 0), float(df[value_col].max() or 0)
        if np.isfinite([vmin, vmax]).all() and vmin < vmax:
            vsel = st.sidebar.slider("Value (USD) range", vmin, vmax, (vmin, vmax))
            df = df[(df[value_col].fillna(0) >= vsel[0]) & (df[value_col].fillna(0) <= vsel[1])]
    if "qty" in df.columns:
        qmin, qmax = float(df["qty"].min() or 0), float(df["qty"].max() or 0)
        if np.isfinite([qmin, qmax]).all() and qmin < qmax:
            qsel = st.sidebar.slider("Quantity range", qmin, qmax, (qmin, qmax))
            df = df[(df["qty"].fillna(0) >= qsel[0]) & (df["qty"].fillna(0) <= qsel[1])]

    # Sort
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    sort_col = st.sidebar.selectbox("Sort by", ["date"] + numeric_cols)
    ascending = st.sidebar.checkbox("Ascending", value=False)
    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=ascending)

    return df


def main() -> None:
    st.set_page_config(page_title="Combined Dataset Explorer", layout="wide")
    st.title("Combined Dataset Explorer")
    st.caption("Analyze shipments by country, supplier, consignee, and description.")

    # Load data (default path); sidebar can override via upload
    df = load_data(DEFAULT_DATA_PATH if DEFAULT_DATA_PATH.exists() else None)
    if df.empty:
        st.error(
            f"No data loaded. Expected CSV at '{DEFAULT_DATA_PATH}'. Upload a CSV from the sidebar to proceed."
        )
        return

    # Apply filters from sidebar (also supports upload override)
    df = sidebar_filters(df)

    # Choose which value metric to use globally
    value_col = _metric_col_exists(df, ["value_norm_usd", "value_usd"])

    # Tabs
    tabs = st.tabs([
        "Holistic List",
        "Country Analysis",
        "Suppliers & Consignees",
        "Descriptions",
        "Trends",
    ])

    # Holistic List
    with tabs[0]:
        st.subheader("Filtered Records")
        st.dataframe(df, use_container_width=True, height=520)
        _download_button(df, "Download filtered CSV", "filtered_records.csv")

    # Country Analysis
    with tabs[1]:
        st.subheader("Totals by Destination Country")
        if "dest_country" in df.columns:
            country_tbl = (
                df.groupby("dest_country")
                .agg(
                    shipments=("dest_country", "size"),
                    value_usd=(value_col, "sum"),
                    qty=("qty", "sum"),
                    std_qty=("std_qty", "sum"),
                )
                .reset_index()
                .sort_values("value_usd", ascending=False)
            )
            st.dataframe(country_tbl, use_container_width=True, height=420)

            # Charts
            top_countries = country_tbl.head(15)
            if not top_countries.empty:
                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(
                        px.bar(top_countries, x="dest_country", y="value_usd", title="Top Countries by Value (USD)"),
                        use_container_width=True,
                    )
                with c2:
                    st.plotly_chart(
                        px.bar(top_countries, x="dest_country", y="qty", title="Top Countries by Quantity"),
                        use_container_width=True,
                    )
        else:
            st.info("Column 'dest_country' not present.")

    # Suppliers & Consignees
    with tabs[2]:
        st.subheader("Suppliers and Consignees")
        c1, c2 = st.columns(2)
        if "shipper" in df.columns:
            ship_tbl = _topn(df, "shipper", value_col, n=25)
            c1.dataframe(ship_tbl, use_container_width=True, height=420)
            c1.plotly_chart(
                px.bar(ship_tbl.head(15), x="shipper", y=value_col, title="Top Shippers by Value (USD)"),
                use_container_width=True,
            )
        else:
            c1.info("Column 'shipper' not present.")
        if "consignee" in df.columns:
            cons_tbl = _topn(df, "consignee", value_col, n=25)
            c2.dataframe(cons_tbl, use_container_width=True, height=420)
            c2.plotly_chart(
                px.bar(cons_tbl.head(15), x="consignee", y=value_col, title="Top Consignees by Value (USD)"),
                use_container_width=True,
            )
        else:
            c2.info("Column 'consignee' not present.")

    # Descriptions
    with tabs[3]:
        st.subheader("Top Descriptions")
        desc_key = "description_any" if "description_any" in df.columns else "product_description"
        if desc_key in df.columns:
            desc_tbl = _topn(df, desc_key, value_col, n=30)
            st.dataframe(desc_tbl, use_container_width=True, height=420)
            st.plotly_chart(
                px.bar(desc_tbl.head(20), x=desc_key, y=value_col, title="Top Descriptions by Value (USD)"),
                use_container_width=True,
            )
        else:
            st.info("No description column available.")

    # Trends
    with tabs[4]:
        st.subheader("Trends by Month")
        if "month" not in df.columns or df["month"].isna().all():
            st.info("No month column available.")
        else:
            # Overall trend
            trend = (
                df.groupby("month")[value_col]
                .sum(min_count=1)
                .reset_index()
                .sort_values("month")
            )
            st.plotly_chart(
                px.line(trend, x="month", y=value_col, markers=True, title="Total Value by Month"),
                use_container_width=True,
            )

            # By country, choose a focus country
            if "dest_country" in df.columns:
                countries = ["All"] + sorted(df["dest_country"].dropna().unique().tolist())
                sel = st.selectbox("Country focus", countries)
                df_focus = df if sel == "All" else df[df["dest_country"] == sel]
                trend_c = (
                    df_focus.groupby(["month", "dest_country"])[value_col]
                    .sum(min_count=1)
                    .reset_index()
                )
                st.plotly_chart(
                    px.line(trend_c, x="month", y=value_col, color="dest_country", title="Value by Month (Country Split)"),
                    use_container_width=True,
                )


if __name__ == "__main__":
    main()

