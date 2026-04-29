from shiny import App, reactive, render, ui
import pandas as pd

# =============================================================================
# Constants and Logic
# =============================================================================
CONST_BASE  = 1.6569
COEF_ES     = -0.4006
COEF_VOL    = -0.0333
COEF_AGE    =  0.0168
DEFAULT_ELECTRICITY_PRICE = 0.29  # $/kWh
COL_80 = "Daily Energy Consumption at -80\u00baC (kWh/day)"
COL_70 = "Daily Energy Consumption at -70\u00baC (kWh/day)"

# Comparison sort categories
CAT_SAME_VOL = "Highest Efficiency for Same Volume"
CAT_SAME_SA  = "Highest Efficiency for Same Surface Area"
CAT_VOL_INC  = "Greatest Volume Increase with Similar Surface Area"

COLOR_SAME_VOL = "#7c3aed"   # purple
COLOR_SAME_SA  = "#ea580c"   # orange
COLOR_VOL_INC  = "#16a34a"   # green

# Tolerance windows
VOL_TOLERANCE = 0.10   # +/-10%
SA_TOLERANCE  = 0.10   # +/-10%

# Load Database
df_es = pd.read_csv("ult_freezer_database.csv")

# =============================================================================
# Program Specs
# =============================================================================
ULT_REBATE_TABLE = {"< 20 cu. ft.": 600, "\u2265 20 cu. ft.": 900}
ULT_SALES_INCENTIVE = 50


# =============================================================================
# Helpers
# =============================================================================

def _energy_col(temp_str):
    return COL_70 if temp_str == "-70\u00baC" and COL_70 in df_es.columns else COL_80


def _get_efficiency(row, col):
    vol = row["Total Volume (cu. ft.)"]
    return round(row[col] / vol, 4) if vol > 0 else 999.0


def _find_comparison_models(vol, sa, temp_str):
    col = _energy_col(temp_str)
    tmp = df_es.copy()
    tmp["_eff"] = tmp.apply(lambda r: _get_efficiency(r, col), axis=1)

    vol_lo, vol_hi = vol * (1 - VOL_TOLERANCE), vol * (1 + VOL_TOLERANCE)
    sa_lo,  sa_hi  = sa  * (1 - SA_TOLERANCE),  sa  * (1 + SA_TOLERANCE)

    same_vol_mask = (tmp["Total Volume (cu. ft.)"] >= vol_lo) & (tmp["Total Volume (cu. ft.)"] <= vol_hi)
    same_sa_mask  = (tmp["Surface Area (sq. in.)"] >= sa_lo)  & (tmp["Surface Area (sq. in.)"] <= sa_hi)
    vol_inc_mask  = (tmp["Total Volume (cu. ft.)"] > vol * (1 + VOL_TOLERANCE)) & same_sa_mask

    tmp["_category"] = None
    tmp.loc[same_vol_mask, "_category"] = CAT_SAME_VOL
    tmp.loc[same_sa_mask & tmp["_category"].isna(), "_category"] = CAT_SAME_SA
    tmp.loc[vol_inc_mask & tmp["_category"].isna(), "_category"] = CAT_VOL_INC

    tmp = tmp[tmp["_category"].notna()].copy()
    tmp = tmp.sort_values("_eff")
    return tmp, col


def _knn_recommend(vol, temp_str, k=5):
    col = _energy_col(temp_str)
    tmp = df_es.copy()
    tmp["dist"] = (tmp["Total Volume (cu. ft.)"] - vol).abs()
    neighbors = tmp.nsmallest(k, "dist")
    best_model = neighbors.loc[neighbors[col].idxmin(), "Model Name"]
    return best_model, neighbors, col


def _lookup_model(model_name, temp_str):
    row = df_es[df_es["Model Name"] == model_name]
    if row.empty:
        return None
    return row.iloc[0]


def _cat_color(cat):
    return {CAT_SAME_VOL: COLOR_SAME_VOL, CAT_SAME_SA: COLOR_SAME_SA, CAT_VOL_INC: COLOR_VOL_INC}.get(cat, "#6c757d")


def _cat_light(cat):
    return {CAT_SAME_VOL: "#f3e8ff", CAT_SAME_SA: "#fff7ed", CAT_VOL_INC: "#f0fdf4"}.get(cat, "#f8f9fa")



