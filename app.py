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
COL_80 = "Daily Energy Consumption at -80ºC (kWh/day)"
COL_70 = "Daily Energy Consumption at -70ºC (kWh/day)"

# Load Database
df_es = pd.read_csv("ult_freezer_database.csv")

# =============================================================================
# Program Specs — ULT Freezers (updated Jan 1, 2026)
# =============================================================================
ULT_REBATE_TABLE = {"< 20 cu. ft.": 600, "≥ 20 cu. ft.": 900}
ULT_SALES_INCENTIVE = 50


# =============================================================================
# Helpers
# =============================================================================

def _energy_col(temp_str: str) -> str:
    return COL_70 if temp_str == "-70ºC" and COL_70 in df_es.columns else COL_80

def _knn_recommend(vol: float, temp_str: str, k: int = 5):
    col = _energy_col(temp_str)
    tmp = df_es.copy()
    tmp["dist"] = (tmp["Total Volume (cu. ft.)"] - vol).abs()
    neighbors = tmp.nsmallest(k, "dist")
    best_model = neighbors.loc[neighbors[col].idxmin(), "Model Name"]
    return best_model, neighbors, col

def _lookup_model(model_name: str, temp_str: str):
    row = df_es[df_es["Model Name"] == model_name]
    if row.empty:
        return None
    return row.iloc[0]


# =============================================================================
# KPI Cards Helper
# =============================================================================

