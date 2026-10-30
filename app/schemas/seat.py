from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.utils.constants import SeatCategory


class SeatSelection(BaseModel):
    seat_ids: List[int] = Field(min_length=1, max_length=10, description="1 to 10 seat ids")

    @field_validator("seat_ids")
    @classmethod
    def _positive(cls, ids):
        if any(i <= 0 for i in ids):
            raise ValueError("seat ids must be positive integers")
        return ids


class SeatRelease(BaseModel):
    seat_ids: Optional[List[int]] = Field(default=None, description="Omit to release all of your locks for the show")


class SuggestionQuery(BaseModel):
    count: int = Field(ge=1, le=10)
    category: Optional[SeatCategory] = None
