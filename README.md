[![PyPi](https://img.shields.io/pypi/v/hyperion-py.svg)](https://pypi.org/project/hyperion-py/)
[![PyPi](https://img.shields.io/pypi/pyversions/hyperion-py.svg)](https://pypi.org/project/hyperion-py/)
[![Build Status](https://travis-ci.com/dermotduffy/hyperion-py.svg?branch=master)](https://travis-ci.com/dermotduffy/hyperion-py)
[![Coverage](https://img.shields.io/codecov/c/github/dermotduffy/hyperion-py)](https://codecov.io/gh/dermotduffy/hyperion-py)

# Hyperion Library

Python library for
[Hyperion-NG](https://github.com/hyperion-project/hyperion.ng). See [JSON
API](https://docs.hyperion-project.org/en/json/) for more details about the
inputs and outputs of this library.

# Installation

```bash
$ pip3 install hyperion-py
```

# Usage

## Data model philosophy

Whilst not universally true, this library attempts to precisely represent the
data model, API and parameters as defined in the [Hyperion JSON
documentation](https://docs.hyperion-project.org/en/json/). Thus no attempt is
made (intentionally) to present convenient accessors/calls at a finer level of
granularity than the model already supports. This is to ensure the client has a
decent chance at staying functional regardless of underlying data model changes
from the server, and the responsibility to match the changes to the server's
data model (e.g. new Hyperion server features) belong to the caller.

### Constructor Arguments

The following arguments may be passed to the `HyperionClient` constructor:

|Argument|Type|Default|Description|
|--------|----|-------|-----------|
|host    |`str`||Host or IP to connect to|
|port    |`int`|19444|Port to connect to|
|default_callback|`callable`|None|A callable for Hyperion callbacks. See [callbacks](#callbacks)|
|callbacks|`dict`|None|A dictionary of callables keyed by the update name. See [callbacks](#callbacks)|
|token|`str`|None|An authentication token|
|instance|`int`|0|An instance id to switch to upon connection|
|origin|`str`|"hyperion-py"|An arbitrary string describing the calling application|
|timeout_secs|`float`|5.0|The number of seconds to wait for a server response or connection attempt before giving up. See [timeouts](#timeouts)|
|retry_secs|`float`|30.0|The number of seconds between connection attempts|
|raw_connection|`bool`|False|If True, the connect call will establish the network connection but not attempt to authenticate, switch to the required instance or load state. The client must call `async_client_login` to login, `async_client_switch_instance` to switch to the configured instance and `async_get_serverinfo` to load the state manually. This may be useful if the caller wishes to communicate with the server prior to authentication.|

### Connection, disconnection and client control calls

   * `async_client_connect()`: Connect the client.
   * `async_client_disconnect()`: Disconnect the client.
   * `async_client_login()`: Login a connected client. Automatically called by
     `async_client_connect()` unless the `raw_connection` constructor argument is True.
   * `async_client_switch_instance()`: Switch to the configured instance on the Hyperion
     server. Automatically called by `async_client_connect()` unless the `raw_connection`
     constructor argument is True.

### Native API Calls

All API calls can be found in
[client.py](https://github.com/dermotduffy/hyperion-py/blob/master/hyperion/client.py).
All async calls start with `async_`.

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
|async_get_serverinfo|async_send_get_serverinfo|[Docs](https://docs.hyperion-project.org/en/json/ServerInfo.html#parts)|
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
|async_sysinfo|async_send_sysinfo|[Docs](https://docs.hyperion-project.org/en/json/ServerInfo.html#system-hyperion)|

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

#### Example: Sending commands

A slightly more complex example that sends commands (clears the Hyperion source
select at a given priority, then sets color at that same priority).

```python
#!/usr/bin/python
"""Simple Hyperion client request demonstration."""

import asyncio
import logging
import sys
from hyperion import client

HOST = "hyperion"
PRIORITY = 20


async def set_color():
    """Set red color on Hyperion."""

    hc = client.HyperionClient(HOST)

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
    await hc.async_client_disconnect()


logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
asyncio.get_event_loop().run_until_complete(set_color())
```

#### Example: Starting and switching instances

The following example will start a stopped instance, wait for it to be ready,
then switch to it. Uses [callbacks](#callbacks), discussed below.


```python
#!/usr/bin/python
"""Simple Hyperion client request demonstration."""

import asyncio
import logging
import sys
from hyperion import client

HOST = "hyperion"
PRIORITY = 20


async def instance_start_and_switch():
    """Wait for an instance to start."""

    instance_ready = asyncio.Event()

    def instance_update(json):
        for data in json["data"]:
            if data["instance"] == 1 and data["running"]:
                instance_ready.set()

    hc = client.HyperionClient(HOST, callbacks={"instance-update": instance_update})

    if not await hc.async_client_connect():
        logging.error("Could not connect to: %s", HOST)
        return

    if not client.ResponseOK(await hc.async_start_instance(instance=1)):
        logging.error("Could not start instance on: %s", HOST)
        return

    # Blocks waiting for the instance to start.
    await instance_ready.wait()

    if not client.ResponseOK(await hc.async_switch_instance(instance=1)):
        logging.error("Could not switch instance on: %s", HOST)
        return
    await hc.async_client_disconnect()


logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
asyncio.get_event_loop().run_until_complete(instance_start_and_switch())
```

<a name="callbacks"></a>
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

<a name="timeouts"></a>
## Timeouts

The client makes liberal use of timeouts, which may be specified at multiple levels:

   * In the client constructor argument `timeout_secs`, used for connection and requests.
   * In each request using a `timeout_secs` argument to the individual calls

Timeout values:

   * `None`: If `None` is used as a timeout, the client will wait forever.
   * `0`: If `0` is used as a timeout, the client default (specified in the constructor) will be used.
   * `>0.0`: This number of seconds (or partial seconds) will be used.

By default, all requests will honour the `timeout_secs` specified in the client constructor unless explicitly overridden and defaults to 5 seconds (see [const.py](https://github.com/dermotduffy/hyperion-py/blob/master/hyperion/const.py#L95)). The one exception to this is the `async_send_request_token` which has a much larger default (180 seconds, see [const.py](https://github.com/dermotduffy/hyperion-py/blob/master/hyperion/const.py#L96)) as this request involves the user needing the interact with the Hyperion UI prior to the call being able to return.


## Helpers

### ResponseOK

A handful of convenience callable classes are provided to determine whether
server responses were successful.

   * `ResponseOK`: Whether any Hyperion command response was successful (general).
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
# then have them visit the Hyperion UI.
```
