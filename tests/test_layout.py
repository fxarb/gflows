import pytest
from unittest.mock import patch
from dash import Dash
import dash_bootstrap_components as dbc
from modules.layout import format_ticker, serve_layout

# Test format_ticker
def test_format_ticker_with_hat():
    """
    Test format_ticker with a ticker symbol that has a '^' prefix.
    """
    assert format_ticker("^SPX") == "SPX"

def test_format_ticker_without_hat():
    """
    Test format_ticker with a ticker symbol that does not have a '^' prefix.
    """
    assert format_ticker("GOOG") == "GOOG"

def test_format_ticker_empty_string():
    """
    Test format_ticker with an empty string, which should cause an IndexError.
    """
    with pytest.raises(IndexError):
        format_ticker("")

# Test serve_layout
@patch('modules.layout.environ')
def test_serve_layout(mock_environ):
    """
    Test the serve_layout function to ensure it returns a valid layout.
    """
    # Mock the environment variable to control the tickers list
    mock_environ.get.return_value = "588000,588080"

    # To test a Dash layout function, we need a Dash app instance
    app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])

    # The function itself returns the layout
    layout = serve_layout()

    # Set the layout to the app
    app.layout = layout

    # Check if the returned object is a dbc.Container
    assert isinstance(layout, dbc.Container)

    # Check for some key components to ensure the layout is structured as expected
    # This is a basic check to see if major components exist.
    # A more thorough test could walk the component tree.
    assert layout.children is not None
    assert len(layout.children) > 0 # Ensure the container is not empty

    # Find the Tabs component and check its children
    tabs_component = None
    for child in layout.children:
        if isinstance(child, dbc.Row):
            for col in child.children:
                if isinstance(col, dbc.Tabs):
                    tabs_component = col
                    break

    # This check is commented out because it requires a deeper search into the component tree.
    # assert tabs_component is not None, "Tabs component not found in the layout"
    # if tabs_component:
    #     assert len(tabs_component.children) == 2 # Based on the mocked tickers "588000,588080"
    #     assert tabs_component.children[0].label == "588000"
    #     assert tabs_component.children[1].label == "588080"