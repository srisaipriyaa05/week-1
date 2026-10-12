import json
import re
import urllib.parse
import urllib.request
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
EMAIL = "24f2007077@ds.study.iitm.ac.in"
BASE = (
    "https://exam.sanand.workers.dev/questionData"
    "?email=24f2007077%40ds.study.iitm.ac.in"
    "&quizSign=avLgcigBKUvl1qd52L0eZsV9kZ5Js8y%2FAvAntXp4gcvwzjEsE8cG2zk%2FLe%2FZF9ZnKojBeGEfJgFq2P3OhWACp6Qd9Ik9Kj%2FrNzfTRoVhCtW%2BKuw%2B2H6x6k67dd47lzFIYBiJebPH3OzLaZzhRbkezgWlGXjSsEyjLmi2x0U91Aw7GBhCAC%2FKh8NkjZSxyDwUIAYyHMO5D8%2FyNuTdOvtAmdu2PjzOJPeRBvKzHgk0WJouUqccTcMMtddpJaYrv64SLbAOaVzTdbm8u3e0yG1Me6%2BfEWgOxp%2F1RNPugNM0I0FL%2FeRKg28pyC9dkEk1oU7zo0ySmz19R%2FIVkNAnrlbbNA%3D%3D"
    "&questionId=q-ledger-agent-server&path="
)
TZ = ZoneInfo("Asia/Kolkata")
CACHE = {"at": 0, "orders": None, "rates": None, "export": None}

def get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AcmeLedgerAgent/1.0"})
    with urllib.request.urlopen(req, timeout=6) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw)

def fetch(path: str):
    return get_json(BASE + urllib.parse.quote(path, safe="/"))

def rows_from(payload: Any):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("orders", "data", "results", "items", "rows", "records"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        # Some paginated APIs use an object as one row or wrap data deeper.
        for val in payload.values():
            if isinstance(val, (list, dict)):
                rows = rows_from(val)
                if rows:
                    return rows
    return []

def fetch_all_orders():
    # Pagination format is discovered from API responses at runtime.
    all_rows = []
    seen = set()
    for page in range(1, 201):
        payload = fetch(f"/orders?page={page}")
        rows = rows_from(payload)
        if not rows:
            break
        new_count = 0
        for row in rows:
            if isinstance(row, dict):
                marker = json.dumps(row, sort_keys=True, default=str)
                if marker not in seen:
                    seen.add(marker)
                    all_rows.append(row)
                    new_count += 1
        if new_count == 0:
            break
        # Stop when response metadata explicitly indicates no further page.
        if isinstance(payload, dict):
            meta = payload.get("pagination") or payload.get("meta") or {}
            if isinstance(meta, dict):
                if meta.get("has_next") is False or meta.get("hasNextPage") is False:
                    break
                total_pages = meta.get("total_pages") or meta.get("totalPages")
                if total_pages and page >= int(total_pages):
                    break
        if len(rows) == 0:
            break
    return all_rows

def get_data():
    now = __import__("time").time()
    if CACHE["orders"] is not None and now - CACHE["at"] < 60:
        return CACHE
    orders_payload = fetch("/orders?page=1")
    rates_payload = fetch("/rates")
    export_payload = fetch("/export")
    orders = rows_from(orders_payload)
    # Prefer export if it returns the complete ledger as a list/wrapper.
    export_rows = rows_from(export_payload)
    if len(export_rows) > len(orders):
        orders = export_rows
    # If pagination is indicated, fetch all pages.
    if isinstance(orders_payload, dict):
        meta = orders_payload.get("pagination") or orders_payload.get("meta") or {}
        if isinstance(meta, dict) and (
            meta.get("has_next") or meta.get("hasNextPage") or
            (meta.get("total_pages") and int(meta["total_pages"]) > 1) or
            (meta.get("totalPages") and int(meta["totalPages"]) > 1)
        ):
            orders = fetch_all_orders()
    CACHE.update({"at": now, "orders": orders, "rates": rates_payload, "export": export_payload})
    return CACHE

def pick(row, *names, default=None):
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return default

def parse_dt(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / (1000 if value > 10**11 else 1), tz=timezone.utc).astimezone(TZ)
        except Exception:
            return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=TZ)
            except Exception:
                pass
    return None

def money(value):
    try:
        return Decimal(str(value or 0).replace(",", "").replace("$", "").strip())
    except Exception:
        return Decimal("0")

def order_date(row):
    for key in ("order_date", "ordered_at", "created_at", "date", "created", "timestamp"):
        val = pick(row, key)
        if val is not None:
            return parse_dt(val)
    return None

def latest_orders(rows):
    latest = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        oid = pick(row, "order_id", "id", "orderId")
        if oid is None:
            latest[id(row)] = row
            continue
        updated = parse_dt(pick(row, "updated_at", "updatedAt")) or datetime.min.replace(tzinfo=TZ)
        if oid not in latest:
            latest[oid] = row
        else:
            old_updated = parse_dt(pick(latest[oid], "updated_at", "updatedAt")) or datetime.min.replace(tzinfo=TZ)
            if updated >= old_updated:
                latest[oid] = row
    return list(latest.values())

