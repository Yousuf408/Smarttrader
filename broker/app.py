# app.py - Your existing file with Angel One added
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import traceback

# ================================================================
# IMPORT YOUR EXISTING MODULES
# ================================================================

# Your existing Dhan module
try:
    import quantity_calculator as dhan_margin  # Your existing Dhan file
    DHAN_AVAILABLE = True
    print("✅ Dhan module loaded")
except ImportError:
    DHAN_AVAILABLE = False
    print("⚠️ Dhan module not found")

# NEW: Import Angel One module
try:
    import angel_margin_calculator as angel_margin
    ANGEL_AVAILABLE = True
    print("✅ Angel One module loaded")
except ImportError as e:
    ANGEL_AVAILABLE = False
    print(f"⚠️ Angel One module not found: {e}")

app = FastAPI()

# ================================================================
# REQUEST MODEL
# ================================================================

class BrokerConnectRequest(BaseModel):
    broker: str
    client_id: Optional[str] = None
    totp_secret: Optional[str] = None
    pin: Optional[str] = None
    api_key: Optional[str] = None
    password: Optional[str] = None

# ================================================================
# API ENDPOINTS
# ================================================================

@app.post("/api/broker/connect")
async def connect_broker(request: BrokerConnectRequest):
    try:
        print(f"🔌 Connecting to broker: {request.broker}")

        # ===== DHAN CONNECTION =====
        if request.broker == "dhan":
            if not DHAN_AVAILABLE:
                raise HTTPException(status_code=400, detail="Dhan module not available")

            # Your existing Dhan connection logic
            dhan_margin.set_dhan_credentials(
                client_id=request.client_id,
                pin=request.pin,
                totp_secret=request.totp_secret
            )
            # ... your existing Dhan auth logic here ...

            return {
                "connected": True, 
                "broker": "dhan", 
                "client_id_masked": mask_client_id(request.client_id)
            }

        # ===== ANGEL ONE CONNECTION =====
        elif request.broker == "angel":
            if not ANGEL_AVAILABLE:
                raise HTTPException(
                    status_code=400, 
                    detail="Angel One module not available"
                )

            # Set credentials
            angel_margin.set_credentials(
                api_key=request.api_key,
                client_id=request.client_id,
                password=request.password,
                totp_secret=request.totp_secret
            )

            # Authenticate
            auth_result = angel_margin.authenticate()

            if auth_result.get("ok"):
                return {
                    "connected": True,
                    "broker": "angel",
                    "client_id_masked": mask_client_id(request.client_id)
                }
            else:
                raise HTTPException(
                    status_code=401,
                    detail=auth_result.get("error", "Authentication failed")
                )

        else:
            raise HTTPException(
                status_code=400, 
                detail=f"Broker '{request.broker}' not supported"
            )

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

# ================================================================
# STATUS ENDPOINT
# ================================================================

@app.get("/api/broker/status")
async def get_broker_status():
    try:
        # Check Dhan
        dhan_connected = False
        dhan_client_id = ""
        if DHAN_AVAILABLE:
            try:
                if hasattr(dhan_margin, 'DHAN_ACCESS_TOKEN'):
                    dhan_connected = bool(str(dhan_margin.DHAN_ACCESS_TOKEN))
                if hasattr(dhan_margin, 'DHAN_CLIENT_ID'):
                    dhan_client_id = str(dhan_margin.DHAN_CLIENT_ID)
            except:
                pass

        # Check Angel One
        angel_connected = False
        angel_client_id = ""
        if ANGEL_AVAILABLE:
            try:
                angel_connected = angel_margin.is_connected()
                if hasattr(angel_margin, '_CREDS'):
                    angel_client_id = angel_margin._CREDS.get("client_id", "")
            except:
                pass

        if dhan_connected:
            return {
                "connected": True,
                "broker": "dhan",
                "client_id_masked": mask_client_id(dhan_client_id)
            }
        elif angel_connected:
            return {
                "connected": True,
                "broker": "angel",
                "client_id_masked": mask_client_id(angel_client_id)
            }
        else:
            return {"connected": False}

    except Exception as e:
        print(f"❌ Status error: {e}")
        return {"connected": False}

# ================================================================
# DISCONNECT ENDPOINT
# ================================================================

@app.post("/api/broker/disconnect")
async def disconnect_broker():
    try:
        # Disconnect Angel One
        if ANGEL_AVAILABLE:
            angel_margin.disconnect()

        # Disconnect Dhan
        if DHAN_AVAILABLE and hasattr(dhan_margin, 'clear_dhan_credentials'):
            dhan_margin.clear_dhan_credentials()

        return {"status": "disconnected"}
    except Exception as e:
        print(f"❌ Disconnect error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ================================================================
# HELPER FUNCTIONS
# ================================================================

def mask_client_id(client_id):
    if not client_id:
        return "****"
    client_id = str(client_id)
    if len(client_id) <= 4:
        return "****"
    return client_id[:2] + "***" + client_id[-2:]

# ================================================================
# HEALTH CHECK
# ================================================================

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "modules": {
            "dhan": DHAN_AVAILABLE,
            "angel": ANGEL_AVAILABLE
        }
    }

