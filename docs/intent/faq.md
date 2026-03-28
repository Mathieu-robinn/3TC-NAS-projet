# FAQ — Intent.json (v4.0)

## À quoi servait `autogen_rules` ? Est-ce utile ?

`autogen_rules` était une idée de schéma pour décrire une allocation (loopbacks, p2p, LAN, CE-PE) « par stratégie », sans implémentation dans `cisco_intent`.

Dans le générateur actuel :
- les allocations sont **déjà déterminées par le code** (itération sur les nœuds/liens/sites)
- `autogen_rules` n’était **pas consommé**

Conclusion:
- **inutile** tant qu’il n’y a pas d’implémentation réelle derrière
- il a été retiré pour éviter la confusion

Si un jour tu veux réintroduire l’idée, il faut que le générateur (`cisco_intent`) supporte réellement plusieurs stratégies d’allocation (ordre stable, “random seed”, par rôle, etc.).

## Le `type` dans `links[]` est-il pertinent ?

Dans la version actuelle du projet, non: il est retiré du schéma pour simplifier.

Pourquoi:
- les liens de l’intent sont tous des liens core
- la logique “par type” n’apportait rien dans ce contexte

En pratique:
- l’area OSPF est soit globale (`single_area`), soit explicite par lien (`igp_area`)
- MPLS est soit global (`all_core_links`), soit explicite par lien (`mpls`)

## Champs underlay IGP / MPLS à utiliser

- Aires OSPF : **`underlay.igp.area.mode`** (`single_area` ou `explicit`, avec `igp_area` par lien si besoin).
- MPLS sur les liens core : **`underlay.mpls.enabled`** et **`underlay.mpls.interfaces.mode`** (`all_core_links` ou `explicit`, avec `mpls` par lien si besoin).

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

## Où sont les fichiers intent et les sorties ?

- **Intents** : répertoire [`intent/`](../../intent/) à la racine du dépôt (ex. `intent/topologie1/Intent_*.json`). Chaque intent doit avoir un champ racine **`name`** (identifiant de topologie).
- **Configs complètes** : tout est sous **`configs/<name>/`** où `<name>` vient de l’intent. `generate` sans `--push` → **`configs/<name>/live/`** si aucun `*.cfg` dans ce `live/`, sinon **`configs/<name>/staging/`** ; avec `--push` → toujours ce `live/`. Zip **`configs/<name>/backup/full_configs/Configs-<timestamp>.zip`** à chaque génération.
- **Modifs (`update`)** : archive **`configs/<name>/backup/modifs/Modifs-<timestamp>.zip`** (push ultérieur = dézipper puis `push <dossier>`).
- **`configs/<name>/live/`** : dernier jeu appliqué pour cette topologie ; OLD par défaut pour `update` lorsque l’intent NEW a le même `name` (sinon `--old-configs-dir`).
- **Reset lab** : **`configs/default/default-conf-C7200.txt`** — copié vers chaque startup Dynamips par la commande **`reset`** (hors arborescence par topologie).

Ces chemins par défaut sont relatifs à la **racine du dépôt** (`cisco_intent.paths.PROJECT_ROOT`).

## Différence entre `push` (telnet) et `sync-startup` ?

- **`push`** : envoie le contenu des `.cfg` sur les **consoles telnet** GNS3 — les VMs/routeurs doivent être **déjà démarrés**. Adapté au **chaud** (y compris les fichiers de **modifs** produits par `update`).
- **`sync-startup`** : copie les `<hostname>.cfg` depuis **`configs/<name>/live/`** si tu passes **`--topology <name>`**, ou depuis **`--configs-dir`** (ex. dossier extrait d’un zip ou `configs/<name>/staging`) vers les **startup-config** Dynamips — utile pour un **prochain démarrage à froid** dans GNS3.

## C’est quoi `reset` par rapport à `sync-startup` ?

- **`sync-startup`** : un fichier **par routeur** (`PE1.cfg`, `PE2.cfg`, …) aligné sur les configs générées.
- **`reset`** : un **seul** fichier template (**`configs/default/default-conf-C7200.txt`** par défaut) copié **vers chaque** startup Dynamips du projet. Après coup, redémarrer les nœuds dans GNS3 pour charger cette config de base.

## `--push` sur `generate` / `update` vs sous-commande `push` ?

- **`generate … --push --gns3-project DIR`** : génère toujours dans **`configs/<name>/live/`** puis push ; ce `live` reste la référence pour la topologie `<name>`.
- **`push` (manuel)** : après succès, si le dossier poussé n’est pas le `live/` de la topologie concernée et que **`staging/`** de cette topologie contient des `*.cfg`, copie `staging` → `live` puis vide `staging` (topologie déduite de l’intent ou du chemin).
- **`update … --push --gns3-project DIR`** : pousse les snippets depuis un répertoire temporaire (après création du zip) ; si le push réussit, **`configs/<name>/live/`** est rempli depuis **`staging/`** (NEW complet), pas depuis les seules modifs.
- **`push PROJET DOSSIER_CFG`** : équivalent explicite quand tu choisis toi-même le dossier de `.cfg`.

Options communes : `--push-only`, `--push-dry-run`, `--push-write-memory`, etc. (`python -m cisco_intent generate -h` / `update -h`).

## `update --dry-run` et `--push` ?

Avec **`--dry-run`**, `update` **n’écrit pas** les modifs sur disque : **`--push` est ignoré** (rien de cohérent à envoyer). Enlever `--dry-run` pour produire les fichiers puis pousser, ou lancer `push` séparément ensuite.

## Est-ce que le générateur peut traiter d’autres protocoles/stratégies ?

Oui, le schéma est conçu pour évoluer. Aujourd’hui, le générateur supporte déjà:
- IGP: `ospf` (complet) + `isis` (support minimal)
- iBGP peering: `rr_clients`, `full_mesh`, `rr_redundant`
- MPLS activation: `all_core_links`, `explicit`
- LAN: `loopback`, `interface`, `subinterface_vlan`

Extensions classiques possibles (non implémentées ici):
- PE-CE: OSPF ou statique, ou options eBGP avancées
- VRF: import/export plus riche (policies), multi-RT, hub-and-spoke
- MPLS: SR-MPLS (hors scope IOS c7200 classique)

