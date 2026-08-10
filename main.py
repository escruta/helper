import os
import re
import shutil
import tempfile
from urllib.parse import urlparse

import requests
from ddgs import DDGS
from ddgs.exceptions import DDGSException
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


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_characters(html: str) -> str:
    """Remove XML-incompatible control characters that break lxml parsing."""
    return CONTROL_CHARS.sub("", html)


HTML_MIME_TYPES = {"text/html", "application/xhtml+xml"}


def is_html_content(response: requests.Response) -> bool:
    """True if the response is an HTML page rather than a standalone document."""
    content_type = (
        response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    )
    if content_type:
        return content_type in HTML_MIME_TYPES
    extension = os.path.splitext(urlparse(response.url).path)[1].lower()
    return extension in ("", ".html", ".htm")


@app.post("/search")
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


@app.post("/extract")
def extract_content(file: UploadFile = File(None), url: str = Form(None)):
    if not file and not url:
        raise HTTPException(status_code=400, detail="Provide a file or a URL")

    try:
        response = None
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

            if is_youtube_url(url) or not is_html_content(response):
                result = md.convert(response)
            else:
                from bs4 import BeautifulSoup
                from readability import Document

                soup = BeautifulSoup(
                    strip_control_characters(response.text), "html.parser"
                )
                for math_el in soup.find_all(class_="mwe-math-element"):
                    img = math_el.find("img")
                    alt = img.get("alt") if img else None
                    if alt:
                        math_el.replace_with(f"$${str(alt).strip()}$$")

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
        if not title:
            if file and file.filename:
                title = os.path.splitext(file.filename)[0]
            elif (
                response is not None
                and not is_youtube_url(url)
                and not is_html_content(response)
            ):
                filename = os.path.basename(urlparse(url).path)
                if filename:
                    title = os.path.splitext(filename)[0]

        return {"title": title, "content": result.text_content}
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch the URL: {e}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"File handling error: {e}")
    except (TypeError, ValueError, UnicodeError) as e:
        raise HTTPException(status_code=500, detail=f"Could not extract content: {e}")
