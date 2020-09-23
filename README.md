[![PyPi](https://img.shields.io/pypi/v/hyperion-py.svg)](https://pypi.org/project/hyperion-py/)
[![PyPi](https://img.shields.io/pypi/pyversions/hyperion-py.svg)](https://pypi.org/project/hyperion-py/)
[![Build Status](https://travis-ci.com/dermotduffy/hyperion-py.svg?branch=master)](https://travis-ci.com/dermotduffy/hyperion-py)
[![Coverage](https://img.shields.io/codecov/c/github/dermotduffy/hyperion-py)](https://codecov.io/gh/dermotduffy/hyperion-py)

# Hyperion Library

Python library for
[Hyperion-NG](https://github.com/hyperion-project/hyperion.ng). See [JSON
API](https://docs.hyperion-project.org/en/json/) for more details about the
inputs and outputs of this library.

# Usage

## Client API calls

All API calls can be found in
[client.py](https://github.com/dermotduffy/hyperion-py/blob/master/hyperion/client.py).
All async calls start with `async_`.

## Data model philosophy

Whilst not universally true, this library attempts to precisely represent the
data model, API and parameters as defined in the [Hyperion JSON
documentation](https://docs.hyperion-project.org/en/json/). Thus no attempt is
made (intentionally) to present convenient accessors/calls at a finer level of
granularity than the model already supports. This is to ensure the client has a
decent chance at staying functional regardless of underlying data model changes
from the server, and the responsibility to match the changes to the server's
data model (e.g. new Hyperion server features) belong to the caller.

### Connection & Disconnection

   * async_client_connect()
   * async_client_disconnect()

### Native API Calls
|Send request and await response|Send request only|Documentation|
|-------------------------------|-----------------|-------------|
|async_clear|async_send_clear|[Docs](https://docs.hyperion-project.org/en/json/Control.html#clear)|
|async_image_stream_start|async_send_image_stream_start|[Docs](https://docs.hyperion-project.org/en/json/Control.html#live-image-stream)|
|async_image_stream_stop|async_send_image_stream_stop|[Docs](https://docs.hyperion-project.org/en/json/Control.html#live-image-stream)|
|async_is_auth_required|async_send_is_auth_required|[Docs](https://docs.hyperion-project.org/en/json/Authorization.html#authorization-check)|
|async_led_stream_start|async_send_led_stream_start|[Docs](https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream)|
|async_led_stream_stop|async_send_led_stream_stop|[Docs](https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream)|
|async_login|async_send_login|[Docs](https://docs.hyperion-project.org/en/json/Authorization.html#login-with-token)|
|async_logout|async_send_logout|[Docs](https://docs.hyperion-project.org/en/json/Authorization.html#logout)|
|async_request_token|async_send_request_token|[Docs](https://docs.hyperion-project.org/en/json/Authorization.html#request-a-token)|
|async_request_token_abort|async_send_request_token_abort|[Docs](https://docs.hyperion-project.org/en/json/Authorization.html#request-a-token)|
|async_set_adjustment|async_send_set_adjustment|[Docs](https://docs.hyperion-project.org/en/json/Control.html#adjustments)|
|async_set_color|async_send_set_color|[Docs](https://docs.hyperion-project.org/en/json/Control.html#set-color)|
|async_set_component|async_send_set_component|[Docs](https://docs.hyperion-project.org/en/json/Control.html#control-components)|
|async_set_effect|async_send_set_effect|[Docs](https://docs.hyperion-project.org/en/json/Control.html#set-effect)|
|async_set_image|async_send_set_image|[Docs](https://docs.hyperion-project.org/en/json/Control.html#set-image)|
|async_set_led_mapping_type|async_send_set_led_mapping_type|[Docs](https://docs.hyperion-project.org/en/json/Control.html#led-mapping)|
|async_set_sourceselect|async_send_set_sourceselect|[Docs](https://docs.hyperion-project.org/en/json/Control.html#source-selection)|
|async_set_videomode|async_send_set_videomode|[Docs](https://docs.hyperion-project.org/en/json/Control.html#video-mode)|
|async_start_instance|async_send_start_instance|[Docs](https://docs.hyperion-project.org/en/json/Control.html#control-instances)|
|async_stop_instance|async_send_stop_instance|[Docs](https://docs.hyperion-project.org/en/json/Control.html#control-instances)|
|async_switch_instance|async_send_switch_instance|[Docs](https://docs.hyperion-project.org/en/json/Control.html#api-instance-handling)|

Note that the `command` and `subcommand` keys shown in the above linked
documentation will automatically be included in the calls the client sends, and
do not need to be specified.

## Client inputs / outputs

The API parameters and output are all as defined in the [JSON API
documentation](https://docs.hyperion-project.org/en/json/).

## Example usage:

```python
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
```

## Running in the background

A background `asyncio task` runs to process all post-connection inbound data
(e.g. request responses, or subscription updates from state changes on the
server side). This background task must either be started post-connection, or
start (and it will itself establish connection).

Optionally, this background task can call callbacks back to the user.

### Waiting for responses

If the user makes a call that does not have `_send_` in the name (see table
above), the function call will wait for the response and return it to the
caller. This matching of request & response is done via the `tan` parameter. If
not specified, the client will automatically attach a `tan` integer, and this
will be visible in the returned output data. This matching is necessary to
differentiate between responses due to requests, and "spontaneous data" from
subscription updates.

#### Example: Waiting for a response

```python
#!/usr/bin/python
"""Simple Hyperion client request demonstration."""

import asyncio
from hyperion import client

HOST = "hyperion"


async def print_if_auth_required():
    """Print whether auth is required."""

    hc = client.HyperionClient(HOST)
    await hc.async_client_connect()

    result = await hc.async_is_auth_required()
    print("Result: %s" % result)


asyncio.get_event_loop().run_until_complete(print_if_auth_required())
```

Output:

```
Result: {'command': 'authorize-tokenRequired', 'info': {'required': False}, 'success': True, 'tan': 1}
```

### Callbacks

The client can be configured to callback as the Hyperion server reports new
values. There are two classes of callbacks supported:

   * **default_callback**: This callback will be called when a more specific callback is not specified.
   * **callbacks**: A dict of callbacks keyed on the Hyperion subscription 'command' (see [JSON API documentation](https://docs.hyperion-project.org/en/json/))

Callbacks can be specified in the `HyperionClient` constructor
(`default_callback=` or `callbacks=` arguments) or after construction via the
`set_callbacks()` and `set_default_callback()` methods.

As above, the `callbacks` dict is keyed on the relevant Hyperion subscription
`command` (e.g. `components-update`, `priorities-update`). The client also
provides a custom callback with command `connection-update` of the following
form:

```python
{"command": "connection-update",
 "connected": True}
```

This can be used to take special action as the client connects or disconnects from the server.

#### Example: Callbacks

```python
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
```

Output, showing the progression of connection stages:

```
Received Hyperion callback: {'connected': True, 'logged-in': False, 'instance': None, 'loaded-state': False, 'command': 'client-update'}
Received Hyperion callback: {'connected': True, 'logged-in': True, 'instance': None, 'loaded-state': False, 'command': 'client-update'}
Received Hyperion callback: {'connected': True, 'logged-in': True, 'instance': 0, 'loaded-state': False, 'command': 'client-update'}
Received Hyperion callback: {'command': 'serverinfo', ... }
Received Hyperion callback: {'connected': True, 'logged-in': True, 'instance': 0, 'loaded-state': True, 'command': 'client-update'}
```

## ThreadedHyperionClient

A `ThreadedHyperionClient` is also provided as a convenience wrapper to for
non-async code. The `ThreadedHyperionClient` wraps the async calls with
non-async versions (methods are named as shown above, except do not start with
`async_`).

### Waiting for the thread to initialize the client

The thread must be given a chance to initialize the client prior to interaction
with it. This method call will block the caller until the client has been initialized.

   * wait_for_client_init()

### Example use of Threaded client

```python
#!/usr/bin/python
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
```

Output:

```
Brightness: 59%
```

## Exceptions

### Philosophy

HyperionClient strives not to throw an exception regardless of network
circumstances, reconnection will automatically happen in the background.
Exceptions are only raised (intentionally) for instances of likely programmer
error.

### HyperionError

Not directly raised, but other exceptions inherit from this.

### HyperionClientTanNotAvailable

Exception raised if a `tan` parameter is provided to an API call, but that
`tan` parameter is already being used by another in-progress call. Users
should either not specify `tan` at all (and the client library will
automatically manage it in an incremental fashion), or if specified manually,
it is the caller's responsibility to ensure no two simultaneous calls share a
`tan` (as otherwise the client would not be able to match the call to the
response, and this exception will be raised automatically prior to the call).
