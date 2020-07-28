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

SERVER_GET_INTERFACES = "get interfaces"
SERVER_GET_NEIGHBORS = "get neighbors"
SERVER_GET_DELAY = "get delay"
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
async def get_delay():
    reply = {};
    for port in ['swp0', 'swp1', 'swp2', 'swp3']:
        cmd = "pmc -2 -i %s \"GET PORT_DATA_SET\" | grep peerMeanPathDelay | awk '{print $2}'"%(port)
        pdelayvalues = subprocess.Popen(cmd, \
		    shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
        delayvalues = pdelayvalues.stdout.read().decode('utf-8');
        values = delayvalues.split()
        if (len(values) == 0):
            reply[port] = 0;
            continue;

        cmd = "pmc -2 -i %s \"GET PORT_DATA_SET\" | grep RESPONSE | awk '{print $1}'"%(port)
        pdelaykeys = subprocess.Popen(cmd, \
                   shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
        delaykeys = pdelaykeys.stdout.read().decode('utf-8');
        keys = delaykeys.split()
        delaylist = dict(zip(keys, values))

        cmd ="pmc -2 -i %s \"GET USER_DESCRIPTION\" | grep RESPONSE | awk '{print $1}'"%(port)
        pcurrentkey = subprocess.Popen(cmd, \
                   shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
        currentkey = pcurrentkey.stdout.read().decode('utf-8');
        reply[port]= delaylist[currentkey.split()[0]]
    return (str(reply))

async def deal_message(websocket, cmd):
#    cmd = await websocket.recv()
    if (cmd == SERVER_GET_INTERFACES):
        await set_device_name()
        feedback = await get_interfaces()
    elif (cmd == SERVER_GET_NEIGHBORS):
        feedback = await get_neighbors()
    elif (cmd == SERVER_GET_DELAY):
        feedback = await get_delay()
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

