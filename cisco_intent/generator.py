# -*- coding: utf-8 -*-
"""
================================================================================
generator.py — Intent JSON → fichiers de configuration Cisco IOS (.cfg)
================================================================================

Pipeline principal (voir ``generate_configs``) :
  1. ``load_intent`` puis ``normalize_intent`` et ``validate_intent`` (module ``intent``).
  2. Pour chaque AS : allocation des loopbacks, liens core, liens CE-PE, LAN (``allocation``).
  3. Pour chaque routeur du core : assemblage de chaînes de texte IOS (hostname, interfaces,
     IGP, MPLS, iBGP, VRF…).
  4. Pour chaque CE : config simplifiée (interface vers PE, LAN optionnel, BGP).

Retour :
  ``generate_configs`` renvoie ``(code_de_sortie, run_dir)`` : ``run_dir`` est le dossier
  ``Configs-*`` créé si tout va bien, sinon ``None``. Cela permet à la CLI ``generate --push``
  de passer ce dossier à ``gns3_push.run_push``.

Organisation du fichier :
  - Fonctions ``gen_*`` : morceaux de config réutilisables (en-tête, OSPF, BGP…).
  - ``gen_core_router`` / ``gen_ce`` : assemblage par rôle.
  - ``generate_configs`` : boucle sur l'intent et écriture des fichiers.

Pour étendre :
  - Nouvelle brique IOS : ajoute ``gen_ma_feature(...) -> str`` et appelle-la depuis
    ``gen_core_router`` ou ``gen_ce`` selon le cas.
================================================================================
"""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cisco_intent.allocation import (
    _enrich_core_alloc_with_underlay,
    alloc_core_links,
    alloc_customer_access_links,
    alloc_customer_lans,
    alloc_loopbacks,
    get_node_core_links,
    wildcard_from_mask,
)
from cisco_intent.intent import (
    _deep_get,
    get_all_nodes,
    get_nodes_by_role,
    load_intent,
    normalize_core_links,
    normalize_intent,
    validate_intent,
)
from cisco_intent.paths import make_configs_run_dir


# --- Blocs de texte IOS de base (hostname, loopback, interfaces core) ---

def gen_header(node: str) -> str:
    """Bloc d'en-tête IOS commun : version, hostname, services de base."""
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


def gen_loopback(node: str, loopbacks: Dict[str, str]) -> str:
    """Interface Loopback0 /32 pour le routeur ``node`` à partir du dict ``loopbacks``."""
    ip = loopbacks[node]
    return (
        f"interface Loopback0\n"
        f" ip address {ip} 255.255.255.255\n"
        f" no shutdown\n"
        f"!\n"
    )


def gen_core_interfaces(node: str, core_alloc: List[Dict[str, Any]]) -> str:
    """Interfaces physiques du core pour ``node`` (IP, description, ``mpls ip`` si activé)."""
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


def gen_ospf(node: str, core_alloc: List[Dict[str, Any]], loopbacks: Dict[str, str], area_mode: str = "single_area") -> str:
    """Process OSPF 1 : router-id loopback, networks core et loopback selon ``area_mode``."""
    router_id = loopbacks[node]
    config = f"router ospf 1\n router-id {router_id}\n"
    config += f" network {router_id} 0.0.0.0 area 0\n"
    for link in get_node_core_links(node, core_alloc):
        wildcard = wildcard_from_mask(link["mask"])
        area = int(link.get("igp_area", 0)) if area_mode != "single_area" else 0
        config += f" network {link['network']} {wildcard} area {area}\n"
    config += "!\n"
    return config


# --- IGP / MPLS (underlay) ---

def gen_mpls() -> str:
    """Commandes globales MPLS LDP (router-id forcé sur Loopback0)."""
    return (
        "mpls ip\n"
        "mpls label protocol ldp\n"
        "mpls ldp router-id Loopback0 force\n"
        "!\n"
    )


def isis_net_from_loopback(loopback_ip: str, area: str = "49.0001") -> str:
    """Construit une chaîne NET IS-IS à partir de l'IPv4 du loopback (system ID dérivé des octets)."""
    octets = [int(x) for x in loopback_ip.split(".")]
    if len(octets) != 4:
        raise ValueError(f"Loopback IPv4 invalide pour NET IS-IS: {loopback_ip}")
    system_id = f"{octets[0]:02d}{octets[1]:02d}.{octets[2]:02d}{octets[3]:02d}.0001"
    return f"{area}.{system_id}.00"


