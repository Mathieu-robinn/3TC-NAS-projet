# Documentation du dépôt

Ce dépôt automatise la génération de configurations **Cisco IOS** (MPLS / BGP VPN) à partir d’un fichier **intent JSON**, puis la prise en charge **GNS3** (copie startup-config ou push telnet).

## Arborescence utile

| Élément | Rôle |
|--------|------|
| [`cisco_intent/`](../cisco_intent/) | Package Python : générateur, update, push, sync |
| [`intent/`](../intent/) | Fichiers intent d’exemple (`Intent.v4.json`, etc.) |
| [`configs/`](../configs/) | `live/`, `staging/`, `backup/` (zips), `scratch_old/` |
| `configs/live/` | Sortie de `generate` : `*.cfg` + intent ; OLD par défaut de `update` |
| `configs/staging/` | Jeu NEW de `update` (vidé après `update --push` réussi) |
| `configs/backup/full_configs/`, `configs/backup/modifs/` | Archives `.zip` (snapshots configs complètes + snippets modifs de `update`) |
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
| `generate <intent.json>` | `live/` si aucun `*.cfg` dedans, sinon `staging/` ; **toujours** `live/` avec `--push` |
| `update` | OLD = `configs/live/` ; NEW dans `configs/staging/` ; modifs archivées dans `backup/modifs/*.zip` (temporaire sur disque le temps du run) |
| `push <projet_gns3> <dossier_cfg>` | Telnet GNS3 ; si dossier ≠ `live/` et `staging/` a des `*.cfg` → copie `staging` → `live` après succès |
| `sync-startup <projet_gns3>` | Copie les `.cfg` depuis **`configs/live/`** (ou `--configs-dir`) vers les **startup-config** Dynamips |

Options détaillées : `python -m cisco_intent <sous-commande> -h`.

### Flux typiques

**Génération seule**

```bash
python -m cisco_intent generate intent/Intent.v4.json
```

**Génération puis push telnet** (sans commande `push` séparée)

```bash
python -m cisco_intent generate intent/Intent.v4.json --push --gns3-project gns3/projet
```

**Diff puis push** des modifs

```bash
python -m cisco_intent update --new-intent intent/foo.json --push --gns3-project gns3/projet
```

**Préparer un démarrage à froid GNS3** (fichiers startup sur disque)

```bash
python -m cisco_intent sync-startup gns3/projet
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
