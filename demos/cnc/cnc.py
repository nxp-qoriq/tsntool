#!/usr/bin/python3
#
# SPDX-License-Identifier: (GPL-2.0 OR MIT)
#
# Copyright 2019-2022 NXP
#

from flask import Flask, render_template, request
from flask import jsonify
from flask_restful import Api, Resource
from xml.etree import ElementTree as ET
from threading import Thread
from ast import literal_eval
import json
import subprocess
import sys
import time, threading
import netconf
import pexpect
import math
import asyncio
import websockets
import pprint
import os
import xml.dom.minidom
from copy import deepcopy

GROUP_NAME="real-time-edge"

def removeknowhost():
	print("remove /root/.ssh/known_hosts");
	subprocess.call(["rm", "-f", '/root/.ssh/known_hosts']);
	ssh_config_path = '/root/.ssh/config'
	ssh_config_content = """
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile=/dev/null
"""
	try:
		os.makedirs('/root/.ssh', exist_ok=True)
		with open(ssh_config_path, 'w') as f:
			f.write(ssh_config_content)
		os.chmod(ssh_config_path, 0o600)
		print(f"SSH config updated: {ssh_config_path}")
	except Exception as e:
		print(f"Failed to update SSH config: {e}")

def prettyXml(element, indent = '\t', newline = '\n', level = 0):
    if element:
        if element.text == None or element.text.isspace():
            element.text = newline + indent * (level + 1)    
        else:  
            element.text = newline + indent * (level + 1) + element.text.strip() + newline + indent * (level + 1)  
    temp = list(element)
    for subelement in temp:  
        if temp.index(subelement) < (len(temp) - 1):
            subelement.tail = newline + indent * (level + 1)  
        else:
            subelement.tail = newline + indent * level  
        prettyXml(subelement, indent, newline, level = level + 1)

def loadNetconf(xmlstr, device):
    print (xmlstr);
    #start the netconf request
    session = netconf.Session.connect(device, int('830'), str('root'))
    dstype = netconf.RUNNING
    status = session.editConfig(target=dstype, source=xmlstr, defop=netconf.NC_EDIT_DEFOP_MERGE,
	erropt=netconf.NC_EDIT_ERROPT_NOTSET, testopt=netconf.NC_EDIT_TESTOPT_TESTSET)
    print('editconfig %s feedback: %s\n'%(dstype, status));
    getfeedback = session.getConfig(dstype);

    del(session);
    return (status, getfeedback);

