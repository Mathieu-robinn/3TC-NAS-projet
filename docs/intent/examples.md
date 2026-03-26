# Exemples d’intents (v4.0) + recettes

Ce document explique les intents fournis dans `script/examples/` et propose des “recettes” pour modifier une variante sans réécrire tout le JSON.

## Exemple 1 — iBGP full-mesh

Fichier: `script/examples/Intent_full_mesh.json`

Objectif:
- Underlay OSPF single-area + MPLS/LDP partout sur les liens core
- Overlay iBGP vpnv4 entre PEs en **full-mesh** (pas de route-reflector)

Points clés:
- BGP:

```json
"bgp": {
  "type": "ibgp",
  "vpnv4": true,
  "peering": { "strategy": "full_mesh", "transport": "loopback" }
}
```

Effet:
- Chaque PE établit une session iBGP (update-source Loopback0) vers chaque autre PE.
- Dans l’address-family `vpnv4`, tous les voisins sont activés (pas de `route-reflector-client`).

Quand l’utiliser:
- Topologies petites (2–6 PEs), lab simple.
- Quand tu veux éviter la complexité/centralisation d’un RR.

## Exemple 2 — Dual RR + multi-area OSPF (explicit) + MPLS explicite + LAN VLAN

Fichier: `script/examples/Intent_dual_rr_multi_area.json`

Objectif:
- OSPF multi-area: l’area est décidée **par lien** via `igp_area`
- MPLS: activation `mpls ip` **par lien** via `mpls: true/false`
- iBGP vpnv4: stratégie **RR redondants** (2 RRs)
- LAN CE: **subinterface VLAN** + annonce via `redistribute_connected`

Points clés:

### OSPF area explicite (par lien)

```json
"underlay": {
  "igp": { "protocol": "ospf", "area": { "mode": "explicit" } }
}
```

Et dans chaque lien:

```json
{ "...": "...", "igp_area": 10 }
```

### MPLS explicite (par lien)

```json
"mpls": {
  "enabled": true,
  "interfaces": { "mode": "explicit" }
}
```

Et dans chaque lien:

```json
{ "...": "...", "mpls": true }
```

### RR redondants

```json
"bgp": {
  "vpnv4": true,
  "route_reflectors": { "nodes": ["PE1", "PE2"] },
  "peering": { "strategy": "rr_redundant", "transport": "loopback" }
}
```

Effet:
- Les PEs non-RR se peerent vers **tous** les RRs listés.
- Les RRs se peerent aussi avec les autres PEs et configurent `route-reflector-client`.

### LAN en subinterface VLAN + redistribution connected

```json
"lan": {
  "enabled": true,
  "type": "subinterface_vlan",
  "subinterface": { "parent": "GigabitEthernet0/1", "vlan_base": 200 },
  "bgp": { "advertise": true, "method": "redistribute_connected" }
}
```

Effet:
- Sur chaque CE, création d’une subinterface `<parent>.<vlan>` avec `encapsulation dot1Q <vlan>`.
- Redistribution `connected` filtrée par route-map (match interface LAN) pour ne pas annoncer le lien CE-PE.

## Recettes

### Passer de RR (rr_clients) à full-mesh

1) Dans `autonomous_systems.<AS>.bgp.peering.strategy`, mettre `full_mesh`.\n
2) Supprimer (optionnel) `bgp.route_reflectors` (le script n’en a plus besoin).

```json
"peering": { "strategy": "full_mesh", "transport": "loopback" }
```

### Activer 2 RRs (rr_redundant)

```json
"route_reflectors": { "nodes": ["PE1", "PE2"] },
"peering": { "strategy": "rr_redundant", "transport": "loopback" }
```

### OSPF multi-area (2 modes)

- **Mode simple**:

```json
"area": { "mode": "single_area" }
```

- **Mode par lien**:

```json
"area": { "mode": "explicit" }
```

et `links[].igp_area`.

### MPLS partout / explicite

- Partout:

```json
"interfaces": { "mode": "all_core_links" }
```

- Par lien:

```json
"interfaces": { "mode": "explicit" }
```

et `links[].mpls`.

### LAN: loopback → interface → subinterface VLAN

- Loopback:

```json
"type": "loopback",
"naming": { "pattern": "Loopback0" }
```

- Interface L3:

```json
"type": "interface",
"interface": { "name": "GigabitEthernet0/1" }
```

- VLAN:

```json
"type": "subinterface_vlan",
"subinterface": { "parent": "GigabitEthernet0/1", "vlan_base": 200 }
```

