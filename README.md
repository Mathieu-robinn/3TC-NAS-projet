# 3TC NAS Project

Automatisation de la generation de configurations Cisco IOS pour un lab MPLS/BGP VPN, puis synchronisation vers un projet GNS3.

## Contenu du depot

- `script/script_intent_to_configs.py` : genere les fichiers `*.cfg` depuis un intent JSON.
- `script/sync_gns3_startup_configs.py` : copie les configs generees vers les `startup-config` attendus par GNS3 (Dynamips).
- `script/Configs/` : sorties de generation (`Configs-YYYYMMDD-HHMMSS/`).
- `gns3/` : projets GNS3 (ex: `projet_gns3_1`).
- `docs/` : documentation du sujet et du format d'intent.

## Prerequis

- Python 3.10+ (ou version recente compatible).
- Un projet GNS3 present dans `gns3/<nom_du_projet>/`.
- Un fichier intent JSON (exemple: `script/Intent.v4.json`).

## Workflow recommande

1. Generer les configs a partir de l'intent.
2. Synchroniser les configs vers GNS3.
3. Demarrer les routeurs dans GNS3.

## 1) Generer les configurations

Depuis la racine du projet:

```bash
python "script/script_intent_to_configs.py" "script/Intent.v4.json"
```

Le script cree un nouveau dossier:

- `script/Configs/Configs-YYYYMMDD-HHMMSS/`

Ce dossier contient:

- les fichiers `*.cfg` des routeurs
- une copie de l'intent utilise

## 2) Synchroniser vers GNS3

Le script de sync prend un **argument positionnel obligatoire** pour le dossier projet GNS3:

```bash
python "script/sync_gns3_startup_configs.py" gns3/projet_gns3_1
```

Par defaut, le script reconstruit automatiquement:

- `gns3-file` => `<project_root>/<nom_du_dossier>.gns3` (ex: `gns3/projet_gns3_1/projet_gns3_1.gns3`)

### Options utiles

- Dry-run (affiche sans ecrire):

```bash
python "script/sync_gns3_startup_configs.py" gns3/projet_gns3_1 --dry-run
```

- Continuer meme si certaines configs source manquent:

```bash
python "script/sync_gns3_startup_configs.py" gns3/projet_gns3_1 --no-strict
```

- Chemins personnalises (si besoin):

```bash
python "script/sync_gns3_startup_configs.py" \
  --gns3-file "gns3/projet_gns3_1/projet_gns3_1.gns3" \
  --project-root "gns3/projet_gns3_1" \
  --configs-base "script/Configs"
```

## Documentation complementaire

- Sujet / checklist: `docs/NAS_Project.md`
- Intent v4: `docs/intent/README.md`
- Schema intent: `docs/intent/schema.md`

## Notes

- Le mapping routeur `<name>.cfg` -> startup-config est deduit via le fichier `.gns3` (node_id + dynamips_id).
- Le script de sync utilise automatiquement le dernier dossier `Configs-YYYYMMDD-HHMMSS` dans `script/Configs`.
