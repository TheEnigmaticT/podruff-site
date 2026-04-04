"""Web server module — FastAPI app serving the hook assistant UI and API."""

import asyncio
import json
import os
import time
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

import config
from suggestion_engine import SuggestionStatus, SuggestionStore, SuggestionType

logger = logging.getLogger(__name__)

app = FastAPI(title="Hook Assistant")
_templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=_templates_dir)

# These get set by main.py at startup
_app_state = {
    "audio_capture": None,
    "transcriber": None,
    "hook_generator": None,
    "store": None,  # SuggestionStore
    "recording": False,
    "start_time": None,
    "sse_queues": [],  # list of asyncio.Queue for SSE clients
}


def set_components(transcriber, hook_generator, store: SuggestionStore):
    """Inject shared components from main.py."""
    _app_state["transcriber"] = transcriber
    _app_state["hook_generator"] = hook_generator
    _app_state["store"] = store


def broadcast_suggestion(suggestion):
    """Push a new suggestion to all connected SSE clients."""
    start = _app_state.get("start_time") or time.time()
    data = {
        "id": suggestion.id,
        "type": suggestion.type.value,
        "text": suggestion.text,
        "time": _format_elapsed(suggestion.timestamp - start),
        "timestamp": suggestion.timestamp,
        "status": suggestion.status.value,
    }
    dead_queues = []
    for q in _app_state["sse_queues"]:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead_queues.append(q)
    for q in dead_queues:
        _app_state["sse_queues"].remove(q)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/suggestions")
async def get_suggestions(type: Optional[str] = Query(None)):
    """Get all suggestions, optionally filtered by type (comma-separated: hook,topic,followup)."""
    store: SuggestionStore = _app_state["store"]
    if store is None:
        return []

    type_filter = None
    if type:
        type_filter = []
        for t in type.split(","):
            t = t.strip().lower()
            if t in SuggestionType.__members__:
                type_filter.append(SuggestionType(t))

    suggestions = store.get_all(type_filter=type_filter)
    start = _app_state.get("start_time") or time.time()
    return [
        {
            "id": s.id,
            "type": s.type.value,
            "text": s.text,
            "time": _format_elapsed(s.timestamp - start),
            "timestamp": s.timestamp,
            "status": s.status.value,
        }
        for s in suggestions
    ]


class TagRequest(BaseModel):
    status: str


@app.post("/api/suggestions/{suggestion_id}/tag")
async def tag_suggestion(suggestion_id: str, body: TagRequest):
    """Tag a suggestion as used, good, or bad."""
    store: SuggestionStore = _app_state["store"]
    if store is None:
        raise HTTPException(500, "Store not initialized")

    try:
        new_status = SuggestionStatus(body.status.lower())
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.status}. Use: used, good, bad, new")

    result = store.tag(suggestion_id, new_status)
    if result is None:
        raise HTTPException(404, f"Suggestion {suggestion_id} not found")

    # If tagged BAD, trigger replacement generation
    if new_status == SuggestionStatus.BAD:
        _trigger_replacement(result)

    return {"id": result.id, "status": result.status.value}


def _trigger_replacement(suggestion):
    """Generate one replacement suggestion of the same type when one is marked BAD."""
    import threading
    store: SuggestionStore = _app_state["store"]
    if store is None:
        return
    gen = store.get_generator(suggestion.type)
    if gen is None:
        return
    trans = _app_state["transcriber"]
    if trans is None:
        return

    def _do_replacement():
        transcript = trans.get_buffer_text()
        if transcript.strip():
            new_suggestions = gen.generate(transcript)
            for s in new_suggestions:
                broadcast_suggestion(s)

    threading.Thread(target=_do_replacement, daemon=True).start()


# Legacy endpoint — backwards compat
@app.get("/api/hooks")
async def get_hooks():
    gen = _app_state["hook_generator"]
    if gen is None:
        return []
    hooks = gen.get_hooks()
    start = _app_state.get("start_time") or time.time()
    return [
        {
            "id": h.id,
            "text": h.text,
            "time": _format_elapsed(h.timestamp - start),
            "timestamp": h.timestamp,
            "status": h.status.value,
        }
        for h in hooks
    ]


@app.get("/api/transcript")
async def get_transcript():
    trans = _app_state["transcriber"]
    if trans is None:
        return {"text": ""}
    return {"text": trans.get_buffer_text()}


@app.get("/api/status")
async def get_status():
    recording = _app_state["recording"]
    elapsed = 0
    if recording and _app_state["start_time"]:
        elapsed = time.time() - _app_state["start_time"]
    return {
        "recording": recording,
        "elapsed_seconds": elapsed,
        "audio_ok": _app_state["recording"] or _app_state["audio_capture"] is not None,
        "whisper_ok": _app_state["transcriber"] is not None,
        "ollama_ok": _app_state["hook_generator"] is not None,
    }


@app.get("/api/stream")
async def sse_stream():
    """Server-Sent Events stream for real-time suggestion updates."""
    queue = asyncio.Queue(maxsize=100)
    _app_state["sse_queues"].append(queue)

    async def event_generator():
        try:
            # Send initial ping
            yield "event: ping\ndata: connected\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: suggestion\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _app_state["sse_queues"]:
                _app_state["sse_queues"].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/start")
async def start_capture():
    if _app_state["recording"]:
        raise HTTPException(400, "Already recording")

    from main import start_pipeline
    try:
        start_pipeline()
    except Exception as e:
        raise HTTPException(500, str(e))

    _app_state["recording"] = True
    _app_state["start_time"] = time.time()
    return {"status": "started"}


@app.post("/api/stop")
async def stop_capture():
    if not _app_state["recording"]:
        raise HTTPException(400, "Not recording")

    from main import stop_pipeline
    result = stop_pipeline()

    _app_state["recording"] = False
    return {"status": "stopped", "file": result.get("filepath", ""), "analysis": result.get("analysis")}


def _format_elapsed(seconds: float) -> str:
    """Format seconds since start as HH:MM:SS."""
    s = max(0, int(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


# ---------- standalone test mode ----------
if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
