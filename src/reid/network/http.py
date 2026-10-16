"""Shared HTTP payload validation for peer and local review endpoints."""
import json

from fastapi import HTTPException


def _reject_constant(value):
    raise ValueError("Nonfinite JSON number")


async def read_json(request, max_bytes):
    """Bound streamed bodies before decoding, and reject nonstandard JSON numbers."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HTTPException(413, "Request too large")
    try:
        return json.loads(data, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(400, "Invalid JSON") from error