# --- iBGP et VPNv4 (PE uniquement, selon stratégie RR / full mesh) ---

def gen_ibgp(
    node: str,
    asn: int,
    loopbacks: Dict[str, str],
    all_pe: List[str],
    peering_cfg: Dict[str, Any],
    rr_nodes: List[str],
) -> str:
    """Bloc ``router bgp`` + address-family vpnv4 (full mesh ou route reflector selon ``peering_cfg``)."""
    router_id = loopbacks[node]
    strategy = peering_cfg.get("strategy", "rr_clients")

    config = (
        f"router bgp {asn}\n"
        f" bgp router-id {router_id}\n"
        f" bgp log-neighbor-changes\n"
        f" no bgp default ipv4-unicast\n"
    )

    def add_neighbor(ip: str) -> str:
        """Lignes ``neighbor`` iBGP avec update-source Loopback0."""
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
        if node in rr_nodes:
            for pe in all_pe:
                if pe == node:
                    continue
                pe_ip = loopbacks[pe]
                config += (
                    f"  neighbor {pe_ip} activate\n"
                    f"  neighbor {pe_ip} send-community both\n"
                )
                if pe not in rr_nodes:
                    config += f"  neighbor {pe_ip} route-reflector-client\n"
        else:
            for rr in rr_nodes:
                rr_ip = loopbacks[rr]
                config += f"  neighbor {rr_ip} activate\n  neighbor {rr_ip} send-community both\n"
    config += " exit-address-family\n!\n"
    return config


# --- VRF, RD/RT, interfaces PE vers CE ---

def _compute_rd_rt(vpn_services: Dict[str, Any], core_asn: int, vrf_name: str, vrf_index: int) -> Tuple[str, str]:
    """Calcule la valeur RD et RT (ou marqueur ``AUTO_PER_CUSTOMER_ASN``) pour une VRF."""
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
        rt = "AUTO_PER_CUSTOMER_ASN"
    else:
        raise ValueError(f"vpn_services.rt.strategy invalide: {rt_strategy}")

    return rd, rt


def _ce_needs_allowas_in(cust: Dict[str, Any]) -> bool:
    """True si le client a plusieurs sites (même ASN partagé) : le CE doit accepter l'ASN local dans l'AS_PATH."""
    sites = cust.get("sites") or []
    return len(sites) > 1


def gen_vrf_and_pe_ce(
    node: str,
    customers: List[Dict[str, Any]],
    vpn_services: Dict[str, Any],
    cust_alloc: Dict[Tuple[str, str, str], Dict[str, Any]],
    core_asn: int,
) -> str:
    """Pour le PE ``node`` : vrf definition, interfaces PE-CE en VRF, voisins eBGP par VRF (sans as-override)."""
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
                f" vrf forwarding {vrf_name}\n"
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
            )
        config += " exit-address-family\n!\n"

    return config


# --- Routeur client (CE) : lien PE, LAN test, eBGP vers le core ---

def gen_ce(
    site: Dict[str, Any],
    cust: Dict[str, Any],
    cust_alloc: Dict[Tuple[str, str, str], Dict[str, Any]],
    cust_lans: Dict[Tuple[str, str, str], Dict[str, Any]],
    core_asn: int,
    lan_cfg: Dict[str, Any],
) -> str:
    """Configuration complète d'un routeur CE (uplink PE, LAN optionnel, eBGP vers l'AS core).

    ``allowas-in`` vers le PE est ajouté seulement si le client a plusieurs sites (même ASN).
    """
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
    if _ce_needs_allowas_in(cust):
        config += f"  neighbor {alloc['pe_ip']} allowas-in\n"

    advertise = lan_cfg.get("bgp", {}).get("advertise", True)
    method = lan_cfg.get("bgp", {}).get("method", "network_statement")
    if lan and advertise and method == "network_statement":
        config += f"  network {lan['ip']} mask {lan['mask']}\n"
    elif lan and advertise and method == "redistribute_connected":
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


# --- Assemblage d'un routeur core (P ou PE) : tout le underlay + optionnellement VPN ---

