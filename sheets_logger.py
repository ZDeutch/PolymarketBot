"""Authenticates with Google Sheets and logs every simulated position with timestamp, market, outcome, price, stake, and running P&L."""

import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "ArbBotLog")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")

HEADERS = ["Timestamp", "Market", "Outcome", "Price", "Stake", "P&L", "Running P&L"]


def get_sheet():
    """Authenticate and return the first worksheet of the target Google Sheet.
    Returns None if authentication or connection fails."""
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        worksheet = spreadsheet.sheet1

        # Ensure header row exists
        existing = worksheet.row_values(1)
        if existing != HEADERS:
            worksheet.insert_row(HEADERS, 1)

        return worksheet
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}")
        return None


def log_opportunity(sheet, market_name: str, stakes_result: dict, prices: dict):
    """Append one row per outcome to the sheet, with a shared Running P&L column.

    Args:
        sheet:          gspread Worksheet object from get_sheet()
        market_name:    Human-readable event title (e.g. 'SC Senate Republican Primary')
        stakes_result:  Dict from calculate_stakes() with keys:
                        stakes, total_deployed, expected_payout, expected_profit
        prices:         Dict of {outcome_name: yes_price}
    """
    try:
        # Compute Running P&L: last recorded value + this opportunity's expected profit
        all_records = sheet.get_all_records()
        last_running_pl = 0.0
        if all_records:
            for row in reversed(all_records):
                val = row.get("Running P&L", "")
                if val != "":
                    try:
                        last_running_pl = float(val)
                        break
                    except (ValueError, TypeError):
                        pass

        expected_profit = stakes_result["expected_profit"]
        running_pl = round(last_running_pl + expected_profit, 2)

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stakes = stakes_result["stakes"]

        rows = []
        for i, (outcome, price) in enumerate(prices.items()):
            stake = stakes.get(outcome, 0.0)
            rows.append([
                timestamp,
                market_name,
                outcome,
                round(price, 4),
                round(stake, 2),
                round(expected_profit, 2),
                running_pl if i == 0 else "",   # Running P&L only on first row of group
            ])

        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"  Logged {len(rows)} rows to '{SHEET_NAME}' — Running P&L: ${running_pl:,.2f}")

    except Exception as e:
        print(f"Error logging opportunity to Sheets: {e}")


if __name__ == "__main__":
    sheet = get_sheet()
    if sheet:
        print("Connected to Google Sheets successfully.")
        print(f"Sheet: {sheet.title}")
    else:
        print("Failed to connect.")
