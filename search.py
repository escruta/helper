from ddgs import DDGS
from ddgs.exceptions import DDGSException
from fastapi import APIRouter, Form, HTTPException

router = APIRouter()


@router.post("/search")
def search(query: str = Form(...), max_results: int = Form(10)):
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    if max_results < 1 or max_results > 50:
        raise HTTPException(
            status_code=400, detail="max_results must be between 1 and 50"
        )

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except DDGSException as e:
        raise HTTPException(status_code=502, detail=f"Search provider error: {e}")

    return {
        "results": [
            {"title": r.get("title"), "link": r.get("href"), "snippet": r.get("body")}
            for r in results
        ]
    }
