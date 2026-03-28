# Documentation du dépôt

Ce dépôt automatise la génération de configurations **Cisco IOS** (MPLS / BGP VPN) à partir d’un fichier **intent JSON**, puis la prise en charge **GNS3** (copie startup-config ou push telnet).

## Arborescence utile

| Élément | Rôle |
|--------|------|
| [`cisco_intent/`](../cisco_intent/) | Package Python : générateur, diff, push, sync |
| [`intent/`](../intent/) | Fichiers intent d’exemple (`Intent.v4.json`, etc.) |
| [`Configs/`](../Configs/) | Sorties du générateur : `Configs-YYYYMMDD-HHMMSS/*.cfg` + copie de l’intent |
| [`modifs/`](../modifs/) | Sorties du diff : `Modifs-YYYYMMDD-HHMMSS/*.cfg` (commandes IOS incrémentales) |
| [`gns3/`](../gns3/) | Projets GNS3 |

Tous les chemins par défaut (`Configs/`, `modifs/`) sont ancrés sur la **racine du dépôt** (voir `cisco_intent.paths`).

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
| `generate <intent.json>` | Produit les `.cfg` complets dans `Configs/Configs-*` |
| `diff` | Compare deux générations d’intent, écrit les modifs dans `modifs/Modifs-*` |
| `push <projet_gns3> <dossier_cfg>` | Envoie les `.cfg` sur les consoles **telnet** GNS3 (routeurs déjà démarrés) |
| `sync-startup <projet_gns3>` | Copie le **dernier** run `Configs-*` vers les **startup-config** Dynamips |

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
python -m cisco_intent diff --new-intent intent/foo.json --push --gns3-project gns3/projet
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
