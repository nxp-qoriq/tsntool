#!/usr/bin/env python
#
#Copyright 2020 NXP
#

import subprocess
import json
import os
import errno
import asyncio
import websockets

FIFO_STEP_1 = "get interfaces"
FIFO_STEP_2 = "get neighbors"
async def set_device_name():
    line = subprocess.Popen('ps  aux | grep avahi | grep running', \
               shell = True, stdout= subprocess.PIPE, stderr=subprocess.STDOUT);
    line_txt = line.stdout.readline().decode("utf-8")
    if (line_txt.find("running") == -1):
        print("avahi hostname is not registered!")
        return (-1);

    avahi_list = line_txt.split()
    hostname = avahi_list[-1].split('[')[-1].split(']')[0]
    subprocess.Popen(f'lldpcli -d -f json configure system description {hostname}', \
            shell = True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT);
    print(hostname)
    return (0);

async def get_interfaces():
    pinterfaces = subprocess.Popen('lldpcli -d show -f json interfaces ports swp0,swp1,swp2,swp3 details', \
        shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
    interfaces = pinterfaces.stdout.read().decode('utf-8');
    return (interfaces);

async def get_neighbors():
    pneighbors = subprocess.Popen('lldpcli -d show -f json neighbors ports swp0,swp1,swp2,swp3 details', \
            shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
    neighbors = pneighbors.stdout.read().decode('utf-8');
    return (neighbors);
 
async def deal_message(websocket, cmd):
#    cmd = await websocket.recv()
    if (cmd == FIFO_STEP_1):
        await set_device_name()
        feedback = await get_interfaces()
    elif (cmd == FIFO_STEP_2):
        feedback = await get_neighbors()
    else:
        print("error command");
        feedback = "{error:-1}";

    await websocket.send(feedback)
    print(f"{feedback}")


async def remotecall(websocket, path):
    async for message in websocket:
        print(f"< {message}")
        await deal_message(websocket, message)

start_server = websockets.serve(remotecall, "0.0.0.0", 8181)

asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()

