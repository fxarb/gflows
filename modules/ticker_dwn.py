import requests
import base64
import orjson
from multiprocessing.dummy import Pool as ThreadPool
from datetime import datetime
from os import environ, getcwd
from pathlib import Path
from functools import partial
from .logging_config import setup_logging

logger = setup_logging()

def fulfill_req(ticker, is_json, session):
    """
    Fulfills a request to download options data for a given ticker.

    :param ticker: The ticker symbol of the stock.
    :param is_json: A boolean indicating whether to download the data in JSON format.
    :param session: A requests session object.
    """
    # The URL of the API to download the data from.
    api_url = environ.get("API_URL", 'https://cdn.cboe.com/api/global/delayed_quotes/options/{0}.json').format(ticker.upper()).strip()
    logger.debug(f"API URL for {ticker}: {api_url}")
    ticker = ticker.lower() if ticker[0] != "_" else ticker[1:].lower()
    # The format of the data to download.
    d_format = "json" if is_json else "csv"
    # The name of the file to save the data to.
    filename = (
        Path(f"{getcwd()}/data/json/{ticker}_quotedata.json")
        if is_json
        else Path(f"{getcwd()}/data/csv/{ticker}_quotedata.csv")
    )
    with open(filename, "wb") as f, session.get(api_url) as r:
        for i in range(3):  # in case of unavailable data, retry twice
            try:  # check if data is available
                r.raise_for_status()
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTPError for {ticker}: {e}")
                f.write("Unavailable".encode("utf-8"))
                if r.status_code == 504:  # check if timeout occurred
                    logger.warning(f"Gateway timeout for {ticker}, retry {i+1}/3")
                    continue
                elif r.status_code == 500:  # internal server error
                    logger.warning(f"Internal server error for {ticker}, retry {i+1}/3")
                    continue
            else:
                if is_json:
                    # incoming json data
                    f.write(orjson.dumps(r.json()))
                else:
                    # incoming csv data
                    for line in r.iter_lines():
                        if len(line) % 4:
                            # add padding:
                            line += b"==="
                        f.write(base64.b64decode(line) + "\n".encode("utf-8"))
                logger.info(f"Request done for {ticker} in {d_format} format")
                break


def dwn_data(select, is_json):
    """
    Downloads options data for a given list of tickers.

    :param select: A list of tickers to download data for.
    :param is_json: A boolean indicating whether to download the data in JSON format.
    """
    # A thread pool to download the data in parallel.
    pool = ThreadPool()
    logger.info(f"Download start: {datetime.now()}")
    # A list of tickers to download data for.
    tickers_format = (environ.get("TICKERS") or "159901,159915,159919,159922,510050,510300,510500,588000,588080").strip().split(",")
    if select:  # select tickers to download
        tickers_format = select
    logger.debug(f"Tickers to download: {tickers_format}")
    # A requests session object.
    session = requests.Session()
    session.headers.update({"Accept": "application/json" if is_json else "text/csv"})
    # A partial function to fulfill the request.
    fulfill_req_with_args = partial(fulfill_req, is_json=is_json, session=session)
    pool.map(fulfill_req_with_args, tickers_format)
    pool.close()
    pool.join()
    logger.info(f"Download end: {datetime.now()}")


if __name__ == "__main__":
    dwn_data(select=None, is_json=True)
