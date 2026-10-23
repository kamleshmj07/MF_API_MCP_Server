"""
Mutual Fund Analysis MCP Server
Connects to MFAPI (https://www.mfapi.in) for Indian mutual fund data.
Base API: https://api.mfapi.in
Provides performance analysis, benchmark comparison, and key metrics.
"""

import json
import math
import ssl
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum

import httpx
import truststore
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.mcpserver import MCPServer

# ─────────────────────────────────────────────
# Server Init
# ─────────────────────────────────────────────
mcp = MCPServer("mutualfund_mcp")

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
MFAPI_BASE        = "https://api.mfapi.in/mf"
MFAPI_SEARCH_BASE = "https://api.mfapi.in/mf/search"
MFAPI_LATEST      = "https://api.mfapi.in/mf/{}/latest"
TIMEOUT           = 20.0

# Verify TLS against the OS certificate store (Windows/macOS/Linux) so
# corporate proxies and antivirus TLS inspection roots are trusted.
SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

# Benchmark mapping by fund category keywords
# Returns (benchmark_name, benchmark_scheme_code)
# Using well-known index fund scheme codes from MFAPI as proxy benchmarks
BENCHMARK_MAP: Dict[str, Tuple[str, int]] = {
    # Equity – Large Cap → Nifty 50
    "large cap":        ("Nifty 50",        120716),
    "largecap":         ("Nifty 50",        120716),
    "bluechip":         ("Nifty 50",        120716),
    # Equity – Mid Cap → Nifty Midcap 150
    "mid cap":          ("Nifty Midcap 150", 120684),
    "midcap":           ("Nifty Midcap 150", 120684),
    # Equity – Small Cap → Nifty Smallcap 250
    "small cap":        ("Nifty Smallcap 250", 145552),
    "smallcap":         ("Nifty Smallcap 250", 145552),
    # Flexi / Multi cap → Nifty 500
    "flexi cap":        ("Nifty 500",        120685),
    "flexicap":         ("Nifty 500",        120685),
    "multi cap":        ("Nifty 500",        120685),
    "multicap":         ("Nifty 500",        120685),
    # ELSS → Nifty 500
    "elss":             ("Nifty 500",        120685),
    "tax saver":        ("Nifty 500",        120685),
    # Index / ETF → Nifty 50
    "index":            ("Nifty 50",        120716),
    "etf":              ("Nifty 50",        120716),
    "nifty":            ("Nifty 50",        120716),
    "sensex":           ("Sensex",          119551),
    # Hybrid / Balanced
    "hybrid":           ("Nifty 50 Hybrid Composite Debt 65:35", 120505),
    "balanced":         ("Nifty 50 Hybrid Composite Debt 65:35", 120505),
    "aggressive hybrid":("Nifty 50 Hybrid Composite Debt 65:35", 120505),
    # Debt
    "debt":             ("Crisil Short Term Bond",  118989),
    "short term":       ("Crisil Short Term Bond",  118989),
    "liquid":           ("Crisil Liquid",            118701),
    "overnight":        ("Crisil Liquid",            118701),
    "ultra short":      ("Crisil Liquid",            118701),
    "gilt":             ("Crisil 10 Year Gilt",      119271),
    "long duration":    ("Crisil 10 Year Gilt",      119271),
    "corporate bond":   ("Crisil Short Term Bond",   118989),
    "credit risk":      ("Crisil Short Term Bond",   118989),
}

DEFAULT_BENCHMARK = ("Nifty 50", 120716)

RISK_FREE_RATE = 0.065  # ~6.5% Indian T-bill rate

# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────
class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON     = "json"

# ─────────────────────────────────────────────
# Pydantic Input Models
# ─────────────────────────────────────────────
class SearchFundInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(..., description="Fund name or keyword to search (e.g. 'SBI Bluechip', 'Axis Midcap')", min_length=2)
    limit: Optional[int] = Field(default=10, description="Max results to return (1-20)", ge=1, le=20)

class AnalyzeFundInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    scheme_code: int = Field(..., description="MFAPI scheme code of the mutual fund (get from mf_search_funds)")
    period_years: Optional[float] = Field(default=3.0, description="Analysis period in years (e.g. 1, 3, 5)", ge=0.25, le=10.0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' for readable report, 'json' for raw data")

class ComparePeriodsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    scheme_code: int = Field(..., description="MFAPI scheme code of the mutual fund")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")

class GetNavHistoryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    scheme_code: int = Field(..., description="MFAPI scheme code of the mutual fund")
    days: Optional[int] = Field(default=365, description="Number of past days of NAV history to return", ge=1, le=1825)

# ─────────────────────────────────────────────
# HTTP Helpers
# ─────────────────────────────────────────────
async def _get(url: str, params: Optional[Dict] = None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=SSL_CONTEXT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 404:
            return "Error: Fund not found. Please check the scheme code using mf_search_funds."
        if e.response.status_code == 429:
            return "Error: Rate limit exceeded. Please wait a moment and try again."
        return f"Error: API request failed with status {e.response.status_code}."
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. MFAPI may be slow — please retry."
    return f"Error: {type(e).__name__}: {str(e)}"

# ─────────────────────────────────────────────
# Data Helpers
# ─────────────────────────────────────────────
def _parse_nav_series(data: List[Dict]) -> List[Tuple[datetime, float]]:
    """Parse MFAPI NAV data into sorted (date, nav) tuples, newest first."""
    series = []
    for item in data:
        try:
            dt  = datetime.strptime(item["date"], "%d-%m-%Y")
            nav = float(item["nav"])
            series.append((dt, nav))
        except (ValueError, KeyError):
            continue
    series.sort(key=lambda x: x[0], reverse=True)
    return series

def _nav_at_date(series: List[Tuple[datetime, float]], target: datetime) -> Optional[float]:
    """Return NAV closest to (but not after) target date."""
    for dt, nav in series:
        if dt <= target:
            return nav
    return None

def _cagr(start_nav: float, end_nav: float, years: float) -> float:
    if years <= 0 or start_nav <= 0:
        return 0.0
    return ((end_nav / start_nav) ** (1 / years) - 1) * 100

def _compute_returns(series: List[Tuple[datetime, float]]) -> Dict[str, Optional[float]]:
    """Compute absolute/CAGR returns for standard periods."""
    if not series:
        return {}
    latest_dt, latest_nav = series[0]
    periods = {"1M": 1/12, "3M": 3/12, "6M": 6/12, "1Y": 1, "3Y": 3, "5Y": 5}
    results = {}
    for label, yrs in periods.items():
        target = latest_dt - timedelta(days=int(yrs * 365))
        past_nav = _nav_at_date(series, target)
        if past_nav:
            if yrs <= 1:
                results[label] = round(((latest_nav / past_nav) - 1) * 100, 2)
            else:
                results[label] = round(_cagr(past_nav, latest_nav, yrs), 2)
        else:
            results[label] = None
    return results

def _compute_volatility(series: List[Tuple[datetime, float]], days: int = 252) -> Optional[float]:
    """Annualised standard deviation of daily returns (%)."""
    navs = [n for _, n in series[:days + 1]]
    if len(navs) < 30:
        return None
    daily_returns = [(navs[i] / navs[i + 1]) - 1 for i in range(len(navs) - 1)]
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    return round(math.sqrt(variance) * math.sqrt(252) * 100, 2)

def _compute_max_drawdown(series: List[Tuple[datetime, float]], years: float = 3) -> Optional[float]:
    """Maximum peak-to-trough drawdown (%) over the given period."""
    cutoff = series[0][0] - timedelta(days=int(years * 365))
    navs = [n for dt, n in series if dt >= cutoff]
    if len(navs) < 10:
        return None
    peak    = navs[-1]  # series is newest-first, so walk backwards
    max_dd  = 0.0
    for nav in reversed(navs):
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)

def _compute_sharpe(cagr_pct: Optional[float], volatility: Optional[float]) -> Optional[float]:
    if cagr_pct is None or volatility is None or volatility == 0:
        return None
    return round((cagr_pct / 100 - RISK_FREE_RATE) / (volatility / 100), 2)

def _compute_beta_alpha(
    fund_series: List[Tuple[datetime, float]],
    bench_series: List[Tuple[datetime, float]],
    years: float = 3,
) -> Tuple[Optional[float], Optional[float]]:
    """Compute beta and alpha vs benchmark using monthly return regression."""
    cutoff = fund_series[0][0] - timedelta(days=int(years * 365))
    months = int(years * 12)

    fund_monthly, bench_monthly = [], []
    latest = fund_series[0][0]

    for i in range(months):
        t1 = latest - timedelta(days=30 * i)
        t0 = latest - timedelta(days=30 * (i + 1))
        fn1 = _nav_at_date(fund_series,  t1)
        fn0 = _nav_at_date(fund_series,  t0)
        bn1 = _nav_at_date(bench_series, t1)
        bn0 = _nav_at_date(bench_series, t0)
        if fn1 and fn0 and bn1 and bn0 and fn0 > 0 and bn0 > 0:
            fund_monthly.append((fn1 / fn0) - 1)
            bench_monthly.append((bn1 / bn0) - 1)

    n = len(fund_monthly)
    if n < 12:
        return None, None

    mean_f = sum(fund_monthly)  / n
    mean_b = sum(bench_monthly) / n
    cov    = sum((fund_monthly[i] - mean_f) * (bench_monthly[i] - mean_b) for i in range(n)) / (n - 1)
    var_b  = sum((r - mean_b) ** 2 for r in bench_monthly) / (n - 1)

    if var_b == 0:
        return None, None

    beta  = round(cov / var_b, 2)
    alpha = round((mean_f - RISK_FREE_RATE / 12) - beta * (mean_b - RISK_FREE_RATE / 12), 4)
    alpha_annualised = round(alpha * 12 * 100, 2)
    return beta, alpha_annualised

def _rolling_returns(series: List[Tuple[datetime, float]], window_years: float = 1) -> Dict:
    """Compute rolling returns stats (min, max, avg, pct_positive)."""
    window_days = int(window_years * 365)
    returns = []
    for i, (dt, nav) in enumerate(series):
        target = dt - timedelta(days=window_days)
        past_nav = _nav_at_date(series[i:], target)
        if past_nav:
            ret = _cagr(past_nav, nav, window_years) if window_years > 1 else ((nav / past_nav) - 1) * 100
            returns.append(ret)
    if not returns:
        return {}
    pos = sum(1 for r in returns if r > 0)
    return {
        "min":          round(min(returns), 2),
        "max":          round(max(returns), 2),
        "avg":          round(sum(returns) / len(returns), 2),
        "pct_positive": round(pos / len(returns) * 100, 1),
        "observations": len(returns),
    }

def _detect_benchmark(fund_name: str, scheme_category: str = "") -> Tuple[str, int]:
    """Auto-detect benchmark based on fund name and category."""
    text = (fund_name + " " + scheme_category).lower()
    for keyword, benchmark in BENCHMARK_MAP.items():
        if keyword in text:
            return benchmark
    return DEFAULT_BENCHMARK

def _verdict(fund_ret: Optional[float], bench_ret: Optional[float], label: str = "") -> str:
    if fund_ret is None or bench_ret is None:
        return "Insufficient data"
    diff = fund_ret - bench_ret
    if diff > 2:
        return f"✅ Outperforming benchmark by {diff:.2f}%"
    elif diff < -2:
        return f"🔴 Underperforming benchmark by {abs(diff):.2f}%"
    else:
        return f"🟡 At par with benchmark (diff: {diff:+.2f}%)"

# ─────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────

@mcp.tool(
    name="mf_search_funds",
    annotations={
        "title": "Search Mutual Funds",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mf_search_funds(params: SearchFundInput) -> str:
    """Search for Indian mutual funds by name or keyword.

    Returns a list of matching funds with their scheme codes, which are needed
    for all other tools. Use this first to find the correct scheme code.

    Args:
        params (SearchFundInput):
            - query (str): Fund name or keyword (e.g. 'Axis Midcap', 'SBI Bluechip')
            - limit (int): Max results (default 10)

    Returns:
        str: Markdown or JSON list of matching funds with scheme codes and names.
    """
    try:
        results = await _get(MFAPI_SEARCH_BASE, params={"q": params.query})
        if not results:
            return f"No funds found for query: '{params.query}'. Try a broader search term."

        funds = results[: params.limit]
        lines = [f"## Mutual Fund Search Results for '{params.query}'\n",
                 f"Found {len(results)} funds. Showing top {len(funds)}:\n",
                 "| # | Scheme Code | Fund Name |",
                 "|---|-------------|-----------|"]
        for i, f in enumerate(funds, 1):
            lines.append(f"| {i} | `{f['schemeCode']}` | {f['schemeName']} |")
        lines.append("\n> Use the **Scheme Code** with `mf_analyze_fund` to get a full analysis.")
        return "\n".join(lines)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="mf_analyze_fund",
    annotations={
        "title": "Analyze Mutual Fund Performance",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mf_analyze_fund(params: AnalyzeFundInput) -> str:
    """Full performance analysis of a mutual fund vs its auto-detected benchmark.

    Computes returns across periods, volatility, Sharpe ratio, beta, alpha,
    max drawdown, rolling returns, and delivers a clear verdict on whether
    the fund is outperforming, at par, or underperforming its benchmark.

    Args:
        params (AnalyzeFundInput):
            - scheme_code (int): MFAPI scheme code
            - period_years (float): Analysis window in years (default 3)
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Full analysis report with benchmark comparison and verdict.
             Schema (JSON): {
                 fund_info: {name, scheme_code, category, benchmark_name},
                 returns: {fund: {1M,3M,6M,1Y,3Y,5Y}, benchmark: {1M,3M,6M,1Y,3Y,5Y}},
                 metrics: {volatility_pct, sharpe_ratio, beta, alpha_pct, max_drawdown_pct},
                 rolling_1y: {min, max, avg, pct_positive, observations},
                 verdicts: {1Y, 3Y, 5Y},
                 overall_verdict: str
             }
    """
    try:
        # ── Fetch fund data ──────────────────────────────────────
        fund_data = await _get(f"{MFAPI_BASE}/{params.scheme_code}")
        meta      = fund_data.get("meta", {})
        nav_data  = fund_data.get("data", [])
        fund_name = meta.get("scheme_name", f"Scheme {params.scheme_code}")
        category  = meta.get("scheme_category", "")

        fund_series = _parse_nav_series(nav_data)
        if len(fund_series) < 30:
            return "Error: Insufficient NAV history for this fund (need at least 30 data points)."

        # ── Auto-detect benchmark ─────────────────────────────────
        bench_name, bench_code = _detect_benchmark(fund_name, category)

        # ── Fetch benchmark data ──────────────────────────────────
        try:
            bench_data   = await _get(f"{MFAPI_BASE}/{bench_code}")
            bench_series = _parse_nav_series(bench_data.get("data", []))
        except Exception:
            bench_series = []

        # ── Compute metrics ───────────────────────────────────────
        fund_returns  = _compute_returns(fund_series)
        bench_returns = _compute_returns(bench_series) if bench_series else {}

        volatility = _compute_volatility(fund_series)
        max_dd     = _compute_max_drawdown(fund_series, params.period_years)
        sharpe     = _compute_sharpe(fund_returns.get("1Y"), volatility)
        beta, alpha = _compute_beta_alpha(fund_series, bench_series, params.period_years) if bench_series else (None, None)
        rolling_1y = _rolling_returns(fund_series, 1.0)

        verdicts = {
            "1Y": _verdict(fund_returns.get("1Y"), bench_returns.get("1Y"), "1Y"),
            "3Y": _verdict(fund_returns.get("3Y"), bench_returns.get("3Y"), "3Y"),
            "5Y": _verdict(fund_returns.get("5Y"), bench_returns.get("5Y"), "5Y"),
        }

        # Overall verdict based on 3Y (primary), fallback to 1Y
        primary_key     = "3Y" if fund_returns.get("3Y") is not None else "1Y"
        overall_verdict = verdicts[primary_key]

        # ── Format response ───────────────────────────────────────
        if params.response_format == ResponseFormat.JSON:
            return json.dumps({
                "fund_info": {
                    "name":           fund_name,
                    "scheme_code":    params.scheme_code,
                    "category":       category,
                    "benchmark_name": bench_name,
                    "benchmark_code": bench_code,
                },
                "returns": {
                    "fund":      fund_returns,
                    "benchmark": bench_returns,
                },
                "metrics": {
                    "volatility_pct":  volatility,
                    "sharpe_ratio":    sharpe,
                    "beta":            beta,
                    "alpha_pct":       alpha,
                    "max_drawdown_pct": max_dd,
                },
                "rolling_1y":     rolling_1y,
                "verdicts":       verdicts,
                "overall_verdict": overall_verdict,
            }, indent=2)

        # Markdown report
        def _fmt(v): return f"{v:.2f}%" if v is not None else "N/A"
        def _diff(f, b):
            if f is None or b is None: return ""
            d = f - b
            return f" ({d:+.2f}% vs benchmark)"

        lines = [
            f"# 📊 Mutual Fund Analysis Report",
            f"",
            f"## {fund_name}",
            f"**Category:** {category or 'N/A'}  |  **Scheme Code:** `{params.scheme_code}`",
            f"**Auto-detected Benchmark:** {bench_name}",
            f"",
            f"---",
            f"",
            f"## 📈 Returns Comparison",
            f"",
            f"| Period | Fund | Benchmark | Difference |",
            f"|--------|------|-----------|------------|",
        ]
        for period in ["1M", "3M", "6M", "1Y", "3Y", "5Y"]:
            fr = fund_returns.get(period)
            br = bench_returns.get(period)
            diff = f"{(fr - br):+.2f}%" if fr is not None and br is not None else "N/A"
            lines.append(f"| {period} | {_fmt(fr)} | {_fmt(br)} | {diff} |")

        lines += [
            f"",
            f"---",
            f"",
            f"## 🔢 Risk & Quality Metrics",
            f"",
            f"| Metric | Value | What it means |",
            f"|--------|-------|---------------|",
            f"| Volatility (Ann.) | {_fmt(volatility)} | Annualised std dev of daily returns |",
            f"| Sharpe Ratio | {f'{sharpe:.2f}' if sharpe is not None else 'N/A'} | Return per unit of risk (>1 = good) |",
            f"| Beta | {f'{beta:.2f}' if beta is not None else 'N/A'} | Sensitivity to benchmark moves (<1 = less volatile) |",
            f"| Alpha (Ann.) | {_fmt(alpha)} | Excess return over benchmark after risk adjustment |",
            f"| Max Drawdown | {_fmt(max_dd)} | Worst peak-to-trough fall in the period |",
        ]

        if rolling_1y:
            lines += [
                f"",
                f"---",
                f"",
                f"## 🔄 Rolling 1-Year Returns",
                f"",
                f"| Min | Max | Average | % Positive Periods |",
                f"|-----|-----|---------|---------------------|",
                f"| {_fmt(rolling_1y.get('min'))} | {_fmt(rolling_1y.get('max'))} | {_fmt(rolling_1y.get('avg'))} | {rolling_1y.get('pct_positive', 'N/A')}% |",
                f"*(Based on {rolling_1y.get('observations', 0)} rolling windows)*",
            ]

        lines += [
            f"",
            f"---",
            f"",
            f"## ⚖️ Verdict by Period",
            f"",
            f"| Period | Verdict |",
            f"|--------|---------|",
            f"| 1 Year | {verdicts['1Y']} |",
            f"| 3 Year | {verdicts['3Y']} |",
            f"| 5 Year | {verdicts['5Y']} |",
            f"",
            f"---",
            f"",
            f"## 🏁 Overall Verdict (based on {primary_key})",
            f"",
            f"> ### {overall_verdict}",
            f"",
            f"---",
            f"*Data sourced from MFAPI.in. Returns <1Y are absolute; ≥1Y are CAGR.*",
            f"*Alpha/Beta computed over {params.period_years:.1f}-year window using monthly returns.*",
        ]

        return "\n".join(lines)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="mf_compare_periods",
    annotations={
        "title": "Compare Fund Returns Across All Periods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mf_compare_periods(params: ComparePeriodsInput) -> str:
    """Compare a mutual fund's returns across 1M, 3M, 6M, 1Y, 3Y, and 5Y periods
    against its auto-detected benchmark. Good for quick snapshot comparisons.

    Args:
        params (ComparePeriodsInput):
            - scheme_code (int): MFAPI scheme code
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Returns table with fund vs benchmark for all standard periods.
    """
    try:
        fund_data   = await _get(f"{MFAPI_BASE}/{params.scheme_code}")
        meta        = fund_data.get("meta", {})
        nav_data    = fund_data.get("data", [])
        fund_name   = meta.get("scheme_name", f"Scheme {params.scheme_code}")
        category    = meta.get("scheme_category", "")
        fund_series = _parse_nav_series(nav_data)

        bench_name, bench_code = _detect_benchmark(fund_name, category)

        try:
            bench_data   = await _get(f"{MFAPI_BASE}/{bench_code}")
            bench_series = _parse_nav_series(bench_data.get("data", []))
        except Exception:
            bench_series = []

        fund_returns  = _compute_returns(fund_series)
        bench_returns = _compute_returns(bench_series) if bench_series else {}

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({
                "fund_name":      fund_name,
                "benchmark_name": bench_name,
                "fund_returns":   fund_returns,
                "bench_returns":  bench_returns,
            }, indent=2)

        def _fmt(v): return f"{v:.2f}%" if v is not None else "N/A"

        lines = [
            f"## 📅 Returns Snapshot — {fund_name}",
            f"**Benchmark:** {bench_name}\n",
            f"| Period | Fund | Benchmark | Outperform? |",
            f"|--------|------|-----------|-------------|",
        ]
        for period in ["1M", "3M", "6M", "1Y", "3Y", "5Y"]:
            fr = fund_returns.get(period)
            br = bench_returns.get(period)
            flag = ""
            if fr is not None and br is not None:
                d = fr - br
                flag = "✅" if d > 2 else ("🔴" if d < -2 else "🟡")
            lines.append(f"| {period} | {_fmt(fr)} | {_fmt(br)} | {flag} |")

        return "\n".join(lines)

    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="mf_get_nav_history",
    annotations={
        "title": "Get NAV History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mf_get_nav_history(params: GetNavHistoryInput) -> str:
    """Retrieve recent NAV (Net Asset Value) history for a mutual fund.

    Useful for plotting or inspecting raw price movement.

    Args:
        params (GetNavHistoryInput):
            - scheme_code (int): MFAPI scheme code
            - days (int): Number of past days to return (default 365, max 1825)

    Returns:
        str: JSON list of {date, nav} entries sorted newest first.
    """
    try:
        fund_data   = await _get(f"{MFAPI_BASE}/{params.scheme_code}")
        meta        = fund_data.get("meta", {})
        nav_data    = fund_data.get("data", [])
        fund_name   = meta.get("scheme_name", f"Scheme {params.scheme_code}")
        fund_series = _parse_nav_series(nav_data)

        cutoff  = datetime.now() - timedelta(days=params.days)
        history = [
            {"date": dt.strftime("%d-%m-%Y"), "nav": nav}
            for dt, nav in fund_series if dt >= cutoff
        ]

        return json.dumps({
            "fund_name":   fund_name,
            "scheme_code": params.scheme_code,
            "days":        params.days,
            "count":       len(history),
            "nav_history": history,
        }, indent=2)

    except Exception as e:
        return _handle_error(e)


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()