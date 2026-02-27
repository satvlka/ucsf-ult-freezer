from shiny import App, reactive, render, ui
import pandas as pd
import numpy as np


# =============================================================================
# Constants and Logic
# =============================================================================
CONST_BASE = 1.6569
COEF_ES = -0.4006
COEF_VOL = -0.0333
COEF_AGE = 0.0168
DEFAULT_ELECTRICITY_PRICE = 0.29  # $/kWh

# Load Database
df_es = pd.read_csv("ult_freezer_database.csv")

# =============================================================================
# Server Logic
# =============================================================================

def server(input, output, session):
    # Reactive storage for the session inventory
    inventory = reactive.Value(pd.DataFrame())
    # Monotonically increasing counter so IDs remain unique after deletions
    unit_counter = reactive.Value(0)

    @reactive.effect
    @reactive.event(input.add_btn)
    def _add_unit():
        # 1. kNN Logic for Benchmarking
        col_80 = "Daily Energy Consumption at -80ºC (kWh/day)"
        col_70 = "Daily Energy Consumption at -70ºC (kWh/day)"

        using_fallback = False
        if input.temp() == "-70ºC":
            if col_70 in df_es.columns:
                target_col = col_70
            else:
                target_col = col_80
                using_fallback = True
        else:
            target_col = col_80

        temp_df = df_es.copy()
        temp_df['dist'] = (temp_df['Total Volume (cu. ft.)'] - input.vol()).abs()
        neighbors = temp_df.nsmallest(5, 'dist')

        # Benchmark Stats (Energy Star average for similar-sized units)
        avg_es_daily = neighbors[target_col].mean()
        avg_es_year = avg_es_daily * 365
        best_model = neighbors.loc[neighbors[target_col].idxmin()]['Model Name']

        # 2. Regression Logic
        # Predicted energy use WITH current ES status (for display)
        es_adj = COEF_ES if input.is_es() else 0
        pred_daily_kwh = CONST_BASE + es_adj + (COEF_VOL * input.vol()) + (COEF_AGE * input.age())
        pred_efficiency = pred_daily_kwh / input.vol() if input.vol() > 0 else 0
        pred_year_kwh = pred_daily_kwh * 365

        # Predicted energy use WITHOUT ES adjustment (non-ES baseline for savings calc)
        # Savings = how much the unit could save if replaced with a best-in-class ES model
        baseline_daily_kwh = CONST_BASE + (COEF_VOL * input.vol()) + (COEF_AGE * input.age())
        baseline_year_kwh = baseline_daily_kwh * 365
        annual_kwh_savings = max(0.0, baseline_year_kwh - avg_es_year)
        annual_cost_savings = annual_kwh_savings * input.elec_rate()

        # 3. Update counter and inventory
        new_count = unit_counter() + 1
        unit_counter.set(new_count)

        fallback_note = " (⚠ -70ºC data unavailable, used -80ºC benchmark)" if using_fallback else ""

        new_row = pd.DataFrame({
            "ID": [f"Unit {new_count}"],
            "Storage Volume (cu. ft.)": [input.vol()],
            "Age (Years)": [input.age()],
            "Temp": [input.temp() + fallback_note],
            "Energy Star?": ["Yes" if input.is_es() else "No"],
            "Predicted Efficiency (kWh/cu ft/day)": [round(pred_efficiency, 3)],
            "Predicted Energy Use (kWh/day)": [round(pred_daily_kwh, 2)],
            "Predicted Energy Use (kWh/Year)": [round(pred_year_kwh, 0)],
            "ES Benchmark Avg (kWh/Year)": [round(avg_es_year, 0)],
            "Potential Annual Savings (kWh/Year)": [round(annual_kwh_savings, 0)],
            "Potential Annual Cost Savings ($/Year)": [round(annual_cost_savings, 2)],
            "Recommended Energy Star Model (Closest Volume)": [best_model],
        })

        inventory.set(pd.concat([inventory(), new_row], ignore_index=True))

    @reactive.effect
    @reactive.event(input.clear_btn)
    def _clear_inventory():
        inventory.set(pd.DataFrame())
        # Counter intentionally NOT reset so IDs remain unique across sessions

    @output
    @render.table
    def inventory_table():
        df = inventory()
        if df.empty:
            return pd.DataFrame({"Status": ["No units added yet. Use the sidebar to add a freezer."]})
        return df

    @output
    @render.ui
    def freezer_selector():
        df = inventory()
        if df.empty:
            return ui.div()
        choices = {row['ID']: row['ID'] for _, row in df.iterrows()}
        return ui.input_select("selected_id", "Quick Detail Select:", choices)

    @output
    @render.ui
    def detail_card():
        df = inventory()
        if df.empty:
            return ui.div()
        sel = input.selected_id() if hasattr(input, 'selected_id') else None
        if not sel:
            return ui.div()
        row = df[df['ID'] == sel]
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
                style="font-size:0.85rem;"
            ),
            style="background:#fff; border:1px solid #dee2e6; border-radius:6px; padding:12px; margin-top:10px;"
        )


# =============================================================================
# UI Definition
# =============================================================================

app_ui = ui.page_fluid(
    ui.tags.head(
        ui.tags.style("""
            .app-header { background: #003262; color: white; padding: 20px; margin-bottom: 20px; }
            .input-section { background: #f8f9fa; padding: 15px; border-radius: 8px; border: 1px solid #dee2e6; }
            table { font-size: 0.85rem; }
            .btn-danger-outline { color: #dc3545; border-color: #dc3545; background: white; }
            .btn-danger-outline:hover { background: #dc3545; color: white; }
        """)
    ),

    ui.div(
        ui.h2("UC ULT Freezer Savings Calculator"),
        ui.p("Benchmarking Facility Inventory against Energy Star performance"),
        class_="app-header"
    ),

    ui.layout_sidebar(
        ui.sidebar(
            ui.div(
                ui.h4("Input Parameters"),
                ui.input_numeric("vol", "Storage Volume (cu. ft.)", value=20.0, min=0.1, step=0.1),
                ui.input_numeric("age", "Age of Unit (Years)", value=5, min=0),
                ui.input_select("temp", "Operating Temperature", choices=["-80ºC", "-70ºC"]),
                ui.input_switch("is_es", "Is currently Energy Star?"),
                ui.hr(),
                ui.input_numeric(
                    "elec_rate", "Electricity Rate ($/kWh)",
                    value=DEFAULT_ELECTRICITY_PRICE, min=0.01, step=0.01
                ),
                ui.input_action_button("add_btn", "Add to Inventory", class_="btn-primary w-100"),
                ui.br(), ui.br(),
                ui.input_action_button(
                    "clear_btn", "Clear Inventory",
                    class_="btn-danger-outline w-100"
                ),
                ui.hr(),
                ui.output_ui("freezer_selector"),
                ui.output_ui("detail_card"),
                class_="input-section"
            ),
            width=370
        ),

        ui.div(
            ui.h4("Calculated Freezer Inventory"),
            ui.output_table("inventory_table"),
            ui.hr(),
            ui.p(
                "Note: 'Potential Annual Savings' reflects how much energy could be saved by replacing "
                "the unit with a comparably-sized Energy Star model. Calculations are based on linear "
                "regression and k-Nearest Neighbors analysis of current Energy Star models."
            )
        )
    )
)

app = App(app_ui, server)