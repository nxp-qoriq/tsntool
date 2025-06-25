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
import time

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
    ports = subprocess.Popen("ls -l /sys/class/net/ | grep -v 'virtual\|can\|eno2' |  awk -F' ' '{print$9}' | grep -v '^$' | tr '\n' ','", \
            shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
    portstr = ports.stdout.read().decode('utf-8');
    cmd = "lldpcli -d show -f json0 interfaces ports %s details"%(portstr);
    pinterfaces = subprocess.Popen(cmd, \
        shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
    interfaces = pinterfaces.stdout.read().decode('utf-8');
    return (interfaces);

glinks = [];
async def get_neighbors():
    global glinks;

    ports = subprocess.Popen("ls -l /sys/class/net/ | grep -v 'virtual\|can\|eno2' | awk -F' ' '{print$9}' | grep -v '^$' | tr '\n' ','", \
            shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
    portstr = ports.stdout.read().decode('utf-8');
    cmd = "lldpcli -d show -f json0 neighbors ports %s details"%(portstr);
    pneighbors = subprocess.Popen(cmd, \
            shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
    neighbors = pneighbors.stdout.read().decode('utf-8');
    dneighbors = json.loads(neighbors);
    if not dneighbors['lldp'][0]:
        return (neighbors);

    glinks.clear();
    for interface in dneighbors['lldp'][0]['interface']:
        print(interface["name"]);
        glinks.append(interface["name"]);

    print(glinks);
    return (neighbors);
async def get_delay():
    reply = {};
    for port in glinks:
        cmd = "pmc -2 -i %s \"GET PORT_DATA_SET\""%(port)
        pdelayvalues = subprocess.Popen(cmd, \
		    shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
        pmcpid = pdelayvalues.pid;
        cmd = "grep peerMeanPathDelay";
        pchild1 = subprocess.Popen(cmd, stdin= pdelayvalues.stdout, \
                shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
        cmd = "awk '{print $2}'"
        pchild2 = subprocess.Popen(cmd, stdin= pchild1.stdout, \
                shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT);
        if (pdelayvalues.poll() == None):
            time.sleep(0.2);
            if (pdelayvalues.poll() == None):
                pdelayvalues.terminate();
                reply[port] = 0;
                continue;

        delayvalues = pchild2.stdout.read().decode('utf-8');
        print('delay:', delayvalues)
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
        if currentkey == "":
            reply[port] = 0;
        else:
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
#    print(f"{feedback}")


async def remotecall(websocket):
    async for message in websocket:
        print(f"< {message}")
        await deal_message(websocket, message)

async def main():
    async with websockets.serve(remotecall, "0.0.0.0", 8181):
        print("WebSocket server running at ws://0.0.0.0:8181/")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
