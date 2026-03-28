# Référence du schéma `Intent.json` (v4.0)

Ce document décrit **les champs réellement supportés** par le package `cisco_intent` (`python -m cisco_intent generate`) et les valeurs attendues.

## Outils et commandes

Le schéma ci-dessous est consommé par le générateur (`cisco_intent.generator`). En pratique :

| Commande | Effet |
|----------|--------|
| `python -m cisco_intent generate <intent.json>` | Écrit sous **`configs/<name>/`** (`<name>` = champ racine `name`) : `live/` si vide (sans `*.cfg`), sinon `staging/` ; avec `--push` toujours `live/` + zip dans `configs/<name>/backup/full_configs/` |
| `python -m cisco_intent update --new-intent …` | OLD = **`configs/<name>/live/`** par défaut (même `name` que le NEW) ; NEW dans **`configs/<name>/staging/`** ; modifs dans **`configs/<name>/backup/modifs/Modifs-*.zip`** |
| `python -m cisco_intent push <projet> <dossier_cfg>` | Push **telnet** des `.cfg` vers des routeurs **déjà démarrés** dans GNS3 |
| `python -m cisco_intent sync-startup <projet> --topology <name>` | Copie les `<hostname>.cfg` depuis **`configs/<name>/live/`** (ou **`--configs-dir`**) vers les **startup-config** Dynamips |
| `python -m cisco_intent reset <projet>` | Copie **`configs/default/default-conf-C7200.txt`** (ou **`--template`**) vers **chaque** startup Dynamips du projet |

Options **`--push`** et **`--gns3-project`** sur `generate` et `update` enchaînent le push telnet après succès (voir `python -m cisco_intent generate -h` / `update -h`). Les chemins par défaut sont définis dans `cisco_intent.paths` (`PROJECT_ROOT`).

## Vue d’ensemble

Structure haut niveau:

```json
{
  "intent_version": "4.0",
  "name": "topologie1",
  "addressing": { ... },
  "autonomous_systems": { ... },
  "customers": [ ... ],
  "vpn_services": { ... },
  "pe_ce": { ... },
  "lan": { ... }
}
```

## `name` (topologie)

- **Type**: string
- **Requis**: **oui**
- **Format**: 1 à 64 caractères ; uniquement lettres, chiffres, tirets bas `_` et tirets `-` (ex. `topologie1`, `topology_1`).

Identifiant de la topologie : tous les fichiers générés et les dossiers **`live/`**, **`staging/`**, **`backup/`**, **`scratch_old/`** vivent sous **`configs/<name>/`** à la racine du dépôt. La CLI `generate` / `update` lit ce champ pour choisir les chemins ; `sync-startup` utilise **`--topology <name>`** pour pointer vers **`configs/<name>/live/`**.

## `intent_version`

- **Type**: string
- **Requis**: recommandé
- **Valeur attendue**: `"4.0"`

Le générateur n’impose pas strictement la valeur, mais la documentation et les exemples sont alignés sur `4.0`.

## `addressing`

- **Type**: object
- **Requis**: oui

Champs:

| Champ | Type | Requis | Exemple | Rôle |
|---|---:|---:|---|---|
| `loopback_pool` | string (CIDR) | oui | `1.0.0.0/8` | Pool des loopbacks des nœuds core (PE/P). |
| `p2p_pool` | string (CIDR) | oui | `10.0.0.0/16` | Pool des liens core P2P. |
| `customer_pool` | string (CIDR) | oui | `172.16.0.0/16` | Pool des liens CE-PE (accès client). |
| `p2p_prefix` | int | oui | `30` | Préfixe des sous-réseaux P2P core. |
| `ce_pe_prefix` | int | oui | `30` | Préfixe des sous-réseaux CE-PE (seule source utilisée par le générateur). |

Notes:
- `customer_prefix` peut apparaître dans des intents d’exemple mais n’est pas consommé par `cisco_intent`.

## `autonomous_systems`

- **Type**: object (map)
- **Requis**: oui

Chaque entrée (ex: `"AS1"`) décrit un AS “opérateur” (core).

