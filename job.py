import requests
import json
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.combining import OrTrigger
from zoneinfo import ZoneInfo
from cboe_exchange.converter import convert_szosho_to_cboe
import re
import os
from modules.logging_config import setup_logging

logger = setup_logging()

def fetch_and_convert():
    """
    Fetches JSON data from the specified URL, converts it, and saves it to files.
    """
    url = os.environ.get("SZOSHO_URL", "http://localhost")
    logger.debug(f"Fetching data from URL: {url}")
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes
        logger.info(f"Successfully fetched data from {url}")
        szosho_data = response.json()

        with open('szosho.json', 'w', encoding='utf-8') as f:
            json.dump(szosho_data, f, ensure_ascii=False, indent=4)
        logger.debug("Successfully saved szosho.json")
        
        # Extract RiskFreeRate and save to its own file
        if 'RiskFreeRate' in szosho_data:
            risk_free_rate = szosho_data['RiskFreeRate']
            with open('szosho.RiskFreeRate.json', 'w', encoding='utf-8') as f:
                json.dump({'RiskFreeRate': risk_free_rate}, f, ensure_ascii=False, indent=4)
            logger.debug("Successfully saved szosho.RiskFreeRate.json")


        converted_data = convert_szosho_to_cboe(szosho_data)

        for data in converted_data:
            # Extract the base stock symbol (e.g., "000001" from "000001.SZ")
            symbol_match = re.match(r'(\d+)', data['symbol'])
            if symbol_match:
                stock_symbol = symbol_match.group(1)
                filename = f"cboe.{stock_symbol}.json"
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                logger.info(f"Successfully saved {filename}")

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {e}")
    except Exception as e:
        logger.error(f"An error occurred: {e}")

if __name__ == "__main__":
    # This script is intended to be run as a scheduled job.
    # To run the job once for testing, you can call fetch_and_convert() directly.
    # fetch_and_convert()

    scheduler = BlockingScheduler()
    # Schedule the job to run every minute between 09:15 and 15:30 Shanghai time
    trigger = OrTrigger(
        [
            CronTrigger(
                day_of_week="0-4",
                hour="9",
                minute="15-59",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
            CronTrigger(
                day_of_week="0-4",
                hour="10-14",
                minute="*",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
            CronTrigger(
                day_of_week="0-4",
                hour="15",
                minute="0-30",
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
        ]
    )
    scheduler.add_job(fetch_and_convert, trigger=trigger)
    logger.info("Scheduler started. Press Ctrl+C to exit.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
        pass
