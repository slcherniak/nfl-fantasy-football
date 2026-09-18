"""Yahoo OAuth via yfpy.

Yahoo access tokens expire in ~1 hour; the long-lived refresh token is what
keeps unattended runs alive. yfpy handles the refresh itself as long as it can
read the token and write the refreshed one back, so we point it at a .env file
and let it persist YAHOO_ACCESS_TOKEN_JSON there on every run.

First-time setup (one-time browser dance):
    1. Register an app at developer.yahoo.com (Installed Application,
       Fantasy Sports read permission).
    2. Put YAHOO_CONSUMER_KEY / YAHOO_CONSUMER_SECRET in .env.
    3. Run `python -m src.main --auth-only` and follow the printed URL; paste
       the verifier code back. yfpy writes YAHOO_ACCESS_TOKEN_JSON to .env.

In CI, YAHOO_ACCESS_TOKEN_JSON lives in a GitHub secret and is exported into
the environment; the refresh token inside it does not rotate, so the secret
stays valid indefinitely.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from yfpy.query import YahooFantasySportsQuery

logger = logging.getLogger(__name__)


def build_query(
    league_id: str,
    game_code: str = "nfl",
    game_id: int | None = None,
    env_file_dir: str | Path = ".",
) -> YahooFantasySportsQuery:
    """Create an authenticated yfpy query client.

    Credentials resolve from the environment (YAHOO_CONSUMER_KEY,
    YAHOO_CONSUMER_SECRET, YAHOO_ACCESS_TOKEN_JSON), which python-dotenv has
    already populated from .env when running locally. When no access token
    exists yet, yfpy launches the interactive OAuth flow.
    """
    consumer_key = os.environ.get("YAHOO_CONSUMER_KEY")
    consumer_secret = os.environ.get("YAHOO_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        raise RuntimeError(
            "YAHOO_CONSUMER_KEY / YAHOO_CONSUMER_SECRET are not set. "
            "Copy .env.example to .env and fill them in (see README)."
        )

    env_file_dir = Path(env_file_dir)
    # Only ask yfpy to persist the refreshed token when a .env file exists to
    # write into (local runs). In CI the token comes from a secret and there
    # is nothing to persist.
    persist = (env_file_dir / ".env").exists()

    query = YahooFantasySportsQuery(
        league_id=str(league_id),
        game_code=game_code,
        game_id=game_id,
        yahoo_consumer_key=consumer_key,
        yahoo_consumer_secret=consumer_secret,
        env_var_fallback=True,
        env_file_location=env_file_dir if persist else None,
        save_token_data_to_env_file=persist,
    )
    logger.info("Yahoo API client ready (league %s, game %s)", league_id, game_code)
    return query
