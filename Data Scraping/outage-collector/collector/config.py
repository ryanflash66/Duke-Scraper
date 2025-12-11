"""Configuration assembly for the outage collector service."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Tuple

import yaml

from .datatypes import BBoxPolicy, ExportFormat, PlacePreset, ResolvedConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_PRESETS_FILE = PROJECT_ROOT / "presets" / "places.yml"

DEFAULTS: Dict[str, Any] = {
    "format": ExportFormat.BOTH,
    "output_dir": PROJECT_ROOT / "data",
    "poll_interval_s": 90.0,
    "max_events_per_cycle": 1000,
    "bbox_policy": BBoxPolicy.CENTROID,
    "padding_km": 0.0,
    "viewport_only": False,
    "rebootstrap_on_401": False,
    "log_level": "INFO",
    "playwright_timeout_s": 120,
    "bootstrap_cache_path": PROJECT_ROOT / ".bootstrap_cache.json",
    "discovery_override_url": None,
}

FALLBACK_PRESETS: Dict[str, Dict[str, Any]] = {
    "raleigh": {
        "jurisdiction": "DEC",
        "center": {"lat": 35.5, "lng": -80.0},
        "radius_km": 400.0,
        "bbox": {
            "min_lat": 25.0,
            "max_lat": 38.0,
            "min_lng": -85.0,
            "max_lng": -75.0,
        },
        "polygon": [
            {"lat": 37.8, "lng": -85.0},
            {"lat": 37.8, "lng": -75.0},
            {"lat": 33.0, "lng": -75.0},
            {"lat": 32.0, "lng": -79.0},
            {"lat": 30.5, "lng": -82.5},
            {"lat": 33.5, "lng": -85.0},
        ],
        "query_params": {},
    }
}

ENV_CASTERS: Dict[str, Any] = {
    "PLACE": str,
    "FORMAT": str,
    "OUTPUT_DIR": str,
    "POLL_INTERVAL_S": float,
    "MAX_EVENTS_PER_CYCLE": int,
    "BBOX_POLICY": str,
    "RADIUS_KM": float,
    "PADDING_KM": float,
    "VIEWPORT_ONLY": "bool",
    "REBOOTSTRAP_ON_401": "bool",
    "JURISDICTION": str,
    "LOG_LEVEL": str,
    "DISCOVERY_URL": str,
    "BOOTSTRAP_CACHE": str,
    "PLAYWRIGHT_TIMEOUT_S": int,
    "DISCOVERY_OVERRIDE_URL": str,
}


def build_cli() -> argparse.ArgumentParser:
    """Construct the top-level CLI for the outage collector."""

    parser = argparse.ArgumentParser(description="Duke Energy outage polling service")

    place_group = parser.add_mutually_exclusive_group()
    place_group.add_argument(
        "--place",
        type=str,
        help="Named place preset to load (e.g. raleigh)",
    )
    place_group.add_argument(
        "--raleigh",
        action="store_true",
        help="Convenience flag identical to --place raleigh",
    )

    parser.add_argument(
        "--format",
        choices=[fmt.value for fmt in ExportFormat],
        help="Export format: csv, geojson, or both",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory for CSV/GeoJSON outputs",
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--max-events-per-cycle",
        type=int,
        help="Maximum number of events to process per poll cycle",
    )
    parser.add_argument(
        "--bbox-policy",
        choices=[policy.value for policy in BBoxPolicy],
        help="Bbox inclusion policy for stage-A filtering",
    )
    parser.add_argument(
        "--radius-km",
        type=float,
        help="Override default radius in kilometres",
    )
    parser.add_argument(
        "--padding-km",
        type=float,
        help="Additional padding in kilometres to apply to the bbox filter",
    )
    parser.add_argument(
        "--viewport-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Restrict results strictly to the viewport (stage-A only)",
    )
    parser.add_argument(
        "--rebootstrap-on-401",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Trigger a re-bootstrap on 401/403 responses during polling",
    )
    parser.add_argument(
        "--jurisdiction",
        type=str,
        help="Override jurisdiction string used when querying the feed",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        help="Logging level for the structured logger",
    )
    parser.add_argument(
        "--discovery-url",
        type=str,
        help="Optional starting URL for Playwright discovery",
    )
    parser.add_argument(
        "--discovery-override-url",
        type=str,
        help="Bypass Playwright and use this outage feed endpoint directly",
    )
    parser.add_argument(
        "--bootstrap-cache",
        type=str,
        help="File path used to persist the last successful discovery template",
    )
    parser.add_argument(
        "--playwright-timeout-s",
        type=int,
        help="Playwright bootstrap timeout in seconds",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to a .env file (defaults to project root .env)",
    )
    parser.add_argument(
        "--presets-file",
        type=str,
        default=None,
        help="Path to YAML file containing place presets",
    )

    return parser


def load_presets(path: Optional[Path] = None) -> Dict[str, PlacePreset]:
    """Load presets from YAML with Python fallback."""

    effective_path = path or DEFAULT_PRESETS_FILE
    loaded: MutableMapping[str, Any] = {}
    if effective_path and effective_path.exists():
        content = effective_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        if not isinstance(data, Mapping):  # pragma: no cover - guardrail
            raise ValueError("Presets file must contain a mapping of places")
        loaded.update({str(key).lower(): value for key, value in data.items()})

    for key, value in FALLBACK_PRESETS.items():
        loaded.setdefault(key.lower(), value)

    return {name: PlacePreset.model_validate(value) for name, value in loaded.items()}


def load_env_file(path: Optional[Path]) -> Dict[str, str]:
    """Parse a dotenv-style file into a dictionary."""

    env: Dict[str, str] = {}
    if not path or not path.exists():
        return env

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def load_environment(env_path: Optional[Path] = None) -> Dict[str, str]:
    """Combine .env values with process environment variables."""

    combined = load_env_file(env_path)
    for key in ENV_CASTERS:
        if key in os.environ:
            combined[key] = os.environ[key]
    return combined


def _parse_bool(value: str) -> bool:
    truthy = {"1", "true", "yes", "on"}
    falsy = {"0", "false", "no", "off"}
    lowered = value.lower()
    if lowered in truthy:
        return True
    if lowered in falsy:
        return False
    msg = f"Unable to parse boolean value from '{value}'"
    raise ValueError(msg)


def normalise_environment(raw_env: Mapping[str, str]) -> Dict[str, Any]:
    """Coerce environment values to their expected Python types."""

    typed: Dict[str, Any] = {}
    for key, caster in ENV_CASTERS.items():
        if key not in raw_env:
            continue
        value = raw_env[key]
        if caster == "bool":
            typed[key] = _parse_bool(value)
        else:
            typed[key] = caster(value)
    return typed


def _cli_or_env(
    cli_value: Any,
    env: Mapping[str, Any],
    env_key: str,
    default: Any,
) -> Any:
    """Resolution helper obeying CLI > env > default."""

    if cli_value is not None:
        return cli_value
    if env_key in env:
        return env[env_key]
    return default


def resolve_config(
    args: argparse.Namespace,
    env: Mapping[str, Any],
    presets: Mapping[str, PlacePreset],
) -> ResolvedConfig:
    """Build a ResolvedConfig using precedence rules."""

    place_cli = getattr(args, "place", None)
    if getattr(args, "raleigh", False):
        place_cli = "raleigh"

    place_source = _cli_or_env(place_cli, env, "PLACE", "raleigh")
    place_key = str(place_source).lower()
    if place_key not in presets:
        available = ", ".join(sorted(presets))
        msg = f"Place preset '{place_key}' was not found. Available presets: {available}"
        raise KeyError(msg)

    preset = presets[place_key]

    format_value = _cli_or_env(getattr(args, "format", None), env, "FORMAT", DEFAULTS["format"])
    if isinstance(format_value, ExportFormat):
        export_format = format_value
    else:
        export_format = ExportFormat(str(format_value).lower())

    bbox_value = _cli_or_env(
        getattr(args, "bbox_policy", None),
        env,
        "BBOX_POLICY",
        DEFAULTS["bbox_policy"],
    )
    if isinstance(bbox_value, BBoxPolicy):
        bbox_policy = bbox_value
    else:
        bbox_policy = BBoxPolicy(str(bbox_value).lower())

    output_dir = Path(
        _cli_or_env(getattr(args, "output_dir", None), env, "OUTPUT_DIR", DEFAULTS["output_dir"])
    )

    poll_interval_s = float(
        _cli_or_env(getattr(args, "poll_interval_s", None), env, "POLL_INTERVAL_S", DEFAULTS["poll_interval_s"])
    )

    max_events_per_cycle = int(
        _cli_or_env(
            getattr(args, "max_events_per_cycle", None),
            env,
            "MAX_EVENTS_PER_CYCLE",
            DEFAULTS["max_events_per_cycle"],
        )
    )

    radius_km = float(
        _cli_or_env(getattr(args, "radius_km", None), env, "RADIUS_KM", preset.radius_km)
    )

    padding_km = float(
        _cli_or_env(getattr(args, "padding_km", None), env, "PADDING_KM", DEFAULTS["padding_km"])
    )

    viewport_only = bool(
        _cli_or_env(getattr(args, "viewport_only", None), env, "VIEWPORT_ONLY", DEFAULTS["viewport_only"])
    )

    rebootstrap_on_401 = bool(
        _cli_or_env(
            getattr(args, "rebootstrap_on_401", None),
            env,
            "REBOOTSTRAP_ON_401",
            DEFAULTS["rebootstrap_on_401"],
        )
    )

    log_level = str(
        _cli_or_env(getattr(args, "log_level", None), env, "LOG_LEVEL", DEFAULTS["log_level"])
    ).upper()

    discovery_url = _cli_or_env(getattr(args, "discovery_url", None), env, "DISCOVERY_URL", None)
    discovery_override_url = _cli_or_env(
        getattr(args, "discovery_override_url", None),
        env,
        "DISCOVERY_OVERRIDE_URL",
        DEFAULTS["discovery_override_url"],
    )

    bootstrap_cache_path = Path(
        _cli_or_env(
            getattr(args, "bootstrap_cache", None),
            env,
            "BOOTSTRAP_CACHE",
            DEFAULTS["bootstrap_cache_path"],
        )
    )

    playwright_timeout_s = int(
        _cli_or_env(
            getattr(args, "playwright_timeout_s", None),
            env,
            "PLAYWRIGHT_TIMEOUT_S",
            DEFAULTS["playwright_timeout_s"],
        )
    )

    jurisdiction = str(
        _cli_or_env(getattr(args, "jurisdiction", None), env, "JURISDICTION", preset.jurisdiction)
    )

    if poll_interval_s <= 0:
        raise ValueError("poll_interval_s must be positive")
    if radius_km <= 0:
        raise ValueError("radius_km must be positive")
    if max_events_per_cycle <= 0:
        raise ValueError("max_events_per_cycle must be positive")
    if padding_km < 0:
        raise ValueError("padding_km cannot be negative")
    if playwright_timeout_s <= 0:
        raise ValueError("playwright_timeout_s must be positive")

    raw_cli = {k: v for k, v in vars(args).items() if not k.startswith("_")}

    return ResolvedConfig(
        place=place_key,
        preset=preset,
        poll_interval_s=poll_interval_s,
        max_events_per_cycle=max_events_per_cycle,
        export_format=export_format,
        output_dir=output_dir,
        bbox_policy=bbox_policy,
        radius_km=radius_km,
        padding_km=padding_km,
        viewport_only=viewport_only,
        rebootstrap_on_401=rebootstrap_on_401,
        log_level=log_level,
        discovery_url=discovery_url,
        discovery_override_url=discovery_override_url,
        bootstrap_cache_path=bootstrap_cache_path,
        playwright_timeout_s=playwright_timeout_s,
        jurisdiction=jurisdiction,
        query_params=dict(preset.query_params),
        raw_cli=raw_cli,
        raw_env=dict(env),
    )


def resolve_runtime_config(
    argv: Optional[Sequence[str]] = None,
    env_path: Optional[Path] = None,
    presets_path: Optional[Path] = None,
) -> Tuple[ResolvedConfig, argparse.Namespace]:
    """End-to-end configuration resolution helper."""

    parser = build_cli()
    args = parser.parse_args(argv)

    effective_env_path = env_path or (
        Path(getattr(args, "env_file")) if getattr(args, "env_file", None) else DEFAULT_ENV_FILE
    )
    raw_env = load_environment(effective_env_path)
    typed_env = normalise_environment(raw_env)

    effective_presets_path = (
        Path(getattr(args, "presets_file"))
        if getattr(args, "presets_file", None)
        else presets_path
        or DEFAULT_PRESETS_FILE
    )
    presets = load_presets(effective_presets_path)

    config = resolve_config(args, typed_env, presets)
    return config, args
