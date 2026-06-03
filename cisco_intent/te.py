# -*- coding: utf-8 -*-
"""
================================================================================
te.py — MPLS Traffic Engineering : validation intent et résolution nœud → loopback
================================================================================

Rôle :
  - Valide les blocs ``underlay.mpls.traffic_engineering`` et ``traffic_engineering``
    (chemins explicites, tunnels) dans l'intent.
  - Résout les noms de nœuds en adresses Loopback0 (allouées par ``allocation.alloc_loopbacks``).
  - Filtre chemins/tunnels à générer sur chaque routeur tête (``source_node``).

Liens : ``generator.gen_core_router`` ; ``config_update`` (blocs ``ip explicit-path``, ``Tunnel*``) ;
        ``intent.validate_intent`` importe ``validate_traffic_engineering`` en différé (évite import circulaire).
================================================================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from cisco_intent.intent import _deep_get, get_all_nodes, get_nodes_by_role


def loopback_for_node(node: str, loopbacks: Dict[str, str]) -> str:
    """Retourne l'IPv4 Loopback0 d'un nœud ; lève ``ValueError`` si le nom est inconnu."""
    if node not in loopbacks:
        raise ValueError(f"Nœud inconnu pour résolution TE: {node!r}")
    return loopbacks[node]


def resolve_explicit_path_hops(
    hops: List[str],
    loopbacks: Dict[str, str],
    *,
    exclude_node: str | None = None,
) -> List[str]:
    """
    Traduit une liste de noms de nœuds en adresses loopback (ordre conservé).

    ``exclude_node`` : le PE tête (``source_node``) est retiré des hops intent avant
    résolution — il ne doit pas apparaître comme ``next-address`` du chemin explicite.
    """
    filtered = [hop for hop in hops if hop != exclude_node] if exclude_node else hops
    return [loopback_for_node(hop, loopbacks) for hop in filtered]


def tunnel_autoroute_announce(tun: Dict[str, Any], te_cfg: Dict[str, Any]) -> bool:
    """
    ``autoroute_announce`` par tunnel si présent, sinon valeur AS ``traffic_engineering.autoroute_announce``
    (défaut ``false``).

    Désactivé par défaut pour que ``traceroute`` IP et ``traceroute mpls ipv4`` restent distincts.
    """
    if isinstance(tun.get("autoroute_announce"), bool):
        return tun["autoroute_announce"]
    return bool(te_cfg.get("autoroute_announce", False))


def mpls_te_enabled(as_data: Dict[str, Any]) -> bool:
    """True si MPLS et traffic_engineering sont activés dans l'underlay."""
    mpls = _deep_get(as_data, ["underlay", "mpls"], {}) or {}
    if not mpls.get("enabled"):
        return False
    te = mpls.get("traffic_engineering", {}) or {}
    return bool(te.get("enabled", False))


def rsvp_default_bandwidth(as_data: Dict[str, Any]) -> int:
    """Bande passage RSVP par défaut (kb/s) lorsque TE est activé."""
    te = _deep_get(as_data, ["underlay", "mpls", "traffic_engineering"], {}) or {}
    return int(te.get("rsvp_default_bandwidth", 0))


