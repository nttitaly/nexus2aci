import pandas as pd
import numpy as np
import yaml
import math
from copy import deepcopy
import pickle
import csv
import urllib3

from cobra.mit.access import MoDirectory
from cobra.mit.session import LoginSession
from cobra.mit.request import DnQuery
from cobra.model.fv import Tenant, Ap, AEPg, BD, RsBd
from cobra.mit.request import ConfigRequest

import acicreds

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

default_tenant = {'name': '',
                  'description': '',
                  'app': [],
                  'bd': [],
                  'vrf': [],
                  'contract': [],
                  'protocol_policy': {}}

default_app = {'name': '',
               'epg': []}

default_epg = {'name': '',
               'bd': '',
               'contract': [],
               'static_path': [],
               'domain': []}

default_bd = {'name': '',
              'subnet': [],
              'vrf': ''}


excel = pd.read_excel("excelout.xlsx")

with open("tempdata.bin", "rb") as data:
    networkdata = pickle.load(data)

with open("intnames.csv", "r") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        for interface in networkdata:
            try:
                if interface['name'] == row['name']:
                    interface['newname'] = row['newname']
                    break
            except KeyError:
                pass


tenant_list = excel.Tenant.unique().tolist()
# remove nan from list
clean_tenant_list = [x for x in tenant_list if str(x) != 'nan']

data = {}

fabric = []
alltenant = []

for tenant in clean_tenant_list:
    thistenant = excel.loc[excel['Tenant'] == tenant]
    app_list = thistenant.ANP.unique().tolist()
    clean_app_list = [x for x in app_list if str(x) != 'nan']
    allapp = []
    all_tenant_bd = []

    for app in clean_app_list:
        app_bd_list = []
        thisapp = thistenant.loc[thistenant['ANP'] == app]
        epg_list = thisapp.EPG.unique().tolist()
        clean_epg_list = [x for x in epg_list if str(x) != 'nan']
        all_app_epg = []

        for epg in clean_epg_list:
            thisepg = thisapp.loc[thisapp['EPG'] == epg]
            bd_list = thisepg.BD.unique().tolist()
            clean_bd_list = [x for x in bd_list if str(x) != 'nan']
            assert len(clean_bd_list) == 1

            for bd in clean_bd_list:
                thisbd = thisepg.loc[thisepg['BD'] == bd]
                thisrow = thisbd.iloc[0]
                vrf = thisrow['VRF-NEW']
                bddict = deepcopy(default_bd)
                vlanid = int(thisrow['Vlan ID'])


                bddict.update({'name': bd,
                               'vrf': vrf})

                if isinstance(thisrow['netmask'], str):
                    netmask = thisrow['netmask'].split('.')
                    # Validate subnet mask
                    for i in range(len(netmask)-1):
                        try:
                            assert netmask[i] >= netmask[i+1]
                        except AssertionError:
                            raise AssertionError(f"Invalid subnet format {thisrow['netmask']}")

                    # Black magic from https://stackoverflow.com/questions/38085571/how-use-netaddr-to-convert-subnet-mask-to-cidr-in-python
                    cidr = sum(bin(int(x)).count('1') for x in netmask)
                    l3 = {'name': thisrow['ip_address'],
                          'mask': cidr,
                          'scope': 'public'}

                bddict['subnet'].append(l3)
                all_tenant_bd.append(bddict)

            epgdict = deepcopy(default_epg)
            epgdict.update({'name': epg,
                            'bd': bddict,
                            'old_vlan_tag': vlanid})
            all_app_epg.append(epgdict)

            epgdict = deepcopy(default_epg)
            epgdict.update({'name': epg,
                            'bd': clean_bd_list[0]})

        

        appdict = deepcopy(default_app)
        appdict.update({'name': app,
                        'epg': all_app_epg})
        allapp.append(appdict)

    vrf_list = thistenant['VRF-NEW'].unique().tolist()
    clean_vrf_list = [{'name': x} for x in vrf_list if str(x) != 'nan']

    tenantdict = deepcopy(default_tenant)
    tenantdict.update({'name': tenant,
                       'app': allapp,
                       'bd': all_tenant_bd,
                       'vrf': clean_vrf_list})
    alltenant.append(tenantdict)

for tenant in alltenant:
    for application in tenant['app']:
        for epg in application['epg']:
            for interface in networkdata:
                if "ismember" in interface:
                    continue
                try:
                    if epg['old_vlan_tag'] in interface['allowed_vlan']:
                        epg['static_path'].append({'interface': interface,
                                                   'tag': epg['old_vlan_tag']})
                except KeyError:
                    pass

                try:
                    if epg['old_vlan_tag'] == interface['native_vlan']:
                        epg['static_path'].append({'interface': interface,
                                                   'tag': 1})
                except KeyError:
                    pass


# Init ACI session
loginSession = LoginSession(acicreds.url, acicreds.username, acicreds.password)
moDir = MoDirectory(loginSession)
moDir.login()
uniMo = moDir.lookupByDn('uni')

for tenant in alltenant:
    tenantconfig = ConfigRequest()
    fvTenant = Tenant(uniMo, tenant['name'])
    tenantconfig.addMo(fvTenant)
    print(f"Creating tenant {tenant['name']}")
    moDir.commit(tenantconfig)

    for app in tenant['app']:
        fvApp = Ap(fvTenant, app['name'])
        tenantconfig.addMo(fvApp)
        print(f"Creating APP {app['name']} in Tenant {tenant['name']}")

        for epg in app['epg']:
            fvAEPg = AEPg(fvApp, epg['name'])
            tenantconfig.addMo(fvAEPg)

            fvBD = BD(fvTenant, epg['bd']['name'])
            tenantconfig.addMo(fvBD)
            print(f"Creating EPG {epg['name']} and BD {epg['bd']['name']} in APP {app['name']}")

            bind = RsBd(fvAEPg, tnFvBDName=epg['bd']['name'])
            tenantconfig.addMo(bind)
            print(f"Binding {epg['name']} to {epg['bd']['name']}")
            moDir.commit(tenantconfig)
