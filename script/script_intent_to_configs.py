#!/usr/bin/env python3
"""
generate_lan_v2.py — Génère les configs Cisco c7200 IOS 15.2 depuis un fichier intent.json
Usage : python generate_lan_v2.py intent.json

Évolutions principales :
- prise en charge d'un LAN CE de test sur Loopback0
- annonce du LAN CE en BGP via network statement
- ajout de as-override côté PE pour les VPN multi-sites dans le même AS client
- conservation du backup d'intent dans Configs/Configs-YYYYMMDD-HHMMSS/
"""

import json
import ipaddress
import sys
import shutil
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────
# CHARGEMENT / NORMALISATION
# ─────────────────────────────────────────────

def load_intent(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize_core_links(raw_links):
    """
    Supporte :
    - ancien format: ["PE1", "P1"]
    - nouveau format:
      {
        "endpoints": [
          {"node": "PE1", "interface": "Gi1/0"},
          {"node": "P1",  "interface": "Gi1/0"}
        ],
        "type": "core"
      }
    Retourne une liste normalisée.
    """
    normalized = []
    for link in raw_links:
        if isinstance(link, list) and len(link) == 2:
            normalized.append({
                "a": {"node": link[0], "interface": None},
                "b": {"node": link[1], "interface": None},
                "type": "core"
            })
        elif isinstance(link, dict) and "endpoints" in link and len(link["endpoints"]) == 2:
            ep1, ep2 = link["endpoints"]
            normalized.append({
                "a": {"node": ep1["node"], "interface": ep1.get("interface")},
                "b": {"node": ep2["node"], "interface": ep2.get("interface")},
                "type": link.get("type", "core")
            })
        else:
            raise ValueError(f"Format de lien core non supporté : {link}")
    return normalized


def get_all_nodes(as_data):
    if "nodes" in as_data:
        return list(as_data["nodes"].keys())
    topo = as_data.get("topology", {})
    return topo.get("PE", []) + topo.get("P", [])


def get_nodes_by_role(as_data, role):
    if "nodes" in as_data:
        return [name for name, data in as_data["nodes"].items() if data.get("role") == role]
    return as_data.get("topology", {}).get(role, [])


# ─────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────

def validate_intent(intent):
    errors = []

    addr = intent.get("addressing", {})
    for required in ["loopback_pool", "p2p_pool", "customer_pool", "p2p_prefix"]:
        if required not in addr:
            errors.append(f"Champ addressing.{required} manquant")

    lan_cfg = intent.get("lan", {})
    if lan_cfg.get("enabled", True):
        lan_addr = lan_cfg.get("addressing", {})
        if "base_pool" not in lan_addr:
            errors.append("Champ lan.addressing.base_pool manquant")
        if "prefix" not in lan_addr:
            errors.append("Champ lan.addressing.prefix manquant")

    for as_name, as_data in intent.get("autonomous_systems", {}).items():
        if "asn" not in as_data:
            errors.append(f"{as_name}: asn manquant")

        seen_interfaces = {}
        for link in normalize_core_links(as_data.get("links", [])):
            for ep in [link["a"], link["b"]]:
                iface = ep.get("interface")
                if iface:
                    key = (ep["node"], iface)
                    if key in seen_interfaces:
                        errors.append(f"Interface dupliquée sur {ep['node']} : {iface}")
                    seen_interfaces[key] = True

        for cust in intent.get("customers", []):
            for site in cust.get("sites", []):
                ce_link = site.get("link", {})
                endpoints = ce_link.get("endpoints", [])
                if len(endpoints) == 2:
                    for ep in endpoints:
                        iface = ep.get("interface")
                        if iface:
                            key = (ep["node"], iface)
                            if key in seen_interfaces:
                                errors.append(f"Interface dupliquée sur {ep['node']} : {iface}")
                            seen_interfaces[key] = True

    if errors:
        raise ValueError("Intent invalide :\n- " + "\n- ".join(errors))


# ─────────────────────────────────────────────
# ALLOCATION D'ADRESSES
# ─────────────────────────────────────────────

def alloc_loopbacks(nodes, pool):
    network = ipaddress.ip_network(pool)
    hosts = network.hosts()
    return {node: str(next(hosts)) for node in nodes}


def alloc_core_links(links, pool, prefix_len):
    network = ipaddress.ip_network(pool)
    subnets = network.subnets(new_prefix=prefix_len)
    result = []
    for link in links:
        subnet = next(subnets)
        hosts = list(subnet.hosts())
        result.append({
            "a_node": link["a"]["node"],
            "a_if": link["a"].get("interface"),
            "a_ip": str(hosts[0]),
            "b_node": link["b"]["node"],
            "b_if": link["b"].get("interface"),
            "b_ip": str(hosts[1]),
            "mask": str(subnet.netmask),
            "network": str(subnet.network_address),
            "prefix_len": prefix_len,
            "type": link.get("type", "core")
        })
    return result


def alloc_customer_access_links(customers, pool, prefix_len):
    network = ipaddress.ip_network(pool)
    subnets = network.subnets(new_prefix=prefix_len)
    result = {}
    for cust in customers:
        for site in cust.get("sites", []):
            subnet = next(subnets)
            hosts = list(subnet.hosts())
            endpoints = site.get("link", {}).get("endpoints", [])
            ce_ep = next((e for e in endpoints if e.get("node") == site["ce"]), {"node": site["ce"], "interface": "GigabitEthernet0/0"})
            pe_ep = next((e for e in endpoints if e.get("node") == site["pe"]), {"node": site["pe"], "interface": None})
            key = (cust["name"], site["ce"], site["pe"])
            result[key] = {
                "customer": cust["name"],
                "ce": site["ce"],
                "pe": site["pe"],
                "ce_if": ce_ep.get("interface"),
                "pe_if": pe_ep.get("interface"),
                "ce_ip": str(hosts[1]),
                "pe_ip": str(hosts[0]),
                "mask": str(subnet.netmask),
                "network": str(subnet.network_address),
                "prefix_len": prefix_len,
            }
    return result


def alloc_customer_lans(customers, lan_cfg):
    if not lan_cfg.get("enabled", True):
        return {}

    lan_addr = lan_cfg.get("addressing", {})
    pool = ipaddress.ip_network(lan_addr.get("base_pool", "10.0.0.0/8"))
    prefix_len = int(lan_addr.get("prefix", 32))
    if prefix_len < pool.prefixlen:
        raise ValueError(f"lan.addressing.prefix={prefix_len} incompatible avec le pool {pool}")

    subnets = pool.subnets(new_prefix=prefix_len)
    result = {}
    for cust in customers:
        for site_index, site in enumerate(cust.get("sites", []), start=1):
            subnet = next(subnets)
            if prefix_len == 32:
                lan_ip = str(subnet.network_address)
                mask = "255.255.255.255"
            else:
                hosts = list(subnet.hosts())
                if not hosts:
                    raise ValueError(f"Sous-réseau LAN sans hôte utilisable : {subnet}")
                lan_ip = str(hosts[0])
                mask = str(subnet.netmask)

            key = (cust["name"], site["ce"], site["pe"])
            result[key] = {
                "customer": cust["name"],
                "ce": site["ce"],
                "pe": site["pe"],
                "interface": lan_cfg.get("naming", {}).get("pattern", "Loopback0"),
                "ip": lan_ip,
                "mask": mask,
                "network": str(subnet.network_address),
                "prefix_len": prefix_len,
                "site_index": site_index,
            }
    return result


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def wildcard_from_mask(mask):
    netmask = ipaddress.ip_address(mask)
    wildcard = ipaddress.ip_address(int(ipaddress.ip_address("255.255.255.255")) - int(netmask))
    return str(wildcard)


def get_node_core_links(node, core_alloc):
    links = []
    for link in core_alloc:
        if link["a_node"] == node:
            links.append({
                "interface": link["a_if"],
                "ip": link["a_ip"],
                "neighbor": link["b_node"],
                "neighbor_ip": link["b_ip"],
                "mask": link["mask"],
                "network": link["network"],
                "prefix_len": link["prefix_len"]
            })
        elif link["b_node"] == node:
            links.append({
                "interface": link["b_if"],
                "ip": link["b_ip"],
                "neighbor": link["a_node"],
                "neighbor_ip": link["a_ip"],
                "mask": link["mask"],
                "network": link["network"],
                "prefix_len": link["prefix_len"]
            })
    return links


def make_output_dir(base_dir="Configs"):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(base_dir) / f"Configs-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ─────────────────────────────────────────────
# BLOCS DE CONFIG
# ─────────────────────────────────────────────

def gen_header(node):
    return (
        f"!\n"
        f"version 15.2\n"
        f"service timestamps debug datetime msec\n"
        f"service timestamps log datetime msec\n"
        f"hostname {node}\n"
        f"!\n"
        f"no ip domain-lookup\n"
        f"ip cef\n"
        f"!\n"
    )


def gen_loopback(node, loopbacks):
    ip = loopbacks[node]
    return (
        f"interface Loopback0\n"
        f" ip address {ip} 255.255.255.255\n"
        f" no shutdown\n"
        f"!\n"
    )


def gen_core_interfaces(node, core_alloc, mpls_enabled):
    config = ""
    for link in get_node_core_links(node, core_alloc):
        if not link["interface"]:
            raise ValueError(f"Interface manquante sur un lien core pour {node} -> {link['neighbor']}")
        config += (
            f"interface {link['interface']}\n"
            f" description to_{link['neighbor']}\n"
            f" ip address {link['ip']} {link['mask']}\n"
            f" no shutdown\n"
        )
        if mpls_enabled:
            config += " mpls ip\n"
        config += "!\n"
    return config


def gen_ospf(node, core_alloc, loopbacks):
    router_id = loopbacks[node]
    config = (
        f"router ospf 1\n"
        f" router-id {router_id}\n"
        f" network {router_id} 0.0.0.0 area 0\n"
    )
    for link in get_node_core_links(node, core_alloc):
        wildcard = wildcard_from_mask(link["mask"])
        config += f" network {link['network']} {wildcard} area 0\n"
    config += "!\n"
    return config


def gen_mpls():
    return (
        "mpls ip\n"
        "mpls label protocol ldp\n"
        "mpls ldp router-id Loopback0 force\n"
        "!\n"
    )


def gen_ibgp(node, asn, loopbacks, all_pe, rr_config):
    router_id = loopbacks[node]
    rr_node = rr_config["node"]
    rr_ip = loopbacks[rr_node]

    config = (
        f"router bgp {asn}\n"
        f" bgp router-id {router_id}\n"
        f" bgp log-neighbor-changes\n"
        f" no bgp default ipv4-unicast\n"
    )

    if node == rr_node:
        for pe in all_pe:
            if pe == node:
                continue
            pe_ip = loopbacks[pe]
            config += (
                f" neighbor {pe_ip} remote-as {asn}\n"
                f" neighbor {pe_ip} update-source Loopback0\n"
            )
    else:
        config += (
            f" neighbor {rr_ip} remote-as {asn}\n"
            f" neighbor {rr_ip} update-source Loopback0\n"
        )

    config += " !\n address-family vpnv4\n"
    if node == rr_node:
        for pe in all_pe:
            if pe == node:
                continue
            pe_ip = loopbacks[pe]
            config += (
                f"  neighbor {pe_ip} activate\n"
                f"  neighbor {pe_ip} send-community both\n"
                f"  neighbor {pe_ip} route-reflector-client\n"
            )
    else:
        config += (
            f"  neighbor {rr_ip} activate\n"
            f"  neighbor {rr_ip} send-community both\n"
        )
    config += " exit-address-family\n!\n"
    return config


def gen_vrf_and_pe_ce(node, customers, vpn_services, cust_alloc, core_asn):
    config = ""
    vrfs = vpn_services.get("vrfs", [])

    for vrf in vrfs:
        cust_name = vrf["customer"]
        vrf_name = vrf.get("name", cust_name)
        cust = next((c for c in customers if c["name"] == cust_name), None)
        if not cust:
            continue

        sites_on_this_pe = [s for s in cust.get("sites", []) if s["pe"] == node]
        if not sites_on_this_pe:
            continue

        rd = f"{core_asn}:{cust['asn']}"
        rt = f"{core_asn}:{cust['asn']}"

        config += (
            f"ip vrf {vrf_name}\n"
            f" rd {rd}\n"
            f" route-target export {rt}\n"
            f" route-target import {rt}\n"
            f"!\n"
        )

        for site in sites_on_this_pe:
            alloc = cust_alloc[(cust_name, site["ce"], node)]
            if not alloc["pe_if"]:
                raise ValueError(f"Interface PE manquante pour {cust_name}/{site['ce']} sur {node}")
            config += (
                f"interface {alloc['pe_if']}\n"
                f" description to_{site['ce']}_{cust_name}\n"
                f" ip vrf forwarding {vrf_name}\n"
                f" ip address {alloc['pe_ip']} {alloc['mask']}\n"
                f" no shutdown\n"
                f"!\n"
            )

        config += (
            f"router bgp {core_asn}\n"
            f" address-family ipv4 vrf {vrf_name}\n"
        )
        for site in sites_on_this_pe:
            alloc = cust_alloc[(cust_name, site["ce"], node)]
            config += (
                f"  neighbor {alloc['ce_ip']} remote-as {cust['asn']}\n"
                f"  neighbor {alloc['ce_ip']} activate\n"
                f"  neighbor {alloc['ce_ip']} as-override\n"
            )
        config += " exit-address-family\n!\n"

    return config


def gen_ce(site, cust, cust_alloc, cust_lans, core_asn, lan_cfg):
    ce_name = site["ce"]
    pe_name = site["pe"]
    alloc = cust_alloc[(cust["name"], ce_name, pe_name)]
    lan = cust_lans.get((cust["name"], ce_name, pe_name))
    ce_if = alloc["ce_if"] or "GigabitEthernet0/0"

    config = gen_header(ce_name)
    config += (
        f"interface {ce_if}\n"
        f" description to_{pe_name}\n"
        f" ip address {alloc['ce_ip']} {alloc['mask']}\n"
        f" no shutdown\n"
        f"!\n"
    )

    if lan:
        config += (
            f"interface {lan['interface']}\n"
            f" description LAN_TEST_{cust['name']}_{ce_name}\n"
            f" ip address {lan['ip']} {lan['mask']}\n"
            f" no shutdown\n"
            f"!\n"
        )

    config += (
        f"router bgp {cust['asn']}\n"
        f" bgp router-id {alloc['ce_ip']}\n"
        f" bgp log-neighbor-changes\n"
        f" neighbor {alloc['pe_ip']} remote-as {core_asn}\n"
        f" !\n"
        f" address-family ipv4\n"
        f"  neighbor {alloc['pe_ip']} activate\n"
    )

    advertise = lan_cfg.get("bgp", {}).get("advertise", True)
    method = lan_cfg.get("bgp", {}).get("method", "network_statement")
    if lan and advertise and method == "network_statement":
        config += f"  network {lan['ip']} mask {lan['mask']}\n"

    config += (
        f" exit-address-family\n"
        f"!\n"
        f"end\n"
    )
    return config


def gen_core_router(node, role, as_data, asn, loopbacks, core_alloc, customers, vpn_services, cust_alloc):
    mpls_enabled = as_data.get("underlay", {}).get("mpls", {}).get("enabled", False)
    igp = as_data.get("underlay", {}).get("igp", {})
    all_pe = get_nodes_by_role(as_data, "PE")

    config = gen_header(node)
    config += gen_loopback(node, loopbacks)
    config += gen_core_interfaces(node, core_alloc, mpls_enabled)

    if igp.get("protocol") == "ospf":
        config += gen_ospf(node, core_alloc, loopbacks)

    if mpls_enabled:
        config += gen_mpls()

    if role == "PE" and as_data.get("bgp", {}).get("vpnv4"):
        config += gen_ibgp(node, asn, loopbacks, all_pe, as_data["bgp"]["route_reflector"])
        config += gen_vrf_and_pe_ce(node, customers, vpn_services, cust_alloc, asn)

    config += "end\n"
    return config


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage : python generate_lan_v2.py intent.json")
        sys.exit(1)

    intent_path = Path(sys.argv[1])
    intent = load_intent(intent_path)

    if "lan" not in intent:
        intent["lan"] = {
            "type": "loopback",
            "enabled": True,
            "addressing": {
                "base_pool": "10.0.0.0/8",
                "prefix": 32,
                "strategy": "per_site"
            },
            "naming": {
                "pattern": "Loopback0"
            },
            "bgp": {
                "advertise": True,
                "method": "network_statement"
            }
        }

    validate_intent(intent)

    addr = intent["addressing"]
    lan_cfg = intent.get("lan", {})
    customers = intent.get("customers", [])
    vpn = intent.get("vpn_services", {})

    run_dir = make_output_dir("Configs")
    shutil.copy2(intent_path, run_dir / intent_path.name)

    for _, as_data in intent["autonomous_systems"].items():
        asn = as_data["asn"]
        all_nodes = get_all_nodes(as_data)
        core_links = normalize_core_links(as_data.get("links", []))

        loopbacks = alloc_loopbacks(all_nodes, addr["loopback_pool"])
        core_alloc = alloc_core_links(core_links, addr["p2p_pool"], addr["p2p_prefix"])

        ce_pe_prefix = (
            addr.get("ce_pe_prefix")
            or addr.get("pe_ce_prefix")
            or intent.get("pe_ce", {}).get("addressing", {}).get("prefix")
            or 30
        )
        cust_alloc = alloc_customer_access_links(customers, addr["customer_pool"], ce_pe_prefix)
        cust_lans = alloc_customer_lans(customers, lan_cfg)

        for node, meta in as_data.get("nodes", {}).items():
            role = meta["role"]
            config = gen_core_router(node, role, as_data, asn, loopbacks, core_alloc, customers, vpn, cust_alloc)
            path = run_dir / f"{node}.cfg"
            path.write_text(config, encoding="utf-8")
            print(f"[OK] {path}")

        for cust in customers:
            for site in cust.get("sites", []):
                ce_name = site["ce"]
                config = gen_ce(site, cust, cust_alloc, cust_lans, asn, lan_cfg)
                path = run_dir / f"{ce_name}.cfg"
                path.write_text(config, encoding="utf-8")
                print(f"[OK] {path}")

    print(f"\nTerminé. Configs et backup intent dans ./{run_dir}")


if __name__ == "__main__":
    main()