def _make_kpi_cards(df):
    n           = len(df)
    total_kwh   = df["Existing: Pred. Energy (kWh/yr)"].sum()  if not df.empty else 0
    total_sav   = df["Annual Energy Savings (kWh/yr)"].sum()   if not df.empty else 0
    total_cost  = df["Annual Cost Savings ($/yr)"].sum()        if not df.empty else 0
    total_rebate = 0
    if not df.empty and "Rebate Amount (2026)" in df.columns:
        total_rebate = df["Rebate Amount (2026)"].str.replace("[$,]", "", regex=True).astype(float).sum()

    def card(title, value, sub, color, icon):
        return ui.div(
            ui.div(
                ui.tags.span(icon, style="font-size:1.4rem; margin-bottom:4px; display:block;"),
                ui.div(title, style="font-size:0.70rem; text-transform:uppercase; letter-spacing:0.06em; color:#6c757d; margin-bottom:6px; font-weight:600;"),
                ui.div(value, style=f"font-size:1.6rem; font-weight:700; color:{color}; line-height:1.1; margin-bottom:4px;"),
                ui.div(sub, style="font-size:0.75rem; color:#adb5bd;"),
                style="background:#fff; border:1px solid #dee2e6; border-radius:12px; padding:18px 20px; flex:1; min-width:155px; box-shadow:0 2px 6px rgba(0,0,0,0.06); text-align:center;"
            ),
        )

    return ui.div(
        card("Total Units",                 str(n),                    "in inventory",                    "#003262", "\U0001f9ca"),
        card("Total Energy Use",            f"{int(total_kwh):,} kWh", "predicted / year",                "#e07b39", "\u26a1"),
        card("Potential kWh Savings",       f"{int(total_sav):,} kWh", "if switch to Energy Star models", "#4a9d6f", "\U0001f4c9"),
        card("Potential Cost Savings",      f"${total_cost:,.2f}",     "power demand reduction / yr",     "#5b8fcf", "\U0001f4b0"),
        card("Total Available Rebates",     f"${total_rebate:,.0f}",   "2026 program rebates",            "#7c3aed", "\U0001f3f7\ufe0f"),
        style="display:flex; gap:14px; flex-wrap:wrap; margin-bottom:24px;"
    )


# =============================================================================
# Server Logic
# =============================================================================

