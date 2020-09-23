#!/usr/bin/python
"""Simple Hyperion client callback demonstration."""

import asyncio
from hyperion import client

HOST = "hyperion"


def callback(json):
    """Sample callback function."""

    print("Received Hyperion callback: %s" % json)


if __name__ == "__main__":
    hyperion_client = client.HyperionClient(HOST, default_callback=callback)
    asyncio.get_event_loop().run_until_complete(hyperion_client.async_client_connect())
    asyncio.get_event_loop().run_forever()
