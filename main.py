from fastapi import Depends, FastAPI

from auth import verify_token
from extract import router as extract_router
from search import router as search_router

app = FastAPI(dependencies=[Depends(verify_token)])

app.include_router(search_router)
app.include_router(extract_router)