#tsn config for tsn yang v2
def loadnetconfqbv(configdata):
    print(configdata);
    interfaces = ET.Element('interfaces');
    interfaces.set('xmlns', 'urn:ietf:params:xml:ns:yang:ietf-interfaces');
    interfaces.set('xmlns:sched', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-sched');
    interfaces.set('xmlns:sched-bridge', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-sched-bridge');
    interfaces.set('xmlns:dot1q', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-bridge');
    interfaces.set('xmlns:ianaift', 'urn:ietf:params:xml:ns:yang:iana-if-type');

    port = ET.SubElement(interfaces, 'interface');
    iname = ET.SubElement(port, 'name');
    iname.text = configdata['port'];

    enable = ET.SubElement(port, 'enabled');
    enable.text = 'true';

    itype = ET.SubElement(port, 'type');
    itype.text = 'ianaift:ethernetCsmacd';

    brport = ET.SubElement(port, 'dot1q:bridge-port');
    admin = ET.SubElement(brport, 'sched-bridge:gate-parameter-table');
    gate_enable = ET.SubElement(admin, 'sched-bridge:gate-enabled');
    gate_enable.text = configdata['enable'];

    configchange = ET.SubElement(admin, 'sched-bridge:config-change');
    configchange.text = 'true';

    #admin = ET.SubElement(port, 'admin');
    print(configdata['enable']);
    if (configdata['enable'] == 'false'):
        prettyXml(interfaces);
        ET.dump(interfaces);
        qbvxmlb = ET.tostring(interfaces, encoding='utf8', method='xml');
        qbvxmlstr = str(qbvxmlb, encoding='utf-8');

        return loadNetconf(qbvxmlstr, configdata['device']);

    admin_state = ET.SubElement(admin, 'sched-bridge:admin-gate-states');
    admin_state.text = '255';
    sup_list = ET.SubElement(admin, 'sched-bridge:supported-list-max');
    sup_list.text = '64';
    sup_inverval = ET.SubElement(admin, 'sched-bridge:supported-interval-max');
    sup_inverval.text = '1000000000';
    admin_gcl = ET.SubElement(admin, 'sched-bridge:admin-control-list');
    admin_gcl.set('xmlns:nc', 'urn:ietf:params:xml:ns:netconf:base:1.0');
    admin_gcl.set('nc:operation', 'replace');

    ctsum = 0;
    for i in range(len(configdata['entry'])):
        gateentry = ET.SubElement(admin_gcl,'sched-bridge:gate-control-entry');

        gindex = ET.SubElement(gateentry, 'sched-bridge:index');
        gindex.text = str(i);

        oname = ET.SubElement(gateentry, 'sched-bridge:operation-name');
        oname.text = 'sched:set-gate-states';

        gatestate = ET.SubElement(gateentry, 'sched-bridge:gate-states-value');
        gatestate.text = str(configdata['entry'][i]['gate']);
        ti = ET.SubElement(gateentry, 'sched-bridge:time-interval-value');
        ti.text = str(configdata['entry'][i]['period']);
        ctsum = ctsum + int(configdata['entry'][i]['period']);

    cycletime = ET.SubElement(admin, 'sched-bridge:admin-cycle-time');
    ctnumerator = ET.SubElement(cycletime, 'sched-bridge:numerator');
    if configdata.__contains__('cycletime'):
        ctnumerator.text = configdata['cycletime'];
    else:
        ctnumerator.text = str(ctsum);
    ctdenominator = ET.SubElement(cycletime, 'sched-bridge:denominator');
    ctdenominator.text = '1000000000';
    if configdata.__contains__('basetime'):
        xs,zs=math.modf(float(configdata['basetime']));
        xsstr = str(xs).split('.');
        if (len(xsstr[1]) > 8):
            xshu = xsstr[1][0:9];
        else:
            xshu = xsstr[1].ljust(9, '0');
        basetime = ET.SubElement(admin, 'sched-bridge:admin-base-time');
        seconds = ET.SubElement(basetime, 'sched-bridge:seconds');
        seconds.text = str(int(zs));
        fragseconds = ET.SubElement(basetime, 'sched-bridge:nanoseconds');
        fragseconds.text = xshu;

    prettyXml(interfaces);
    #ET.dump(tsn);
    qbvxmlb = ET.tostring(interfaces, encoding='utf8', method='xml');

    qbvxmlstr = str(qbvxmlb, encoding='utf-8');

    return loadNetconf(qbvxmlstr, configdata['device']);

def loadbridge_vlan(configdata):
    bridges = ET.Element('bridges');
    bridges.set('xmlns', "urn:ieee:std:802.1Q:yang:ieee802-dot1q-bridge");
    bridges.set('xmlns:nc', "urn:ietf:params:xml:ns:netconf:base:1.0");

    bridge = ET.SubElement(bridges, 'bridge');
    name = ET.SubElement(bridge, 'name');
    name.text = 'switch';
    address = ET.SubElement(bridge, 'address');
    address.text = 'd6-ad-62-c5-49-ae'

    bridgetype = ET.SubElement(bridge, 'bridge-type');
    bridgetype.text = 'provider-bridge'
    component = ET.SubElement(bridge, 'component');
    name_ = ET.SubElement(component, 'name');
    name_.text = 'eno0';
    type_ = ET.SubElement(component, 'type');
    type_.text = 'edge-relay-component';
    bridgeVlan = ET.SubElement(component, 'bridge-vlan');
    vlan = ET.SubElement(bridgeVlan, 'vlan');
    vlan.set('nc:operation', "replace");
    vid = ET.SubElement(vlan, 'vid');
    vid.text = configdata['vid'];
    portname = ET.SubElement(vlan, 'name');
    portname.text = configdata['port_name'];

    prettyXml(bridges);
    bridgexmlb = ET.tostring(bridges, encoding='utf8', method='xml');
    bridgexmlstr = str(bridgexmlb, encoding='utf-8');
    print(configdata['device'])
    return loadNetconf(bridgexmlstr, configdata['device']);

def loadstream_handle(configdata):
    print(configdata);
    streamid = ET.Element('stream-identity');
    streamid.set('xmlns', "urn:ieee:std:802.1Q:yang:ieee802-dot1cb-stream-identification");

    index = ET.SubElement(streamid, 'index');
    index.text = configdata['index'];
    streamhandle = ET.SubElement(streamid, 'handle');
    streamhandle.text = configdata['streamhandle'];
    oface = ET.SubElement(streamid, 'out-facing');
    iport = ET.SubElement(oface, 'input-port');
    iport.text = configdata['iport'];
    oport = ET.SubElement(oface, 'output-port');
    oport.text = configdata['oport'];
    streamidentification = ET.SubElement(streamid, 'null-stream-identification');
    dmac = ET.SubElement(streamidentification, 'destination-mac');
    dmac.text = configdata['macaddr'];
    vlantype = ET.SubElement(streamidentification, 'tagged');
    vlantype.text = configdata['vlantype'];
    vlanid = ET.SubElement(streamidentification, 'vlan');
    vlanid.text = configdata['vlanid'];

    prettyXml(streamid);
    streamxmlb = ET.tostring(streamid, encoding='utf8', method='xml');
    streamxmlstr = str(streamxmlb, encoding='utf-8');
    print(configdata['device'])
    return loadNetconf(streamxmlstr, configdata['device']);

def loadport(configdata):
    print(configdata);
    interfaces = ET.Element('interfaces');
    interfaces.set('xmlns', 'urn:ietf:params:xml:ns:yang:ietf-interfaces');
    interfaces.set('xmlns:ianaift', 'urn:ietf:params:xml:ns:yang:iana-if-type');

    for i in range(len(configdata['ports'])):
        interface = ET.SubElement(interfaces, 'interface');
        name = ET.SubElement(interface, 'name');
        name.text = configdata['ports'][i];
        enable = ET.SubElement(interface, 'enabled');
        enable.text =  'true';
        porttype = ET.SubElement(interface, 'type');
        porttype.text = 'ianaift:ethernetCsmacd';

    prettyXml(interfaces);
    portxmlb = ET.tostring(interfaces, encoding='utf8', method='xml');
    portxmlstr = str(portxmlb, encoding='utf-8');
    print(configdata['device'])
    return loadNetconf(portxmlstr, configdata['device']);

def loadnetconfcbgen(configdata):
    print(configdata);
    frame = ET.Element('frer');
    frame.set('xmlns', "urn:ieee:std:802.1Q:yang:ieee802-dot1cb-frer");

    sequence = ET.SubElement(frame, 'sequence-generation');
    index = ET.SubElement(sequence, 'index');
    index.text = configdata['index'];
    streamhandle = ET.SubElement(sequence, 'stream');
    streamhandle.text = configdata['streamhandle'];

    sequence = ET.SubElement(frame, 'sequence-identification');
    port = ET.SubElement(sequence, 'port');
    port.text = configdata['port'];
    direction = ET.SubElement(sequence, 'direction-out-facing');
    direction.text = 'true';
    streamhandle = ET.SubElement(sequence, 'stream');
    streamhandle.text = configdata['streamhandle'];

    stream = ET.SubElement(frame, 'stream-split');
    port = ET.SubElement(stream, 'port');
    port.text = configdata['port'];
    direction = ET.SubElement(stream, 'direction-out-facing');
    direction.text = 'true';

    inputid = ET.SubElement(stream, 'input-id');
    inputid.text = configdata['input-id'];

    for i in range(len(configdata['output-id'])):
        outputid = ET.SubElement(stream, 'output-id');
        outputid.text = configdata['output-id'][i];

    prettyXml(frame);
    cbgenxmlb = ET.tostring(frame, encoding='utf8', method='xml');
    cbgenxmlstr = str(cbgenxmlb, encoding='utf-8');
    print(configdata['device'])
    return loadNetconf(cbgenxmlstr, configdata['device']);

def loadnetconfcbrec(configdata):
    print(configdata);
    frame = ET.Element('frer');
    frame.set('xmlns', "urn:ieee:std:802.1Q:yang:ieee802-dot1cb-frer");

    reclist = ET.SubElement(frame, 'sequence-recovery');
    index = ET.SubElement(reclist, 'index');
    index.text = configdata['index']
    hislen = ET.SubElement(reclist, 'history-length');
    hislen.text = configdata['hislen']
    portlist = ET.SubElement(reclist, 'port');
    portlist.text = configdata['port_name']

    prettyXml(frame);
    cbrecxmlb = ET.tostring(frame, encoding='utf8', method='xml');
    cbrecxmlstr = str(cbrecxmlb, encoding='utf-8');
    print(configdata['device'])
    return loadNetconf(cbrecxmlstr, configdata['device']);

def loadnetconfqbu(configdata):
    print(configdata);
    interfaces = ET.Element('interfaces');
    interfaces.set('xmlns', 'urn:ietf:params:xml:ns:yang:ietf-interfaces');
    interfaces.set('xmlns:dot1q', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-bridge');
    interfaces.set('xmlns:sched', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-sched');
    interfaces.set('xmlns:sched-bridge', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-sched-bridge');
    interfaces.set('xmlns:preempt-bridge', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-preemption-bridge');
    interfaces.set('xmlns:nc', 'urn:ietf:params:xml:ns:netconf:base:1.0');
    port = ET.SubElement(interfaces, 'interface');
    iname = ET.SubElement(port, 'name');
    iname.text = configdata['port'];
    enable = ET.SubElement(port, 'enabled');
    enable.text =  'true';

    itype = ET.SubElement(port, 'type');
    itype.set('xmlns:ianaift', 'urn:ietf:params:xml:ns:yang:iana-if-type');
    itype.text = 'ianaift:ethernetCsmacd';

    brport = ET.SubElement(port, 'dot1q:bridge-port');
    tclist = ET.SubElement(brport, 'preempt-bridge:frame-preemption-parameters');
    tclist.set('nc:operation', 'replace');

    tctable = ET.SubElement(tclist, 'preempt-bridge:frame-preemption-status-table');
    for i in range(len(configdata['plist'])):
        index = ET.SubElement(tctable, 'preempt-bridge:priority'+str(configdata['plist'][i]['tc']));
        index.text = configdata['plist'][i]['preemptable'];

    prettyXml(interfaces);
    #ET.dump(interfaces);
    qbuxmlb = ET.tostring(interfaces, encoding='utf8', method='xml');
    qbuxmlstr = str(qbuxmlb, encoding='utf-8');

    return loadNetconf(qbuxmlstr, configdata['device']);

def loadncqcisid(configdata):
    print(configdata);

    pconf = dict();
    pconf['device'] = configdata['device'];
    if (configdata.__contains__('iport') == False):
        configdata['iport'] = configdata['port'];
        configdata['oport'] = configdata['port'];

    pconf['ports'] = [configdata['port']];
    loadport(pconf);
    return loadstream_handle(configdata);

def createsfixml(component, configdata):
    streamfilter = ET.SubElement(component, 'psfp-bridge:stream-filters');
    sfitable = ET.SubElement(streamfilter, 'psfp-bridge:stream-filter-instance-table');
    index = ET.SubElement(sfitable, 'psfp-bridge:stream-filter-instance-id');
    index.text = configdata['index'];
    enable = ET.SubElement(sfitable, 'qci-augment:stream-filter-enabled');
    enable.text = configdata['enable'];
    if (configdata['enable'] == 'false'):
        return

    if (configdata.__contains__('streamhandle')):
        streamhandle = ET.SubElement(sfitable, 'psfp-bridge:stream-handle');
        streamhandle.text = configdata['streamhandle'];
    else:
        streamhandle = ET.SubElement(sfitable, 'psfp-bridge:wildcard');

    prio = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven');
    priority = ET.SubElement(sfitable, 'psfp-bridge:priority-spec');
    if (configdata.__contains__('priority')):
        priority.text = prio[int(configdata['priority'])];
    else:
        priority.text = 'wildcard';

    gateid = ET.SubElement(sfitable, 'psfp-bridge:stream-gate-ref');
    gateid.text = configdata['gateid'];

    if (configdata.__contains__('flowmeterid')):
        fmiid = ET.SubElement(sfitable, 'psfp-bridge:flow-meter-ref');
        fmiid.text = configdata['flowmeterid'];

    sdu = ET.SubElement(sfitable, 'psfp-bridge:max-sdu-size');
    sdu.text = '1518';
    oversizeen = ET.SubElement(sfitable, 'psfp-bridge:stream-blocked-due-to-oversize-frame-enabled');
    oversizeen.text = 'false';


def createsgixml(component, configdata):
    streamgate = ET.SubElement(component, 'psfp-bridge:stream-gates');
    sgitable =  ET.SubElement(streamgate, 'psfp-bridge:stream-gate-instance-table');
    index = ET.SubElement(sgitable, 'psfp-bridge:stream-gate-instance-id');
    index.text = configdata['index'];
    gateen = ET.SubElement(sgitable, 'psfp-bridge:gate-enable');
    gateen.text = configdata['enable'];
    if (configdata['enable'] == 'false'):
        return;

    prio = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven');

    initgate = ET.SubElement(sgitable, 'psfp-bridge:admin-gate-states');
    initgate.text = configdata['initgate'];
    initipv = ET.SubElement(sgitable, 'psfp-bridge:admin-ipv');
    intinitipv = int(configdata['initipv']);
    if (intinitipv >= 0 and intinitipv < 8) :
        initipv.text = prio[intinitipv];
    else :
        initipv.text = 'null';

    ctsum = 0;
    adminlist = ET.SubElement(sgitable, 'psfp-bridge:admin-control-list');
    for i in range(len(configdata['entry'])):
        entry = ET.SubElement(adminlist, 'psfp-bridge:gate-control-entry');
        entryindex = ET.SubElement(entry, 'psfp-bridge:index');
        entryindex.text = str(i);
        ename = ET.SubElement(entry, 'psfp-bridge:operation-name');
        ename.text = 'psfp:set-gate-and-ipv';
        egate = ET.SubElement(entry, 'psfp-bridge:gate-state-value');
        egate.text = configdata['entry'][i]['gate'];
        eti = ET.SubElement(entry, 'psfp-bridge:time-interval-value');
        eti.text = configdata['entry'][i]['period'];
        ctsum = ctsum + int(configdata['entry'][i]['period']);
        einitipv = ET.SubElement(entry, 'psfp-bridge:ipv-spec');
        eintinitipv = int(configdata['entry'][i]['ipv']);
        if (eintinitipv >= 0 and eintinitipv < 8) :
            einitipv.text = prio[eintinitipv];
        else:
            einitipv.text = 'null';

    if configdata.__contains__('basetime'):
        xs,zs=math.modf(float(configdata['basetime']));
        xsstr = str(xs).split('.');
        if (len(xsstr[1]) > 8):
            xshu = xsstr[1][0:9];
        else:
            xshu = xsstr[1].ljust(9, '0');
        basetime = ET.SubElement(sgitable, 'psfp-bridge:admin-base-time');
        seconds = ET.SubElement(basetime, 'psfp-bridge:seconds');
        seconds.text = str(int(zs));
        fragseconds = ET.SubElement(basetime, 'psfp-bridge:nanoseconds');
        fragseconds.text = xshu;

    ct = ET.SubElement(sgitable, 'psfp-bridge:admin-cycle-time');
    ctnumerator = ET.SubElement(ct, 'psfp-bridge:numerator');

    if configdata.__contains__('cycletime'):
        ctnumerator.text = configdata['cycletime'];
    else:
        ctnumerator.text = str(ctsum);

    ctdenominator = ET.SubElement(ct, 'psfp-bridge:denominator');
    ctdenominator.text = '1000000000';
    suplist = ET.SubElement(streamgate, 'psfp-bridge:supported-list-max');
    suplist.text = '184';
    supct = ET.SubElement(streamgate, 'psfp-bridge:supported-cycle-max');
    supctnu = ET.SubElement(supct, 'psfp-bridge:numerator');
    supctnu.text = '1';
    supctden = ET.SubElement(supct, 'psfp-bridge:denominator');
    supctden.text = '1';
    supitv = ET.SubElement(streamgate, 'psfp-bridge:supported-interval-max');
    supitv.text = '1000000000';

def createfmixml(component, configdata):
    flowmeter= ET.SubElement(component, 'psfp-bridge:flow-meters');
    fmitable =  ET.SubElement(flowmeter, 'psfp-bridge:flow-meter-instance-table');
    index = ET.SubElement(fmitable, 'psfp-bridge:flow-meter-instance-id');
    index.text = configdata['index'];
    enable = ET.SubElement(fmitable, 'qci-augment:flow-meter-enabled');
    enable.text = configdata['enable'];
    if (configdata['enable'] == 'false'):
        return;

    if configdata.__contains__('cir'):
        cir = ET.SubElement(fmitable, 'psfp-bridge:committed-information-rate');
        cir.text = configdata['cir'];
    if configdata.__contains__('cbs'):
        cbs = ET.SubElement(fmitable, 'psfp-bridge:committed-burst-size');
        cbs.text = configdata['cbs'];
    if configdata.__contains__('eir'):
        eir = ET.SubElement(fmitable, 'psfp-bridge:excess-information-rate');
        eir.text = configdata['eir'];
    if configdata.__contains__('ebs'):
        ebs = ET.SubElement(fmitable, 'psfp-bridge:excess-burst-size');
        ebs.text = configdata['ebs'];

    cf = ET.SubElement(fmitable, 'psfp-bridge:coupling-flag');
    if (configdata['cf'] == True):
        cf.text = 'one';
    else:
        cf.text = 'zero';

    cm = ET.SubElement(fmitable, 'psfp-bridge:color-mode');
    if (configdata['cm'] == True):
        cm.text = 'color-blind';
    else:
        cm.text = 'color-aware';

    dropyellow = ET.SubElement(fmitable, 'psfp-bridge:drop-on-yellow');
    if (configdata['dropyellow'] == True):
        dropyellow.text = 'true';
    else:
        dropyellow.text = 'false';

    supfmi = ET.SubElement(flowmeter, 'psfp-bridge:max-flow-meter-instances');
    supfmi.text = '183';

def loadncqciset(configdata):
    print(configdata);
    bridges = ET.Element('bridges');
    bridges.set('xmlns', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-bridge');
    bridges.set('xmlns:sfsg', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-stream-filters-gates');
    bridges.set('xmlns:psfp', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-psfp');
    bridges.set('xmlns:psfp-bridge', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-psfp-bridge');
    bridges.set('xmlns:qci-augment', 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-qci-augment');

    bridge = ET.SubElement(bridges, 'bridge');
    #we have to judge by port name
    brname = ET.SubElement(bridge, 'name');
    address = ET.SubElement(bridge, 'address');
    brtype = ET.SubElement(bridge, 'bridge-type');

    if (configdata['port'].find("eno") >= 0):
        brname.text = 'enetc';
        address.text = '00-00-00-00-00-01';
    else:
        brname.text = 'switch';
        address.text = '00-00-00-00-00-02';

    brtype.text = 'provider-edge-bridge';
    component = ET.SubElement(bridge, 'component');
    compname = ET.SubElement(component, 'name');
    compname.text = configdata['port'];

    comptype = ET.SubElement(component, 'type');
    comptype.text = 'edge-relay-component';
    if (configdata['whichpart'] == 'sfi'):
        createsfixml(component, configdata);
    elif (configdata['whichpart'] == 'sgi'):
        createsgixml(component, configdata);
    elif (configdata['whichpart'] == 'fmi'):
        createfmixml(component, configdata);
    else:
         print("No this config: %s"%(configdata['whichpart']));

    prettyXml(bridges);
    qcixmlb = ET.tostring(bridges, encoding='utf8', method='xml');
    qcixmlstr = str(qcixmlb, encoding='utf-8');

    return loadNetconf(qcixmlstr, configdata['device']);

def loadgetconfig(configdata):
    session = netconf.Session.connect(configdata['device'], int('830'), str('root'))
    dstype = netconf.RUNNING;
    getfeedback = session.getConfig(dstype);

    del(session);
    return ('true', getfeedback);

app = Flask(__name__)
api = Api(app)
#app.config['SECRET_KEY'] = "dfdfdffdad"

#app.config.from_object('config')
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/configstreamHTML')
def configstreamHTML():
    return render_template('indexstream.html')

@app.route('/configStreamidentifyHTML')
def configStreamidentifyHTML():
    return render_template('configStreamidentify.html')

@app.route('/configStreamQbvHTML')
def configStreamQbvHTML():
    return render_template('configStreamQbv.html')

@app.route('/configStreamCQFHTML')
def configStreamCQFHTML():
    return render_template('configStreamCQF.html')

@app.route('/configStreamQciHTML')
def configStreamQciHTML():
    return render_template('configStreamQci.html')

@app.route('/streamidentify',  methods=['POST'])
def streamidentify():
    try:
        tojson = request.get_json();
        stream = streams[tojson['sid']];
        streampath = stream['path'];
        conf = dict();
        conf['macaddr'] = tojson['macaddr'];
        conf['vlanid'] = stream['vid'];
        conf['enable'] = tojson['enable'];
        conf['vlantype'] = tojson['vlantype'];
        conf['filtertype'] = tojson['filtertype'];
        conf['streamhandle'] = tojson['sid'];
        conf['index'] = tojson['sid'];
        for i in range(len(streampath)):
            board = streampath[i][0];
            for key, value in devices.items():
                if (value['name'] == board):
                    deviceip = value['ip'];
                    break;
            conf['device'] = deviceip;
            conf['iport'] = streampath[i][1];
            conf['oport'] = streampath[i][2];

            loadncqcisid(conf);
        status = 'true';
    except Exception:
        status = 'false';
    return jsonify({"status": status});

@app.route('/qcistreamset',  methods=['POST'])
def qcistreamset():
    try:
        tojson = request.get_json();
        stream = streams[tojson['sid']];
        streampath = stream['path'];
        conf = dict();
        conf['priority'] = stream['priority'];
        conf['sid'] = tojson['sid'];
        conf['enable'] = tojson['enable'];
        conf['cir'] = tojson['cir'];
        conf['cbs'] = tojson['cbs'];
        conf['eir'] = tojson['eir'];
        conf['ebs'] = tojson['ebs'];
        conf['cf'] = tojson['cf'];
        conf['cm'] = tojson['cm'];
        conf['dropyellow'] = tojson['dropyellow'];
        conf['markred'] = tojson['markred'];

        for i in range(len(streampath)):
            board = streampath[i][0];
            port = streampath[i][1];
            if (port != '' and board.find('ls1028a') >= 0):
                    status, ret = board_qcifmi_set(board, port, conf);
    except Exception:
        status = 'false';
        return jsonify({"status": status, "getconfig": ''});
        raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

Cqfct = '';
Cqfot = '';
Cqfslots = dict();
@app.route('/cqfstreamset',  methods=['POST'])
def cqfstreamset():
    try:
        global Cqfct;
        global Cqfot;
        global Cqfslots;
        tojson = request.get_json();
        stream = streams[tojson['sid']];
        streampath = stream['path'];
        if (Cqfct == ''):
            Cqfct = tojson['cycletime'];
        elif(Cqfct != tojson['cycletime']):
            return jsonify({"status": 'false'});

        if (Cqfot == ''):
            Cqfot = tojson['opentime'];
        elif(Cqfot != tojson['opentime']):
            return jsonify({"status": 'false'});

        conf = dict();
        conf['priority'] = stream['priority'];
        conf['basetime'] = '0';
        conf['cycletime'] = Cqfct;
        conf['opentime'] = Cqfot;
        conf['sid'] = tojson['sid'];
        slotnums = int(int(Cqfct) / int(Cqfot));

        for i in range(len(streampath)):
            board = streampath[i][0];
            if (Cqfslots.get(board) == None):
                Cqfslots[board] = 0;

        suc = True;
        for i in range(slotnums):
            suc = True;
            for j in range(len(streampath) - 1):
                board = streampath[j][0];
                if ((Cqfslots[board] & (1 << (i + j))) > 0):
                    suc = False;
                    break;
            if (suc):
                offset = i;
                break;

        if (suc == False):
            return jsonify({"status": 'false'});

        for i in range(len(streampath)):
            board = streampath[i][0];
            port = streampath[i][1];
            if (port != ''):
                    delay = int(Cqfot) * (offset + i - 1);
                    board_qci_set(board, port, conf, delay);
            port = streampath[i][2];
            print(board+':'+port);
            if (port == ''):
                continue;
            delay = int(Cqfot) * (offset + i);
            Cqfslots[board] = Cqfslots[board] | (1 << (offset + i));
            board_cqf_qbv_set(board, port, conf, delay);
        status = 'true';
    except Exception:
        status = 'false';
    return jsonify({"status": status, "offset": int(Cqfot) * offset});

@app.route('/qbvstreamset',  methods=['POST'])
def qbvstreamset():
    try:
        tojson = request.get_json();
        stream = streams[tojson['sid']];
        streampath = stream['path'];
        delay = 0;
        pdelay = 0;
        conf = dict();
        conf['priority'] = stream['priority'];
        conf['basetime'] = tojson['basetime'];
        conf['cycletime'] = tojson['cycletime'];
        conf['opentime'] = tojson['opentime'];
        conf['sid'] = tojson['sid'];
        qcien = tojson['qcien'];
        for i in range(len(streampath)):
            board = streampath[i][0];
            port = streampath[i][1];
            if (qcien and port != ''):
                    board_qci_set(board, port, conf, delay);
            port = streampath[i][2];
            print(board+':'+port);
            if (port == ''):
                continue;
            delay = delay + pdelay;
            baseoffset = board_qbv_set(board, port, conf, delay)
            if (baseoffset < 0):
                status = 'false';
                break;
            pdelays = literal_eval(gdelays[board]);
            pdelay = int(pdelays[port]);
            delay = delay + baseoffset;
        status = 'true';
    except Exception:
        status = 'false';
    return jsonify({"status": status, "delay": delay});

@app.route('/configdeviceHTML')
def configdeviceHTML():
    deviceip = request.args.get('ip');
    #ret = devauthorize(deviceip);
    #removeknowhost();
    return render_template('indexdevice.html')

@app.route('/configQbvHTML')
def configQbvHTML():
    return render_template('configQbv.html')

#need to add methods = ['POST']
@app.route('/qbvset',  methods=['POST'])
def qbvset():
    try:
       tojson = request.get_json();
       print (tojson);
       print("%s "%(tojson['device']));
       print (type(tojson))
       status, ret = loadnetconfqbv(tojson);
       print (ret);
    except Exception:
       status = 'false';
       return jsonify({"status": status, "getconfig": ''});
       raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

@app.route('/cbgenset',  methods=['POST'])
def cbgen():
    try:
       tojson = request.get_json();

       ports = ['swp0', 'swp1', 'swp2', 'swp3'];
       pconf = {'ports': ports, 'device': tojson['device']};
       status, ret = loadport(pconf);
       print("loadport status: (%s)" %(status));

       portlist = tojson["portlist"];
       defmac = '00-00-00-00-00-01';
       oids = [];
       count = 0;
       for i in range(0, 4):
           if portlist[i] == "out":
               oids.append(str(count));
               streamconf = {'index': str(count), 'streamhandle': str(count), 'iport': tojson['port_name'], 'oport': ports[i], 'macaddr': defmac, 'vlantype': 'all', 'vlanid': '1', 'device': tojson['device']};
               status, ret = loadstream_handle(streamconf);
               print("loadstream_handle status: (%s)" %(status));
               count = count + 1;

       cbconf = {'index': tojson['index'], 'streamhandle': tojson['index'], 'port': tojson['port_name'], 'input-id': '0', 'output-id': oids, 'device':  tojson['device']};
       status, ret = loadnetconfcbgen(cbconf);
       print (ret);
    except Exception:
       status = 'false';
       return jsonify({"status": status, "getconfig": ''});
       raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

@app.route('/cbrecset',  methods=['POST'])
def cbrec():
    try:
       tojson = request.get_json();
       pconf = {'ports': [tojson['port_name']], 'device': tojson['device']};
       status, ret = loadport(pconf);
       print("loadport status: (%s)" %(status))
       status, ret = loadnetconfcbrec(tojson);
       print (ret);
    except Exception:
       status = 'false';
       return jsonify({"status": status, "getconfig": ''});
       raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

@app.route('/qbuset',  methods=['POST'])
def qbuset():
    try:
       tojson = request.get_json();
       status, ret = loadnetconfqbu(tojson);
       print (ret);
    except Exception:
       status = 'false';
       return jsonify({"status": status, "getconfig": ''});
       raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

@app.route('/qcisidset', methods=['POST'])
def qcisidset():
    try:
        tojson = request.get_json();
        status, ret = loadncqcisid(tojson);
        print(ret);
    except Exception:
       status = 'false';
       return jsonify({"status": status, "getconfig": ''});
       raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

@app.route('/qciset', methods=['POST'])
def qciset():
    try:
        tojson = request.get_json();
        status, ret = loadncqciset(tojson);
        print(ret);
    except Exception:
       status = 'false';
       return jsonify({"status": status, "getconfig": ''});
       raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

@app.route('/getconfig',  methods=['POST'])
def getconfig():
    try:
       tojson = request.get_json();
       print (tojson);
       status, ret = loadgetconfig(tojson);
       print (ret);
    except Exception:
       status = 'false';
       return jsonify({"status": status, "getconfig": ''});
       raise exceptions.WrongParametersException
    return jsonify({"status": status, "getconfig":str(ret)});

@app.route('/configcbgenHTML')
def configcbgenHTML():
    return render_template('configcbgen.html')

@app.route('/configcbrecHTML')
def configcbrecHTML():
    return render_template('configcbrec.html')

@app.route('/configQciHTML')
def configQciHTML():
    return render_template('configQci.html')

@app.route('/configQbuHTML')
def configQbuHTML():
    return render_template('configQbu.html')

@app.route('/configQavHTML')
def configQavHTML():
    return render_template('configQav.html')

@app.route('/configp8021cbHTML')
def configp8021cbHTML():
    return render_template('configp8021cb.html')

devices = {}
ginterfaces = {}
ginterfaces_back = {}
gneighbors = {}
gneighbors_back = {}
gdelays = {}

WS_CMD_INTERFACES = "get interfaces"
WS_CMD_NEIGHBORS = "get neighbors"
WS_CMD_DELAYS = "get delay"

async def get_ifc_nb(uri, dname):
    print(f"{uri}--->")
    global ginterfaces
    global gneighbors
    global gdelays
    try:
       async with websockets.connect(uri) as websocket:
           await websocket.send(WS_CMD_INTERFACES)
           try:
               interfaces = await asyncio.wait_for(websocket.recv(), timeout=1.0)
           except asyncio.TimeoutError:
               print('Wait receive interfaces TIMEOUT');
#           print(f"{interfaces}")
#           device['interfaces'] = 'interfaces';
           ginterfaces[dname] = json.loads(interfaces);
           await websocket.send(WS_CMD_NEIGHBORS)
           try:
               neighbors = await asyncio.wait_for(websocket.recv(), timeout=1.0)
           except asyncio.TimeoutError:
               print("Wait receive neighbors TIMEOUT");
#           print(f"{neighbors}")
#           device['neighbors'] = 'neighbors';
           gneighbors[dname] = json.loads(neighbors);
           await websocket.send(WS_CMD_DELAYS)
           try:
               delays = await asyncio.wait_for(websocket.recv(), timeout=2.0)
           except asyncio.TimeoutError:
               print("Wait receive delays TIMEOUT");
           gdelays[dname] = delays
           print(gdelays)
           print("------->----------------")
           await websocket.close();
    except websockets.ConnectionClosed:
           print("connection OK");
    except websockets.ConnectionClosedOK:
           print("close ok")
    except websockets.ConnectionClosedError:
           print("closcked Error")
    except websockets.InvalidHandshake:
           print("invalid handshake")
    except websockets.InvalidState:
           print("invaid state")
    except websockets.InvalidURI:
           print("invalid uri")
    except websockets.NegotiationError:
           print("negotiation error")
    except websockets.InvalidMessage:
           print("invalid message")
    except websockets.WebSocketException:
           print("websockets exception");
    except:
           await websocket.close()
           print("Connect "+uri+" FAIL!");

def get_device_link(uri, dname):
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    asyncio.get_event_loop().run_until_complete(get_ifc_nb(uri, dname))

neighbors_for_client= {};
interfaces_for_client = {};

def getLinksFromNeighborships():
    print("getLinksFromNeighborships")
    global gneighbors_back
    global neighbors_for_client;
    links = {'links':[]}
    neighbors_client = { };

    if not gneighbors_back:
        return (links);
    for key,value in gneighbors_back.items():
        if not value['lldp'][0]:
            continue;
        neighbors_client[key] = [];
        for interface in value['lldp'][0]['interface']:
            rneighbor = interface['chassis'][0]['descr'][0]['value']
            #default link could check 'port''current', just set 1 here
            candidate = {"source":key, "target":rneighbor, "value":1};
            rport = interface['port'][0]['descr'][0]['value']
            one_nfc = {"local_intf":interface['name'], "neighbor":rneighbor, "neighbor_intf":rport }
            neighbors_client[key].append(one_nfc);
            links['links'].append(candidate)

    neighbors_for_client = neighbors_client;
    return(links)

def getNodesFromNeighborships():
    print("getNodesFromNeighborships:")
    global ginterfaces_back
    global gneighbors_back
    global interfaces_for_client
    nodes = {'nodes':[]}
    bridges_list = []
    interfaces_client = {}
    lldpport = []

    if gneighbors_back:
        for key,value in gneighbors_back.items():
            if not value['lldp'][0]:
                continue;
            for interface in value['lldp'][0]['interface']:
                lldpport.append(interface['name']);

    if not ginterfaces_back:
        return (nodes)
    for key,value in ginterfaces_back.items():
        candidate = {"id":key,"group":2}
        nodes['nodes'].append(candidate)
        bridges_list.append(key);
        interfaces_client[key] = [];
        if not value['lldp'][0]:
            continue;
        for infs in value['lldp'][0]['interface']:
            if (infs['name'] not in lldpport):
                one_inf = {"actual_bandwith":"1000000", "admin_status": "UP",
                        "description": "1G Interface",
                        "local_intf":infs['name'],
                        "oper_status": "UP"};
                interfaces_client[key].append(one_inf)

    interfaces_for_client = interfaces_client;

    if not gneighbors_back:
        return (nodes);
    for key,value in gneighbors_back.items():
        if not value['lldp'][0]:
            continue;
        for interface in value['lldp'][0]['interface']:
            rinterface = interface['chassis'][0]['descr'][0]['value']
            if rinterface not in bridges_list:
                candidate = {"id":rinterface, "group":1}
                nodes['nodes'].append(candidate)
                bridges_list.append(rinterface)

    return(nodes)


def restructure(list_a, dict_c,  whole, current):
    for link in list_a:
        if (link['source'] == current):
            if (link['target'] not in whole):
                whole.append(link['target']);
                dict_c[link['target']] = {

                        };
                restructure(list_a, dict_c[link['target']], whole, link['target']);
        elif (link['target'] == current):
            if (link['source'] not in whole):
                whole.append(link['source']);
                dict_c[link['source']] = {

                        };
                restructure(list_a, dict_c[link['source']], whole, link['source']);

ret_path = list();
def path_get_value(dict_a, path, value):
    global ret_path;
    for k, v in dict_a.items():
        if (v != {}):
            path.append(k);
            path_get_value(v, path, value);
            if (value == k):
                ret_path = path.copy();
            path.remove(k);
        else:
            if (value == k):
                path.append(k)
                print("got path: {}".format(path));
                ret_path = path.copy();

def get_route_path(source, target):
    adict = {};
    adict[source] = {};
    whole = list();
    whole.append(source);
    SITE_ROOT = os.path.realpath(os.path.dirname(__file__))
    json_url = os.path.join(SITE_ROOT, "templates/topology", "graph.json")
    with open(json_url, 'r') as outfile:
        graph = json.loads(outfile.read())
        print(graph)
        restructure(graph['links'], adict[source] , whole, source)

    print(whole)
    print(adict)

    path_get_value(adict, [], target);
    print("PATH:========={}================".format(ret_path))
    print(str(ret_path))
    return ({
            'path' : str(ret_path)
            })

streams = {};
streampaths = [];
streampath_tmp = [];

def lookup_streampath(target, local, local_intf):
    global neighbors_for_client;
    global streampath_tmp;
    global streampaths;

    if local == target:
        streampath_tmp.append([local, local_intf, '']);
        streampaths.append(streampath_tmp.copy());
        streampath_tmp.pop();
        return;

    for node in streampath_tmp:
        if local == node[0]:
            return;

    for item in neighbors_for_client[local]:
        if item['local_intf'] == local_intf:
            continue;
        streampath_tmp.append([local, local_intf, item['local_intf']]);
        lookup_streampath(target, item['neighbor'], item['neighbor_intf']);
        streampath_tmp.pop();

    return;

def board_qbv_conf_set(board, port, conf):
    for key, value in devices.items():
        if (value['name'] == board):
            deviceip = value['ip'];
            break;
    basetime = float(conf['basetime'])/1000000000;
    conf['basetime'] = str(basetime);
    conf['device'] = deviceip;
    conf['port'] = port;
    conf['enable'] = 'true';
    ret = loadnetconfqbv(conf);
    return ret;

def board_qbv_conf_get(board, port):
    for key, value in devices.items():
        if (value['name'] == board):
            deviceip = value['ip'];
            break;

    configdata = dict();
    configdata['device'] = deviceip;
    status, getfeedback = loadgetconfig(configdata);
    getfeedback = "<root>" + getfeedback + "</root>";
    dom1 = xml.dom.minidom.parseString(getfeedback);
    interfacelist = dom1.getElementsByTagName('interface');
    for interface in interfacelist:
        name = interface.getElementsByTagName('name')[0];
        if (name.firstChild != None and name.firstChild.data == port):
            brport = interface.getElementsByTagName('bridge-port')[0];
            nodelist = brport.getElementsByTagName('gate-parameter-table');
            for node in nodelist:
                if (node.getAttribute("xmlns") == 'urn:ieee:std:802.1Q:yang:ieee802-dot1q-sched'):
                    btnode = node.getElementsByTagName('admin-base-time')[0];
                    btsec = btnode.getElementsByTagName('seconds')[0].firstChild.data;
                    btnsec = btnode.getElementsByTagName('nanoseconds')[0].firstChild.data;
                    basetime = int(btsec)*1000000000 + int(btnsec);
                    admin_cl = node.getElementsByTagName('admin-control-list')[0];
                    gcl_list = admin_cl.getElementsByTagName('gate-control-entry');
                    gcls = [];
                    cycletime = 0;
                    for gcl_node in gcl_list:
                        gate = gcl_node.getElementsByTagName('gate-states-value')[0].firstChild.data;
                        interval = gcl_node.getElementsByTagName('time-interval-value')[0].firstChild.data;
                        gcls.append({'gate':gate, 'period':interval});
                        cycletime += int(interval);
                    conf = {'basetime':str(basetime), 'cycletime': str(cycletime), 'entry': gcls};
                    return conf;
            break;

    return None;

def board_cqf_qbv_set(board, port, streamconf, delay):
    conf = board_qbv_conf_get(board, port);
    print("Get CQF Qbv config params:");
    print (conf);
    if (conf == None):
        print("There is no CQF Qbv config on this device port");
        gatestatus = 1 << int(streamconf['priority']);
        basetime = streamconf['basetime'];
        closetime = int(streamconf['cycletime']) - int(streamconf['opentime']) - delay;
        closegate = 255 & (~gatestatus);
        conf = {'basetime': basetime, 'cycletime': streamconf['cycletime'], 'entry': [{'gate': str(closegate), 'period': delay}, {'gate': str(gatestatus), 'period': streamconf['opentime']}, {'gate': str(closegate), 'period': str(closetime)}]};
        print("Qbv config data:");
        print (conf);
        board_qbv_conf_set(board, port, conf);
        return 0;

    gatestatus = 1 << int(streamconf['priority']);
    for entry in conf['entry']:
        entry['gate'] = str(int(entry['gate']) & (~gatestatus));

    offset = 0;
    i = 0;
    for entry in conf['entry']:
        if (delay == offset):
            lefttime = int(entry['period']) - int(streamconf['opentime']);
            if (lefttime == 0):
                entry['gate'] = str(gatestatus);
            else:
                entry['period'] = str(lefttime);
                conf['entry'].insert(i, {'gate':str(gatestatus), 'period':streamconf['opentime']});
            break;
        elif (delay < (offset + int(entry['period']))):
            entry['period'] = str(delay - offset);
            conf['entry'].insert(i + 1, {'gate':str(gatestatus), 'period':streamconf['opentime']});
            lefttime = offset + int(entry['period']) - delay - int(streamconf['opentime']);
            if (lefttime > 0):
                conf['entry'].insert(i + 2, {'gate':entry['gate'], 'period':str(lefttime)});
            break;
        else:
            offset = offset + int(entry['period']);
            i = i + 1;
    board_qbv_conf_set(board, port, conf);
    return 0;

def board_qbv_set(board, port, streamconf, delay):
    conf = board_qbv_conf_get(board, port);
    print("Get Qbv config params:");
    print (conf);
    btime = int(float(streamconf['basetime'])*1000000000);
    if (conf == None):
        print("There is no Qbv config on this device port");
        gatestatus = 1 << int(streamconf['priority']);
        btime += delay;
        closetime = int(streamconf['cycletime']) - int(streamconf['opentime']);
        conf = {'basetime': str(btime), 'cycletime': streamconf['cycletime'], 'entry': [{'gate': str(gatestatus), 'period': streamconf['opentime']}, {'gate': str(255), 'period': str(closetime)}]};
        print("Qbv config data:");
        print (conf);
        board_qbv_conf_set(board, port, conf);
        return 0;

    if (conf['cycletime'] != streamconf['cycletime']):
        return -1;

    begin = (btime + delay - int(conf['basetime'])) % int(conf['cycletime']);
    interval_len = 0;
    offset = 0;
    i = 0;
    for entry in conf['entry']:
        i = i + 1;
        if (begin >= interval_len and begin < (interval_len + int(entry['period']))):
            if (int(entry['gate']) == 255 and
                    (begin + int(streamconf['opentime'])) <= (interval_len + int(entry['period']))):
                interval = interval_len + int(entry['period']) - begin - int(streamconf['opentime']);
                if (interval > 0):
                    conf['entry'].insert(i, {'gate':'255', 'period':str(interval)});
                gatestatus = 1 << int(streamconf['priority']);
                conf['entry'].insert(i, {'gate':str(gatestatus), 'period':streamconf['opentime']});
                interval = begin - interval_len;
                if (interval > 0):
                    conf['entry'].insert(i, {'gate':'255', 'period':str(interval)});
                conf['entry'].pop(i - 1);
                board_qbv_conf_set(board, port, conf);
                return offset
            else:
                offset += interval_len + int(entry['period']) - begin
                begin = interval_len + int(entry['period']);
        interval_len += int(entry['period']);

    return -1;

def board_qci_set(board, port, streamconf, delay):
    for key, value in devices.items():
        if (value['name'] == board):
            deviceip = value['ip'];
            break;

    btime = int(float(streamconf['basetime'])*1000000000);
    btime += delay;
    closetime = int(streamconf['cycletime']) - int(streamconf['opentime']);
    conf = {'index': streamconf['sid'], 'enable': 'true', 'initgate': 'open', 'initipv': streamconf['priority'], 'basetime': str(btime), 'cycletime': streamconf['cycletime'], 'entry': [{'gate': 'open', 'period': streamconf['opentime'], 'ipv': '-1'}, {'gate': 'closed', 'period': str(closetime), 'ipv': '-1'}]};
    conf['port'] = port;
    conf['whichpart'] = 'sgi';
    conf['device'] = deviceip;
    loadncqciset(conf);

    fmid = int(streamconf['sid']) + 63;

    sficonf = {'index': streamconf['sid'], 'enable': 'true', 'streamhandle': streamconf['sid'], 'priority': streamconf['priority'], 'gateid': streamconf['sid'], 'flowmeterid': str(fmid)};
    sficonf['port'] = port;
    sficonf['whichpart'] = 'sfi';
    sficonf['device'] = deviceip;
    loadncqciset(sficonf);

def board_qcifmi_set(board, port, streamconf):
    for key, value in devices.items():
        if (value['name'] == board):
            deviceip = value['ip'];
            break;

    streamconf['port'] = port;
    streamconf['whichpart'] = 'fmi';
    streamconf['device'] = deviceip;
    if (port.find("swp") >= 0):
        fmid = int(streamconf['sid']) + 63;
    else:
        fmid = int(streamconf['sid']);
    streamconf['index'] = str(fmid);
    status, ret = loadncqciset(streamconf);
    if not (status):
        return (status, ret)

    sficonf = {'index': streamconf['sid'], 'enable': 'true', 'streamhandle': streamconf['sid'], 'priority': streamconf['priority'], 'gateid': streamconf['sid'], 'flowmeterid': str(fmid)};
    sficonf['port'] = port;
    sficonf['whichpart'] = 'sfi';
    sficonf['device'] = deviceip;
    status, ret = loadncqciset(sficonf);
    if not (status):
        sgiconf = {'index': streamconf['sid'], 'enable': 'true', 'initgate': 'open', 'initipv': streamconf['priority'], 'basetime': '0', 'cycletime': '1000000', 'entry': [{'gate': 'open', 'period': '1000000', 'ipv': '-1'}]};
        sgiconf['port'] = port;
        sgiconf['whichpart'] = 'sgi';
        sgiconf['device'] = deviceip;
        status, ret = loadncqciset(sgiconf);
        if (status):
            status, ret = loadncqciset(sficonf);
    return (status, ret);

@app.route('/topology/graph.json',methods=['GET'])
def get_graph_file():
    SITE_ROOT = os.path.realpath(os.path.dirname(__file__))
    json_url = os.path.join(SITE_ROOT, "templates/topology", "graph.json")
    with open(json_url, 'r') as outfile:
        graphjson = json.loads(outfile.read())
#        print(graphjson)
        return (graphjson)

@app.route('/topology/neighborships.json',methods=['GET'])
def get_device_neighbors():
   global neighbors_for_client;
   devicename = request.args.get('device');
   print(devicename)
   print(neighbors_for_client)
   if (neighbors_for_client.__contains__(devicename)):
       return({devicename:neighbors_for_client[devicename]})
   else:
       return({devicename:[]})


@app.route('/topology/no_neighbor_interfaces.json',methods=['GET'])
def get_device_noneighbor_interfaces():
    devicename = request.args.get('device');
    print(devicename)
#    print(interfaces_for_client)
    if (interfaces_for_client.__contains__(devicename)):
        return ({devicename:interfaces_for_client[devicename]})
    else:
        return({devicename:[]})

@app.route('/topology/linkdelay', methods=['GET'])
def get_ports_delay():
    global neighbors_for_client;
    global gdelays;
    source = request.args.get('source');
    target = request.args.get('target');
    portdelay = literal_eval(gdelays[source])
    print(portdelay)
    if not neighbors_for_client.__contains__(source):
        return ({
            source: "no neighbor found, check lldp is running"
            })

    print(portdelay[neighbors_for_client[source][0]['local_intf']])

    for onelink in neighbors_for_client[source]:
        print(onelink)
        if (onelink["neighbor"] == target):
            print(portdelay[onelink['local_intf']])
            return ({source: portdelay[onelink['local_intf']]})

    return ({
        source:[]
        })

@app.route('/topology/getpath', methods=['GET'])
def front_get_path():
    global neighbors_for_client;
    global gdelays;
    global streampaths;

    source = request.args.get('source');
    target = request.args.get('target');
    path = get_route_path(source, target);
    streampaths.clear();
    lookup_streampath(target, source, '');
    return (jsonify(path));

@app.route('/topology/streamregister', methods=['POST'])
def stream_register():
    global streampaths;
    global streams;

    stream = dict();
    configdata = dict();
    try:
        tojson = request.get_json();
        stream['vid'] = tojson['vid'];
        stream['priority'] = tojson['priority'];
        pathid = int(tojson['pathid']);
        stream['path'] = streampaths[pathid];
        streams[tojson['streamid']] = stream.copy();

        configdata['vid'] = stream['vid'];
        for i in range(len(streampaths[pathid])):
            configdata['port_name'] = streampaths[pathid][i][1];
            board = streampaths[pathid][i][0];
            for key, value in devices.items():
                if (value['name'] == board):
                    deviceip = value['ip'];
                    break;
            configdata['device'] = deviceip;
            if (configdata['port_name'] != ''):
                loadbridge_vlan(configdata);
            configdata['port_name'] = streampaths[pathid][i][2];
            if (configdata['port_name'] != ''):
                loadbridge_vlan(configdata);

        status = 'true';

    except Exception:
        status = 'false';

    return jsonify(streams);

@app.route('/topology/getstreampaths', methods=['GET'])
def get_stream_paths():
    global streampaths;

    pathdict = dict();
    for i in range(len(streampaths)):
        str = '';
        for j in range(len(streampaths[i])):
            str = str + streampaths[i][j][0] + '-';
        pathdict[i] = str;

    return (jsonify(pathdict));


def get_topology():
    '''
    NOW LETS FORMAT THE DICTIONARY TO NEEDED D3 LIbary JSON
    '''
    print("================")
    print("NODES DICTIONARY")
    print("================")
    nodes_dict = getNodesFromNeighborships()
    print(nodes_dict)

    print("================")
    print("LINKS DICTIONARY")
    print("================")
    links_dict = getLinksFromNeighborships()
    print(links_dict)

    print("==========================================")
    print("VISUALIZATION graph.json DICTIONARY MERGE")
    print("==========================================")
    visualization_dict = {'nodes':nodes_dict['nodes'],'links':links_dict['links']}

    SITE_ROOT = os.path.realpath(os.path.dirname(__file__))
    json_url = os.path.join(SITE_ROOT, "templates/topology", "graph.json")
    with open(json_url, 'w') as outfile:
        json.dump(visualization_dict, outfile, sort_keys=True, indent=4)
        print("")
        print("JSON printed into graph.json")

def probe_boards(n):
    global devices
    global gneighbors_back;
    global ginterfaces_back;
    while True:
        devices_temp = {}
        output = subprocess.Popen("avahi-browse -a -d local -t | grep {}".format(GROUP_NAME), \
                shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT)

        i = 0
        for line in iter(output.stdout.readline, b''):
            line_txt = line.decode("utf-8")
            devices_list = line_txt.split()
            if (devices_list[4] != 'SSH') and (devices_list[4] != 'ssh._tcp') :
                continue
            board = '%s.local'%(devices_list[3])
            resulte_list = [];
            while True:
                result = subprocess.Popen('avahi-resolve-host-name %s' %(board), \
                        shell = True, stdout =subprocess.PIPE, stderr=subprocess.STDOUT)

                resultb = result.stdout.readline()
                resulte_txt = resultb.decode("utf-8")
                resulte_list = resulte_txt.split()
                if (len(resulte_list[1]) < 18):
                    break;
            if (resulte_list[0] == 'Failed') :
                continue
            if (devices_temp.__contains__(resulte_list[0])) :
                continue
            devices_temp[resulte_list[0]] = resulte_list[1]
            i += 1

        mutex.acquire()
        print("==========================")
        pprint.pprint(gneighbors.keys())
        print("--------------------------")
        pprint.pprint(ginterfaces.keys())
        print("==========================")
        get_topology()

        ginterfaces_back = {};
        ginterfaces_back = deepcopy(ginterfaces);
        gneighbors_back = {};
        gneighbors_back = deepcopy(gneighbors);

        ginterfaces.clear()
        gneighbors.clear()
        devices.clear()

        j = 0
        for key, value in devices_temp.items():
            devices[j] = {'name': key, 'ip': value}
            uri = f"ws://{value}:8181"
            t = Thread(target=get_device_link, args=(uri, key))
            t.daemon = True
            t.start()
            j += 1
        mutex.release()
        print (devices)
        time.sleep(5)

@app.route('/getdevices')
def getdevices():
	global devices
	mutex.acquire()
	reply = jsonify(devices)
	mutex.release()
	return reply

REST_APIs = {
        '/restapi'  : "List all REST APIs",
        '/restapi/topo' : "topology graph data",
        '/restapi/devicelist' : "get devices list",
        '/restapi/devicelist/<devicename>' : "get one device interfaces",
	'/restapi/delays' : "get ports delay",
	'/restapi/delays/<devicename>' : "get one device ports delay",
        '/restapi/getpath/<source>-<target>' : "get path between two devices"
        }

class restapi_list(Resource):
    def get(self):
        return REST_APIs

class topoview(Resource):
    def get(self):
        print ("RESTful API get topoview");
        with open("templates/topology/graph.json", 'r') as outfile:
            graphjson = json.loads(outfile.read())
            print(graphjson)
            return {'code': 200, 'msg': 'success', 'data':graphjson}
        return {'code': 404, 'msg': 'failure', 'data':{}}

    def post(self):
        return {'code': 404, 'msg': 'failure', 'data':{}}

class device_list(Resource):
    def get(self):
        global devices
        return {
                'code' : 200,
                'msg' : 'success',
                'data' : devices
                }

class device_detail(Resource):
    def get(self, devicename):
        if not ginterfaces[devicename]:
            return {'code': 404, 'msg': 'failure, no such device', 'data':{}}
        return {'code': 200, 'msg': 'success', 'data':ginterfaces[devicename]}

class delays(Resource):
    def get(self):
        global gdelays
        return {
                'code' : 200,
                'msg' : 'success',
                'data' : gdelays
                }

class delays_ports(Resource):
    def get(self, devicename):
        global gdelays
        if not gdelays[devicename]:
            return {'code': 404, 'msg': 'failure, no such device', 'data':{}}
        return {'code': 200, 'msg': 'success', 'data':gdelays[devicename]}

class get_path(Resource):
    def get(self, source, target):
        path = get_route_path(source, target);
        print("--getting path: %s - %s"%(source, target));
        return {
                'code' : 200,
                'msg' : 'success',
                'data' : path
                }

if (len(sys.argv) > 1):
       GROUP_NAME = sys.argv[1]

print("Group Name : %s"%(GROUP_NAME))

try:
   t_probeboards = threading.Thread(target=probe_boards, args=(5,))
   t_probeboards.start()
except:
	print ("Error: start new threading")

mutex = threading.Lock()

api.add_resource(restapi_list, '/restapi')
api.add_resource(topoview, '/restapi/topo')
api.add_resource(device_list, '/restapi/devicelist')
api.add_resource(device_detail, '/restapi/devicelist/<string:devicename>')
api.add_resource(delays, '/restapi/delays')
api.add_resource(delays_ports, '/restapi/delays/<string:devicename>')
api.add_resource(get_path, '/restapi/getpath/<string:source>,<string:target>')

if __name__ == '__main__':
    removeknowhost()
    app.run(host = "0.0.0.0" , port = 8180)

t_probeboards.join()

