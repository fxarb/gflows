import pandas as pd
import exchange_calendars as xcals
import numpy as np
import orjson
import requests
import modules.stats as stats
from os import environ
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateparser.date import DateDataParser
from warnings import simplefilter
from calendar import monthrange
from cachetools import cached, TTLCache
from pathlib import Path
from os import getcwd
from re import compile
from .logging_config import setup_logging

logger = setup_logging()

# Ignore warning for NaN values in dataframe
simplefilter(action="ignore", category=RuntimeWarning)

pd.options.display.float_format = "{:,.4f}".format

# Precompile regex patterns for performance
_strike_regex = compile(r"[CP](\d+)")  # Regex for extracting strike price.
_exp_date_regex = compile(r"(\d{6})([CP])")  # Regex for extracting expiration date and type.


# fetch risk-free rate from a URL
@cached(cache=TTLCache(maxsize=1, ttl=60))  # in-memory cache for 1 min
def get_risk_free_rate():
    """
    Fetches the risk-free rate from a URL specified in the RISK_FREE_RATE_URL environment variable.

    :return: The risk-free rate.
    """
    url = environ.get("RISK_FREE_RATE_URL")
    if not url:
        logger.warning("RISK_FREE_RATE_URL not set, using default value of 0.02")
        return 0.02

    try:
        logger.debug(f"Fetching risk-free rate from {url}")
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        risk_free_rate = float(data.get("RiskFreeRate", 0.02))
        logger.info(f"Successfully fetched risk-free rate: {risk_free_rate}")
        return risk_free_rate
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.error(f"Failed to fetch risk-free rate from {url}: {e}")
        logger.warning("Using default value of 0.02")
        return 0.02


def is_parsable(date):
    """
    Checks if a string can be parsed as a date.

    :param date: The string to check.
    :return: True if the string can be parsed as a date, False otherwise.
    """
    logger.debug(f"is_parsable called with date: {date}")
    try:
        datetime.strptime(date.split()[-2], "%H:%M")
        logger.debug("Date is parsable.")
        return True
    except ValueError:
        logger.debug("Date is not parsable.")
        return False


def format_data(data, today_ddt, tzinfo):
    """
    Formats the options data.

    :param data: The options data.
    :param today_ddt: The current date and time.
    :param tzinfo: The timezone info.
    :return: The formatted options data.
    """
    logger.debug("Formatting data...")
    keys_to_keep = ["option", "iv", "open_interest", "delta", "gamma", "theta"]
    data = pd.DataFrame([{k: d[k] for k in keys_to_keep if k in d} for d in data])

    # Extract strike, expiration date, and option type
    data["strike_price"] = (
        data["option"].str.extract(_strike_regex)[0].astype(float) / 1000.0
    )
    exp_data = data["option"].str.extract(_exp_date_regex)
    data["expiration_date"] = (
        pd.to_datetime(exp_data[0], format="%y%m%d").dt.tz_localize(tzinfo)
        + timedelta(hours=15)
    )
    data["option_type"] = exp_data[1]

    # Separate calls and puts
    calls = data[data["option_type"] == "C"].copy()
    puts = data[data["option_type"] == "P"].copy()

    # Rename columns for merging
    calls = calls.rename(
        columns={
            "option": "calls",
            "iv": "call_iv",
            "open_interest": "call_open_int",
            "delta": "call_delta",
            "gamma": "call_gamma",
            "theta": "call_theta",
        }
    )
    puts = puts.rename(
        columns={
            "option": "puts",
            "iv": "put_iv",
            "open_interest": "put_open_int",
            "delta": "put_delta",
            "gamma": "put_gamma",
            "theta": "put_theta",
        }
    )

    # Merge calls and puts on strike and expiration to create a single row per strike/expiry.
    data = pd.merge(
        calls[
            [
                "expiration_date",
                "strike_price",
                "calls",
                "call_iv",
                "call_open_int",
                "call_delta",
                "call_gamma",
                "call_theta",
            ]
        ],
        puts[
            [
                "expiration_date",
                "strike_price",
                "puts",
                "put_iv",
                "put_open_int",
                "put_delta",
                "put_gamma",
                "put_theta",
            ]
        ],
        on=["expiration_date", "strike_price"],
        how="outer",
    )

    # time to expiration in years
    data["time_till_exp"] = (data["expiration_date"] - today_ddt).dt.total_seconds() / (
        365 * 24 * 60 * 60
    )

    data = data.sort_values(by=["expiration_date", "strike_price"]).reset_index(
        drop=True
    )

    logger.debug("Data formatting complete.")
    return data


