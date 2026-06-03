# -*- coding: utf-8 -*-
"""
================================================================================
intent.py — Fichier intent JSON : chargement, normalisation, validation
================================================================================

Rôle :
  L'intent décrit la topologie et les services (AS, liens, clients, VPN…) au format v4.
  Ce module charge le JSON, effectue une copie profonde (``normalize_intent``), puis
  ``validate_intent`` vérifie la cohérence avant génération.

Données :
  - Entrée : chemin vers un ``.json``.
  - Sortie : dictionnaires Python (types ``Any`` / ``Dict``).

Liens : ``generator`` appelle ``load_intent``, ``normalize_intent``, ``validate_intent`` ;
        ``allocation`` utilise ``_deep_get`` pour lire des clés imbriquées.

Pour étendre :
  - Nouveau champ obligatoire : ajoute une vérification dans ``validate_intent``.
================================================================================
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

# Identifiant de topologie : dossier sous configs/<name>/ (live, staging, backup, …)
_TOPOLOGY_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_TOPOLOGY_NAME_MAX_LEN = 64


def load_intent(path: Path) -> Any:
    """Lit un fichier intent JSON (UTF-8) et retourne l'objet Python (souvent un dict)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize_core_links(raw_links: Any) -> List[Dict[str, Any]]:
    """
    Normalise les liens core : chaque élément doit être un objet avec ``endpoints``
    (exactement deux entrées ``node`` / ``interface`` optionnelle).
    """
    normalized: List[Dict[str, Any]] = []
    for link in raw_links:
        if not isinstance(link, dict) or "endpoints" not in link:
            raise ValueError(f"Lien core invalide (objet avec « endpoints » attendu) : {link!r}")
        eps = link["endpoints"]
        if not isinstance(eps, list) or len(eps) != 2:
            raise ValueError(f"Lien core : « endpoints » doit être une liste de 2 éléments : {link!r}")
        ep1, ep2 = eps
        item: Dict[str, Any] = {
            "a": {"node": ep1["node"], "interface": ep1.get("interface")},
            "b": {"node": ep2["node"], "interface": ep2.get("interface")},
        }
        if "igp_area" in link:
            item["igp_area"] = link["igp_area"]
        if "mpls" in link:
            item["mpls"] = link["mpls"]
        normalized.append(item)
    return normalized


def _deep_get(dct: Dict[str, Any], path: List[str], default: Any = None) -> Any:
    """Parcourt une liste de clés dans un dict imbriqué ; retourne ``default`` si une étape manque."""
    cur: Any = dct
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def normalize_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Retourne une copie profonde de l'intent sans modifier l'objet d'origine.
    Toute la cohérence du schéma est imposée par ``validate_intent``.
    """
    return json.loads(json.dumps(intent))


def load_validate_intent(path: Path) -> Dict[str, Any]:
    """Charge un fichier JSON, normalise et valide ; retourne le dict intent."""
    intent = normalize_intent(load_intent(path))
    validate_intent(intent)
    return intent


def topology_name_from_intent(intent: Dict[str, Any]) -> str:
    """
    Nom de topologie (champ racine ``name``). À utiliser après ``validate_intent``
    ou ``load_validate_intent``.
    """
    return str(intent["name"])


def get_all_nodes(as_data: Dict[str, Any]) -> List[str]:
    """Liste les noms de tous les nœuds de l'AS (clé ``nodes``)."""
    nodes = as_data.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError("autonomous_systems.*.nodes doit être un objet non vide")
    return list(nodes.keys())


def get_nodes_by_role(as_data: Dict[str, Any], role: str) -> List[str]:
    """Filtre les nœuds par rôle (ex. ``PE``, ``P``)."""
    nodes = as_data.get("nodes")
    if not isinstance(nodes, dict):
        raise ValueError("autonomous_systems.*.nodes doit être un objet")
    return [name for name, data in nodes.items() if data.get("role") == role]


