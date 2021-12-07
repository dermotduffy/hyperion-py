#!/usr/bin/env python
"""Simple Threaded HyperHDR client demonstration."""

from hyperhdr import client, const

HOST = "hyperhdr"

if __name__ == "__main__":
    hyperhdr_client = client.ThreadedHyperHDRClient(HOST)

    # Start the asyncio loop in a new thread.
    hyperhdr_client.start()

    # Wait for the client to initialize in the new thread.
    hyperhdr_client.wait_for_client_init()

    # Connect the client.
    hyperhdr_client.client_connect()

    print("Brightness: %i%%" % hyperhdr_client.adjustment[0][const.KEY_BRIGHTNESS])

    # Disconnect the client.
    hyperhdr_client.client_disconnect()

    # Stop the loop (will stop the thread).
    hyperhdr_client.stop()

    # Join the created thread.
    hyperhdr_client.join()