def calc_exposures(
    option_data,
    ticker,
    expir,
    spot_price,
    today_ddt,
    today_ddt_string,
):
    """
    Calculates the exposures for the given options data.

    :param option_data: The options data.
    :param ticker: The ticker symbol.
    :param expir: The expiration date.
    :param spot_price: The spot price.
    :param today_ddt: The current date and time.
    :param today_ddt_string: The current date and time as a string.
    :return: A tuple containing the calculated exposures.
    """
    logger.debug(f"Calculating exposures for ticker: {ticker}, expiration: {expir}")
    if option_data.empty:
        logger.warning("Option data is empty, returning empty result.")
        # Return a tuple of Nones with the expected length (15)
        return (pd.DataFrame(), None, None, None, None, None, None, {}, {}, {}, {}, 0, 0, {}, {})
    dividend_yield = 0.0  # assume 0
    risk_free_rate = get_risk_free_rate()

    strike_prices = option_data["strike_price"].to_numpy()
    expirations = option_data["expiration_date"].to_numpy()
    time_till_exp = option_data["time_till_exp"].to_numpy()
    opt_call_ivs = option_data["call_iv"].to_numpy()
    opt_put_ivs = option_data["put_iv"].to_numpy()
    call_open_interest = option_data["call_open_int"].to_numpy().astype(np.float64)
    put_open_interest = option_data["put_open_int"].to_numpy().astype(np.float64)

    nonzero_call_cond = (time_till_exp > 0) & (opt_call_ivs > 0)
    nonzero_put_cond = (time_till_exp > 0) & (opt_put_ivs > 0)
    np_spot_price = np.array([[spot_price]])

    call_dp, call_cdf_dp, call_pdf_dp = stats.calc_dp_cdf_pdf(
        np_spot_price,
        strike_prices,
        opt_call_ivs,
        time_till_exp,
        risk_free_rate,
        dividend_yield,
    )
    put_dp, put_cdf_dp, put_pdf_dp = stats.calc_dp_cdf_pdf(
        np_spot_price,
        strike_prices,
        opt_put_ivs,
        time_till_exp,
        risk_free_rate,
        dividend_yield,
    )

    from_strike = 0.5 * spot_price
    to_strike = 1.5 * spot_price

    # ---=== CALCULATE EXPOSURES ===---
    option_data["call_dex"] = (
        option_data["call_delta"].to_numpy() * call_open_interest * spot_price
    )
    option_data["put_dex"] = (
        option_data["put_delta"].to_numpy() * put_open_interest * spot_price
    )
    option_data["call_gex"] = (
        option_data["call_gamma"].to_numpy()
        * call_open_interest
        * spot_price
        * spot_price
    )
    option_data["put_gex"] = (
        option_data["put_gamma"].to_numpy()
        * put_open_interest
        * spot_price
        * spot_price
        * -1
    )
    logger.debug(f"np_spot_price: {np_spot_price}, opt_put_ivs: {opt_put_ivs}, time_till_exp: {time_till_exp}, dividend_yield: {dividend_yield}, put_open_interest: {put_open_interest}, put_dp: {put_dp}, put_pdf_dp: {put_pdf_dp}")
    option_data["call_vex"] = np.where(
        nonzero_call_cond,
        stats.calc_vanna_ex(
            np_spot_price,
            opt_call_ivs,
            time_till_exp,
            dividend_yield,
            call_open_interest,
            call_dp,
            call_pdf_dp,
        )[0],
        0,
    )
    option_data["put_vex"] = np.where(
        nonzero_put_cond,
        stats.calc_vanna_ex(
            np_spot_price,
            opt_put_ivs,
            time_till_exp,
            dividend_yield,
            put_open_interest,
            put_dp,
            put_pdf_dp,
        )[0],
        0,
    )
    option_data["call_cex"] = np.where(
        nonzero_call_cond,
        stats.calc_charm_ex(
            np_spot_price,
            opt_call_ivs,
            time_till_exp,
            risk_free_rate,
            dividend_yield,
            "call",
            call_open_interest,
            call_dp,
            call_cdf_dp,
            call_pdf_dp,
        )[0],
        0,
    )
    option_data["put_cex"] = np.where(
        nonzero_put_cond,
        stats.calc_charm_ex(
            np_spot_price,
            opt_put_ivs,
            time_till_exp,
            risk_free_rate,
            dividend_yield,
            "put",
            put_open_interest,
            put_dp,
            put_cdf_dp,
            put_pdf_dp,
        )[0],
        0,
    )
    # Calculate total Greek exposures by summing Call and Put components and scaling to billions.
    option_data["total_delta"] = (
        option_data["call_dex"].to_numpy() + option_data["put_dex"].to_numpy()
    ) / 10**9
    option_data["total_gamma"] = (
        option_data["call_gex"].to_numpy() + option_data["put_gex"].to_numpy()
    ) / 10**9
    option_data["total_vanna"] = (
        option_data["call_vex"].to_numpy() - option_data["put_vex"].to_numpy()
    ) / 10**9
    option_data["total_charm"] = (
        option_data["call_cex"].to_numpy() - option_data["put_cex"].to_numpy()
    ) / 10**9

    # group all options by strike / expiration then average their IVs
    df_agg_strike_mean = (
        option_data[["strike_price", "call_iv", "put_iv"]]
        .groupby(["strike_price"])
        .mean(numeric_only=True)
    )
    df_agg_exp_mean = (
        option_data[["expiration_date", "call_iv", "put_iv"]]
        .groupby(["expiration_date"])
        .mean(numeric_only=True)
    )
    # filter strikes / expirations for relevance
    df_agg_strike_mean = df_agg_strike_mean[from_strike:to_strike]
    # df_agg_exp_mean = df_agg_exp_mean[: today_ddt + timedelta(weeks=52)]

    call_ivs = {
        "strike": df_agg_strike_mean["call_iv"].to_numpy(),
        "exp": df_agg_exp_mean["call_iv"].to_numpy(),
    }
    put_ivs = {
        "strike": df_agg_strike_mean["put_iv"].to_numpy(),
        "exp": df_agg_exp_mean["put_iv"].to_numpy(),
    }

    # ---=== CALCULATE EXPOSURE PROFILES ===---
    levels = np.linspace(from_strike, to_strike, 300).reshape(-1, 1)

    totaldelta = {
        "all": np.array([]),
    }
    totalgamma = {
        "all": np.array([]),
    }
    totalvanna = {
        "all": np.array([]),
    }
    totalcharm = {
        "all": np.array([]),
    }

    # For each spot level, calculate greek exposure at that point
    call_dp, call_cdf_dp, call_pdf_dp = stats.calc_dp_cdf_pdf(
        levels,
        strike_prices,
        opt_call_ivs,
        time_till_exp,
        risk_free_rate,
        dividend_yield,
    )
    put_dp, put_cdf_dp, put_pdf_dp = stats.calc_dp_cdf_pdf(
        levels,
        strike_prices,
        opt_put_ivs,
        time_till_exp,
        risk_free_rate,
        dividend_yield,
    )
    call_delta_ex = np.where(
        nonzero_call_cond,
        stats.calc_delta_ex(
            levels,
            time_till_exp,
            dividend_yield,
            "call",
            call_open_interest,
            call_cdf_dp,
        ),
        0,
    )
    put_delta_ex = np.where(
        nonzero_put_cond,
        stats.calc_delta_ex(
            levels,
            time_till_exp,
            dividend_yield,
            "put",
            put_open_interest,
            put_cdf_dp,
        ),
        0,
    )
    call_gamma_ex = np.where(
        nonzero_call_cond,
        stats.calc_gamma_ex(
            levels,
            opt_call_ivs,
            time_till_exp,
            dividend_yield,
            call_open_interest,
            call_pdf_dp,
        ),
        0,
    )
    put_gamma_ex = np.where(
        nonzero_put_cond,
        stats.calc_gamma_ex(
            levels,
            opt_put_ivs,
            time_till_exp,
            dividend_yield,
            put_open_interest,
            put_pdf_dp,
        ),
        0,
    )
    call_vanna_ex = np.where(
        nonzero_call_cond,
        stats.calc_vanna_ex(
            levels,
            opt_call_ivs,
            time_till_exp,
            dividend_yield,
            call_open_interest,
            call_dp,
            call_pdf_dp,
        ),
        0,
    )
    put_vanna_ex = np.where(
        nonzero_put_cond,
        stats.calc_vanna_ex(
            levels,
            opt_put_ivs,
            time_till_exp,
            dividend_yield,
            put_open_interest,
            put_dp,
            put_pdf_dp,
        ),
        0,
    )
    call_charm_ex = np.where(
        nonzero_call_cond,
        stats.calc_charm_ex(
            levels,
            opt_call_ivs,
            time_till_exp,
            risk_free_rate,
            dividend_yield,
            "call",
            call_open_interest,
            call_dp,
            call_cdf_dp,
            call_pdf_dp,
        ),
        0,
    )
    put_charm_ex = np.where(
        nonzero_put_cond,
        stats.calc_charm_ex(
            levels,
            opt_put_ivs,
            time_till_exp,
            risk_free_rate,
            dividend_yield,
            "put",
            put_open_interest,
            put_dp,
            put_cdf_dp,
            put_pdf_dp,
        ),
        0,
    )

    # delta exposure
    totaldelta["all"] = (call_delta_ex.sum(axis=1) + put_delta_ex.sum(axis=1)) / 10**9
    # gamma exposure
    totalgamma["all"] = (call_gamma_ex.sum(axis=1) - put_gamma_ex.sum(axis=1)) / 10**9
    # vanna exposure
    totalvanna["all"] = (call_vanna_ex.sum(axis=1) - put_vanna_ex.sum(axis=1)) / 10**9
    # charm exposure
    totalcharm["all"] = (call_charm_ex.sum(axis=1) - put_charm_ex.sum(axis=1)) / 10**9

    # Find Delta Flip Point
    zero_cross_idx = np.where(np.diff(np.sign(totaldelta["all"])))[0]
    neg_delta = totaldelta["all"][zero_cross_idx]
    pos_delta = totaldelta["all"][zero_cross_idx + 1]
    neg_strike = levels[zero_cross_idx]
    pos_strike = levels[zero_cross_idx + 1]
    zerodelta = pos_strike - (
        (pos_strike - neg_strike) * pos_delta / (pos_delta - neg_delta)
    )
    # Find Gamma Flip Point
    zero_cross_idx = np.where(np.diff(np.sign(totalgamma["all"])))[0]
    negGamma = totalgamma["all"][zero_cross_idx]
    posGamma = totalgamma["all"][zero_cross_idx + 1]
    neg_strike = levels[zero_cross_idx]
    pos_strike = levels[zero_cross_idx + 1]
    zerogamma = pos_strike - (
        (pos_strike - neg_strike) * posGamma / (posGamma - negGamma)
    )

    if zerodelta.size > 0:
        zerodelta = zerodelta[0][0]
    else:
        zerodelta = 0
        logger.warning(f"Delta flip not found for {ticker} {expir}")
    if zerogamma.size > 0:
        zerogamma = zerogamma[0][0]
    else:
        zerogamma = 0
        logger.warning(f"Gamma flip not found for {ticker} {expir}")

    return (
        option_data,
        today_ddt,
        today_ddt_string,
        spot_price,
        from_strike,
        to_strike,
        levels.ravel(),
        totaldelta,
        totalgamma,
        totalvanna,
        totalcharm,
        zerodelta,
        zerogamma,
        call_ivs,
        put_ivs,
    )