def server(input, output, session):
    inventory    = reactive.Value(pd.DataFrame())
    unit_counter = reactive.Value(0)

    @reactive.Calc
    def _knn():
        return _knn_recommend(input.vol(), input.temp())

    @reactive.Calc
    def _existing_energy():
        es_adj     = COEF_ES if input.is_es() else 0
        pred_daily = CONST_BASE + es_adj + (COEF_VOL * input.vol()) + (COEF_AGE * input.age())
        eff        = pred_daily / input.vol() if input.vol() > 0 else 0
        return {
            "daily_kwh":  round(pred_daily, 2),
            "year_kwh":   round(pred_daily * 365, 0),
            "efficiency": round(eff, 4),
        }

    @reactive.Calc
    def _existing_sa():
        try:
            return round(float(input.existing_depth()) * float(input.existing_width()), 2)
        except Exception:
            return 0.0

    @reactive.Calc
    def _desired_energy():
        model = input.desired_model()
        if not model:
            return None
        row = _lookup_model(model, input.temp())
        if row is None:
            return None
        col   = _energy_col(input.temp())
        daily = row[col]
        vol   = row["Total Volume (cu. ft.)"]
        sa    = row.get("Surface Area (sq. in.)", 0)
        es_yr = row.get("Average Energy Consumption(kWh/year)", None)
        return {
            "model":         model,
            "vol":           vol,
            "daily_kwh":     round(daily, 2),
            "year_kwh":      round(daily * 365, 0),
            "efficiency":    round(daily / vol if vol > 0 else 0, 4),
            "sa":            sa,
            "depth":         row.get("Depth (in)", "---"),
            "width":         row.get("Width", "---"),
            "height":        row.get("Height", "---"),
            "label":         row.get("Label", model),
            "es_id":         row.get("ENERGY STAR Unique ID", "---"),
            "brand":         row.get("Brand Name", "---"),
            "annual_kwh_es": es_yr,
        }

    # -- SA display in sidebar -------------------------------------------------

    @output
    @render.ui
    def existing_sa_display():
        try:
            sa = round(float(input.existing_depth()) * float(input.existing_width()), 2)
            return ui.div(
                ui.tags.span(f"Surface Area: {sa:,.2f} sq. in.",
                             style="font-size:0.80rem; color:#003262; font-weight:600;"),
                style="margin-top:-6px; margin-bottom:8px; padding:4px 6px; background:#eaf3fb; border-radius:4px;"
            )
        except Exception:
            return ui.div()

    # -- KPI Summary -----------------------------------------------------------

    @output
    @render.ui
    def kpi_summary():
        return _make_kpi_cards(inventory())

    # -- Desired unit selector with legend & color codes ----------------------

    @output
    @render.ui
    def desired_unit_ui():
        best_model, neighbors, col = _knn()
        vol = input.vol()
        sa  = _existing_sa()

        cat_df, _ = _find_comparison_models(vol, sa, input.temp())

        same_vol_models = cat_df[cat_df["_category"] == CAT_SAME_VOL]["Model Name"].tolist()
        same_sa_models  = cat_df[cat_df["_category"] == CAT_SAME_SA]["Model Name"].tolist()
        vol_inc_models  = cat_df[cat_df["_category"] == CAT_VOL_INC]["Model Name"].tolist()
        labeled = set(same_vol_models + same_sa_models + vol_inc_models)
        other_models = df_es[~df_es["Model Name"].isin(labeled)]["Model Name"].tolist()

        choices = {}
        for m in same_vol_models:
            choices[m] = f"\U0001f7e3 {m}"
        for m in same_sa_models:
            choices[m] = f"\U0001f7e0 {m}"
        for m in vol_inc_models:
            choices[m] = f"\U0001f7e2 {m}"
        for m in other_models:
            choices[m] = m

        selected = best_model if best_model in choices else (list(choices.keys())[0] if choices else None)

        legend = ui.div(
            ui.tags.strong("Color Legend", style="font-size:0.78rem; color:#333; display:block; margin-bottom:6px;"),
            ui.div(
                ui.tags.span("\U0001f7e3", style="margin-right:4px;"),
                ui.tags.span(CAT_SAME_VOL, style=f"color:{COLOR_SAME_VOL}; font-weight:600; font-size:0.75rem;"),
                style="margin-bottom:4px;"
            ),
            ui.div(
                ui.tags.span("\U0001f7e0", style="margin-right:4px;"),
                ui.tags.span(CAT_SAME_SA, style=f"color:{COLOR_SAME_SA}; font-weight:600; font-size:0.75rem;"),
                style="margin-bottom:4px;"
            ),
            ui.div(
                ui.tags.span("\U0001f7e2", style="margin-right:4px;"),
                ui.tags.span(CAT_VOL_INC, style=f"color:{COLOR_VOL_INC}; font-weight:600; font-size:0.75rem;"),
            ),
            style="background:#f8f9fa; border:1px solid #dee2e6; border-radius:6px; padding:8px 10px; margin-bottom:10px;"
        )

        return ui.div(
            legend,
            ui.p(
                "Our Recommendation: Closest volume, lowest energy:",
                ui.tags.br(),
                ui.tags.strong(best_model, style="color:#003262;"),
                style="font-size:0.82rem; margin-bottom:6px;"
            ),
            ui.input_select("desired_model", "Select Model from Database:", choices=choices, selected=selected),
            ui.output_ui("desired_model_card"),
        )

    @output
    @render.ui
    def desired_model_card():
        d = _desired_energy()
        if d is None:
            return ui.div()
        temp_label = input.temp()
        fallback   = " (\u26a0 using -80\u00baC data)" if (temp_label == "-70\u00baC" and COL_70 not in df_es.columns) else ""

        es_yr = d["annual_kwh_es"]
        es_yr_str = f"{int(es_yr):,} kWh/yr" if (es_yr and not pd.isna(es_yr)) else "---"

        def row(label, val):
            return ui.tags.tr(
                ui.tags.td(ui.tags.strong(label), style="padding:3px 8px; color:#555;"),
                ui.tags.td(str(val),              style="padding:3px 8px;"),
            )

        return ui.div(
            ui.tags.table(
                row("Brand",                    d["brand"]),
                row("ENERGY STAR ID",           str(d["es_id"])),
                row("Volume",                   f"{d['vol']} cu. ft."),
                row(f"Energy ({temp_label}{fallback})", f"{d['daily_kwh']} kWh/day"),
                row("ES Annual Energy (kWh/yr)", es_yr_str),
                row("Efficiency",               f"{d['efficiency']} kWh/cu ft/day"),
                row("Depth x Width (in)",        f"{d['depth']} x {d['width']} = {d['sa']:.1f} sq.in."),
                style="font-size:0.82rem; width:100%;"
            ),
            style="background:#eaf3fb; border:1px solid #b8d4ea; border-radius:6px; padding:8px; margin-top:8px;"
        )

    # -- Add to Inventory ------------------------------------------------------

    @reactive.effect
    @reactive.event(input.add_btn)
    def _add_unit():
        existing = _existing_energy()
        desired  = _desired_energy()
        sa       = _existing_sa()

        best_model, neighbors, col = _knn()

        if desired:
            es_yr = desired.get("annual_kwh_es")
            desired_year_kwh = float(es_yr) if (es_yr and not pd.isna(es_yr)) else float(desired["year_kwh"])
            annual_kwh_savings = max(0.0, existing["year_kwh"] - desired_year_kwh)
        else:
            avg_es_year = neighbors[col].mean() * 365
            annual_kwh_savings = max(0.0, existing["year_kwh"] - avg_es_year)
            desired_year_kwh = avg_es_year

        annual_cost_savings = annual_kwh_savings * input.elec_rate()
        vol      = input.vol()
        size_cat = "< 20 cu. ft." if vol < 20 else "\u2265 20 cu. ft."
        rebate   = ULT_REBATE_TABLE[size_cat]

        new_count = unit_counter() + 1
        unit_counter.set(new_count)

        desired_model_name = desired["model"]     if desired else best_model
        desired_vol        = desired["vol"]        if desired else "---"
        desired_daily      = desired["daily_kwh"]  if desired else "---"
        desired_sa         = desired["sa"]         if desired else "---"
        desired_brand      = desired["brand"]      if desired else "---"
        desired_es_id      = str(desired["es_id"]) if desired else "---"

        new_row = pd.DataFrame({
            "ID":                                    [f"Unit {new_count}"],
            "Existing: Capacity (cu. ft.)":          [vol],
            "Existing: SA (sq. in.)":                [sa],
            "Existing: Age (Years)":                 [input.age()],
            "Existing: Temp":                        [input.temp()],
            "Existing: Energy Star?":                ["Yes" if input.is_es() else "No"],
            "Existing: Pred. Energy (kWh/day)":      [existing["daily_kwh"]],
            "Existing: Pred. Energy (kWh/yr)":       [int(existing["year_kwh"])],
            "Existing: Efficiency (kWh/cuft/day)":   [existing["efficiency"]],
            "Desired: Brand":                        [desired_brand],
            "Desired: ENERGY STAR ID":               [desired_es_id],
            "Desired: Model":                        [desired_model_name],
            "Desired: Volume (cu. ft.)":             [desired_vol],
            "Desired: SA (sq. in.)":                 [desired_sa],
            "Desired: Actual Energy (kWh/day)":      [desired_daily],
            "Desired: ES Annual Energy (kWh/yr)":    [int(desired_year_kwh)],
            "Annual Energy Savings (kWh/yr)":        [round(annual_kwh_savings, 0)],
            "Annual Cost Savings ($/yr)":            [round(annual_cost_savings, 2)],
            "Size Category (2026)":                  [size_cat],
            "Rebate Amount (2026)":                  [f"${rebate:,}"],
        })

        inventory.set(pd.concat([inventory(), new_row], ignore_index=True))

    @reactive.effect
    @reactive.event(input.clear_btn)
    def _clear_inventory():
        inventory.set(pd.DataFrame())

    # -- Inventory table (styled) ---------------------------------------------

    @output
    @render.ui
    def inventory_table():
        df = inventory()
        if df.empty:
            return ui.div(
                ui.p("No comparisons added yet. Fill in both sidebar tabs and click Add.",
                     style="color:#6c757d; font-style:italic; padding:12px;")
            )

        rows = []
        header_cells = [ui.tags.th(c, style="padding:6px 8px; white-space:nowrap; font-size:0.75rem;") for c in df.columns]
        rows.append(ui.tags.tr(*header_cells, style="background:#003262; color:#fff;"))

        for i, (_, row) in enumerate(df.iterrows()):
            bg = "#fff" if i % 2 == 0 else "#f8f9fa"
            cells = [ui.tags.td(str(row[c]), style="padding:5px 8px; font-size:0.75rem; white-space:nowrap;") for c in df.columns]
            rows.append(ui.tags.tr(*cells, style=f"background:{bg};"))

        return ui.div(
            ui.tags.table(*rows, style="border-collapse:collapse; width:100%; border:1px solid #dee2e6;"),
            style="overflow-x:auto; border-radius:8px; border:1px solid #dee2e6;"
        )

    # -- Live Comparison with category rows -----------------------------------

    @output
    @render.ui
    def comparison_card():
        existing = _existing_energy()
        desired  = _desired_energy()
        vol      = input.vol()
        sa       = _existing_sa()

        cat_df, col = _find_comparison_models(vol, sa, input.temp())

        def spec_row(label, val, light):
            return ui.div(
                ui.div(label, style="font-size:0.72rem; color:#555; font-weight:600; flex:1; min-width:130px;"),
                ui.div(str(val), style="font-size:0.80rem; flex:2;"),
                style=f"display:flex; gap:6px; padding:3px 8px; background:{light}; border-bottom:1px solid #e9ecef;"
            )

        existing_block = ui.div(
            ui.div(
                ui.tags.span("\U0001f4cc YOUR EXISTING UNIT", style="font-weight:700; font-size:0.80rem; color:#003262;"),
                style="padding:6px 10px; background:#fff3cd; border-radius:6px 6px 0 0; border-bottom:2px solid #ffc107;"
            ),
            spec_row("Volume (cu. ft.)",          vol,                                    "#fff3cd"),
            spec_row("Surface Area (sq. in.)",     sa if sa else "Enter depth & width",    "#fff3cd"),
            spec_row("Age (yrs)",                  input.age(),                            "#fff3cd"),
            spec_row("Operating Temp",             input.temp(),                           "#fff3cd"),
            spec_row("Energy Star?",               "Yes" if input.is_es() else "No",      "#fff3cd"),
            spec_row("Pred. Energy/day (kWh)",     existing["daily_kwh"],                 "#fff3cd"),
            spec_row("Pred. Energy/yr (kWh)",      f"{int(existing['year_kwh']):,}",      "#fff3cd"),
            spec_row("Efficiency (kWh/cuft/day)", existing["efficiency"],                  "#fff3cd"),
            style="border:2px solid #ffc107; border-radius:6px; margin-bottom:12px;"
        )

        def make_desired_block(row_data, cat):
            color = _cat_color(cat)
            light = _cat_light(cat)
            daily   = row_data[col]
            yr      = round(daily * 365, 0)
            es_yr   = row_data.get("Average Energy Consumption(kWh/year)", None)
            eff     = _get_efficiency(row_data, col)
            use_yr  = float(es_yr) if (es_yr and not pd.isna(es_yr)) else yr
            kwh_sav = max(0, existing["year_kwh"] - use_yr)
            cost_sv = round(kwh_sav * input.elec_rate(), 2)

            def dr(label, val):
                return ui.div(
                    ui.div(label, style="font-size:0.72rem; color:#555; font-weight:600; flex:1; min-width:130px;"),
                    ui.div(str(val), style="font-size:0.80rem; flex:2;"),
                    style=f"display:flex; gap:6px; padding:3px 8px; background:{light}; border-bottom:1px solid #e9ecef;"
                )

            es_id_val = row_data.get("ENERGY STAR Unique ID", None)
            es_id_str = str(int(es_id_val)) if (es_id_val and not pd.isna(es_id_val)) else "---"
            es_yr_str = f"{int(es_yr):,}" if (es_yr and not pd.isna(es_yr)) else f"{int(yr):,}*"

            return ui.div(
                ui.div(
                    ui.tags.span(f"\u2605 {row_data['Model Name']}", style=f"font-weight:700; font-size:0.80rem; color:{color};"),
                    ui.tags.span(f"  \u2014  {cat}", style=f"font-size:0.72rem; color:{color}; opacity:0.85;"),
                    style=f"padding:6px 10px; background:{light}; border-radius:6px 6px 0 0; border-bottom:2px solid {color};"
                ),
                dr("Brand",                       row_data.get("Brand Name", "---")),
                dr("ENERGY STAR ID",              es_id_str),
                dr("Volume (cu. ft.)",            row_data["Total Volume (cu. ft.)"]),
                dr("Surface Area (sq. in.)",      round(row_data.get("Surface Area (sq. in.)", 0), 2)),
                dr(f"Energy/day ({input.temp()}) kWh", round(daily, 2)),
                dr("ES Annual Energy (kWh/yr)",   es_yr_str),
                dr("Efficiency (kWh/cuft/day)",   eff),
                dr("Est. kWh Saved/yr",           f"{int(kwh_sav):,}"),
                dr("Est. Cost Saved/yr",          f"${cost_sv:,.2f}"),
                style=f"border:2px solid {color}; border-radius:6px; margin-bottom:8px;"
            )

        sort_val = input.comparison_sort() if hasattr(input, "comparison_sort") else CAT_SAME_VOL
        cat_order = [sort_val] + [c for c in [CAT_SAME_VOL, CAT_SAME_SA, CAT_VOL_INC] if c != sort_val]

        desired_blocks = []
        for cat in cat_order:
            subset = cat_df[cat_df["_category"] == cat]
            if subset.empty:
                continue
            desired_blocks.append(make_desired_block(subset.iloc[0], cat))

        if not desired_blocks:
            desired_blocks = [ui.p(
                "No matching ENERGY STAR models found within \u00b110% of this volume/surface area. "
                "Try adjusting capacity or entering dimensions.",
                style="color:#6c757d; font-style:italic;"
            )]

        # Delta banner for currently selected model
        delta_block = ui.div()
        if desired:
            es_yr = desired.get("annual_kwh_es")
            desired_yr_kwh = float(es_yr) if (es_yr and not pd.isna(es_yr)) else float(desired["year_kwh"])
            kwh_saved  = existing["year_kwh"] - desired_yr_kwh
            cost_saved = kwh_saved * input.elec_rate()
            pct        = (kwh_saved / existing["year_kwh"] * 100) if existing["year_kwh"] else 0
            arrow = "\u25bc" if kwh_saved >= 0 else "\u25b2"
            dcolor = "#1a7a3c" if kwh_saved >= 0 else "#b00020"
            delta_block = ui.div(
                ui.tags.span(
                    f"{arrow} Selected vs existing: {abs(kwh_saved):,.0f} kWh/yr "
                    f"({'saved' if kwh_saved >= 0 else 'increase'})  |  "
                    f"${abs(cost_saved):,.2f}/yr  |  {abs(pct):.1f}%",
                    style=f"font-weight:bold; color:{dcolor}; font-size:0.88rem;"
                ),
                style="text-align:center; padding:8px; background:#f8f9fa; border-radius:4px; margin-bottom:12px;"
            )

        return ui.div(
            ui.h6("Live Comparison", style="margin:0 0 8px; color:#003262;"),
            ui.input_select(
                "comparison_sort",
                "Sort Priority:",
                choices={
                    CAT_SAME_VOL: f"\U0001f7e3 {CAT_SAME_VOL}",
                    CAT_SAME_SA:  f"\U0001f7e0 {CAT_SAME_SA}",
                    CAT_VOL_INC:  f"\U0001f7e2 {CAT_VOL_INC}",
                },
                selected=CAT_SAME_VOL,
            ),
            delta_block,
            existing_block,
            *desired_blocks,
            style="background:#fff; border:1px solid #dee2e6; border-radius:8px; padding:12px; margin-bottom:16px;"
        )

    # -- Sidebar detail -------------------------------------------------------

    @output
    @render.ui
    def inventory_selector():
        df = inventory()
        if df.empty:
            return ui.div()
        choices = {row["ID"]: row["ID"] for _, row in df.iterrows()}
        return ui.input_select("selected_id", "Detail View:", choices)

    @output
    @render.ui
    def detail_card():
        df = inventory()
        if df.empty:
            return ui.div()
        sel = input.selected_id() if hasattr(input, "selected_id") else None
        if not sel:
            return ui.div()
        row = df[df["ID"] == sel]
        if row.empty:
            return ui.div()
        row = row.iloc[0]
        return ui.div(
            ui.h5(f"Details \u2014 {sel}"),
            ui.tags.table(
                *[
                    ui.tags.tr(
                        ui.tags.td(ui.tags.strong(c), style="padding-right:12px; white-space:nowrap;"),
                        ui.tags.td(str(row[c]))
                    )
                    for c in df.columns if c != "ID"
                ],
                style="font-size:0.82rem;"
            ),
            style="background:#fff; border:1px solid #dee2e6; border-radius:6px; padding:12px; margin-top:10px;"
        )


