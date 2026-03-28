# 3TC-NAS-projet — Branche `v1`

Projet de lab réseau (MPLS / L3VPN, topologie PE–P–CE) : description déclarative dans un fichier **Intent JSON**, génération de configurations **Cisco IOS** (c7200, IOS 15.2) pour **GNS3**, et outil optionnel pour pousser ces configs vers les *startup-config* Dynamips.

## Prérequis

- **Python 3** (stdlib uniquement pour les scripts fournis)
- **GNS3** avec routeurs Dynamips dont les **noms de nœuds** correspondent aux clés du lab (ex. `PE1`, `P1`, `CE1-1`, …)

## Arborescence utile

| Élément | Rôle |
|--------|------|
| `script/script_intent_to_configs.py` | Génère les `*.cfg` depuis un intent |
| `script/sync_gns3_startup_configs.py` | Copie le dernier run `Configs-*` vers le projet GNS3 |
| `script/Intent.json` | Intent de référence (à adapter) |
| `script/examples/` | Exemples (`Intent_full_mesh.json`, `Intent_dual_rr_multi_area.json`) |
| `script/migrate_intent.py` | Migration d’un intent ancien vers le schéma v4 |
| `docs/intent/` | Documentation détaillée du format Intent (v4.0) |
| `gns3/` | Emplacement prévu pour le projet GNS3 (voir `.gitignore`) |

Les sorties sont écrites sous `script/Configs/Configs-YYYYMMDD-HHMMSS/` : un fichier `.cfg` par routeur, plus une copie de l’intent utilisé.

## Démarrage rapide

À la racine du dépôt :

```bash
python script/script_intent_to_configs.py script/Intent.json
```

Variantes d’exemple :

```bash
python script/script_intent_to_configs.py script/examples/Intent_full_mesh.json
python script/script_intent_to_configs.py script/examples/Intent_dual_rr_multi_area.json
```

Migration vers un intent v4 lisible (sortie sur stdout) :

```bash
python script/migrate_intent.py script/Intent.json > script/Intent.v4.json
```

## Synchronisation avec GNS3

1. Placer votre projet GNS3 sous `gns3/` (le dépôt ignore le contenu réel du dossier pour ne pas versionner les binaires/lab locaux ; un fichier placeholder peut rester).
2. Après génération, lancer la sync (chemins par défaut : `gns3/projet_gns3_1/projet_gns3_1.gns3` et `script/Configs`) :

```bash
python script/sync_gns3_startup_configs.py
```

Options utiles :

- `--dry-run` : affiche ce qui serait copié sans écrire
- `--gns3-file` / `--project-root` / `--configs-base` : si votre arborescence diffère
- `--no-strict` : ignorer les routeurs sans fichier source correspondant

## Documentation Intent

- Vue d’ensemble et glossaire : [docs/intent/README.md](docs/intent/README.md)
- Schéma des champs : [docs/intent/schema.md](docs/intent/schema.md)
- Exemples et recettes : [docs/intent/examples.md](docs/intent/examples.md)
- FAQ : [docs/intent/faq.md](docs/intent/faq.md)

## Branche `v1`

Cette branche regroupe le flux **intent → configurations IOS** et les scripts d’intégration GNS3. Les dossiers datés sous `Configs/` peuvent être volumineux ou absents du dépôt selon l’historique Git ; régénérez-les avec `script_intent_to_configs.py` à partir d’un intent du dossier `script/examples/` ou de votre propre `Intent.json`.
