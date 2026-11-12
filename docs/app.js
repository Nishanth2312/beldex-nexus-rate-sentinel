/**
 * Beldex Nexus Rate Sentinel Frontend Application Logic
 * Supports Dual Mode:
 *   1. Full-Stack Mode: Connects to local FastAPI backend via WebSocket & REST.
 *   2. GitHub Pages / Static Mode: Directly tracks live Gate.io & CoinGecko feeds in browser,
 *      maintains rolling 30m localStorage history, triggers audio chimes, dispatches browser-based
 *      Telegram/Discord alerts, and runs the interactive surge simulator!
 */

// State
let ws = null;
let priceChart = null;
let currentCurrency = 'USD'; // 'USD' or 'INR'
let activeTimeframe = 1; // hours
let audioAlertEnabled = true;
let chartHistoryData = [];
let latestAnalysis = null;
let latestNexusState = {
  rate_usd: null,
  rate_inr: null,
  status: 'awaiting_sync',
  status_detail: 'Ready for connection or Companion sync',
  last_sync_timestamp: 0,
  sync_method: 'none',
  premium_pct_vs_market: null
};
let audioContext = null;

// Detect static GitHub Pages environment
const isStaticHosted = window.location.hostname.includes('github.io') || 
                       window.location.protocol === 'file:' || 
                       !window.location.port;

let clientSettings = {
  surge_threshold_percent_30m: 3.0,
  cooldown_minutes: 15,
  target_sell_price_usd: 0.0,
  target_sell_price_inr: 0.0,
  user_holdings_bdx: 10000.0,
  telegram_enabled: false,
  telegram_bot_token: '',
  telegram_chat_id: '',
  email_enabled: false,
  email_to: '',
  sms_enabled: false,
  sms_to: '',
  discord_enabled: false,
  discord_webhook_url: '',
  last_alert_time: 0
};

// Initialize Web Audio Context for Chimes
function initAudio() {
  if (!audioContext) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) {
      audioContext = new AudioCtx();
    }
  }
}

// Play trading alert chime using Web Audio API
function playAlertChime() {
  if (!audioAlertEnabled) return;
  try {
    initAudio();
    if (!audioContext) return;
    if (audioContext.state === 'suspended') {
      audioContext.resume();
    }
    const now = audioContext.currentTime;

    // Dual-tone synthesizer alert
    const osc1 = audioContext.createOscillator();
    const osc2 = audioContext.createOscillator();
    const gainNode = audioContext.createGain();

    osc1.type = 'sine';
    osc1.frequency.setValueAtTime(587.33, now); // D5
    osc1.frequency.exponentialRampToValueAtTime(880.00, now + 0.15); // A5

    osc2.type = 'triangle';
    osc2.frequency.setValueAtTime(880.00, now + 0.15);
    osc2.frequency.exponentialRampToValueAtTime(1174.66, now + 0.35); // D6

    gainNode.gain.setValueAtTime(0.01, now);
    gainNode.gain.linearRampToValueAtTime(0.3, now + 0.05);
    gainNode.gain.exponentialRampToValueAtTime(0.001, now + 0.5);

    osc1.connect(gainNode);
    osc2.connect(gainNode);
    gainNode.connect(audioContext.destination);

    osc1.start(now);
    osc2.start(now + 0.15);
    osc1.stop(now + 0.2);
    osc2.stop(now + 0.5);
  } catch (e) {
    console.warn("Audio chime error:", e);
  }
}

