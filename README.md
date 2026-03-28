# 3TC NAS Project

Automatisation de la generation de configurations Cisco IOS pour un lab MPLS/BGP VPN, puis synchronisation vers un projet GNS3.

## Contenu du depot

- `cisco_intent/` : package Python (generateur, diff modifs, push/sync GNS3).
- Point d'entree unique : `python -m cisco_intent <sous-commande> ...` (depuis la racine du depot, ou avec `PYTHONPATH` sur la racine).
- `intent/` : fichiers intent JSON d'exemple (`Intent.v4.json`, etc.).
- `Configs/` : sorties de generation (`Configs-YYYYMMDD-HHMMSS/`).
- `modifs/` : sorties du diff (`Modifs-YYYYMMDD-HHMMSS/`), cree au besoin.
- `gns3/` : projets GNS3 (ex: `projet/`).
- `docs/` : documentation du sujet et du format d'intent.

## Prerequis

- Python 3.10+ (ou version recente compatible).
- Dependances : `pip install -r requirements.txt` (ou `./dependencies.sh` / `dependencies.bat`).
- Un projet GNS3 present dans `gns3/<nom_du_projet>/`.
- Un fichier intent JSON (exemple: `intent/Intent.v4.json`).

## Workflow recommande

1. Generer les configs a partir de l'intent.
2. Synchroniser les configs vers GNS3.
3. Demarrer les routeurs dans GNS3.
4. (Option) Generer des **modifications a chaud** (diff) et les push en telnet.

## 1) Generer les configurations

Depuis la racine du projet:

```bash
python -m cisco_intent generate intent/Intent.v4.json
```

Le programme cree un nouveau dossier:

- `Configs/Configs-YYYYMMDD-HHMMSS/`

Ce dossier contient:

- les fichiers `*.cfg` des routeurs
- une copie de l'intent utilise

### Option `--push` (configs completes en telnet)

Apres generation reussie, tu peux enchainer le push telnet vers GNS3 sans passer par la sous-commande `push` :

```bash
python -m cisco_intent generate intent/Intent.v4.json \
  --push --gns3-project gns3/projet
```

Options utiles : `--push-only PE1,P2`, `--push-dry-run`, `--push-write-memory`, `--push-timeout`, `--push-workers` (voir `python -m cisco_intent generate -h`).

## 2) Synchroniser vers GNS3

La sous-commande `sync-startup` prend un **argument positionnel obligatoire** pour le dossier projet GNS3:

```bash
python -m cisco_intent sync-startup gns3/projet
```

Par defaut, le programme reconstruit automatiquement:

- `gns3-file` => `<project_root>/<nom_du_dossier>.gns3` (ex: `gns3/projet/projet.gns3`)

### Options utiles

- Dry-run (affiche sans ecrire):

```bash
python -m cisco_intent sync-startup gns3/projet --dry-run
```

- Continuer meme si certaines configs source manquent:

```bash
python -m cisco_intent sync-startup gns3/projet --no-strict
```

- Chemins personnalises (si besoin):

```bash
python -m cisco_intent sync-startup \
  --gns3-file "gns3/projet/projet.gns3" \
  --project-root "gns3/projet" \
  --configs-base "Configs"
```

## Documentation complementaire

- **Index et CLI** : [`docs/README.md`](docs/README.md)
- Sujet / checklist : [`docs/NAS_Project.md`](docs/NAS_Project.md)
- Intent v4 : [`docs/intent/README.md`](docs/intent/README.md) (schema, exemples, FAQ)

## Notes

- Le mapping routeur `<name>.cfg` -> startup-config est deduit via le fichier `.gns3` (node_id + dynamips_id).
- La commande `sync-startup` utilise automatiquement le dernier dossier `Configs-YYYYMMDD-HHMMSS` dans `Configs/` (racine du depot).

## 3) Modifications a chaud (diff -> Modifs-*)

La sous-commande `diff`:

- execute d'abord le generateur avec l'intent NEW
- compare les configs OLD vs NEW (par blocs IOS) et genere des **commandes de modification** dans `modifs/Modifs-YYYYMMDD-HHMMSS/`
- emet les suppressions (`no ...` / `default interface ...`) pour eviter le **config ghosting**
- n'emet jamais de commandes dangereuses (reload / write erase / etc.)

Exemple (depuis la racine du projet):

```bash
python -m cisco_intent diff \
  --old-intent "intent/Intent.v4.json" \
  --new-intent "intent/Intent.v4.json" \
  --only PE1
```

### Push des modifs en telnet (GNS3 consoles)

En une commande apres le diff (dossier `Modifs-*` produit puis push telnet) :

```bash
python -m cisco_intent diff \
  --new-intent intent/Intent.v4.NEW.example.json \
  --push --gns3-project gns3/projet
```

`--only` filtre a la fois les fichiers de modifs et les cibles du push. `--push-only` sert seulement au push si tu veux un sous-ensemble different (rare).

Avec `--dry-run` sur le diff, le push est ignore.

Sinon, commande separee (equivalent) :

```bash
python -m cisco_intent push gns3/projet "modifs/Modifs-YYYYMMDD-HHMMSS" --only PE1
```

### Autres sous-commandes

```bash
python -m cisco_intent --help
python -m cisco_intent generate -h
python -m cisco_intent diff -h
python -m cisco_intent push -h
python -m cisco_intent sync-startup -h
```
