#!/usr/bin/env python
"""Simple Hyperion client callback demonstration."""

from __future__ import annotations

import asyncio
from typing import Any

from hyperion import client

HOST = "hyperion"


def callback(json: dict[str, Any]) -> None:
    """Sample callback function."""

    print("Received Hyperion callback: %s" % json)


async def show_callback() -> None:
    """Show a default callback is called."""

    async with client.HyperionClient(HOST, default_callback=callback):
        pass


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(show_callback())