# =============================================================================
# UI Helpers
# =============================================================================

def _specs_panel():
    ult_rebate_rows = [
        ui.tags.tr(
            ui.tags.th("Size Category (2026)", style="padding:6px 10px; background:#dce8f5; text-align:left;"),
            ui.tags.th("Rebate",               style="padding:6px 10px; background:#dce8f5; text-align:left;"),
        )
    ] + [
        ui.tags.tr(
            ui.tags.td(cat,        style="padding:5px 10px; border-top:1px solid #dee2e6;"),
            ui.tags.td(f"${amt:,}", style="padding:5px 10px; border-top:1px solid #dee2e6;"),
        )
        for cat, amt in ULT_REBATE_TABLE.items()
    ]
    return ui.div(
        ui.h5("ULT Freezers \u2014 2026 Program Update", style="color:#003262; margin-top:0; font-size:0.95rem;"),
        ui.p("Effective January 1, 2026. Certified to ", ui.tags.strong("ENERGY STAR Lab Grade Spec v2.0"), ".", style="font-size:0.82rem;"),
        ui.h6("Rebate Amounts", style="margin-bottom:4px;"),
        ui.tags.table(*ult_rebate_rows, style="border-collapse:collapse; font-size:0.84rem; margin-bottom:6px; width:100%;"),
        ui.p(f"Sales incentive: ${ULT_SALES_INCENTIVE}/unit (unchanged).", style="font-size:0.8rem; color:#555; margin-bottom:0;"),
        style="background:#f0f4f8; border:1px solid #c8d6e5; border-radius:8px; padding:16px; margin-bottom:20px;"
    )


