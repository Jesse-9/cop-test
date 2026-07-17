![](images/homeImg.png)

# CoppeliaSim API framework

Within CoppeliaSim, one can distinguish between two group of functions:

[the regular API](apiFunctions.html): those are functions made available by CoppeliaSim itself, and can be seen as the main functions. They are grouped under the *sim* namespace, e.g. [sim.getObject](regularApi/simGetObject.html). [Properties](properties.html) and [their related API functions](apiFunctions.html#properties) are an important part of the regular API.

the other API functions: those are functions made available by specific [plugins](plugins.html). Each plugin groups its functions under a unique namespace, e.g. [simOMPL](simOMPL.html?view=alphabetical), [simUI](simUI.html?view=alphabetical), [simIK](simIK.html?view=category), etc. Description and API references to those functions can be found in the [functionality section](functionality.html).

From an external application, there are [many different API mechanisms available to communicate and interact with CoppeliaSim](meansOfCommunication.html).
