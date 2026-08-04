from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import requests
import time
from datetime import datetime, timedelta

app = FastAPI(title="Earning Radar API", version="1.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# 5-Minute In-Memory Cache to prevent NSE/BSE Rate Limiting
# ---------------------------------------------------------
_cache = {}

def cached_fetch(key: str, fn, ttl: int = 300):
    """Caches fetch responses for 'ttl' seconds (default 300s / 5 minutes)."""
    if key in _cache and time.time() - _cache[key]["t"] < ttl:
        return _cache[key]["d"]
    result = fn()
    _cache[key] = {"d": result, "t": time.time()}
    return result


SYSTEM_PROMPT = """You are a sharp Indian equity analyst specialising in NSE/BSE-listed companies. 
For each result, write exactly 3 sentences:
1. Beat or miss vs analyst estimates — state the exact % difference.
2. The single biggest driver — revenue momentum, margin change, one-off item, or sector trend.
3. Short-term stock outlook for the next 5 trading sessions with a clear directional bias.
Use rupee amounts and percentages. Be specific. No generic statements."""

class CompanyResultData(BaseModel):
    name: str
    symbol: str
    sector: str
    market_cap: str
    quarter: Optional[str] = "Q1 FY27 (April–June 2026)"
    revenue: str = "Not announced"
    est_revenue: str
    revenue_yoy: str
    net_profit: str = "Not announced"
    est_profit: str
    profit_yoy: str
    eps: str
    ebitda_margin: str
    vs_estimates: str  # BEAT / MISSED / Not yet reported

def format_user_prompt(data: CompanyResultData) -> str:
    return f"""Company: {data.name} | Symbol: {data.symbol} | Sector: {data.sector} | Cap: {data.market_cap}
Quarter: {data.quarter}
Revenue: {data.revenue} | Estimate: {data.est_revenue} | YoY: {data.revenue_yoy}
Net Profit: {data.net_profit} | Estimate: {data.est_profit} | YoY: {data.profit_yoy}
EPS: {data.eps} | EBITDA Margin: {data.ebitda_margin}
vs estimates: {data.vs_estimates}"""

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com",
    "Connection": "keep-alive",
}

