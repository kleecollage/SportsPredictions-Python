"""Registro único de deportes: main, config, mensaje y parlays derivan de sports_registry."""
from value_bot import config, main
from value_bot import telegram_sender as ts
from value_bot.analysis import parlays
from value_bot.sports_registry import SPORTS, active_sports, sport_keys


def test_every_sport_has_a_pick_source_toggle_and_model_date():
    keys = set(sport_keys())
    assert set(main.PICK_SOURCES) == keys
    assert set(config.SPORTS_ENABLED) == keys
    assert set(config.MODEL_SINCE) == keys


def test_parlay_sports_come_from_the_registry():
    assert parlays.PARLAY_SPORTS == tuple(s.key for s in SPORTS if s.parlay_ok)
    assert "football" not in parlays.PARLAY_SPORTS   # el resolver aún no tiene muestra de fútbol


def test_message_emojis_come_from_the_registry():
    assert ts.SPORT_EMO == {s.key: s.emoji for s in SPORTS}


def test_active_sports_follow_the_toggles(monkeypatch):
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"nba": True, "nfl": False, "football": True})
    assert active_sports() == ["nba", "football"]


def test_message_sections_follow_registry_order():
    pick = {"league": "L", "type": "T", "confidence": 60, "est_odds": 1.9, "value": 0.05,
            "reasoning": "r", "data_quality": "high", "real_odds_source": True}
    picks = {key: [{**pick, "sport": key, "home": f"H {key}", "away": f"A {key}"}] for key in sport_keys()}
    msg = ts.build_message(picks, [])
    positions = [msg.index(f"H {key} vs") for key in sport_keys()]
    assert positions == sorted(positions)
    assert "3 picks generados hoy" in msg


def test_main_reads_toggles_only_through_the_registry():
    # Un segundo SPORTS_ENABLED en main dejaba a los tests (y al código) viendo deportes a medias
    assert not hasattr(main, "SPORTS_ENABLED")


def test_sports_outside_the_registry_are_not_counted():
    pick = {"sport": "mlb", "league": "MLB", "home": "H", "away": "A", "type": "T", "confidence": 60,
            "est_odds": 1.9, "value": 0.05, "reasoning": "r", "data_quality": "high"}
    nfl = {**pick, "sport": "nfl", "league": "NFL"}
    assert "Sin partidos disponibles" in ts.build_message({"mlb": [pick]}, [])
    assert "1 pick generado hoy" in ts.build_message({"mlb": [pick], "nfl": [nfl]}, [])
