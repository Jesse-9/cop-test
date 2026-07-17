![](images/homeImg.png)

# Plugins

A CoppeliaSim plugin is a shared library that can be loaded via the [loadPlugin](regularApi/loadPlugin.html) API function, on demand. It allows CoppeliaSim\'s functionality to be extended by user-written functions (in a similar way as with [add-ons](addOns.html)). The language can be any language able to generate a shared library and able to call exported C-functions.

Plugins are usually used to customize the simulator and/or a particular simulation. Often, plugins are only used to provide a simulation with [custom script commands](apiFunctions.html#customizingLuaFunctions), and so are used in conjunction with [scripts](scripts.html). Other times, plugins are used to provide CoppeliaSim with a special functionality requiring either fast calculation capability (scripts are most of the times slower than compiled languages) or an interface to a hardware device (e.g. a real robot).

Each plugin is required to have at least the *simInit* entry point, other entry points are optional:

`#include <simLib/simExp.h> #include <simLib/simTypes.h> SIM_DLLEXPORT int simInit(SSimInit* info); SIM_DLLEXPORT void simMsg(SSimMsg* info); SIM_DLLEXPORT void simCleanup(); SIM_DLLEXPORT void simInit_ui(); // called immediately after simInit SIM_DLLEXPORT void simMsg_ui(SSimMsg_ui* info); SIM_DLLEXPORT void simCleanup_ui(); // called immediately before simCleanup

\*\_ui entry points are exclusively called by the UI thread, while the other entry points are called by the simulation thread.

A plugin shoud be named \"sim\<name\>-\<version\>\" (with the version information being optional), e.g. \"simIK-2-1\" for the name and \"libsimIK-2-1.so\" for the file on Linux. The plugin should be placed in *\<coppeliaSim executable\>*. It can then be loaded via Lua with e.g.:

```lua
simIK = loadPlugin('simIK-2-1')

It is however good practice and more flexible to wrap the load procedure inside of a module of its own, e.g.:

`--lua local simIK = loadPlugin('simIK-2-1') function simIK.anotherFunction() print('a pure Lua function') end return simIK

Above module should have the same name as the plugin it is loading (e.g. \"simIK-2-1.lua\") and placed in *\<coppeliaSim executable\>/lua*. From a script (Python or Lua), the desired functionality, including version, can then be loaded with e.g.:

```python
def sysCall_init(): simIK = require('simIK-2-1')
```lua
function sysCall_init() simIK = require('simIK-2-1') end

For more information on plugins, refer to following repositories:

[simSkeleton](https://github.com/CoppeliaRobotics/simSkeleton): represents a plugin template that is easy to understand. Convenient for small projects.

[simSkel](https://github.com/CoppeliaRobotics/simSkel): similar to simSkeleton above, but where a lot of redundant code is automatically generated. Ideal for larger projects.

[simVision](https://github.com/CoppeliaRobotics/simVision): a plugin that handles specific vision tasks (e.g. simulation of LIDAR sensors, or simulation of an omnidirectional camera).

[simBubble](https://github.com/CoppeliaRobotics/simBubble): illustrates how to add customized script functions and how to handle several specific [models](models.html). Refer also to the [related plugin tutorial](pluginTutorial.html).

[simROS](https://github.com/CoppeliaRobotics/simROS): the ROS package allowing you to build the [ROS Interface](rosInterf.html) for CoppeliaSim.

[simROS2](https://github.com/CoppeliaRobotics/simROS2): the ROS 2 package allowing you to build the [ROS 2 Interface](ros2Interface.html) for CoppeliaSim.

[simMTB](https://github.com/CoppeliaRobotics/simMTB): illustrates a Qt plugin which integrates a robot language interpreter (or other emulator) into CoppeliaSim. Refer also to its [related tutorial](robotLanguageIntegrationTutorial.html).

CoppeliaSim plugins may be published under any license.
```
