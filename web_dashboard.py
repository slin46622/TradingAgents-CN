"""TradingAgents Web Dashboard — zero dependencies, pure stdlib.

Usage:
    python web_dashboard.py

Then open http://localhost:8080 in your browser.
"""

import json
import os
import sys
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "."))

# Will be lazily imported on first request
TA = None
CONFIG = None


def _get_ta():
    global TA
    if TA is not None:
        return TA
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = DEFAULT_CONFIG.copy()
    provider = os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "deepseek")
    config.update({
        "llm_provider": provider,
        "backend_url": os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL", "https://api.deepseek.com"),
        "quick_think_llm": os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "deepseek-v4-flash"),
        "deep_think_llm": os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "deepseek-v4-flash"),
        "online_tools": False,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "enable_memory": False,
    })
    TA = TradingAgentsGraph(debug=False, config=config)
    return TA


_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TradingAgents Dashboard</title>
<style>
  :root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --accent: #58a6ff; --green: #3fb950; --red: #f85149; --yellow: #d29922; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background: var(--bg); color: var(--text); padding: 20px; }
  .container { max-width: 960px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin-bottom: 20px; display: flex; align-items: center; gap: 8px; }
  h1 .status { font-size: 0.7rem; color: var(--green); }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
  .card h2 { font-size: 1rem; margin-bottom: 12px; color: var(--accent); }
  .row { display: flex; gap: 12px; flex-wrap: wrap; }
  .row label { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 150px; }
  input, select, button { background: #21262d; border: 1px solid var(--border); color: var(--text); padding: 8px 12px; border-radius: 6px; font-size: 0.9rem; }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  button { background: #238636; border-color: rgba(240,246,252,0.1); cursor: pointer; font-weight: 600; }
  button:hover { background: #2ea043; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.danger { background: #da3633; } button.danger:hover { background: #f85149; }
  .result { white-space: pre-wrap; font-family: 'JetBrains Mono','Cascadia Code',monospace; font-size: 0.8rem; line-height: 1.5; max-height: 400px; overflow-y: auto; background: #0d1117; padding: 12px; border-radius: 6px; }
  .result .label { color: var(--accent); font-weight: 600; }
  .result .rating-buy { color: var(--green); } .result .rating-sell { color: var(--red); } .result .rating-hold { color: var(--yellow); }
  .tab-bar { display: flex; gap: 0; margin-bottom: 16px; }
  .tab { padding: 8px 16px; border: 1px solid var(--border); background: var(--card); cursor: pointer; }
  .tab:first-child { border-radius: 6px 0 0 6px; } .tab:last-child { border-radius: 0 6px 6px 0; }
  .tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .hidden { display: none; }
  #spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin .8s linear infinite; margin-left: 8px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .meta { font-size: 0.8rem; color: #8b949e; margin-top: 8px; }
  .checkpoint-item { padding: 8px; border-bottom: 1px solid var(--border); cursor: pointer; }
  .checkpoint-item:hover { background: #21262d; }
  .checkpoint-item small { color: #8b949e; }
</style>
</head>
<body>
<div class="container">
  <h1>📈 TradingAgents <span class="status">● online</span></h1>

  <div class="tab-bar">
    <div class="tab active" onclick="switchTab('run')">▶ 运行分析</div>
    <div class="tab" onclick="switchTab('checkpoints')">💾 Checkpoints</div>
    <div class="tab" onclick="switchTab('log')">📋 决策日志</div>
  </div>

  <!-- Run Analysis -->
  <div id="tab-run">
    <div class="card">
      <h2>运行分析</h2>
      <div class="row">
        <label>标的
          <input id="ticker" value="AAPL" placeholder="AAPL / 600519 / BTC-USD">
        </label>
        <label>交易日期
          <input id="trade-date" type="date" value="2024-05-10">
        </label>
        <label>Provider
          <select id="provider">
            <option value="deepseek">DeepSeek</option>
            <option value="local">Local (Qwen 35B)</option>
          </select>
        </label>
        <label style="flex:0;min-width:auto;align-self:flex-end">
          <button id="run-btn" onclick="runAnalysis()">▶ 运行</button>
        </label>
      </div>
    </div>

    <div id="loading" class="card hidden">
      <h2>分析中... <span id="spinner"></span></h2>
      <div id="progress" style="font-size:0.85rem;color:#8b949e;">Initializing...</div>
    </div>

    <div id="result-card" class="card hidden">
      <h2>决策结果</h2>
      <div id="decision-summary" style="margin-bottom:12px;"></div>
      <div id="decision-detail" class="result"></div>
      <div class="meta" id="result-meta"></div>
    </div>

    <div id="error-card" class="card hidden" style="border-color:var(--red);">
      <h2 style="color:var(--red);">❌ 分析失败</h2>
      <pre id="error-detail" style="white-space:pre-wrap;font-size:0.85rem;"></pre>
    </div>
  </div>

  <!-- Checkpoints -->
  <div id="tab-checkpoints" class="hidden">
    <div class="card">
      <h2>已保存的 Checkpoints <button class="danger" onclick="cleanCheckpoints()" style="float:right;font-size:0.8rem;">清理过期</button></h2>
      <div id="checkpoint-list"><p style="color:#8b949e;">加载中...</p></div>
    </div>
  </div>

  <!-- Log -->
  <div id="tab-log" class="hidden">
    <div class="card">
      <h2>决策日志 <span style="font-weight:normal;font-size:0.8rem;color:#8b949e;">(~/.tradingagents/memory/trading_memory.md)</span></h2>
      <div id="log-content"><p style="color:#8b949e;">暂无记录</p></div>
    </div>
  </div>
</div>

<script>
let polling = false;

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('[id^="tab-"]').forEach(t => t.classList.add('hidden'));
  document.querySelector(`.tab[onclick*="'${name}'"]`).classList.add('active');
  document.getElementById(`tab-${name}`).classList.remove('hidden');
  if (name === 'checkpoints') loadCheckpoints();
  if (name === 'log') loadLog();
}

async function runAnalysis() {
  const ticker = document.getElementById('ticker').value.trim();
  const date = document.getElementById('trade-date').value;
  const provider = document.getElementById('provider').value;
  if (!ticker) return alert('请输入标的代码');

  document.getElementById('run-btn').disabled = true;
  document.getElementById('loading').classList.remove('hidden');
  document.getElementById('result-card').classList.add('hidden');
  document.getElementById('error-card').classList.add('hidden');
  document.getElementById('progress').textContent = `正在分析 ${ticker} (${date})...`;

  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, trade_date: date, provider }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || '请求失败');

    document.getElementById('loading').classList.add('hidden');
    document.getElementById('result-card').classList.remove('hidden');

    // Decision summary
    const d = data.decision || {};
    let summaryHtml = '<div class="row" style="gap:16px;">';
    const actionClass = d.action === '买入' || d.action === 'Buy' ? 'rating-buy' :
                        d.action === '卖出' || d.action === 'Sell' ? 'rating-sell' : 'rating-hold';
    summaryHtml += `<div><strong>建议</strong><br><span class="${actionClass}" style="font-size:1.5rem;">${d.action || '?'}</span></div>`;
    summaryHtml += `<div><strong>置信度</strong><br>${d.confidence || '?'}</div>`;
    summaryHtml += `<div><strong>风险评分</strong><br>${d.risk_score || '?'}</div>`;
    if (d.target_price) summaryHtml += `<div><strong>目标价</strong><br>${d.target_price}</div>`;
    summaryHtml += '</div>';
    document.getElementById('decision-summary').innerHTML = summaryHtml;

    // Full decision detail
    const detail = data.final_trade_decision || '(无详细输出)';
    document.getElementById('decision-detail').textContent = detail;

    // Meta
    document.getElementById('result-meta').textContent =
      `Total time: ${data.elapsed || '?'}s  |  Model: ${d.model_info || '?'}  |  Checkpoint: ${data.checkpoint || 'N/A'}`;

  } catch (err) {
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('error-card').classList.remove('hidden');
    document.getElementById('error-detail').textContent = err.message;
  } finally {
    document.getElementById('run-btn').disabled = false;
  }
}

async function loadCheckpoints() {
  const el = document.getElementById('checkpoint-list');
  try {
    const resp = await fetch('/api/checkpoints');
    const data = await resp.json();
    const cps = data.checkpoints || [];
    if (cps.length === 0) {
      el.innerHTML = '<p style="color:#8b949e;">暂无 checkpoints</p>';
      return;
    }
    el.innerHTML = cps.map(cp =>
      `<div class="checkpoint-item" onclick="runCheckpoint('${cp.ticker}','${cp.run_id}')">
        <strong>${cp.ticker}</strong> — ${cp.phase} <small>${cp.timestamp}</small>
        <br><small>Run: ${cp.run_id}</small>
      </div>`
    ).join('');
  } catch (err) {
    el.innerHTML = `<p style="color:var(--red);">加载失败: ${err.message}</p>`;
  }
}

async function runCheckpoint(ticker, runId) {
  document.getElementById('ticker').value = ticker;
  switchTab('run');
  // Future: resume_from support
}

async function cleanCheckpoints() {
  await fetch('/api/checkpoints/clean', { method: 'POST' });
  loadCheckpoints();
}

async function loadLog() {
  const el = document.getElementById('log-content');
  try {
    const resp = await fetch('/api/log');
    const data = await resp.json();
    if (data.content) {
      el.innerHTML = `<div class="result">${escapeHtml(data.content)}</div>`;
    } else {
      el.innerHTML = '<p style="color:#8b949e;">暂无记录</p>';
    }
  } catch (err) {
    el.innerHTML = `<p style="color:var(--red);">加载失败: ${err.message}</p>`;
  }
}

function escapeHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_html(200, _HTML)
        elif path == "/api/checkpoints":
            self._send_json(self._list_checkpoints())
        elif path == "/api/log":
            self._send_json(self._load_log())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/analyze":
            self._handle_analyze()
        elif path == "/api/checkpoints/clean":
            self._clean_checkpoints()
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_analyze(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        ticker = body.get("ticker", "AAPL")
        trade_date = body.get("trade_date", "2024-05-10")
        provider = body.get("provider", "deepseek")

        # Set env vars for provider
        if provider == "local":
            os.environ["TRADINGAGENTS_LLM_PROVIDER"] = "local"
            os.environ["TRADINGAGENTS_LLM_BACKEND_URL"] = "http://172.27.208.1:11434/v1"
            os.environ["TRADINGAGENTS_QUICK_THINK_LLM"] = "Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M.gguf"
            os.environ["TRADINGAGENTS_DEEP_THINK_LLM"] = "Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M.gguf"
            os.environ["LOCAL_API_KEY"] = "sk-no-key-needed"
        else:
            os.environ["TRADINGAGENTS_LLM_PROVIDER"] = "deepseek"
            os.environ["TRADINGAGENTS_QUICK_THINK_LLM"] = "deepseek-v4-flash"
            os.environ["TRADINGAGENTS_DEEP_THINK_LLM"] = "deepseek-v4-flash"

        try:
            global TA
            TA = None  # Re-initialize with new config
            ta = _get_ta()
            start = time.time()
            final_state, decision = ta.propagate(ticker, trade_date)
            elapsed = round(time.time() - start, 1)

            fd = final_state.get("final_trade_decision", "")
            self._send_json({
                "decision": {
                    k: str(v) if not isinstance(v, (str, int, float, type(None))) else v
                    for k, v in decision.items()
                },
                "final_trade_decision": fd[:3000] if isinstance(fd, str) else str(fd),
                "elapsed": elapsed,
                "checkpoint": f"{ticker}/{ta.run_id}",
            })
        except Exception as e:
            self._send_json({"error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"}, 500)

    def _list_checkpoints(self):
        try:
            from tradingagents.graph.checkpointer import list_checkpoints
            return {"checkpoints": list_checkpoints()}
        except Exception as e:
            return {"checkpoints": [], "error": str(e)}

    def _clean_checkpoints(self):
        try:
            from tradingagents.graph.checkpointer import clean_old_checkpoints
            clean_old_checkpoints(30)
        except Exception:
            pass

    def _load_log(self):
        log_path = os.path.expanduser("~/.tradingagents/memory/trading_memory.md")
        if os.path.exists(log_path):
            with open(log_path) as f:
                return {"content": f.read()[-5000:]}
        return {"content": ""}

    def _send_html(self, status, html):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}")


def main():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"\n  📈 TradingAgents Dashboard")
    print(f"  ────────────────────────────")
    print(f"  Open: http://localhost:{port}")
    print(f"  Quit: Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
