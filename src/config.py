"""Load config.yaml and .env into one settings object."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def league_id(self) -> str:
        return os.environ.get("LEAGUE_ID") or str(self.raw["league"]["league_id"])

    @property
    def game_code(self) -> str:
        return self.raw["league"].get("game_code", "nfl")

    @property
    def expected_num_teams(self) -> int:
        return int(self.raw["league"]["expected_num_teams"])

    @property
    def power_weights(self) -> dict[str, float]:
        return dict(self.raw["power_rankings"]["weights"])

    @property
    def recent_weeks(self) -> int:
        return int(self.raw["power_rankings"].get("recent_weeks", 3))

    @property
    def close_game_margin(self) -> float:
        return float(self.raw["luck"].get("close_game_margin", 10.0))

    @property
    def roster_slots(self) -> dict[str, int]:
        return dict(self.raw["roster_slots"])

    @property
    def flex_eligibility(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.raw.get("flex_eligibility", {}).items()}

    @property
    def non_starting_slots(self) -> list[str]:
        return list(self.raw.get("non_starting_slots", ["BN", "IR"]))

    @property
    def playoffs(self) -> dict[str, Any]:
        return dict(self.raw["playoffs"])

    @property
    def regular_season_weeks(self) -> list[int]:
        return list(self.raw["regular_season"]["weeks"])

    @property
    def num_sims(self) -> int:
        return int(self.raw["simulation"].get("num_sims", 10000))

    @property
    def random_seed(self) -> int | None:
        seed = self.raw["simulation"].get("random_seed")
        return int(seed) if seed is not None else None

    @property
    def sheet_tabs(self) -> dict[str, str]:
        return dict(self.raw["sheets"]["tabs"])

    @property
    def sheet_id(self) -> str | None:
        return os.environ.get("SHEET_ID")


def load_settings(config_path: str | Path = "config.yaml") -> Settings:
    load_dotenv()
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return Settings(raw=raw)
