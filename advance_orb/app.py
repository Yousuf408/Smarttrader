from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "ok", "message": "Hello World"}

@app.get("/api/strategies/advanceorb")
def get_data():
    return {"strategy": "advanceorb", "data": [{"symbol": "RELIANCE", "price": 2856}]}
