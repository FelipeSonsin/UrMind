from __future__ import annotations

import json

from app.services.external_sources.__main__ import main


def test_check_is_offline_and_includes_runtime_integrations(capsys) -> None:
    assert main(["check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {entry["name"] for entry in payload} >= {
        "Supabase", "Nominatim", "Open-Meteo", "GeoSampa", "BrasilAPI", "ViaCEP",
    }
    assert {entry["name"] for entry in payload} >= {"Geofabrik", "IBGE SIDRA", "Webots"}
    carto = next(entry for entry in payload if entry["name"] == "CARTO")
    assert carto["status"] == "FRONTEND_CONFIG_UNKNOWN"
    assert carto["configured"] is None
    sidra = next(entry for entry in payload if entry["name"] == "IBGE SIDRA")
    assert sidra["status"] == "TERRITORY_CONFIG_REQUIRED"
    assert sidra["configured"] is False


def test_bulk_commands_require_explicit_destination_and_selection() -> None:
    try:
        main(["download-cnefe", "--destination", "downloads"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("download CNEFE sem selecao territorial deveria ser recusado")
