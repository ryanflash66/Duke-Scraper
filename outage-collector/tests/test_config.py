from __future__ import annotations

from pathlib import Path

import pytest

from collector import config
from collector.datatypes import ExportFormat

PRESETS_PATH = Path(__file__).resolve().parents[1] / "presets" / "places.yml"


def _clear_known_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in config.ENV_CASTERS:
        monkeypatch.delenv(key, raising=False)


def test_build_cli_supports_raleigh_flag():
    parser = config.build_cli()
    args = parser.parse_args(["--raleigh"])
    assert args.raleigh is True
    assert getattr(args, "place", None) is None


def test_resolve_runtime_config_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _clear_known_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    resolved, _ = config.resolve_runtime_config(
        argv=[],
        env_path=env_file,
        presets_path=PRESETS_PATH,
    )

    assert resolved.place == "raleigh"
    assert resolved.poll_interval_s == pytest.approx(90.0)
    assert resolved.export_format == ExportFormat.BOTH
    assert resolved.query_params == {}


def test_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _clear_known_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "POLL_INTERVAL_S=120",
                "FORMAT=csv",
                "RADIUS_KM=40",
            ]
        ),
        encoding="utf-8",
    )

    resolved, _ = config.resolve_runtime_config(
        argv=[],
        env_path=env_file,
        presets_path=PRESETS_PATH,
    )

    assert resolved.poll_interval_s == pytest.approx(120.0)
    assert resolved.export_format == ExportFormat.CSV
    assert resolved.radius_km == pytest.approx(40.0)


def test_cli_precedence_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _clear_known_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("POLL_INTERVAL_S=120", encoding="utf-8")

    resolved, _ = config.resolve_runtime_config(
        argv=["--poll-interval-s", "45", "--format", "geojson", "--rebootstrap-on-401"],
        env_path=env_file,
        presets_path=PRESETS_PATH,
    )

    assert resolved.poll_interval_s == pytest.approx(45.0)
    assert resolved.export_format == ExportFormat.GEOJSON
    assert resolved.rebootstrap_on_401 is True
