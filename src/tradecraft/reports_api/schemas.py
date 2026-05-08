from __future__ import annotations

from pydantic import BaseModel, Field


class CrawlOnceRequest(BaseModel):
    sync_rag: bool = Field(default=True)


class RAGSyncRequest(BaseModel):
    limit: int | None = Field(default=None)
    force: bool = Field(default=False)
    metadata_only: bool = Field(default=False)
    prune_orphans: bool = Field(default=False)


class SymbolRefreshRequest(BaseModel):
    as_of: str = Field(default="")


class ReportFiltersPayload(BaseModel):
    query: str = Field(default="")
    symbol: str = Field(default="")
    category: str = Field(default="")
    broker: str = Field(default="")
    analyst: str = Field(default="")
    date_from: str = Field(default="")
    date_to: str = Field(default="")
    limit: int = Field(default=20, ge=1, le=100)


class SavedViewAlertPayload(BaseModel):
    enabled: bool = Field(default=False)
    channel: str = Field(default="telegram")
    target: str = Field(default="")


class SavedViewUpsertRequest(BaseModel):
    view_id: str | None = Field(default=None)
    name: str = Field(min_length=1, max_length=80)
    filters: ReportFiltersPayload = Field(default_factory=ReportFiltersPayload)
    alert: SavedViewAlertPayload = Field(default_factory=SavedViewAlertPayload)


class SavedViewAlertTestRequest(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)
