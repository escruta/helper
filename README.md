# Escruta - Helper

Dedicated microservice for web search and content extraction within the Escruta platform. It combines two capabilities in a single service:

- **Search** (`POST /search`): performs web searches via DDGS and returns structured results (title, link, snippet).
- **Extract** (`POST /extract`): parses files (PDF, DOCX, PPTX, XLSX, TXT, MD, HTML, audio) and URLs (websites, PDFs, YouTube) into clean Markdown using PyMuPDF4LLM, Trafilatura, YouTube Transcript API, and MarkItDown.

Built with Python, FastAPI, Trafilatura, PyMuPDF4LLM, DDGS, and MarkItDown.

> [!IMPORTANT]
> This service is a required component of the Escruta ecosystem. It must be accessible to the Core service for both search and document processing functionality.

## Getting Started

1. `uv sync` - Install dependencies
2. `uv run --env-file .env fastapi run` - Start the development server

The helper service will be available at [localhost:8000](http://localhost:8000). It is consumed by [Core](https://github.com/escruta/core) at this URL (configured via `ESCRUTA_HELPER_URL`).

## Configuration

### Environment Variables

The application is secured and configured using environment variables. These must be set in your `.env` file or environment.

| Variable                   | Description                                           | Default    |
| -------------------------- | ----------------------------------------------------- | ---------- |
| `ESCRUTA_INTERNAL_API_KEY` | Internal API Key for service-to-service communication | (Required) |

## Endpoints

| Method | Path       | Description                                      |
| ------ | ---------- | ------------------------------------------------ |
| POST   | `/search`  | Web search via DDGS (`query`, `max_results`)     |
| POST   | `/extract` | Extract content from `file` or `url` to Markdown |
