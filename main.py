from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import ROOT_DIR
from .schemas import FeedbackRequest, TriageRequest, TriageResponse
from .storage import LocalStore
from .service import copilot

store = LocalStore()

app = FastAPI(
    title="Grand Mobile Support Copilot",
    description="Support ticket triage with rule-grounded RAG and safe draft replies.",
    version="1.0.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home() -> str:
    page = STATIC_DIR / "index.html"
    return page.read_text(encoding="utf-8") if page.exists() else "<h1>Support Copilot API</h1><a href='/docs'>API docs</a>"


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "rules_loaded": len(copilot.rules.documents),
        "taxonomy_categories_loaded": len(copilot.taxonomy.categories),
        "llm_enabled": copilot.config.llm_enabled,
    }


@app.post("/triage", response_model=TriageResponse)
def triage(payload: TriageRequest) -> TriageResponse:
    try:
        result = copilot.triage(payload)
        try:
            store.add_ticket(result.model_dump(), payload.kind)
        except Exception:
            # Persistence is auxiliary; triage response must remain available.
            pass
        return result
    except Exception as exc:  # Keep the HTTP service available and log a concise server-side error.
        raise HTTPException(status_code=500, detail=f"triage_failed: {type(exc).__name__}") from exc


@app.post("/admin/reload-knowledge")
def reload_knowledge() -> dict:
    return copilot.reload_knowledge()


@app.get("/admin/queue")
def admin_queue(limit: int = 50) -> dict:
    return {"items": store.list_queue(limit)}


@app.post("/admin/feedback")
def admin_feedback(payload: FeedbackRequest) -> dict:
    feedback_id = store.save_feedback(
        ticket_id=payload.ticket_id, action=payload.action,
        original_draft=payload.original_draft, edited_draft=payload.edited_draft, note=payload.note,
    )
    return {"saved": True, "feedback_id": feedback_id}


@app.get("/admin/feedback/stats")
def admin_feedback_stats() -> dict:
    return store.feedback_stats()