def _energystar_panel():
    return ui.div(
        ui.div(
            ui.tags.span("\U0001f50d", style="font-size:1.1rem; margin-right:6px;"),
            ui.tags.strong("Search the Energy Star Product Database"),
            style="margin-bottom:8px; font-size:0.9rem; color:#003262;"
        ),
        ui.p(
            "Browse and verify certified ULT freezer models directly from the Energy Star website.",
            style="font-size:0.8rem; color:#555; margin-bottom:10px;"
        ),
        ui.tags.a(
            "\U0001f517 Open Energy Star ULT Freezer Search (Full Page)",
            href="https://www.energystar.gov/productfinder/product/certified-laboratory-grade-refrigerators-and-freezers/details",
            target="_blank",
            style="display:inline-block; padding:7px 16px; background:#003262; color:#fff; border-radius:6px; text-decoration:none; font-size:0.82rem; font-weight:600; margin-bottom:12px;"
        ),
        ui.tags.iframe(
            src="https://www.energystar.gov/productfinder/product/certified-laboratory-grade-refrigerators-and-freezers/details",
            width="100%",
            height="600px",
            style="border:1px solid #dee2e6; border-radius:8px;",
        ),
        ui.p("\u26a0 If the search tool doesn't load above, use the 'Open Full Page' link.", style="font-size:0.75rem; color:#888; margin-top:6px;"),
        style="background:#fff; border:1px solid #dee2e6; border-radius:10px; padding:16px; margin-bottom:20px;"
    )


