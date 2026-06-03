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
  ``generate_configs`` renvoie ``(code_de_sortie, out_dir)`` : dossier d'écriture
  (``configs/<name>/live/`` par défaut selon l'intent, ou ``output_dir`` pour ``update``), sinon ``None``.
  La CLI ``generate --push`` passe ce dossier à ``gns3_push.run_push``.

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

import hashlib
import json
import os
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
    build_core_adjacency,
    wildcard_from_mask,
)
from cisco_intent.intent import (
    _deep_get,
    get_all_nodes,
    get_nodes_by_role,
    load_intent,
    normalize_core_links,
    normalize_intent,
    topology_name_from_intent,
    validate_intent,
)
from cisco_intent.backup_zip import zip_run_dir
from cisco_intent.paths import (
    backup_full_configs_dir,
    configs_backup_stamp,
    live_dir,
    prepare_dir_for_generation,
)
from cisco_intent.te import (
    get_explicit_paths_for_node,
    get_te_tunnels_for_node,
    mpls_te_enabled,
    resolve_explicit_path_hops,
    tunnel_autoroute_announce,
)


# --- Blocs de texte IOS de base (hostname, loopback, interfaces core) ---

def gen_header(node: str) -> str:
    """Bloc d'en-tête IOS commun : version, hostname, services de base."""
    # Cette fonction renvoie du texte IOS brut. Toutes les fonctions gen_* suivent
    # le même principe: elles construisent une chaîne qui sera écrite telle quelle
    # dans le fichier final <hostname>.cfg.
    #
    # Le header est commun aux P, PE et CE:
    #   - hostname: nom du routeur
    #   - no ip domain-lookup: évite les délais quand on tape une mauvaise commande
    #   - ip cef: nécessaire/utile pour MPLS et forwarding moderne
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
    # Loopback0 est une interface logique toujours up tant que le routeur tourne.
    # Dans ce lab, elle sert notamment de router-id IGP/BGP/LDP et de source pour
    # les sessions iBGP entre PE.
    ip = loopbacks[node]
    return (
        f"interface Loopback0\n"
        f" ip address {ip} 255.255.255.255\n"
        f" no shutdown\n"
        f"!\n"
    )


def gen_core_interfaces(node: str, node_core_links: List[Dict[str, Any]]) -> str:
    """Interfaces physiques du core pour ``node`` (IP, description, ``mpls ip`` si activé)."""
    # node_core_links contient seulement les liens du routeur en cours.
    # Exemple pour P1: liens vers PE1, P2, P3, etc.
    #
    # Chaque entrée contient déjà l'interface locale, l'IP locale, le voisin, le
    # masque et le flag MPLS éventuel.
    config = ""
    for link in node_core_links:
        if not link["interface"]:
            # Sans nom d'interface, on ne peut pas savoir où poser l'adresse IP.
            raise ValueError(f"Interface manquante sur un lien core pour {node} -> {link['neighbor']}")
        config += (
            f"interface {link['interface']}\n"
            f" description to_{link['neighbor']}\n"
            f" ip address {link['ip']} {link['mask']}\n"
            f" no shutdown\n"
        )
        if link.get("mpls"):
            # Cette commande active MPLS sur l'interface. Elle n'est présente que si
            # la politique underlay le demande.
            config += " mpls ip\n"
        if link.get("te"):
            # TE/RSVP sur les mêmes interfaces que ``mpls ip`` (voir allocation._enrich_core_alloc).
            config += " mpls traffic-eng tunnels\n"
            config += f" ip rsvp bandwidth {link['rsvp_bandwidth']}\n"
        config += "!\n"
    return config