### `autonomous_systems.<asName>.asn`
- **Type**: int
- **Requis**: oui

### `autonomous_systems.<asName>.nodes`
- **Type**: object (map)
- **Requis**: oui

Format:

```json
"nodes": {
  "PE1": { "role": "PE" },
  "P1":  { "role": "P" }
}
```

`role` (enum):
- `PE`: génère iBGP vpnv4 + VRF + eBGP CE-PE
- `P`: génère underlay uniquement (IGP/MPLS selon config)

### `autonomous_systems.<asName>.links`
- **Type**: array
- **Requis**: oui

Chaque élément est un **objet** avec exactement deux **endpoints** (pas d’autre forme de lien).

Chaque lien core doit avoir 2 endpoints :

```json
{
  "endpoints": [
    { "node": "PE1", "interface": "GigabitEthernet1/0" },
    { "node": "P1",  "interface": "GigabitEthernet1/0" }
  ]
}
```

Champs:

| Champ | Type | Requis | Défaut | Rôle |
|---|---:|---:|---|---|
| `endpoints` | array[2] | oui | — | Extrémités du lien (nœud + interface). |
| `igp_area` | int | non | `0` | Utilisé si `underlay.igp.area.mode="explicit"`. |
| `mpls` | bool | non | `false` | Utilisé si `mpls.interfaces.mode="explicit"`. |

## `underlay`

### `underlay.igp`
- **Type**: object
- **Requis**: non (mais recommandé)

Champs:

| Champ | Type | Requis | Valeurs | Rôle |
|---|---:|---:|---|---|
| `protocol` | string | non | `ospf`, `isis` | Choix IGP (génération OSPF ou IS-IS minimal). |
| `area.mode` | string | non | `single_area`, `explicit` | Mode de design OSPF area. |

Comportements:
- `single_area`: tous les réseaux core en area 0.
- `explicit`: area prise depuis `links[].igp_area`.

### `underlay.mpls`
- **Type**: object
- **Requis**: non

Champs:

| Champ | Type | Requis | Valeurs | Rôle |
|---|---:|---:|---|---|
| `enabled` | bool | non | true/false | Active MPLS/LDP global + possibilité de `mpls ip` interface. |
| `label_distribution` | string | non | `ldp` | Indicatif (le générateur produit LDP). |
| `interfaces.mode` | string | non | `all_core_links`, `explicit` | Contrôle “mpls ip” par interface. |

Comportements:
- `all_core_links`: `mpls ip` sur toutes les interfaces core.
- `explicit`: `mpls ip` seulement si `links[].mpls=true`.

## `bgp` (core)

### `bgp.type`
- **Type**: string
- **Requis**: non
- **Valeur supportée**: `ibgp`

### `bgp.vpnv4`
- **Type**: bool
- **Requis**: non
- **Rôle**: si `true`, le générateur produit MP-BGP vpnv4 sur les PE.

### `bgp.peering`

```json
"peering": {
  "strategy": "rr_clients",
  "transport": "loopback"
}
```

Champs:

| Champ | Type | Requis | Valeurs | Rôle |
|---|---:|---:|---|---|
| `strategy` | string | non | `rr_clients`, `full_mesh`, `rr_redundant` | Stratégie de sessions iBGP entre PEs. |
| `transport` | string | non | `loopback` | Update-source Loopback0. |

### `bgp.route_reflectors`

- **Type**: object
- **Requis**: si `strategy` vaut `rr_clients` ou `rr_redundant`

```json
"route_reflectors": { "nodes": ["PE1", "PE2"] }
```

## `customers`

Liste des clients et de leurs sites.

```json
{
  "name": "CUST1",
  "asn": 65001,
  "sites": [
    {
      "ce": "CE1-1",
      "pe": "PE1",
      "link": {
        "endpoints": [
          { "node": "CE1-1", "interface": "GigabitEthernet2/0" },
          { "node": "PE1",   "interface": "GigabitEthernet2/0" }
        ]
      }
    }
  ]
}
```

Champs:

