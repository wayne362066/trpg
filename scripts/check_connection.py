#!/usr/bin/env python3
"""Check that a local or LAN client reaches the same room API."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get_json(url: str, timeout: float) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check TRPG room connectivity")
    parser.add_argument("base_url", help="for example http://192.168.1.50:8787")
    parser.add_argument("player_id", help="for example player-a")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    try:
        health = get_json(f"{base}/health", args.timeout)
        connection = get_json(
            f"{base}/rooms/main/connection?{urlencode({'player_id': args.player_id})}", args.timeout
        )
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"connection failed: {error}")
        return 1

    if not health.get("ok") or not connection.get("connected"):
        print(json.dumps({"health": health, "connection": connection}, ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {
                "connected": True,
                "room_id": connection.get("room_id"),
                "player_id": connection.get("player_id"),
                "api_version": health.get("api_version"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
