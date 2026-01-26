"""Typed models for the outage collector service."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExportFormat(str, Enum):
    """Available export format options."""

    CSV = "csv"
    GEOJSON = "geojson"
    BOTH = "both"


class BBoxPolicy(str, Enum):
    """Supported strategies for stage-A filtering."""

    CENTROID = "centroid"
    VERTEX = "vertex"
    INTERSECTS = "intersects"


class LatLng(BaseModel):
    """Simple latitude/longitude pair."""

    model_config = ConfigDict(frozen=True)

    lat: float
    lng: float

    @field_validator("lat")
    @classmethod
    def _validate_lat(cls, value: float) -> float:  # noqa: D401, N805
        """Ensure latitude is within valid bounds."""
        if not -90 <= value <= 90:
            msg = f"Latitude must be between -90 and 90 degrees; got {value}"
            raise ValueError(msg)
        return value

    @field_validator("lng")
    @classmethod
    def _validate_lng(cls, value: float) -> float:  # noqa: D401, N805
        """Ensure longitude is within valid bounds."""
        if not -180 <= value <= 180:
            msg = f"Longitude must be between -180 and 180 degrees; got {value}"
            raise ValueError(msg)
        return value


class BoundingBox(BaseModel):
    """Axis-aligned bounding box."""

    model_config = ConfigDict(frozen=True)

    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float

    @model_validator(mode="after")
    def _validate_bounds(self) -> "BoundingBox":  # noqa: D401
        """Ensure min values do not exceed max values."""
        if self.max_lat < self.min_lat:
            msg = "max_lat must be greater than or equal to min_lat"
            raise ValueError(msg)
        if self.max_lng < self.min_lng:
            msg = "max_lng must be greater than or equal to min_lng"
            raise ValueError(msg)
        return self

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return the bounding box as (min_lat, max_lat, min_lng, max_lng)."""
        return (self.min_lat, self.max_lat, self.min_lng, self.max_lng)


class PlacePreset(BaseModel):
    """Configuration payload for a preset geography."""

    model_config = ConfigDict(frozen=True)

    jurisdiction: str
    center: LatLng
    radius_km: float
    bbox: BoundingBox
    polygon: List[LatLng]
    query_params: Dict[str, Any] = Field(default_factory=dict)


class DiscoveryTemplate(BaseModel):
    """HTTP request template captured during discovery."""

    model_config = ConfigDict(frozen=True)

    url: str
    method: str = Field(default="GET")
    headers: Dict[str, str] = Field(default_factory=dict)
    query: Dict[str, Any] = Field(default_factory=dict)
    body: Optional[Any] = None

    def redacted_dict(self) -> Dict[str, Any]:
        """Return a version safe for logging (headers/cookies redacted)."""
        headers = {
            key: "<redacted>" if key.lower() in {"cookie", "authorization"} else value
            for key, value in self.headers.items()
        }
        return {
            "url": self.url,
            "method": self.method,
            "headers": headers,
            "query": self.query,
            "has_body": self.body is not None,
        }


class NormalizedEvent(BaseModel):
    """Normalized outage event record."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    lat: float
    lng: float
    customers_affected: int
    polygon: Optional[List[LatLng]] = None
    cause_code: Optional[str] = None
    cause_description: Optional[str] = None
    jurisdiction: str
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    estimated_restoration_at: Optional[datetime] = None
    first_reported_at: Optional[datetime] = None
    status_text: Optional[str] = None
    zip_code: Optional[str] = None


class ResolvedConfig(BaseModel):
    """Runtime configuration resolved from presets/env/CLI."""

    model_config = ConfigDict(frozen=True)

    place: str
    preset: PlacePreset
    poll_interval_s: float
    max_events_per_cycle: int
    export_format: ExportFormat
    output_dir: Path
    bbox_policy: BBoxPolicy
    radius_km: float
    padding_km: float
    viewport_only: bool
    rebootstrap_on_401: bool
    log_level: str
    discovery_url: Optional[str]
    discovery_override_url: Optional[str]
    bootstrap_cache_path: Path
    playwright_timeout_s: int
    jurisdiction: str
    query_params: Dict[str, Any] = Field(default_factory=dict)
    raw_cli: Dict[str, Any] = Field(default_factory=dict)
    raw_env: Dict[str, Any] = Field(default_factory=dict)

    def redacted_dict(self) -> Dict[str, Any]:
        """Return a sanitized dict for logging."""
        return {
            "place": self.place,
            "jurisdiction": self.jurisdiction,
            "poll_interval_s": self.poll_interval_s,
            "max_events_per_cycle": self.max_events_per_cycle,
            "export_format": self.export_format.value,
            "output_dir": str(self.output_dir),
            "bbox_policy": self.bbox_policy.value,
            "radius_km": self.radius_km,
            "padding_km": self.padding_km,
            "viewport_only": self.viewport_only,
            "rebootstrap_on_401": self.rebootstrap_on_401,
            "log_level": self.log_level,
            "discovery_url": self.discovery_url,
            "discovery_override_url": bool(self.discovery_override_url),
            "bootstrap_cache_path": str(self.bootstrap_cache_path),
            "playwright_timeout_s": self.playwright_timeout_s,
            "query_params": bool(self.query_params),
        }


class PollCycleStats(BaseModel):
    """Runtime stats for reporting through the ops plane."""

    model_config = ConfigDict(frozen=True)

    polled_at: datetime
    events_seen: int
    events_emitted: int
    errors: int
    duration_s: float


class RawPayload(BaseModel):
    """Captured raw payload for archival or replay."""

    model_config = ConfigDict(frozen=True)

    request: DiscoveryTemplate
    response: Dict[str, Any]
