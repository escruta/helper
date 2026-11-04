import os

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

api_key_header = APIKeyHeader(name="ESCRUTA_INTERNAL_API_KEY", auto_error=False)


def verify_token(x_token: str = Security(api_key_header)):
    api_key = os.getenv("ESCRUTA_INTERNAL_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Server Configuration Error")

    if not x_token or x_token != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return x_token
