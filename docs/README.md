# Documentation du dépôt

Ce dépôt automatise la génération de configurations **Cisco IOS** (MPLS / BGP VPN) à partir d’un fichier **intent JSON**, puis la prise en charge **GNS3** (copie startup-config, push telnet, reset startup par défaut).

## Arborescence utile

| Élément | Rôle |
|--------|------|
| [`cisco_intent/`](../cisco_intent/) | Package Python : générateur, update, push, sync, reset ; **`te.py`** = MPLS-TE/RSVP |
| [`intent/`](../intent/) | Fichiers intent d’exemple (ex. `topologie1/Intent_*.json`) |
| [`configs/<name>/`](../configs/) | **Par topologie** : `live/`, `staging/`, `scratch_old/`, `backup/` (zips). `<name>` = champ racine `name` de l’intent |
| `configs/<name>/live/` | Sortie de `generate` : `*.cfg` + intent ; OLD par défaut de `update` (même `name`) |
| `configs/<name>/staging/` | Jeu NEW de `update` (vidé après `update --push` réussi) |
| `configs/<name>/backup/full_configs/`, `.../modifs/` | Archives `.zip` (snapshots + snippets `update`) |
| [`configs/default/`](../configs/default/) | Config de référence C7200 (`default-conf-C7200.txt`) pour `reset` |
| [`gns3/`](../gns3/) | Projets GNS3 |

Les chemins par défaut sont ancrés sur la **racine du dépôt** (voir `cisco_intent.paths`).

## Prérequis

- **Python 3.10+**
- Dépendances : `pip install -r requirements.txt`  
  ou `./dependencies.sh` / `dependencies.bat` / `dependencies.ps1` à la racine du projet.

Lancer les commandes depuis la **racine du dépôt** (ou avec `PYTHONPATH` pointant sur celle-ci).

## Interface en ligne de commande

Point d’entrée unique :

```bash
python -m cisco_intent --help
```

| Sous-commande | Description |
|---------------|-------------|
| `generate <intent.json>` | Écrit sous `configs/<name>/` : `live/` si aucun `*.cfg`, sinon `staging/` ; **toujours** `live/` avec `--push` |
| `update` | OLD = `configs/<name>/live/` (nom = NEW intent) ; NEW dans `staging/` ; modifs dans `backup/modifs/*.zip` |
| `push <projet_gns3> <dossier_cfg>` | Telnet GNS3 ; sync `staging`→`live` possible si topologie déductible et dossier ≠ `live/` |
| `sync-startup <projet_gns3>` | Copie les `<name>.cfg` depuis **`--topology <name>`** (`configs/<name>/live/`) **ou** `--configs-dir` vers les startup Dynamips |
| `reset <projet_gns3>` | Copie le template (defaut **`default-conf-C7200.txt`**) vers chaque startup ; **`%h`** est remplace par le nom du nœud GNS3 (`--template` possible) |

Options détaillées : `python -m cisco_intent <sous-commande> -h`.

### Flux typiques

**Génération seule**

```bash
python -m cisco_intent generate intent/topologie1/Intent_isis_rr_redunt.json
```

**Génération puis push telnet**

```bash
python -m cisco_intent generate intent/topologie1/Intent_isis_rr_redunt.json --push --gns3-project gns3/projet
```

**Diff puis push** des modifs (MPLS, TE/RSVP, BGP, VRF, etc.)

```bash
python -m cisco_intent update --new-intent intent/topologie1/Intent_isis_rr_redunt.json --push --gns3-project gns3/projet
```

**Activer ou modifier MPLS-TE / RSVP à chaud** (chemins explicites, tunnels, bande passante RSVP)

```bash
python -m cisco_intent update --new-intent intent/topologie1/RSVPplease.json --dry-run
python -m cisco_intent update --new-intent intent/topologie1/RSVPplease.json --push --gns3-project gns3/mine
```

Voir [schéma intent — traffic_engineering](intent/schema.md#traffic_engineering-niveau-as) et [FAQ — TE à chaud](intent/faq.md#comment-activer-ou-modifier-le-te-à-chaud).

**Préparer un démarrage à froid GNS3** (fichiers startup sur disque)

```bash
python -m cisco_intent sync-startup gns3/projet --topology topologie1
```

**Réinitialiser tous les routeurs au template C7200** (puis redémarrer les nœuds dans GNS3)

```bash
python -m cisco_intent reset gns3/projet
```

Des exemples supplémentaires figurent dans le [README racine](../README.md).

## Index de la documentation

| Document | Contenu |
|----------|---------|
| [NAS_Project.md](NAS_Project.md) | Énoncé du projet NAS, phases, checklist (anglais) |
| [intent/README.md](intent/README.md) | Point d’entrée vers la doc intent |
| [intent/schema.md](intent/schema.md) | Référence des champs `Intent.json` v4 |
| [intent/examples.md](intent/examples.md) | Recettes et variantes d’intent |
| [intent/faq.md](intent/faq.md) | Questions fréquentes |
