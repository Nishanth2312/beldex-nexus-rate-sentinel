#!/usr/bin/env python3
"""
Beldex Nexus Rate Sentinel - Cross-Platform CLI
Monitor 30-minute Beldex (BDX) rate differences, track nexus.vin, and dispatch selling alerts.
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings
from app.database import init_db, record_price_tick
from app.services.market_feed import market_feed
from app.services.nexus_client import nexus_client
from app.services.rate_analyzer import rate_analyzer
from app.services.notification import notification_service

COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

async def cmd_status():
    await init_db()
    print(f"\n{COLOR_BOLD}{COLOR_BLUE}=== BELDEX NEXUS RATE SENTINEL STATUS ==={COLOR_RESET}")
    print("Fetching live market prices from Gate.io, KuCoin, MEXC, CoinGecko...")
    snapshot = await market_feed.get_market_snapshot()
    if not snapshot:
        print(f"{COLOR_RED}Failed to fetch live prices from exchanges.{COLOR_RESET}")
        return

    # Persist tick
    await record_price_tick(
        source="composite",
        price_usd=snapshot.composite_price_usd,
        price_inr=snapshot.composite_price_inr,
        volume_24h=snapshot.volume_24h_total,
        high_24h=snapshot.high_24h_max,
        low_24h=snapshot.low_24h_min,
        change_24h_pct=snapshot.average_change_24h_pct
    )

    analysis = await rate_analyzer.analyze_current_rate()
    if not analysis:
        print(f"{COLOR_YELLOW}Price recorded. Need at least 1 historical snapshot for 30m window comparison.{COLOR_RESET}")
        return

    print(f"\n{COLOR_BOLD}Current BDX Rate:{COLOR_RESET} {COLOR_GREEN}₹{analysis.current_price_inr:.2f}{COLOR_RESET} (~${analysis.current_price_usd:.4f} USD)")
    
    # 30m Difference
    diff_pct = analysis.diff_30m_pct
    diff_usd = analysis.diff_30m_usd
    diff_inr = analysis.diff_30m_inr

    if diff_pct is not None:
        color = COLOR_GREEN if diff_pct >= 0 else COLOR_RED
        sign = "+" if diff_pct >= 0 else ""
        print(f"{COLOR_BOLD}30-Min Difference:{COLOR_RESET} {color}{sign}{diff_pct:.2f}%{COLOR_RESET} ({sign}₹{diff_inr:.2f} / {sign}${diff_usd:.4f})")
        print(f"Rate 30 Mins Ago: ₹{analysis.price_30m_inr:.2f} (~${analysis.price_30m_usd:.4f} USD)")
    else:
        print(f"{COLOR_YELLOW}30-Min Difference: Establishing historical baseline...{COLOR_RESET}")

    # Selling Signal
    print(f"\n{COLOR_BOLD}Demand Level:{COLOR_RESET} {analysis.demand_level}")
    print(f"{COLOR_BOLD}Action Signal:{COLOR_RESET} {analysis.recommendation}")
    if analysis.alert_reasons:
        print(f"{COLOR_BOLD}Trigger Reasons:{COLOR_RESET} {' • '.join(analysis.alert_reasons)}")

    # Portfolio
    print(f"\n{COLOR_BOLD}Portfolio Estimation ({analysis.holdings_bdx:,.0f} BDX):{COLOR_RESET}")
    print(f"  Current Value: ₹{analysis.portfolio_value_inr:,.0f} (~${analysis.portfolio_value_usd:,.2f} USD)")
    if analysis.profit_gain_30m_inr is not None:
        p_sign = "+" if analysis.profit_gain_30m_inr >= 0 else ""
        print(f"  30-Min Gain: {p_sign}₹{analysis.profit_gain_30m_inr:,.0f} ({p_sign}${analysis.profit_gain_30m_usd:,.2f})")

    # Exchange breakdown
    print(f"\n{COLOR_BOLD}Live Exchanges:{COLOR_RESET}")
    for src, t in snapshot.sources.items():
        print(f"  • {src.upper():<10}: ₹{t.price_inr:.2f} (${t.price_usd:.4f}) [24h: {t.change_24h_pct:+.2f}%]")

    # Nexus.vin state
    print(f"\n{COLOR_BOLD}Nexus.vin Portal:{COLOR_RESET}")
    print(f"  Status: {nexus_client.state.status} ({nexus_client.state.status_detail})")
    if nexus_client.state.rate_inr:
        print(f"  Nexus Rate: ₹{nexus_client.state.rate_inr:.2f} (~${nexus_client.state.rate_usd:.4f} USD)")
        if nexus_client.state.premium_pct_vs_market:
            print(f"  Arbitrage vs Market: {nexus_client.state.premium_pct_vs_market:+.2f}%")
    print(f"{COLOR_BLUE}=========================================={COLOR_RESET}\n")

async def cmd_monitor(interval_sec: int):
    await init_db()
    print(f"{COLOR_BOLD}{COLOR_GREEN}Starting Beldex Sentinel continuous monitor (Interval: {interval_sec}s)...{COLOR_RESET}")
    print("Press Ctrl+C to stop.\n")
    while True:
        try:
            snapshot = await market_feed.get_market_snapshot()
            if snapshot:
                await record_price_tick(
                    source="composite",
                    price_usd=snapshot.composite_price_usd,
                    price_inr=snapshot.composite_price_inr,
                    volume_24h=snapshot.volume_24h_total,
                    high_24h=snapshot.high_24h_max,
                    low_24h=snapshot.low_24h_min,
                    change_24h_pct=snapshot.average_change_24h_pct
                )
                analysis = await rate_analyzer.analyze_current_rate()
                if analysis:
                    diff_str = f"{analysis.diff_30m_pct:+.2f}%" if analysis.diff_30m_pct is not None else "Init"
                    timestamp_str = time.strftime("%H:%M:%S")
                    print(f"[{timestamp_str}] BDX: ${analysis.current_price_usd:.4f} (₹{analysis.current_price_inr:.2f}) | 30m Diff: {diff_str} | Signal: {analysis.recommendation}")
                    
                    if analysis.should_trigger_alert:
                        print(f"{COLOR_BOLD}{COLOR_GREEN}🚨 DISPATCHING HIGH DEMAND RATE ALERTS!{COLOR_RESET}")
                        alert_res = await notification_service.dispatch_rate_alert(analysis.model_dump())
                        print(f"Channels notified: {alert_res.get('channels_succeeded')}")
            await asyncio.sleep(interval_sec)
        except KeyboardInterrupt:
            print(f"\n{COLOR_YELLOW}Monitor stopped by user.{COLOR_RESET}")
            break
        except Exception as e:
            print(f"{COLOR_RED}Error: {e}{COLOR_RESET}")
            await asyncio.sleep(interval_sec)

async def cmd_test_alert(channel: str):
    await init_db()
    print(f"Sending test alert to {channel.upper()}...")
    res = await notification_service.send_test_alert(channel)
    print("Result:", res)

async def cmd_simulate(surge_pct: float, trigger: bool):
    await init_db()
    print(f"Simulating +{surge_pct:.1f}% 30-minute surge...")
    snapshot = await market_feed.get_market_snapshot()
    cur_usd = snapshot.composite_price_usd if snapshot else 0.0710
    now = time.time()
    
    # 30m ago tick
    await record_price_tick(source="sim_base", price_usd=cur_usd, price_inr=cur_usd*93.85, timestamp=now-1800)
    
    # New high price
    new_usd = cur_usd * (1.0 + (surge_pct / 100.0))
    await record_price_tick(source="composite", price_usd=new_usd, price_inr=new_usd*93.85, timestamp=now)
    
    analysis = await rate_analyzer.analyze_current_rate(override_usd=new_usd)
    print(f"Simulated Price: ${new_usd:.4f} (30m Diff: +{analysis.diff_30m_pct:.2f}%)")
    print(f"Recommendation: {analysis.recommendation}")

    if trigger and analysis:
        print("Dispatching alerts...")
        res = await notification_service.dispatch_rate_alert(analysis.model_dump())
        print("Dispatched:", res)

def main():
    parser = argparse.ArgumentParser(description="Beldex Nexus Rate Sentinel CLI")
    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", help="Get current BDX rates, 30m diff and selling signals")

    # monitor
    mon_p = subparsers.add_parser("monitor", help="Run 24/7 continuous rate monitor in terminal")
    mon_p.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")

    # test-alert
    test_p = subparsers.add_parser("test-alert", help="Send a test notification")
    test_p.add_argument("--channel", choices=["telegram", "email", "sms", "discord"], required=True)

    # simulate
    sim_p = subparsers.add_parser("simulate", help="Simulate a 30m price surge")
    sim_p.add_argument("--surge", type=float, default=6.5, help="Surge percentage (default: 6.5)")
    sim_p.add_argument("--trigger", action="store_true", default=True, help="Trigger alerts")

    # nexus-rate
    nex_p = subparsers.add_parser("nexus-rate", help="Set manual nexus.vin rate")
    nex_p.add_argument("--usd", type=float, help="Rate in USD")
    nex_p.add_argument("--inr", type=float, help="Rate in INR")

    args = parser.parse_args()

    if args.command == "status":
        asyncio.run(cmd_status())
    elif args.command == "monitor":
        asyncio.run(cmd_monitor(args.interval))
    elif args.command == "test-alert":
        asyncio.run(cmd_test_alert(args.channel))
    elif args.command == "simulate":
        asyncio.run(cmd_simulate(args.surge, args.trigger))
    elif args.command == "nexus-rate":
        nexus_client.set_manual_rate(args.usd, args.inr)
        print("Nexus rate set to:", nexus_client.state.rate_usd)
    else:
        # Default: status
        asyncio.run(cmd_status())

if __name__ == "__main__":
    main()
