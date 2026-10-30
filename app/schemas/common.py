"""Shared schema types."""
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

# Money is stored as Decimal but sent to clients as a JSON number with 2 decimals.
Money = Annotated[Decimal, PlainSerializer(lambda v: round(float(v), 2), return_type=float, when_used="json")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    message: str
