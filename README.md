# 3TC NAS Project

Automatisation de la generation de configurations Cisco IOS pour un lab MPLS/BGP VPN, puis synchronisation vers un projet GNS3.

## Contenu du depot

- `cisco_intent/` : package Python (generateur, update modifs, push/sync GNS3, reset startup).
- Point d'entree unique : `python -m cisco_intent <sous-commande> ...` (depuis la racine du depot, ou avec `PYTHONPATH` sur la racine).
- `intent/` : fichiers intent JSON d'exemple (ex. `intent/topologie1/Intent_*.json`).
- `configs/` regroupe :
  - **`configs/<name>/`** — une arborescence **par topologie** ; `<name>` est le champ racine **`name`** de l'intent JSON (identifiant stable, ex. `topologie1`, `topology_1`).
    - `configs/<name>/live/` : reference sur disque (`*.cfg` + copie d'intent) ; rempli au premier `generate` sans push (si `live/` vide), ou **toujours** avec `generate --push` ; mis à jour depuis `staging/` apres `update --push` reussi ou apres un **`push` manuel** ; OLD par defaut de `update` pour le meme `name`.
    - `configs/<name>/staging/` : `generate` **sans** `--push` lorsque `live/` contient deja des `*.cfg` ; aussi jeu NEW de `update`. **Vide** apres copie vers `live/`.
    - `configs/<name>/scratch_old/` : baseline temporaire si `update --old-intent` (scratch lie a la topologie de l'intent OLD).
    - `configs/<name>/backup/modifs/Modifs-<timestamp>.zip` : historique des modifs incrementales (`update`).
    - `configs/<name>/backup/full_configs/Configs-<timestamp>.zip` : snapshot zip a chaque generation.
  - **`configs/default/`** : fichiers de reference hors topologie (ex. **`default-conf-C7200.txt`** utilise par `reset`).
- `gns3/` : projets GNS3 (ex: `projet/`).
- `docs/` : documentation du sujet et du format d'intent.

## Prerequis

- Python 3.10+ (ou version recente compatible).
- Dependances : `pip install -r requirements.txt` (ou `./dependencies.sh` / `dependencies.bat`).
- Un projet GNS3 present dans `gns3/<nom_du_projet>/`.
- Un fichier intent JSON avec le champ racine **`name`** (exemple : `intent/topologie1/Intent_isis_rr_redunt.json`).

## Workflow recommande

1. Generer les configs a partir de l'intent.
2. Synchroniser les configs vers GNS3 (`sync-startup` ou `push`).
3. Demarrer les routeurs dans GNS3.
4. (Option) Generer des **modifications a chaud** (`update`) et les push en telnet.
5. (Option) **Reset** des startup-config Dynamips vers la config par defaut C7200 (`reset`).

## 1) Generer les configurations

Depuis la racine du projet (remplacer par ton fichier intent) :

```bash
python -m cisco_intent generate intent/topologie1/Intent_isis_rr_redunt.json
```

- Le repertoire cible est **`configs/<name>/`** ou `<name>` vient de l'intent.
- Si **`configs/<name>/live/`** est **vide** (aucun `*.cfg`) : ecriture dans **`live/`**.
- Si `live/` contient deja des `*.cfg` : ecriture dans **`staging/`** (message sur stderr).

Contenu : fichiers `*.cfg` + copie de l'intent.

### Option `--push` (configs completes en telnet)

Avec `--push`, la generation va **toujours** dans `configs/<name>/live/`, puis telnet. Apres succes, `live` est deja a jour.

```bash
python -m cisco_intent generate intent/topologie1/Intent_isis_rr_redunt.json \
  --push --gns3-project gns3/projet
```

Options utiles : `--push-only PE1,P2`, `--push-dry-run`, `--push-write-memory`, `--push-timeout`, `--push-workers` (voir `python -m cisco_intent generate -h`).

## 2) Synchroniser vers GNS3 (startup sur disque)

La sous-commande **`sync-startup`** copie les `<hostname>.cfg` vers les fichiers startup Dynamips. Il faut indiquer la source soit avec **`--topology <name>`** (dossier = `configs/<name>/live/`), soit avec **`--configs-dir`** (chemin explicite).

```bash
python -m cisco_intent sync-startup gns3/projet --topology topologie1
```

Par defaut, le programme deduit le fichier projet :

- `<project_root>/<nom_du_dossier>.gns3` (ex: `gns3/projet/projet.gns3`)

### Options utiles

- Dry-run :

```bash
python -m cisco_intent sync-startup gns3/projet --topology topologie1 --dry-run
```

- Continuer meme si certaines configs source manquent :

```bash
python -m cisco_intent sync-startup gns3/projet --topology topologie1 --no-strict
```

- Dossier source explicite (archive dezippee, `staging`, etc.) :

```bash
python -m cisco_intent sync-startup \
  --gns3-file "gns3/projet/projet.gns3" \
  --project-root "gns3/projet" \
  --configs-dir "configs/topologie1/staging"
```

## 2bis) Reset startup-config (config par defaut C7200)

La sous-commande **`reset`** copie le fichier **`configs/default/default-conf-C7200.txt`** vers le startup-config **de chaque** noeud Dynamips du projet (meme arborescence que `sync-startup`, mais un seul fichier source pour tous les routeurs). Utile pour revenir a une base IOS avant un nouveau lab.

```bash
python -m cisco_intent reset gns3/projet
```

Options : `--gns3-file`, `--template <fichier>` (autre source que le defaut), `--dry-run`. Voir `python -m cisco_intent reset -h`.

Redemarrer les routeurs dans GNS3 pour charger la nouvelle startup.

## Documentation complementaire

- **Index et CLI** : [`docs/README.md`](docs/README.md)
- Sujet / checklist : [`docs/NAS_Project.md`](docs/NAS_Project.md)
- Intent v4 : [`docs/intent/README.md`](docs/intent/README.md) (schema, exemples, FAQ)

## Notes

- Le mapping routeur `<name>.cfg` -> startup-config est deduit via le fichier `.gns3` (node_id + dynamips_id).
- **`push` manuel** : si le dossier pousse **n'est pas** `configs/<topo>/live/` et que **`configs/<topo>/staging/`** contient des `*.cfg`, apres push reussi le code peut copier `staging` -> `live` puis vider `staging` (topologie deduite de l'intent dans le dossier ou du chemin).

## 3) Modifications a chaud (`update` → backup/modifs)

La sous-commande `update` :

- OLD par defaut : **`configs/<name>/live/`** ou `<name>` est le champ **`name`** de l'intent **NEW** (l'intent copie dans le live doit avoir le meme `name`, sinon erreur ; ou utiliser `--old-configs-dir`).
- Execute le generateur avec l'intent NEW (sortie dans **`configs/<name>/staging/`**).
- Compare OLD vs NEW et enregistre les **commandes de modification** dans **`configs/<name>/backup/modifs/Modifs-<timestamp>.zip`**
- Apres `update --push` reussi, **`configs/<name>/live/`** est aligne sur le run NEW (configs completes).
- Avec **`--old-intent`** seul : baseline OLD generee dans **`configs/<name_old>/scratch_old/`** (nom lu dans l'intent OLD).

Exemple :

```bash
python -m cisco_intent update \
  --old-intent "intent/topologie1/Intent_isis_rr_redunt.json" \
  --new-intent "intent/topologie1/Intent_isis_rr_redunt.json" \
  --only PE1
```

### Push des modifs en telnet (GNS3 consoles)

```bash
python -m cisco_intent update \
  --new-intent intent/topologie1/Intent_isis_rr_redunt.json \
  --push --gns3-project gns3/projet
```

Avec `--dry-run` sur `update`, le push est ignore.

Push **ultérieur** : dezipper l'archive voulue, puis :

```bash
python -m cisco_intent push gns3/projet "chemin/vers/dossier_dezip" --only PE1
```

### Autres sous-commandes

```bash
python -m cisco_intent --help
python -m cisco_intent generate -h
python -m cisco_intent update -h
python -m cisco_intent push -h
python -m cisco_intent sync-startup -h
python -m cisco_intent reset -h
```