def get_explicit_paths_for_node(node: str, te_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Chemins explicites à émettre sur ``node`` : ceux référencés par un tunnel
    dont ``source_node`` == ``node``.
    """
    if not te_cfg:
        return []
    tunnels = te_cfg.get("tunnels", []) or []
    path_names: Set[str] = {
        str(t["path_option_name"])
        for t in tunnels
        if isinstance(t, dict) and t.get("source_node") == node and t.get("path_option_name")
    }
    if not path_names:
        return []
    paths = te_cfg.get("explicit_paths", []) or []
    return [p for p in paths if isinstance(p, dict) and p.get("name") in path_names]


def get_te_tunnels_for_node(node: str, te_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tunnels TE à créer sur le routeur tête ``node`` (``source_node``)."""
    if not te_cfg:
        return []
    return [
        t
        for t in (te_cfg.get("tunnels", []) or [])
        if isinstance(t, dict) and t.get("source_node") == node
    ]


def validate_traffic_engineering(as_name: str, as_data: Dict[str, Any], errors: List[str]) -> None:
    """
    Ajoute des messages à ``errors`` si la configuration TE/RSVP est incohérente.
    Appelé depuis ``validate_intent`` pour chaque AS.
    """
    mpls = _deep_get(as_data, ["underlay", "mpls"], {}) or {}
    te_underlay = mpls.get("traffic_engineering", {}) or {}
    te_enabled = bool(mpls.get("enabled") and te_underlay.get("enabled", False))

    if te_underlay and not mpls.get("enabled"):
        errors.append(f"{as_name}: underlay.mpls.traffic_engineering requiert mpls.enabled=true")

    if not te_enabled:
        if as_data.get("traffic_engineering"):
            errors.append(
                f"{as_name}: traffic_engineering présent mais "
                f"underlay.mpls.traffic_engineering.enabled n'est pas activé"
            )
        return

    bw = te_underlay.get("rsvp_default_bandwidth")
    if bw is None:
        errors.append(
            f"{as_name}: underlay.mpls.traffic_engineering.rsvp_default_bandwidth requis "
            f"lorsque traffic_engineering.enabled est vrai"
        )
    elif not isinstance(bw, int) or bw <= 0:
        errors.append(
            f"{as_name}: underlay.mpls.traffic_engineering.rsvp_default_bandwidth "
            f"doit être un entier strictement positif"
        )

    te_cfg = as_data.get("traffic_engineering", {}) or {}
    if not isinstance(te_cfg, dict):
        errors.append(f"{as_name}: traffic_engineering doit être un objet")
        return

    all_nodes = set(get_all_nodes(as_data))
    pe_nodes = set(get_nodes_by_role(as_data, "PE"))

    explicit_paths = te_cfg.get("explicit_paths", []) or []
    if not isinstance(explicit_paths, list):
        errors.append(f"{as_name}: traffic_engineering.explicit_paths doit être une liste")
        explicit_paths = []

    path_names: Set[str] = set()
    for i, path in enumerate(explicit_paths):
        if not isinstance(path, dict):
            errors.append(f"{as_name}: traffic_engineering.explicit_paths[{i}] doit être un objet")
            continue
        name = path.get("name")
        if not name or not isinstance(name, str):
            errors.append(f"{as_name}: traffic_engineering.explicit_paths[{i}].name requis (string)")
            continue
        if name in path_names:
            errors.append(f"{as_name}: nom de chemin explicite dupliqué: {name!r}")
        path_names.add(name)

        hops = path.get("hops")
        if not isinstance(hops, list) or not hops:
            errors.append(f"{as_name}: traffic_engineering.explicit_paths[{name!r}].hops requis (liste non vide)")
            continue
        for hop in hops:
            if not isinstance(hop, str):
                errors.append(f"{as_name}: hop invalide dans explicit_paths[{name!r}] (string attendue)")
            elif hop not in all_nodes:
                errors.append(f"{as_name}: hop {hop!r} inconnu dans explicit_paths[{name!r}]")

    tunnels = te_cfg.get("tunnels", []) or []
    if not isinstance(tunnels, list):
        errors.append(f"{as_name}: traffic_engineering.tunnels doit être une liste")
        return

    tunnel_ids: Set[int] = set()
    for i, tun in enumerate(tunnels):
        if not isinstance(tun, dict):
            errors.append(f"{as_name}: traffic_engineering.tunnels[{i}] doit être un objet")
            continue

        tid = tun.get("id")
        if not isinstance(tid, int) or tid <= 0:
            errors.append(f"{as_name}: traffic_engineering.tunnels[{i}].id requis (entier > 0)")
        elif tid in tunnel_ids:
            errors.append(f"{as_name}: id de tunnel dupliqué: {tid}")
        else:
            tunnel_ids.add(tid)

        src = tun.get("source_node")
        dst = tun.get("destination_node")
        if not src or src not in all_nodes:
            errors.append(f"{as_name}: traffic_engineering.tunnels[{i}].source_node invalide ou inconnu")
        elif src not in pe_nodes:
            errors.append(f"{as_name}: traffic_engineering.tunnels[{i}].source_node doit être un PE ({src!r})")

        if not dst or dst not in all_nodes:
            errors.append(f"{as_name}: traffic_engineering.tunnels[{i}].destination_node invalide ou inconnu")
        elif dst not in pe_nodes:
            errors.append(
                f"{as_name}: traffic_engineering.tunnels[{i}].destination_node doit être un PE ({dst!r})"
            )

        path_opt = tun.get("path_option_name")
        if not path_opt or not isinstance(path_opt, str):
            errors.append(f"{as_name}: traffic_engineering.tunnels[{i}].path_option_name requis (string)")
        elif path_opt not in path_names:
            errors.append(
                f"{as_name}: traffic_engineering.tunnels[{i}].path_option_name {path_opt!r} "
                f"ne correspond à aucun explicit_paths[].name"
            )

        ar = tun.get("autoroute_announce")
        if ar is not None and not isinstance(ar, bool):
            errors.append(
                f"{as_name}: traffic_engineering.tunnels[{i}].autoroute_announce doit être un booléen"
            )

    ar_global = te_cfg.get("autoroute_announce")
    if ar_global is not None and not isinstance(ar_global, bool):
        errors.append(f"{as_name}: traffic_engineering.autoroute_announce doit être un booléen")
