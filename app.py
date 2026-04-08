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
ULT_ELIGIBLE_BUILDING_TYPES = [
    "Education – University (incl. Community Colleges)",
    "Health/Medical – Hospital",
    "Manufacturing – Biotech",
    "Manufacturing – Pharmaceutical",
    "Other Agricultural",
    "Health/Medical – Clinics",
    "Food Processing",
    "Other Industrial",
    "Metal Production and Fabrication",
    "Health/Medical – Nursing Home",
    "Retail – Single-Story Large",
    "Warehouse – Refrigerated",
]


# =============================================================================
# Helpers
# =============================================================================

def _energy_col(temp_str: str) -> str:
    """Return the correct CSV column for the chosen operating temperature."""
    return COL_70 if temp_str == "-70ºC" and COL_70 in df_es.columns else COL_80

def _knn_recommend(vol: float, temp_str: str, k: int = 5):
    """Return (best_model_name, neighbors_df) for the given volume & temp."""
    col = _energy_col(temp_str)
    tmp = df_es.copy()
    tmp["dist"] = (tmp["Total Volume (cu. ft.)"] - vol).abs()
    neighbors = tmp.nsmallest(k, "dist")
    best_model = neighbors.loc[neighbors[col].idxmin(), "Model Name"]
    return best_model, neighbors, col

def _lookup_model(model_name: str, temp_str: str):
    """Return the CSV row for a given model name."""
    row = df_es[df_es["Model Name"] == model_name]
    if row.empty:
        return None
    return row.iloc[0]

# =============================================================================
# Server Logic
# =============================================================================

