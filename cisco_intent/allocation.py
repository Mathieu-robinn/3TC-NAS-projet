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
    # Exemple concret:
    #   nodes = ["PE1", "PE2", "P1"]
    #   pool  = "1.0.0.0/8"
    # Résultat:
    #   {"PE1": "1.0.0.1", "PE2": "1.0.0.2", "P1": "1.0.0.3"}
    #
    # On ne choisit pas des adresses au hasard: l'ordre du JSON intent donne un
    # résultat stable, donc deux générations avec le même intent produisent les
    # mêmes loopbacks.
    network = ipaddress.ip_network(pool)
    hosts = network.hosts()
    return {node: str(next(hosts)) for node in nodes}


def first_hosts(subnet: ipaddress.IPv4Network, count: int) -> List[ipaddress.IPv4Address]:
    """
    Retourne seulement les ``count`` premiers hôtes utilisables d'un subnet.

    Important : on évite ``list(subnet.hosts())`` qui peut exploser en mémoire si
    un préfixe large est utilisé par erreur.
    """
    # ipaddress.IPv4Network.hosts() renvoie un itérateur: il calcule les IP au fur
    # et à mesure. C'est important parce qu'un gros réseau comme 10.0.0.0/8 contient
    # des millions d'hôtes. Construire la liste complète serait inutile et lent.
    hosts = subnet.hosts()
    result: List[ipaddress.IPv4Address] = []
    for _ in range(count):
        try:
            # next(hosts) prend simplement l'adresse suivante disponible.
            result.append(next(hosts))
        except StopIteration as exc:
            # StopIteration veut dire qu'on a demandé plus d'hôtes qu'il n'en existe.
            # Exemple: un /32 n'a pas deux hôtes utilisables pour un lien P2P.
            raise ValueError(f"Sous-réseau trop petit pour {count} hôte(s) utilisable(s): {subnet}") from exc
    return result


