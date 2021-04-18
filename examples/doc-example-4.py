#!/usr/bin/env python
"""Simple Threaded Hyperion client demonstration."""

from hyperion import client, const

HOST = "hyperion"

if __name__ == "__main__":
    hyperion_client = client.ThreadedHyperionClient(HOST)

    # Start the asyncio loop in a new thread.
    hyperion_client.start()

    # Wait for the client to initialize in the new thread.
    hyperion_client.wait_for_client_init()

    # Connect the client.
    hyperion_client.client_connect()

    print("Brightness: %i%%" % hyperion_client.adjustment[0][const.KEY_BRIGHTNESS])

    # Disconnect the client.
    hyperion_client.client_disconnect()

    # Stop the loop (will stop the thread).
    hyperion_client.stop()

    # Join the created thread.
    hyperion_client.join()