def server(input, output, session):
    inventory   = reactive.Value(pd.DataFrame())
    unit_counter = reactive.Value(0)

    # ── Reactives ─────────────────────────────────────────────────────────────

    @reactive.Calc
    def _knn():
        return _knn_recommend(input.vol(), input.temp())

    @reactive.Calc
    def _existing_energy():
        """Regression-predicted energy for the existing unit."""
        es_adj          = COEF_ES if input.is_es() else 0
        pred_daily      = CONST_BASE + es_adj + (COEF_VOL * input.vol()) + (COEF_AGE * input.age())
        pred_efficiency = pred_daily / input.vol() if input.vol() > 0 else 0
        return {
            "daily_kwh":      round(pred_daily,      2),
            "year_kwh":       round(pred_daily * 365, 0),
            "efficiency":     round(pred_efficiency,  3),
        }

    @reactive.Calc
    def _desired_energy():
        """Actual CSV energy for the selected desired model."""
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

    # ── Desired unit model selector (pre-populated with KNN pick) ─────────────

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
                    ui.tags.td(ui.tags.strong("Volume"), style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{d['vol']} cu. ft.", style="padding:3px 8px;"),
                ),
                ui.tags.tr(
                    ui.tags.td(ui.tags.strong(f"Energy ({temp_label}{fallback})"), style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{d['daily_kwh']} kWh/day", style="padding:3px 8px;"),
                ),
                ui.tags.tr(
                    ui.tags.td(ui.tags.strong("Annual Energy"), style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{int(d['year_kwh']):,} kWh/yr", style="padding:3px 8px;"),
                ),
                ui.tags.tr(
                    ui.tags.td(ui.tags.strong("Efficiency"), style="padding:3px 8px; color:#555;"),
                    ui.tags.td(f"{d['efficiency']} kWh/cu ft/day", style="padding:3px 8px;"),
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
        avg_es_year  = neighbors[col].mean() * 365

        # Annual savings: existing regression vs desired CSV
        if desired:
            annual_kwh_savings  = max(0.0, existing["year_kwh"] - desired["year_kwh"])
        else:
            annual_kwh_savings  = max(0.0, existing["year_kwh"] - avg_es_year)
        annual_cost_savings = annual_kwh_savings * input.elec_rate()

        vol      = input.vol()
        size_cat = "< 20 cu. ft." if vol < 20 else "≥ 20 cu. ft."
        rebate   = ULT_REBATE_TABLE[size_cat]

        new_count = unit_counter() + 1
        unit_counter.set(new_count)

        desired_model_name = desired["model"]  if desired else best_model
        desired_vol        = desired["vol"]    if desired else "—"
        desired_daily      = desired["daily_kwh"] if desired else "—"
        desired_year       = int(desired["year_kwh"]) if desired else "—"

        new_row = pd.DataFrame({
            "ID":                                     [f"Unit {new_count}"],
            # ── Existing unit ──────────────────────────────────────────────────
            "Existing: Capacity (cu. ft.)":           [vol],
            "Existing: Age (Years)":                  [input.age()],
            "Existing: Temp":                         [input.temp()],
            "Existing: Energy Star?":                 ["Yes" if input.is_es() else "No"],
            "Existing: Pred. Energy (kWh/day)":       [existing["daily_kwh"]],
            "Existing: Pred. Energy (kWh/yr)":        [int(existing["year_kwh"])],
            "Existing: Efficiency (kWh/cu ft/day)":   [existing["efficiency"]],
            # ── Desired unit ──────────────────────────────────────────────────
            "Desired: Model":                         [desired_model_name],
            "Desired: Volume (cu. ft.)":              [desired_vol],
            "Desired: Actual Energy (kWh/day)":       [desired_daily],
            "Desired: Actual Energy (kWh/yr)":        [desired_year],
            # ── Savings & rebate ──────────────────────────────────────────────
            "Annual Energy Savings (kWh/yr)":         [round(annual_kwh_savings, 0)],
            "Annual Cost Savings ($/yr)":             [round(annual_cost_savings, 2)],
            "Size Category (2026)":                   [size_cat],
            "Rebate Amount (2026)":                   [f"${rebate:,}"],
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
        """Live side-by-side comparison before adding to inventory."""
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
                # Existing column
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
                # Desired column
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
            ui.tags.td(cat, style="padding:5px 10px; border-top:1px solid #dee2e6;"),
            ui.tags.td(f"${amt:,}", style="padding:5px 10px; border-top:1px solid #dee2e6;"),
        )
        for cat, amt in ULT_REBATE_TABLE.items()
    ]
    building_items = [ui.tags.li(b, style="margin-bottom:2px;") for b in ULT_ELIGIBLE_BUILDING_TYPES]
    ice_rows = [
        ui.tags.tr(
            ui.tags.th("Tier",       style="padding:6px 10px; background:#dce8f5; text-align:left;"),
            ui.tags.th("Incentive",  style="padding:6px 10px; background:#dce8f5; text-align:left;"),
            ui.tags.th("Eligibility",style="padding:6px 10px; background:#dce8f5; text-align:left;"),
        )
    ] 
    return ui.div(
        ui.div(
            ui.div(
                ui.h5("ULT Freezers — 2026 Program Update", style="color:#003262; margin-top:0; font-size:0.95rem;"),
                ui.p("Effective January 1, 2026. Certified to ", ui.tags.strong("ENERGY STAR Lab Grade Spec v2.0"), ".", style="font-size:0.82rem;"),
                ui.h6("Rebate Amounts", style="margin-bottom:4px;"),
                ui.tags.table(*ult_rebate_rows, style="border-collapse:collapse; font-size:0.84rem; margin-bottom:6px; width:100%;"),
                ui.p(f"Sales incentive: ${ULT_SALES_INCENTIVE}/unit (unchanged).", style="font-size:0.8rem; color:#555; margin-bottom:8px;"),
                ui.h6("Eligible Building Types (2026)", style="margin-bottom:4px;"),
                ui.tags.ul(*building_items, style="font-size:0.8rem; margin:0 0 4px 0; padding-left:18px;"),
            ),
            style="display:flex; flex-wrap:wrap; gap:8px;"
        ),
        style="background:#f0f4f8; border:1px solid #c8d6e5; border-radius:8px; padding:16px; margin-bottom:20px;"
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
                # ── Tab 1: Existing Unit ─────────────────────────────────────
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
                # ── Tab 2: Desired Unit ──────────────────────────────────────
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
        ui.div(
            _specs_panel(),
            ui.output_ui("comparison_card"),
            ui.h4("Freezer Comparison Inventory"),
            ui.output_table("inventory_table"),
            ui.hr(),
            ui.p(
                "Existing unit energy is estimated via linear regression (age, capacity, Energy Star status). "
                "Desired unit energy is the actual measured value from the Energy Star database. "
                "Savings = existing predicted annual kWh minus desired actual annual kWh. "
                "Rebates reflect 2026 CPUC size categories.",
                style="font-size:0.82rem; color:#555;"
            )
        )
    )
)

app = App(app_ui, server)
