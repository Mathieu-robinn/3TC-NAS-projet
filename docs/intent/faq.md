# FAQ — Intent.json (v4.0)

## À quoi servait `autogen_rules` ? Est-ce utile ?

Dans l’historique du projet, `autogen_rules` décrivait l’idée d’une allocation (loopbacks, p2p, LAN, CE-PE) “par stratégie”.

Dans le générateur actuel:
- les allocations sont **déjà déterminées par le code** (itération sur les nœuds/liens/sites)
- `autogen_rules` n’était **pas consommé**

Conclusion:
- **inutile** tant qu’il n’y a pas d’implémentation réelle derrière
- il a été retiré pour éviter la confusion

Si un jour tu veux réintroduire l’idée, il faut que le script supporte réellement plusieurs stratégies d’allocation (ordre stable, “random seed”, par rôle, etc.).

## Le `type` dans `links[]` est-il pertinent ?

Dans la version actuelle du projet, non: il est retiré du schéma pour simplifier.

Pourquoi:
- les liens de l’intent sont tous des liens core
- la logique “par type” n’apportait rien dans ce contexte

En pratique:
- l’area OSPF est soit globale (`single_area`), soit explicite par lien (`igp_area`)
- MPLS est soit global (`all_core_links`), soit explicite par lien (`mpls`)

## `underlay.igp.area_design` / `enabled_on` étaient-ils pertinents ?

Historiquement ils existaient dans l’intent mais n’étaient pas appliqués.

En v4.0:
- `underlay.igp.area_design` est remplacé par **`underlay.igp.area.mode`** et est **implémenté**.
- `mpls.enabled_on` est remplacé par **`mpls.interfaces.mode`** et est **implémenté**.

## “RR clients” en iBGP, c’est quoi ?

`rr_clients` = stratégie iBGP où:
- un (ou plusieurs) PE(s) est déclaré **Route Reflector (RR)**
- les autres PEs (clients) établissent leurs sessions iBGP vers le RR
- le RR “réfléchit” les routes iBGP entre clients

But:
- éviter un full-mesh iBGP entre tous les PEs

Dans l’intent:
- `bgp.peering.strategy = rr_clients`
- `bgp.route_reflectors.nodes = ["PE1"]` (au moins un RR)

Alternative:
- `full_mesh` (simple mais non scalable)
- `rr_redundant` (2 RRs ou plus, redondance)

## Pourquoi RD unique “par VRF” et pas “par route” ?

Le RD (Route Distinguisher) sert à rendre **les préfixes d’une VRF uniques dans BGP vpnv4**.

Pratique courante:
- un RD **par VRF** (stable, lisible, évite collisions)

Un RD “par route” n’est pas un usage standard et complexifie inutilement l’exploitation sans bénéfice dans ce lab.

Dans v4.0, le générateur produit par défaut:
- `RD = <core_asn>:(base + index_vrf)`

## RT: `auto_per_vrf` vs `auto_per_customer_asn`, lequel choisir ?

- `auto_per_vrf`:
  - un RT par VRF (couplé à l’index)
  - pratique quand tu veux isoler strictement des VRFs même si elles partagent un ASN client
- `auto_per_customer_asn`:
  - RT dérivé de l’ASN client
  - peut être plus “parlant” si 1 VRF ↔ 1 client

## LAN: quelles possibilités et quel impact sur l’annonce BGP ?

Types supportés:
- `loopback`: simple, aucun VLAN, idéal pour un LAN “test” par CE
- `interface`: met une IP sur une interface L3 donnée
- `subinterface_vlan`: crée une subinterface dot1q (`encapsulation dot1Q`)

Méthodes d’annonce BGP:
- `network_statement`: ajoute `network ... mask ...` (le plus “propre” et déterministe)
- `redistribute_connected`: redistribue connected, mais filtré par route-map (match interface LAN) pour éviter d’annoncer le lien CE-PE

## Est-ce que le script peut traiter d’autres protocoles/stratégies ?

Oui, le schéma est conçu pour évoluer. Aujourd’hui, le générateur supporte déjà:
- IGP: `ospf` (complet) + `isis` (support minimal)
- iBGP peering: `rr_clients`, `full_mesh`, `rr_redundant`
- MPLS activation: `all_core_links`, `explicit`
- LAN: `loopback`, `interface`, `subinterface_vlan`

Extensions classiques possibles (non implémentées ici):
- PE-CE: OSPF ou statique, ou options eBGP avancées
- VRF: import/export plus riche (policies), multi-RT, hub-and-spoke
- MPLS: SR-MPLS (hors scope IOS c7200 classique)

