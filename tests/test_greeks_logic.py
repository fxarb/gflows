import pytest
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from modules.calc import calc_exposures

def test_greeks_additive_sign_and_scaling():
    # Setup dummy data for a straddle at 100 strike
    # Both call and put have same OI, same IV, same strike.
    tz = 'UTC'
    today_ddt = datetime(2024, 1, 1, 10, 0, 0, tzinfo=ZoneInfo(tz))

    # 0.1 year to expiry
    exp_date = today_ddt + pd.Timedelta(days=36.5)

    data = {
        'strike_price': [100.0],
        'expiration_date': [exp_date],
        'time_till_exp': [0.1],
        'call_iv': [0.2],
        'put_iv': [0.2],
        'call_open_int': [1000.0],
        'put_open_int': [1000.0],
        'call_delta': [0.5],
        'put_delta': [-0.5],
        'call_gamma': [0.05],
        'put_gamma': [0.05]
    }
    df = pd.DataFrame(data)

    spot_price = 100.0

    # Call calc_exposures
    # We need to mock get_risk_free_rate or ensure it returns a value
    from unittest.mock import patch
    with patch('modules.calc.get_risk_free_rate', return_value=0.0):
        (
            option_data, _, _, _, _, _, _,
            totaldelta, totalgamma, totalvanna, totalcharm,
            _, _, _, _
        ) = calc_exposures(df, 'TEST', 'all', spot_price, today_ddt, 'dummy_date')

    # Delta: Call (0.5 * 1000 * 100 * 0.01) + Put (-0.5 * 1000 * 100 * 0.01) = 500 - 500 = 0
    # Scaled by 10^9 in option_data
    assert option_data['total_delta'].sum() == pytest.approx(0.0)

    # Gamma: Call (0.05 * 1000 * 100 * 100 * 0.01) + Put (0.05 * 1000 * 100 * 100 * 0.01)
    # = 5000 + 5000 = 10000
    # Scaled by 10^9
    expected_gamma = (10000.0) / 1e9
    assert option_data['total_gamma'].sum() == pytest.approx(expected_gamma)

    # Total Greeks in dataframe should also be additive
    # Check total_vanna and total_charm
    # Since Call and Put have same params, their Vanna/Charm (from BS) should be same
    # total_vanna = call_vex + put_vex (both should be positive or negative but same sign)
    assert option_data['total_vanna'].iloc[0] == pytest.approx(
        (option_data['call_vex'].iloc[0] + option_data['put_vex'].iloc[0]) / 1e9
    )
    assert option_data['total_charm'].iloc[0] == pytest.approx(
        (option_data['call_cex'].iloc[0] + option_data['put_cex'].iloc[0]) / 1e9
    )

def test_scaling_values():
    # Verify exact values if possible
    # For S=100, K=100, vol=0.2, T=0.1, r=0, q=0
    # d1 = (0 + (0.5*0.04)*0.1) / (0.2 * sqrt(0.1)) = 0.002 / 0.063245 = 0.031622
    # pdf(d1) = 1/sqrt(2pi) * exp(-0.5 * d1^2) = 0.398942 * 0.9995 = 0.39874
    # Gamma = pdf(d1) / (S * vol * sqrt(T)) = 0.39874 / (100 * 0.2 * 0.31622) = 0.39874 / 6.3245 = 0.063047

    # Exposure = Gamma * OI * S^2 * 0.01
    # For OI=1000: 0.063047 * 1000 * 10000 * 0.01 = 6304.7

    tz = 'UTC'
    today_ddt = datetime(2024, 1, 1, 10, 0, 0, tzinfo=ZoneInfo(tz))
    exp_date = today_ddt + pd.Timedelta(days=36.5)

    data = {
        'strike_price': [100.0],
        'expiration_date': [exp_date],
        'time_till_exp': [0.1],
        'call_iv': [0.2],
        'put_iv': [0.2],
        'call_open_int': [1000.0],
        'put_open_int': [0.0], # Only Call
        'call_delta': [0.5126], # Approximate
        'put_delta': [-0.4874],
        'call_gamma': [0.063047],
        'put_gamma': [0.063047]
    }
    df = pd.DataFrame(data)

    spot_price = 100.0
    from unittest.mock import patch
    with patch('modules.calc.get_risk_free_rate', return_value=0.0):
        (
            option_data, _, _, _, _, _, _,
            _, totalgamma, _, _,
            _, _, _, _
        ) = calc_exposures(df, 'TEST', 'all', spot_price, today_ddt, 'dummy_date')

    expected_gex_notional = 6304.7
    assert option_data['call_gex'].iloc[0] == pytest.approx(expected_gex_notional, rel=1e-3)