| Champ | Type | Requis | Rôle |
|---|---:|---:|---|
| `name` | string | oui | Nom client (sert aussi au mapping VRF). |
| `asn` | int | oui | ASN client (eBGP CE-PE). |
| `sites` | array | oui | Sites multi-homing / multi-CE. |
| `sites[].ce` | string | oui | Nom du routeur CE. |
| `sites[].pe` | string | oui | PE attaché. |
| `sites[].link.endpoints` | array[2] | oui | Interfaces CE et PE sur le lien d’accès. |

## `vpn_services`

```json
"vpn_services": {
  "type": "l3vpn",
  "rd": { "mode": "asn_vrfid", "base": 100 },
  "rt": { "strategy": "auto_per_vrf" },
  "vrfs": [ { "name": "CUST1", "customer": "CUST1" } ]
}
```

### `vpn_services.type`
- **Type**: string
- **Requis**: recommandé
- **Valeur attendue**: `l3vpn`

### `vpn_services.vrfs[]`
Chaque VRF associe un nom (sur PE) à un client:

| Champ | Type | Requis | Rôle |
|---|---:|---:|---|
| `name` | string | non | Nom VRF (défaut = `customer`). |
| `customer` | string | oui | Référence `customers[].name`. |

### RD: `vpn_services.rd`

| Champ | Type | Requis | Valeurs | Rôle |
|---|---:|---:|---|---|
| `mode` | string | non | `asn_vrfid`, `asn_hash` | Construction RD. |
| `base` | int | non | ex: `100` | Base de l’ID VRF (mode `asn_vrfid`). |

Le générateur produit un RD **unique par VRF** (recommandé). Il ne produit pas de RD “par route”.

### RT: `vpn_services.rt`

| Champ | Type | Requis | Valeurs | Rôle |
|---|---:|---:|---|---|
| `strategy` | string | non | `auto_per_vrf`, `auto_per_customer_asn` | Construction RT import/export. |

## `pe_ce`

Ce bloc documente l’intention, mais `cisco_intent` implémente actuellement le cas eBGP CE-PE.

```json
"pe_ce": {
  "routing": "ebgp",
  "addressing": { "strategy": "derived_from_customer_pool", "prefix": 30 }
}
```

Champs:
- `routing`: attendu `ebgp` (documentation du scénario lab).
- `addressing` dans ce bloc est informatif ; le préfixe CE-PE effectif est **`addressing.ce_pe_prefix`** à la racine.

## `lan`

Le LAN est un “LAN de test” créé sur chaque CE, avec annonce dans le BGP client.

Champs communs:

```json
"lan": {
  "enabled": true,
  "type": "loopback",
  "addressing": { "base_pool": "10.0.0.0/8", "prefix": 32, "strategy": "per_site" },
  "bgp": { "advertise": true, "method": "network_statement" }
}
```

### `lan.enabled`
- **Type**: bool
- **Défaut**: `true`

### `lan.type` (enum)
- `loopback`: interface CE = `lan.naming.pattern` (défaut `Loopback0`).
- `interface`: interface CE = `lan.interface.name` (ou override par site, voir ci-dessous).
- `subinterface_vlan`: interface CE = `<parent>.<vlan>` et ajout `encapsulation dot1Q`.

Champs spécifiques:

#### `loopback`
```json
"naming": { "pattern": "Loopback0" }
```

#### `interface`
```json
"interface": { "name": "GigabitEthernet0/1" }
```

Option: override par site (si besoin):
```json
"sites": [
  { "...": "...", "lan": { "interface": "GigabitEthernet0/2" } }
]
```

#### `subinterface_vlan`
```json
"subinterface": { "parent": "GigabitEthernet0/1", "vlan_base": 200 }
```

Le VLAN effectif est `vlan_base + site_index`.

### `lan.bgp`

| Champ | Type | Requis | Valeurs | Rôle |
|---|---:|---:|---|---|
| `advertise` | bool | non | true/false | Active l’annonce du LAN dans BGP côté CE. |
| `method` | string | non | `network_statement`, `redistribute_connected` | Méthode d’annonce. |

Notes:
- `redistribute_connected` utilise une route-map qui match **uniquement** l’interface LAN (pour éviter d’annoncer le lien CE-PE).

