from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from data_layer.storage import init_db
from reconciliation.api import router as reconciliation_router

init_db()

app = FastAPI(
    title="Trading Quant Bots API",
    description="Backend API dedicado a los bots de operacion sobre Binance (spot, futuros, derivados, opciones y P2P).",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reconciliation_router, prefix="/api/reconciliation", tags=["Reconciliation"])

@app.get("/api")
def read_root():
    return {"message": "Trading Quant Bots API is running"}

# Initialize NiceGUI (interfaz reducida solo con los modulos de bots Binance)
from web_gui.main_bots import create_gui_bots
create_gui_bots(app)