def get_options_data_json(ticker, expir, tz):
    """
    Gets the options data from a JSON file.

    :param ticker: The ticker symbol.
    :param expir: The expiration date.
    :param tz: The timezone.
    :return: The options data.
    """
    logger.debug(f"Getting options data from JSON for ticker: {ticker}, expiration: {expir}")
    try:
        # CBOE file format, json
        with open(
            Path(f"{getcwd()}/data/json/{ticker}_quotedata.json"), encoding="utf-8"
        ) as json_file:
            json_data = json_file.read()
        data = pd.json_normalize(orjson.loads(json_data))
    except orjson.JSONDecodeError as e:  # handle error if data unavailable
        logger.error(f"{e}, {ticker} {expir} data is unavailable")
        return

    # Get Spot
    spot_price = data["data.current_price"][0].astype(float)

    # Get Today's Date
    today_date = DateDataParser(
        settings={
            "TIMEZONE": tz,
            "TO_TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
        }
    ).get_date_data(str(data["timestamp"][0]))
    # Handle date formats
    today_ddt = today_date.date_obj
    today_ddt_string = today_ddt.strftime("%Y %b %d, %I:%M %p %Z")

    option_data = format_data(
        data["data.options"][0],
        today_ddt,
        today_date.date_obj.tzinfo,
    )

    all_dates = option_data["expiration_date"].drop_duplicates()
    first_expiry = all_dates.iat[0]
    if today_ddt > first_expiry:
        # first date expired so, if available, use next date as 0DTE
        try:
            option_data = option_data[option_data["expiration_date"] != first_expiry]
            first_expiry = all_dates.iat[1]
        except IndexError:
            logger.warning("Next date unavailable. Using expired date.")

    tzinfo = today_date.date_obj.tzinfo
    year = today_ddt.year
    month = today_ddt.month

    # This month
    _, last_day_this_month = monthrange(year, month)
    end_of_this_month = datetime(year, month, last_day_this_month, 23, 59, 59, tzinfo=tzinfo)

    # Next month
    next_month_date = today_ddt + pd.DateOffset(months=1)
    _, last_day_next_month = monthrange(next_month_date.year, next_month_date.month)
    end_of_next_month = datetime(next_month_date.year, next_month_date.month, last_day_next_month, 23, 59, 59, tzinfo=tzinfo)

    # This season
    current_quarter = (month - 1) // 3
    end_month_of_this_season = (current_quarter * 3) + 3
    _, last_day_this_season = monthrange(year, end_month_of_this_season)
    end_of_this_season = datetime(year, end_month_of_this_season, last_day_this_season, 23, 59, 59, tzinfo=tzinfo)

    # Next season
    date_in_next_season = end_of_this_season + timedelta(days=1)
    next_season_year = date_in_next_season.year
    next_season_month = date_in_next_season.month
    next_quarter = (next_season_month - 1) // 3
    end_month_of_next_season = (next_quarter * 3) + 3
    _, last_day_next_season = monthrange(next_season_year, end_month_of_next_season)
    end_of_next_season = datetime(next_season_year, end_month_of_next_season, last_day_next_season, 23, 59, 59, tzinfo=tzinfo)

    if expir == "this-month":
        option_data = option_data[option_data["expiration_date"] <= end_of_this_month]
    elif expir == "next-month":
        option_data = option_data[option_data["expiration_date"] <= end_of_next_month]
    elif expir == "this-season":
        option_data = option_data[option_data["expiration_date"] <= end_of_this_season]
    elif expir == "next-season":
        option_data = option_data[option_data["expiration_date"] <= end_of_next_season]
    # "all" expirations require no filtering

    return calc_exposures(
        option_data,
        ticker,
        expir,
        spot_price,
        today_ddt,
        today_ddt_string,
    )


