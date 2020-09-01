import re
import ipaddress
import ciscoconfparse

def allowed_vlan_to_list(vlanlist, l2dict=None):
    # Expands vlan ranges and checks if the vlan is in l2dict
    # l2dict is the result of parse_vlan_l2
    split = vlanlist.split(",")
    outlist = []
    for vlan in split:
        if "-" in vlan:
            begin, end = vlan.split("-")
            newvlans = list(range(int(begin), int(end)+1))
            outlist = outlist + newvlans
        else:
            outlist.append(int(vlan))

    if l2dict is not None:
        outlist = [x for x in outlist if x in l2dict]

    return outlist


def parse_vlan_l2(conf, l2dict=None):
    if l2dict is None:
        l2dict = {1: {}}

    vlans = conf.find_objects(r"^vlan \d{1,4}$")
    for vlan in vlans:
        vlan_id = int(vlan.text.split()[-1])
        if vlan_id not in l2dict:
            l2dict[vlan_id] = {}

        assert isinstance(l2dict[vlan_id], dict)

        for line in vlan.re_search_children("name"):
            vlan_name = re.search("name (.*)", line.text)
            l2dict[vlan_id].update({"name": vlan_name.groups()[0]})

    return l2dict


def parse_svi(conf, svidict):
    """
    Returns a dictionary with vlan names and l3 info.
    'svidict' must be output of parse_vlan_l2
    """

    svis = conf.find_objects(r"^interface Vlan")
    for svi in svis:
        # Cut away "interface Vlan"
        svi_id = int(svi.re_match(r"interface Vlan(\d{1,4})$"))

        if svi_id not in svidict:
            # vlan does not exist at layer2, leftover l3
            continue

        thissvi = {}
        thissvi["vrf"] = "default"
        for vrf in svi.re_search_children("vrf member"):
            vrf_name = vrf.re_match("vrf member (.*)")
            thissvi.update({"vrf": vrf_name})

        for vrf in svi.re_search_children("ip address"):
            ip_text = vrf.re_match(
                r"ip address (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\/\d{1,2})")
            address = ipaddress.ip_interface(ip_text)
            ip = str(address.ip)
            netmask = str(address.netmask)
            thissvi.update({"ip_address": ip, "netmask": netmask})

        for hsrp in svi.re_search_children(r"hsrp \d"):
            for address in hsrp.re_search_children("ip"):
                hsrp_ip = address.re_match(
                    r"ip (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")
                thissvi.update({"ip_address": hsrp_ip})

        thissvi.update({"shutdown": False})
        for line in svi.re_search_children("shutdown"):
            if "no" not in line.text:
                thissvi.update({"shutdown": True})

        if 'ip_address' in thissvi:
            svidict[svi_id]['l3'] = thissvi

        for line in svi.re_search_children("description"):
            description = line.re_match(r"description (.*)$")
            svidict[svi_id].update({"description": description})

    return svidict


def transform_port_numbers(dic, path):
    """
    This function "flattens" port numbers, transforming module
    3 and 4 interface numbers as if everything was module 1
    path should look like
    ["Ethernet", 1, 1, 36]
    """

    if len(path) == 3:
        is_ethernet = True if path[0] == "Ethernet" else False
        is_secondary_module = True if path[1] > 1 and path[1] < 100 else False

        if is_ethernet and is_secondary_module:
            ports_in_mod1 = len(dic["Ethernet"][1])
            path[1] = 1
            path[2] = path[2] + ports_in_mod1

    return path


