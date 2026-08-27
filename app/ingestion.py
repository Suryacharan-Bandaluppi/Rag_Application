import uuid
import importlib
from io import BytesIO
from typing import List, Dict, Any, Optional

from app.config import CHUNK_OVERLAP, CHUNK_SIZE
from app.embeddings import embed_texts
from app.vectorstore import get_vector_store


class PurePythonTextSplitter:
    """Pure Python text splitter when external langchain package is unavailable."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        self.chunk_size = max(100, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_size - 20))

    def split_text(self, text: str) -> List[str]:
        cleaned_text = text.strip()
        if not cleaned_text:
            return []

        chunks = []
        start = 0
        text_length = len(cleaned_text)

        while start < text_length:
            end = start + self.chunk_size
            if end >= text_length:
                chunks.append(cleaned_text[start:])
                break

            split_at = cleaned_text.rfind("\n\n", start, end)
            if split_at == -1:
                split_at = cleaned_text.rfind("\n", start, end)
            if split_at == -1:
                split_at = cleaned_text.rfind(". ", start, end)
            if split_at == -1:
                split_at = cleaned_text.rfind(" ", start, end)
            if split_at <= start:
                split_at = end

            chunk = cleaned_text[start:split_at].strip()
            if chunk:
                chunks.append(chunk)

            start = max(start + 1, split_at - self.chunk_overlap)

        return chunks


def extract_text_from_bytes(filename: str, raw_bytes: bytes) -> str:
    """Extract plain text from PDF, TXT, Markdown, JSON, or CSV binary streams."""
    fn = filename.lower()
    if fn.endswith(".pdf"):
        try:
            pypdf_mod = importlib.import_module("pypdf")
            PdfReader = getattr(pypdf_mod, "PdfReader")
            reader = PdfReader(BytesIO(raw_bytes))
            extracted_pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    extracted_pages.append(text)
            full_pdf_text = "\n\n".join(extracted_pages)
            if full_pdf_text.strip():
                return full_pdf_text
        except Exception as e:
            print(f"PDF extraction fallback for {filename}: {e}")
        
        return raw_bytes.decode("utf-8", errors="ignore")
    else:
        return raw_bytes.decode("utf-8", errors="ignore")


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into smaller chunks with configurable overlap."""
    try:
        splitters_mod = importlib.import_module("langchain_text_splitters")
        RecursiveCharacterTextSplitter = getattr(splitters_mod, "RecursiveCharacterTextSplitter")
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        return splitter.split_text(text)
    except Exception:
        pass

    splitter = PurePythonTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_text(text)


def ingest_document(filename: str, raw_bytes: bytes, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> Dict[str, Any]:
    """Extract, chunk, embed, and upsert a document into the vector store."""
    full_text = extract_text_from_bytes(filename, raw_bytes)
    if not full_text.strip():
        return {"filename": filename, "chunks_indexed": 0, "status": "Empty document"}

    chunks = chunk_text(full_text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    vectors = embed_texts(chunks)

    doc_ids = [f"{filename}-{uuid.uuid4().hex[:8]}-{i}" for i in range(len(chunks))]
    metadatas = [{"source": filename, "chunk_index": i} for i in range(len(chunks))]

    store = get_vector_store()
    store.upsert(ids=doc_ids, vectors=vectors, metadatas=metadatas, documents=chunks)

    return {
        "filename": filename,
        "chunks_indexed": len(chunks),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "status": "Success"
    }
