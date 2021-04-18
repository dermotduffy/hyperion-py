#!/usr/bin/env python
"""Simple Hyperion client read demonstration."""

import asyncio

from hyperion import client, const

HOST = "hyperion"


async def print_brightness() -> None:
    """Print Hyperion brightness."""

    async with client.HyperionClient(HOST) as hyperion_client:
        assert hyperion_client

        adjustment = hyperion_client.adjustment
        assert adjustment

        print("Brightness: %i%%" % adjustment[0][const.KEY_BRIGHTNESS])


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(print_brightness())
