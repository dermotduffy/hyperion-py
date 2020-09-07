[![Build Status](https://travis-ci.com/dermotduffy/hyperion-py.svg?branch=master)](https://travis-ci.com/dermotduffy/hyperion-py)

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

### Calls

   * async_connect
   * async_disconnect
   * [async_is_auth_required](https://docs.hyperion-project.org/en/json/Authorization.html#authorization-check)
   * [async_login](https://docs.hyperion-project.org/en/json/Authorization.html#login-with-token)
   * [async_logout](https://docs.hyperion-project.org/en/json/Authorization.html#logout)
   * [async_request_token](https://docs.hyperion-project.org/en/json/Authorization.html#request-a-token)
   * [async_request_token_abort](https://docs.hyperion-project.org/en/json/Authorization.html#request-a-token)
   * [async_set_adjustment](https://docs.hyperion-project.org/en/json/Control.html#adjustments)
   * [async_clear](https://docs.hyperion-project.org/en/json/Control.html#clear)
   * [async_set_color](https://docs.hyperion-project.org/en/json/Control.html#set-color)
   * [async_set_component](https://docs.hyperion-project.org/en/json/Control.html#control-components)
   * [async_set_effect](https://docs.hyperion-project.org/en/json/Control.html#set-effect)
   * [async_set_image](https://docs.hyperion-project.org/en/json/Control.html#set-image)
   * [async_image_stream_start](https://docs.hyperion-project.org/en/json/Control.html#live-image-stream)
   * [async_image_stream_stop](https://docs.hyperion-project.org/en/json/Control.html#live-image-stream)
   * [async_start_instance](https://docs.hyperion-project.org/en/json/Control.html#control-instances)
   * [async_stop_instance](https://docs.hyperion-project.org/en/json/Control.html#control-instances)
   * [async_switch_instance](https://docs.hyperion-project.org/en/json/Control.html#api-instance-handling)
   * [async_set_led_mapping_type](https://docs.hyperion-project.org/en/json/Control.html#led-mapping)
   * [async_led_stream_start](https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream)
   * [async_led_stream_stop](https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream)
   * [async_set_sourceselect](https://docs.hyperion-project.org/en/json/Control.html#source-selection)
   * [async_set_videomode](https://docs.hyperion-project.org/en/json/Control.html#video-mode)

Note that the `command` and `subcommand` keys shown in the above documentation
will automatically be included in the calls the client sends, and do not need
to be specified.

## Client inputs / outputs

The API parameters and output are all as defined in the [JSON API
documentation](https://docs.hyperion-project.org/en/json/).

## Example usage:

```python
#!/usr/bin/python

import asyncio
from hyperion import client, const

HOST = "hyperion"

async def print_brightness():
    hyperion_client = client.HyperionClient(HOST)
    if not await hyperion_client.async_connect():
        return
    print("Brightness: %i%%" % hyperion_client.adjustment[0][const.KEY_BRIGHTNESS])

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(print_brightness())
```

## Running in the background

The `HyperionClient` subscribes to updates from the Hyperion server, which are
used to keep the client state fresh. Optionally, callbacks can be triggered to
the user. To receive these callbacks, the client needs to run as an `asyncio`
background task.

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

### Example use of asyncio event loop to deliver callbacks

```python
from hyperion import client, const

HOST = "hyperion"

def callback(json):
    print("Received Hyperion command: %s" % json)

if __name__ == "__main__":
    hyperion_client = client.HyperionClient(HOST, default_callback=callback)
    asyncio.get_event_loop().run_until_complete(hyperion_client.async_connect())

    # Start client in "background".
    hyperion_client.start_background_task()
    asyncio.get_event_loop().run_forever()
```

Output:

```
Received Hyperion command: {'command': 'connection-update', 'connected': True}
```

## ThreadedHyperionClient

A `ThreadedHyperionClient` is also provided as a convenience wrapper to for
non-async code. The `ThreadedHyperionClient` wraps the async calls with
non-async versions (without the `async_` on the method names shown above).

### Example use of Threaded client

```python
#!/usr/bin/python

from hyperion import client, const

HOST = "hyperion"

def callback(json):
    print("Received Hyperion command: %s" % json)

if __name__ == "__main__":
    hyperion_client = client.ThreadedHyperionClient(HOST, default_callback=callback)

    hyperion_client.connect()
    print("Brightness: %i%%" % hyperion_client.adjustment[0][const.KEY_BRIGHTNESS])

    # Run in a separate thread.
    hyperion_client.start()
    hyperion_client.join()
```

Output:

```
Received Hyperion command: {'command': 'connection-update', 'connected': True}
Brightness: 59%
```