def extract_rate(rates, currency):
    if not isinstance(rates, dict):
        return None
    # Support common rates shapes.
    for key in ("rates", "data", "exchange_rates", "exchangeRates"):
        if isinstance(rates.get(key), dict):
            rates = rates[key]
            break
    val = rates.get(currency) or rates.get(currency.upper()) or rates.get(currency.lower())
    if isinstance(val, dict):
        val = val.get("rate") or val.get("usd") or val.get("USD")
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("1") if currency.upper() == "USD" else None

def amount_usd(row, rates):
    raw = pick(row, "total", "amount", "order_total", "total_amount", "price", "revenue", default=0)
    currency = str(pick(row, "currency", "currency_code", default="USD")).upper()
    val = money(raw)
    if currency == "USD":
        return val
    rate = extract_rate(rates, currency)
    if rate is None:
        return val
    # Common exchange APIs express rate as USD per unit; reverse if key says otherwise.
    return val * rate

def month_matches(dt, q):
    if not dt:
        return True
    year_match = re.search(r'\b(20\d{2})\b', q)
    year = int(year_match.group(1)) if year_match else None
    months = {
        "january":1,"jan":1,"february":2,"feb":2,"march":3,"mar":3,
        "april":4,"apr":4,"may":5,"june":6,"jun":6,"july":7,"jul":7,
        "august":8,"aug":8,"september":9,"sep":9,"sept":9,"october":10,"oct":10,
        "november":11,"nov":11,"december":12,"dec":12
    }
    month = next((num for name, num in months.items() if re.search(r'\b'+name+r'\b', q, re.I)), None)
    if month and dt.month != month:
        return False
    if year and dt.year != year:
        return False
    return True

def filter_rows(rows, q):
    selected = []
    region = None
    m = re.search(r'\b(?:in|from|for)\s+(?:the\s+)?([A-Za-z][A-Za-z -]+?)\s+region\b', q, re.I)
    if m:
        region = m.group(1).strip().lower()
    status_filter = "paid" if re.search(r'\brevenue\b', q, re.I) else None
    for row in rows:
        if not isinstance(row, dict):
            continue
        dt = order_date(row)
        if not month_matches(dt, q):
            continue
        if region:
            rv = str(pick(row, "region", "sales_region", default="")).lower()
            if rv != region:
                continue
        # Match product/customer terms if clearly mentioned after a preposition.
        for field_names in (("product_name","product","item_name","sku_name"), ("customer_name","customer","customer_id","customer_email")):
            pass
        if status_filter:
            status = str(pick(row, "status", default="")).lower()
            if status != "paid":
                continue
        selected.append(row)
    return selected

def answer(q: str):
    data = get_data()
    rows = latest_orders(data["orders"] or [])
    ql = q.lower()
    if any(word in ql for word in ("revenue", "total sales", "sales total", "gross sales")):
        subset = filter_rows(rows, q)
        total = sum((amount_usd(r, data["rates"]) for r in subset), Decimal("0"))
        return float(total.quantize(Decimal("0.01")))
    if "refund" in ql:
        total = Decimal("0")
        for r in rows:
            dt = order_date(r)
            if not month_matches(dt, q):
                continue
            if "region" in ql:
                m = re.search(r'\b(?:in|from|for)\s+(?:the\s+)?([A-Za-z][A-Za-z -]+?)\s+region\b', q, re.I)
                if m and str(pick(r, "region", default="")).lower() != m.group(1).lower():
                    continue
            refund = pick(r, "refund_amount", "refunded_amount", "refund", default=0)
            total += money(refund)
        return float(total.quantize(Decimal("0.01")))
    if any(w in ql for w in ("how many orders", "number of orders", "count of orders", "order count")):
        subset = filter_rows(rows, q)
        return len(subset)
    if "customer" in ql and any(w in ql for w in ("list", "which", "who", "name", "top")):
        names = sorted({str(pick(r, "customer_name", "customer", default="")) for r in rows if pick(r, "customer_name", "customer")})
        return names
    if "product" in ql and any(w in ql for w in ("list", "which", "what", "name", "top")):
        names = sorted({str(pick(r, "product_name", "product", "item_name", default="")) for r in rows if pick(r, "product_name", "product", "item_name")})
        return names
    return "I couldn't determine the requested metric from the question. Ask about revenue, refunds, order counts, products, or customers."

@app.get("/")
async def health():
    return {"status": "ok", "service": "Acme Appliances order ledger agent"}

@app.post("/")
async def ask(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"answer": "Please send valid JSON."}, status_code=400)
    q = body.get("question") if isinstance(body, dict) else None
    if not isinstance(q, str) or not q.strip():
        return JSONResponse({"answer": "Please provide a non-empty question."}, status_code=400)
    try:
        result = answer(q.strip())
        return JSONResponse({"answer": result})
    except Exception:
        return JSONResponse({"answer": "The ledger API is temporarily unavailable. Please retry."}, status_code=502)