def gen_ospf(
    node: str,
    node_core_links: List[Dict[str, Any]],
    loopbacks: Dict[str, str],
    area_mode: str = "single_area",
    te_enabled: bool = False,
) -> str:
    """Process OSPF 1 : router-id loopback, networks core et loopback selon ``area_mode``."""
    # On génère OSPF avec des "network statements". C'est simple et lisible pour un
    # lab IOS: on déclare la loopback et chaque réseau point-à-point du core.
    router_id = loopbacks[node]
    config = f"router ospf 1\n router-id {router_id}\n"
    # La loopback reste toujours en area 0, même si les liens core utilisent des
    # areas explicites.
    config += f" network {router_id} 0.0.0.0 area 0\n"
    for link in node_core_links:
        wildcard = wildcard_from_mask(link["mask"])
        # area_mode:
        #   - single_area: tout en area 0
        #   - explicit: l'area vient de chaque lien dans l'intent
        area = int(link.get("igp_area", 0)) if area_mode != "single_area" else 0
        config += f" network {link['network']} {wildcard} area {area}\n"
    if te_enabled:
        # Extensions OSPF TE : TEDB + annonce des liens RSVP (requis pour tunnels TE).
        config += " mpls traffic-eng router-id Loopback0\n"
        if area_mode == "single_area":
            config += " mpls traffic-eng area 0\n"
        else:
            te_areas = {0}
            for link in node_core_links:
                te_areas.add(int(link.get("igp_area", 0)))
            for area in sorted(te_areas):
                config += f" mpls traffic-eng area {area}\n"
    config += "!\n"
    return config


# --- IGP / MPLS (underlay) ---

def gen_mpls(te_enabled: bool = False) -> str:
    """Commandes globales MPLS LDP (router-id forcé sur Loopback0) ; TE optionnel."""
    # Attention: ce bloc active MPLS/LDP globalement. Le fait qu'un lien transporte
    # vraiment MPLS dépend aussi du "mpls ip" posé interface par interface.
    config = (
        "mpls ip\n"
        "mpls label protocol ldp\n"
        "mpls ldp router-id Loopback0 force\n"
    )
    if te_enabled:
        config += "mpls traffic-eng tunnels\n"
    config += "!\n"
    return config


def gen_explicit_paths(
    paths: List[Dict[str, Any]],
    loopbacks: Dict[str, str],
    source_node: str,
) -> str:
    """Chemins explicites TE (routeur tête uniquement) ; résout les hops nœud → loopback."""
    config = ""
    for path in paths:
        name = path["name"]
        hops = path.get("hops", [])
        resolved = resolve_explicit_path_hops(hops, loopbacks, exclude_node=source_node)
        config += f"ip explicit-path name {name} enable\n"
        for ip in resolved:
            config += f" next-address {ip}\n"
        config += "!\n"
    return config


def gen_te_tunnels(
    tunnels: List[Dict[str, Any]],
    loopbacks: Dict[str, str],
    te_cfg: Dict[str, Any],
) -> str:
    """Interfaces Tunnel MPLS-TE (routeur tête uniquement)."""
    config = ""
    for tun in tunnels:
        tid = tun["id"]
        dest_ip = loopbacks[tun["destination_node"]]
        path_name = tun["path_option_name"]
        config += (
            f"interface Tunnel{tid}\n"
            f" ip unnumbered Loopback0\n"
            f" tunnel destination {dest_ip}\n"
            f" tunnel mode mpls traffic-eng\n"
            f" tunnel mpls traffic-eng path-option 1 explicit name {path_name}\n"
        )
        if tunnel_autoroute_announce(tun, te_cfg):
            # Optionnel : injecte le tunnel dans OSPF (tracé IP = tracé MPLS si activé).
            config += " tunnel mpls traffic-eng autoroute announce\n"
        config += "!\n"
    return config


