from typing import List, Optional, Union
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(..., example="How does PaySlate handle payroll processing?")
    top_k: Optional[int] = Field(default=5, ge=1, le=20)
    use_reranker: Optional[bool] = Field(default=True)


class IngestResponse(BaseModel):
    filename: str
    chunks_indexed: int
    chunk_size: int
    chunk_overlap: int
    status: str


class QueryResponse(BaseModel):
    answer: str
    sources: List[str]
    context_retrieved: int
    error: Optional[str] = None


class StatsResponse(BaseModel):
    store_type: str
    total_chunks: Union[str, int]