def parse_switched_interface(interfaces, l2dict=None):
    # Parse switched interface and expand vlan list
    # l2dict is passed through to allowed_vlan_to_list
    thisswitch = []

    for eth in interfaces:
        # Split interface name in various pieces
        eth_type = eth.re_match(r"interface ([A-Za-z\-]*)(\/*\d*)+")
        eth_id = eth.re_match(r"interface [A-Za-z\-]*((\/*\d*)+)")

        thisint = {'name': eth_type + eth_id}
        # find description
        for line in eth.re_search_children("description"):
            description = line.re_match(r"description (.*)$")
            thisint.update({"description": description})

        # ignore ports connected to a fex
        is_fex = False
        for line in eth.re_search_children("switchport mode"):
            mode = line.re_match(r"switchport mode (.*)$")
            if mode == 'fex-fabric':
                is_fex = True

        if is_fex:
            thisswitch.append(thisint)
            continue

        # Add interface membership to channel-group
        peer_link = False
        for line in eth.re_search_children("channel-group"):
            channel_group_id = int(line.re_match(r"channel-group (\d*)"))
            mode = line.re_match(r"mode (\w*)")
            if mode == 'active':
                protocol = 'lacp-active'
            elif mode == 'passive':
                protocol = 'lacp-passive'
            elif mode == '':
                protocol = None
            else:
                raise ValueError("Could not parse link aggregation protcol")

            thisint['protocol'] = protocol

            # Peer-link, skip this interface
            if len(eth.re_search_children(r"vpc peer-link")) != 0:
                peer_link = True

            if peer_link is False:
                thisint.update({'channel-group': channel_group_id})

        if peer_link:
            thisswitch.append(thisint)
            continue

        native_vlan = None
        for line in eth.re_search_children("switchport trunk native vlan"):
            native_vlan = int(line.re_match(
                r"switchport trunk native vlan (.*)$"))
            thisint.update({"native_vlan": native_vlan})

        for line in eth.re_search_children("switchport access vlan"):
            native_vlan = int(line.re_match(r"switchport access vlan (.*)$"))
            thisint.update({"native_vlan": native_vlan})

        for line in eth.re_search_children(r"switchport trunk allowed vlan ([0-9\-\,]*)$"):
            allowed_vlan = line.re_match(
                r"switchport trunk allowed vlan ([0-9\-\,]*)$")
            allowed_vlan_list = allowed_vlan_to_list(allowed_vlan, l2dict)

            if "native_vlan" is not None:
                try:
                    allowed_vlan_list.remove(native_vlan)
                except ValueError:
                    pass

            if len(allowed_vlan_list) != 0:
                thisint.update({"allowed_vlan": allowed_vlan_list})

        for line in eth.re_search_children("switchport trunk allowed vlan add"):
            # In some cases a vlan list might be split over multiple lines
            # switchport trunk allowed vlan 3-4,6,9-10,25,50,88-89,91-99,110
            # switchport trunk allowed vlan add 700-704,800,802-803,810,1100-1101

            allowed_vlan = line.re_match(
                r"switchport trunk allowed vlan add ([0-9\-\,]*)$$")
            allowed_vlan_list = allowed_vlan_to_list(allowed_vlan, l2dict)

            thisint['allowed_vlan'] = thisint['allowed_vlan'] + allowed_vlan_list

        if "native_vlan" not in thisint:
            if mode == "access":
                thisint["native_vlan"] = 1

        for line in eth.re_search_children(r"vpc \d"):
            vpc_id = line.re_match(r"vpc (\d*)$")
            thisint.update({"vpc": int(vpc_id)})

        thisswitch.append(thisint)

    for interface in thisswitch:
        # Remove interfaces with only names
        if len(interface) == 1:
            thisswitch.remove(interface)

    return thisswitch


def match_port_channel(one_nexus_config):
    """
    Takes in 'thisswitch' from parse_switched_interface
    Returns list of port channels with members
    """

    emptypo = []
    # Find list of all port channels
    for po_int in one_nexus_config:
        if 'name' in po_int:
            if 'port-channel' in po_int['name']:
                po_id = int(po_int['name'][12:])
                po_int['members'] = []
                for eth_int in one_nexus_config:
                    if 'name' in eth_int:
                        if 'Ethernet' in eth_int['name']:
                            if 'channel-group' in eth_int:
                                if eth_int['channel-group'] == po_id:
                                    po_int['members'].append(eth_int)

                if len(po_int['members']) == 0:
                    emptypo.append(po_int)
    
    for i in emptypo:
        one_nexus_config.remove(i)

    return one_nexus_config


