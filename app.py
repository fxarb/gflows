import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from dash import Dash, html, Input, Output, ctx, no_update, State, ALL, clientside_callback
from dash.dcc import send_data_frame
from dash.exceptions import PreventUpdate

import textwrap
import pandas as pd
from pandas import DataFrame, concat
from flask_caching import Cache
from modules.calc import get_options_data
from modules.ticker_dwn import dwn_data
from modules.layout import serve_layout
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers import cron, combining
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from os import environ
from modules.logging_config import setup_logging

logger = setup_logging()
import logging
logging.getLogger('apscheduler').setLevel(logging.WARNING)
load_dotenv()

app = Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.DARKLY,
        dbc.themes.FLATLY,
        dbc.icons.BOOTSTRAP,
    ],
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
    ],
    title="G|Flows",
    update_title=None,
)

cache = Cache(
    app.server,
    config={
        "CACHE_TYPE": "FileSystemCache",
        "CACHE_DIR": "cache",
        "CACHE_THRESHOLD": 150,
    },
)

cache.clear()
app.layout = serve_layout
server = app.server

@cache.memoize(timeout=60)
def analyze_data(ticker, expir):
    logger.debug(f"Analyzing data for ticker: {ticker}, expiration: {expir}")
    result = get_options_data(
        ticker,
        expir,
        is_json=True,
        tz="Asia/Shanghai",
    )
    logger.debug(f"Analysis result: {result}")
    return result if result else (None,) * 15

def cache_data(ticker, expir):
    data = analyze_data(ticker, expir)
    if not cache.has(f"{ticker}_{expir}"):
        cache.set(
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
            timeout=60,
        )
    return data

def sensor(select=None):
    dwn_data(select, is_json=True)
    cache.clear()

def check_for_retry():
    tickers = cache.get("retry")
    if tickers:
        sensor(select=tickers)

response = environ.get("AUTO_RESPONSE")
if not response:
    try:
        response = input("\nDownload recent data? (y/n): ")
    except EOFError:
        response = "n"
if response.strip().lower() == "y":
    sensor()

sched = BackgroundScheduler(daemon=True)
sched.add_job(
    sensor,
    combining.OrTrigger(
        [
            cron.CronTrigger(day_of_week="0-4", hour="9", minute="15-59", timezone=ZoneInfo("Asia/Shanghai")),
            cron.CronTrigger(day_of_week="0-4", hour="10-14", minute="*", timezone=ZoneInfo("Asia/Shanghai")),
            cron.CronTrigger(day_of_week="0-4", hour="15", minute="0-30", timezone=ZoneInfo("Asia/Shanghai")),
        ]
    ),
)
sched.add_job(check_for_retry, trigger=IntervalTrigger(seconds=10), id="check_for_retry_job", replace_existing=True)
sched.start()

