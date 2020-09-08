from cobra.mit.request import DnQuery

def findchildren(mo, moDir):
    dnQuery = DnQuery(mo.dn)
    dnQuery.queryTarget = "children"
    return moDir.query(dnQuery)


def find_switch_profiles(moDir):
    swprofiles = {}
    leafswprofiles = moDir.lookupByClass("infraNodeP")
    
    for leafprof in leafswprofiles:
        leaves = []
        leafselectors = moDir.lookupByClass("infraNodeBlk", 
                                            propFilter=f'wcard(infraNodeBlk.dn, "uni/infra/nprof-{leafprof.name}")')
        
        for selector in leafselectors:
            for i in range(int(selector.from_), int(selector.to_)+1):
                if i not in leaves:
                    leaves.append(i)


        # Find interface selector for normal interfaces
        # port channels are infraHPortS
        intselectors = moDir.lookupByClass("infraRsAccPortP",
                                           propFilter=f'wcard(infraRsAccPortP.dn, "uni/infra/nprof-{leafprof.name}")')
        assert len(intselectors) == 1
        for selector in intselectors:
            realselector = moDir.lookupByDn(selector.tDn)

        swprofiles[tuple(leaves)] = {"leafselector": leafprof,
                                     "interfaceselector": realselector}

    return swprofiles