def isis_net_from_loopback(loopback_ip: str, area: str = "49.0001") -> str:
    """Construit une chaîne NET IS-IS à partir de l'IPv4 du loopback (system ID dérivé des octets)."""
    # IS-IS n'utilise pas une adresse IP comme router-id; il utilise un NET.
    # Pour rester déterministe, on fabrique un system-id depuis les octets de la
    # loopback. Exemple très simplifié: 1.0.0.1 -> 0100.0001.0001.
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
    # Ce bloc concerne uniquement les PE. Les routeurs P n'ont pas de BGP VPNv4:
    # ils transportent seulement le trafic MPLS dans le core.
    #
    # Deux grandes stratégies:
    #   - full_mesh: chaque PE parle à tous les autres PE
    #   - rr_clients / rr_redundant: les PE clients parlent aux route-reflectors
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
        # remote-as = ASN core, car c'est de l'iBGP.
        # update-source Loopback0 garantit que la session reste stable tant que
        # l'IGP sait joindre la loopback.
        return f" neighbor {ip} remote-as {asn}\n neighbor {ip} update-source Loopback0\n"

    if strategy == "full_mesh":
        # Full-mesh: simple et correct pour quelques PE, moins scalable si on en a beaucoup.
        for pe in all_pe:
            if pe == node:
                continue
            config += add_neighbor(loopbacks[pe])
    elif strategy in {"rr_clients", "rr_redundant"}:
        if not rr_nodes:
            raise ValueError("route_reflectors.nodes vide alors que strategy RR est demandée")
        if node in rr_nodes:
            # Le route-reflector configure des voisins vers tous les autres PE.
            for pe in all_pe:
                if pe == node:
                    continue
                config += add_neighbor(loopbacks[pe])
        else:
            # Un PE client ne configure que ses voisins vers les route-reflectors.
            for rr in rr_nodes:
                config += add_neighbor(loopbacks[rr])
    else:
        raise ValueError(f"Stratégie iBGP inconnue: {strategy}")

    config += " !\n address-family vpnv4\n"
    if strategy == "full_mesh":
        # En vpnv4, il faut activer explicitement chaque voisin et envoyer les
        # communautés étendues, sinon les RT ne circulent pas.
        for pe in all_pe:
            if pe == node:
                continue
            pe_ip = loopbacks[pe]
            config += f"  neighbor {pe_ip} activate\n  neighbor {pe_ip} send-community both\n"
    else:
        if node in rr_nodes:
            # Sur un RR, les non-RR deviennent route-reflector-client. Les autres
            # RRs ne sont pas marqués client pour éviter une réflexion incorrecte.
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
            # Côté client RR, on active simplement les voisins RRs dans vpnv4.
            for rr in rr_nodes:
                rr_ip = loopbacks[rr]
                config += f"  neighbor {rr_ip} activate\n  neighbor {rr_ip} send-community both\n"
    config += " exit-address-family\n!\n"
    return config


# --- VRF, RD/RT, interfaces PE vers CE ---

def _compute_rd_rt(vpn_services: Dict[str, Any], core_asn: int, vrf_name: str, vrf_index: int) -> Tuple[str, str]:
    """Calcule la valeur RD et RT (ou marqueur ``AUTO_PER_CUSTOMER_ASN``) pour une VRF."""
    # RD = Route Distinguisher: rend les routes VPN uniques dans BGP vpnv4.
    # RT = Route Target: communauté qui dit quelles routes exporter/importer.
    #
    # Dans un lab simple, RD et RT peuvent avoir la même valeur. En production,
    # ils ont des rôles différents, mais cette génération reste lisible.
    rd_cfg = vpn_services.get("rd", {}) or {}
    rd_mode = rd_cfg.get("mode", "asn_vrfid")
    rd_base = int(rd_cfg.get("base", 100))
    vrf_id = rd_base + vrf_index
    if rd_mode == "asn_vrfid":
        # Mode le plus lisible: ASN core + numéro de VRF.
        rd = f"{core_asn}:{vrf_id}"
    elif rd_mode == "asn_hash":
        # On utilise hashlib au lieu de hash(), car hash() change entre processus
        # Python. Ici, le même nom de VRF donnera toujours le même nombre.
        digest = hashlib.blake2s(vrf_name.encode("utf-8"), digest_size=2).digest()
        rd = f"{core_asn}:{int.from_bytes(digest, 'big')}"
    else:
        raise ValueError(f"vpn_services.rd.mode invalide: {rd_mode}")

    rt_cfg = vpn_services.get("rt", {}) or {}
    rt_strategy = rt_cfg.get("strategy", "auto_per_vrf")
    if rt_strategy == "auto_per_vrf":
        # RT unique par VRF: simple pour isoler chaque client.
        rt = f"{core_asn}:{vrf_id}"
    elif rt_strategy == "auto_per_customer_asn":
        # On ne connaît pas forcément l'ASN client dans cette fonction. On renvoie
        # donc un marqueur que gen_vrf_and_pe_ce remplacera ensuite.
        rt = "AUTO_PER_CUSTOMER_ASN"
    else:
        raise ValueError(f"vpn_services.rt.strategy invalide: {rt_strategy}")

    return rd, rt


def _ce_needs_allowas_in(cust: Dict[str, Any]) -> bool:
    """True si le client a plusieurs sites (même ASN partagé) : le CE doit accepter l'ASN local dans l'AS_PATH."""
    # Quand un client a plusieurs sites dans le même ASN, une route peut revenir
    # vers un CE avec son propre ASN dans l'AS_PATH. IOS rejette ça par défaut;
    # allowas-in autorise ce cas côté CE.
    sites = cust.get("sites") or []
    return len(sites) > 1


