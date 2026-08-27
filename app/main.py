from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.ingestion import ingest_document
from app.rag_engine import answer_query
from app.schemas import QueryRequest, QueryResponse, IngestResponse
from app.vectorstore import get_vector_store

app = FastAPI(
    title="PaySlate RAG Chatbot API",
    description="Custom Retrieval-Augmented Generation system for PaySlate documentation",
    version="1.0.0"
)

# Enable CORS for frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SAMPLE_DOCS_DIR = BASE_DIR / "data" / "payslate_sample_docs"

# Mount static web directory if exists
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Welcome to PaySlate RAG Chatbot API. Visit /docs for OpenAPI documentation."}


@app.post("/api/upload", response_model=IngestResponse)
async def upload_document(
    file: UploadFile = File(...),
    chunk_size: int = Query(default=500, ge=100, le=2000),
    chunk_overlap: int = Query(default=50, ge=0, le=500),
):
    """Upload and ingest a PaySlate document (PDF, TXT, MD)."""
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        result = ingest_document(
            filename=file.filename or "document.txt",
            raw_bytes=raw_bytes,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/query", response_model=QueryResponse)
async def query_payslate_rag(request: QueryRequest):
    """Ask a question about PaySlate documents and receive a grounded answer with citations."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    res = answer_query(query=request.query, top_k=request.top_k or 5)
    return res


@app.get("/api/stats")
async def get_vector_store_stats():
    """Retrieve vector store chunk count and database info."""
    store = get_vector_store()
    return store.get_stats()


@app.delete("/api/clear")
async def clear_vector_store():
    """Clear all document chunks from the vector store."""
    store = get_vector_store()
    store.reset()
    return {"status": "Vector store index cleared successfully."}


@app.on_event("startup")
def auto_ingest_sample_docs():
    """Automatically ingest sample PaySlate docs on startup if vector store is empty."""
    store = get_vector_store()
    stats = store.get_stats()
    chunk_count = stats.get("total_chunks", 0)

    if chunk_count == 0 and SAMPLE_DOCS_DIR.exists():
        print("Auto-ingesting sample PaySlate documentation into vector store...")
        for file_path in SAMPLE_DOCS_DIR.glob("*.*"):
            if file_path.suffix.lower() in [".pdf", ".txt", ".md"]:
                with open(file_path, "rb") as f:
                    content = f.read()
                ingest_document(filename=file_path.name, raw_bytes=content)
        print("Sample PaySlate docs auto-ingested successfully!")
