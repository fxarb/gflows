import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from dash import Dash, html, Input, Output, ctx, no_update, State
from dash.dcc import send_data_frame
from dash.exceptions import PreventUpdate

import textwrap
from pandas import DataFrame, concat
from flask_caching import Cache
from modules.calc import get_options_data
from modules.ticker_dwn import dwn_data
from modules.layout import serve_layout
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers import cron, combining
from datetime import timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from os import environ

load_dotenv()  # load environment variables from .env

# The Dash application instance.
app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,  # Dark theme for the app.
        dbc.themes.FLATLY,  # Light theme for the app.
        dbc.icons.BOOTSTRAP,  # Icons for the app.
    ],
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    ],
    title="G|Flows",
    update_title=None,
)

# The cache for the app.
cache = Cache(
    app.server,
    config={
        "CACHE_TYPE": "FileSystemCache",
        "CACHE_DIR": "cache",
        "CACHE_THRESHOLD": 150,
    },
)

cache.clear()

# The layout of the app.
app.layout = serve_layout
# The Flask server instance.
server = app.server


@cache.memoize(timeout=60 * 15)  # cache charts for 15 min
def analyze_data(ticker, expir):
    """
    Analyzes the options data for a given ticker and expiration date.

    :param ticker: The ticker symbol of the stock.
    :param expir: The expiration date of the options.
    """
    # Analyze stored data of specified ticker and expiry
    # defaults: json format, timezone 'America/New_York'
    result = get_options_data(
        ticker,
        expir,
        is_json=True,  # False for CSV
        tz="Asia/Shanghai",
    )
    return result if result else (None,) * 16


def cache_data(ticker, expir):
    """
    Caches the options data for a given ticker and expiration date.

    :param ticker: The ticker symbol of the stock.
    :param expir: The expiration date of the options.
    """
    data = analyze_data(ticker, expir)
    if not cache.has(f"{ticker}_{expir}"):
        cache.set(  # for client/server sync
            f"{ticker}_{expir}",
            {
                "ticker": ticker,
                "expiration": expir,
                "spot_price": data[3],
                "today_ddt": data[1],
                "today_ddt_string": data[2],
                "zero_delta": data[11],
                "zero_gamma": data[12],
            },
        )
    return data


def sensor(select=None):
    """
    Downloads the options data for the selected tickers.

    :param select: A list of tickers to download data for. If None, downloads data for all tickers.
    """
    # default: all tickers, json format
    dwn_data(select, is_json=True)  # False for CSV
    cache.clear()


def check_for_retry():
    """
    Checks if there are any tickers that need to be redownloaded.
    """
    tickers = cache.get("retry")
    if tickers:
        print("\nRedownloading data due to missing greek exposure...\n")
        sensor(select=tickers)


# respond to prompt if env variable not set
# The response from the user to download recent data.
response = environ.get("AUTO_RESPONSE")
if not response:
    try:
        response = input("\nDownload recent data? (y/n): ")
    except EOFError:
        response = "n"
if response.strip().lower() == "y":  # download data at start
    sensor()
else:
    print("\nUsing existing data...\n")

