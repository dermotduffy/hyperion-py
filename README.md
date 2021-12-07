<img src="images/hyperhdrlogo.png"
     alt="HyperHDR logo"
     width="20%"
     align="right"
     style="float: right; margin: 10px 0px 20px 20px;" />

<!-- [![PyPi](https://img.shields.io/pypi/v/hyperhdr-py.svg?style=flat-square)](https://pypi.org/project/hyperhdr-py/)
[![PyPi](https://img.shields.io/pypi/pyversions/hyperhdr-py.svg?style=flat-square)](https://pypi.org/project/hyperhdr-py/)
[![Build Status](https://img.shields.io/github/workflow/status/dermotduffy/hyperhdr-py/Build?style=flat-square)](https://github.com/dermotduffy/hyperhdr-py/actions/workflows/build.yaml)
[![Test Coverage](https://img.shields.io/codecov/c/gh/dermotduffy/hyperhdr-py?style=flat-square)](https://codecov.io/gh/dermotduffy/hyperhdr-py)
[![License](https://img.shields.io/github/license/dermotduffy/hyperhdr-py.svg?style=flat-square)](LICENSE)
[![BuyMeCoffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=flat-square)](https://www.buymeacoffee.com/dermotdu) -->

# WARNING: This library is just beginning development to support HyperHDR and should not be used in production systems

## I am not a Python developer and there is probably lots of stuff that doesn't work right now.. sorry :-/

## Feel free to help if you know Python :-)

# HyperHDR Library

Python library for
[HyperHDR](https://github.com/awawa-dev/HyperHDR). See [JSON
API](https://docs.hyperhdr-project.org/en/json/) for more details about the
inputs and outputs of this library.

# Installation

```bash
pip3 install hyperhdr-py
```

# Usage

## Data model philosophy

Whilst not universally true, this library attempts to precisely represent the
data model, API and parameters as defined in the [HyperHDR JSON
documentation](https://docs.hyperhdr-project.org/en/json/). Thus no attempt is
made (intentionally) to present convenient accessors/calls at a finer level of
granularity than the model already supports. This is to ensure the client has a
decent chance at staying functional regardless of underlying data model changes
from the server, and the responsibility to match the changes to the server's
data model (e.g. new HyperHDR server features) belong to the caller.

### Constructor Arguments

The following arguments may be passed to the `HyperHDRClient` constructor:

|Argument|Type|Default|Description|
|--------|----|-------|-----------|
|host    |`str`||Host or IP to connect to|
|port    |`int`|19444|Port to connect to|
|default_callback|`callable`|None|A callable for HyperHDR callbacks. See [callbacks](#callbacks)|
|callbacks|`dict`|None|A dictionary of callables keyed by the update name. See [callbacks](#callbacks)|
|token|`str`|None|An authentication token|
|instance|`int`|0|An instance id to switch to upon connection|
|origin|`str`|"hyperhdr-py"|An arbitrary string describing the calling application|
|timeout_secs|`float`|5.0|The number of seconds to wait for a server response or connection attempt before giving up. See [timeouts](#timeouts)|
|retry_secs|`float`|30.0|The number of seconds between connection attempts|
|raw_connection|`bool`|False|If True, the connect call will establish the network connection but not attempt to authenticate, switch to the required instance or load state. The client must call `async_client_login` to login, `async_client_switch_instance` to switch to the configured instance and `async_get_serverinfo` to load the state manually. This may be useful if the caller wishes to communicate with the server prior to authentication.|

### Connection, disconnection and client control calls

* `async_client_connect()`: Connect the client.
* `async_client_disconnect()`: Disconnect the client.
* `async_client_login()`: Login a connected client. Automatically called by
     `async_client_connect()` unless the `raw_connection` constructor argument is True.
* `async_client_switch_instance()`: Switch to the configured instance on the HyperHDR
     server. Automatically called by `async_client_connect()` unless the `raw_connection`
     constructor argument is True.

### Native API Calls

All API calls can be found in
[client.py](https://github.com/dermotduffy/hyperhdr-py/blob/master/hyperhdr/client.py).
All async calls start with `async_`.

|Send request and await response|Send request only|Documentation|
|-------------------------------|-----------------|-------------|
|async_clear|async_send_clear|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#clear)|
|async_image_stream_start|async_send_image_stream_start|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#live-image-stream)|
|async_image_stream_stop|async_send_image_stream_stop|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#live-image-stream)|
|async_is_auth_required|async_send_is_auth_required|[Docs](https://docs.hyperhdr-project.org/en/json/Authorization.html#authorization-check)|
|async_led_stream_start|async_send_led_stream_start|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#live-led-color-stream)|
|async_led_stream_stop|async_send_led_stream_stop|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#live-led-color-stream)|
|async_login|async_send_login|[Docs](https://docs.hyperhdr-project.org/en/json/Authorization.html#login-with-token)|
|async_logout|async_send_logout|[Docs](https://docs.hyperhdr-project.org/en/json/Authorization.html#logout)|
|async_request_token|async_send_request_token|[Docs](https://docs.hyperhdr-project.org/en/json/Authorization.html#request-a-token)|
|async_request_token_abort|async_send_request_token_abort|[Docs](https://docs.hyperhdr-project.org/en/json/Authorization.html#request-a-token)|
|async_get_serverinfo|async_send_get_serverinfo|[Docs](https://docs.hyperhdr-project.org/en/json/ServerInfo.html#parts)|
|async_set_adjustment|async_send_set_adjustment|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#adjustments)|
|async_set_color|async_send_set_color|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#set-color)|
|async_set_component|async_send_set_component|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#control-components)|
|async_set_effect|async_send_set_effect|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#set-effect)|
|async_set_image|async_send_set_image|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#set-image)|
|async_set_led_mapping_type|async_send_set_led_mapping_type|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#led-mapping)|
|async_set_sourceselect|async_send_set_sourceselect|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#source-selection)|
|async_set_videomode|async_send_set_videomode|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#video-mode)|
|async_start_instance|async_send_start_instance|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#control-instances)|
|async_stop_instance|async_send_stop_instance|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#control-instances)|
|async_switch_instance|async_send_switch_instance|[Docs](https://docs.hyperhdr-project.org/en/json/Control.html#api-instance-handling)|
|async_sysinfo|async_send_sysinfo|[Docs](https://docs.hyperhdr-project.org/en/json/ServerInfo.html#system-hyperhdr)|

Note that the `command` and `subcommand` keys shown in the above linked
documentation will automatically be included in the calls the client sends, and
do not need to be specified.

## Client inputs / outputs

The API parameters and output are all as defined in the [JSON API
documentation](https://docs.hyperhdr-project.org/en/json/).

## Example usage

```python
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
#!/usr/bin/env python
"""Simple HyperHDR client request demonstration."""

import asyncio

from hyperhdr import client

HOST = "hyperhdr"


async def print_if_auth_required() -> None:
    """Print whether auth is required."""

    hc = client.HyperHDRClient(HOST)
    await hc.async_client_connect()

    result = await hc.async_is_auth_required()
    print("Result: %s" % result)

    await hc.async_client_disconnect()


asyncio.get_event_loop().run_until_complete(print_if_auth_required())
```

Output:

```
Result: {'command': 'authorize-tokenRequired', 'info': {'required': False}, 'success': True, 'tan': 1}
```

#### Example: Sending commands

A slightly more complex example that sends commands (clears the HyperHDR source
select at a given priority, then sets color at that same priority).

```python
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
```

#### Example: Starting and switching instances

The following example will start a stopped instance, wait for it to be ready,
then switch to it. Uses [callbacks](#callbacks), discussed below.

```python
#!/usr/bin/env python
"""Simple HyperHDR client request demonstration."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from hyperhdr import client

HOST = "hyperhdr"
PRIORITY = 20


async def instance_start_and_switch() -> None:
    """Wait for an instance to start."""

    instance_ready = asyncio.Event()

    def instance_update(json: dict[str, Any]) -> None:
        for data in json["data"]:
            if data["instance"] == 1 and data["running"]:
                instance_ready.set()

    async with client.HyperHDRClient(
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
```

<a name="callbacks"></a>

### Callbacks

The client can be configured to callback as the HyperHDR server reports new
values. There are two classes of callbacks supported:

* **default_callback**: This callback will be called when a more specific callback is not specified.
* **callbacks**: A dict of callbacks keyed on the HyperHDR subscription 'command' (see [JSON API documentation](https://docs.hyperhdr-project.org/en/json/))

Callbacks can be specified in the `HyperHDRClient` constructor
(`default_callback=` or `callbacks=` arguments) or after construction via the
`set_callbacks()` and `set_default_callback()` methods.

As above, the `callbacks` dict is keyed on the relevant HyperHDR subscription
`command` (e.g. `components-update`, `priorities-update`). The client also
provides a custom callback with command `client-update` of the following
form:

```python
{"command": "client-update",
 "connected": True,
 "logged-in": True,
 "instance": 0,
 "loaded-state": True}
```

This can be used to take special action as the client connects or disconnects from the server.

#### Example: Callbacks

```python
#!/usr/bin/env python
"""Simple HyperHDR client callback demonstration."""

from __future__ import annotations

import asyncio
from typing import Any

from hyperhdr import client

HOST = "hyperhdr"


def callback(json: dict[str, Any]) -> None:
    """Sample callback function."""

    print("Received HyperHDR callback: %s" % json)


async def show_callback() -> None:
    """Show a default callback is called."""

    async with client.HyperHDRClient(HOST, default_callback=callback):
        pass


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(show_callback())
```

Output, showing the progression of connection stages:

```
Received HyperHDR callback: {'connected': True, 'logged-in': False, 'instance': None, 'loaded-state': False, 'command': 'client-update'}
Received HyperHDR callback: {'connected': True, 'logged-in': True, 'instance': None, 'loaded-state': False, 'command': 'client-update'}
Received HyperHDR callback: {'connected': True, 'logged-in': True, 'instance': 0, 'loaded-state': False, 'command': 'client-update'}
Received HyperHDR callback: {'command': 'serverinfo', ... }
Received HyperHDR callback: {'connected': True, 'logged-in': True, 'instance': 0, 'loaded-state': True, 'command': 'client-update'}
```

## ThreadedHyperHDRClient

A `ThreadedHyperHDRClient` is also provided as a convenience wrapper to for
non-async code. The `ThreadedHyperHDRClient` wraps the async calls with
non-async versions (methods are named as shown above, except do not start with
`async_`).

### Waiting for the thread to initialize the client

The thread must be given a chance to initialize the client prior to interaction
with it. This method call will block the caller until the client has been initialized.

* wait_for_client_init()

### Example use of Threaded client

```python
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
```

Output:

```
Brightness: 59%
```

## Exceptions / Errors

### Philosophy

HyperHDRClient strives not to throw an exception regardless of network
circumstances, reconnection will automatically happen in the background.
Exceptions are only raised (intentionally) for instances of likely programmer
error.

### HyperHDRError

Not directly raised, but other exceptions inherit from this.

### HyperHDRClientTanNotAvailable

Exception raised if a `tan` parameter is provided to an API call, but that
`tan` parameter is already being used by another in-progress call. Users
should either not specify `tan` at all (and the client library will
automatically manage it in an incremental fashion), or if specified manually,
it is the caller's responsibility to ensure no two simultaneous calls share a
`tan` (as otherwise the client would not be able to match the call to the
response, and this exception will be raised automatically prior to the call).

### "Task was destroyed but it is pending!"

If a `HyperHDRClient` object is connected but destroyed prior to disconnection, a warning message may be printed ("Task was destroyed but it is pending!"). To avoid this, ensure to always call `async_client_disconnect` prior to destruction of a connected client. Alternatively use the async context manager:

```python
async with client.HyperHDRClient(TEST_HOST, TEST_PORT) as hc:
    if not hc:
        return
    ...
```

<a name="timeouts"></a>

## Timeouts

The client makes liberal use of timeouts, which may be specified at multiple levels:

* In the client constructor argument `timeout_secs`, used for connection and requests.
* In each request using a `timeout_secs` argument to the individual calls

Timeout values:

* `None`: If `None` is used as a timeout, the client will wait forever.
* `0`: If `0` is used as a timeout, the client default (specified in the constructor) will be used.
* `>0.0`: This number of seconds (or partial seconds) will be used.

By default, all requests will honour the `timeout_secs` specified in the client constructor unless explicitly overridden and defaults to 5 seconds (see [const.py](https://github.com/dermotduffy/hyperhdr-py/blob/master/hyperhdr/const.py#L95)). The one exception to this is the `async_send_request_token` which has a much larger default (180 seconds, see [const.py](https://github.com/dermotduffy/hyperhdr-py/blob/master/hyperhdr/const.py#L96)) as this request involves the user needing the interact with the HyperHDR UI prior to the call being able to return.

## Helpers

### ResponseOK

A handful of convenience callable classes are provided to determine whether
server responses were successful.

* `ResponseOK`: Whether any HyperHDR command response was successful (general).
* `ServerInfoResponseOK`: Whether a `async_get_serverinfo` was successful.
* `LoginResponseOK`: Whether an `async_login` was successful.
* `SwitchInstanceResponseOK`: Whether an `async_switch_instance` command was successful.

#### Example usage

```
if not client.ResponseOK(await hc.async_clear(priority=PRIORITY))
```

### Auth ID

When requesting an auth token, a 5-character ID can be specified to ensure the
admin user is authorizing the right request from the right origin. By default
the `async_request_token` will randomly generate an ID, but if one is required
to allow the user to confirm a match, it can be explicitly provided. In this case,
this helper method is made available.

* `generate_random_auth_id`: Generate a random 5-character auth ID for external display and inclusion in a call to `async_request_token`.

#### Example usage

```
auth_id  = hc.generate_random_auth_id()
hc.async_send_login(comment="Trustworthy actor", id=auth_id)
# Show auth_id to the user to allow them to verify the origin of the request,
# then have them visit the HyperHDR UI.
```
