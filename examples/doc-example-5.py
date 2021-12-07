#!/usr/bin/env python
"""Simple HyperHDR client request demonstration."""

import asyncio
import logging
import sys

from hyperhdr import client

HOST = "hyperhdr"
PRIORITY = 20


async def set_color() -> None:
    """Set red color on HyperHDR."""

    async with client.HyperHDRClient(HOST) as hc:
        assert hc

        if not await hc.async_client_connect():
            logging.error("Could not connect to: %s", HOST)
            return

        if not client.ResponseOK(
            await hc.async_clear(priority=PRIORITY)
        ) or not client.ResponseOK(
            await hc.async_set_color(
                color=[255, 0, 0], priority=PRIORITY, origin=sys.argv[0]
            )
        ):
            logging.error("Could not clear/set_color on: %s", HOST)
            return


logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
asyncio.get_event_loop().run_until_complete(set_color())
