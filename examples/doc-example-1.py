#!/usr/bin/python
"""Simple Hyperion client read demonstration."""

import asyncio
from hyperion import client, const

HOST = "hyperion"


async def print_brightness():
    """Print Hyperion brightness."""

    hyperion_client = client.HyperionClient(HOST)
    if not await hyperion_client.async_client_connect():
        return
    print("Brightness: %i%%" % hyperion_client.adjustment[0][const.KEY_BRIGHTNESS])


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(print_brightness())
