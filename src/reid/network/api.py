import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response


def create_app(runtime):
    app = FastAPI(title="Classical Re-ID Peer", lifespan=runtime.lifespan, docs_url=None, redoc_url=None)

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > runtime.cfg["network"]["max_body_bytes"]:
                raise HTTPException(413, "Request too large")
        try:
            def reject_constant(value):
                raise ValueError("Nonfinite JSON number")
            return json.loads(data, parse_constant=reject_constant)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Invalid JSON")

    @app.get("/", response_class=HTMLResponse)
    def monitor():
        return (Path(__file__).parents[1] / "visualization" / "monitor.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    def ui_status():
        return runtime.snapshot()

    @app.get("/api/objects")
    def objects():
        return {"observations": runtime.observation_cards(), "identities": runtime.identity_cards(),
                "node_id": runtime.node_id}

    @app.get("/api/metrics")
    def live_metrics():
        return runtime.live_metrics.snapshot()

    @app.post("/api/shutdown")
    def shutdown(request: Request):
        if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(403, 'Shutdown is available only on this machine')
        if runtime.request_shutdown is None:
            raise HTTPException(503, 'Managed shutdown unavailable')
        runtime.request_shutdown()
        return {'stopping': True, 'node_id': runtime.node_id}

    @app.get("/api/thumbnails/{event_id}")
    def thumbnail(event_id: str):
        data = runtime.thumbnails.get(event_id)
        if data is None:
            raise HTTPException(404, "Preview unavailable: older observation, evicted, or no local image")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})

    @app.get("/api/frame")
    def frame():
        with runtime.lock:
            jpeg = runtime.jpeg
        if jpeg is None:
            return Response(status_code=204)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/peer/status")
    def status():
        tip = runtime.db.tip()
        return {"node_id": runtime.node_id, "height": tip["block_index"], "tip_hash": tip["block_hash"],
                "genesis": runtime.db.blocks(0, 1)[0]["block_hash"]}

    @app.get("/peer/blocks")
    def blocks(start: int = 0, limit: int = 32):
        return runtime.db.blocks(max(0, start), max(1, min(128, limit)))

    @app.get("/peer/state")
    def state(after: str = ""):
        if len(after) > 36:
            raise HTTPException(400, "Invalid cursor")
        rows = runtime.db.packets(after, 5)
        page = rows[:4]
        return {"packets": [p for _, p in page], "next_cursor": page[-1][0] if page else "", "has_more": len(rows) > 4}

    @app.post("/peer/transaction")
    async def transaction(request: Request):
        try:
            runtime.receive_packet(await body(request))
            return {"accepted": True}
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error))

    @app.post("/peer/proposal")
    async def proposal(request: Request):
        try:
            return runtime.consensus.vote(await body(request))
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(409, str(error))

    @app.post("/peer/commit")
    async def commit(request: Request):
        try:
            block = await body(request)
            runtime.ledger.accept(block)
            runtime.apply_block(block)
            return {"accepted": True}
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(409, str(error))

    return app
