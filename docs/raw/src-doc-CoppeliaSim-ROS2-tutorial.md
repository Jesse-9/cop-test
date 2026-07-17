![](images/homeImg.png)

# ROS2 tutorial

This tutorial will try to explain in a simple way how you can manage to have CoppeliaSim *ROS 2 enabled*.

First of all you should make sure that you have gone through the [official ROS2 tutorials](https://index.ros.org/doc/ros2/Tutorials/#tutorials), at least the beginner section. Then, we assume that you have the latest Ubuntu running, that ROS2 is installed, and that the workspace folders are set. Here also refer to the [official documentation regarding the ROS 2 installation](https://index.ros.org/doc/ros2/Installation/).

The general ROS2 functionality in CoppeliaSim is supported via the [ROS2 Interface](rosInterf.html) (*libsimROS2.so*). The Linux distribution should include that file already compiled in *CoppeliaSim/compiledROSPlugins*, but it first needs to be copied to *CoppeliaSim/*, otherwise it won\'t be loaded. You might however experience plugin load problems, depending on your system specificities: make sure to always inspect the terminal window of CoppeliaSim for details about plugin load operations. The ROS2 plugin is loaded with:

```python
simROS2 = require('simROS2')
```lua
simROS2 = require('simROS2')

Make sure to source the ROS2 environment prior to running CoppeliaSim.

If the plugin cannot be loaded, then you should recompile it by yourself. It is open source and can be modified as much as needed in order to support a specific feature or to extend its functionality. If specific messages/services/etc. need to be supported, make sure to edit files located in *simROS2/meta/*, prior to recompilation. There are 2 packages:

[simROS2](https://github.com/CoppeliaRobotics/simROS2): this package is the [ROS2 Interface](rosInterf.html) that will be compiled to a \".so\" file, and that is used by CoppeliaSim.

[ros2_bubble_rob](https://github.com/CoppeliaRobotics/ros2_bubble_rob): this is the package of a very simple robot controller that connects to CoppeliaSim via the [ROS2 Interface](rosInterf.html). This node will be in charge of controlling the red robot in the demo scene *controlTypeExamples/controlledViaRos.ttt*

Above packages should be copied to your *ros2_ws/src* folder.

In order to build the packages, navigate to the *ros2_ws* folder and type:

`$ export COPPELIASIM_ROOT_DIR=~/path/to/coppeliaSim/folder $ ulimit -s unlimited #otherwise compilation might freeze/crash $ colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

That\'s it! The packages should have been generated and compiled to a library, which is automatically copied to the CoppeliaSim installation folder. The plugin is now ready to be used.

Now open a terminal, move to the CoppeliaSim installation folder, start CoppeliaSim and load the ROS2 plugin with above\'s Lua code. Upon succesful ROS2 Interface load, checking the available nodes gives this:

`$ ros2 node list /sim_ros2_interface

In an empty CoppeliaSim scene, select an object, then attach a non-threaded [simulation script](simulationScripts.html) to it with \[Add \> Script \> simulation script \> Non threaded \> Lua/Python\]. Open the [script editor](scriptEditor.html) for that script and replace the content with following:

```python
def subscriber_callback(msg): # This is the subscriber callback function log_message = f'subscriber receiver following Float32: {msg["data"]}' sim.addLog(sim.verbosity_scriptinfos, log_message) def getTransformStamped(objHandle, name, relTo, relToName): # This function retrieves the stamped transform for a specific object t = simROS2.getSystemTime() p = sim.getObjectPosition(objHandle, relTo) o = sim.getObjectQuaternion(objHandle, relTo) return { 'header': { 'stamp': t, 'frame_id': relToName }, 'child_frame_id': name, 'transform': { 'translation': {'x': p[0], 'y': p[1], 'z': p[2]}, 'rotation': {'x': o[0], 'y': o[1], 'z': o[2], 'w': o[3]} } } def sysCall_init(): sim = require('sim') simROS2 = require('simROS2') # The simulation script initialization self.objectHandle = sim.getObject('..') self.objectAlias = sim.getObjectAlias(self.objectHandle, 3) # Prepare the float32 publisher and subscriber (we subscribe to the topic we publish): self.publisher = simROS2.createPublisher('/simulationTime', 'std_msgs/msg/Float32') self.subscriber = simROS2.createSubscription('/simulationTime', 'std_msgs/msg/Float32', subscriber_callback) def sysCall_actuation(): # Send an updated simulation time message, and send the transform of the object attached to this script: simROS2.publish(self.publisher, {'data': sim.getSimulationTime()}) simROS2.sendTransform(getTransformStamped(self.objectHandle, self.objectAlias, -1, 'world')) # To send several transforms at once, use simROS2.sendTransforms instead def sysCall_cleanup(): # Following not really needed in a simulation script (i.e. automatically shut down at simulation end): simROS2.shutdownPublisher(self.publisher) simROS2.shutdownSubscription(self.subscriber)
```lua
function subscriber_callback(msg) -- This is the subscriber callback function sim.addLog(sim.verbosity_scriptinfos, 'subscriber receiver following Float32: '..msg.data) end function getTransformStamped(objHandle, name, relTo, relToName) -- This function retrieves the stamped transform for a specific object local t = simROS2.getSystemTime() local p = sim.getObjectPosition(objHandle,relTo) local o = sim.getObjectQuaternion(objHandle,relTo) return { header = { stamp = t, frame_id = relToName }, child_frame_id = name, transform = { translation = {x = p[1], y = p[2], z = p[3]}, rotation = {x = o[1], y = o[2], z = o[3], w = o[4]} } } end function sysCall_init() sim = require('sim') simROS2 = require('simROS2') -- The simulation script initialization objectHandle = sim.getObject('..') objectAlias = sim.getObjectAlias(objectHandle, 3) -- Prepare the float32 publisher and subscriber (we subscribe to the topic we publish): publisher = simROS2.createPublisher('/simulationTime', 'std_msgs/msg/Float32') subscriber = simROS2.createSubscription('/simulationTime', 'std_msgs/msg/Float32', 'subscriber_callback') end function sysCall_actuation() -- Send an updated simulation time message, and send the transform of the object this script is attached to: simROS2.publish(publisher, {data = sim.getSimulationTime()}) simROS2.sendTransform(getTransformStamped(objectHandle, objectAlias, -1, 'world')) -- To send several transforms at once, use simROS2.sendTransforms instead end function sysCall_cleanup() -- Following not really needed in a simulation script (i.e. automatically shut down at simulation end): simROS.shutdownPublisher(publisher) simROS.shutdownSubscriber(subscriber) end

Above script will publish the simulation time, and subscribe to it at the same time. It will also publish the transform of the object the script is attached to. You should be able to see the simulation time topic with:

`$ ros2 topic list

To see the message content, you can type:

`$ ros2 topic echo /simulationTime

Now load the demo scene *messaging/ros2InterfaceTopicPublisherAndSubscriber.ttt*, and run the simulation. The code in the [simulation script](simulationScripts.html) attached to *Vision_sensor* will enable a publisher to stream the vision sensor\'s image, and also enable a subscriber to listen to that same stream. The subscriber applies the read data to the passive vision sensor, that is only used as a data container. So CoppeliaSim is streaming data, while listening to the same data! This is what is happening:

![](images/rosTut1.jpg)

\[Image publisher and image subscriber demo\]

Try experimenting a little bit with the code. You can also visualize the image that CoppeliaSim streams with following command:

`$ ros2 run image_tools showimage --ros-args --remap image:=/image

Had you been streaming simpler data, then you could also have visualized it with:

`$ ros2 topic echo /image

Now stop the simulation and load the demo scene *controlTypeExamples/controlledViaRos2.ttt*, and run the simulation. The robot is simplistic, and also behaving in a simplistic way for simplification purposes. It is controlled via the [ROS2 Interface](rosInterf.html):

![](images/rosBubbleRob.jpg)

\[External client application controlling the robot via ROS\]

The simulation script attached to the robot, and running in a non-threaded fashion, is in charge of following:

determine some object handles (e.g. motor joint handles and proximity sensor handle)

Launch motor speed subscribers

Launch a sensor publisher and a simulation time publisher

and finally launch a client application. The application is called with some topic names as arguments, so that it will know which topics to listen to and to subscribe. The client application (*ros2BubbleRob*) is then taking over the control of the robot via ROS2.

While simulation is running, copy and paste a few times the robot. Notice that every copy is directly operational and independent. This is one of the many strengths of CoppeliaSim.

Now stop the simulation and open a new scene, then drag following model into it: *Models/tools/ros2Interface helper tool.ttm*. This model is constituted by a single [customization script](customizationScripts.html) that offers following topic publishers and subscribers:

*startSimulation* topic: can be used to start a simulation by publishing on this topic a *std_msgs/msg/Bool* message.

*pauseSimulation* topic: can be used to pause a simulation by publishing on this topic a *std_msgs/msg/Bool* message.

*stopSimulation* topic: can be used to stop a simulation by publishing on this topic a *std_msgs/msg/Bool* message.

*enableSyncMode* topic: by publishing a *std_msgs/msg/Bool* message on this topic, you can enable/disable the [stepping mode](simulation.html#stepped).

*triggerNextStep* topic: by publishing a *std_msgs/msg/Bool* message on this topic, you can trigger the next simulation step, while in [stepping mode](simulation.html#stepped).

*simulationStepDone* topic: a message of type *std_msgs/msg/Bool* will be published at the end of each simulation pass.

*simulationState* topic: messages of type *std_msgs/msg/Int32* will be published on a regular basis. *0* indicates that the simulation is stopped, *1* that it is running and *2* that it is paused.

*simulationTime* topic: messages of type *std_msgs/msg/Float32* will be published on a regular basis, indicating the current simulation time.

Have a look at the content of the customization script, that can be fully customized for various purposes. Try generating topic messages from the command line, for instance:

`$ ros2 topic pub /startSimulation std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /pauseSimulation std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /stopSimulation std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /enableSyncMode std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /startSimulation std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /triggerNextStep std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /triggerNextStep std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /triggerNextStep std_msgs/msg/Bool '{data: true}' --once $ ros2 topic pub /stopSimulation std_msgs/msg/Bool '{data: true}' --once

In order to display the current simulation time, you could type:

`$ ros2 topic echo /simulationTime

Finally, make sure to have a look at the [remote API functionality](remoteApiOverview.html) in CoppeliaSim: it allows for remote function execution, fast data streaming back and forth, is quite simple to use, lightweight and cross-platform. The remote API functionality is available for 7 different languages and can be an interesting alternative to ROS in some cases.