def _make_kpi_cards(df):
    n          = len(df)
    total_kwh  = df["Existing: Pred. Energy (kWh/yr)"].sum()      if not df.empty else 0
    total_sav  = df["Annual Energy Savings (kWh/yr)"].sum()        if not df.empty else 0
    total_cost = df["Annual Cost Savings ($/yr)"].sum()            if not df.empty else 0

    def card(title, value, sub, color, icon):
        return ui.div(
            ui.div(
                ui.tags.span(icon, style="font-size:1.4rem; margin-bottom:4px; display:block;"),
                ui.div(title, style=(
                    "font-size:0.70rem; text-transform:uppercase; letter-spacing:0.06em; "
                    "color:#6c757d; margin-bottom:6px; font-weight:600;"
                )),
                ui.div(value, style=(
                    f"font-size:1.6rem; font-weight:700; color:{color}; line-height:1.1; margin-bottom:4px;"
                )),
                ui.div(sub, style="font-size:0.75rem; color:#adb5bd;"),
                style=(
                    "background:#fff; border:1px solid #dee2e6; border-radius:12px; "
                    "padding:18px 20px; flex:1; min-width:160px; "
                    "box-shadow:0 2px 6px rgba(0,0,0,0.06); text-align:center;"
                )
            ),
        )

    return ui.div(
        card("Total Units",                              str(n),                      "in inventory",                        "#003262", "🧊"),
        card("Total Energy Use",                         f"{int(total_kwh):,} kWh",   "predicted / year",                    "#e07b39", "⚡"),
        card("Potential Annual kWh Savings",             f"{int(total_sav):,} kWh",   "if switch to Energy Star models",     "#4a9d6f", "📉"),
        card("Potential Annual Cost Savings\nfrom Power Demand Reduction", f"${total_cost:,.2f}", "if switch to Energy Star models", "#5b8fcf", "💰"),
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
        es_adj          = COEF_ES if input.is_es() else 0
        pred_daily      = CONST_BASE + es_adj + (COEF_VOL * input.vol()) + (COEF_AGE * input.age())
        pred_efficiency = pred_daily / input.vol() if input.vol() > 0 else 0
        return {
            "daily_kwh":  round(pred_daily,       2),
            "year_kwh":   round(pred_daily * 365,  0),
            "efficiency": round(pred_efficiency,   3),
        }

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
        return {
            "model":      model,
            "vol":        vol,
            "daily_kwh":  round(daily, 2),
            "year_kwh":   round(daily * 365, 0),
            "efficiency": round(daily / vol if vol > 0 else 0, 3),
        }

    # ── KPI Summary ───────────────────────────────────────────────────────────

    @output
    @render.ui
    def kpi_summary():
        return _make_kpi_cards(inventory())

    # ── Desired unit model selector ───────────────────────────────────────────

    @output
    @render.ui
    def desired_unit_ui():
        best_model, neighbors, col = _knn()
        all_models = df_es["Model Name"].tolist()
        choices    = {m: m for m in all_models}
        return ui.div(
            ui.p(
                "KNN recommendation (closest volume, lowest energy):",
                ui.tags.br(),
                ui.tags.strong(best_model, style="color:#003262;"),
                style="font-size:0.82rem; margin-bottom:6px;"
            ),
            ui.input_select(
                "desired_model",
                "Select Model from Database:",
                choices=choices,
                selected=best_model,
            ),
            ui.output_ui("desired_model_card"),
        )

    @output
    @render.ui
    def desired_model_card():
        d = _desired_energy()
        if d is None:
            return ui.div()
        temp_label = input.temp()
        using_80   = (temp_label == "-70ºC" and COL_70 not in df_es.columns)
        fallback   = " (⚠ using -80ºC data)" if using_80 else ""
        return ui.div(
            ui.tags.table(
                ui.tags.tr(
                    ui.tags.td(ui.tags.strong("Volume"),                        style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{d['vol']} cu. ft.",                           style="padding:3px 8px;"),
                ),
                ui.tags.tr(
                    ui.tags.td(ui.tags.strong(f"Energy ({temp_label}{fallback})"), style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{d['daily_kwh']} kWh/day",                    style="padding:3px 8px;"),
                ),
                ui.tags.tr(
                    ui.tags.td(ui.tags.strong("Annual Energy"),                 style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{int(d['year_kwh']):,} kWh/yr",               style="padding:3px 8px;"),
                ),
                ui.tags.tr(
                    ui.tags.td(ui.tags.strong("Efficiency"),                    style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{d['efficiency']} kWh/cu ft/day",              style="padding:3px 8px;"),
                ),
                style="font-size:0.82rem; width:100%;"
            ),
            style=(
                "background:#eaf3fb; border:1px solid #b8d4ea; border-radius:6px; "
                "padding:8px; margin-top:8px;"
            )
        )

    # ── Add to Inventory ───────────────────────────────────────────────────────

    @reactive.effect
    @reactive.event(input.add_btn)
    def _add_unit():
        existing = _existing_energy()
        desired  = _desired_energy()

        best_model, neighbors, col = _knn()
        avg_es_year = neighbors[col].mean() * 365

        if desired:
            annual_kwh_savings = max(0.0, existing["year_kwh"] - desired["year_kwh"])
        else:
            annual_kwh_savings = max(0.0, existing["year_kwh"] - avg_es_year)
        annual_cost_savings = annual_kwh_savings * input.elec_rate()

        vol      = input.vol()
        size_cat = "< 20 cu. ft." if vol < 20 else "≥ 20 cu. ft."
        rebate   = ULT_REBATE_TABLE[size_cat]

        new_count = unit_counter() + 1
        unit_counter.set(new_count)

        desired_model_name = desired["model"]     if desired else best_model
        desired_vol        = desired["vol"]        if desired else "—"
        desired_daily      = desired["daily_kwh"]  if desired else "—"
        desired_year       = int(desired["year_kwh"]) if desired else "—"

        new_row = pd.DataFrame({
            "ID":                                    [f"Unit {new_count}"],
            "Existing: Capacity (cu. ft.)":          [vol],
            "Existing: Age (Years)":                 [input.age()],
            "Existing: Temp":                        [input.temp()],
            "Existing: Energy Star?":                ["Yes" if input.is_es() else "No"],
            "Existing: Pred. Energy (kWh/day)":      [existing["daily_kwh"]],
            "Existing: Pred. Energy (kWh/yr)":       [int(existing["year_kwh"])],
            "Existing: Efficiency (kWh/cu ft/day)":  [existing["efficiency"]],
            "Desired: Model":                        [desired_model_name],
            "Desired: Volume (cu. ft.)":             [desired_vol],
            "Desired: Actual Energy (kWh/day)":      [desired_daily],
            "Desired: Actual Energy (kWh/yr)":       [desired_year],
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

    @output
    @render.table
    def inventory_table():
        df = inventory()
        if df.empty:
            return pd.DataFrame({"Status": ["No comparisons added yet. Fill in both tabs and click Add."]})
        return df

    @output
    @render.ui
    def comparison_card():
        existing = _existing_energy()
        desired  = _desired_energy()
        if desired is None:
            return ui.div()

        kwh_saved  = existing["year_kwh"] - desired["year_kwh"]
        cost_saved = kwh_saved * input.elec_rate()
        pct        = (kwh_saved / existing["year_kwh"] * 100) if existing["year_kwh"] else 0

        arrow = "▼" if kwh_saved >= 0 else "▲"
        color = "#1a7a3c" if kwh_saved >= 0 else "#b00020"

        def _stat(label, val):
            return ui.tags.tr(
                ui.tags.td(label, style="padding:4px 10px; color:#555; font-size:0.82rem;"),
                ui.tags.td(val,   style="padding:4px 10px; font-size:0.82rem;"),
            )

        return ui.div(
            ui.h6("Live Comparison", style="margin:0 0 8px; color:#003262;"),
            ui.div(
                ui.div(
                    ui.p(ui.tags.strong("Existing Unit"), style="margin:0 0 4px; font-size:0.85rem; text-align:center;"),
                    ui.tags.table(
                        _stat("Energy/day",  f"{existing['daily_kwh']} kWh"),
                        _stat("Energy/year", f"{int(existing['year_kwh']):,} kWh"),
                        _stat("Efficiency",  f"{existing['efficiency']} kWh/cu ft/day"),
                        style="width:100%;"
                    ),
                    style="flex:1; background:#fff3cd; border-radius:6px; padding:8px;"
                ),
                ui.div(
                    ui.p(ui.tags.strong("Desired Unit"), style="margin:0 0 4px; font-size:0.85rem; text-align:center;"),
                    ui.tags.table(
                        _stat("Energy/day",  f"{desired['daily_kwh']} kWh"),
                        _stat("Energy/year", f"{int(desired['year_kwh']):,} kWh"),
                        _stat("Efficiency",  f"{desired['efficiency']} kWh/cu ft/day"),
                        style="width:100%;"
                    ),
                    style="flex:1; background:#eaf3fb; border-radius:6px; padding:8px;"
                ),
                style="display:flex; gap:8px; margin-bottom:8px;"
            ),
            ui.div(
                ui.tags.span(
                    f"{arrow} {abs(kwh_saved):,.0f} kWh/yr  "
                    f"({'saved' if kwh_saved >= 0 else 'increase'})  |  "
                    f"${abs(cost_saved):,.2f}/yr  |  {abs(pct):.1f}%",
                    style=f"font-weight:bold; color:{color}; font-size:0.88rem;"
                ),
                style="text-align:center; padding:6px; background:#f8f9fa; border-radius:4px;"
            ),
            style=(
                "background:#fff; border:1px solid #dee2e6; border-radius:8px; "
                "padding:12px; margin-bottom:16px;"
            )
        )

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
            ui.h5(f"Details — {sel}"),
            ui.tags.table(
                *[
                    ui.tags.tr(
                        ui.tags.td(ui.tags.strong(col), style="padding-right:12px;"),
                        ui.tags.td(str(row[col]))
                    )
                    for col in df.columns if col != "ID"
                ],
                style="font-size:0.82rem;"
            ),
            style=(
                "background:#fff; border:1px solid #dee2e6; border-radius:6px; "
                "padding:12px; margin-top:10px;"
            )
        )


# =============================================================================
# UI Helper — Program Specs Panel
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
        ui.div(
            ui.h5("ULT Freezers — 2026 Program Update", style="color:#003262; margin-top:0; font-size:0.95rem;"),
            ui.p("Effective January 1, 2026. Certified to ", ui.tags.strong("ENERGY STAR Lab Grade Spec v2.0"), ".", style="font-size:0.82rem;"),
            ui.h6("Rebate Amounts", style="margin-bottom:4px;"),
            ui.tags.table(*ult_rebate_rows, style="border-collapse:collapse; font-size:0.84rem; margin-bottom:6px; width:100%;"),
            ui.p(f"Sales incentive: ${ULT_SALES_INCENTIVE}/unit (unchanged).", style="font-size:0.8rem; color:#555; margin-bottom:8px;"),
        ),
        style="background:#f0f4f8; border:1px solid #c8d6e5; border-radius:8px; padding:16px; margin-bottom:20px;"
    )


# =============================================================================
# UI Helper — Energy Star Search Panel
# =============================================================================

def _energystar_panel():
    return ui.div(
        ui.div(
            ui.tags.span("🔍", style="font-size:1.1rem; margin-right:6px;"),
            ui.tags.strong("Search the Energy Star Product Database"),
            style="margin-bottom:8px; font-size:0.9rem; color:#003262;"
        ),
        ui.p(
            "Browse and verify certified ULT freezer models directly from the Energy Star website. "
            "Use the search below to look up make, model, and specifications.",
            style="font-size:0.8rem; color:#555; margin-bottom:10px;"
        ),
        # Direct link button
        ui.tags.a(
            "🔗 Open Energy Star ULT Freezer Search (Full Page)",
            href="https://www.energystar.gov/productfinder/product/certified-laboratory-grade-refrigerators-and-freezers/details",
            target="_blank",
            style=(
                "display:inline-block; padding:7px 16px; background:#003262; color:#fff; "
                "border-radius:6px; text-decoration:none; font-size:0.82rem; font-weight:600; "
                "margin-bottom:12px;"
            )
        ),
        # Embedded iframe — Energy Star product finder
        ui.tags.iframe(
            src=(
                "https://www.energystar.gov/productfinder/product/"
                "certified-laboratory-grade-refrigerators-and-freezers/details"
            ),
            width="100%",
            height="600px",
            style="border:1px solid #dee2e6; border-radius:8px;",
            # Some sites block iframe embedding; the link above is the fallback
        ),
        ui.p(
            "⚠ If the search tool doesn't load above, use the 'Open Full Page' link.",
            style="font-size:0.75rem; color:#888; margin-top:6px;"
        ),
        style=(
            "background:#fff; border:1px solid #dee2e6; border-radius:10px; "
            "padding:16px; margin-bottom:20px;"
        )
    )


# =============================================================================
# UI Definition
# =============================================================================

app_ui = ui.page_fluid(
    ui.tags.head(
        ui.tags.style("""
            .app-header { background:#003262; color:white; padding:20px; margin-bottom:20px; }
            .input-section { background:#f8f9fa; padding:15px; border-radius:8px; border:1px solid #dee2e6; }
            .nav-link { font-size:0.88rem; }
            table { font-size:0.85rem; }
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
        ui.p("Compare existing inventory against Energy Star models"),
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
                        ui.input_select( "temp", "Operating Temperature", choices=["-80ºC", "-70ºC"]),
                        ui.input_switch( "is_es","Is currently Energy Star?"),
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
                            "Select any model from the Energy Star database. "
                            "The KNN-recommended model (closest capacity, lowest energy) is pre-selected.",
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
            width=380
        ),

        # ── Main Panel ────────────────────────────────────────────────────────
        ui.navset_tab(

            # Tab 1: Calculator
            ui.nav_panel(
                "📊 Calculator",
                ui.div(
                    _specs_panel(),

                    # KPI Summary
                    ui.div("Inventory Summary", class_="section-title"),
                    ui.output_ui("kpi_summary"),

                    # Live comparison
                    ui.div("Live Comparison", class_="section-title"),
                    ui.output_ui("comparison_card"),

                    # Inventory table
                    ui.div("Freezer Comparison Inventory", class_="section-title"),
                    ui.output_table("inventory_table"),
                    ui.hr(),
                    ui.p(
                        "Existing unit energy is estimated via linear regression (age, capacity, Energy Star status). "
                        "Desired unit energy is the actual measured value from the Energy Star database. "
                        "Savings = existing predicted annual kWh minus desired actual annual kWh. "
                        "Rebates reflect 2026 CPUC size categories.",
                        style="font-size:0.82rem; color:#555;"
                    ),
                    style="padding-top:16px;"
                )
            ),

            # Tab 2: Energy Star Search
            ui.nav_panel(
                "🔍 Energy Star Model Search",
                ui.div(
                    _energystar_panel(),
                    style="padding-top:16px;"
                )
            ),

            id="main_tabs"
        )
    )
)

app = App(app_ui, server)