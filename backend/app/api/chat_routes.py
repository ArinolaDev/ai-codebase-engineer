from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ingestion.ingest_service import get_project_path, IngestError
from app.chat.chat_service import answer_question
from app.core.llm_client import LLMError

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5


class SourceOut(BaseModel):
    file_path: str
    symbol_name: str
    start_line: int
    end_line: int


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceOut]


@router.post("/{project_id}/chat", response_model=ChatResponse)
def chat_with_project(project_id: str, payload: ChatRequest):
    try:
        get_project_path(project_id)
    except IngestError as e:
        raise HTTPException(404, str(e))

    try:
        result = answer_question(project_id, payload.question, payload.top_k)
    except LLMError as e:
        raise HTTPException(503, str(e))

    return ChatResponse(**result)