def get_nse_session():
    """NSE requires an active browser session with valid cookies before API calls work."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com/", headers=NSE_HEADERS, timeout=10)
        session.get("https://www.nseindia.com/market-data/live-equity-market", headers=NSE_HEADERS, timeout=10)
    except Exception:
        pass
    return session


@app.get("/")
def read_root():
    return {
        "message": "Earning Radar API is running",
        "cache_active": True,
        "ttl_seconds": 300,
        "endpoints": [
            "/api/results-calendar",
            "/api/bse-results",
            "/api/merged-calendar",
            "/api/company-results/{symbol}",
            "/api/generate-prompt"
        ]
    }

@app.post("/api/generate-prompt")
def generate_prompt(data: CompanyResultData):
    """Generates System Prompt and formatted User Message for Equity Analysis."""
    return {
        "system_prompt": SYSTEM_PROMPT,
        "user_message": format_user_prompt(data)
    }


def fetch_raw_nse_calendar(from_date: str, to_date: str):
    session = get_nse_session()
    url = f"https://www.nseindia.com/api/corporates-announcements?index=equities&category=Result&from_date={from_date}&to_date={to_date}"
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        
        fallback_url = "https://www.nseindia.com/api/event-calendar"
        fallback_resp = session.get(fallback_url, headers=NSE_HEADERS, timeout=15)
        if fallback_resp.status_code == 200:
            return fallback_resp.json()
        return []
    except Exception:
        return []


@app.get("/api/results-calendar")
def get_results_calendar(from_date: str = None, to_date: str = None):
    if not from_date:
        from_date = datetime.today().strftime("%d-%m-%Y")
    if not to_date:
        to_date = (datetime.today() + timedelta(days=7)).strftime("%d-%m-%Y")
    
    cache_key = f"nse_calendar_{from_date}_{to_date}"
    data = cached_fetch(cache_key, lambda: fetch_raw_nse_calendar(from_date, to_date), ttl=300)
    return {"source": "NSE", "cached": cache_key in _cache, "data": data}


def fetch_raw_bse_results():
    url = "https://api.bseindia.com/BseIndiaAPI/api/BoardMeetingDet/w?pageno=1&strCat=-1&strPrevDate=&strScrip=&strSearch=P&strToDate=&strType=C&subcategory=-1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bseindia.com",
        "Accept": "application/json, text/plain, */*"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200 and "json" in resp.headers.get("content-type", "").lower():
            return resp.json()
        try:
            return resp.json()
        except Exception:
            return []
    except Exception:
        return []


@app.get("/api/bse-results")
def get_bse_results():
    cache_key = "bse_results_raw"
    data = cached_fetch(cache_key, fetch_raw_bse_results, ttl=300)
    return {"source": "BSE", "cached": cache_key in _cache, "data": data}


@app.get("/api/merged-calendar")
def get_merged_calendar(from_date: str = None, to_date: str = None):
    """
    Merges NSE and BSE earnings announcements by ISIN / Symbol to cover 
    5,000+ listed companies without duplicate entries.
    """
    if not from_date:
        from_date = datetime.today().strftime("%d-%m-%Y")
    if not to_date:
        to_date = (datetime.today() + timedelta(days=7)).strftime("%d-%m-%Y")

    cache_key = f"merged_calendar_{from_date}_{to_date}"

    def perform_merge():
        nse_raw = fetch_raw_nse_calendar(from_date, to_date)
        bse_raw = fetch_raw_bse_results()

        merged_dict = {}

        # 1. Process NSE entries
        if isinstance(nse_raw, list):
            for item in nse_raw:
                isin = item.get("isin") or item.get("isinCode") or item.get("symbol") or item.get("sm_symbol")
                symbol = item.get("symbol") or item.get("sm_symbol") or isin
                if not isin and not symbol:
                    continue
                
                key = (isin or symbol).upper()
                merged_dict[key] = {
                    "isin": isin,
                    "symbol": symbol,
                    "company_name": item.get("company") or item.get("sm_name") or symbol,
                    "exchanges": ["NSE"],
                    "event_date": item.get("an_dt") or item.get("bm_date") or from_date,
                    "purpose": item.get("purpose") or item.get("bm_desc") or "Financial Results",
                    "nse_data": item
                }

        # 2. Process BSE entries and deduplicate by ISIN / Symbol
        if isinstance(bse_raw, list):
            for item in bse_raw:
                isin = item.get("isin") or item.get("ISIN") or item.get("strScrip") or item.get("scrip_id")
                symbol = item.get("symbol") or item.get("scrip_id") or item.get("strScrip") or isin
                if not isin and not symbol:
                    continue

                key = (isin or symbol).upper()
                if key in merged_dict:
                    if "BSE" not in merged_dict[key]["exchanges"]:
                        merged_dict[key]["exchanges"].append("BSE")
                    merged_dict[key]["bse_data"] = item
                else:
                    merged_dict[key] = {
                        "isin": isin,
                        "symbol": symbol,
                        "company_name": item.get("company_name") or item.get("scrip_name") or symbol,
                        "exchanges": ["BSE"],
                        "event_date": item.get("meeting_date") or item.get("date") or from_date,
                        "purpose": item.get("purpose") or "Board Meeting Results",
                        "bse_data": item
                    }

        return list(merged_dict.values())

    merged_data = cached_fetch(cache_key, perform_merge, ttl=300)
    return {
        "status": "success",
        "total_unique_companies": len(merged_data),
        "cached": cache_key in _cache,
        "ttl_seconds": 300,
        "data": merged_data
    }


@app.get("/api/company-results/{symbol}")
def get_company_results(symbol: str):
    cache_key = f"company_results_{symbol.upper()}"
    
    def fetch_company():
        session = get_nse_session()
        url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol.upper()}&section=financials"
        try:
            resp = session.get(url, headers=NSE_HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            return {"symbol": symbol.upper(), "status_code": resp.status_code, "message": f"Could not fetch financials for {symbol.upper()}"}
        except Exception as e:
            return {"symbol": symbol.upper(), "error": str(e)}

    return cached_fetch(cache_key, fetch_company, ttl=300)