def alloc_core_links(links: List[Dict[str, Any]], pool: str, prefix_len: int) -> List[Dict[str, Any]]:
    """Découpe ``pool`` en sous-réseaux / préfixe ; premier hôte = extrémité A, second = B."""
    # Un "lien core" est un lien entre routeurs opérateur: PE-P ou P-P.
    # Pour chaque lien, on découpe le pool en petits sous-réseaux de taille prefix_len.
    #
    # Exemple:
    #   pool       = 10.0.0.0/16
    #   prefix_len = 30
    #   1er lien   = 10.0.0.0/30  -> hôtes 10.0.0.1 et 10.0.0.2
    #   2e lien    = 10.0.0.4/30  -> hôtes 10.0.0.5 et 10.0.0.6
    network = ipaddress.ip_network(pool)
    subnets = network.subnets(new_prefix=prefix_len)
    result: List[Dict[str, Any]] = []
    for link in links:
        subnet = next(subnets)
        a_ip, b_ip = first_hosts(subnet, 2)
        # La sortie est une liste de dictionnaires "plats". On garde a_node/b_node
        # et a_ip/b_ip pour savoir plus tard quelle IP mettre sur quelle interface.
        result.append(
            {
                "a_node": link["a"]["node"],
                "a_if": link["a"].get("interface"),
                "a_ip": str(a_ip),
                "b_node": link["b"]["node"],
                "b_if": link["b"].get("interface"),
                "b_ip": str(b_ip),
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
    # Ces liens sont séparés du core: ce sont les liens entre un routeur client CE
    # et un routeur opérateur PE. On utilise donc customer_pool et pas p2p_pool.
    #
    # La clé du dictionnaire résultat est volontairement très précise:
    #   (nom_client, nom_CE, nom_PE)
    # Cela permet de retrouver le lien sans ambiguïté même si deux clients ont des
    # noms de CE proches ou si un PE raccorde plusieurs clients.
    network = ipaddress.ip_network(pool)
    subnets = network.subnets(new_prefix=prefix_len)
    result: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for cust in customers:
        for site in cust.get("sites", []):
            subnet = next(subnets)
            pe_ip, ce_ip = first_hosts(subnet, 2)
            endpoints = site.get("link", {}).get("endpoints", [])
            # Dans l'intent, les endpoints décrivent explicitement les interfaces.
            # On cherche l'endpoint qui correspond au CE puis celui qui correspond au PE.
            ce_ep = next(
                (e for e in endpoints if e.get("node") == site["ce"]),
                {"node": site["ce"], "interface": "GigabitEthernet0/0"},
            )
            pe_ep = next(
                (e for e in endpoints if e.get("node") == site["pe"]),
                {"node": site["pe"], "interface": None},
            )
            key = (cust["name"], site["ce"], site["pe"])
            # Convention d'adressage:
            #   PE = premier hôte du subnet
            #   CE = second hôte du subnet
            # C'est arbitraire mais stable et facile à lire dans les configs.
            result[key] = {
                "customer": cust["name"],
                "ce": site["ce"],
                "pe": site["pe"],
                "ce_if": ce_ep.get("interface"),
                "pe_if": pe_ep.get("interface"),
                "ce_ip": str(ce_ip),
                "pe_ip": str(pe_ip),
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
    # Le LAN de test sert à avoir un préfixe client à annoncer dans BGP.
    # Selon lan.type, il peut être représenté par:
    #   - une Loopback0
    #   - une interface L3 classique
    #   - une subinterface VLAN dot1Q
    if not lan_cfg.get("enabled", True):
        # Si l'intent désactive le LAN, le générateur CE ne créera ni interface LAN
        # ni "network ..." / "redistribute connected" pour ce LAN.
        return {}

    lan_addr = lan_cfg.get("addressing", {})
    pool = ipaddress.ip_network(lan_addr.get("base_pool", "10.0.0.0/8"))
    prefix_len = int(lan_addr.get("prefix", 32))
    if prefix_len < pool.prefixlen:
        # Un nouveau préfixe doit être "plus spécifique" ou égal au pool.
        # Exemple valide: pool /8 découpé en /32.
        # Exemple invalide: pool /24 découpé en /16.
        raise ValueError(f"lan.addressing.prefix={prefix_len} incompatible avec le pool {pool}")

    subnets = pool.subnets(new_prefix=prefix_len)
    result: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for cust in customers:
        for site_index, site in enumerate(cust.get("sites", []), start=1):
            subnet = next(subnets)
            if prefix_len == 32:
                # Cas fréquent: une loopback est une seule adresse /32.
                # Pour un /32, network_address est directement l'adresse à poser.
                lan_ip = str(subnet.network_address)
                mask = "255.255.255.255"
            else:
                # Pour un LAN plus large, on pose la première adresse utilisable
                # sur l'interface du CE.
                lan_ip = str(first_hosts(subnet, 1)[0])
                mask = str(subnet.netmask)

            key = (cust["name"], site["ce"], site["pe"])
            lan_type = lan_cfg.get("type", "loopback")
            interface: Optional[str] = None
            encapsulation: Optional[int] = None

            if lan_type == "loopback":
                # Même nom de loopback sur tous les CE par défaut. C'est possible
                # parce que chaque routeur a son propre espace d'interfaces.
                interface = lan_cfg.get("naming", {}).get("pattern", "Loopback0")
            elif lan_type == "interface":
                # Un site peut surcharger l'interface LAN globale. Ça permet par
                # exemple d'utiliser Gi0/1 sur un CE et Gi0/2 sur un autre.
                interface = (
                    _deep_get(site, ["lan", "interface"])
                    or _deep_get(lan_cfg, ["interface", "name"])
                    or "GigabitEthernet0/1"
                )
            elif lan_type == "subinterface_vlan":
                # Pour les VLANs, on génère une sous-interface du type Gi0/1.201.
                # site_index commence à 1, donc avec vlan_base=200 le premier site
                # obtient VLAN 201, le second VLAN 202, etc.
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
    # OSPF IOS n'écrit pas les réseaux avec un prefix length (/30), mais avec un
    # masque inversé appelé wildcard. Cette fonction fait l'inversion bit à bit.
    netmask = ipaddress.ip_address(mask)
    wildcard = ipaddress.ip_address(int(ipaddress.ip_address("255.255.255.255")) - int(netmask))
    return str(wildcard)


def build_core_adjacency(core_alloc: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Indexe les liens core par routeur pour éviter de rescanner toute la liste à chaque bloc généré."""
    # Au lieu de refaire "parcours tous les liens et garde ceux de PE1" pour chaque
    # bloc de config, on construit un dictionnaire:
    #   {
    #     "PE1": [liens vus depuis PE1],
    #     "P1":  [liens vus depuis P1],
    #   }
    #
    # Chaque lien est ajouté deux fois: une vue depuis l'extrémité A, une vue depuis
    # l'extrémité B. Le champ "neighbor" change donc selon le côté.
    adjacency: Dict[str, List[Dict[str, Any]]] = {}
    for link in core_alloc:
        # Vue locale depuis le routeur côté A.
        adjacency.setdefault(link["a_node"], []).append(
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
        # Vue locale depuis le routeur côté B.
        adjacency.setdefault(link["b_node"], []).append(
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
    return adjacency


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
