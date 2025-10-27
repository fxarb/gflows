import requests
import base64
import orjson
from multiprocessing.dummy import Pool as ThreadPool
from datetime import datetime
from os import environ, getcwd
from pathlib import Path
from functools import partial


def fulfill_req(ticker, is_json, session):
    """
    Fulfills a request to download options data for a given ticker.

    :param ticker: The ticker symbol of the stock.
    :param is_json: A boolean indicating whether to download the data in JSON format.
    :param session: A requests session object.
    """
    # The URL of the API to download the data from.
    api_url = environ.get("API_URL", 'https://cdn.cboe.com/api/global/delayed_quotes/options/{0}.json').format(ticker.upper()).strip()
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
        for _ in range(3):  # in case of unavailable data, retry twice
            try:  # check if data is available
                r.raise_for_status()
            except requests.exceptions.HTTPError as e:
                print(e)
                f.write("Unavailable".encode("utf-8"))
                if r.status_code == 504:  # check if timeout occurred
                    print("gateway timeout, retrying search for", ticker, d_format)
                    continue
                elif r.status_code == 500:  # internal server error
                    print(
                        "internal server error, retrying search for", ticker, d_format
                    )
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
                print("\nrequest done for", ticker, d_format)
                break


def dwn_data(select, is_json):
    """
    Downloads options data for a given list of tickers.

    :param select: A list of tickers to download data for.
    :param is_json: A boolean indicating whether to download the data in JSON format.
    """
    # A thread pool to download the data in parallel.
    pool = ThreadPool()
    print(f"\ndownload start: {datetime.now()}\n")
    # A list of tickers to download data for.
    tickers_pool = (environ.get("TICKERS") or "^SPX,^NDX,^RUT").strip().split(",")
    if select:  # select tickers to download
        tickers_pool = [f"^{t}" if f"^{t}" in tickers_pool else t for t in select]
    # A list of formatted tickers.
    tickers_format = [
        f"_{ticker[1:]}" if ticker[0] == "^" else ticker for ticker in tickers_pool
    ]
    # A requests session object.
    session = requests.Session()
    session.headers.update({"Accept": "application/json" if is_json else "text/csv"})
    # A partial function to fulfill the request.
    fulfill_req_with_args = partial(fulfill_req, is_json=is_json, session=session)
    pool.map(fulfill_req_with_args, tickers_format)
    pool.close()
    pool.join()
    print(f"\n\ndownload end: {datetime.now()}\n")


if __name__ == "__main__":
    dwn_data(select=None, is_json=True)
