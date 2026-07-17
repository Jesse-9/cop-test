![](images/homeImg.png)

# External controller tutorial

There are several ways one can control a robot or simulation in CoppeliaSim:

The most convenient way is to write a [simulation script](simulationScripts.html) (in Python or Lua) that handles the behaviour of a given robot or [model](models.html). It is the most convenient way, because simulation scripts are [scene objects](objects.html), and as such, they will also be duplicated during a copy operation. Additionally, they do not need any compilation with an external tool, they can run in [threaded or non-threaded mode](threadedAndNonThreadedCode.html), they can be extended via [custom script functions](apiFunctions.html#customizingLuaFunctions) or via Lua/Python language extension mechanisms. Another major advantage in using simulation scripts (in the case of Lua): there is no communication lag as with the last 3 methods mentioned in this section (i.e. the [regular API](apiFunctions.html) is used), and simulation scripts are part of the application main thread (inherent synchronous operation). There are however [small differences between Lua and Python](luaPythonDifferences.html).

Another way one can control a robot or a simulation is by writing a [plugin](plugins.html). A plugin allows for callback mechanisms, [custom script function registration](apiFunctions.html#customizingLuaFunctions), and of course access to external function libraries. A plugin is often used in conjunction with simulation scripts (e.g. the plugin registers custom script functions, that, when called from a simulation script, will call back a specific plugin function). A major advantage in using plugins is also that there is no communication lag as with the last 3 methods mentioned in this section (i.e. the [regular API](apiFunctions.html) is used), and that a plugin is part of the application main thread (inherent synchronous operation). The drawbacks with plugins are: they are more complicated to program, and they need to be compiled with an external tool. Refer also to the [plugin tutorial](pluginTutorial.html).

A third way one can control a robot or a simulation is by writing a custom [client application](mainClientApplication.html). This can be done in C/C++, or in Python for convenience, and allows to code custom behaviours such as starting CoppeliaSim in a particular manner, while interacting with a simulation at a more global and immediate level.

A forth way one can control a robot or a simulation is by writing an external client application that relies on the [remote API](remoteApiOverview.html). This is a very convenient and easy way, if you need to run the control code from an external application, from a robot or from another computer. This also allows you to control a simulation or a [model](models.html) (e.g. a virtual robot) with the exact same code as the one that runs the real robot.

A fifth way to control a robot or a simulation is via a [ROS](http://www.ros.org) node. In a similar way as the [remote API](remoteApiOverview.html), ROS is a convenient way to have several distributed processes communicate with each other. While the remote API is very lightweight and fast, it allows only communication with CoppeliaSim. ROS on the other hand allows connecting virtually any number of processes with each other, and a large amount of compatible libraries are available. It is however heavier and more complicated than the remote API. Refer to the [ROS interfaces](rosInterfaces.html) for details.

A sixth way to control a robot or a simulation is by writing an external application that communicates via various means (e.g. pipes, [ZeroMQ](meansOfCommunication.html#zmq), [sockets](meansOfCommunication.html#sockets), [serial port](meansOfCommunication.html#serialPort), etc.) with a CoppeliaSim plugin or CoppeliaSim script. Two major advantages are the choice of programming language, which can be just any language, and the flexibility. Here also, the control code can run on a robot, or a different computer. This way of controlling a simulation or a model is however more tedious that the methods with the [remote API](remoteApiOverview.html).

There are 7 scene files related to this tutorial:

*scenes/controlTypeExamples/controlledViaScript*: three robots are controlled via their respective [simulation script](simulationScripts.html): one runs Lua threaded code, one runs Lua non-threaded code, and the last one runs Python non-threaded code.

*scenes/controlTypeExamples/controlledViaPlugin*: the robot is controlled via a [plugin](plugins.html).

*scenes/controlTypeExamples/controlledViaRemoteApi*: the robot is controlled via an [external application](https://github.com/CoppeliaRobotics/bubbleRobClient) and the [ZeroMQ remote API](zmqRemoteApiOverview.html).

*scenes/controlTypeExamples/controlledViaRos*: the robot is controlled via an [external application](https://github.com/CoppeliaRobotics/ros_bubble_rob) and the [ROS interface](rosInterf.html).

*scenes/controlTypeExamples/controlledViaRos2*: the robot is controlled via an [external application](https://github.com/CoppeliaRobotics/ros2_bubble_rob) and the [ROS 2 interface](ros2Interface.html).

*scenes/controlTypeExamples/controlledViaZmq*: the robot is controlled via and [external application](https://github.com/CoppeliaRobotics/bubbleRobZmqServer) and [ZeroMQ](https://zeromq.org/).

*scenes/controlTypeExamples/controlledViaTcp*: the robot is controlled via an [external application](https://github.com/CoppeliaRobotics/bubbleRobServer) and [LuaSocket](http://w3.impa.br/~diego/software/luasocket/) and TCP.

![](images/externalControllerTut1.jpg)

In all 7 cases, [simulation scripts](simulationScripts.html) are used, mainly to make the link with the outside world (e.g. launch the correct client application, and pass the correct object handles to it). There are two other ways one can control a robot, a simulation, or the simulator itself: by using [customization scripts](customizationScripts.html), or [add-ons](addOns.html). They are however not recommended for control and should be rather used to handle functionality while simulation is not running.

As an example, the simulation script linked to the robot in scene *controlledViaZmq.ttt* has following main tasks:

Launch the controller application ([bubbleRobZmqServer](https://github.com/CoppeliaRobotics/bubbleRobZmqServer)) with a connection port as argument

Locally connect to the controller application

At each simulation pass, send the sensor values to the controller, and read the desired motor values from the controller

At each simulation pass, apply the desired motor values to the robot\'s joints

Run the simulations, and copy-and-paste the robot: the duplicated robots are directly operational.
