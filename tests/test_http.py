"""Boundary checks for streamed JSON used by both HTTP services."""
import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from reid.network.http import read_json


def decode(chunks, limit=64):
    messages = iter([
        {'type': 'http.request', 'body': chunk, 'more_body': i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ])

    async def receive():
        return next(messages)

    return asyncio.run(read_json(Request({'type': 'http'}, receive), limit))


def test_json_accepts_streamed_body_at_limit():
    assert decode([b'{"value":', b'1}'], limit=11) == {'value': 1}


def test_json_limits_total_bytes_across_chunks():
    with pytest.raises(HTTPException) as error:
        decode([b'{"value":', b'1}'], limit=10)
    assert error.value.status_code == 413


@pytest.mark.parametrize('data', [b'', b'{', b'\xff', b'{"n":NaN}', b'{"n":Infinity}', b'{"n":-Infinity}'])
def test_json_rejects_invalid_encodings_and_nonfinite_numbers(data):
    with pytest.raises(HTTPException) as error:
        decode([data])
    assert error.value.status_code == 400
