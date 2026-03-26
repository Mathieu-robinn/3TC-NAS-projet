#!/usr/bin/env python3
"""
migrate_intent.py — Convertit un Intent historique vers le schéma v4 (lisible).

Usage:
  python migrate_intent.py script/Intent.json > script/Intent.v4.json
"""

import json
import sys
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def migrate(intent: dict) -> dict:
    # Minimal migration by reusing the same normalization logic conceptually.
    out = json.loads(json.dumps(intent))

    out["intent_version"] = "4.0"
    out.pop("autogen_rules", None)
    out.pop("policies", None)
    out.pop("validation", None)

    for _, as_data in out.get("autonomous_systems", {}).items():
        # links[].type is removed from the current schema (all links are core links)
        for link in as_data.get("links", []):
            if isinstance(link, dict):
                link.pop("type", None)

        underlay = as_data.setdefault("underlay", {})
        igp = underlay.setdefault("igp", {})
        area_design = igp.pop("area_design", None)
        area = igp.setdefault("area", {})
        area.setdefault("mode", area_design or "single_area")
        if area.get("mode") == "per_link_type":
            area["mode"] = "single_area"
        area.pop("area_by_link_type", None)

        mpls = underlay.setdefault("mpls", {})
        enabled_on = mpls.pop("enabled_on", None)
        interfaces = mpls.setdefault("interfaces", {})
        if enabled_on:
            interfaces.setdefault("mode", "all_core_links")
        else:
            interfaces.setdefault("mode", "all_core_links")
        if interfaces.get("mode") == "by_link_type":
            interfaces["mode"] = "all_core_links"
        interfaces.pop("link_types", None)

        bgp = as_data.setdefault("bgp", {})
        rr = bgp.pop("route_reflector", None)
        rrs = bgp.setdefault("route_reflectors", {})
        if rr and rr.get("enabled") and rr.get("node"):
            rrs.setdefault("nodes", [rr["node"]])

    vpn = out.setdefault("vpn_services", {})
    vpn.pop("rd_strategy", None)
    rt_legacy = vpn.pop("rt_strategy", None)
    if rt_legacy:
        vpn.setdefault("rt", {}).setdefault("strategy", rt_legacy)
    vpn.setdefault("rd", {}).setdefault("mode", "asn_vrfid")
    vpn.setdefault("rd", {}).setdefault("base", 100)
    vpn.setdefault("rt", {}).setdefault("strategy", "auto_per_vrf")

    if "lan" in out:
        out["lan"].setdefault("type", "loopback")
        out["lan"].setdefault("enabled", True)
        out["lan"].setdefault("bgp", {}).setdefault("method", "network_statement")
    else:
        out["lan"] = {
            "enabled": True,
            "type": "loopback",
            "addressing": {"base_pool": "10.0.0.0/8", "prefix": 32, "strategy": "per_site"},
            "naming": {"pattern": "Loopback0"},
            "bgp": {"advertise": True, "method": "network_statement"},
        }

    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: python migrate_intent.py <intent.json>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    migrated = migrate(load(path))
    json.dump(migrated, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()