def match_vpc(row_config, sw1_id, sw2_id):
    # Takes in input of parse_switched_interface
    # Peers vpc configs in one single line

    vpc = []
    # Cycle all interfaces in switch 1
    for interface in row_config[sw1_id]:
        if "port-channel" in interface['name']:
            thisvpc = {'members': []}
            # Find port-channels assigned to a vpc
            if 'vpc' in interface:
                # Create vpc object, add pointer to interface in members
                thisvpcid = interface['vpc']
                thisvpc['name'] = str(thisvpcid)
                thisvpc['members'].append(interface)
                # Find other vpc member in second switch
                for interface2 in row_config[sw2_id]:
                    if "port-channel" in interface2['name']:
                        if "vpc" in interface2:
                            if interface2['vpc'] == thisvpcid:
                                thisvpc['members'].append(interface2)
                                break

                # Some VPC might only have one member
                # This happens because one of the port-channels is empty
                # and has been deleted in the previous step
                if len(thisvpc['members']) == 2:
                    vpc.append(thisvpc)
                elif len(thisvpc['members']) == 1:
                    pass
                else:
                    raise ValueError("Wrong number of VPC members")

    row_config['vpc'] = vpc
    return row_config


def flatten_dict(row_config):
    "Flattens outout of match_vpc"
    out = []
    for k, v in row_config.items():
        if k == 'vpc':
            separator = "-"
        else:
            separator = "/"

        for interface in v:
            interface['name'] = str(k) + separator + interface['name']
            out.append(interface)

    return out


def parse_nexus_pair_l2(conf1, conf2):
    # parse configs for access and distribution switches
    # you should theoretically add all switches in the DC
    sw1 = ciscoconfparse.CiscoConfParse(conf1)
    sw2 = ciscoconfparse.CiscoConfParse(conf2)

    # Combine info for VLANs for all switches.
    # Just one switch could do, but why not showing off?
    sw1_l2 = parse_vlan_l2(sw1)
    l2dict = parse_vlan_l2(sw2, sw1_l2)

    # Filter all switched interfaces from access switches
    sw1_switched = sw1.find_objects(r"^interface (port-channel|Ethernet).*")
    sw2_switched = sw2.find_objects(r"^interface (port-channel|Ethernet).*")

    sw1_parsed = parse_switched_interface(sw1_switched, l2dict)
    sw2_parsed = parse_switched_interface(sw2_switched, l2dict)

    match_port_channel(sw1_parsed)
    match_port_channel(sw2_parsed)

    cage = {1: sw1_parsed, 2: sw2_parsed}

    cage_config = match_vpc(cage, 1, 2)

    flat = flatten_dict(cage_config)

    return flat


def consolidate_interfaces(wholefabric, inttype):
    """
    Take a list of interfaces, find port-channels and 
    consolidate vlan membership between all interfaces
    Then, do the same for vpcs
    """

    for interface in wholefabric:
        if inttype in interface['name']:
            allowed_vlan = []
            protocol = None
            native_vlan = None
            for member in interface['members']:
                try:
                    for vlan in member['allowed_vlan']:
                        if vlan not in allowed_vlan:
                            allowed_vlan.append(vlan)
                    
                    del member['allowed_vlan']
                except KeyError:
                    pass

                try:
                    if native_vlan is None:
                        native_vlan = member['native_vlan']
                    else:
                        assert native_vlan == member['native_vlan']

                    del member['native_vlan']
                except KeyError:
                    pass

                try:
                    if protocol is None:
                        protocol = member['protocol']
                    else:
                        assert protocol == member['protocol']
                    
                    del member['protocol']
                except KeyError:
                    pass

                try:
                    del member['channel-group']
                except KeyError:
                    pass

            if len(allowed_vlan) > 0:
                interface['allowed_vlan'] = allowed_vlan

            if protocol is not None:
                interface['protocol'] = protocol

            if native_vlan is not None:
                interface['native_vlan'] = native_vlan