# =============================================================================
# UI Definition
# =============================================================================

app_ui = ui.page_fluid(
    ui.tags.head(
        ui.tags.style("""
            .app-header { background:#003262; color:white; padding:20px; margin-bottom:20px; }
            .nav-link { font-size:0.88rem; }
            .btn-danger-outline { color:#dc3545; border-color:#dc3545; background:white; }
            .btn-danger-outline:hover { background:#dc3545; color:white; }
            .section-title {
                font-size:0.82rem; font-weight:700; color:#495057;
                text-transform:uppercase; letter-spacing:0.06em;
                margin-bottom:12px; border-bottom:2px solid #e9ecef; padding-bottom:5px;
            }
        """)
    ),
    ui.div(
        ui.h2("UC ULT Freezer Savings Calculator"),
        ui.p("Compare existing inventory against Energy Star models \u2014 updated ENERGY STAR database"),
        class_="app-header"
    ),
    ui.layout_sidebar(
        ui.sidebar(
            ui.navset_tab(
                ui.nav_panel(
                    "Existing Unit",
                    ui.div(
                        ui.br(),
                        ui.input_numeric("vol",  "Capacity (cu. ft.)",   value=20.0, min=0.1, step=0.1),
                        ui.input_numeric("age",  "Age (Years)",           value=5,   min=0),
                        ui.input_select( "temp", "Operating Temperature", choices=["-80\u00baC", "-70\u00baC"]),
                        ui.input_switch( "is_es","Is currently Energy Star?"),
                        ui.hr(),
                        ui.p("Footprint (for surface area matching)",
                             style="font-size:0.78rem; font-weight:600; color:#495057; margin-bottom:4px;"),
                        ui.input_numeric("existing_depth", "Depth (in)", value=37.25, min=0.1, step=0.25),
                        ui.input_numeric("existing_width", "Width (in)", value=32.50, min=0.1, step=0.25),
                        ui.output_ui("existing_sa_display"),
                        ui.hr(),
                        ui.input_numeric(
                            "elec_rate", "Electricity Rate ($/kWh)",
                            value=DEFAULT_ELECTRICITY_PRICE, min=0.01, step=0.01
                        ),
                    )
                ),
                ui.nav_panel(
                    "Desired Unit",
                    ui.div(
                        ui.br(),
                        ui.p(
                            "Select any model from the ENERGY STAR database. "
                            "Color-coded icons show how each model matches your existing unit.",
                            style="font-size:0.8rem; color:#555;"
                        ),
                        ui.output_ui("desired_unit_ui"),
                    )
                ),
                id="sidebar_tabs"
            ),
            ui.hr(),
            ui.input_action_button("add_btn",   "Add Comparison to Inventory", class_="btn-primary w-100"),
            ui.br(), ui.br(),
            ui.input_action_button("clear_btn", "Clear Inventory", class_="btn-danger-outline w-100"),
            ui.hr(),
            ui.output_ui("inventory_selector"),
            ui.output_ui("detail_card"),
            width=410
        ),

        ui.navset_tab(
            ui.nav_panel(
                "\U0001f4ca Calculator",
                ui.div(
                    _specs_panel(),
                    ui.div("Inventory Summary", class_="section-title"),
                    ui.output_ui("kpi_summary"),
                    ui.div("Live Comparison & Model Suggestions", class_="section-title"),
                    ui.output_ui("comparison_card"),
                    ui.div("Freezer Comparison Inventory", class_="section-title"),
                    ui.output_ui("inventory_table"),
                    ui.hr(),
                    ui.p(
                        "Existing unit energy estimated via linear regression (age, capacity, ENERGY STAR status). "
                        "Desired unit savings use official ENERGY STAR Annual Energy Consumption (kWh/yr) from the database. "
                        "Surface area = Depth \u00d7 Width (in\u00b2). "
                        "Matching tolerances: \u00b110% volume / \u00b110% surface area. "
                        "Rebates reflect 2026 CPUC size categories.",
                        style="font-size:0.82rem; color:#555;"
                    ),
                    style="padding-top:16px;"
                )
            ),
            ui.nav_panel(
                "\U0001f50d Energy Star Model Search",
                ui.div(_energystar_panel(), style="padding-top:16px;")
            ),
            id="main_tabs"
        )
    )
)

app = App(app_ui, server)
