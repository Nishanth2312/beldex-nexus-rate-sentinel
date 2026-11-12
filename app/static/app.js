/**
 * Beldex Nexus Rate Sentinel Frontend Application Logic
 */

// State
let ws = null;
let priceChart = null;
let currentCurrency = 'USD'; // 'USD' or 'INR'
let activeTimeframe = 1; // hours
let audioAlertEnabled = true;
let chartHistoryData = [];
let latestAnalysis = null;
let latestNexusState = null;
let audioContext = null;

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

function formatCurrency(val, currency = 'USD') {
  if (val === null || val === undefined || isNaN(val)) return '--';
  if (currency === 'INR') {
    return '₹' + parseFloat(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return '$' + parseFloat(val).toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 });
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

  if (pastUsd !== null) {
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

    diffUsdElem.textContent = `${sign}$${Math.abs(diffUsd).toFixed(4)}`;
    diffInrElem.textContent = `${sign}₹${Math.abs(diffInr).toFixed(2)}`;

    // Signal banner
    const banner = document.getElementById('signalBanner');
    const signalTitle = document.getElementById('signalTitle');
    const signalDesc = document.getElementById('signalDesc');
    const glow = document.getElementById('ambientGlow');

    if (analysis.is_high_demand_surge || analysis.is_target_sell_reached) {
      playAlertChime();
      banner.className = "mt-4 p-3 rounded-lg bg-emerald-950/60 border-2 border-emerald-400 flex items-center gap-3 shadow-lg shadow-emerald-500/20";
      signalTitle.className = "text-xs font-black text-emerald-300 uppercase tracking-wide";
      signalTitle.textContent = "🚨 " + analysis.recommendation;
      signalDesc.textContent = analysis.alert_reasons.join(" • ");
      glow.className = "absolute -right-20 -top-20 w-80 h-80 rounded-full bg-emerald-500/30 blur-3xl pointer-events-none";
    } else {
      banner.className = "mt-4 p-3 rounded-lg bg-emerald-950/40 border border-emerald-500/30 flex items-center gap-3";
      signalTitle.className = "text-xs font-bold text-emerald-400 uppercase tracking-wide";
      signalTitle.textContent = analysis.recommendation;
      signalDesc.textContent = `30m Diff is ${sign}${diffPct.toFixed(2)}%. Sentinel will alert you when spike meets threshold.`;
      glow.className = "absolute -right-20 -top-20 w-80 h-80 rounded-full bg-emerald-500/10 blur-3xl pointer-events-none";
    }
  }

  // Portfolio
  const holdings = analysis.holdings_bdx || 10000;
  document.getElementById('heroHoldingsAmount').textContent = `${holdings.toLocaleString()} BDX`;
  document.getElementById('heroPortUsd').textContent = `$${(curUsd * holdings).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  document.getElementById('heroPortInr').textContent = `₹${(curInr * holdings).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

  if (analysis.profit_gain_30m_inr !== null) {
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
    const totalUsd = targetUsd * holdings;
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

async function loadChartHistory(hours = 1) {
  try {
    const res = await fetch(`/api/rates/history?hours=${hours}`);
    const data = await res.json();
    if (data && data.ticks) {
      chartHistoryData = data.ticks;
      renderChartData();
    }
  } catch (e) {
    console.error("Failed to load chart history:", e);
  }
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

// WebSocket Connection
function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

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

        // Append to chart
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
}

// Load Alerts History
async function loadAlertsHistory() {
  try {
    const res = await fetch('/api/alerts/history?limit=15');
    const data = await res.json();
    const tbody = document.getElementById('alertsTableBody');
    const countBadge = document.getElementById('alertsCountBadge');

    if (!data.alerts || data.alerts.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6" class="py-6 text-center text-slate-500">
            No alerts triggered yet. Sentinel is monitoring 24/7 for 30m high demand price surges.
          </td>
        </tr>`;
      countBadge.textContent = '0 Alerts';
      return;
    }

    countBadge.textContent = `${data.alerts.length} Dispatched`;
    tbody.innerHTML = data.alerts.map(a => {
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
          <td class="py-2.5">${channels || '<span class="text-slate-500">None</span>'}</td>
          <td class="py-2.5">
            <span class="badge ${a.status === 'success' ? 'badge-green' : 'badge-amber'} text-[10px]">
              ${a.status}
            </span>
          </td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    console.error("Alerts history error:", e);
  }
}

// Test Channel
window.testChannel = async function(channel) {
  initAudio();
  showToast(`Dispatching test alert to ${channel}...`, 'info');
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
    const res = await fetch('/api/alerts/config');
    const data = await res.json();
    
    // Monitoring
    if (data.monitoring) {
      document.getElementById('cfgSurgePct').value = data.monitoring.surge_threshold_percent_30m;
      document.getElementById('thresholdDisplay').textContent = `${data.monitoring.surge_threshold_percent_30m}%`;
      document.getElementById('cfgCooldown').value = data.monitoring.cooldown_minutes;
      document.getElementById('cfgTargetUsd').value = data.monitoring.target_sell_price_usd || '';
      document.getElementById('cfgTargetInr').value = data.monitoring.target_sell_price_inr || '';
      if (data.monitoring.user_holdings_bdx) {
        document.getElementById('calcHoldingsInput').value = data.monitoring.user_holdings_bdx;
      }
    }

    // Channels
    if (data.channels) {
      const c = data.channels;
      // Telegram
      document.getElementById('cfgTgEnabled').checked = c.telegram.enabled;
      document.getElementById('cfgTgChatId').value = c.telegram.chat_id || '';
      document.getElementById('telegramStatusText').textContent = c.telegram.enabled ? 'Enabled' : 'Disabled';

      // Email
      document.getElementById('cfgEmailEnabled').checked = c.email.enabled;
      document.getElementById('cfgEmailTo').value = c.email.email_to || '';
      document.getElementById('cfgSmtpHost').value = c.email.smtp_host || '';
      document.getElementById('cfgSmtpUser').value = c.email.smtp_user || '';
      document.getElementById('emailStatusText').textContent = c.email.enabled ? (c.email.email_to || 'Enabled') : 'Disabled';

      // SMS
      document.getElementById('cfgSmsEnabled').checked = c.sms.enabled;
      document.getElementById('cfgSmsProvider').value = c.sms.provider || 'twilio';
      document.getElementById('cfgSmsTo').value = c.sms.twilio_to_number || c.sms.fast2sms_numbers || '';
      document.getElementById('smsStatusText').textContent = c.sms.enabled ? 'Enabled' : 'Disabled';

      // Discord
      document.getElementById('cfgDiscordEnabled').checked = c.discord.enabled;
      document.getElementById('discordStatusText').textContent = c.discord.enabled ? 'Enabled' : 'Disabled';
    }
  } catch (e) {
    console.error("Config load error:", e);
  }
}

// Save Settings Form
document.getElementById('settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    surge_threshold_percent_30m: parseFloat(document.getElementById('cfgSurgePct').value),
    cooldown_minutes: parseInt(document.getElementById('cfgCooldown').value),
    target_sell_price_usd: parseFloat(document.getElementById('cfgTargetUsd').value) || 0.0,
    target_sell_price_inr: parseFloat(document.getElementById('cfgTargetInr').value) || 0.0,
    user_holdings_bdx: parseFloat(document.getElementById('calcHoldingsInput').value) || 10000.0,
    
    telegram_enabled: document.getElementById('cfgTgEnabled').checked,
    telegram_bot_token: document.getElementById('cfgTgToken').value,
    telegram_chat_id: document.getElementById('cfgTgChatId').value,

    email_enabled: document.getElementById('cfgEmailEnabled').checked,
    smtp_host: document.getElementById('cfgSmtpHost').value,
    smtp_user: document.getElementById('cfgSmtpUser').value,
    smtp_password: document.getElementById('cfgSmtpPass').value,
    email_to: document.getElementById('cfgEmailTo').value,

    sms_enabled: document.getElementById('cfgSmsEnabled').checked,
    sms_provider: document.getElementById('cfgSmsProvider').value,
    twilio_to_number: document.getElementById('cfgSmsTo').value,
    twilio_account_sid: document.getElementById('cfgTwilioSid').value,
    twilio_auth_token: document.getElementById('cfgTwilioToken').value,
    twilio_from_number: document.getElementById('cfgTwilioFrom').value,
    fast2sms_api_key: document.getElementById('cfgFast2smsKey').value,
    fast2sms_numbers: document.getElementById('cfgSmsTo').value,

    discord_enabled: document.getElementById('cfgDiscordEnabled').checked,
    discord_webhook_url: document.getElementById('cfgDiscordUrl').value
  };

  try {
    const res = await fetch('/api/alerts/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      showToast('✅ Sentinel settings saved successfully!', 'success');
      document.getElementById('settingsModal').classList.remove('active');
      loadConfig();
    }
  } catch (err) {
    showToast('Failed to save settings: ' + err.message, 'error');
  }
});

// Setup Bookmarklet Code
async function setupBookmarklet() {
  try {
    const res = await fetch('/api/nexus/bookmarklet-code');
    const data = await res.json();
    if (data.bookmarklet) {
      const anchor = document.getElementById('bookmarkletAnchor');
      anchor.setAttribute('href', data.bookmarklet);

      document.getElementById('btnCopyBookmarklet').addEventListener('click', () => {
        navigator.clipboard.writeText(data.bookmarklet);
        showToast('📋 Bookmarklet JavaScript copied to clipboard!', 'success');
      });
    }
  } catch (e) {
    console.error("Bookmarklet fetch error:", e);
  }
}

// Event Listeners
document.addEventListener('DOMContentLoaded', () => {
  initChart();
  connectWebSocket();
  loadChartHistory(activeTimeframe);
  loadAlertsHistory();
  loadConfig();
  setupBookmarklet();

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
      const res = await fetch('/api/rates/scan-now', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'success') {
        showToast('🔄 Live scan complete! Prices updated.', 'success');
      }
    } catch (e) {
      showToast('Scan error: ' + e.message, 'error');
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
    btn.addEventListener('click', () => {
      document.querySelectorAll('#chartTf1h, #chartTf6h, #chartTf24h').forEach(b => {
        b.className = 'px-2 py-1 rounded text-slate-400 hover:text-white font-medium';
      });
      btn.className = 'px-2 py-1 rounded bg-blue-600 text-white font-medium';
      const hours = tf === '1h' ? 1 : (tf === '6h' ? 6 : 24);
      loadChartHistory(hours);
    });
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
    try {
      const res = await fetch('/api/nexus/manual-rate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(isUsd ? { rate_usd: val } : { rate_inr: val })
      });
      if (res.ok) {
        showToast('✅ Nexus rate updated!', 'success');
        document.getElementById('nexusModal').classList.remove('active');
        fetch('/api/nexus/status').then(r => r.json()).then(d => updateNexusDisplay(d.state));
      }
    } catch (e) {
      showToast('Error updating manual rate: ' + e.message, 'error');
    }
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
