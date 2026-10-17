"""
列出sim对象所有以 modelbbox 开头的属性，以确定正确的常量名称
"""
import sys
from coppeliasim_zmqremoteapi_client import RemoteAPIClient

def main():
    client = RemoteAPIClient(host="localhost", port=23000)
    sim = client.require("sim")
    attrs = [a for a in dir(sim) if 'modelbbox' in a.lower()]
    print("modelbbox related attributes:", attrs)
    # also list all objfloatparameter* for inspection
    float_params = [a for a in dir(sim) if a.startswith('objfloatparameter')]
    print("Number of objfloatparameter*:", len(float_params))
    for p in sorted(float_params)[:20]:
        print(p)
    # maybe the constants are under sim. prefix?
    consts = [a for a in dir(sim) if a.isupper() and 'MODEL' in a]
    print("Uppercase MODEL consts:", consts)

if __name__ == "__main__":
    main()