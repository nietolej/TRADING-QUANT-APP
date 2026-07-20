from fastapi import APIRouter

router = APIRouter()

@router.get("/catalog")
def get_data_catalog():
    # TODO: Fetch available data from SQLite/StorageService
    return {"catalog": ["BTC/USDT", "ETH/USDT"]}