app.clientside_callback(
    """ 
    (themeToggle, theme) => {
        let themeLink = themeToggle ? theme[1] : theme[0]
        let kofiBtn = themeToggle ? "dark" : "light"
        let kofiLink = themeToggle ? "link-light" : "link-dark"
        let stylesheets = document.querySelectorAll('link[rel=stylesheet][href^="https://cdn.jsdelivr"]')
        stylesheets[1].href = themeLink
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

@app.callback(
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
    if not ctx.triggered_id and greek:
        is_active1, is_active2, is_active3, is_active4 = greek["is_active"]
        active_page, options, value = greek["active_page"], greek["options"], greek["value"]
    elif ctx.triggered_id == "live-dropdown":
        is_active1, is_active2, is_active3, is_active4 = greek["is_active"]
        active_page, options = greek["active_page"], greek["options"]
    elif ctx.triggered_id == "pagination":
        is_active1, is_active2, is_active3, is_active4 = greek["is_active"]
        options, value = greek["options"], greek["value"]
    else:
        button_map = {
            "delta-btn": (True, False, False, False, ["Absolute Delta Exposure", "Delta Exposure By Calls/Puts", "Delta Exposure Profile"], "Absolute Delta Exposure"),
            "gamma-btn": (False, True, False, False, ["Absolute Gamma Exposure", "Gamma Exposure By Calls/Puts", "Gamma Exposure Profile"], "Absolute Gamma Exposure"),
            "vanna-btn": (False, False, True, False, ["Absolute Vanna Exposure", "Implied Volatility Average", "Vanna Exposure Profile"], "Absolute Vanna Exposure"),
            "charm-btn": (False, False, False, True, ["Absolute Charm Exposure", "Charm Exposure Profile"], "Absolute Charm Exposure"),
        }
        is_active1, is_active2, is_active3, is_active4, options, value = button_map[ctx.triggered_id or "delta-btn"]
    greek = {"is_active": (is_active1, is_active2, is_active3, is_active4), "active_page": active_page, "options": options, "value": value}
    return greek, is_active1, is_active2, is_active3, is_active4, active_page, options, value

@app.callback(
    Output("refresh", "data"),
    Output("interval", "n_intervals"),
    Output("positions-store-v2", "data", allow_duplicate=True),
    Input("interval", "n_intervals"),
    State("tabs", "active_tab"),
    State("exp-value", "data"),
    State("live-chart", "figure"),
    State("positions-store-v2", "data"),
    prevent_initial_call=True,
)
def check_cache_key(n_intervals, stock, expiration, fig, positions):
    data = cache.get(f"{stock.lower()}_{expiration}")
    if not data and stock and expiration:
        cache_data(stock.lower(), expiration)
    new_positions = positions.copy() if positions else {}
    changed = False
    if positions:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        for key in list(new_positions.keys()):
            try:
                expiry_str = key.split("|")[1]
                expiry_dt = pd.to_datetime(expiry_str).tz_localize(ZoneInfo("Asia/Shanghai")) + timedelta(hours=15)
                if expiry_dt < now:
                    del new_positions[key]
                    changed = True
            except:
                del new_positions[key]
                changed = True
    if data and (fig and fig["data"]) and (data["today_ddt_string"] and data["ticker"] == stock.lower() and data["ticker"].upper() in fig["layout"]["title"]["text"].replace("<br>", " ") and data["expiration"] == expiration) and (data["today_ddt_string"] not in fig["layout"]["title"]["text"].replace("<br>", " ") or ("shapes" in fig["layout"] and "name" in fig["layout"]["shapes"][-1] and data["spot_price"] != fig["layout"]["shapes"][-1]["x0"])):
        return data, 0, new_positions if changed else no_update
    if changed:
        return no_update, no_update, new_positions
    raise PreventUpdate

@app.callback(
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
    data = cache.get(f"{stock.lower()}_{expiration}")
    if not data or not data["today_ddt"] or not fig["data"]: raise PreventUpdate
    fig_data = fig["data"]
    if not fig_data[0]["y"]: raise PreventUpdate
    exp_date = expiration.replace("-", "_") if expiration != "all" else "All_Expirations"
    date_condition = active_page == 2 and not "Profile" in value
    prefix = "Strikes" if not date_condition else "Dates"
    formatted_date = str(data["today_ddt"]).replace(" ", "_")
    chart_name = value.replace(" ", "_")
    filename = f"{prefix}_{chart_name}_{exp_date}__{formatted_date}.csv"
    x_data_source = fig_data[0].get("x")
    if x_data_source is None: raise PreventUpdate
    if isinstance(x_data_source, list): index_values = x_data_source
    elif isinstance(x_data_source, dict) and "_inputArray" in x_data_source: index_values = [v for k, v in x_data_source.get("_inputArray").items() if k.isdigit()]
    else: raise PreventUpdate
    def extract_series_data(item):
        y_component = item.get("y")
        series_name = item.get("name", "Unnamed Series")
        if isinstance(y_component, dict) and "_inputArray" in y_component:
            series_values = [v for k, v in y_component.get("_inputArray").items() if k.isdigit()]
            return (series_values, series_name) if series_values else None
        elif isinstance(y_component, list) and y_component: return (y_component, series_name)
        return None
    valid_series = [res for item in fig_data if (res := extract_series_data(item)) is not None]
    if not valid_series: raise PreventUpdate
    y_series_data, column_names = zip(*valid_series)
    df_agg = DataFrame(data=list(zip(*y_series_data)), index=index_values, columns=column_names)
    df_agg.index.name = prefix
    if ctx.triggered_id == "btn-chart-data": return send_data_frame(df_agg.to_csv, f"{stock}_{filename}")
    elif ctx.triggered_id == "btn-sig-points":
        sig = DataFrame({f"Signif_{c.replace(' ', '_')}": concat([df_agg.loc[df_agg[c] > 0, c].nlargest(5), df_agg.loc[df_agg[c] < 0, c].nsmallest(5)]) for c in df_agg.columns})
        if "Delta" in value: sig["Delta_Flip"] = data["zero_delta"]
        elif "Gamma" in value: sig["Gamma_Flip"] = data["zero_gamma"]
        return send_data_frame(sig.fillna(0).to_csv, f"{stock}_SigPoints_{filename}")

clientside_callback(
    """
    function(n_clicks, positions) {
        if (!dash_clientside.callback_context.triggered.length) return window.dash_clientside.no_update;
        let triggered = dash_clientside.callback_context.triggered[0];
        let prop_id = triggered.prop_id;
        if (!prop_id.includes('index')) return window.dash_clientside.no_update;
        let id = JSON.parse(prop_id.split('.')[0]);
        let parts = id.index.split('|');
        if (parts.length < 5) return window.dash_clientside.no_update;
        let pos_key = parts.slice(0,4).join('|');
        let new_positions = Object.assign({}, positions || {});
        let current_qty = new_positions[pos_key] || 0;
        if (parts[4] === 'plus') new_positions[pos_key] = current_qty + 1;
        else if (parts[4] === 'minus') new_positions[pos_key] = current_qty - 1;
        if (new_positions[pos_key] === 0) delete new_positions[pos_key];
        return new_positions;
    }
    """,
    Output("positions-store-v2", "data"),
    Input({"type": "pos-btn", "index": ALL}, "n_clicks"),
    State("positions-store-v2", "data"),
    prevent_initial_call=True,
)

@app.callback(
    Output("strike-list-container", "children"),
    Input("tabs", "active_tab"),
    Input("refresh", "data"),
    Input("positions-store-v2", "data"),
)
def render_strike_list(ticker, refresh, positions):
    if not ticker: return ""
    res = analyze_data(ticker.lower(), "all")
    df = res[0]
    if df is None or df.empty: return "Data unavailable"
    expiries = sorted(df["expiration_date"].unique())
    expiry_tabs = []
    btn_style = {"padding": "0px 2px", "fontSize": "9px", "height": "18px", "width": "18px", "lineHeight": "1"}
    text_style = {"fontSize": "9px", "whiteSpace": "nowrap", "overflow": "hidden"}
    col_style = {"padding": "0 1px"}

    for expiry in expiries:
        expiry_df = df[df["expiration_date"] == expiry]
        expiry_str = pd.to_datetime(expiry).strftime("%Y-%m-%d")
        header = html.Div([
            dbc.Row([
                dbc.Col(html.Div("CALLS", className="text-center bg-primary text-white small py-0"), width=5),
                dbc.Col(html.Div("STRIKE", className="text-center bg-secondary text-white small py-0"), width=2),
                dbc.Col(html.Div("PUTS", className="text-center bg-danger text-white small py-0"), width=5),
            ], className="gx-0"),
            dbc.Row([
                dbc.Col(html.Div("IV", className="text-center", style=text_style), width=1),
                dbc.Col(html.Div("Tho", className="text-center", style=text_style), width=1),
                dbc.Col(html.Div("Rho", className="text-center", style=text_style), width=1),
                dbc.Col(html.Div("Veg", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Tht", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Gam", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Del", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Strike", className="text-center", style=text_style), width=2),
                dbc.Col(html.Div("Del", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Gam", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Tht", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Veg", className="text-center", style=text_style), width=0.5),
                dbc.Col(html.Div("Rho", className="text-center", style=text_style), width=1),
                dbc.Col(html.Div("Tho", className="text-center", style=text_style), width=1),
                dbc.Col(html.Div("IV", className="text-center", style=text_style), width=1),
            ], className="gx-0 text-muted")
        ])
        rows = [header]
        for _, row in expiry_df.iterrows():
            strike = row["strike_price"]
            def fmt(v): return f"{v:.2f}" if pd.notnull(v) else "0.00"
            c_key, p_key = f"{ticker.lower()}|{expiry_str}|{strike}|C", f"{ticker.lower()}|{expiry_str}|{strike}|P"
            c_qty, p_qty = (positions.get(c_key, 0) if positions else 0), (positions.get(p_key, 0) if positions else 0)
            rows.append(dbc.Row([
                dbc.Col(html.Span(f"{row['call_iv']*100:.1f}", style=text_style), width=1, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['call_theo']), style=text_style), width=1, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['call_rho']), style=text_style), width=1, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['call_vega']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['call_theta']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['call_gamma']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['call_delta']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Div([
                    dbc.Button("-", id={"type": "pos-btn", "index": f"{c_key}|minus"}, size="sm", color="danger", outline=True, style=btn_style),
                    html.Span(str(c_qty), className="px-1", style=text_style),
                    dbc.Button("+", id={"type": "pos-btn", "index": f"{c_key}|plus"}, size="sm", color="success", outline=True, style=btn_style),
                    html.B(f"{strike:g}", className="mx-1", style={"fontSize": "10px"}),
                    dbc.Button("+", id={"type": "pos-btn", "index": f"{p_key}|plus"}, size="sm", color="success", outline=True, style=btn_style),
                    html.Span(str(p_qty), className="px-1", style=text_style),
                    dbc.Button("-", id={"type": "pos-btn", "index": f"{p_key}|minus"}, size="sm", color="danger", outline=True, style=btn_style),
                ], className="d-flex align-items-center justify-content-center"), width=2, style=col_style),
                dbc.Col(html.Span(fmt(row['put_delta']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['put_gamma']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['put_theta']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['put_vega']), style=text_style), width=0.5, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['put_rho']), style=text_style), width=1, className="text-center", style=col_style),
                dbc.Col(html.Span(fmt(row['put_theo']), style=text_style), width=1, className="text-center", style=col_style),
                dbc.Col(html.Span(f"{row['put_iv']*100:.1f}", style=text_style), width=1, className="text-center", style=col_style),
            ], className="gx-0 border-bottom border-secondary py-0 align-items-center"))
        expiry_tabs.append(dbc.Tab(html.Div(rows, className="mt-1", style={"maxHeight": "500px", "overflowY": "auto"}), label=expiry_str, tab_id=f"tab-{expiry_str}"))
    return dbc.Tabs(expiry_tabs, id=f"expiry-tabs-{ticker}", persistence=True, persistence_type="local")

@app.callback(
    Output("pos-delta", "children"), Output("pos-gamma", "children"), Output("pos-theta", "children"),
    Input("positions-store-v2", "data"), Input("refresh", "data"), State("tabs", "active_tab"),
)
def display_position_greeks(positions, refresh, active_tab):
    if not positions: return "Position Total Greeks: Delta 0.0000", "Position Gamma: 0.0000", "Position Theta: 0.0000"
    td, tg, tt = 0, 0, 0
    t_pos = {}
    for k, q in positions.items():
        try:
            tk = k.split("|")[0]
            if tk not in t_pos: t_pos[tk] = []
            t_pos[tk].append((k, q))
        except: continue
    for tk, pl in t_pos.items():
        res = analyze_data(tk, "all")
        if not res or len(res) < 2: continue
        df, t_ddt = res[0], res[1]
        if df is None or df.empty: continue
        for k, q in pl:
            parts = k.split("|")
            if len(parts) < 4: continue
            es, s, ot = parts[1], float(parts[2]), parts[3]
            try:
                ed = pd.to_datetime(es).tz_localize(t_ddt.tzinfo) + timedelta(hours=15)
                if ed < t_ddt: continue
            except: continue
            m = df[(df["expiration_date"].dt.strftime("%Y-%m-%d") == es) & (df["strike_price"] == s)]
            if not m.empty:
                if ot == "C":
                    td += (m.iloc[0]["call_delta"] or 0) * q
                    tg += (m.iloc[0]["call_gamma"] or 0) * q
                    tt += (m.iloc[0]["call_theta"] or 0) * q
                else:
                    td += (m.iloc[0]["put_delta"] or 0) * q
                    tg += (m.iloc[0]["put_gamma"] or 0) * q
                    tt += (m.iloc[0]["put_theta"] or 0) * q
    return f"Position Total Greeks: Delta {td:.4f}", f"Gamma {tg:.4f}", f"Theta {tt:.4f}"

@app.callback(
    Output("live-chart", "figure"), Output("live-chart", "style"), Output("pagination-div", "hidden"),
    Input("live-dropdown", "value"), Input("tabs", "active_tab"), Input("exp-value", "data"), Input("pagination", "active_page"), Input("refresh", "data"), Input("switch", "value"),
)
def update_live_chart(value, stock, expiration, active_page, refresh, toggle_dark):
    logger.debug(f"update_live_chart called with value: {value}, stock: {stock}, expiration: {expiration}, active_page: {active_page}")
    res = cache_data(stock.lower(), expiration)
    (df, t_ddt, t_ddt_s, sp, fs, ts, levels, tdelta, tgama, tvanna, tcharm, zd, zg, civ, piv) = res
    xaxis, yaxis = dict(gridcolor="lightgray", minor=dict(ticklen=5, tickcolor="#000", showgrid=True)), dict(gridcolor="lightgray", minor=dict(tickcolor="#000"))
    layout = {"title_x": 0.5, "title_font_size": 12.5, "title_xref": "paper", "legend": dict(orientation="v", yanchor="top", xanchor="right", y=0.98, x=0.98, bgcolor="rgba(0,0,0,0.1)", font_size=10), "showlegend": True, "margin": dict(l=0, r=40), "xaxis": xaxis, "yaxis": yaxis, "dragmode": "pan"}
    if not toggle_dark: pio.templates["custom_template"] = pio.templates["seaborn"]
    else:
        pio.templates["custom_template"] = pio.templates["plotly_dark"]
        for axis in [xaxis, yaxis]: axis["gridcolor"], axis["minor"]["tickcolor"] = "#373737", "#707070"
        layout["paper_bgcolor"] = "#222222"; layout["plot_bgcolor"] = "rgba(40, 40, 50, 0.8)"
    pio.templates["custom_template"].update(layout=layout); pio.templates.default = "custom_template"
    if df is None or df.empty: return go.Figure(layout={"title_text": f"{stock} data unavailable, retry later"}), {}, True
    ret_c = cache.get("retry")
    if df["total_delta"].sum() == 0 and (not ret_c or stock not in ret_c):
        ret_c = ret_c or []; ret_c.append(stock); cache.set("retry", ret_c)
    dc = active_page == 2 and not "Profile" in value
    if not dc:
        df_agg = df.groupby(["strike_price"]).sum(numeric_only=True); df_agg = df_agg[fs:ts]
        civ, piv = civ["strike"], piv["strike"]
    else:
        df_agg = df.groupby(["expiration_date"]).sum(numeric_only=True)
        civ, piv = civ["exp"], piv["exp"]
    lt_map = {"this-month": "This Month", "next-month": "Next Month", "this-season": "This Season", "next-season": "Next Season"}
    lt = lt_map.get(expiration) if expiration != "all" else "All Expirations"
    strikes = df_agg.index.to_numpy()
    is_pv = "Profile" in value or "Average" in value
    name = value.split()[1] if "Absolute" in value else value.split()[0]
    n2v = {"Delta": (f"per 1% {stock} Move", f"{name} Exposure (price / 1% move)", zd), "Gamma": (f"per 1% {stock} Move", f"{name} Exposure (delta / 1% move)", zg), "Vanna": (f"per 1% {stock} IV Move", f"{name} Exposure (delta / 1% IV move)", 0), "Charm": (f"a day til {stock} Expiry", f"{name} Exposure (delta / day til expiry)", 0), "Implied": ("", "Implied Volatility (IV) Average", 0)}
    desc, yt, zf = n2v[name]; yaxis.update(title_text=yt); scale = 10**9
    if "Absolute" in value:
        fig = go.Figure(data=[go.Bar(name=name + " Exposure", x=strikes, y=df_agg[f"total_{name.lower()}"].to_numpy(), marker=dict(line=dict(width=0.25, color=("#2B5078" if not toggle_dark else "#8795FA"))))])
    elif "Calls/Puts" in value:
        fig = go.Figure(data=[go.Bar(name="Call " + name, x=strikes, y=df_agg[f"call_{name[:1].lower()}ex"].to_numpy() / scale, marker=dict(line=dict(width=0.25, color=("#2B5078" if not toggle_dark else "#8795FA")))), go.Bar(name="Put " + name, x=strikes, y=df_agg[f"put_{name[:1].lower()}ex"].to_numpy() / scale, marker=dict(line=dict(width=0.25, color=("#9B5C30" if not toggle_dark else "#F5765B"))))])
    if not is_pv:
        st = textwrap.wrap(f"Total {name}: $" + str("{:,.2f}".format(df[f"total_{name.lower()}"].sum() * scale)) + f" {desc}, {t_ddt_s}", width=50)
        fig.update_layout(title_text="<br>".join(st), legend_title_text=lt, xaxis=xaxis, yaxis=yaxis, barmode="relative", modebar_remove=["autoscale", "lasso2d"])
    if is_pv:
        fig = make_subplots(rows=1, cols=1)
        if not dc and name != "Implied":
            st = textwrap.wrap(f"{stock} {name} Exposure Profile, {t_ddt_s}", width=50)
            n2v_p = {"Delta": tdelta["all"], "Gamma": tgama["all"], "Vanna": tvanna["all"], "Charm": tcharm["all"]}
            ae = n2v_p[name]; fig.add_trace(go.Scatter(x=levels, y=ae, name="All Expiries"))
            if name == "Charm" or name == "Vanna":
                aemn, aemx = ae.min(), ae.max()
                if aemn < 0: fig.add_hrect(y0=0, y1=aemn * 1.5, fillcolor="red", opacity=0.1, line_width=0)
                if aemx > 0: fig.add_hrect(y0=0, y1=aemx * 1.5, fillcolor="green", opacity=0.1, line_width=0)
                fig.add_hline(y=0, line_width=0, name=name + " Flip", annotation_text=name + " Flip", annotation_position="top left")
            elif zf > 0:
                fig.add_vline(x=zf, line_color="dimgray", line_width=1, name=name + " Flip", annotation_text=name + " Flip: " + str("{:,.0f}".format(zf)), annotation_position="top left")
                fig.add_vrect(x0=fs, x1=zf, fillcolor="red", opacity=0.1, line_width=0); fig.add_vrect(x0=zf, x1=ts, fillcolor="green", opacity=0.1, line_width=0)
            elif ae[0] < 0: fig.add_vrect(x0=fs, x1=ts, fillcolor="red", opacity=0.1, line_width=0)
            elif ae[0] > 0: fig.add_vrect(x0=fs, x1=ts, fillcolor="green", opacity=0.1, line_width=0)
        elif name == "Implied":
            fig.add_trace(go.Scatter(x=strikes, y=piv * 100, name="Put IV", fill="tozeroy", line_color="#C44E52"))
            fig.add_trace(go.Scatter(x=strikes, y=civ * 100, name="Call IV", fill="tozeroy", line_color="#32A3A3"))
            st = textwrap.wrap(f"{stock} IV Average, {t_ddt_s}", width=50)
        fig.add_hline(y=0, line_width=1, line_color="dimgray")
        fig.update_layout(title_text="<br>".join(st), legend_title_text=lt, xaxis=xaxis, yaxis=yaxis, modebar_remove=["autoscale"])
    fig.update_xaxes(title="Strike" if not dc else "Date", showgrid=True, range=([sp * 0.9, sp * 1.1] if not dc else ([strikes[0] - timedelta(seconds=0.0625), strikes[0] + timedelta(seconds=0.0625)] if len(strikes) == 1 else [t_ddt, t_ddt + timedelta(days=31)])), gridwidth=1, rangeslider=dict(visible=True))
    fig.update_yaxes(showgrid=True, fixedrange=True, minor_ticks="inside", gridwidth=1)
    if not dc: fig.add_vline(x=sp, line_color="#707070", line_width=1, line_dash="dash", name=stock + " Spot", annotation_text="Last: " + str("{:,.2f}".format(sp)), annotation_position="top")
    isp_hidden = "Profile" in value
    return fig, {}, isp_hidden

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port="8050")
