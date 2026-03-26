"""Authenticates with Google Sheets and logs every simulated position with timestamp, market, outcome, price, stake, and running P&L."""

import os
import datetime

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")


def get_sheet():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_file(
            "credentials.json", scopes=scopes
        )
        client = gspread.authorize(creds)
        sheet = client.open(GOOGLE_SHEET_NAME)
        return sheet.get_worksheet(0)
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}")
        return None


def log_opportunity(sheet, market_name, stakes_result, prices):
    if sheet is None:
        print("Error: sheet is None — skipping log.")
        return

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_logged = 0

    for outcome, stake in stakes_result["stakes"].items():
        row = [
            timestamp,
            market_name,
            outcome,
            prices[outcome],
            stake,
            "",
            ""
        ]
        sheet.append_row(row)
        rows_logged += 1

    print(f"Logged {rows_logged} row(s) to '{GOOGLE_SHEET_NAME}'.")


if __name__ == "__main__":

    # Test: log a fake opportunity to ArbBotLog
    sheet = get_sheet()
    if sheet:
        fake_prices = {
            "Le Pen wins": 0.42,
            "Macron wins": 0.38,
            "Other wins": 0.10
        }
        fake_stakes = {
            "stakes": {
                "Le Pen wins": 4200.0,
                "Macron wins": 3800.0,
                "Other wins": 1000.0
            },
            "total_deployed": 9000.0,
            "expected_payout": 10000.0,
            "expected_profit": 1000.0
        }
        log_opportunity(
            sheet,
            "French Presidential Election 2027",
            fake_stakes,
            fake_prices
        )
        print("Check ArbBotLog in Google Sheets to verify 3 rows appeared")
