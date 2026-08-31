"""Data contracts between pipeline steps. Every LLM output is validated
against one of these before it is allowed to flow downstream."""

from typing import Literal

from pydantic import BaseModel, Field


class RawReview(BaseModel):
    """One scraped review as it arrives from a Korean source."""
    source: str                # e.g. naver_cafe, gangnam_unni, blog
    author: str
    posted: str                # ISO date as scraped
    text_ko: str               # original Korean, never discarded


class Translation(BaseModel):
    translated_en: str
    source_lang: str = "ko"


class Extraction(BaseModel):
    procedure: str
    clinic_raw: str            # clinic name exactly as written in the review
    price_krw: int | None = None
    sentiment: Literal["positive", "neutral", "negative"]
    red_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ClinicMatch(BaseModel):
    canonical: str             # normalized clinic name, or UNMATCHED
    confidence: float = Field(ge=0.0, le=1.0)
    via: Literal["alias_table", "llm_adjudication", "unmatched"]


class Record(BaseModel):
    """Final emitted record, one per surviving review."""
    source: str
    author: str
    posted: str
    text_ko: str
    translated_en: str
    procedure: str
    clinic: str
    price_krw: int | None
    sentiment: str
    red_flags: list[str]
    trust_score: int           # 0-100, deterministic, reasons included
    trust_reasons: list[str]
    needs_human_review: bool
    duplicate_of: str | None = None
