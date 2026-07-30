"""Shared config — loads .env and exposes free-tier API keys to lab modules."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
except Exception:
    pass

FRED_KEY = os.environ.get("FRED_API_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
AV_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "")
SNAPTRADE_CLIENT_ID = os.environ.get("SNAPTRADE_CLIENT_ID", "")
SNAPTRADE_CONSUMER_KEY = os.environ.get("SNAPTRADE_CONSUMER_KEY", "")
