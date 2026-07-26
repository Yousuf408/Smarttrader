# broker/utils.py
# Helper functions for broker operations

import pandas as pd
import requests
import io
from datetime import datetime, timedelta

# Cache for security ID map (24 hours)
_security_map_cache = None
_security_map_cache_time = None

def get_security_id_map():
    """
    Get Dhan security ID for each symbol.
    Cached for 24 hours.
    """
    global _security_map_cache, _security_map_cache_time
    
    # Check cache
    if _security_map_cache is not None and _security_map_cache_time is not None:
        if datetime.now() - _security_map_cache_time < timedelta(hours=24):
            return _security_map_cache
    
    try:
        # Download instrument master
        response = requests.get(
            "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
            timeout=30
        )
        
        if response.status_code != 200:
            return {}
        
        df = pd.read_csv(io.StringIO(response.text), low_memory=False)
        
        # Find columns
        symbol_col = next((c for c in df.columns if c.upper() == "UNDERLYING_SYMBOL"), None)
        sec_id_col = next((c for c in df.columns if c.upper() == "SECURITY_ID"), None)
        
        if not symbol_col or not sec_id_col:
            return {}
        
        # Filter to NSE Equity
        df = df[df['SEGMENT'].astype(str).str.upper() == "E"]
        df = df[df['SEM_EXM_EXCH_ID'].astype(str).str.upper() == "NSE"]
        
        # Build map
        security_map = {}
        for _, row in df.iterrows():
            symbol = str(row[symbol_col]).strip().upper()
            sec_id = str(row[sec_id_col]).strip()
            if symbol and sec_id:
                security_map[symbol] = sec_id
        
        # Update cache
        _security_map_cache = security_map
        _security_map_cache_time = datetime.now()
        
        return security_map
        
    except Exception as e:
        print(f"Error loading security map: {e}")
        return {}
