import os
import re
import tempfile
from urllib.parse import urlparse

import pymupdf
import pymupdf4llm
import requests
import trafilatura
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import RequestException as CffiRequestException
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from markitdown import MarkItDown
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import YouTubeTranscriptApiException

router = APIRouter()
md = MarkItDown()
yt_api = YouTubeTranscriptApi()

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HTML_MIME_TYPES = {"text/html", "application/xhtml+xml"}
PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}
YOUTUBE_REGEX = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|embed/|v/|shorts/|live/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)
PREFERRED_TRANSCRIPT_LANGUAGES = [
    "es",
    "es-419",
    "es-ES",
    "en",
    "en-US",
    "en-GB",
    "pt",
    "fr",
    "de",
    "it",
]


def extract_youtube_video_id(url: str) -> str | None:
    match = YOUTUBE_REGEX.search(url)
    return match.group(1) if match else None


def strip_control_characters(text: str) -> str:
    """Remove XML-incompatible control characters that break lxml parsing."""
    return CONTROL_CHARS.sub("", text)


def clean_markdown(content: str) -> str:
    """Normalize line endings, strip control chars, and collapse excessive blank lines."""
    text = strip_control_characters(content).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_first_markdown_heading(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^#{1,3}\s+(.+)$", stripped)
        if match:
            heading = re.sub(r"[*_`~]+", "", match.group(1)).strip()
            if heading:
                return heading[:200]
        break
    return None


def preprocess_html_math(html: str) -> str:
    """Convert common math representations (Wikipedia, KaTeX, MathJax) into LaTeX $$ blocks."""
    soup = BeautifulSoup(strip_control_characters(html), "html.parser")

    for math_el in soup.find_all(class_="mwe-math-element"):
        annotation = math_el.find("annotation", attrs={"encoding": "application/x-tex"})
        if annotation and annotation.string:
            math_el.replace_with(f"$${annotation.string.strip()}$$")
            continue
        img = math_el.find("img")
        alt = img.get("alt") if img else None
        if alt:
            math_el.replace_with(f"$${str(alt).strip()}$$")

    for katex_el in soup.find_all(class_="katex"):
        annotation = katex_el.find(
            "annotation", attrs={"encoding": "application/x-tex"}
        )
        if annotation and annotation.string:
            katex_el.replace_with(f"${annotation.string.strip()}$")

    for script_el in soup.find_all("script", attrs={"type": re.compile(r"^math/tex")}):
        if script_el.string:
            is_display = "mode=display" in (script_el.get("type") or "")
            delim = "$$" if is_display else "$"
            script_el.replace_with(f"{delim}{script_el.string.strip()}{delim}")

    return str(soup)


def fetch_url(url: str) -> cffi_requests.Response:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    return cffi_requests.get(
        url,
        impersonate="chrome",
        headers=headers,
        timeout=25,
        allow_redirects=True,
    )


def fetch_youtube_title(url: str, video_id: str) -> str | None:
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = cffi_requests.get(oembed_url, impersonate="chrome", timeout=10)
        if resp.status_code == 200:
            title = resp.json().get("title")
            if title and str(title).strip():
                return str(title).strip()
    except (CffiRequestException, ValueError, KeyError):
        pass

    try:
        resp = fetch_url(url)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                return str(og_title["content"]).strip()
            if soup.title and soup.title.string:
                return soup.title.string.removesuffix(" - YouTube").strip()
    except (CffiRequestException, ValueError):
        pass

    return None


def extract_youtube(url: str, video_id: str) -> tuple[str | None, str]:
    title = fetch_youtube_title(url, video_id)

    try:
        transcript_list = yt_api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(PREFERRED_TRANSCRIPT_LANGUAGES)
        except YouTubeTranscriptApiException:
            transcript = next(iter(transcript_list))

        fetched = transcript.fetch()
        paragraphs: list[str] = []
        current_chunk: list[str] = []
        current_length = 0

        for snippet in fetched:
            text = snippet.text.replace("\n", " ").strip()
            if not text:
                continue
            current_chunk.append(text)
            current_length += len(text)
            if current_length >= 450 and text.endswith((".", "!", "?", "…")):
                paragraphs.append(" ".join(current_chunk))
                current_chunk = []
                current_length = 0

        if current_chunk:
            paragraphs.append(" ".join(current_chunk))

        content = "\n\n".join(paragraphs)
        if content.strip():
            return title, clean_markdown(content)
    except (YouTubeTranscriptApiException, StopIteration):
        pass

    # Fallback to MarkItDown if transcript API fails
    std_resp = requests.get(url, timeout=15)
    std_resp.raise_for_status()
    result = md.convert(std_resp)
    return title or getattr(result, "title", None), clean_markdown(
        result.text_content or ""
    )


def extract_pdf_bytes(
    pdf_bytes: bytes, fallback_title: str | None = None
) -> tuple[str | None, str]:
    title: str | None = None
    content = ""

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        meta_title = (doc.metadata or {}).get("title")
        if (
            meta_title
            and meta_title.strip()
            and not meta_title.lower().startswith("untitled")
        ):
            title = meta_title.strip()

        raw_md = pymupdf4llm.to_markdown(
            doc,
            use_ocr=False,
            header=False,
            footer=False,
            show_progress=False,
        )
        content = (
            raw_md
            if isinstance(raw_md, str)
            else "\n\n".join(str(chunk.get("text", "")) for chunk in raw_md)
        )

    content = clean_markdown(content)
    if not title:
        title = extract_first_markdown_heading(content) or fallback_title

    return title, content


def extract_html(html: str, url: str) -> tuple[str | None, str]:
    processed_html = preprocess_html_math(html)

    metadata = trafilatura.extract_metadata(processed_html, default_url=url)
    title = metadata.title.strip() if metadata and metadata.title else None

    content = trafilatura.extract(
        processed_html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=True,
        include_formatting=True,
        include_comments=False,
        favor_recall=True,
    )

    if not content or len(content.strip()) < 80:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".html", mode="w", encoding="utf-8"
        ) as tmp:
            tmp.write(processed_html)
            tmp_name = tmp.name
        try:
            fallback = md.convert(tmp_name)
            if fallback.text_content and len(fallback.text_content.strip()) > len(
                (content or "").strip()
            ):
                content = fallback.text_content
            if not title:
                title = getattr(fallback, "title", None)
        finally:
            os.unlink(tmp_name)

    if not title:
        soup = BeautifulSoup(processed_html, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

    content = clean_markdown(content or "")
    if not title:
        title = extract_first_markdown_heading(content)

    return title, content


def extract_generic_file(
    file_bytes: bytes, suffix: str, fallback_title: str | None = None
) -> tuple[str | None, str]:
    if suffix in (".txt", ".md", ".markdown", ".csv"):
        text = file_bytes.decode("utf-8", errors="replace")
        cleaned = clean_markdown(text)
        title = extract_first_markdown_heading(cleaned) or fallback_title
        return title, cleaned

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_name = tmp.name

    try:
        result = md.convert(tmp_name)
        content = clean_markdown(result.text_content or "")
        title = (
            getattr(result, "title", None)
            or extract_first_markdown_heading(content)
            or fallback_title
        )
        return title, content
    finally:
        os.unlink(tmp_name)


def get_response_content_type(response: cffi_requests.Response) -> str:
    return response.headers.get("Content-Type", "").split(";")[0].strip().lower()


def is_html_response(response: cffi_requests.Response) -> bool:
    content_type = get_response_content_type(response)
    if content_type:
        return content_type in HTML_MIME_TYPES
    extension = os.path.splitext(urlparse(str(response.url)).path)[1].lower()
    return extension in ("", ".html", ".htm")


def is_pdf_response(response: cffi_requests.Response) -> bool:
    content_type = get_response_content_type(response)
    if content_type in PDF_MIME_TYPES:
        return True
    extension = os.path.splitext(urlparse(str(response.url)).path)[1].lower()
    return extension == ".pdf" or response.content[:4] == b"%PDF"


@router.post("/extract")
def extract_content(file: UploadFile = File(None), url: str = Form(None)):
    if not file and not url:
        raise HTTPException(status_code=400, detail="Provide a file or a URL")

    try:
        if file:
            filename = file.filename or ""
            stem, suffix = os.path.splitext(filename)
            suffix = suffix.lower()
            fallback_title = stem or None
            file_bytes = file.file.read()

            if suffix == ".pdf" or file_bytes[:4] == b"%PDF":
                title, content = extract_pdf_bytes(
                    file_bytes, fallback_title=fallback_title
                )
            elif suffix in (".html", ".htm"):
                html_text = file_bytes.decode("utf-8", errors="replace")
                title, content = extract_html(html_text, url="")
                title = title or fallback_title
            else:
                title, content = extract_generic_file(
                    file_bytes, suffix=suffix, fallback_title=fallback_title
                )
        else:
            url = url.strip()
            video_id = extract_youtube_video_id(url)
            if video_id:
                title, content = extract_youtube(url, video_id)
            else:
                response = fetch_url(url)
                response.raise_for_status()

                url_path = urlparse(str(response.url)).path
                url_filename = os.path.basename(url_path)
                url_stem, url_suffix = os.path.splitext(url_filename)
                url_suffix = url_suffix.lower()

                if is_pdf_response(response):
                    title, content = extract_pdf_bytes(
                        response.content, fallback_title=url_stem or None
                    )
                elif is_html_response(response):
                    title, content = extract_html(response.text, url=str(response.url))
                else:
                    title, content = extract_generic_file(
                        response.content,
                        suffix=url_suffix or ".bin",
                        fallback_title=url_stem or None,
                    )

        if not content or not content.strip():
            raise HTTPException(
                status_code=422,
                detail="No readable content could be extracted from the provided source",
            )

        return {"title": title, "content": content}
    except HTTPException:
        raise
    except (CffiRequestException, requests.RequestException) as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch the URL: {e}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"File handling error: {e}")
    except (TypeError, ValueError, UnicodeError, RuntimeError) as e:
        raise HTTPException(status_code=500, detail=f"Could not extract content: {e}")