def gen_vrf_and_pe_ce(
    node: str,
    customers_by_name: Dict[str, Dict[str, Any]],
    sites_by_customer_pe: Dict[Tuple[str, str], List[Dict[str, Any]]],
    vpn_services: Dict[str, Any],
    cust_alloc: Dict[Tuple[str, str, str], Dict[str, Any]],
    core_asn: int,
) -> str:
    """Pour le PE ``node`` : vrf definition, interfaces PE-CE en VRF, voisins eBGP par VRF (sans as-override)."""
    # Cette fonction est appelée PE par PE.
    # Elle parcourt toutes les VRF déclarées dans vpn_services, mais ne configure
    # sur ce PE que les VRF qui ont au moins un site client raccordé à ce PE.
    #
    # Les index customers_by_name et sites_by_customer_pe évitent de reparcourir
    # toute la liste des clients et sites à chaque VRF.
    config = ""
    vrfs = vpn_services.get("vrfs", [])

    for vrf_index, vrf in enumerate(vrfs, start=1):
        cust_name = vrf["customer"]
        vrf_name = vrf.get("name", cust_name)
        cust = customers_by_name.get(cust_name)
        if not cust:
            # Si la validation laisse passer une VRF qui référence un client absent,
            # on ignore ici pour ne pas casser toute la génération. Idéalement, ce
            # cas devrait être signalé plus haut par validate_intent.
            continue

        sites_on_this_pe = sites_by_customer_pe.get((cust_name, node), [])
        if not sites_on_this_pe:
            # La VRF existe peut-être ailleurs, mais ce PE n'a aucun CE de ce client.
            continue

        rd, rt = _compute_rd_rt(vpn_services, core_asn, vrf_name, vrf_index)
        if rt == "AUTO_PER_CUSTOMER_ASN":
            # Remplacement du marqueur par une valeur basée sur l'ASN client.
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
                # Une interface PE manquante est bloquante: impossible de savoir où
                # appliquer "vrf forwarding" et l'adresse PE-CE.
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
    # Un CE est plus simple qu'un PE:
    #   - une interface vers le PE
    #   - éventuellement un LAN de test
    #   - une session eBGP vers le PE
    ce_name = site["ce"]
    pe_name = site["pe"]
    alloc = cust_alloc[(cust["name"], ce_name, pe_name)]
    lan = cust_lans.get((cust["name"], ce_name, pe_name))
    ce_if = alloc["ce_if"] or "GigabitEthernet0/0"

    config = gen_header(ce_name)
    # Interface CE -> PE.
    config += (
        f"interface {ce_if}\n"
        f" description to_{pe_name}\n"
        f" ip address {alloc['ce_ip']} {alloc['mask']}\n"
        f" no shutdown\n"
        f"!\n"
    )

    if lan:
        # Interface LAN optionnelle. Elle peut être Loopback0, interface physique,
        # ou subinterface VLAN selon le champ lan.type.
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
        # Nécessaire pour les clients multi-sites qui partagent le même ASN.
        config += f"  neighbor {alloc['pe_ip']} allowas-in\n"

    advertise = lan_cfg.get("bgp", {}).get("advertise", True)
    method = lan_cfg.get("bgp", {}).get("method", "network_statement")
    if lan and advertise and method == "network_statement":
        # Méthode précise: on annonce uniquement le LAN calculé.
        config += f"  network {lan['ip']} mask {lan['mask']}\n"
    elif lan and advertise and method == "redistribute_connected":
        # Méthode plus générique mais filtrée: la route-map limite la redistribution
        # à l'interface LAN, pour ne pas annoncer le lien CE-PE.
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
    node_core_links: List[Dict[str, Any]],
    customers_by_name: Dict[str, Dict[str, Any]],
    sites_by_customer_pe: Dict[Tuple[str, str], List[Dict[str, Any]]],
    vpn_services: Dict[str, Any],
    cust_alloc: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> str:
    """Assemble la config IOS d'un routeur P ou PE (IGP, MPLS, iBGP/VRF si PE et vpnv4)."""
    # Fonction d'assemblage principale pour le core.
    # "role" décide de la partie VPN:
    #   - P  : underlay seulement
    #   - PE : underlay + BGP vpnv4 + VRF client
    igp = as_data.get("underlay", {}).get("igp", {})
    all_pe = get_nodes_by_role(as_data, "PE")
    area_mode = _deep_get(igp, ["area", "mode"], "single_area")
    mpls_enabled = bool(as_data.get("underlay", {}).get("mpls", {}).get("enabled", False))
    te_enabled = mpls_te_enabled(as_data) if mpls_enabled else False

    config = gen_header(node)
    config += gen_loopback(node, loopbacks)
    config += gen_core_interfaces(node, node_core_links)

    if igp.get("protocol") == "ospf":
        # OSPF: génération complète avec network statements.
        config += gen_ospf(
            node, node_core_links, loopbacks, area_mode=area_mode, te_enabled=te_enabled
        )
    elif igp.get("protocol") == "isis":
        # IS-IS: support minimal mais fonctionnel pour le lab.
        isis_net = isis_net_from_loopback(loopbacks[node])
        config += (
            "router isis 1\n"
            f" net {isis_net}\n"
            " is-type level-2-only\n"
            "!\n"
        )
        config += "interface Loopback0\n ip router isis 1\n!\n"
        for link in node_core_links:
            if link["interface"]:
                config += f"interface {link['interface']}\n ip router isis 1\n!\n"

    if mpls_enabled:
        # Bloc MPLS/LDP global. Les interfaces MPLS ont déjà été marquées dans
        # gen_core_interfaces via link["mpls"].
        config += gen_mpls(te_enabled=te_enabled)

        if te_enabled:
            # Chemins/tunnels uniquement sur le PE ``source_node`` (pas sur P ni PE transit).
            te_cfg = as_data.get("traffic_engineering", {}) or {}
            node_paths = get_explicit_paths_for_node(node, te_cfg)
            node_tunnels = get_te_tunnels_for_node(node, te_cfg)
            if node_paths:
                config += gen_explicit_paths(node_paths, loopbacks, source_node=node)
            if node_tunnels:
                config += gen_te_tunnels(node_tunnels, loopbacks, te_cfg)

    if role == "PE" and as_data.get("bgp", {}).get("vpnv4"):
        # Seuls les PE participent à l'overlay VPN.
        bgp = as_data.get("bgp", {}) or {}
        peering_cfg = bgp.get("peering", {}) or {}
        rr_nodes = _deep_get(bgp, ["route_reflectors", "nodes"], []) or []
        config += gen_ibgp(node, asn, loopbacks, all_pe, peering_cfg, rr_nodes)
        config += gen_vrf_and_pe_ce(
            node,
            customers_by_name,
            sites_by_customer_pe,
            vpn_services,
            cust_alloc,
            asn,
        )

    config += "end\n"
    return config


