# 🇮🇳 Mutual Fund MCP Server

An MCP server for analyzing Indian mutual funds — fetches live data from [MFAPI.in](https://mfapi.in), auto-detects the right benchmark, and delivers a full performance verdict.

**No API key needed. Completely free.**

---

## 📦 Installation

```bash
# 1. Clone / copy this folder, then install dependencies
pip install -r requirements.txt

# 2. Verify it runs
python server.py
```

---

## 🔌 Connect to Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mutualfund_mcp": {
      "command": "python",
      "args": ["/absolute/path/to/mutualfund_mcp/server.py"]
    }
  }
}
```

> Replace `/absolute/path/to/` with the actual path on your machine.

Config file location:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

---

## 🛠️ Tools Available

### 1. `mf_search_funds`
Search for funds by name or keyword. Returns scheme codes needed for other tools.

**Example prompt:** *"Search for Axis Midcap fund"*

---

### 2. `mf_analyze_fund`
Full analysis report for a fund including:
- Returns vs benchmark (1M, 3M, 6M, 1Y, 3Y, 5Y)
- Volatility, Sharpe Ratio, Beta, Alpha
- Max Drawdown
- Rolling 1Y returns (min/max/avg/% positive)
- Verdict: ✅ Outperforming / 🟡 At Par / 🔴 Underperforming

**Example prompt:** *"Analyze scheme code 120503 over 3 years"*

---

### 3. `mf_compare_periods`
Quick returns snapshot across all periods vs benchmark.

**Example prompt:** *"Compare returns for scheme 120503 across all periods"*

---

### 4. `mf_get_nav_history`
Raw NAV history for the last N days.

**Example prompt:** *"Get NAV history for scheme 120503 for the last 180 days"*

---

## 📐 Benchmark Auto-Detection

The server automatically picks the right benchmark based on the fund name and category:

| Fund Type | Benchmark |
|-----------|-----------|
| Large Cap | Nifty 50 |
| Mid Cap | Nifty Midcap 150 |
| Small Cap | Nifty Smallcap 250 |
| Flexi/Multi Cap | Nifty 500 |
| ELSS / Tax Saver | Nifty 500 |
| Index / ETF | Nifty 50 |
| Hybrid / Balanced | Nifty 50 Hybrid 65:35 |
| Debt / Liquid | Crisil Short Term / Liquid |
| Gilt | Crisil 10 Year Gilt |

---

## 📊 Metrics Explained

| Metric | Description |
|--------|-------------|
| **Returns** | Absolute for <1Y, CAGR for ≥1Y |
| **Alpha** | Annualised excess return over benchmark after risk adjustment |
| **Beta** | Sensitivity to benchmark; <1 = less volatile than market |
| **Sharpe Ratio** | Return per unit of risk; >1 = good, >2 = excellent |
| **Volatility** | Annualised standard deviation of daily returns |
| **Max Drawdown** | Worst peak-to-trough fall in the selected period |
| **Rolling Returns** | Min/Max/Avg/% positive rolling 1Y windows |

---

## 🔄 Verdict Logic

| Difference vs Benchmark | Verdict |
|--------------------------|---------|
| Fund > Benchmark by >2% | ✅ Outperforming |
| Within ±2% | 🟡 At Par |
| Fund < Benchmark by >2% | 🔴 Underperforming |

---

## 📝 Sample Conversation

> **You:** Search for SBI Bluechip fund
>
> **Claude:** *(calls mf_search_funds)* Found SBI Bluechip Fund — scheme code `119598`
>
> **You:** Analyze it for 5 years
>
> **Claude:** *(calls mf_analyze_fund)* Full report with returns, Sharpe, Beta, Alpha vs Nifty 50 and final verdict.

---

*Data sourced from [mfapi.in](https://mfapi.in) — free, no auth required.*
