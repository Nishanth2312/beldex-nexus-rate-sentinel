/**
 * Beldex Nexus Rate Sentinel Frontend Application Logic (INR Primary + Admin Access Control)
 */

// State
let ws = null;
let priceChart = null;
let currentCurrency = 'INR'; // Locked & defaulted to INR (₹)
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

// Admin Authentication State
let adminToken = localStorage.getItem('beldex_admin_token') || null;
let isAdmin = false;

// Default Admin Credentials (for UI reference & static demo verification)
const DEFAULT_ADMIN_USER = "admin";
const DEFAULT_ADMIN_PASS = "Admin@Beldex2026!";

// Detect static GitHub Pages environment
const isStaticHosted = window.location.hostname.includes('github.io') || 
                       window.location.protocol === 'file:' || 
                       !window.location.port;

let clientSettings = {
  surge_threshold_percent_30m: 3.0,
  cooldown_minutes: 15,
  target_sell_price_usd: 0.0,
  target_sell_price_inr: 7.50,
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
function formatPrice(val, decimals = 2) {
  if (val === null || val === undefined || isNaN(val)) return '--';
  return parseFloat(val).toFixed(decimals);
}

// Format time
function formatTime(epochSec) {
  if (!epochSec) return 'Never';
  const d = new Date(epochSec * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// --------------------------------------------------------------------------
// ADMIN AUTHENTICATION HELPERS
// --------------------------------------------------------------------------
function updateAdminUI() {
  const banner = document.getElementById('adminActiveBanner');
  const lockIcon = document.getElementById('adminLockIcon');
  const btnText = document.getElementById('adminBtnText');

  if (isAdmin) {
    if (banner) banner.classList.remove('hidden');
    if (lockIcon) lockIcon.className = "fa-solid fa-shield-halved text-emerald-400";
    if (btnText) btnText.textContent = "Admin Active (Logout)";
  } else {
    if (banner) banner.classList.add('hidden');
    if (lockIcon) lockIcon.className = "fa-solid fa-lock text-amber-400";
    if (btnText) btnText.textContent = "Admin Login";
  }
}

async function checkAdminSession() {
  if (!adminToken) {
    isAdmin = false;
    updateAdminUI();
    return;
  }

  if (isStaticHosted) {
    // In static mode, verify token presence
    isAdmin = true;
    updateAdminUI();
    return;
  }

  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${adminToken}` }
    });
    if (res.ok) {
      isAdmin = true;
    } else {
      isAdmin = false;
      adminToken = null;
      localStorage.removeItem('beldex_admin_token');
    }
  } catch (e) {
    // Backend offline: keep existing session if present
    isAdmin = Boolean(adminToken);
  }
  updateAdminUI();
}

function promptAdminLogin(reason = "Access restricted to Administrator alone.") {
  showToast(`🔒 ${reason}`, "info");
  const modal = document.getElementById('adminLoginModal');
  if (modal) modal.classList.add('active');
}

// --------------------------------------------------------------------------
// UI UPDATERS (ALL INR PRIMARY)
// --------------------------------------------------------------------------
function updateHeroDisplay(analysis) {
  if (!analysis) return;
  latestAnalysis = analysis;

  const curInr = analysis.current_price_inr;
  const curUsd = analysis.current_price_usd;
  const pastInr = analysis.price_30m_inr;
  const pastUsd = analysis.price_30m_usd;
  const diffPct = analysis.diff_30m_pct;
  const diffInr = analysis.diff_30m_inr;
  const diffUsd = analysis.diff_30m_usd;

  // Primary rate in INR
  document.getElementById('heroCurInr').textContent = '₹' + formatPrice(curInr, 2);
  document.getElementById('heroCurUsd').textContent = `($${formatPrice(curUsd, 4)} USD)`;

  if (pastInr !== null && pastInr !== undefined) {
    document.getElementById('heroPastInr').textContent = '₹' + formatPrice(pastInr, 2);
    document.getElementById('heroPastUsd').textContent = `($${formatPrice(pastUsd, 4)} USD)`;
  }

  // 30m Diff formatting (INR PRIMARY)
  const diffInrElem = document.getElementById('diff30mInr');
  const diffPctElem = document.getElementById('diff30mPercent');
  const indElem = document.getElementById('diff30mIndicator');
  const diffUsdElem = document.getElementById('diff30mUsd');

  if (diffPct !== null && diffPct !== undefined) {
    const isPositive = diffPct >= 0;
    const sign = isPositive ? '+' : '';
    
    diffInrElem.textContent = `${sign}₹${Math.abs(diffInr || 0).toFixed(2)}`;
    diffPctElem.textContent = `(${sign}${diffPct.toFixed(2)}%)`;
    diffPctElem.className = `text-2xl md:text-3xl font-black font-mono ${isPositive ? 'rate-surge-positive' : 'rate-surge-negative'}`;

    indElem.innerHTML = isPositive
      ? '<i class="fa-solid fa-arrow-trend-up text-emerald-400"></i>'
      : '<i class="fa-solid fa-arrow-trend-down text-rose-400"></i>';

    diffUsdElem.textContent = `${sign}$${Math.abs(diffUsd || 0).toFixed(4)} USD`;

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
        : `30-Minute INR Surge +${diffPct.toFixed(2)}% detected!`;
      glow.className = "absolute -right-20 -top-20 w-80 h-80 rounded-full bg-emerald-500/30 blur-3xl pointer-events-none";
    } else {
      banner.className = "mt-4 p-3 rounded-lg bg-emerald-950/40 border border-emerald-500/30 flex items-center gap-3";
      signalTitle.className = "text-xs font-bold text-emerald-400 uppercase tracking-wide";
      signalTitle.textContent = analysis.recommendation || "HOLD / MONITOR";
      signalDesc.textContent = `30m INR Diff is ${sign}₹${Math.abs(diffInr || 0).toFixed(2)} (${sign}${diffPct.toFixed(2)}%). Sentinel will alert you on high demand spikes.`;
      glow.className = "absolute -right-20 -top-20 w-80 h-80 rounded-full bg-emerald-500/10 blur-3xl pointer-events-none";
    }
  }

  // Portfolio in INR
  const holdings = analysis.holdings_bdx || clientSettings.user_holdings_bdx || 10000;
  const portInr = curInr * holdings;
  const portUsd = curUsd * holdings;

  document.getElementById('heroHoldingsAmount').textContent = `${holdings.toLocaleString()} BDX`;
  document.getElementById('heroPortInr').textContent = `₹${portInr.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  document.getElementById('heroPortUsd').textContent = `($${portUsd.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD)`;

  if (analysis.profit_gain_30m_inr !== null && analysis.profit_gain_30m_inr !== undefined) {
    const pSign = analysis.profit_gain_30m_inr >= 0 ? '+' : '';
    document.getElementById('heroPortGain').textContent = `${pSign}₹${Math.abs(analysis.profit_gain_30m_inr).toFixed(0)} in 30m`;
  }

  // Multi-Timeframe Matrix in INR
  updateMatrixCell('5m', analysis.diff_5m);
  updateMatrixCell('15m', analysis.diff_15m);
  updateMatrixCell('30m', { diff_pct: analysis.diff_30m_pct, diff_inr: analysis.diff_30m_inr });
  updateMatrixCell('1h', analysis.diff_1h);
  updateMatrixCell('24h', analysis.diff_24h);

  document.getElementById('lastTickTime').textContent = formatTime(analysis.timestamp);
  updateSellingLadder(curInr, curUsd);
  updateNexusMenuUI();
}

function updateMatrixCell(windowKey, diffObj) {
  const inrElem = document.getElementById(`matrix${windowKey}Inr`);
  const pctElem = document.getElementById(`matrix${windowKey}Pct`);
  if (!inrElem || !pctElem) return;

  if (diffObj && diffObj.diff_pct !== null && diffObj.diff_pct !== undefined) {
    const isPos = diffObj.diff_pct >= 0;
    const sign = isPos ? '+' : '';
    const diffInrVal = diffObj.diff_inr !== undefined && diffObj.diff_inr !== null 
      ? Math.abs(diffObj.diff_inr).toFixed(2)
      : (Math.abs(diffObj.diff_usd || 0) * 93.85).toFixed(2);

    inrElem.textContent = `${sign}₹${diffInrVal}`;
    inrElem.className = `text-lg md:text-xl font-bold font-mono my-1 ${isPos ? 'text-emerald-400' : 'text-rose-400'}`;
    pctElem.textContent = `${sign}${diffObj.diff_pct.toFixed(2)}%`;
  } else {
    inrElem.textContent = '--';
    pctElem.textContent = 'Collecting...';
  }
}

let nexusSellingPremiumInr = 0.45; // 45 paise formula offset (+₹0.45 INR)

function updateNexusDisplay(nexus) {
  if (!nexus) return;
  latestNexusState = nexus;
  if (nexus.nexus_selling_premium_inr) {
    nexusSellingPremiumInr = nexus.nexus_selling_premium_inr;
  }
  updateNexusMenuUI();
}

function updateNexusMenuUI() {
  const actualInr = latestAnalysis ? latestAnalysis.current_price_inr : (latestNexusState.actual_beldex_price_inr || 6.66);
  const actualUsd = latestAnalysis ? latestAnalysis.current_price_usd : (latestNexusState.actual_beldex_price_usd || 0.0710);
  
  // Rule: Selling Price = Actual Beldex Price + 0.45 paise (+₹0.45 INR)
  const premiumInr = nexusSellingPremiumInr;
  const sellingInr = actualInr + premiumInr;
  const sellingUsd = sellingInr / 93.85;
  const arbitragePct = actualInr > 0 ? ((sellingInr - actualInr) / actualInr) * 100.0 : 0;
  
  // 1. Update Card on Dashboard
  const cardActual = document.getElementById('nexusCardActualInr');
  const cardPayout = document.getElementById('nexusCardPayoutInr');
  const cardBonus = document.getElementById('nexusCardBonusInr');
  const inrDisp = document.getElementById('nexusInrDisplay');
  const rateDisp = document.getElementById('nexusRateDisplay');
  const arbDisp = document.getElementById('nexusArbitrageDisplay');
  const syncMethodDisp = document.getElementById('nexusSyncMethod');
  
  if (cardActual) cardActual.textContent = `₹${actualInr.toFixed(4)}`;
  if (cardPayout) cardPayout.textContent = `₹${(10000 * sellingInr).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  if (cardBonus) cardBonus.textContent = `+₹${(10000 * premiumInr).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (inrDisp) inrDisp.textContent = `₹${sellingInr.toFixed(4)}`;
  if (rateDisp) rateDisp.textContent = `($${sellingUsd.toFixed(4)} USD)`;
  if (arbDisp) arbDisp.textContent = `+${arbitragePct.toFixed(2)}%`;
  if (syncMethodDisp) syncMethodDisp.textContent = "Selling Formula (+45p)";

  // 2. Update Dedicated Nexus Menu Section Elements
  const menuActualInr = document.getElementById('nexusMenuActualInr');
  const menuActualUsd = document.getElementById('nexusMenuActualUsd');
  const menuPremiumInr = document.getElementById('nexusMenuPremiumInr');
  const menuPremiumPaise = document.getElementById('nexusMenuPremiumPaise');
  const menuSellingInr = document.getElementById('nexusMenuSellingInr');
  const menuSellingUsd = document.getElementById('nexusMenuSellingUsd');
  const menuArbitragePct = document.getElementById('nexusMenuArbitragePct');
  const menu30mDiff = document.getElementById('nexusMenu30mDiff');
  const adminPremiumDisplay = document.getElementById('adminPremiumDisplay');

  if (menuActualInr) menuActualInr.textContent = `₹${actualInr.toFixed(4)}`;
  if (menuActualUsd) menuActualUsd.textContent = `($${actualUsd.toFixed(4)} USD)`;
  if (menuPremiumInr) menuPremiumInr.textContent = `+₹${premiumInr.toFixed(2)}`;
  if (menuPremiumPaise) menuPremiumPaise.textContent = `${(premiumInr * 100).toFixed(0)} Paise per coin`;
  if (menuSellingInr) menuSellingInr.textContent = `₹${sellingInr.toFixed(4)}`;
  if (menuSellingUsd) menuSellingUsd.textContent = `($${sellingUsd.toFixed(4)} USD)`;
  if (menuArbitragePct) menuArbitragePct.innerHTML = `<i class="fa-solid fa-arrow-trend-up"></i><span>+${arbitragePct.toFixed(2)}% above spot market</span>`;
  if (adminPremiumDisplay) adminPremiumDisplay.textContent = `${premiumInr.toFixed(2)} INR (${(premiumInr * 100).toFixed(0)} paise)`;

  if (latestAnalysis && menu30mDiff) {
    const diffPct = latestAnalysis.diff_30m_pct || 0.0;
    const diffInr = latestAnalysis.diff_30m_inr || 0.0;
    const sign = diffPct >= 0 ? '+' : '';
    menu30mDiff.textContent = `30m Trend: ${sign}${diffPct.toFixed(2)}% (${sign}₹${Math.abs(diffInr).toFixed(2)})`;
  }

  // 3. Update Calculator
  calculateNexusReturns(actualInr, sellingInr, premiumInr);
}

function calculateNexusReturns(actualInr, sellingInr, premiumInr) {
  if (actualInr === undefined || sellingInr === undefined || premiumInr === undefined) {
    actualInr = latestAnalysis ? latestAnalysis.current_price_inr : 6.66;
    premiumInr = nexusSellingPremiumInr;
    sellingInr = actualInr + premiumInr;
  }
  const inputElem = document.getElementById('nexusCalcHoldingsInput');
  const holdings = parseFloat(inputElem ? inputElem.value : 10000) || 10000;
  
  const regPayout = holdings * actualInr;
  const nexPayout = holdings * sellingInr;
  const extraProfit = holdings * premiumInr;
  
  const regElem = document.getElementById('nexusCalcRegularPayout');
  const nexElem = document.getElementById('nexusCalcTotalPayout');
  const extraElem = document.getElementById('nexusCalcExtraProfit');
  
  if (regElem) regElem.textContent = `₹${regPayout.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (nexElem) nexElem.textContent = `₹${nexPayout.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (extraElem) extraElem.textContent = `+₹${extraProfit.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function switchMainView(viewName) {
  const dashSection = document.getElementById('sectionDashboard');
  const nexusSection = document.getElementById('sectionNexusMenu');
  const tabDash = document.getElementById('navTabDashboard');
  const tabNexus = document.getElementById('navTabNexusMenu');
  
  if (viewName === 'nexus') {
    if (dashSection) dashSection.classList.add('hidden');
    if (nexusSection) nexusSection.classList.remove('hidden');
    if (tabDash) {
      tabDash.className = 'nav-tab px-4 py-2 rounded-xl font-bold transition-all text-slate-300 hover:bg-slate-800/50 border border-transparent flex items-center gap-2 whitespace-nowrap';
    }
    if (tabNexus) {
      tabNexus.className = 'nav-tab active px-4 py-2 rounded-xl font-bold transition-all bg-indigo-600/30 text-indigo-300 border border-indigo-500/40 flex items-center gap-2 whitespace-nowrap shadow-sm';
    }
    updateNexusMenuUI();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } else {
    if (nexusSection) nexusSection.classList.add('hidden');
    if (dashSection) dashSection.classList.remove('hidden');
    if (tabDash) {
      tabDash.className = 'nav-tab active px-4 py-2 rounded-xl font-bold transition-all bg-blue-600/30 text-blue-300 border border-blue-500/40 flex items-center gap-2 whitespace-nowrap shadow-sm';
    }
    if (tabNexus) {
      tabNexus.className = 'nav-tab px-4 py-2 rounded-xl font-bold transition-all text-indigo-300 hover:bg-indigo-600/20 border border-transparent hover:border-indigo-500/30 flex items-center gap-2 whitespace-nowrap';
    }
  }
}

function updateSellingLadder(curInr, curUsd) {
  const inputElem = document.getElementById('calcHoldingsInput');
  const holdings = parseFloat(inputElem.value) || 10000;

  const getTierStr = (mult) => {
    const targetInr = curInr * mult;
    const totalInr = targetInr * holdings;
    return `₹${targetInr.toFixed(2)} → ₹${totalInr.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  };

  document.getElementById('ladderCur').textContent = `₹${curInr.toFixed(2)} → ₹${(curInr * holdings).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  document.getElementById('ladder5pct').textContent = getTierStr(1.05);
  document.getElementById('ladder10pct').textContent = getTierStr(1.10);
  document.getElementById('ladder25pct').textContent = getTierStr(1.25);
}

// Chart.js Setup (INR PRIMARY)
function initChart() {
  const ctx = document.getElementById('priceChart').getContext('2d');
  
  priceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'BDX Rate (INR ₹)',
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
          label: '30m Reference Level (INR)',
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
          padding: 10,
          callbacks: {
            label: function(context) {
              return ` ${context.dataset.label}: ₹${parseFloat(context.parsed.y).toFixed(2)}`;
            }
          }
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
              return '₹' + parseFloat(val).toFixed(2);
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

  // Find price ~30 mins ago in INR
  let p30mVal = null;
  for (let i = chartHistoryData.length - 1; i >= 0; i--) {
    if (chartHistoryData[i].timestamp <= target30mAgo) {
      p30mVal = chartHistoryData[i].price_inr;
      break;
    }
  }
  if (!p30mVal && chartHistoryData.length > 0) {
    p30mVal = chartHistoryData[0].price_inr;
  }

  chartHistoryData.forEach(tick => {
    const d = new Date(tick.timestamp * 1000);
    labels.push(d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    prices.push(tick.price_inr);
    ref30m.push(p30mVal);
  });

  priceChart.data.labels = labels;
  priceChart.data.datasets[0].data = prices;
  priceChart.data.datasets[0].label = `BDX Rate (INR ₹)`;
  priceChart.data.datasets[1].data = ref30m;
  priceChart.update();
}

// --------------------------------------------------------------------------
// STANDALONE / GITHUB PAGES BROWSER ENGINE (When no local server is active)
// --------------------------------------------------------------------------
async function fetchDirectMarket() {
  let p_usd = null;
  let p_inr = null;

  // 1. Fetch CoinGecko
  try {
    const cgRes = await fetch("https://api.coingecko.com/api/v3/simple/price?ids=beldex&vs_currencies=usd,inr&include_24hr_vol=true&include_24hr_change=true");
    if (cgRes.ok) {
      const cgData = await cgRes.json();
      if (cgData && cgData.beldex) {
        p_usd = cgData.beldex.usd;
        p_inr = cgData.beldex.inr;
      }
    }
  } catch (e) {}

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
        }
      }
    }
  } catch (e) {}

  if (!p_usd) {
    p_usd = 0.0711;
    p_inr = 6.68;
  }

  const now = Date.now() / 1000;

  // Maintain local history in localStorage
  let stored = [];
  try {
    stored = JSON.parse(localStorage.getItem('bdx_sentinel_ticks') || '[]');
  } catch (e) {}

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

  const pastInr = tick30m ? tick30m.price_inr : p_inr;
  const pastUsd = tick30m ? tick30m.price_usd : p_usd;
  const diffInr = p_inr - pastInr;
  const diffUsd = p_usd - pastUsd;
  const diffPct = ((p_inr - pastInr) / pastInr) * 100.0;

  const holdings = clientSettings.user_holdings_bdx || 10000;
  const isSurge = diffPct >= clientSettings.surge_threshold_percent_30m;
  const isTargetHit = (clientSettings.target_sell_price_inr > 0 && p_inr >= clientSettings.target_sell_price_inr) ||
                      (clientSettings.target_sell_price_usd > 0 && p_usd >= clientSettings.target_sell_price_usd);

  const reasons = [];
  let demandLevel = "NORMAL";
  let recommendation = "HOLD / MONITOR";

  if (isSurge) {
    reasons.push(`30-Min INR Surge: +${diffPct.toFixed(2)}% (+₹${Math.abs(diffInr).toFixed(2)})`);
    demandLevel = diffPct >= (clientSettings.surge_threshold_percent_30m * 2) ? "EXTREME SURGE" : "HIGH DEMAND";
    recommendation = "STRONG SELL OPPORTUNITY";
  }

  if (isTargetHit) {
    reasons.push(`Target INR Price Hit (₹${p_inr.toFixed(2)} >= ₹${clientSettings.target_sell_price_inr.toFixed(2)})!`);
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

  if (latestNexusState.rate_inr) {
    const arb = ((latestNexusState.rate_inr - p_inr) / p_inr) * 100.0;
    latestNexusState.premium_pct_vs_market = arb;
    updateNexusDisplay(latestNexusState);
  }

  if ((isSurge || isTargetHit) && (now - clientSettings.last_alert_time) > (clientSettings.cooldown_minutes * 60)) {
    clientSettings.last_alert_time = now;
    dispatchClientAlerts(analysis);
  }

  return analysis;
}

// Client-side Direct Alert Dispatcher
async function dispatchClientAlerts(analysis) {
  const diffPct = analysis.diff_30m_pct.toFixed(2);
  const curInr = analysis.current_price_inr.toFixed(2);
  const curUsd = analysis.current_price_usd.toFixed(4);
  const holdings = (analysis.holdings_bdx || 10000).toLocaleString();
  const portInr = (analysis.portfolio_value_inr || 0).toLocaleString();

  playAlertChime();
  const channelsDispatched = [];

  // Telegram
  if (clientSettings.telegram_enabled && clientSettings.telegram_bot_token && clientSettings.telegram_chat_id) {
    try {
      const tgText = `🚨 *BELDEX HIGH DEMAND ALERT!*\n\n📈 *30m Surge*: \`+${diffPct}%\`\n💵 *Price*: \`₹${curInr}\` ($${curUsd} USD)\n💼 *Holdings (${holdings} BDX)*: ~₹${portInr}\n\n💡 *Action*: High Demand Peak - Ready to sell on nexus.vin!`;
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
    } catch (e) {}
  }

  // Discord
  if (clientSettings.discord_enabled && clientSettings.discord_webhook_url) {
    try {
      await fetch(clientSettings.discord_webhook_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          embeds: [{
            title: "🚨 BELDEX HIGH DEMAND ALERT (INR)",
            description: `30-Min Surge: **+${diffPct}%**\nRate: **₹${curInr}** ($${curUsd})\nHoldings Value: **₹${portInr}**`,
            color: 0x10B981
          }]
        })
      });
      channelsDispatched.push('discord');
    } catch (e) {}
  }

  if ("Notification" in window && Notification.permission === "granted") {
    new Notification("🚨 Beldex High Demand Alert!", {
      body: `30m Surge: +${diffPct}% | Rate: ₹${curInr}`
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
        <td class="py-2.5 font-mono font-bold text-white">₹${formatPrice(a.current_price_inr, 2)}</td>
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

// WebSocket Connection
function connectWebSocket() {
  if (isStaticHosted) {
    const badge = document.getElementById('wsStatusBadge');
    badge.textContent = 'WEB DEMO (INR)';
    badge.className = 'badge badge-green text-[10px]';
    return;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  try {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      document.getElementById('wsStatusBadge').textContent = 'LIVE FEED (INR)';
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
      } catch (e) {}
    };

    ws.onclose = () => {
      document.getElementById('wsStatusBadge').textContent = 'RECONNECTING';
      document.getElementById('wsStatusBadge').className = 'badge badge-amber text-[10px]';
      setTimeout(connectWebSocket, 3000);
    };
  } catch (e) {}
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

// Test Channel (ADMIN RESTRICTED)
window.testChannel = async function(channel) {
  if (!isAdmin) {
    promptAdminLogin(`Admin login required to test the ${channel.toUpperCase()} notification channel.`);
    return;
  }

  initAudio();
  showToast(`Testing ${channel.toUpperCase()} alert...`, 'info');

  if (isStaticHosted) {
    if (channel === 'telegram') {
      if (!clientSettings.telegram_bot_token || !clientSettings.telegram_chat_id) {
        showToast("⚠️ Configure Telegram Bot Token & Chat ID in Alerts & Config first!", "error");
        return;
      }
      try {
        const text = "🧪 *BELDEX SENTINEL TEST ALERT (Telegram)*\n\n✅ GitHub Pages direct Telegram alerts in INR are working properly!";
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
          body: JSON.stringify({ content: "🧪 **BELDEX SENTINEL**: Discord Webhook test in INR successful!" })
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
      showToast(`📱 Note: ${channel.toUpperCase()} requires the local Python backend with Twilio/SMTP.`, 'info');
      return;
    }
  }

  // Local Backend Mode
  try {
    const res = await fetch('/api/alerts/test', {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${adminToken}`
      },
      body: JSON.stringify({ channel: channel })
    });
    if (res.status === 401 || res.status === 403) {
      promptAdminLogin("Admin session expired. Please re-authenticate.");
      return;
    }
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
  document.getElementById('cfgTargetInr').value = clientSettings.target_sell_price_inr || '';
  document.getElementById('cfgTargetUsd').value = clientSettings.target_sell_price_usd || '';
  document.getElementById('calcHoldingsInput').value = clientSettings.user_holdings_bdx || 10000;
  const cfgNexusPrem = document.getElementById('cfgNexusPremiumInr');
  if (cfgNexusPrem) cfgNexusPrem.value = nexusSellingPremiumInr;

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

// Save Settings Form (ADMIN RESTRICTED)
document.getElementById('settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!isAdmin) {
    promptAdminLogin("Admin login required to modify sentinel settings.");
    return;
  }

  clientSettings.surge_threshold_percent_30m = parseFloat(document.getElementById('cfgSurgePct').value);
  clientSettings.cooldown_minutes = parseInt(document.getElementById('cfgCooldown').value);
  clientSettings.target_sell_price_inr = parseFloat(document.getElementById('cfgTargetInr').value) || 0.0;
  clientSettings.target_sell_price_usd = parseFloat(document.getElementById('cfgTargetUsd').value) || 0.0;
  clientSettings.user_holdings_bdx = parseFloat(document.getElementById('calcHoldingsInput').value) || 10000.0;
  
  const cfgNexusPrem = document.getElementById('cfgNexusPremiumInr');
  if (cfgNexusPrem) {
    nexusSellingPremiumInr = parseFloat(cfgNexusPrem.value) || 0.45;
  }
  updateNexusMenuUI();

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
      const res = await fetch('/api/alerts/config', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${adminToken}`
        },
        body: JSON.stringify(clientSettings)
      });
      if (res.status === 401 || res.status === 403) {
        promptAdminLogin("Admin authentication required.");
        return;
      }
    } catch (e) {}
  }

  showToast('✅ Admin configuration saved successfully!', 'success');
  document.getElementById('settingsModal').classList.remove('active');
  loadConfig();
});

// Setup Bookmarklet Code
function setupBookmarklet() {
  const port = window.location.port || '8080';
  const script = `javascript:(function(){try{var text=document.body.innerText;var bdxMatch=text.match(/(?:BDX|Beldex)[^0-9]*([0-9]+(?:\\.[0-9]+)?)/i);var inrMatch=text.match(/(?:₹|INR|Rs)[^0-9]*([0-9]+(?:\\.[0-9]+)?)/i);var usdMatch=text.match(/(?:\\$|USDT|USD)[^0-9]*([0-9]+(?:\\.[0-9]+)?)/i);var rate_usd=usdMatch?parseFloat(usdMatch[1]):null;var rate_inr=inrMatch?parseFloat(inrMatch[1]):null;if(!rate_usd&&!rate_inr&&bdxMatch){rate_usd=parseFloat(bdxMatch[1]);}if(!rate_usd&&!rate_inr){var promptRate=prompt("Enter current Beldex INR rate seen on Nexus:","7.00");if(promptRate)rate_inr=parseFloat(promptRate);}fetch('http://localhost:${port}/api/nexus/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rate_usd:rate_usd,rate_inr:rate_inr,note:'Nexus Browser Tab ('+window.location.host+')'})}).then(r=>r.json()).then(data=>{alert('✅ Beldex Rate successfully synced to Sentinel Dashboard!\\nRate: ₹'+(rate_inr?rate_inr:rate_usd*93.85));}).catch(err=>{alert('❌ Could not connect to Beldex Sentinel (http://localhost:${port}).\\nMake sure the app is running!');});}catch(e){alert('Error running bookmarklet: '+e.message);}})();`;

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
  checkAdminSession();
  connectWebSocket();
  loadConfig();
  loadAlertsHistory();
  setupBookmarklet();

  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }

  // Polling loop
  if (isStaticHosted) {
    fetchDirectMarket();
    setInterval(fetchDirectMarket, 30000);
  } else {
    fetch('/api/rates/current')
      .then(r => r.json())
      .then(d => updateHeroDisplay(d))
      .catch(() => {
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
        showToast('🔄 Live scan complete! INR prices updated directly from exchanges.', 'success');
      } else {
        const res = await fetch('/api/rates/scan-now', { method: 'POST' });
        if (res.ok) {
          showToast('🔄 Live scan complete! INR prices updated.', 'success');
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

  // Holdings Input
  document.getElementById('calcHoldingsInput').addEventListener('input', () => {
    if (latestAnalysis) {
      updateSellingLadder(latestAnalysis.current_price_inr, latestAnalysis.current_price_usd);
    }
  });

  // -------------------------------------------------------------
  // ADMIN AUTHENTICATION MODAL & LOGOUT
  // -------------------------------------------------------------
  const adminModal = document.getElementById('adminLoginModal');
  const openAdminModal = () => {
    document.getElementById('adminLoginError').classList.add('hidden');
    adminModal.classList.add('active');
  };
  const closeAdminModal = () => adminModal.classList.remove('active');

  document.getElementById('btnAdminLoginToggle').addEventListener('click', () => {
    if (isAdmin) {
      // Logout
      isAdmin = false;
      adminToken = null;
      localStorage.removeItem('beldex_admin_token');
      updateAdminUI();
      showToast('👋 Administrator logged out. Dashboard is in guest view mode.', 'info');
    } else {
      openAdminModal();
    }
  });

  const quickLogoutBtn = document.getElementById('btnQuickLogout');
  if (quickLogoutBtn) {
    quickLogoutBtn.addEventListener('click', () => {
      isAdmin = false;
      adminToken = null;
      localStorage.removeItem('beldex_admin_token');
      updateAdminUI();
      showToast('👋 Administrator logged out.', 'info');
    });
  }

  document.getElementById('btnCloseAdminModal').addEventListener('click', closeAdminModal);

  // Autofill Default Credentials button
  document.getElementById('btnAutoFillAdmin').addEventListener('click', () => {
    document.getElementById('adminUsernameInput').value = DEFAULT_ADMIN_USER;
    document.getElementById('adminPasswordInput').value = DEFAULT_ADMIN_PASS;
    showToast('✨ Credentials filled! Click Log In.', 'info');
  });

  // Admin Login Form Submit
  document.getElementById('adminLoginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const userVal = document.getElementById('adminUsernameInput').value.trim();
    const passVal = document.getElementById('adminPasswordInput').value.trim();
    const errBox = document.getElementById('adminLoginError');
    errBox.classList.add('hidden');

    if (isStaticHosted) {
      // Static validation
      if (userVal === DEFAULT_ADMIN_USER && passVal === DEFAULT_ADMIN_PASS) {
        isAdmin = true;
        adminToken = "admin_session_" + Date.now();
        localStorage.setItem('beldex_admin_token', adminToken);
        updateAdminUI();
        closeAdminModal();
        playAlertChime();
        showToast("🛡️ Administrator authenticated! Full access granted.", "success");
      } else {
        errBox.textContent = "Invalid username or password. Use default admin credentials shown above.";
        errBox.classList.remove('hidden');
      }
      return;
    }

    // Full-stack backend login
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: userVal, password: passVal })
      });
      const data = await res.json();
      if (res.ok && data.token) {
        isAdmin = true;
        adminToken = data.token;
        localStorage.setItem('beldex_admin_token', adminToken);
        updateAdminUI();
        closeAdminModal();
        playAlertChime();
        showToast("🛡️ Administrator authenticated! Full access granted.", "success");
      } else {
        errBox.textContent = data.detail || "Authentication failed. Check credentials.";
        errBox.classList.remove('hidden');
      }
    } catch (err) {
      // Fallback check against default credentials
      if (userVal === DEFAULT_ADMIN_USER && passVal === DEFAULT_ADMIN_PASS) {
        isAdmin = true;
        adminToken = "admin_session_" + Date.now();
        localStorage.setItem('beldex_admin_token', adminToken);
        updateAdminUI();
        closeAdminModal();
        playAlertChime();
        showToast("🛡️ Admin authenticated locally.", "success");
      } else {
        errBox.textContent = "Connection error: " + err.message;
        errBox.classList.remove('hidden');
      }
    }
  });

  // Settings Button (Admin Protected)
  document.getElementById('btnOpenSettings').addEventListener('click', () => {
    if (!isAdmin) {
      promptAdminLogin("Alert configuration and sentinel settings are restricted to Admin alone.");
      return;
    }
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
  const btnManual = document.getElementById('btnManualRate');
  if (btnManual) {
    btnManual.addEventListener('click', () => {
      if (!isAdmin) {
        promptAdminLogin("Overriding nexus rates manually is restricted to Administrator.");
        return;
      }
      openNexus();
    });
  }
  document.getElementById('btnCloseNexusModal').addEventListener('click', () => {
    document.getElementById('nexusModal').classList.remove('active');
  });

  // Manual Rate Submit (Admin Protected)
  document.getElementById('btnSubmitManualRate').addEventListener('click', async () => {
    if (!isAdmin) {
      promptAdminLogin("Only Administrator can override rates manually.");
      return;
    }
    const val = parseFloat(document.getElementById('modalManualRateInput').value);
    if (!val || val <= 0) return;
    const isUsd = val < 2.0;
    const rInr = isUsd ? (val * 93.85) : val;
    const rUsd = isUsd ? val : (val / 93.85);

    latestNexusState.rate_inr = rInr;
    latestNexusState.rate_usd = rUsd;
    latestNexusState.status = 'manual_override';
    latestNexusState.sync_method = 'manual';
    latestNexusState.last_sync_timestamp = Date.now() / 1000;
    latestNexusState.status_detail = `Manual rate configured at ${new Date().toLocaleTimeString()}`;

    if (latestAnalysis) {
      latestNexusState.premium_pct_vs_market = ((rInr - latestAnalysis.current_price_inr) / latestAnalysis.current_price_inr) * 100.0;
    }
    updateNexusDisplay(latestNexusState);

    if (!isStaticHosted) {
      try {
        await fetch('/api/nexus/manual-rate', {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${adminToken}`
          },
          body: JSON.stringify({ rate_inr: rInr, rate_usd: rUsd })
        });
      } catch (e) {}
    }

    showToast('✅ Nexus INR rate updated by Admin!', 'success');
    document.getElementById('nexusModal').classList.remove('active');
  });

  // Simulation Modal (Admin Protected)
  document.getElementById('btnOpenSimulate').addEventListener('click', () => {
    if (!isAdmin) {
      promptAdminLogin("Simulating price surges is restricted to Administrator.");
      return;
    }
    document.getElementById('simulateModal').classList.add('active');
  });
  document.getElementById('btnCloseSimulateModal').addEventListener('click', () => {
    document.getElementById('simulateModal').classList.remove('active');
  });

  document.querySelectorAll('.sim-pct-btn').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.sim-pct-btn').forEach(btn => btn.classList.remove('active', 'border-emerald-500'));
      b.classList.add('active', 'border-emerald-500');
      document.getElementById('simCustomPct').value = b.dataset.pct;
    });
  });

  // Execute Simulation (Admin Protected)
  document.getElementById('btnExecuteSimulation').addEventListener('click', async () => {
    if (!isAdmin) {
      promptAdminLogin("Only Administrator can trigger simulations.");
      return;
    }
    const pct = parseFloat(document.getElementById('simCustomPct').value) || 5.0;
    const trigger = document.getElementById('simTriggerAlertsCheck').checked;
    initAudio();

    if (isStaticHosted || !ws || ws.readyState !== WebSocket.OPEN) {
      const baseInr = latestAnalysis ? latestAnalysis.current_price_inr : 6.68;
      const baseUsd = latestAnalysis ? latestAnalysis.current_price_usd : 0.0711;
      const newInr = baseInr * (1.0 + (pct / 100.0));
      const newUsd = baseUsd * (1.0 + (pct / 100.0));
      const holdings = clientSettings.user_holdings_bdx || 10000;

      const simAnalysis = {
        current_price_usd: newUsd,
        current_price_inr: newInr,
        timestamp: Date.now() / 1000,
        source: 'simulation',
        price_30m_usd: baseUsd,
        price_30m_inr: baseInr,
        diff_30m_usd: newUsd - baseUsd,
        diff_30m_inr: newInr - baseInr,
        diff_30m_pct: pct,
        is_high_demand_surge: true,
        is_target_sell_reached: false,
        alert_reasons: [`Simulated 30-Minute Price Surge: +${pct.toFixed(1)}% (+₹${(newInr - baseInr).toFixed(2)})`],
        demand_level: pct >= 10 ? "EXTREME SURGE" : "HIGH DEMAND",
        recommendation: "STRONG SELL OPPORTUNITY (Spike Detected)",
        holdings_bdx: holdings,
        portfolio_value_usd: newUsd * holdings,
        portfolio_value_inr: newInr * holdings,
        profit_gain_30m_usd: (newUsd - baseUsd) * holdings,
        profit_gain_30m_inr: (newInr - baseInr) * holdings
      };

      playAlertChime();
      showToast(`🔥 Simulated +${pct}% 30m INR Surge! Alerts dispatched.`, 'success');
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
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${adminToken}`
        },
        body: JSON.stringify({ surge_percent: pct, trigger_alerts: trigger })
      });
      if (res.status === 401 || res.status === 403) {
        promptAdminLogin("Admin session required to execute simulation.");
        return;
      }
      const data = await res.json();
      if (data.status === 'simulation_complete') {
        playAlertChime();
        showToast(`🔥 Simulated +${pct}% 30m INR Surge! Alerts triggered.`, 'success');
        document.getElementById('simulateModal').classList.remove('active');
        if (data.analysis) updateHeroDisplay(data.analysis);
        loadAlertsHistory();
      }
    } catch (e) {
      showToast('Simulation failed: ' + e.message, 'error');
    }
  });

  // -------------------------------------------------------------
  // Dedicated Nexus Menu & Navigation Tab Handlers
  // -------------------------------------------------------------
  const btnOpenNexusMenu = document.getElementById('btnOpenNexusMenu');
  if (btnOpenNexusMenu) {
    btnOpenNexusMenu.addEventListener('click', () => switchMainView('nexus'));
  }

  const btnCardOpenNexusMenu = document.getElementById('btnCardOpenNexusMenu');
  if (btnCardOpenNexusMenu) {
    btnCardOpenNexusMenu.addEventListener('click', () => switchMainView('nexus'));
  }

  const btnBackToDashboard = document.getElementById('btnBackToDashboard');
  if (btnBackToDashboard) {
    btnBackToDashboard.addEventListener('click', () => switchMainView('dashboard'));
  }

  const navTabDashboard = document.getElementById('navTabDashboard');
  if (navTabDashboard) {
    navTabDashboard.addEventListener('click', () => switchMainView('dashboard'));
  }

  const navTabNexusMenu = document.getElementById('navTabNexusMenu');
  if (navTabNexusMenu) {
    navTabNexusMenu.addEventListener('click', () => switchMainView('nexus'));
  }

  const navTabLadder = document.getElementById('navTabTargetLadder');
  if (navTabLadder) {
    navTabLadder.addEventListener('click', () => {
      switchMainView('dashboard');
      const ladderElem = document.getElementById('calcHoldingsInput');
      if (ladderElem) ladderElem.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }

  // Nexus Calculator Input Handler
  const nexusCalcInput = document.getElementById('nexusCalcHoldingsInput');
  if (nexusCalcInput) {
    nexusCalcInput.addEventListener('input', () => {
      calculateNexusReturns();
    });
  }

  // Calculator Preset Buttons
  document.querySelectorAll('.btn-calc-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.btn-calc-preset').forEach(b => {
        b.className = 'btn-calc-preset px-2.5 py-1 rounded bg-[#1e293b] hover:bg-indigo-600/30 text-xs font-mono text-slate-300';
      });
      btn.className = 'btn-calc-preset px-2.5 py-1 rounded bg-indigo-600/30 border border-indigo-500/40 text-xs font-mono text-indigo-200 font-bold';
      if (nexusCalcInput) {
        nexusCalcInput.value = btn.dataset.amount;
        calculateNexusReturns();
      }
    });
  });

  // Copy Nexus Selling Price
  const btnCopyNexusPrice = document.getElementById('btnCopyNexusPrice');
  if (btnCopyNexusPrice) {
    btnCopyNexusPrice.addEventListener('click', () => {
      const actualInr = latestAnalysis ? latestAnalysis.current_price_inr : 6.66;
      const sellingInr = (actualInr + nexusSellingPremiumInr).toFixed(4);
      navigator.clipboard.writeText(`₹${sellingInr}`);
      showToast(`Copied Nexus Selling Price (₹${sellingInr}) to clipboard!`, 'success');
    });
  }

  // Open Bookmarklet from Nexus Menu
  const btnOpenBmFromMenu = document.getElementById('btnOpenNexusBookmarkletFromMenu');
  if (btnOpenBmFromMenu) {
    btnOpenBmFromMenu.addEventListener('click', openNexus);
  }

  // Edit Nexus Premium (Admin Only)
  const btnAdminEditNexusPremium = document.getElementById('btnAdminEditNexusPremium');
  if (btnAdminEditNexusPremium) {
    btnAdminEditNexusPremium.addEventListener('click', async () => {
      if (!isAdmin) {
        promptAdminLogin("Editing the Nexus Selling Premium is restricted to Administrator.");
        return;
      }
      const newPrem = prompt("Enter new Nexus selling premium in INR / paise (Default: 0.45 for 45 paise):", nexusSellingPremiumInr.toString());
      if (newPrem !== null) {
        const val = parseFloat(newPrem);
        if (!isNaN(val) && val >= 0) {
          nexusSellingPremiumInr = val;
          updateNexusMenuUI();
          showToast(`✅ Nexus premium updated to ₹${val.toFixed(2)} (+${(val*100).toFixed(0)} paise)`, 'success');
          if (!isStaticHosted && adminToken) {
            try {
              await fetch('/api/nexus/config', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'Authorization': `Bearer ${adminToken}`
                },
                body: JSON.stringify({ nexus_selling_premium_inr: val })
              });
            } catch (e) {}
          }
        }
      }
    });
  }
});
