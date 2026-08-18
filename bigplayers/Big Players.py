"""
Big Players Standalone Execution Script
"""
import os
import sys
import time
import logging

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bigplayers.strategy import run_big_players_screener

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def main():
    print("=" * 75)
    print("🚀 RUNNING BIG PLAYERS SCANNER (LIVE TRADINGVIEW DATA)")
    print("=" * 75)
    budget = 100000
    parts = 5
    results = run_big_players_screener(budget, parts)
    print(f"Total screened candidates: {len(results)}\n")
    for r in results[:8]:
        print(f"[{r['Symbol']:<12}] Price: ₹{r['Price']:<8} | Breakout: {r['Breakout']:<9} | SL: ₹{r['SL']:<7} | MaxQty: {r['MaxQty']:<5} | Risk: ₹{r['RiskRs']}")

if __name__ == "__main__":
    main()
