import pytest
from unittest.mock import patch
from app import on_click_expirations

# Test the on_click callback for expiration selection
@patch('app.ctx')
def test_on_click_expiration_all_button(mock_ctx):
    """
    Test the expiration on_click callback when the 'all-btn' is clicked.
    """
    mock_ctx.triggered_id = "all-btn"

    # Inputs: value, btn, expiration
    result = on_click_expirations(None, 1, "next-month")

    # Expected output: "all", True, None
    assert result == ("all", True, None)

@patch('app.ctx')
def test_on_click_expiration_dropdown_selection(mock_ctx):
    """
    Test the expiration on_click callback when a value is selected from the dropdown.
    """
    mock_ctx.triggered_id = "exp-dropdown"

    # Inputs: value, btn, expiration
    result = on_click_expirations("this-month-btn", 0, "all")

    # Expected output for "this-month-btn"
    assert result == ("this-month", False, "this-month-btn")

@patch('app.ctx')
def test_on_click_expiration_no_trigger_with_state(mock_ctx):
    """
    Test the expiration on_click callback when there's no trigger but there's existing state.
    """
    mock_ctx.triggered_id = None

    # Simulate a page load where the user previously selected "next-month"
    result = on_click_expirations(None, 0, "next-month")

    # The callback should re-apply the state for "next-month-btn"
    assert result == ("next-month", False, "next-month-btn")

@patch('app.ctx')
def test_on_click_expiration_default_case(mock_ctx):
    """
    Test the expiration on_click callback with an unknown value, which should trigger the default.
    """
    mock_ctx.triggered_id = "exp-dropdown"

    # Inputs: value, btn, expiration
    result = on_click_expirations("unknown-btn", 0, "all")

    # The default behavior is to return "next-month"
    assert result == ("next-month", False, "next-month-btn")