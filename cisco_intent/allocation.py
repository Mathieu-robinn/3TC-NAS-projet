# -*- coding: utf-8 -*-
"""
================================================================================
allocation.py — Réseaux IP : loopbacks, liens core, accès CE-PE, LAN clients
================================================================================

Rôle :
  À partir des pools déclarés dans l'intent (``addressing``, ``lan``), ce module
  attribue des adresses IPv4 de façon déterministe (ordre des nœuds / des liens).

Structures importantes :
  - ``alloc_core_links`` : une entrée par lien avec ``a_ip``, ``b_ip``, masque, préfixe…
  - ``alloc_customer_access_links`` et ``alloc_customer_lans`` : indexés par la clé
    ``(nom_client, ce, pe)`` pour retrouver vite les paramètres d'un site.

Enrichissement underlay :
  ``_enrich_core_alloc_with_underlay`` ajoute à chaque lien l'aire OSPF et le flag MPLS
  selon ``underlay.igp`` et ``underlay.mpls`` dans l'intent.

Liens : ``generator.generate_configs`` appelle toutes ces fonctions après validation.

Pour étendre :
  - Nouveau type d'adressage : ajoute une fonction ``alloc_*`` et branche-la dans le générateur.
================================================================================
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any, Dict, List, Optional, Tuple

from cisco_intent.intent import _deep_get


def alloc_loopbacks(nodes: List[str], pool: str) -> Dict[str, str]:
    """Un hôte du pool par nœud, dans l'ordre de la liste ``nodes``."""
    network = ipaddress.ip_network(pool)
    hosts = network.hosts()
    return {node: str(next(hosts)) for node in nodes}


def alloc_core_links(links: List[Dict[str, Any]], pool: str, prefix_len: int) -> List[Dict[str, Any]]:
    """Découpe ``pool`` en sous-réseaux / préfixe ; premier hôte = extrémité A, second = B."""
    network = ipaddress.ip_network(pool)
    subnets = network.subnets(new_prefix=prefix_len)
    result: List[Dict[str, Any]] = []
    for link in links:
        subnet = next(subnets)
        hosts = list(subnet.hosts())
        result.append(
            {
                "a_node": link["a"]["node"],
                "a_if": link["a"].get("interface"),
                "a_ip": str(hosts[0]),
                "b_node": link["b"]["node"],
                "b_if": link["b"].get("interface"),
                "b_ip": str(hosts[1]),
                "mask": str(subnet.netmask),
                "network": str(subnet.network_address),
                "prefix_len": prefix_len,
            }
        )
    return result


def alloc_customer_access_links(
    customers: List[Dict[str, Any]], pool: str, prefix_len: int
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """
    Un sous-réseau P2P par site ; par convention PE = premier hôte, CE = second
    (aligné avec la génération BGP côté PE/CE).
    """
    network = ipaddress.ip_network(pool)
    subnets = network.subnets(new_prefix=prefix_len)
    result: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for cust in customers:
        for site in cust.get("sites", []):
            subnet = next(subnets)
            hosts = list(subnet.hosts())
            endpoints = site.get("link", {}).get("endpoints", [])
            ce_ep = next(
                (e for e in endpoints if e.get("node") == site["ce"]),
                {"node": site["ce"], "interface": "GigabitEthernet0/0"},
            )
            pe_ep = next(
                (e for e in endpoints if e.get("node") == site["pe"]),
                {"node": site["pe"], "interface": None},
            )
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


def alloc_customer_lans(
    customers: List[Dict[str, Any]], lan_cfg: Dict[str, Any]
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """
    Alloue une adresse (et interface) LAN de test par site client, clé ``(client, ce, pe)``.
    Retourne un dict vide si ``lan.enabled`` est faux.
    """
    if not lan_cfg.get("enabled", True):
        return {}

    lan_addr = lan_cfg.get("addressing", {})
    pool = ipaddress.ip_network(lan_addr.get("base_pool", "10.0.0.0/8"))
    prefix_len = int(lan_addr.get("prefix", 32))
    if prefix_len < pool.prefixlen:
        raise ValueError(f"lan.addressing.prefix={prefix_len} incompatible avec le pool {pool}")

    subnets = pool.subnets(new_prefix=prefix_len)
    result: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
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
            interface: Optional[str] = None
            encapsulation: Optional[int] = None

            if lan_type == "loopback":
                interface = lan_cfg.get("naming", {}).get("pattern", "Loopback0")
            elif lan_type == "interface":
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


def wildcard_from_mask(mask: str) -> str:
    """
    Masque réseau Cisco → wildcard OSPF (inversion bit à bit du masque).
    Ex. 255.255.255.252 → 0.0.0.3
    """
    netmask = ipaddress.ip_address(mask)
    wildcard = ipaddress.ip_address(int(ipaddress.ip_address("255.255.255.255")) - int(netmask))
    return str(wildcard)


def get_node_core_links(node: str, core_alloc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filtre la liste plate des liens pour ne garder que ceux où ``node`` est une extrémité."""
    links: List[Dict[str, Any]] = []
    for link in core_alloc:
        if link["a_node"] == node:
            links.append(
                {
                    "interface": link["a_if"],
                    "ip": link["a_ip"],
                    "neighbor": link["b_node"],
                    "neighbor_ip": link["b_ip"],
                    "mask": link["mask"],
                    "network": link["network"],
                    "prefix_len": link["prefix_len"],
                    "igp_area": link.get("igp_area", 0),
                    "mpls": link.get("mpls", False),
                }
            )
        elif link["b_node"] == node:
            links.append(
                {
                    "interface": link["b_if"],
                    "ip": link["b_ip"],
                    "neighbor": link["a_node"],
                    "neighbor_ip": link["a_ip"],
                    "mask": link["mask"],
                    "network": link["network"],
                    "prefix_len": link["prefix_len"],
                    "igp_area": link.get("igp_area", 0),
                    "mpls": link.get("mpls", False),
                }
            )
    return links


def _enrich_core_alloc_with_underlay(
    core_alloc: List[Dict[str, Any]],
    core_links: List[Dict[str, Any]],
    as_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Copie ``core_alloc`` et y ajoute ``igp_area`` et ``mpls`` par lien, en fonction
    du mode area OSPF (single vs explicite) et du mode MPLS (tous les liens vs explicite).
    L'index ``i`` relie le i-ème bloc alloué au i-ème lien dans l'intent normalisé.
    """
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