def gen_core_router(
    node: str,
    role: str,
    as_data: Dict[str, Any],
    asn: int,
    loopbacks: Dict[str, str],
    core_alloc: List[Dict[str, Any]],
    customers: List[Dict[str, Any]],
    vpn_services: Dict[str, Any],
    cust_alloc: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> str:
    """Assemble la config IOS d'un routeur P ou PE (IGP, MPLS, iBGP/VRF si PE et vpnv4)."""
    igp = as_data.get("underlay", {}).get("igp", {})
    all_pe = get_nodes_by_role(as_data, "PE")
    area_mode = _deep_get(igp, ["area", "mode"], "single_area")

    config = gen_header(node)
    config += gen_loopback(node, loopbacks)
    config += gen_core_interfaces(node, core_alloc)

    if igp.get("protocol") == "ospf":
        config += gen_ospf(node, core_alloc, loopbacks, area_mode=area_mode)
    elif igp.get("protocol") == "isis":
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


def generate_configs(
    intent_path: Path,
    *,
    only_nodes: Optional[Set[str]] = None,
    fill_from_run_dir: Optional[Path] = None,
) -> Tuple[int, Optional[Path]]:
    """
    Génère les .cfg dans Configs/Configs-YYYYMMDD-HHMMSS/ (racine projet)
    et copie l'intent dans ce répertoire.

    Si ``only_nodes`` et ``fill_from_run_dir`` sont fournis (cas ``diff --only``) :
    seuls les nœuds listés sont régénérés ; pour les autres, on copie
    ``<nom>.cfg`` depuis ``fill_from_run_dir`` lorsqu'il existe (sinon génération
    comme d'habitude). Ainsi un run NEW reste aligné sur OLD pour les équipements
    non concernés par la modif à chaud.

    Retourne (code_de_sortie, run_dir) : run_dir est le dossier Configs-* créé si succès, sinon None.
    """
    intent_path = intent_path.resolve()
    try:
        intent = normalize_intent(load_intent(intent_path))
        validate_intent(intent)

        if only_nodes is not None:
            if fill_from_run_dir is None:
                raise ValueError("fill_from_run_dir est requis lorsque only_nodes est défini")
            fill_from_run_dir = fill_from_run_dir.resolve()
            if not fill_from_run_dir.is_dir():
                raise ValueError(f"Dossier source des configs introuvable: {fill_from_run_dir}")

        addr = intent["addressing"]
        lan_cfg = intent["lan"]
        customers = intent.get("customers", [])
        vpn = intent.get("vpn_services", {})

        run_dir = make_configs_run_dir()
        shutil.copy2(intent_path, run_dir / intent_path.name)

        def try_copy_unchanged(name: str) -> bool:
            """Copie depuis le run précédent si ce nœud n'est pas dans ``only_nodes``."""
            if only_nodes is None or name in only_nodes:
                return False
            assert fill_from_run_dir is not None
            src = fill_from_run_dir / f"{name}.cfg"
            if not src.is_file():
                return False
            dst = run_dir / f"{name}.cfg"
            shutil.copy2(src, dst)
            print(f"[COPY] {dst}")
            return True

        # Un tour par AS du fichier intent (souvent un seul AS dans les labs)
        for _, as_data in intent["autonomous_systems"].items():
            asn = as_data["asn"]
            all_nodes = get_all_nodes(as_data)
            core_links = normalize_core_links(as_data.get("links", []))

            loopbacks = alloc_loopbacks(all_nodes, addr["loopback_pool"])
            core_alloc_raw = alloc_core_links(core_links, addr["p2p_pool"], addr["p2p_prefix"])
            core_alloc = _enrich_core_alloc_with_underlay(core_alloc_raw, core_links, as_data)

            ce_pe_prefix = int(addr["ce_pe_prefix"])
            cust_alloc = alloc_customer_access_links(customers, addr["customer_pool"], ce_pe_prefix)
            cust_lans = alloc_customer_lans(customers, lan_cfg)

            for node, meta in as_data.get("nodes", {}).items():
                if try_copy_unchanged(node):
                    continue
                role = meta["role"]
                config = gen_core_router(
                    node, role, as_data, asn, loopbacks, core_alloc, customers, vpn, cust_alloc
                )
                path = run_dir / f"{node}.cfg"
                path.write_text(config, encoding="utf-8")
                print(f"[OK] {path}")

            for cust in customers:
                for site in cust.get("sites", []):
                    ce_name = site["ce"]
                    if try_copy_unchanged(ce_name):
                        continue
                    config = gen_ce(site, cust, cust_alloc, cust_lans, asn, lan_cfg)
                    path = run_dir / f"{ce_name}.cfg"
                    path.write_text(config, encoding="utf-8")
                    print(f"[OK] {path}")

        print(f"\nTerminé. Configs et backup intent dans {run_dir}")
        return 0, run_dir
    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1, None
