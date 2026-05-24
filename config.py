import os
import random
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_env_int(key, default):
    """Safely get integer from environment variable"""
    value = os.getenv(key)
    if value is None or value.strip() == '':
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_bool(key, default):
    """Safely get boolean from environment variable"""
    value = os.getenv(key)
    if value is None or value.strip() == '':
        return default
    return value.lower() in ['true', '1', 'yes', 'on']


def get_env_list(key, default):
    """Safely get list from environment variable"""
    value = os.getenv(key)
    if value is None or value.strip() == '':
        return default
    return [item.strip() for item in value.split(',') if item.strip()]


def get_env_float(key, default):
    """Safely get float from environment variable"""
    value = os.getenv(key)
    if value is None or value.strip() == '':
        return default
    try:
        return float(value)
    except ValueError:
        return default


def get_session():
    """Create a requests session with proper headers"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': 'https://www.reddit.com/',
        'Origin': 'https://www.reddit.com',
        'DNT': '1',
        'Connection': 'keep-alive',
    })
    return session


# CORS proxies list
CORS_PROXIES = [
    # "https://cors-anywhere.herokuapp.com/",
    # "https://corsproxy.io/",
    # "https://api.allorigins.win/raw?url=",
    # "https://cors.bridged.cc/",
]


def get_proxy_url(original_url, proxy_index=0):
    """Wrap URL with a CORS proxy"""
    if proxy_index >= len(CORS_PROXIES):
        return original_url
    
    proxy = CORS_PROXIES[proxy_index]
    
    if "allorigins" in proxy:
        return f"{proxy}{original_url}"
    elif "cors-anywhere" in proxy or "corsproxy.io" in proxy or "bridged.cc" in proxy:
        return f"{proxy}{original_url}"
    else:
        return original_url


# ============================================
# REDDIT CONFIGURATION
# ============================================
SUBREDDITS = get_env_list("SUBREDDITS", ["Gambiarra"])
POST_LIMIT_PER_SUBREDDIT = get_env_int("POST_LIMIT_PER_SUBREDDIT", 10)
TOP_COMMENTS_PER_POST = get_env_int("TOP_COMMENTS_PER_POST", 10)
MIN_SCORE = get_env_int("MIN_SCORE", 0)
COMMENT_DEPTH = get_env_int("COMMENT_DEPTH", 3)
INCLUDE_NESTED_COMMENTS = get_env_bool("INCLUDE_NESTED_COMMENTS", True)
USE_PROXY = get_env_bool("USE_PROXY", False)


# ============================================
# IMAGE CONFIGURATION - KINDLE OPTIMIZED
# ============================================
INCLUDE_IMAGES = get_env_bool("INCLUDE_IMAGES", True)
DOWNLOAD_IMAGES = get_env_bool("DOWNLOAD_IMAGES", True)

# Kindle image settings
KINDLE_IMAGE_WIDTH = get_env_int("KINDLE_IMAGE_WIDTH", 600)  # Default width for Kindle
KINDLE_IMAGE_QUALITY = get_env_int("KINDLE_IMAGE_QUALITY", 75)  # JPEG quality (1-100)
KINDLE_MAX_IMAGE_SIZE_MB = get_env_float("KINDLE_MAX_IMAGE_SIZE_MB", 0.5)  # Max size per image in MB

# For backward compatibility
IMAGE_MAX_WIDTH = KINDLE_IMAGE_WIDTH


# ============================================
# KINDLE CONFIGURATION
# ============================================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = get_env_int("SMTP_PORT", 587)

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
KINDLE_EMAIL = os.getenv("KINDLE_EMAIL")

if SENDER_EMAIL and SENDER_PASSWORD and KINDLE_EMAIL:
    KINDLE = {
        "smtp_server": SMTP_SERVER,
        "smtp_port": SMTP_PORT,
        "sender_email": SENDER_EMAIL,
        "sender_password": SENDER_PASSWORD,
        "kindle_email": KINDLE_EMAIL,
    }
else:
    KINDLE = {
        "smtp_server": SMTP_SERVER,
        "smtp_port": SMTP_PORT,
        "sender_email": SENDER_EMAIL or "not_set@example.com",
        "sender_password": SENDER_PASSWORD or "not_set",
        "kindle_email": KINDLE_EMAIL or "not_set@kindle.com",
    }
    if not all([SENDER_EMAIL, SENDER_PASSWORD, KINDLE_EMAIL]):
        print("⚠️  Warning: Email credentials not fully configured")