def validate_intent(intent: Dict[str, Any]) -> None:
    """
    Lève ``ValueError`` avec une liste d'erreurs si l'intent est incohérent.
    ``seen_interfaces`` : évite deux fois la même interface sur un même routeur (core + CE-PE).
    """
    # Import différé : te.py importe intent (_deep_get, get_all_nodes) ; un import
    # au niveau module créerait une boucle intent → te → intent.
    from cisco_intent.te import validate_traffic_engineering

    errors: List[str] = []

    name = intent.get("name")
    if name is None:
        errors.append("Champ « name » manquant à la racine (identifiant de topologie, ex. topologie_1)")
    elif not isinstance(name, str):
        errors.append("Champ « name » doit être une chaîne")
    elif not (1 <= len(name) <= _TOPOLOGY_NAME_MAX_LEN):
        errors.append(f"Champ « name » : longueur entre 1 et {_TOPOLOGY_NAME_MAX_LEN} caractères")
    elif not _TOPOLOGY_NAME_RE.match(name):
        errors.append(
            "Champ « name » : uniquement lettres, chiffres, tirets et underscores (ex. topologie_1)"
        )

    if "lan" not in intent:
        errors.append("Champ « lan » manquant à la racine")

    addr = intent.get("addressing", {})
    for required in ["loopback_pool", "p2p_pool", "customer_pool", "p2p_prefix", "ce_pe_prefix"]:
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

        if not isinstance(as_data.get("nodes"), dict) or not as_data["nodes"]:
            errors.append(f"{as_name}: nodes doit être un objet non vide")

        igp = _deep_get(as_data, ["underlay", "igp"], None)
        if not isinstance(igp, dict):
            errors.append(f"{as_name}: underlay.igp manquant ou invalide")
        else:
            protocol = igp.get("protocol")
            if protocol not in {"ospf", "isis"}:
                errors.append(f"{as_name}: underlay.igp.protocol requis (ospf ou isis)")
            area_mode = _deep_get(igp, ["area", "mode"], None)
            if area_mode not in {"single_area", "explicit"}:
                errors.append(f"{as_name}: underlay.igp.area.mode requis (single_area ou explicit)")

        mpls = _deep_get(as_data, ["underlay", "mpls"], {}) or {}
        if mpls.get("enabled"):
            m_mode = _deep_get(mpls, ["interfaces", "mode"], None)
            if m_mode not in {"all_core_links", "explicit"}:
                errors.append(
                    f"{as_name}: underlay.mpls.interfaces.mode requis "
                    f"(all_core_links ou explicit) lorsque mpls.enabled est vrai"
                )

        validate_traffic_engineering(as_name, as_data, errors)

        bgp = as_data.get("bgp", {}) or {}
        if bgp:
            if bgp.get("type", "ibgp") not in {"ibgp"}:
                errors.append(f"{as_name}: bgp.type invalide: {bgp.get('type')}")
            peering = bgp.get("peering") or {}
            peering_strategy = peering.get("strategy")
            peering_transport = peering.get("transport")
            if peering_strategy is None:
                errors.append(f"{as_name}: bgp.peering.strategy manquant")
            elif peering_strategy not in {"rr_clients", "full_mesh", "rr_redundant"}:
                errors.append(f"{as_name}: bgp.peering.strategy invalide: {peering_strategy}")
            if peering_transport is None:
                errors.append(f"{as_name}: bgp.peering.transport manquant")
            if peering_strategy in {"rr_clients", "rr_redundant"}:
                rr_nodes = _deep_get(bgp, ["route_reflectors", "nodes"], [])
                if not rr_nodes:
                    errors.append(f"{as_name}: bgp.route_reflectors.nodes requis pour strategy={peering_strategy}")

        seen_interfaces: Dict[tuple, bool] = {}
        for link in as_data.get("links", []):
            if not isinstance(link, dict) or "endpoints" not in link:
                errors.append(f"{as_name}: chaque lien doit être un objet avec « endpoints »")
                continue
            eps = link["endpoints"]
            if not isinstance(eps, list) or len(eps) != 2:
                errors.append(f"{as_name}: chaque lien doit avoir exactement 2 endpoints")
                continue
            for ep in eps:
                if not isinstance(ep, dict) or "node" not in ep:
                    errors.append(f"{as_name}: chaque endpoint doit avoir un champ « node »")
                    continue
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
                        if not isinstance(ep, dict):
                            continue
                        node_name = ep.get("node")
                        iface = ep.get("interface")
                        if iface and node_name:
                            key = (node_name, iface)
                            if key in seen_interfaces:
                                errors.append(f"Interface dupliquée sur {node_name} : {iface}")
                            seen_interfaces[key] = True

    if errors:
        raise ValueError("Intent invalide :\n- " + "\n- ".join(errors))
