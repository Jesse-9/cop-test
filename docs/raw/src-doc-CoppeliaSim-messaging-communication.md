![](images/homeImg.png)

# Messaging/interfaces/connectivity

There are several ways messages or data can be exchanged/transmitted/received in and around CoppeliaSim, but also between CoppeliaSim and an external application, other computer, machine, etc.:

One can send/receive data via:

[]{#signals}[signals](properties.html#signals)

[]{#customData}[]{#customDataBlocks}[custom data](properties.html#customData)

[calling plugin functions](#pluginFunctions)

[calling script functions](#simCallScriptFunction)

[Remote API](remoteApiOverview.html)

[broadcasting a message](#simBroadcastMsg)

[events](#events)

[ROS](rosInterfaces.html)

[ZeroMQ](#zmq)

[WebSocket](#ws)

[serial port](#serialPort)

[sockets](#sockets)

[other](#other)

## []{#callingPluginFunctions}[]{#pluginFunctions}Calling plugin functions

[Scripts](scripts.html) can call specific [plugin](plugins.html) functions, so-called callback functions: in order to be able to do this, the plugin must first register its callback functions via [simRegisterScriptFunction](apiFunctions.html#customScriptFunctions). This is a convenient mechanism to extend CoppeliaSim\'s functionality, but can also be used for complex data exchange between scripts and plugins. Following illustrates a very simple plugin function and its registration:

`void myCallbackFunc(SScriptCallBack* p) { int stack = p -> stackID; CStackArray inArguments; inArguments.buildFromStack(stack); if ( (inArguments.getSize() > 0) && inArguments.isString(0) ) { std::string tmp("we received a string: "); tmp += inArguments.getString(0); simAddLog("ABC", sim_verbosity_msgs,tmp.c_str()); CStackArray outArguments; outArguments.pushString("Hello to you too!"); outArguments.buildOntoStack(stack); } else simSetLastError(nullptr, "Not enough arguments or wrong arguments."); } // Somewhere in the plugin's initialization code: simRegisterScriptCallbackFunction("func", nullptr, myCallbackFunc);

## []{#simCallScriptFunction}[]{#simCallScriptFunctionEx}Calling script functions

A [script](scripts.html) function can obviously be called from within the same script, but also:

across scripts (via [sim.callScriptFunction](regularApi/simCallScriptFunction.html) or [sim.getScriptFunctions](regularApi/simGetScriptFunctions.html))

from a plugin (via [simCallScriptFunctionEx](regularApi/simCallScriptFunctionEx_cpp.html))

from a [ROS](rosInterfaces.html) client (via a callback mechanism)

or from a [remote API](remoteApiOverview.html) client

The called script function can perform various tasks, then send back data to the caller. This is also a simple way to extend the functionality of an external application in a quick manner. It is however important that the called script doesn\'t perform lengthly tasks, otherwise everything will come to a halt (lengthly tasks should rather be triggered externally, and processed at an appropriate moment by the script itself when called from the regular [system callbacks](callbackFunctions.html)).

## []{#simBroadcastMsg}Broadcasting messages

A [script](scripts.html) or a [remote API](remoteApiOverview.html) client can broadcast a message to all scripts at once, via the [sim.broadcastMsg](regularApi/simBroadcastMsg.html) function. For instance, following will constantly broadcast a message to all scripts:

```python
def sysCall_init(): sim = require('sim') self.objectHandle = sim.getObject('.') def sysCall_sensing(): message = {'id': 'greetingMessage', 'data': {'msg': 'Hello!'}} sim.broadcastMsg(message) def sysCall_msg(msg, origin): if origin != self.objectHandle and msg.id == 'greetingMessage': print("Received following message from script {}:".format(origin)) print(msg['data']['msg'])
```lua
function sysCall_init() sim = require('sim') scriptHandle = sim.getObject('.') end function sysCall_sensing() local message = {id = 'greetingMessage', data = {msg = 'Hello!'}} sim.broadcastMsg(message) end function sysCall_msg(msg, origin) if origin ~= scriptHandle and msg.id == 'greetingMessage' then print(string.format("Received following message from script %i:", origin)) print(msg.data.msg) end end

## []{#events}Events

A [script](scripts.html) or a [plugin](plugins.html) can receive notifications for everything that happens in CoppeliaSim itself, e.g. when an object is created, modified, removed, or when an internal state changed. A script can use the sysCall_event [callback function](callbackFunctions.html) to that end, coupled with some filtering for efficiency\'s sake. For instance, following script tracks a pose change with any scene object, and a change in scene object selection:

```python
#luaExec eventCallback = true -- enable event callback import cbor2 as cbor def sysCall_init(): sim = require('sim') filters = {} filters[sim.handle_sceneobject] = ['pose'] filters[sim.handle_scene] = ['selectionHandles'] sim.setEventFilters(filters) def sysCall_event(events): ev = cbor.loads(events) print(ev)
```lua
function sysCall_init() sim = require('sim') cbor = require('org.conman.cbor') local filters = {} filters[sim.handle_sceneobject] = {'pose'} filters[sim.handle_scene] = {'selectionHandles'} sim.setEventFilters(filters) end function sysCall_event(events) local ev = cbor.decode(events) print(ev) end

Most [properties](properties.html) generate an event when changed (i.e. modified explicitly or internally by CoppeliaSim). See also the event viewer add-on located at \[Modules \> Developer tools \> Event viewer\].

## []{#zmq}ZMQ

The [ZeroMQ library](http://api.zeromq.org/), wrapped inside the [ZMQ plugin](https://github.com/CoppeliaRobotics/simZMQ), offers several [API functions related to ZeroMQ messaging](simZMQ.html). When using Python, one is of course also free to using the pyzmq package for the ZeroMQ functionality. Following illustrates a simple requester:

```python
def sysCall_thread(): sim = require('sim') simZMQ = require('simZMQ') # suppose we do not use the pyzmq package print('Connecting to hello world server...') self.context = simZMQ.ctx_new() self.requester = simZMQ.socket(self.context, simZMQ.REQ) simZMQ.connect(self.requester, 'tcp://localhost:5555') for request_nbr in range(11): print('-----------------------------------------') data = 'Hello' print(f'[requester] Sending "{data}"...') simZMQ.send(self.requester, data, 0) rc, data = simZMQ.recv(self.requester, 0) print(f'[requester] Received "{data}"') def sysCall_cleanup(): simZMQ.close(self.requester) simZMQ.ctx_term(self.context)
```lua
function sysCall_thread() sim = require('sim') simZMQ = require('simZMQ') print('Connecting to hello world server...') context = simZMQ.ctx_new() requester = simZMQ.socket(context, simZMQ.REQ) simZMQ.connect(requester, 'tcp://localhost:5555') for request_nbr = 0, 10 do print('-----------------------------------------') local data = 'Hello' printf('[requester] Sending "%s"...', data) simZMQ.send(requester, data, 0) local rc, data = simZMQ.recv(requester, 0) printf('[requester] Received "%s"', data) end end function sysCall_cleanup() simZMQ.close(requester) simZMQ.ctx_term(context) end

And following would be the corresponding responder:

```python
def sysCall_thread(): sim = require('sim') simZMQ = require('simZMQ') # suppose we do not use the pyzmq package self.context = simZMQ.ctx_new() self.responder = simZMQ.socket(self.context, simZMQ.REP) rc = simZMQ.bind(self.responder, 'tcp://*:5555') if rc != 0: raise Exception('failed bind') while True: rc, data = simZMQ.recv(self.responder, 0) print(f'[responder] Received "{data}"') data = 'World' print(f'[responder] Sending "{data}"...') simZMQ.send(self.responder, data, 0) def sysCall_cleanup(): simZMQ.close(self.responder) simZMQ.ctx_term(self.context)
```lua
function sysCall_thread() sim = require('sim') simZMQ = require('simZMQ') context = simZMQ.ctx_new() responder = simZMQ.socket(context, simZMQ.REP) local rc = simZMQ.bind(responder, 'tcp://*:5555') if rc ~= 0 then error('failed bind') end while true do local rc, data = simZMQ.recv(responder, 0) printf('[responder] Received "%s"', data) data = 'World' printf('[responder] Sending "%s"...', data) simZMQ.send(responder, data, 0) end end function sysCall_cleanup() simZMQ.close(responder) simZMQ.ctx_term(context) end

## []{#ws}WebSocket

The [WebSocket plugin](https://github.com/CoppeliaRobotics/simWS), offers several [API functions allowing to interact with a web browser](simWS.html). When using Python, one is of course also free to using a Python specific WS package. Following is a simple echo server:

```python
def onMessage(server, connection, data): simWS.send(server, connection, data) def sysCall_init(): simWS = require('simWS') # suppose one is not using any specific Python package related to WS server = simWS.start(9000) simWS.setMessageHandler(server, 'onMessage')
```lua
function onMessage(server, connection, data) simWS.send(server, connection, data) end function sysCall_init() simWS = require('simWS') server = simWS.start(9000) simWS.setMessageHandler(server, 'onMessage') end

And following is a simple broadcaster:

```python
def onOpen(server, connection): if server not in clients: clients[server] = {} clients[server][connection] = 1 def onClose(server, connection): del clients[server][connection] def broadcast(server, data): for connection in clients.get(server, {}): simWS.send(server, connection, data) def sysCall_init(): simWS = require('simWS') # suppose one is not using any specific Python package related to WS clients = {} server = simWS.start(9000) simWS.setOpenHandler(server, onOpen) simWS.setCloseHandler(server, onClose)
```lua
function onOpen(server, connection) clients[server] = clients[server] or {} clients[server][connection] = 1 end function onClose(server, connection) clients[server][connection] = nil end function broadcast(server, data) for connection, _ in pairs(clients[server] or {}) do simWS.send(server, connection, data) end end function sysCall_init() simWS = require('simWS') clients = {} server = simWS.start(9000) simWS.setOpenHandler(server, 'onOpen') simWS.setCloseHandler(server, 'onClose') end

## []{#serialPort}Serial port

CoppeliaSim implements several [serial port API functions](apiFunctions.html#serialPort) for Lua. With Python, use the [Python serial port package](https://pypi.org/project/pyserial/).

## []{#sockets}Sockets

CoppeliaSim ships with the [LuaSocket](http://w3.impa.br/~diego/software/luasocket/home.html) extension library for Lua, while several packages are available for Python. Following illustrates how to fetch a webpage:

```python
import requests response = requests.get('http://www.google.com') page = response.text
```lua
http = require('socket.http') page = http.request('http://www.google.com')

## []{#other}Other

Many other means of communication can be directly supported from within a script, via a Lua extension library or via a Python package. Indirectly, by passing via a [plugin](plugins.html), there are even more possibilities, since a plugin can virtually link to any type of c/c++ communication library.
