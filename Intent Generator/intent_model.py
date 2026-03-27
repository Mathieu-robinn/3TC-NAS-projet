from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import json
import ipaddress


IntentDict = Dict[str, Any]


def new_default_intent() -> IntentDict:
    """Return a minimal, valid-ish default intent structure."""
    return {
        "intent_version": "4.0",
        "addressing": {
            "loopback_pool": "1.0.0.0/8",
            "p2p_pool": "10.0.0.0/16",
            "customer_pool": "172.16.0.0/16",
            "p2p_prefix": 30,
            "ce_pe_prefix": 30,
        },
        "autonomous_systems": {
            "AS1": {
                "asn": 64512,
                "nodes": {
                    "PE1": {"role": "PE"},
                    "P1": {"role": "P"},
                },
                "links": [
                    {
                        "endpoints": [
                            {"node": "PE1", "interface": "GigabitEthernet1/0"},
                            {"node": "P1", "interface": "GigabitEthernet1/0"},
                        ]
                    }
                ],
                "underlay": {
                    "igp": {"protocol": "ospf", "area": {"mode": "single_area"}},
                    "mpls": {
                        "enabled": True,
                        "interfaces": {"mode": "all_core_links"},
                    },
                },
                "bgp": {
                    "type": "ibgp",
                    "vpnv4": True,
                    "peering": {"strategy": "full_mesh", "transport": "loopback"},
                },
            }
        },
        "customers": [
            {
                "name": "CUST1",
                "asn": 65001,
                "sites": [
                    {
                        "ce": "CE1-1",
                        "pe": "PE1",
                        "link": {
                            "endpoints": [
                                {
                                    "node": "CE1-1",
                                    "interface": "GigabitEthernet2/0",
                                },
                                {
                                    "node": "PE1",
                                    "interface": "GigabitEthernet2/0",
                                },
                            ]
                        },
                    }
                ],
            }
        ],
        "vpn_services": {
            "type": "l3vpn",
            "rd": {"mode": "asn_vrfid", "base": 100},
            "rt": {"strategy": "auto_per_vrf"},
            "vrfs": [{"name": "CUST1", "customer": "CUST1"}],
        },
        "pe_ce": {
            "routing": "ebgp",
            "addressing": {"strategy": "derived_from_customer_pool", "prefix": 30},
        },
        "lan": {
            "enabled": True,
            "type": "loopback",
            "addressing": {
                "base_pool": "10.0.0.0/8",
                "prefix": 32,
                "strategy": "per_site",
            },
            "naming": {"pattern": "Loopback0"},
            "bgp": {"advertise": True, "method": "network_statement"},
        },
    }


def load_intent(path: str | Path) -> IntentDict:
    """Load an intent JSON file."""
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        data: IntentDict = json.load(f)
    return data


def save_intent(intent: IntentDict, path: str | Path) -> None:
    """Save the intent to disk as JSON with indentation."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(intent, f, indent=2, ensure_ascii=False, sort_keys=True)


def _deep_get(dct: Dict[str, Any], path: List[str], default: Any = None) -> Any:
    cur: Any = dct
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def validate_intent(intent: IntentDict) -> List[str]:
    """Return a list of human-readable validation errors."""
    errors: List[str] = []

    addr = intent.get("addressing", {})
    for required in ["loopback_pool", "p2p_pool", "customer_pool", "p2p_prefix"]:
        if required not in addr:
            errors.append(f"Champ addressing.{required} manquant")

    pools: Dict[str, ipaddress._BaseNetwork] = {}
    for key in ["loopback_pool", "p2p_pool", "customer_pool"]:
        val = addr.get(key)
        if not val:
            continue
        try:
            pools[key] = ipaddress.ip_network(str(val), strict=False)
        except ValueError:
            errors.append(f"addressing.{key} invalide (CIDR attendu): {val}")

    pool_items = list(pools.items())
    for i in range(len(pool_items)):
        name_a, net_a = pool_items[i]
        for j in range(i + 1, len(pool_items)):
            name_b, net_b = pool_items[j]
            if net_a == net_b:
                errors.append(
                    f"addressing.{name_a} et addressing.{name_b} sont identiques ({net_a})"
                )
            elif net_a.overlaps(net_b):
                errors.append(
                    f"addressing.{name_a} ({net_a}) chevauche addressing.{name_b} ({net_b})"
                )

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
                errors.append(
                    "lan.interface.name requis quand lan.type=interface"
                )
        if lan_type == "subinterface_vlan":
            parent = _deep_get(lan_cfg, ["subinterface", "parent"])
            base = _deep_get(lan_cfg, ["subinterface", "vlan_base"])
            if not parent:
                errors.append(
                    "lan.subinterface.parent requis quand lan.type=subinterface_vlan"
                )
            if base is None:
                errors.append(
                    "lan.subinterface.vlan_base requis quand lan.type=subinterface_vlan"
                )

    for as_name, as_data in intent.get("autonomous_systems", {}).items():
        if "asn" not in as_data:
            errors.append(f"{as_name}: asn manquant")

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
            errors.append(
                f"{as_name}: bgp.peering.strategy invalide: {peering_strategy}"
            )
        if peering_strategy in {"rr_clients", "rr_redundant"}:
            rr_nodes = _deep_get(bgp, ["route_reflectors", "nodes"], [])
            if not rr_nodes:
                errors.append(
                    f"{as_name}: bgp.route_reflectors.nodes requis pour strategy={peering_strategy}"
                )

    autos = intent.get("autonomous_systems", {}) or {}
    asn_to_as_names: Dict[int, List[str]] = {}
    for as_name, as_data in autos.items():
        asn = as_data.get("asn")
        if isinstance(asn, int):
            asn_to_as_names.setdefault(asn, []).append(as_name)
    for asn, names in asn_to_as_names.items():
        if len(names) > 1:
            errors.append(
                f"ASN opérateur dupliqué {asn} pour les AS: {', '.join(sorted(names))}"
            )

    customers = intent.get("customers", [])
    customer_asn_to_names: Dict[int, List[str]] = {}
    customer_name_count: Dict[str, int] = {}
    for cust in customers:
        name = cust.get("name")
        if "name" not in cust:
            errors.append("Client sans champ name")
        elif not str(name).strip():
            errors.append("Client avec name vide")
        if "asn" not in cust:
            errors.append(f"Client {cust.get('name', '?')}: asn manquant")

        if isinstance(name, str) and name.strip():
            customer_name_count[name.strip()] = customer_name_count.get(name.strip(), 0) + 1

        asn = cust.get("asn")
        if isinstance(asn, int):
            key_name = str(name).strip() if isinstance(name, str) else "?"
            customer_asn_to_names.setdefault(asn, []).append(key_name)

    for name, count in customer_name_count.items():
        if count > 1:
            errors.append(f"Nom client dupliqué: {name}")

    for asn, names in customer_asn_to_names.items():
        if len(names) > 1:
            errors.append(f"ASN client dupliqué {asn} pour: {', '.join(names)}")

    vpn = intent.get("vpn_services", {})
    if vpn:
        if vpn.get("type", "l3vpn") != "l3vpn":
            errors.append(f"vpn_services.type invalide: {vpn.get('type')}")

    return errors

