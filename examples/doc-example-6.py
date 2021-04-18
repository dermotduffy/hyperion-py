#!/usr/bin/env python
"""Simple Hyperion client request demonstration."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from hyperion import client

HOST = "hyperion"
PRIORITY = 20


async def instance_start_and_switch() -> None:
    """Wait for an instance to start."""

    instance_ready = asyncio.Event()

    def instance_update(json: dict[str, Any]) -> None:
        for data in json["data"]:
            if data["instance"] == 1 and data["running"]:
                instance_ready.set()

    async with client.HyperionClient(
        HOST, callbacks={"instance-update": instance_update}
    ) as hc:
        assert hc

        if not client.ResponseOK(await hc.async_start_instance(instance=1)):
            logging.error("Could not start instance on: %s", HOST)
            return

        # Blocks waiting for the instance to start.
        await instance_ready.wait()

        if not client.ResponseOK(await hc.async_switch_instance(instance=1)):
            logging.error("Could not switch instance on: %s", HOST)
            return


logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
asyncio.get_event_loop().run_until_complete(instance_start_and_switch())
