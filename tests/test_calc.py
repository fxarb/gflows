import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from modules.calc import get_risk_free_rate, format_data

# Test get_risk_free_rate
@patch('modules.calc.environ')
@patch('modules.calc.requests.get')
def test_get_risk_free_rate_success(mock_get, mock_environ):
    """
    Test get_risk_free_rate when the request is successful.
    """
    get_risk_free_rate.cache.clear()
    mock_environ.get.return_value = 'http://test-url.com'
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'RiskFreeRate': '0.03'}
    mock_get.return_value = mock_response

    rate = get_risk_free_rate()
    assert rate == 0.03

@patch('modules.calc.environ')
def test_get_risk_free_rate_no_url(mock_environ):
    """
    Test get_risk_free_rate when the environment variable is not set.
    """
    get_risk_free_rate.cache.clear()
    mock_environ.get.return_value = None
    rate = get_risk_free_rate()
    assert rate == 0.02

@patch('modules.calc.environ')
@patch('modules.calc.requests.get')
def test_get_risk_free_rate_request_fails(mock_get, mock_environ):
    """
    Test get_risk_free_rate when the request fails.
    """
    get_risk_free_rate.cache.clear()
    mock_environ.get.return_value = 'http://test-url.com'
    mock_get.side_effect = requests.exceptions.RequestException('Request failed')
    rate = get_risk_free_rate()
    assert rate == 0.02

# Test format_data
@pytest.fixture
def sample_options_data():
    """
    Provides a sample of raw options data as a fixture.
    """
    return [
        {'option': 'SPXW241011C04450000', 'iv': 0.1, 'open_interest': 10, 'delta': 0.5, 'gamma': 0.02},
        {'option': 'SPXW241011P04450000', 'iv': 0.12, 'open_interest': 12, 'delta': -0.4, 'gamma': 0.021},
    ]

def test_format_data(sample_options_data):
    """
    Test the format_data function to ensure it correctly processes raw options data.
    """
    tz = 'Asia/Shanghai'
    today_ddt = datetime(2024, 10, 9, 10, 0, 0, tzinfo=ZoneInfo(tz))

    formatted_df = format_data(sample_options_data, today_ddt, ZoneInfo(tz))

    # Check that the dataframe has the correct columns
    expected_columns = [
        'expiration_date', 'strike_price', 'calls', 'call_iv', 'call_open_int',
        'call_delta', 'call_gamma', 'puts', 'put_iv', 'put_open_int',
        'put_delta', 'put_gamma', 'time_till_exp'
    ]
    assert all(col in formatted_df.columns for col in expected_columns)

    # Check that there is one row of data
    assert len(formatted_df) == 1

    # Check data types and values
    row = formatted_df.iloc[0]
    assert row['strike_price'] == 4450.0
    assert row['call_iv'] == 0.1
    assert row['put_delta'] == -0.4
    assert isinstance(row['expiration_date'], pd.Timestamp)

    # Check time_till_exp calculation
    expected_expiration = datetime(2024, 10, 11, 15, 0, 0, tzinfo=ZoneInfo(tz))
    expected_tte = (expected_expiration - today_ddt).total_seconds() / (365 * 24 * 60 * 60)
    assert abs(row['time_till_exp'] - expected_tte) < 1e-9 # Compare with a small tolerance