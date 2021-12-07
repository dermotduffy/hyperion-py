#!/usr/bin/env python
"""Simple HyperHDR client read demonstration."""

import asyncio

from hyperhdr import client, const

HOST = "hyperhdr"


async def print_brightness() -> None:
    """Print HyperHDR brightness."""

    async with client.HyperHDRClient(HOST) as hyperhdr_client:
        assert hyperhdr_client

        adjustment = hyperhdr_client.adjustment
        assert adjustment

        print("Brightness: %i%%" % adjustment[0][const.KEY_BRIGHTNESS])


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(print_brightness())