# schedule when to redownload data
# The scheduler for redownloading data.
sched = BackgroundScheduler(daemon=True)
sched.add_job(
    sensor,
    combining.OrTrigger(
        [
            cron.CronTrigger(
                minute="30,45", hour="9", day_of_week="0-4", timezone=ZoneInfo("Asia/Shanghai")
            ),
            cron.CronTrigger(
                minute="*/15", hour="10", day_of_week="0-4", timezone=ZoneInfo("Asia/Shanghai")
            ),
            cron.CronTrigger(
                minute="0,15,30", hour="11", day_of_week="0-4", timezone=ZoneInfo("Asia/Shanghai")
            ),
            cron.CronTrigger(
                minute="*/15", hour="13-14", day_of_week="0-4", timezone=ZoneInfo("Asia/Shanghai")
            ),
            cron.CronTrigger(
                minute="0", hour="15", day_of_week="0-4", timezone=ZoneInfo("Asia/Shanghai")
            ),
        ]
    ),
)
sched.add_job(
    check_for_retry,
    combining.OrTrigger(
        [
            cron.CronTrigger(
                day_of_week="0-4",
                hour="9",
                minute="30-59",
                second="*/5",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
            cron.CronTrigger(
                day_of_week="0-4",
                hour="10",
                second="*/5",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
            cron.CronTrigger(
                day_of_week="0-4",
                hour="11",
                minute="0-30",
                second="*/5",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
            cron.CronTrigger(
                day_of_week="0-4",
                hour="13-14",
                second="*/5",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
            cron.CronTrigger(
                day_of_week="0-4",
                hour="15",
                minute="0",
                second="*/5",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
        ]  # during the specified times, check every 5 seconds for a retry condition
    ),
)
sched.start()


app.clientside_callback(  # toggle light or dark theme
    """ 
    (themeToggle, theme) => {
        let themeLink = themeToggle ? theme[1] : theme[0]
        let kofiBtn = themeToggle ? "dark" : "light"
        let kofiLink = themeToggle ? "link-light" : "link-dark"
        let stylesheets = document.querySelectorAll(
            'link[rel=stylesheet][href^="https://cdn.jsdelivr"]'
        )
        // Update main theme
        stylesheets[1].href = themeLink
        // Update buffer after a short delay
        setTimeout(() => {stylesheets[0].href = themeLink;}, 100)
        return [kofiBtn, kofiLink]
    }
    """,
    [Output("kofi-btn", "color"), Output("kofi-link-color", "className")],
    [Input("switch", "value"), State("theme-store", "data")],
)


@app.callback(
    Output("exp-value", "data"),
    Output("all-btn", "active"),
    Output("exp-dropdown", "value"),
    Input("exp-dropdown", "value"),
    Input("all-btn", "n_clicks"),
    State("exp-value", "data"),
)
def on_click_expirations(value, btn, expiration):
    """
    Handles the selection of an expiration date.

    :param value: The value of the selected expiration date.
    :param btn: The number of clicks on the all button.
    :param expiration: The current expiration date.
    """
    if not ctx.triggered_id and expiration:
        value = f"{expiration}-btn" if expiration != "all" else "all-btn"

    if ctx.triggered_id == "all-btn" or value == "all-btn":
        return "all", True, None
    else:
        button_map = {
            "this-month-btn": ("this-month", False, "this-month-btn"),
            "next-month-btn": ("next-month", False, "next-month-btn"),
            "this-season-btn": ("this-season", False, "this-season-btn"),
            "next-season-btn": ("next-season", False, "next-season-btn"),
        }
        return button_map.get(value, ("next-month", False, "next-month-btn"))


@app.callback(  # handle selected option greek
    Output("greek-value", "data"),
    Output("delta-btn", "active"),
    Output("gamma-btn", "active"),
    Output("vanna-btn", "active"),
    Output("charm-btn", "active"),
    Output("pagination", "active_page"),
    Output("live-dropdown", "options"),
    Output("live-dropdown", "value"),
    Input("delta-btn", "n_clicks"),
    Input("gamma-btn", "n_clicks"),
    Input("vanna-btn", "n_clicks"),
    Input("charm-btn", "n_clicks"),
    Input("pagination", "active_page"),
    Input("live-dropdown", "value"),
    State("greek-value", "data"),
)
def on_click_greeks(btn1, btn2, btn3, btn4, active_page, value, greek):
    """
    Handles the selection of an option greek.

    :param btn1: The number of clicks on the delta button.
    :param btn2: The number of clicks on the gamma button.
    :param btn3: The number of clicks on the vanna button.
    :param btn4: The number of clicks on the charm button.
    :param active_page: The active page of the pagination.
    :param value: The value of the live dropdown.
    :param greek: The current greek value.
    """
    if not ctx.triggered_id and greek:
        is_active1, is_active2, is_active3, is_active4 = greek["is_active"]
        active_page, options, value = (
            greek["active_page"],
            greek["options"],
            greek["value"],
        )
    elif ctx.triggered_id == "live-dropdown":
        is_active1, is_active2, is_active3, is_active4 = greek["is_active"]
        active_page, options = greek["active_page"], greek["options"]
    elif ctx.triggered_id == "pagination":
        is_active1, is_active2, is_active3, is_active4 = greek["is_active"]
        options, value = greek["options"], greek["value"]
    else:
        button_map = {
            "delta-btn": (
                True,
                False,
                False,
                False,
                [
                    "Absolute Delta Exposure",
                    "Delta Exposure By Calls/Puts",
                    "Delta Exposure Profile",
                ],
                "Absolute Delta Exposure",
            ),
            "gamma-btn": (
                False,
                True,
                False,
                False,
                [
                    "Absolute Gamma Exposure",
                    "Gamma Exposure By Calls/Puts",
                    "Gamma Exposure Profile",
                ],
                "Absolute Gamma Exposure",
            ),
            "vanna-btn": (
                False,
                False,
                True,
                False,
                [
                    "Absolute Vanna Exposure",
                    "Implied Volatility Average",
                    "Vanna Exposure Profile",
                ],
                "Absolute Vanna Exposure",
            ),
            "charm-btn": (
                False,
                False,
                False,
                True,
                [
                    "Absolute Charm Exposure",
                    "Charm Exposure Profile",
                ],
                "Absolute Charm Exposure",
            ),
        }
        is_active1, is_active2, is_active3, is_active4, options, value = button_map[
            ctx.triggered_id or "delta-btn"
        ]

    greek = {
        "is_active": (is_active1, is_active2, is_active3, is_active4),
        "active_page": active_page,
        "options": options,
        "value": value,
    }

    return (
        greek,
        is_active1,
        is_active2,
        is_active3,
        is_active4,
        active_page,
        options,
        value,
    )


@app.callback(  # handle refreshed data
    Output("refresh", "data"),
    Output("interval", "n_intervals"),
    Input("interval", "n_intervals"),
    State("tabs", "active_tab"),
    State("exp-value", "data"),
    State("live-chart", "figure"),
)
def check_cache_key(n_intervals, stock, expiration, fig):
    """
    Checks if the data in the cache is up to date.

    :param n_intervals: The number of intervals that have passed.
    :param stock: The stock ticker.
    :param expiration: The expiration date.
    :param fig: The figure object.
    """
    data = cache.get(f"{stock.lower()}_{expiration}")
    if not data and stock and expiration:
        cache_data(stock.lower(), expiration)
    if (
        data
        and (fig and fig["data"])
        and (
            data["today_ddt_string"]
            and data["ticker"] == stock.lower()
            and data["ticker"].upper()
            in fig["layout"]["title"]["text"].replace("<br>", " ")
            and data["expiration"] == expiration
        )
        and (
            data["today_ddt_string"]
            not in fig["layout"]["title"]["text"].replace("<br>", " ")
            or (
                "shapes" in fig["layout"]
                and "name" in fig["layout"]["shapes"][-1]
                and data["spot_price"] != fig["layout"]["shapes"][-1]["x0"]
            )
        )
    ):  # refresh on current selection if client data differs from server cache
        return data, 0
    raise PreventUpdate


@app.callback(  # handle export menu
    Output("export-df-csv", "data"),
    Input("btn-chart-data", "n_clicks"),
    Input("btn-sig-points", "n_clicks"),
    State("tabs", "active_tab"),
    State("exp-value", "data"),
    State("pagination", "active_page"),
    State("live-dropdown", "value"),
    State("live-chart", "figure"),
    prevent_initial_call=True,
)
def handle_menu(btn1, btn2, stock, expiration, active_page, value, fig):
    """
    Handles the export menu.

    :param btn1: The number of clicks on the chart data button.
    :param btn2: The number of clicks on the significant points button.
    :param stock: The stock ticker.
    :param expiration: The expiration date.
    :param active_page: The active page of the pagination.
    :param value: The value of the live dropdown.
    :param fig: The figure object.
    """
    data = cache.get(f"{stock.lower()}_{expiration}")
    if not data or not data["today_ddt"] or not fig["data"]:
        raise PreventUpdate

    fig_data = fig["data"]

    if not fig_data[0]["y"]:
        raise PreventUpdate

    if expiration != "all":
        exp_date = expiration.replace("-", "_")
    else:
        exp_date = "All_Expirations"

    date_condition = active_page == 2 and not "Profile" in value
    prefix = "Strikes" if not date_condition else "Dates"
    formatted_date = str(data["today_ddt"]).replace(" ", "_")
    chart_name = value.replace(" ", "_")
    filename = f"{prefix}_{chart_name}_{exp_date}__{formatted_date}.csv"

    # --- X-axis Data (DataFrame Index) Preparation ---

    # Get the x-axis data from the figure
    x_data_source = fig_data[0].get("x")

    if x_data_source is None:
        raise PreventUpdate("X-axis data (fig_data[0]['x']) is missing.")

    # Determine how to extract index values based on the type of x_data_source
    if isinstance(x_data_source, list):
        index_values = x_data_source
    elif isinstance(x_data_source, dict) and "_inputArray" in x_data_source:
        input_array = x_data_source.get("_inputArray")
        if isinstance(input_array, dict):
            # Extract values from the dictionary where keys are digits
            index_values = [v for k, v in input_array.items() if k.isdigit()]
        else:
            raise PreventUpdate("fig_data[0]['x']['_inputArray'] is not a dictionary.")
    else:
        raise PreventUpdate(
            f"Unrecognized type for fig_data[0]['x']: {type(x_data_source)}."
        )

    # --- Y-axis Data (DataFrame Columns) Preparation ---

    # Extract y-series data and column names
    def extract_series_data(item):
        y_component = item.get("y")
        series_name = item.get("name", "Unnamed Series")

        if isinstance(y_component, dict) and "_inputArray" in y_component:
            input_array = y_component.get("_inputArray")
            if isinstance(input_array, dict) and input_array:
                series_values = [v for k, v in input_array.items() if k.isdigit()]
                return (series_values, series_name) if series_values else None
        elif isinstance(y_component, list) and y_component:
            return (y_component, series_name)
        return None

    valid_series = [
        result for item in fig_data if (result := extract_series_data(item)) is not None
    ]

    if not valid_series:
        raise PreventUpdate("No data series with values found to create DataFrame.")

    y_series_data, column_names = zip(*valid_series)

    # --- DataFrame Creation ---

    df_agg = DataFrame(
        data=list(zip(*y_series_data)),
        index=index_values,
        columns=column_names,
    )
    df_agg.index.name = prefix

    if ctx.triggered_id == "btn-chart-data":
        return send_data_frame(
            df_agg.to_csv,
            f"{stock}_{filename}",
        )
    elif ctx.triggered_id == "btn-sig-points":
        significant_points = DataFrame(
            {
                f"Signif_{col.replace(' ', '_')}": concat(
                    [
                        df_agg.loc[df_agg[col] > 0, col].nlargest(5),
                        df_agg.loc[df_agg[col] < 0, col].nsmallest(5),
                    ]
                )
                for col in df_agg.columns
            }
        )
        if "Delta" in value:
            significant_points["Delta_Flip"] = data["zero_delta"]
        elif "Gamma" in value:
            significant_points["Gamma_Flip"] = data["zero_gamma"]
        return send_data_frame(
            significant_points.fillna(0).to_csv,
            f"{stock}_SigPoints_{filename}",
        )


@app.callback(  # handle chart display based on inputs
    Output("live-chart", "figure"),
    Output("live-chart", "style"),
    Output("pagination-div", "hidden"),
    Input("live-dropdown", "value"),
    Input("tabs", "active_tab"),
    Input("exp-value", "data"),
    Input("pagination", "active_page"),
    Input("refresh", "data"),
    Input("switch", "value"),
)
def update_live_chart(value, stock, expiration, active_page, refresh, toggle_dark):
    """
    Updates the live chart based on user inputs.

    :param value: The value of the live dropdown.
    :param stock: The stock ticker.
    :param expiration: The expiration date.
    :param active_page: The active page of the pagination.
    :param refresh: The refresh data.
    :param toggle_dark: A boolean indicating whether to use the dark theme.
    """
    (
        df,
        today_ddt,
        today_ddt_string,
        spot_price,
        from_strike,
        to_strike,
        levels,
        totaldelta,
        totalgamma,
        totalvanna,
        totalcharm,
        zerodelta,
        zerogamma,
        call_ivs,
        put_ivs,
    ) = cache_data(stock.lower(), expiration)

    # chart theme and layout
    xaxis, yaxis = dict(
        gridcolor="lightgray", minor=dict(ticklen=5, tickcolor="#000", showgrid=True)
    ), dict(gridcolor="lightgray", minor=dict(tickcolor="#000"))
    layout = {
        "title_x": 0.5,
        "title_font_size": 12.5,
        "title_xref": "paper",
        "legend": dict(
            orientation="v",
            yanchor="top",
            xanchor="right",
            y=0.98,
            x=0.98,
            bgcolor="rgba(0,0,0,0.1)",
            font_size=10,
        ),
        "showlegend": True,
        "margin": dict(l=0, r=40),
        "xaxis": xaxis,
        "yaxis": yaxis,
        "dragmode": "pan",
    }
    if not toggle_dark:
        # light theme
        pio.templates["custom_template"] = pio.templates["seaborn"]
    else:
        # dark theme
        pio.templates["custom_template"] = pio.templates["plotly_dark"]
        for axis in [xaxis, yaxis]:
            axis["gridcolor"], axis["minor"]["tickcolor"] = "#373737", "#707070"
        layout["paper_bgcolor"] = "#222222"
        layout["plot_bgcolor"] = "rgba(40, 40, 50, 0.8)"
    pio.templates["custom_template"].update(layout=layout)
    pio.templates.default = "custom_template"

    if df.empty:
        return (
            go.Figure(layout={"title_text": f"{stock} data unavailable, retry later"}),
            {},
            True,
        )

    retry_cache = cache.get("retry")
    if (
        df["total_delta"].sum() == 0
        and (not retry_cache or stock not in retry_cache)
    ):
        # if data hasn't expired and total delta exposure is 0,
        # set a 'retry' for the scheduler to catch
        retry_cache = retry_cache or []
        retry_cache.append(stock)
        cache.set("retry", retry_cache)

    date_condition = active_page == 2 and not "Profile" in value
    if not date_condition:
        df_agg = df.groupby(["strike_price"]).sum(numeric_only=True)
        df_agg = df_agg[from_strike:to_strike]  # filter for relevance
        call_ivs, put_ivs = call_ivs["strike"], put_ivs["strike"]
    else:  # use dates
        df_agg = df.groupby(["expiration_date"]).sum(numeric_only=True)
        # df_agg = df_agg[: today_ddt + timedelta(weeks=52)] # filter for relevance
        call_ivs, put_ivs = call_ivs["exp"], put_ivs["exp"]

    legend_title_map = {
        "this-month": "This Month",
        "next-month": "Next Month",
        "this-season": "This Season",
        "next-season": "Next Season",
    }
    legend_title = (
        legend_title_map.get(expiration) if expiration != "all" else "All Expirations"
    )

    strikes = df_agg.index.to_numpy()

    is_profile_or_volatility = "Profile" in value or "Average" in value
    name = value.split()[1] if "Absolute" in value else value.split()[0]

    name_to_vals = {
        "Delta": (
            f"per 1% {stock} Move",
            f"{name} Exposure (price / 1% move)",
            zerodelta,
        ),
        "Gamma": (
            f"per 1% {stock} Move",
            f"{name} Exposure (delta / 1% move)",
            zerogamma,
        ),
        "Vanna": (
            f"per 1% {stock} IV Move",
            f"{name} Exposure (delta / 1% IV move)",
            0,
        ),
        "Charm": (
            f"a day til {stock} Expiry",
            f"{name} Exposure (delta / day til expiry)",
            0,
        ),
        "Implied": ("", "Implied Volatility (IV) Average", 0),
    }

    description, y_title, zeroflip = name_to_vals[name]
    yaxis.update(title_text=y_title)
    scale = 10**9

    if "Absolute" in value:
        fig = go.Figure(
            data=[
                go.Bar(
                    name=name + " Exposure",
                    x=strikes,
                    y=df_agg[f"total_{name.lower()}"].to_numpy(),
                    marker=dict(
                        line=dict(
                            width=0.25,
                            color=("#2B5078" if not toggle_dark else "#8795FA"),
                        ),
                    ),
                )
            ]
        )
    elif "Calls/Puts" in value:
        fig = go.Figure(
            data=[
                go.Bar(
                    name="Call " + name,
                    x=strikes,
                    y=df_agg[f"call_{name[:1].lower()}ex"].to_numpy() / scale,
                    marker=dict(
                        line=dict(
                            width=0.25,
                            color=("#2B5078" if not toggle_dark else "#8795FA"),
                        ),
                    ),
                ),
                go.Bar(
                    name="Put " + name,
                    x=strikes,
                    y=df_agg[f"put_{name[:1].lower()}ex"].to_numpy() / scale,
                    marker=dict(
                        line=dict(
                            width=0.25,
                            color=("#9B5C30" if not toggle_dark else "#F5765B"),
                        ),
                    ),
                ),
            ]
        )

    if not is_profile_or_volatility:
        split_title = textwrap.wrap(
            f"Total {name}: $"
            + str("{:,.2f}".format(df[f"total_{name.lower()}"].sum() * scale))
            + f" {description}, {today_ddt_string}",
            width=50,
        )
        fig.update_layout(  # bar chart layout
            title_text="<br>".join(split_title),
            legend_title_text=legend_title,
            xaxis=xaxis,
            yaxis=yaxis,
            barmode="relative",
            modebar_remove=["autoscale", "lasso2d"],
        )
    if is_profile_or_volatility:
        fig = make_subplots(rows=1, cols=1)
        if not date_condition and name != "Implied":  # chart profiles
            split_title = textwrap.wrap(
                f"{stock} {name} Exposure Profile, {today_ddt_string}", width=50
            )
            name_to_vals = {
                "Delta": totaldelta["all"],
                "Gamma": totalgamma["all"],
                "Vanna": totalvanna["all"],
                "Charm": totalcharm["all"],
            }
            all_ex = name_to_vals[name]
            fig.add_trace(go.Scatter(x=levels, y=all_ex, name="All Expiries"))
            # show - &/or + areas of exposure depending on condition
            if name == "Charm" or name == "Vanna":
                all_ex_min, all_ex_max = all_ex.min(), all_ex.max()
                if all_ex_min < 0:
                    fig.add_hrect(
                        y0=0,
                        y1=all_ex_min * 1.5,
                        fillcolor="red",
                        opacity=0.1,
                        line_width=0,
                    )
                if all_ex_max > 0:
                    fig.add_hrect(
                        y0=0,
                        y1=all_ex_max * 1.5,
                        fillcolor="green",
                        opacity=0.1,
                        line_width=0,
                    )
                fig.add_hline(
                    y=0,
                    line_width=0,
                    name=name + " Flip",
                    annotation_text=name + " Flip",
                    annotation_position="top left",
                )
            # greek has a - to + flip
            elif zeroflip > 0:
                fig.add_vline(
                    x=zeroflip,
                    line_color="dimgray",
                    line_width=1,
                    name=name + " Flip",
                    annotation_text=name + " Flip: " + str("{:,.0f}".format(zeroflip)),
                    annotation_position="top left",
                )
                fig.add_vrect(
                    x0=from_strike,
                    x1=zeroflip,
                    fillcolor="red",
                    opacity=0.1,
                    line_width=0,
                )
                fig.add_vrect(
                    x0=zeroflip,
                    x1=to_strike,
                    fillcolor="green",
                    opacity=0.1,
                    line_width=0,
                )
            # flip unknown, assume - dominance
            elif all_ex[0] < 0:
                fig.add_vrect(
                    x0=from_strike,
                    x1=to_strike,
                    fillcolor="red",
                    opacity=0.1,
                    line_width=0,
                )
            # flip unknown, assume + dominance
            elif all_ex[0] > 0:
                fig.add_vrect(
                    x0=from_strike,
                    x1=to_strike,
                    fillcolor="green",
                    opacity=0.1,
                    line_width=0,
                )
        elif name == "Implied":  # in IV section, chart put/call IV averages
            fig.add_trace(
                go.Scatter(
                    x=strikes,
                    y=put_ivs * 100,
                    name="Put IV",
                    fill="tozeroy",
                    line_color="#C44E52",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=strikes,
                    y=call_ivs * 100,
                    name="Call IV",
                    fill="tozeroy",
                    line_color="#32A3A3",
                )
            )
            split_title = textwrap.wrap(
                f"{stock} IV Average, {today_ddt_string}", width=50
            )
        fig.add_hline(
            y=0,
            line_width=1,
            line_color="dimgray",
        )
        fig.update_layout(  # scatter chart layout
            title_text="<br>".join(split_title),
            legend_title_text=legend_title,
            xaxis=xaxis,
            yaxis=yaxis,
            modebar_remove=["autoscale"],
        )

    fig.update_xaxes(
        title="Strike" if not date_condition else "Date",
        showgrid=True,
        range=(
            [spot_price * 0.9, spot_price * 1.1]
            if not date_condition
            else (
                [
                    strikes[0] - timedelta(seconds=0.0625),
                    strikes[0] + timedelta(seconds=0.0625),
                ]
                if len(strikes) == 1
                else [today_ddt, today_ddt + timedelta(days=31)]
            )
        ),
        gridwidth=1,
        rangeslider=dict(visible=True),
    )
    fig.update_yaxes(
        showgrid=True,
        fixedrange=True,
        minor_ticks="inside",
        gridwidth=1,
    )

    if not date_condition:
        fig.add_vline(
            x=spot_price,
            line_color="#707070",
            line_width=1,
            line_dash="dash",
            name=stock + " Spot",
            annotation_text="Last: " + str("{:,.2f}".format(spot_price)),
            annotation_position="top",
        )

    is_pagination_hidden = "Profile" in value

    return fig, {}, is_pagination_hidden


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port="8050")
