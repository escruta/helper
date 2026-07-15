import os
import re
import shutil
import tempfile

import requests
from ddgs import DDGS
from fastapi import Depends, FastAPI, File, Form, HTTPException, Security, UploadFile
from fastapi.security.api_key import APIKeyHeader
from markitdown import MarkItDown

api_key_header = APIKeyHeader(name="ESCRUTA_INTERNAL_API_KEY", auto_error=False)


def verify_token(x_token: str = Security(api_key_header)):
    api_key = os.getenv("ESCRUTA_INTERNAL_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Server Configuration Error")

    if not x_token or x_token != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return x_token


app = FastAPI(dependencies=[Depends(verify_token)])
md = MarkItDown()


def is_youtube_url(url: str) -> bool:
    pattern = r"^(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/(watch\?v=|embed/|v/|shorts/)?([^&\n?#]+)"
    return bool(re.match(pattern, url, re.IGNORECASE))


@app.post("/search")
async def search(query: str = Form(...), max_results: int = Form(10)):
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    if max_results < 1 or max_results > 50:
        raise HTTPException(
            status_code=400, detail="max_results must be between 1 and 50"
        )

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Search provider error: {e}")

    return {
        "results": [
            {"title": r.get("title"), "link": r.get("href"), "snippet": r.get("body")}
            for r in results
        ]
    }


@app.post("/extract")
async def extract_content(file: UploadFile = File(None), url: str = Form(None)):
    if not file and not url:
        raise HTTPException(status_code=400, detail="Provide a file or a URL")

    try:
        if file:
            filename = file.filename or ""
            suffix = os.path.splitext(filename)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                shutil.copyfileobj(file.file, tmp)

            result = md.convert(tmp.name)
            os.unlink(tmp.name)
        else:
            headers = {"User-Agent": "EscrutaExtractorBot/1.0 (+https://escruta.com)"}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            if is_youtube_url(url):
                result = md.convert(response)
            else:
                from readability import Document
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(response.text, "html.parser")
                for math_el in soup.find_all(class_="mwe-math-element"):
                    img = math_el.find("img")
                    if img and img.get("alt"):
                        latex = img.get("alt").strip()
                        math_el.replace_with(f"$${latex}$$")

                doc = Document(str(soup))

                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".html", mode="w", encoding="utf-8"
                ) as tmp:
                    tmp.write(doc.summary())
                    tmp_name = tmp.name

                try:
                    result = md.convert(tmp_name)
                    if not getattr(result, "title", None):
                        result.title = doc.title()
                finally:
                    os.unlink(tmp_name)

        title = getattr(result, "title", None)
        if not title and file and file.filename:
            title = os.path.splitext(file.filename)[0]

        return {"title": title, "content": result.text_content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
