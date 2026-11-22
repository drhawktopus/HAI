#!/usr/bin/env python3
"""An hourly real-time electricity price feed for a Home Assistant command_line sensor: today's and tomorrow's 24
prices from the utility's price API, and the current hour's price.

    python3 hourly_price_feed.py <prices page URL> <prices API URL>

The page is fetched first: the API answers only a session holding the cookie the page sets. The API takes a POST of
{"SelectedDate": "Y-M-D"} and returns {"hourlyPriceDetails": [{"hour": N, "price": P}, ...]}, hour N covering
(N-1):00 to N:00 local time. Prints one JSON object (current_price, current_hour, today, tomorrow); on any failure an
error and a null current_price, and exits 1.
"""
import json, sys
from datetime import datetime, timedelta
try:
    import requests
except ImportError:
    print(json.dumps({'error': 'requests unavailable', 'current_price': None}))
    sys.exit(1)

if len(sys.argv) != 3:
    print(json.dumps({'error': 'usage: hourly_price_feed.py <prices page URL> <prices API URL>', 'current_price': None}))
    sys.exit(1)
PAGE, API = sys.argv[1], sys.argv[2]


def date_str(d):
    return f'{d.year}-{d.month}-{d.day}'


def fetch_prices(session, d):
    r = session.post(API, json={'SelectedDate': date_str(d)}, timeout=15)
    r.raise_for_status()
    details = r.json().get('hourlyPriceDetails') or []
    return {str(int(p['hour'])): float(p['price']) for p in details}


now = datetime.now()
current_hour = str(now.hour + 1)

try:
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Referer': PAGE})
    s.get(PAGE, timeout=15)
    today = fetch_prices(s, now)
    tomorrow = fetch_prices(s, now + timedelta(days=1))
    print(json.dumps({'current_price': today.get(current_hour), 'current_hour': int(current_hour), 'today': today, 'tomorrow': tomorrow}))
except Exception as e:
    print(json.dumps({'error': str(e), 'current_price': None}))
    sys.exit(1)