def get_options_data_csv(ticker, expir, tz):
    """
    Gets the options data from a CSV file.

    :param ticker: The ticker symbol.
    :param expir: The expiration date.
    :param tz: The timezone.
    :return: The options data.
    """
    logger.debug(f"Getting options data from CSV for ticker: {ticker}, expiration: {expir}")
    try:
        # CBOE file format, csv
        with open(
            Path(f"{getcwd()}/data/csv/{ticker}_quotedata.csv"), encoding="utf-8"
        ) as csv_file:
            next(csv_file)  # skip first line
            spot_line = csv_file.readline()
            date_line = csv_file.readline()
            # Option data starts at line 4
            option_data = pd.read_csv(
                csv_file,
                header=0,
                names=[
                    "expiration_date",
                    "calls",
                    "call_last_sale",
                    "call_net",
                    "call_bid",
                    "call_ask",
                    "call_vol",
                    "call_iv",
                    "call_delta",
                    "call_gamma",
                    "call_open_int",
                    "strike_price",
                    "puts",
                    "put_last_sale",
                    "put_net",
                    "put_bid",
                    "put_ask",
                    "put_vol",
                    "put_iv",
                    "put_delta",
                    "put_gamma",
                    "put_open_int",
                ],
                usecols=lambda x: x
                not in [
                    "call_last_sale",
                    "call_net",
                    "call_bid",
                    "call_ask",
                    "call_vol",
                    "put_last_sale",
                    "put_net",
                    "put_bid",
                    "put_ask",
                    "put_vol",
                ],
            )
    except:  # handle error if data unavailable
        print(ticker, expir, "data is unavailable")
        return

    # Get Spot
    spot_price = float(spot_line.split("Last:")[1].split(",")[0])

    # Get Today's Date
    today_date = date_line.split("Date: ")[1].split(",Bid")[0]
    # Handle date formats
    if is_parsable(today_date):
        pass
    else:
        tmp = today_date.split()
        tmp[-1], tmp[-2] = tmp[-2], tmp[-1]
        today_date = " ".join(tmp)
    today_date = DateDataParser(
        settings={
            "TIMEZONE": tz,
            "TO_TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
        }
    ).get_date_data(today_date)
    today_ddt = today_date.date_obj
    today_ddt_string = today_ddt.strftime("%Y %b %d, %I:%M %p %Z")

    option_data["expiration_date"] = pd.to_datetime(
        option_data["expiration_date"], format="%a %b %d %Y"
    ).dt.tz_localize(today_date.date_obj.tzinfo) + timedelta(hours=15)
    option_data["strike_price"] = option_data["strike_price"].astype(float)
    option_data["call_iv"] = option_data["call_iv"].astype(float)
    option_data["put_iv"] = option_data["put_iv"].astype(float)
    option_data["call_delta"] = option_data["call_delta"].astype(float)
    option_data["put_delta"] = option_data["put_delta"].astype(float)
    option_data["call_gamma"] = option_data["call_gamma"].astype(float)
    option_data["put_gamma"] = option_data["put_gamma"].astype(float)
    option_data["call_open_int"] = option_data["call_open_int"].astype(float)
    option_data["put_open_int"] = option_data["put_open_int"].astype(float)

    all_dates = option_data["expiration_date"].drop_duplicates()
    first_expiry = all_dates.iat[0]
    if today_ddt > first_expiry:
        # first date expired so, if available, use next date as 0DTE
        try:
            option_data = option_data[option_data["expiration_date"] != first_expiry]
            first_expiry = all_dates.iat[1]
        except IndexError:
            print("next date unavailable. using expired date")
    # time to expiration in years
    option_data["time_till_exp"] = (
        option_data["expiration_date"] - today_ddt
    ).dt.total_seconds() / (365 * 24 * 60 * 60)

    tzinfo = today_date.date_obj.tzinfo
    year = today_ddt.year
    month = today_ddt.month

    # This month
    _, last_day_this_month = monthrange(year, month)
    end_of_this_month = datetime(year, month, last_day_this_month, 23, 59, 59, tzinfo=tzinfo)

    # Next month
    next_month_date = today_ddt + pd.DateOffset(months=1)
    _, last_day_next_month = monthrange(next_month_date.year, next_month_date.month)
    end_of_next_month = datetime(next_month_date.year, next_month_date.month, last_day_next_month, 23, 59, 59, tzinfo=tzinfo)

    # This season
    current_quarter = (month - 1) // 3
    end_month_of_this_season = (current_quarter * 3) + 3
    _, last_day_this_season = monthrange(year, end_month_of_this_season)
    end_of_this_season = datetime(year, end_month_of_this_season, last_day_this_season, 23, 59, 59, tzinfo=tzinfo)

    # Next season
    date_in_next_season = end_of_this_season + timedelta(days=1)
    next_season_year = date_in_next_season.year
    next_season_month = date_in_next_season.month
    next_quarter = (next_season_month - 1) // 3
    end_month_of_next_season = (next_quarter * 3) + 3
    _, last_day_next_season = monthrange(next_season_year, end_month_of_next_season)
    end_of_next_season = datetime(next_season_year, end_month_of_next_season, last_day_next_season, 23, 59, 59, tzinfo=tzinfo)

    if expir == "this-month":
        option_data = option_data[option_data["expiration_date"] <= end_of_this_month]
    elif expir == "next-month":
        option_data = option_data[option_data["expiration_date"] <= end_of_next_month]
    elif expir == "this-season":
        option_data = option_data[option_data["expiration_date"] <= end_of_this_season]
    elif expir == "next-season":
        option_data = option_data[option_data["expiration_date"] <= end_of_next_season]
    # "all" expirations require no filtering

    return calc_exposures(
        option_data,
        ticker,
        expir,
        spot_price,
        today_ddt,
        today_ddt_string,
    )


def get_options_data(ticker, expir, is_json, tz):
    """
    Gets the options data for a given ticker, expiration, and format.

    :param ticker: The ticker symbol.
    :param expir: The expiration date.
    :param is_json: Whether the data is in JSON format.
    :param tz: The timezone.
    :return: The options data.
    """
    return (
        get_options_data_json(ticker, expir, tz)
        if is_json
        else get_options_data_csv(ticker, expir, tz)
    )
