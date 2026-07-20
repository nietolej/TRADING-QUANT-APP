from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import backtest_router, data_router

app = FastAPI(
    title="Trading Quant API",
    description="Backend API for high-performance quantitative trading and backtesting.",
    version="1.0.0"
)

# Configure CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(backtest_router.router, prefix="/api/backtest", tags=["Backtest"])
app.include_router(data_router.router, prefix="/api/data", tags=["Data Catalog"])

@app.get("/api")
def read_root():
    return {"message": "Trading Quant API is running"}

# Initialize NiceGUI over FastAPI
from web_gui.main import create_gui
create_gui(app)

