import operator
from typing import Annotated, List, TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field


class RetailPilot(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    timestamp: str
    thread_id: str
    channel: str
    role: str
    date: str
    day: str
    review_attempts: int


class ThreadGeneration(BaseModel):
    title: str = Field(...,description="Short title for the chat thread, 3 to 6 words")