def generate_configs(
    intent_path: Path,
    *,
    intent: Optional[Dict[str, Any]] = None,
    only_nodes: Optional[Set[str]] = None,
    fill_from_run_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Tuple[int, Optional[Path]]:
    """
    Écrit les .cfg dans ``output_dir`` si fourni, sinon dans ``configs/<name>/live/`` (``name`` = intent).

    Si ``intent`` est fourni (dict déjà validé, ex. par ``load_validate_intent``), évite un second chargement.

    ``update`` passe ``staging_dir(topology)`` (ou ``scratch_old``) explicitement.

    Si ``only_nodes`` et ``fill_from_run_dir`` sont fournis (cas ``update --only``) :
    seuls les nœuds listés sont régénérés ; pour les autres, on copie
    ``<nom>.cfg`` depuis ``fill_from_run_dir`` lorsqu'il existe.

    Un zip d'archive est ajouté sous ``configs/<topology>/backup/full_configs/Configs-<timestamp>.zip``.

    Retourne (code_de_sortie, out_dir) : out_dir est le dossier cible si succès, sinon None.

    En cas d'erreur, la stack trace complète est affichée seulement si
    ``CISCO_INTENT_DEBUG`` est défini dans l'environnement.
    """
    # Cette fonction est appelée par:
    #   - la commande generate
    #   - la commande update pour générer OLD/NEW
    #   - les tests éventuels
    #
    # Elle retourne un code de sortie plutôt que de lever l'exception au niveau CLI,
    # pour garder une interface simple avec cli.py.
    intent_path = intent_path.resolve()
    try:
        if intent is None:
            # Cas normal CLI generate: on lit le fichier ici.
            intent = normalize_intent(load_intent(intent_path))
            validate_intent(intent)
        # Si intent est fourni, il est supposé déjà validé par l'appelant.
        topology = topology_name_from_intent(intent)

        if only_nodes is not None:
            # Cas update --only: seuls certains routeurs sont recalculés. Les autres
            # sont copiés depuis fill_from_run_dir pour garder un jeu complet.
            if fill_from_run_dir is None:
                raise ValueError("fill_from_run_dir est requis lorsque only_nodes est défini")
            fill_from_run_dir = fill_from_run_dir.resolve()
            if not fill_from_run_dir.is_dir():
                raise ValueError(f"Dossier source des configs introuvable: {fill_from_run_dir}")

        addr = intent["addressing"]
        lan_cfg = intent["lan"]
        customers = intent.get("customers", [])
        vpn = intent.get("vpn_services", {})
        # Index de lecture rapide:
        #   customers_by_name["CUST1"] -> objet client
        #   sites_by_customer_pe[("CUST1", "PE1")] -> sites CUST1 attachés à PE1
        customers_by_name = {cust["name"]: cust for cust in customers}
        sites_by_customer_pe: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for cust in customers:
            cust_name = cust["name"]
            for site in cust.get("sites", []):
                sites_by_customer_pe.setdefault((cust_name, site["pe"]), []).append(site)

        out_base = output_dir if output_dir is not None else live_dir(topology)
        # prepare_dir_for_generation nettoie les fichiers directs du dossier cible
        # avant d'écrire les nouvelles configs.
        run_dir = prepare_dir_for_generation(out_base)
        # On copie l'intent utilisé dans le dossier de sortie pour pouvoir retrouver
        # exactement ce qui a généré les .cfg.
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

            # Calculs réseau pour cet AS.
            loopbacks = alloc_loopbacks(all_nodes, addr["loopback_pool"])
            core_alloc_raw = alloc_core_links(core_links, addr["p2p_pool"], addr["p2p_prefix"])
            core_alloc = _enrich_core_alloc_with_underlay(core_alloc_raw, core_links, as_data)
            core_adjacency = build_core_adjacency(core_alloc)

            # Liens client et LANs de test.
            ce_pe_prefix = int(addr["ce_pe_prefix"])
            cust_alloc = alloc_customer_access_links(customers, addr["customer_pool"], ce_pe_prefix)
            cust_lans = alloc_customer_lans(customers, lan_cfg)

            for node, meta in as_data.get("nodes", {}).items():
                if try_copy_unchanged(node):
                    continue
                role = meta["role"]
                # On donne à gen_core_router uniquement les liens locaux du node,
                # pas toute la topologie.
                config = gen_core_router(
                    node,
                    role,
                    as_data,
                    asn,
                    loopbacks,
                    core_adjacency.get(node, []),
                    customers_by_name,
                    sites_by_customer_pe,
                    vpn,
                    cust_alloc,
                )
                path = run_dir / f"{node}.cfg"
                path.write_text(config, encoding="utf-8")
                print(f"[OK] {path}")

            for cust in customers:
                for site in cust.get("sites", []):
                    ce_name = site["ce"]
                    if try_copy_unchanged(ce_name):
                        continue
                    # Les CE sont générés après le core car ils utilisent les mêmes
                    # allocations CE-PE et LAN calculées plus haut.
                    config = gen_ce(site, cust, cust_alloc, cust_lans, asn, lan_cfg)
                    path = run_dir / f"{ce_name}.cfg"
                    path.write_text(config, encoding="utf-8")
                    print(f"[OK] {path}")

        print(f"\nTerminé. Configs et backup intent dans {run_dir}")
        try:
            # Archive automatique du run complet. Même si l'archive échoue, les .cfg
            # générés restent valides; on affiche donc seulement un warning.
            zip_dest = backup_full_configs_dir(topology) / f"Configs-{configs_backup_stamp()}.zip"
            zip_run_dir(run_dir, zip_dest)
            print(f"[ZIP] {zip_dest}")
        except OSError as e:
            print(f"[WARN] Archive backup full_configs: {e}", file=sys.stderr)
        return 0, run_dir
    except Exception as e:
        print(f"Erreur: {e}", file=sys.stderr)
        if os.environ.get("CISCO_INTENT_DEBUG"):
            traceback.print_exc()
        return 1, None
