#!/usr/bin/env python
"""Simple HyperHDR client callback demonstration."""

from __future__ import annotations

import asyncio
from typing import Any

from hyperhdr import client

HOST = "hyperhdr"


def callback(json: dict[str, Any]) -> None:
    """Sample callback function."""

    print("Received HyperHDR callback: %s" % json)


async def show_callback() -> None:
    """Show a default callback is called."""

    async with client.HyperHDRClient(HOST, default_callback=callback):
        pass


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(show_callback())
