from fastapi import APIRouter

router = APIRouter()

@router.get("/status")
def get_backtest_status():
    return {"status": "Backtest engine is ready."}

@router.post("/run")
def run_backtest(config: dict):
    # TODO: Connect to vectorbt-powered backtester
    return {"message": "Backtest started (placeholder).", "config": config}