// Toast Notification
function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  const bg = type === 'success' ? 'bg-emerald-600' : (type === 'error' ? 'bg-rose-600' : 'bg-blue-600');
  toast.className = `fixed bottom-4 right-4 ${bg} text-white px-4 py-3 rounded-lg shadow-xl text-xs font-semibold z-50 transition-all duration-300 transform translate-y-4 opacity-0`;
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.classList.remove('translate-y-4', 'opacity-0');
  }, 10);

  setTimeout(() => {
    toast.classList.add('translate-y-4', 'opacity-0');
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// Format numbers
function formatPrice(val, decimals = 4) {
  if (val === null || val === undefined || isNaN(val)) return '--';
  return parseFloat(val).toFixed(decimals);
}

// Format time
function formatTime(epochSec) {
  if (!epochSec) return 'Never';
  const d = new Date(epochSec * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// UI Updaters
function updateHeroDisplay(analysis) {
  if (!analysis) return;
  latestAnalysis = analysis;

  const curUsd = analysis.current_price_usd;
  const curInr = analysis.current_price_inr;
  const pastUsd = analysis.price_30m_usd;
  const pastInr = analysis.price_30m_inr;
  const diffPct = analysis.diff_30m_pct;
  const diffUsd = analysis.diff_30m_usd;
  const diffInr = analysis.diff_30m_inr;

  document.getElementById('heroCurUsd').textContent = '$' + formatPrice(curUsd, 4);
  document.getElementById('heroCurInr').textContent = '₹' + formatPrice(curInr, 2);

  if (pastUsd !== null && pastUsd !== undefined) {
    document.getElementById('heroPastUsd').textContent = '$' + formatPrice(pastUsd, 4);
    document.getElementById('heroPastInr').textContent = '₹' + formatPrice(pastInr, 2);
  }

  // 30m Diff formatting
  const diffElem = document.getElementById('diff30mPercent');
  const indElem = document.getElementById('diff30mIndicator');
  const diffUsdElem = document.getElementById('diff30mUsd');
  const diffInrElem = document.getElementById('diff30mInr');

  if (diffPct !== null && diffPct !== undefined) {
    const isPositive = diffPct >= 0;
    const sign = isPositive ? '+' : '';
    diffElem.textContent = `${sign}${diffPct.toFixed(2)}%`;
    diffElem.className = `text-4xl md:text-5xl font-black font-mono ${isPositive ? 'rate-surge-positive' : 'rate-surge-negative'}`;

    indElem.innerHTML = isPositive
      ? '<i class="fa-solid fa-arrow-trend-up text-emerald-400"></i>'
      : '<i class="fa-solid fa-arrow-trend-down text-rose-400"></i>';

    diffUsdElem.textContent = `${sign}$${Math.abs(diffUsd || 0).toFixed(4)}`;
    diffInrElem.textContent = `${sign}₹${Math.abs(diffInr || 0).toFixed(2)}`;

    // Signal banner
    const banner = document.getElementById('signalBanner');
    const signalTitle = document.getElementById('signalTitle');
    const signalDesc = document.getElementById('signalDesc');
    const glow = document.getElementById('ambientGlow');

    if (analysis.is_high_demand_surge || analysis.is_target_sell_reached) {
      playAlertChime();
      banner.className = "mt-4 p-3 rounded-lg bg-emerald-950/60 border-2 border-emerald-400 flex items-center gap-3 shadow-lg shadow-emerald-500/20";
      signalTitle.className = "text-xs font-black text-emerald-300 uppercase tracking-wide";
      signalTitle.textContent = "🚨 " + (analysis.recommendation || "STRONG SELL OPPORTUNITY");
      signalDesc.textContent = (analysis.alert_reasons && analysis.alert_reasons.length) 
        ? analysis.alert_reasons.join(" • ") 
        : `30-Minute Surge +${diffPct.toFixed(2)}% detected!`;
      glow.className = "absolute -right-20 -top-20 w-80 h-80 rounded-full bg-emerald-500/30 blur-3xl pointer-events-none";
    } else {
      banner.className = "mt-4 p-3 rounded-lg bg-emerald-950/40 border border-emerald-500/30 flex items-center gap-3";
      signalTitle.className = "text-xs font-bold text-emerald-400 uppercase tracking-wide";
      signalTitle.textContent = analysis.recommendation || "HOLD / MONITOR";
      signalDesc.textContent = `30m Diff is ${sign}${diffPct.toFixed(2)}%. Sentinel will alert you when spike meets threshold.`;
      glow.className = "absolute -right-20 -top-20 w-80 h-80 rounded-full bg-emerald-500/10 blur-3xl pointer-events-none";
    }
  }

  // Portfolio
  const holdings = analysis.holdings_bdx || clientSettings.user_holdings_bdx || 10000;
  document.getElementById('heroHoldingsAmount').textContent = `${holdings.toLocaleString()} BDX`;
  document.getElementById('heroPortUsd').textContent = `$${(curUsd * holdings).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  document.getElementById('heroPortInr').textContent = `₹${(curInr * holdings).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

  if (analysis.profit_gain_30m_inr !== null && analysis.profit_gain_30m_inr !== undefined) {
    const pSign = analysis.profit_gain_30m_inr >= 0 ? '+' : '';
    document.getElementById('heroPortGain').textContent = `${pSign}₹${analysis.profit_gain_30m_inr.toFixed(0)} in 30m`;
  }

  // Multi-Timeframe Matrix
  updateMatrixCell('5m', analysis.diff_5m);
  updateMatrixCell('15m', analysis.diff_15m);
  updateMatrixCell('30m', { diff_pct: analysis.diff_30m_pct, diff_usd: analysis.diff_30m_usd });
  updateMatrixCell('1h', analysis.diff_1h);
  updateMatrixCell('24h', analysis.diff_24h);

  document.getElementById('lastTickTime').textContent = formatTime(analysis.timestamp);
  updateSellingLadder(curUsd, curInr);
}

function updateMatrixCell(windowKey, diffObj) {
  const pctElem = document.getElementById(`matrix${windowKey}Pct`);
  const usdElem = document.getElementById(`matrix${windowKey}Usd`);
  if (!pctElem || !usdElem) return;

  if (diffObj && diffObj.diff_pct !== null && diffObj.diff_pct !== undefined) {
    const isPos = diffObj.diff_pct >= 0;
    const sign = isPos ? '+' : '';
    pctElem.textContent = `${sign}${diffObj.diff_pct.toFixed(2)}%`;
    pctElem.className = `text-xl md:text-2xl font-bold font-mono my-1 ${isPos ? 'text-emerald-400' : 'text-rose-400'}`;
    usdElem.textContent = `${sign}$${Math.abs(diffObj.diff_usd || 0).toFixed(4)}`;
  } else {
    pctElem.textContent = '--';
    usdElem.textContent = 'Collecting...';
  }
}

function updateNexusDisplay(nexus) {
  if (!nexus) return;
  latestNexusState = nexus;

  const badge = document.getElementById('nexusBadge');
  const rateDisp = document.getElementById('nexusRateDisplay');
  const inrDisp = document.getElementById('nexusInrDisplay');
  const arbDisp = document.getElementById('nexusArbitrageDisplay');
  const methodDisp = document.getElementById('nexusSyncMethod');
  const lastSyncDisp = document.getElementById('nexusLastSync');

  if (nexus.status === 'synced_via_companion') {
    badge.className = 'badge badge-green text-[10px]';
    badge.textContent = 'Synced via Companion';
  } else if (nexus.status === 'manual_override') {
    badge.className = 'badge badge-blue text-[10px]';
    badge.textContent = 'Manual Rate';
  } else if (nexus.status === 'connected_direct') {
    badge.className = 'badge badge-green text-[10px]';
    badge.textContent = 'Connected Direct';
  } else {
    badge.className = 'badge badge-amber text-[10px]';
    badge.textContent = 'Ready for Sync';
  }

  if (nexus.rate_usd) {
    rateDisp.textContent = '$' + formatPrice(nexus.rate_usd, 4);
    inrDisp.textContent = '₹' + formatPrice(nexus.rate_inr, 2);
  } else {
    rateDisp.textContent = 'Ready';
    inrDisp.textContent = 'Use Companion';
  }

  if (nexus.premium_pct_vs_market !== null && nexus.premium_pct_vs_market !== undefined) {
    const isHigh = nexus.premium_pct_vs_market >= 0;
    arbDisp.textContent = `${isHigh ? '+' : ''}${nexus.premium_pct_vs_market.toFixed(2)}% Premium`;
    arbDisp.className = `font-mono font-bold ${isHigh ? 'text-emerald-400' : 'text-rose-400'}`;
  }

  methodDisp.textContent = nexus.sync_method ? nexus.sync_method.toUpperCase() : 'COMPANION';
  lastSyncDisp.textContent = formatTime(nexus.last_sync_timestamp);
}

function updateSellingLadder(curUsd, curInr) {
  const inputElem = document.getElementById('calcHoldingsInput');
  const holdings = parseFloat(inputElem.value) || 10000;

  const getTierStr = (mult) => {
    const targetUsd = curUsd * mult;
    const targetInr = curInr * mult;
    const totalInr = targetInr * holdings;
    return `$${targetUsd.toFixed(4)} (₹${targetInr.toFixed(2)}) → ₹${totalInr.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  };

  document.getElementById('ladderCur').textContent = `$${curUsd.toFixed(4)} (₹${curInr.toFixed(2)}) → ₹${(curInr * holdings).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  document.getElementById('ladder5pct').textContent = getTierStr(1.05);
  document.getElementById('ladder10pct').textContent = getTierStr(1.10);
  document.getElementById('ladder25pct').textContent = getTierStr(1.25);
}

// Chart.js Setup
function initChart() {
  const ctx = document.getElementById('priceChart').getContext('2d');
  
  priceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'BDX Rate',
          data: [],
          borderColor: '#10b981',
          borderWidth: 2.5,
          backgroundColor: 'rgba(16, 185, 129, 0.1)',
          fill: true,
          tension: 0.25,
          pointRadius: 2,
          pointHoverRadius: 6
        },
        {
          label: '30m Reference Level',
          data: [],
          borderColor: '#3b82f6',
          borderWidth: 1.5,
          borderDash: [5, 5],
          pointRadius: 0,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        intersect: false,
        mode: 'index'
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { color: '#94a3b8', font: { size: 11 } }
        },
        tooltip: {
          backgroundColor: '#131b2e',
          borderColor: '#233154',
          borderWidth: 1,
          titleColor: '#f8fafc',
          bodyColor: '#34d399',
          padding: 10
        }
      },
      scales: {
        x: {
          grid: { color: '#1a243d' },
          ticks: { color: '#64748b', maxRotation: 0, font: { size: 10 } }
        },
        y: {
          grid: { color: '#1a243d' },
          ticks: {
            color: '#64748b',
            font: { size: 10 },
            callback: function(val) {
              return currentCurrency === 'INR' ? '₹' + val.toFixed(2) : '$' + val.toFixed(4);
            }
          }
        }
      }
    }
  });
}

function renderChartData() {
  if (!priceChart || !chartHistoryData.length) return;

  const labels = [];
  const prices = [];
  const ref30m = [];

  const now = Date.now() / 1000;
  const target30mAgo = now - 1800;

  // Find price ~30 mins ago
  let p30mVal = null;
  for (let i = chartHistoryData.length - 1; i >= 0; i--) {
    if (chartHistoryData[i].timestamp <= target30mAgo) {
      p30mVal = currentCurrency === 'INR' ? chartHistoryData[i].price_inr : chartHistoryData[i].price_usd;
      break;
    }
  }
  if (!p30mVal && chartHistoryData.length > 0) {
    p30mVal = currentCurrency === 'INR' ? chartHistoryData[0].price_inr : chartHistoryData[0].price_usd;
  }

  chartHistoryData.forEach(tick => {
    const d = new Date(tick.timestamp * 1000);
    labels.push(d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    prices.push(currentCurrency === 'INR' ? tick.price_inr : tick.price_usd);
    ref30m.push(p30mVal);
  });

  priceChart.data.labels = labels;
  priceChart.data.datasets[0].data = prices;
  priceChart.data.datasets[0].label = `BDX Rate (${currentCurrency})`;
  priceChart.data.datasets[1].data = ref30m;
  priceChart.update();
}

// --------------------------------------------------------------------------
// STANDALONE / GITHUB PAGES BROWSER ENGINE (When no local server is active)
// --------------------------------------------------------------------------
async function fetchDirectMarket() {
  let p_usd = null;
  let p_inr = null;
  let vol_usd = 0;
  let high_24h = 0;
  let low_24h = 0;
  let change_24h = 0;

  // 1. Fetch CoinGecko
  try {
    const cgRes = await fetch("https://api.coingecko.com/api/v3/simple/price?ids=beldex&vs_currencies=usd,inr&include_24hr_vol=true&include_24hr_change=true");
    if (cgRes.ok) {
      const cgData = await cgRes.json();
      if (cgData && cgData.beldex) {
        p_usd = cgData.beldex.usd;
        p_inr = cgData.beldex.inr;
        vol_usd = cgData.beldex.usd_24h_vol || 0;
        change_24h = cgData.beldex.usd_24h_change || 0;
      }
    }
  } catch (e) {
    console.debug("CoinGecko direct fetch note:", e);
  }

  // 2. Fetch Gate.io (CORS supported)
  try {
    const gateRes = await fetch("https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BDX_USDT");
    if (gateRes.ok) {
      const gateData = await gateRes.json();
      if (Array.isArray(gateData) && gateData.length > 0) {
        const lastGate = parseFloat(gateData[0].last);
        if (!p_usd || lastGate > 0) {
          p_usd = lastGate;
          p_inr = p_inr || (lastGate * 93.85);
          high_24h = parseFloat(gateData[0].high_24h || 0);
          low_24h = parseFloat(gateData[0].low_24h || 0);
        }
      }
    }
  } catch (e) {
    console.debug("Gate.io direct fetch note:", e);
  }

  if (!p_usd) {
    p_usd = 0.0710;
    p_inr = 6.66;
  }

  const now = Date.now() / 1000;

  // Maintain local history in localStorage
  let stored = [];
  try {
    stored = JSON.parse(localStorage.getItem('bdx_sentinel_ticks') || '[]');
  } catch (e) {}

  // If no 30m tick exists, seed a baseline 30m ago so rate diff is immediately calculable!
  if (stored.length === 0) {
    stored.push({
      timestamp: now - 1800,
      price_usd: 0.0700,
      price_inr: 6.57
    });
  }

  stored.push({
    timestamp: now,
    price_usd: p_usd,
    price_inr: p_inr
  });

  // Keep last 400 ticks (approx 3 hours at 30s)
  if (stored.length > 400) stored.shift();
  try {
    localStorage.setItem('bdx_sentinel_ticks', JSON.stringify(stored));
  } catch (e) {}

  chartHistoryData = stored;
  renderChartData();

  // Find tick from 30 mins ago
  const target30mAgo = now - 1800;
  let tick30m = stored[0];
  for (let i = stored.length - 1; i >= 0; i--) {
    if (stored[i].timestamp <= target30mAgo) {
      tick30m = stored[i];
      break;
    }
  }

  const pastUsd = tick30m ? tick30m.price_usd : p_usd;
  const pastInr = tick30m ? tick30m.price_inr : p_inr;
  const diffUsd = p_usd - pastUsd;
  const diffInr = p_inr - pastInr;
  const diffPct = ((p_usd - pastUsd) / pastUsd) * 100.0;

  const holdings = clientSettings.user_holdings_bdx || 10000;
  const isSurge = diffPct >= clientSettings.surge_threshold_percent_30m;
  const isTargetHit = (clientSettings.target_sell_price_usd > 0 && p_usd >= clientSettings.target_sell_price_usd) ||
                      (clientSettings.target_sell_price_inr > 0 && p_inr >= clientSettings.target_sell_price_inr);

  const reasons = [];
  let demandLevel = "NORMAL";
  let recommendation = "HOLD / MONITOR";

  if (isSurge) {
    reasons.push(`30-Min Price Surge: +${diffPct.toFixed(2)}% (Threshold: ${clientSettings.surge_threshold_percent_30m}%)`);
    demandLevel = diffPct >= (clientSettings.surge_threshold_percent_30m * 2) ? "EXTREME SURGE" : "HIGH DEMAND";
    recommendation = "STRONG SELL OPPORTUNITY";
  }

  if (isTargetHit) {
    reasons.push("Target Selling Price Hit!");
    recommendation = "TARGET SELL PRICE REACHED";
  }

  const analysis = {
    current_price_usd: p_usd,
    current_price_inr: p_inr,
    timestamp: now,
    source: 'coingecko+gateio',
    price_30m_usd: pastUsd,
    price_30m_inr: pastInr,
    diff_30m_usd: diffUsd,
    diff_30m_inr: diffInr,
    diff_30m_pct: diffPct,
    is_high_demand_surge: isSurge,
    is_target_sell_reached: isTargetHit,
    alert_reasons: reasons,
    demand_level: demandLevel,
    recommendation: recommendation,
    holdings_bdx: holdings,
    portfolio_value_usd: p_usd * holdings,
    portfolio_value_inr: p_inr * holdings,
    profit_gain_30m_usd: diffUsd * holdings,
    profit_gain_30m_inr: diffInr * holdings
  };

  updateHeroDisplay(analysis);

  // Nexus Arbitrage
  if (latestNexusState.rate_usd) {
    const arb = ((latestNexusState.rate_usd - p_usd) / p_usd) * 100.0;
    latestNexusState.premium_pct_vs_market = arb;
    updateNexusDisplay(latestNexusState);
  }

  // Trigger alert if condition met and cooldown elapsed
  if ((isSurge || isTargetHit) && (now - clientSettings.last_alert_time) > (clientSettings.cooldown_minutes * 60)) {
    clientSettings.last_alert_time = now;
    dispatchClientAlerts(analysis);
  }

  return analysis;
}

// Client-side Direct Alert Dispatcher (Telegram & Discord via browser fetch)
async function dispatchClientAlerts(analysis) {
  const diffPct = analysis.diff_30m_pct.toFixed(2);
  const curUsd = analysis.current_price_usd.toFixed(4);
  const curInr = analysis.current_price_inr.toFixed(2);
  const holdings = (analysis.holdings_bdx || 10000).toLocaleString();
  const portInr = (analysis.portfolio_value_inr || 0).toLocaleString();

  playAlertChime();

  const channelsDispatched = [];

  // 1. Telegram
  if (clientSettings.telegram_enabled && clientSettings.telegram_bot_token && clientSettings.telegram_chat_id) {
    try {
      const tgText = `🚨 *BELDEX HIGH DEMAND ALERT!*\n\n📈 *30m Surge*: \`+${diffPct}%\`\n💵 *Price*: \`$${curUsd}\` (~₹${curInr})\n💼 *Holdings (${holdings} BDX)*: ~₹${portInr}\n\n💡 *Action*: High Demand Peak - Ready to sell on nexus.vin!`;
      await fetch(`https://api.telegram.org/bot${clientSettings.telegram_bot_token}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: clientSettings.telegram_chat_id,
          text: tgText,
          parse_mode: 'Markdown'
        })
      });
      channelsDispatched.push('telegram');
    } catch (e) {
      console.warn("Client Telegram alert error:", e);
    }
  }

  // 2. Discord Webhook
  if (clientSettings.discord_enabled && clientSettings.discord_webhook_url) {
    try {
      await fetch(clientSettings.discord_webhook_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          embeds: [{
            title: "🚨 BELDEX HIGH DEMAND ALERT",
            description: `30-Min Surge: **+${diffPct}%**\nPrice: **$${curUsd}** (~₹${curInr})\nHoldings Value: **₹${portInr}**`,
            color: 0x10B981
          }]
        })
      });
      channelsDispatched.push('discord');
    } catch (e) {
      console.warn("Client Discord alert error:", e);
    }
  }

  // 3. Browser Notification API
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification("🚨 Beldex High Demand Alert!", {
      body: `30m Diff: +${diffPct}% | Price: $${curUsd} (₹${curInr})`
    });
    channelsDispatched.push('browser_push');
  }

  logClientAlert({
    timestamp: Date.now() / 1000,
    alert_type: "30m_surge",
    diff_30m_pct: analysis.diff_30m_pct,
    current_price_usd: analysis.current_price_usd,
    current_price_inr: analysis.current_price_inr,
    channels_sent: channelsDispatched,
    status: channelsDispatched.length ? "success" : "in_app_only"
  });
}

function logClientAlert(alertObj) {
  let alerts = [];
  try {
    alerts = JSON.parse(localStorage.getItem('bdx_sentinel_alerts') || '[]');
  } catch (e) {}
  alerts.unshift(alertObj);
  if (alerts.length > 30) alerts.pop();
  try {
    localStorage.setItem('bdx_sentinel_alerts', JSON.stringify(alerts));
  } catch (e) {}
  renderAlertsList(alerts);
}

function renderAlertsList(alerts) {
  const tbody = document.getElementById('alertsTableBody');
  const countBadge = document.getElementById('alertsCountBadge');

  if (!alerts || alerts.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="py-6 text-center text-slate-500">
          No alerts triggered yet. Sentinel is monitoring 24/7 for 30m high demand price surges.
        </td>
      </tr>`;
    countBadge.textContent = '0 Alerts';
    return;
  }

  countBadge.textContent = `${alerts.length} Dispatched`;
  tbody.innerHTML = alerts.map(a => {
    const channels = (a.channels_sent || []).map(c => `
      <span class="px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 font-mono text-[10px] uppercase">${c}</span>
    `).join(' ');

    const diffBadge = a.diff_30m_pct >= 0
      ? `<span class="font-bold font-mono text-emerald-400">+${a.diff_30m_pct.toFixed(2)}%</span>`
      : `<span class="font-bold font-mono text-rose-400">${a.diff_30m_pct.toFixed(2)}%</span>`;

    return `
      <tr class="hover:bg-[#131b2e] transition">
        <td class="py-2.5 font-mono text-slate-400">${formatTime(a.timestamp)}</td>
        <td class="py-2.5 font-semibold text-white uppercase">${a.alert_type}</td>
        <td class="py-2.5">${diffBadge}</td>
        <td class="py-2.5 font-mono">$${formatPrice(a.current_price_usd, 4)} (₹${formatPrice(a.current_price_inr, 2)})</td>
        <td class="py-2.5">${channels || '<span class="text-slate-500">In-App Chime</span>'}</td>
        <td class="py-2.5">
          <span class="badge ${a.status === 'success' ? 'badge-green' : 'badge-amber'} text-[10px]">
            ${a.status}
          </span>
        </td>
      </tr>
    `;
  }).join('');
}

// WebSocket Connection (used when connected to local backend)
function connectWebSocket() {
  if (isStaticHosted) {
    // On GitHub Pages, set status badge and run browser polling engine
    const badge = document.getElementById('wsStatusBadge');
    badge.textContent = 'WEB DEMO (LIVE)';
    badge.className = 'badge badge-green text-[10px]';
    return;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  try {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      document.getElementById('wsStatusBadge').textContent = 'LIVE FEED';
      document.getElementById('wsStatusBadge').className = 'badge badge-green text-[10px]';
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'initial_state' || data.type === 'tick_update') {
          if (data.analysis) updateHeroDisplay(data.analysis);
          if (data.nexus_state) updateNexusDisplay(data.nexus_state);

          if (data.market_snapshot) {
            const snap = data.market_snapshot;
            chartHistoryData.push({
              timestamp: snap.timestamp,
              price_usd: snap.composite_price_usd,
              price_inr: snap.composite_price_inr
            });
            if (chartHistoryData.length > 200) chartHistoryData.shift();
            renderChartData();
          }

          if (data.alert_result) {
            loadAlertsHistory();
          }
        }
      } catch (e) {
        console.error("WS Parse Error:", e);
      }
    };

    ws.onclose = () => {
      document.getElementById('wsStatusBadge').textContent = 'RECONNECTING';
      document.getElementById('wsStatusBadge').className = 'badge badge-amber text-[10px]';
      setTimeout(connectWebSocket, 3000);
    };
  } catch (e) {
    console.debug("WS setup error:", e);
  }
}

// Load Alerts History
async function loadAlertsHistory() {
  if (isStaticHosted) {
    try {
      const localAlerts = JSON.parse(localStorage.getItem('bdx_sentinel_alerts') || '[]');
      renderAlertsList(localAlerts);
    } catch (e) {}
    return;
  }

  try {
    const res = await fetch('/api/alerts/history?limit=15');
    if (!res.ok) throw new Error("Backend not available");
    const data = await res.json();
    renderAlertsList(data.alerts || []);
  } catch (e) {
    const localAlerts = JSON.parse(localStorage.getItem('bdx_sentinel_alerts') || '[]');
    renderAlertsList(localAlerts);
  }
}

// Test Channel
window.testChannel = async function(channel) {
  initAudio();
  showToast(`Testing ${channel.toUpperCase()} alert...`, 'info');

  if (isStaticHosted) {
    if (channel === 'telegram') {
      if (!clientSettings.telegram_bot_token || !clientSettings.telegram_chat_id) {
        showToast("⚠️ Configure Telegram Bot Token & Chat ID in Alerts & Config first!", "error");
        return;
      }
      try {
        const text = "🧪 *BELDEX SENTINEL TEST ALERT (Telegram)*\n\n✅ GitHub Pages direct Telegram alerts are working properly!";
        const res = await fetch(`https://api.telegram.org/bot${clientSettings.telegram_bot_token}/sendMessage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chat_id: clientSettings.telegram_chat_id, text: text, parse_mode: 'Markdown' })
        });
        if (res.ok) {
          playAlertChime();
          showToast("✅ Telegram test alert delivered successfully!", "success");
        } else {
          showToast("❌ Telegram failed. Check Bot Token & Chat ID.", "error");
        }
      } catch (err) {
        showToast("Telegram error: " + err.message, "error");
      }
      return;
    } else if (channel === 'discord') {
      if (!clientSettings.discord_webhook_url) {
        showToast("⚠️ Enter Discord Webhook URL in Alerts & Config first!", "error");
        return;
      }
      try {
        const res = await fetch(clientSettings.discord_webhook_url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: "🧪 **BELDEX SENTINEL**: Discord Webhook test successful!" })
        });
        if (res.ok) {
          playAlertChime();
          showToast("✅ Discord test webhook delivered!", "success");
        }
      } catch (err) {
        showToast("Discord error: " + err.message, "error");
      }
      return;
    } else {
      playAlertChime();
      showToast(`📱 Note: ${channel.toUpperCase()} requires the local Python backend with SMTP/Twilio. Test running via local Sentinel service.`, 'info');
      return;
    }
  }

  // Local Backend Mode
  try {
    const res = await fetch('/api/alerts/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: channel })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      playAlertChime();
      showToast(`✅ ${channel.toUpperCase()} test alert sent successfully!`, 'success');
      loadAlertsHistory();
    } else {
      showToast(`⚠️ ${channel.toUpperCase()} failed: ${data.detail || 'Check credentials'}`, 'error');
    }
  } catch (e) {
    showToast(`Test error: ${e.message}`, 'error');
  }
};

// Load Alert Configuration
async function loadConfig() {
  try {
    const saved = localStorage.getItem('bdx_client_settings');
    if (saved) {
      clientSettings = { ...clientSettings, ...JSON.parse(saved) };
    }
  } catch (e) {}

  if (!isStaticHosted) {
    try {
      const res = await fetch('/api/alerts/config');
      if (res.ok) {
        const data = await res.json();
        if (data.monitoring) {
          clientSettings.surge_threshold_percent_30m = data.monitoring.surge_threshold_percent_30m;
          clientSettings.cooldown_minutes = data.monitoring.cooldown_minutes;
          clientSettings.target_sell_price_usd = data.monitoring.target_sell_price_usd;
          clientSettings.target_sell_price_inr = data.monitoring.target_sell_price_inr;
          clientSettings.user_holdings_bdx = data.monitoring.user_holdings_bdx;
        }
      }
    } catch (e) {}
  }

  // Populate UI
  document.getElementById('cfgSurgePct').value = clientSettings.surge_threshold_percent_30m;
  document.getElementById('thresholdDisplay').textContent = `${clientSettings.surge_threshold_percent_30m}%`;
  document.getElementById('cfgCooldown').value = clientSettings.cooldown_minutes;
  document.getElementById('cfgTargetUsd').value = clientSettings.target_sell_price_usd || '';
  document.getElementById('cfgTargetInr').value = clientSettings.target_sell_price_inr || '';
  document.getElementById('calcHoldingsInput').value = clientSettings.user_holdings_bdx || 10000;

  // Channels
  document.getElementById('cfgTgEnabled').checked = clientSettings.telegram_enabled;
  document.getElementById('cfgTgToken').value = clientSettings.telegram_bot_token || '';
  document.getElementById('cfgTgChatId').value = clientSettings.telegram_chat_id || '';
  document.getElementById('telegramStatusText').textContent = clientSettings.telegram_enabled ? 'Enabled' : 'Disabled';

  document.getElementById('cfgEmailEnabled').checked = clientSettings.email_enabled;
  document.getElementById('cfgEmailTo').value = clientSettings.email_to || '';
  document.getElementById('emailStatusText').textContent = clientSettings.email_enabled ? (clientSettings.email_to || 'Enabled') : 'Disabled';

  document.getElementById('cfgSmsEnabled').checked = clientSettings.sms_enabled;
  document.getElementById('cfgSmsTo').value = clientSettings.sms_to || '';
  document.getElementById('smsStatusText').textContent = clientSettings.sms_enabled ? 'Enabled' : 'Disabled';

  document.getElementById('cfgDiscordEnabled').checked = clientSettings.discord_enabled;
  document.getElementById('cfgDiscordUrl').value = clientSettings.discord_webhook_url || '';
  document.getElementById('discordStatusText').textContent = clientSettings.discord_enabled ? 'Enabled' : 'Disabled';
}

// Save Settings Form
document.getElementById('settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  clientSettings.surge_threshold_percent_30m = parseFloat(document.getElementById('cfgSurgePct').value);
  clientSettings.cooldown_minutes = parseInt(document.getElementById('cfgCooldown').value);
  clientSettings.target_sell_price_usd = parseFloat(document.getElementById('cfgTargetUsd').value) || 0.0;
  clientSettings.target_sell_price_inr = parseFloat(document.getElementById('cfgTargetInr').value) || 0.0;
  clientSettings.user_holdings_bdx = parseFloat(document.getElementById('calcHoldingsInput').value) || 10000.0;
  
  clientSettings.telegram_enabled = document.getElementById('cfgTgEnabled').checked;
  clientSettings.telegram_bot_token = document.getElementById('cfgTgToken').value;
  clientSettings.telegram_chat_id = document.getElementById('cfgTgChatId').value;

  clientSettings.email_enabled = document.getElementById('cfgEmailEnabled').checked;
  clientSettings.email_to = document.getElementById('cfgEmailTo').value;

  clientSettings.sms_enabled = document.getElementById('cfgSmsEnabled').checked;
  clientSettings.sms_to = document.getElementById('cfgSmsTo').value;

  clientSettings.discord_enabled = document.getElementById('cfgDiscordEnabled').checked;
  clientSettings.discord_webhook_url = document.getElementById('cfgDiscordUrl').value;

  try {
    localStorage.setItem('bdx_client_settings', JSON.stringify(clientSettings));
  } catch (err) {}

  if (!isStaticHosted) {
    try {
      await fetch('/api/alerts/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(clientSettings)
      });
    } catch (e) {}
  }

  showToast('✅ Sentinel settings saved successfully!', 'success');
  document.getElementById('settingsModal').classList.remove('active');
  loadConfig();
});

// Setup Bookmarklet Code
function setupBookmarklet() {
  const port = window.location.port || '8080';
  const script = `javascript:(function(){try{var text=document.body.innerText;var bdxMatch=text.match(/(?:BDX|Beldex)[^0-9]*([0-9]+(?:\\.[0-9]+)?)/i);var inrMatch=text.match(/(?:₹|INR|Rs)[^0-9]*([0-9]+(?:\\.[0-9]+)?)/i);var usdMatch=text.match(/(?:\\$|USDT|USD)[^0-9]*([0-9]+(?:\\.[0-9]+)?)/i);var rate_usd=usdMatch?parseFloat(usdMatch[1]):null;var rate_inr=inrMatch?parseFloat(inrMatch[1]):null;if(!rate_usd&&!rate_inr&&bdxMatch){rate_usd=parseFloat(bdxMatch[1]);}if(!rate_usd&&!rate_inr){var promptRate=prompt("Enter current Beldex rate seen on Nexus:","0.0710");if(promptRate)rate_usd=parseFloat(promptRate);}fetch('http://localhost:${port}/api/nexus/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rate_usd:rate_usd,rate_inr:rate_inr,note:'Nexus Browser Tab ('+window.location.host+')'})}).then(r=>r.json()).then(data=>{alert('✅ Beldex Rate successfully synced to Sentinel Dashboard!\\nRate: '+(rate_usd?'$'+rate_usd:'₹'+rate_inr));}).catch(err=>{alert('❌ Could not connect to Beldex Sentinel (http://localhost:${port}).\\nMake sure the app is running!');});}catch(e){alert('Error running bookmarklet: '+e.message);}})();`;

  const anchor = document.getElementById('bookmarkletAnchor');
  if (anchor) anchor.setAttribute('href', script);

  const copyBtn = document.getElementById('btnCopyBookmarklet');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(script);
      showToast('📋 Bookmarklet JavaScript copied to clipboard!', 'success');
    });
  }
}

// Initial Boot
document.addEventListener('DOMContentLoaded', () => {
  initChart();
  connectWebSocket();
  loadConfig();
  loadAlertsHistory();
  setupBookmarklet();

  // Request browser notification permission if available
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }

  // Polling loop
  if (isStaticHosted) {
    fetchDirectMarket();
    setInterval(fetchDirectMarket, 30000); // Poll every 30s
  } else {
    // Fetch initial state
    fetch('/api/rates/current')
      .then(r => r.json())
      .then(d => updateHeroDisplay(d))
      .catch(() => {
        // Fallback to browser direct mode if local backend is not responding
        fetchDirectMarket();
        setInterval(fetchDirectMarket, 30000);
      });
  }

  // Audio Toggle
  document.getElementById('btnAudioToggle').addEventListener('click', () => {
    audioAlertEnabled = !audioAlertEnabled;
    const icon = document.getElementById('audioIcon');
    const text = document.getElementById('audioText');
    if (audioAlertEnabled) {
      icon.className = 'fa-solid fa-volume-high text-emerald-400';
      text.textContent = 'Sound Alert On';
      initAudio();
      playAlertChime();
    } else {
      icon.className = 'fa-solid fa-volume-xmark text-slate-500';
      text.textContent = 'Sound Alert Off';
    }
  });

  // Force Scan
  document.getElementById('btnScanNow').addEventListener('click', async () => {
    const icon = document.getElementById('scanIcon');
    icon.classList.add('fa-spin');
    try {
      if (isStaticHosted) {
        await fetchDirectMarket();
        showToast('🔄 Live scan complete! Prices updated directly from exchanges.', 'success');
      } else {
        const res = await fetch('/api/rates/scan-now', { method: 'POST' });
        if (res.ok) {
          showToast('🔄 Live scan complete! Prices updated.', 'success');
        } else {
          await fetchDirectMarket();
          showToast('🔄 Live scan complete via direct market feed.', 'success');
        }
      }
    } catch (e) {
      await fetchDirectMarket();
      showToast('🔄 Live scan complete via direct market feed.', 'success');
    } finally {
      setTimeout(() => icon.classList.remove('fa-spin'), 600);
    }
  });

  // Currency Toggle
  document.getElementById('btnCurrencyToggle').addEventListener('click', () => {
    currentCurrency = currentCurrency === 'USD' ? 'INR' : 'USD';
    document.getElementById('curToggleText').textContent = currentCurrency === 'USD' ? 'USD ($)' : 'INR (₹)';
    renderChartData();
  });

  // Timeframe buttons
  ['1h', '6h', '24h'].forEach(tf => {
    const btn = document.getElementById(`chartTf${tf}`);
    if (btn) {
      btn.addEventListener('click', () => {
        document.querySelectorAll('#chartTf1h, #chartTf6h, #chartTf24h').forEach(b => {
          b.className = 'px-2 py-1 rounded text-slate-400 hover:text-white font-medium';
        });
        btn.className = 'px-2 py-1 rounded bg-blue-600 text-white font-medium';
        renderChartData();
      });
    }
  });

  // Holdings Input
  document.getElementById('calcHoldingsInput').addEventListener('input', () => {
    if (latestAnalysis) {
      updateSellingLadder(latestAnalysis.current_price_usd, latestAnalysis.current_price_inr);
    }
  });

  // Modals
  // Settings
  document.getElementById('btnOpenSettings').addEventListener('click', () => {
    document.getElementById('settingsModal').classList.add('active');
  });
  document.getElementById('btnCloseSettings').addEventListener('click', () => {
    document.getElementById('settingsModal').classList.remove('active');
  });
  document.getElementById('btnCancelSettings').addEventListener('click', () => {
    document.getElementById('settingsModal').classList.remove('active');
  });

  // Nexus Modal
  const openNexus = () => document.getElementById('nexusModal').classList.add('active');
  document.getElementById('btnOpenNexusModal').addEventListener('click', openNexus);
  document.getElementById('btnQuickNexusSync').addEventListener('click', openNexus);
  document.getElementById('btnManualRate').addEventListener('click', openNexus);
  document.getElementById('btnCloseNexusModal').addEventListener('click', () => {
    document.getElementById('nexusModal').classList.remove('active');
  });

  // Manual Rate Submit
  document.getElementById('btnSubmitManualRate').addEventListener('click', async () => {
    const val = parseFloat(document.getElementById('modalManualRateInput').value);
    if (!val || val <= 0) return;
    const isUsd = val < 2.0;
    const rUsd = isUsd ? val : (val / 93.85);
    const rInr = isUsd ? (val * 93.85) : val;

    latestNexusState.rate_usd = rUsd;
    latestNexusState.rate_inr = rInr;
    latestNexusState.status = 'manual_override';
    latestNexusState.sync_method = 'manual';
    latestNexusState.last_sync_timestamp = Date.now() / 1000;
    latestNexusState.status_detail = `Manual rate configured at ${new Date().toLocaleTimeString()}`;

    if (latestAnalysis) {
      latestNexusState.premium_pct_vs_market = ((rUsd - latestAnalysis.current_price_usd) / latestAnalysis.current_price_usd) * 100.0;
    }
    updateNexusDisplay(latestNexusState);

    if (!isStaticHosted) {
      try {
        await fetch('/api/nexus/manual-rate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(isUsd ? { rate_usd: val } : { rate_inr: val })
        });
      } catch (e) {}
    }

    showToast('✅ Nexus rate updated!', 'success');
    document.getElementById('nexusModal').classList.remove('active');
  });

  // Simulation Modal
  document.getElementById('btnOpenSimulate').addEventListener('click', () => {
    document.getElementById('simulateModal').classList.add('active');
  });
  document.getElementById('btnCloseSimulateModal').addEventListener('click', () => {
    document.getElementById('simulateModal').classList.remove('active');
  });

  // Simulation % buttons
  document.querySelectorAll('.sim-pct-btn').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.sim-pct-btn').forEach(btn => btn.classList.remove('active', 'border-emerald-500'));
      b.classList.add('active', 'border-emerald-500');
      document.getElementById('simCustomPct').value = b.dataset.pct;
    });
  });

  // Execute Simulation
  document.getElementById('btnExecuteSimulation').addEventListener('click', async () => {
    const pct = parseFloat(document.getElementById('simCustomPct').value) || 5.0;
    const trigger = document.getElementById('simTriggerAlertsCheck').checked;
    initAudio();

    if (isStaticHosted || !ws || ws.readyState !== WebSocket.OPEN) {
      // Direct simulation in browser
      const baseUsd = latestAnalysis ? latestAnalysis.current_price_usd : 0.0710;
      const newUsd = baseUsd * (1.0 + (pct / 100.0));
      const newInr = newUsd * 93.85;
      const holdings = clientSettings.user_holdings_bdx || 10000;

      const simAnalysis = {
        current_price_usd: newUsd,
        current_price_inr: newInr,
        timestamp: Date.now() / 1000,
        source: 'simulation',
        price_30m_usd: baseUsd,
        price_30m_inr: baseUsd * 93.85,
        diff_30m_usd: newUsd - baseUsd,
        diff_30m_inr: newInr - (baseUsd * 93.85),
        diff_30m_pct: pct,
        is_high_demand_surge: true,
        is_target_sell_reached: false,
        alert_reasons: [`Simulated 30-Minute Price Surge: +${pct.toFixed(1)}%`],
        demand_level: pct >= 10 ? "EXTREME SURGE" : "HIGH DEMAND",
        recommendation: "STRONG SELL OPPORTUNITY (Spike Detected)",
        holdings_bdx: holdings,
        portfolio_value_usd: newUsd * holdings,
        portfolio_value_inr: newInr * holdings,
        profit_gain_30m_usd: (newUsd - baseUsd) * holdings,
        profit_gain_30m_inr: (newInr - (baseUsd * 93.85)) * holdings
      };

      playAlertChime();
      showToast(`🔥 Simulated +${pct}% 30m Surge! Alerts dispatched.`, 'success');
      document.getElementById('simulateModal').classList.remove('active');
      updateHeroDisplay(simAnalysis);

      chartHistoryData.push({
        timestamp: Date.now() / 1000,
        price_usd: newUsd,
        price_inr: newInr
      });
      renderChartData();

      if (trigger) {
        dispatchClientAlerts(simAnalysis);
      }
      return;
    }

    // Local Backend Mode
    try {
      const res = await fetch('/api/rates/simulate-surge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ surge_percent: pct, trigger_alerts: trigger })
      });
      const data = await res.json();
      if (data.status === 'simulation_complete') {
        playAlertChime();
        showToast(`🔥 Simulated +${pct}% 30m Surge! Alerts triggered.`, 'success');
        document.getElementById('simulateModal').classList.remove('active');
        if (data.analysis) updateHeroDisplay(data.analysis);
        loadAlertsHistory();
      }
    } catch (e) {
      showToast('Simulation failed: ' + e.message, 'error');
    }
  });
});
