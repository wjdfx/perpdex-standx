"""Project configuration (StandX only)."""

import os

from dotenv import load_dotenv

load_dotenv()

# Exchange
EXCHANGE_TYPE = os.getenv("EXCHANGE_TYPE", "standx").strip().lower() or "standx"
BASE_URL = os.getenv("BASE_URL", "https://perps.standx.com")

# Runtime
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv(
    "LOG_FORMAT",
    "%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s",
)
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# StandX API token mode
STANDX_API_TOKEN = os.getenv("STANDX_API_TOKEN", "").strip()
STANDX_REQUEST_SIGN_PRIVATE_KEY = os.getenv("STANDX_REQUEST_SIGN_PRIVATE_KEY", "").strip()
STANDX_REQUEST_SIGN_VERSION = os.getenv("STANDX_REQUEST_SIGN_VERSION", "v1").strip() or "v1"

# DingTalk (optional)
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_KEYWORD = os.getenv("DINGTALK_KEYWORD", "Standx")
