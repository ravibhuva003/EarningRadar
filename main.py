from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import requests
from datetime import datetime, timedelta

app = FastAPI(title="Earning Radar API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        "endpoints": [
            "/api/results-calendar",
            "/api/bse-results",
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


@app.get("/api/results-calendar")
def get_results_calendar(from_date: str = None, to_date: str = None):
    if not from_date:
        from_date = datetime.today().strftime("%d-%m-%Y")
    if not to_date:
        to_date = (datetime.today() + timedelta(days=7)).strftime("%d-%m-%Y")
    
    session = get_nse_session()
    url = f"https://www.nseindia.com/api/corporates-announcements?index=equities&category=Result&from_date={from_date}&to_date={to_date}"
    
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        if resp.status_code == 200:
            return {"source": "NSE", "data": resp.json()}
        
        # Fallback to NSE Event Calendar if specific dates return 404/non-200
        fallback_url = "https://www.nseindia.com/api/event-calendar"
        fallback_resp = session.get(fallback_url, headers=NSE_HEADERS, timeout=15)
        if fallback_resp.status_code == 200:
            return {"source": "NSE", "data": fallback_resp.json(), "note": "Retrieved from NSE Event Calendar"}
        
        return {"source": "NSE", "status_code": resp.status_code, "data": []}
    except Exception as e:
        return {"source": "NSE", "error": str(e), "data": []}

@app.get("/api/bse-results")
def get_bse_results():
    url = "https://api.bseindia.com/BseIndiaAPI/api/BoardMeetingDet/w?pageno=1&strCat=-1&strPrevDate=&strScrip=&strSearch=P&strToDate=&strType=C&subcategory=-1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bseindia.com",
        "Accept": "application/json, text/plain, */*"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", "").lower():
            return {"source": "BSE", "data": resp.json()}
        
        try:
            data = resp.json()
            return {"source": "BSE", "data": data}
        except Exception:
            return {"source": "BSE", "status_code": resp.status_code, "message": "BSE API returned non-JSON response", "data": []}
    except Exception as e:
        return {"source": "BSE", "error": str(e), "data": []}

@app.get("/api/company-results/{symbol}")
def get_company_results(symbol: str):
    session = get_nse_session()
    url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol.upper()}&section=financials"
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        return {"symbol": symbol.upper(), "status_code": resp.status_code, "message": f"Could not fetch financials for {symbol.upper()}"}
    except Exception as e:
        return {"symbol": symbol.upper(), "error": str(e)}
