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
from typing import Dict, Any, List, Optional, Tuple


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
        ]
      }
    Retourne une liste normalisée.
    """
    normalized = []
    for link in raw_links:
        if isinstance(link, list) and len(link) == 2:
            normalized.append({
                "a": {"node": link[0], "interface": None},
                "b": {"node": link[1], "interface": None},
            })
        elif isinstance(link, dict) and "endpoints" in link and len(link["endpoints"]) == 2:
            ep1, ep2 = link["endpoints"]
            normalized.append({
                "a": {"node": ep1["node"], "interface": ep1.get("interface")},
                "b": {"node": ep2["node"], "interface": ep2.get("interface")},
            })
        else:
            raise ValueError(f"Format de lien core non supporté : {link}")
    return normalized


def _deep_get(dct: Dict[str, Any], path: List[str], default=None):
    cur: Any = dct
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def normalize_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepte l'intent historique et un schéma plus récent.
    Retourne une structure cohérente (sans modifier l'original).
    """
    normalized = json.loads(json.dumps(intent))

    # Defaults LAN (historique)
    if "lan" not in normalized:
        normalized["lan"] = {
            "enabled": True,
            "type": "loopback",
            "addressing": {"base_pool": "10.0.0.0/8", "prefix": 32, "strategy": "per_site"},
            "naming": {"pattern": "Loopback0"},
            "bgp": {"advertise": True, "method": "network_statement"},
        }

    # Underlay normalization (area_design -> area.mode)
    for _, as_data in normalized.get("autonomous_systems", {}).items():
        underlay = as_data.setdefault("underlay", {})
        igp = underlay.setdefault("igp", {})
        area_design = igp.pop("area_design", None)
        area = igp.setdefault("area", {})
        if area_design and "mode" not in area:
            # historical value example: "single_area"
            area["mode"] = area_design

        mpls = underlay.setdefault("mpls", {})
        enabled_on = mpls.pop("enabled_on", None)
        interfaces = mpls.setdefault("interfaces", {})
        if enabled_on and "mode" not in interfaces:
            # historical intent used ["core_links"], keep closest semantic
            interfaces["mode"] = "all_core_links"

        # BGP normalization
        bgp = as_data.setdefault("bgp", {})
        rr = bgp.pop("route_reflector", None)
        rrs = bgp.setdefault("route_reflectors", {})
        if rr and rr.get("enabled") and not rrs.get("nodes"):
            if rr.get("node"):
                rrs["nodes"] = [rr["node"]]
        peering = bgp.setdefault("peering", {})
        if peering.get("strategy") is None:
            peering["strategy"] = "rr_clients" if rrs.get("nodes") else "full_mesh"
        if peering.get("transport") is None:
            peering["transport"] = "loopback"

    # VPN services normalization
    vpn = normalized.setdefault("vpn_services", {})
    if "rd_strategy" in vpn:
        vpn.pop("rd_strategy", None)
    if "rt_strategy" in vpn:
        # keep as legacy alias to rt.strategy
        rt = vpn.setdefault("rt", {})
        rt.setdefault("strategy", vpn.pop("rt_strategy"))

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
        lan_type = lan_cfg.get("type", "loopback")
        if lan_type not in {"loopback", "interface", "subinterface_vlan"}:
            errors.append(f"lan.type invalide: {lan_type}")
        if lan_type == "interface":
            iface = _deep_get(lan_cfg, ["interface", "name"])
            if not iface:
                errors.append("lan.interface.name requis quand lan.type=interface")
        if lan_type == "subinterface_vlan":
            parent = _deep_get(lan_cfg, ["subinterface", "parent"])
            base = _deep_get(lan_cfg, ["subinterface", "vlan_base"])
            if not parent:
                errors.append("lan.subinterface.parent requis quand lan.type=subinterface_vlan")
            if base is None:
                errors.append("lan.subinterface.vlan_base requis quand lan.type=subinterface_vlan")

    for as_name, as_data in intent.get("autonomous_systems", {}).items():
        if "asn" not in as_data:
            errors.append(f"{as_name}: asn manquant")

        # Underlay enums
        igp = _deep_get(as_data, ["underlay", "igp"], {}) or {}
        protocol = igp.get("protocol", "ospf")
        if protocol not in {"ospf", "isis"}:
            errors.append(f"{as_name}: underlay.igp.protocol invalide: {protocol}")
        area_mode = _deep_get(igp, ["area", "mode"], "single_area")
        if area_mode not in {"single_area", "explicit"}:
            errors.append(f"{as_name}: underlay.igp.area.mode invalide: {area_mode}")

        bgp = as_data.get("bgp", {}) or {}
        if bgp.get("type", "ibgp") not in {"ibgp"}:
            errors.append(f"{as_name}: bgp.type invalide: {bgp.get('type')}")
        peering_strategy = _deep_get(bgp, ["peering", "strategy"], "rr_clients")
        if peering_strategy not in {"rr_clients", "full_mesh", "rr_redundant"}:
            errors.append(f"{as_name}: bgp.peering.strategy invalide: {peering_strategy}")
        if peering_strategy in {"rr_clients", "rr_redundant"}:
            rr_nodes = _deep_get(bgp, ["route_reflectors", "nodes"], [])
            if not rr_nodes:
                errors.append(f"{as_name}: bgp.route_reflectors.nodes requis pour strategy={peering_strategy}")

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
            lan_type = lan_cfg.get("type", "loopback")
            interface = None
            encapsulation = None

            if lan_type == "loopback":
                interface = lan_cfg.get("naming", {}).get("pattern", "Loopback0")
            elif lan_type == "interface":
                # Can be overridden per site: site.lan.interface
                interface = (
                    _deep_get(site, ["lan", "interface"])
                    or _deep_get(lan_cfg, ["interface", "name"])
                    or "GigabitEthernet0/1"
                )
            elif lan_type == "subinterface_vlan":
                parent = _deep_get(site, ["lan", "parent"]) or _deep_get(lan_cfg, ["subinterface", "parent"]) or "GigabitEthernet0/1"
                vlan_base = int(_deep_get(lan_cfg, ["subinterface", "vlan_base"], 100))
                vlan = vlan_base + site_index
                interface = f"{parent}.{vlan}"
                encapsulation = vlan
            else:
                raise ValueError(f"lan.type invalide: {lan_type}")

            result[key] = {
                "customer": cust["name"],
                "ce": site["ce"],
                "pe": site["pe"],
                "interface": interface,
                "encapsulation": encapsulation,
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
                "prefix_len": link["prefix_len"],
                "igp_area": link.get("igp_area", 0),
                "mpls": link.get("mpls", False),
            })
        elif link["b_node"] == node:
            links.append({
                "interface": link["b_if"],
                "ip": link["b_ip"],
                "neighbor": link["a_node"],
                "neighbor_ip": link["a_ip"],
                "mask": link["mask"],
                "network": link["network"],
                "prefix_len": link["prefix_len"],
                "igp_area": link.get("igp_area", 0),
                "mpls": link.get("mpls", False),
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


def gen_core_interfaces(node, core_alloc):
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
        if link.get("mpls"):
            config += " mpls ip\n"
        config += "!\n"
    return config


def gen_ospf(node, core_alloc, loopbacks, area_mode: str = "single_area"):
    router_id = loopbacks[node]
    config = (f"router ospf 1\n" f" router-id {router_id}\n")
    # Loopback always in area 0 for simplicity/compat
    config += f" network {router_id} 0.0.0.0 area 0\n"
    for link in get_node_core_links(node, core_alloc):
        wildcard = wildcard_from_mask(link["mask"])
        area = int(link.get("igp_area", 0)) if area_mode != "single_area" else 0
        config += f" network {link['network']} {wildcard} area {area}\n"
    config += "!\n"
    return config


def gen_mpls():
    return (
        "mpls ip\n"
        "mpls label protocol ldp\n"
        "mpls ldp router-id Loopback0 force\n"
        "!\n"
    )


def isis_net_from_loopback(loopback_ip: str, area: str = "49.0001") -> str:
    """
    Build a deterministic, unique NET from loopback IPv4.
    Example 1.0.0.4 -> 49.0001.0100.0004.00
    """
    octets = [int(x) for x in loopback_ip.split(".")]
    if len(octets) != 4:
        raise ValueError(f"Loopback IPv4 invalide pour NET IS-IS: {loopback_ip}")
    system_id = f"{octets[0]:02d}{octets[1]:02d}.{octets[2]:02d}{octets[3]:02d}.0001"
    return f"{area}.{system_id}.00"


def gen_ibgp(node, asn, loopbacks, all_pe, peering_cfg, rr_nodes: List[str]):
    router_id = loopbacks[node]
    strategy = peering_cfg.get("strategy", "rr_clients")

    config = (
        f"router bgp {asn}\n"
        f" bgp router-id {router_id}\n"
        f" bgp log-neighbor-changes\n"
        f" no bgp default ipv4-unicast\n"
    )

    def add_neighbor(ip: str):
        return f" neighbor {ip} remote-as {asn}\n neighbor {ip} update-source Loopback0\n"

    if strategy == "full_mesh":
        for pe in all_pe:
            if pe == node:
                continue
            config += add_neighbor(loopbacks[pe])
    elif strategy in {"rr_clients", "rr_redundant"}:
        if not rr_nodes:
            raise ValueError("route_reflectors.nodes vide alors que strategy RR est demandée")
        if node in rr_nodes:
            for pe in all_pe:
                if pe == node:
                    continue
                config += add_neighbor(loopbacks[pe])
        else:
            for rr in rr_nodes:
                config += add_neighbor(loopbacks[rr])
    else:
        raise ValueError(f"Stratégie iBGP inconnue: {strategy}")

    config += " !\n address-family vpnv4\n"
    if strategy == "full_mesh":
        for pe in all_pe:
            if pe == node:
                continue
            pe_ip = loopbacks[pe]
            config += f"  neighbor {pe_ip} activate\n  neighbor {pe_ip} send-community both\n"
    else:
        # RR-based
        if node in rr_nodes:
            for pe in all_pe:
                if pe == node:
                    continue
                pe_ip = loopbacks[pe]
                config += (
                    f"  neighbor {pe_ip} activate\n"
                    f"  neighbor {pe_ip} send-community both\n"
                )
                # Only non-RR peers are RR clients.
                if pe not in rr_nodes:
                    config += f"  neighbor {pe_ip} route-reflector-client\n"
        else:
            for rr in rr_nodes:
                rr_ip = loopbacks[rr]
                config += f"  neighbor {rr_ip} activate\n  neighbor {rr_ip} send-community both\n"
    config += " exit-address-family\n!\n"
    return config


def _compute_rd_rt(vpn_services: Dict[str, Any], core_asn: int, vrf_name: str, vrf_index: int) -> Tuple[str, str]:
    # Defaults: unique per VRF
    rd_cfg = vpn_services.get("rd", {}) or {}
    rd_mode = rd_cfg.get("mode", "asn_vrfid")
    rd_base = int(rd_cfg.get("base", 100))
    vrf_id = rd_base + vrf_index
    if rd_mode == "asn_vrfid":
        rd = f"{core_asn}:{vrf_id}"
    elif rd_mode == "asn_hash":
        rd = f"{core_asn}:{abs(hash(vrf_name)) % 65535}"
    else:
        raise ValueError(f"vpn_services.rd.mode invalide: {rd_mode}")

    rt_cfg = vpn_services.get("rt", {}) or {}
    rt_strategy = rt_cfg.get("strategy", "auto_per_vrf")
    if rt_strategy == "auto_per_vrf":
        rt = f"{core_asn}:{vrf_id}"
    elif rt_strategy == "auto_per_customer_asn":
        # kept for compatibility by setting per-VRF later when customer AS known
        rt = "AUTO_PER_CUSTOMER_ASN"
    else:
        raise ValueError(f"vpn_services.rt.strategy invalide: {rt_strategy}")

    return rd, rt


def gen_vrf_and_pe_ce(node, customers, vpn_services, cust_alloc, core_asn):
    config = ""
    vrfs = vpn_services.get("vrfs", [])

    for vrf_index, vrf in enumerate(vrfs, start=1):
        cust_name = vrf["customer"]
        vrf_name = vrf.get("name", cust_name)
        cust = next((c for c in customers if c["name"] == cust_name), None)
        if not cust:
            continue

        sites_on_this_pe = [s for s in cust.get("sites", []) if s["pe"] == node]
        if not sites_on_this_pe:
            continue

        rd, rt = _compute_rd_rt(vpn_services, core_asn, vrf_name, vrf_index)
        if rt == "AUTO_PER_CUSTOMER_ASN":
            rt = f"{core_asn}:{cust['asn']}"

        config += (
            f"vrf definition {vrf_name}\n"
            f" rd {rd}\n"
            f" address-family ipv4\n"
            f"  route-target export {rt}\n"
            f"  route-target import {rt}\n"
            f" exit-address-family\n"
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
        config += f"interface {lan['interface']}\n"
        if lan.get("encapsulation"):
            config += f" encapsulation dot1Q {lan['encapsulation']}\n"
        config += (
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
    elif lan and advertise and method == "redistribute_connected":
        # Avoid redistributing the CE-PE link: match only the LAN interface
        config += (
            f"  redistribute connected route-map RM_LAN_ONLY\n"
            f" exit-address-family\n"
            f"!\n"
            f"route-map RM_LAN_ONLY permit 10\n"
            f" match interface {lan['interface']}\n"
        )
        config += "!\nend\n"
        return config

    config += (
        f" exit-address-family\n"
        f"!\n"
        f"end\n"
    )
    return config


def _enrich_core_alloc_with_underlay(core_alloc: List[Dict[str, Any]], core_links: List[Dict[str, Any]], as_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Copy to avoid mutating callers
    alloc = json.loads(json.dumps(core_alloc))
    underlay = as_data.get("underlay", {}) or {}
    igp = underlay.get("igp", {}) or {}
    area_mode = _deep_get(igp, ["area", "mode"], "single_area")
    mpls_cfg = underlay.get("mpls", {}) or {}
    mpls_enabled = bool(mpls_cfg.get("enabled", False))
    mpls_mode = _deep_get(mpls_cfg, ["interfaces", "mode"], "all_core_links") if mpls_enabled else "disabled"

    for i, link_alloc in enumerate(alloc):
        link = core_links[i]

        if area_mode == "single_area":
            link_alloc["igp_area"] = 0
        elif area_mode == "explicit":
            link_alloc["igp_area"] = int(link.get("igp_area", 0))
        else:
            raise ValueError(f"underlay.igp.area.mode invalide: {area_mode}")

        if not mpls_enabled:
            link_alloc["mpls"] = False
        else:
            if mpls_mode == "all_core_links":
                link_alloc["mpls"] = True
            elif mpls_mode == "explicit":
                link_alloc["mpls"] = bool(link.get("mpls", False))
            else:
                raise ValueError(f"underlay.mpls.interfaces.mode invalide: {mpls_mode}")

    return alloc


def gen_core_router(node, role, as_data, asn, loopbacks, core_alloc, customers, vpn_services, cust_alloc):
    igp = as_data.get("underlay", {}).get("igp", {})
    all_pe = get_nodes_by_role(as_data, "PE")
    area_mode = _deep_get(igp, ["area", "mode"], "single_area")

    config = gen_header(node)
    config += gen_loopback(node, loopbacks)
    config += gen_core_interfaces(node, core_alloc)

    if igp.get("protocol") == "ospf":
        config += gen_ospf(node, core_alloc, loopbacks, area_mode=area_mode)
    elif igp.get("protocol") == "isis":
        # IS-IS L2 underlay with per-node unique NET and loopback reachability.
        isis_net = isis_net_from_loopback(loopbacks[node])
        config += (
            "router isis 1\n"
            f" net {isis_net}\n"
            " is-type level-2-only\n"
            "!\n"
        )
        config += "interface Loopback0\n ip router isis 1\n!\n"
        for link in get_node_core_links(node, core_alloc):
            if link["interface"]:
                config += f"interface {link['interface']}\n ip router isis 1\n!\n"

    if as_data.get("underlay", {}).get("mpls", {}).get("enabled", False):
        config += gen_mpls()

    if role == "PE" and as_data.get("bgp", {}).get("vpnv4"):
        bgp = as_data.get("bgp", {}) or {}
        peering_cfg = bgp.get("peering", {}) or {}
        rr_nodes = _deep_get(bgp, ["route_reflectors", "nodes"], []) or []
        config += gen_ibgp(node, asn, loopbacks, all_pe, peering_cfg, rr_nodes)
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
    intent = normalize_intent(load_intent(intent_path))

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
        core_alloc_raw = alloc_core_links(core_links, addr["p2p_pool"], addr["p2p_prefix"])
        core_alloc = _enrich_core_alloc_with_underlay(core_alloc_raw, core_links, as_data)

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
