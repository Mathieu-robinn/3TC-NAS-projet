# 3TC NAS Project

Automatisation de la generation de configurations Cisco IOS pour un lab MPLS/BGP VPN, puis synchronisation vers un projet GNS3.

## Contenu du depot

- `cisco_intent/` : package Python (generateur, update modifs, push/sync GNS3).
- Point d'entree unique : `python -m cisco_intent <sous-commande> ...` (depuis la racine du depot, ou avec `PYTHONPATH` sur la racine).
- `intent/` : fichiers intent JSON d'exemple (`Intent.v4.json`, etc.).
- `configs/` (tout en minuscules) regroupe :
  - `configs/live/` : référence sur disque (au moins un `*.cfg`) ; rempli au premier `generate` sans push, ou **toujours** avec `generate --push` ; mis à jour depuis `staging/` après `update --push` réussi ou après un **`push` manuel** (voir ci-dessous) ; OLD par défaut de `update`
  - `configs/staging/` : `generate` **sans** `--push` lorsque `live/` contient déjà des `*.cfg` ; aussi jeu NEW de `update`. **Vidé** après copie vers `live/` (`update --push` réussi, ou `push` manuel depuis un dossier autre que `live/` si `staging/` avait des `*.cfg`)
  - `configs/scratch_old/` : baseline temporaire si `update --old-intent`
  - `configs/backup/modifs/Modifs-<timestamp>.zip` : historique des modifs incrémentales produites par `update` (seul stockage persistant des snippets ; pas de dossier dédié sous `configs/`)
  - `configs/backup/full_configs/Configs-<timestamp>.zip` : snapshot zip a chaque generation
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
4. (Option) Generer des **modifications a chaud** (`update`) et les push en telnet.

## 1) Generer les configurations

Depuis la racine du projet:

```bash
python -m cisco_intent generate intent/Intent.v4.json
```

- Si `configs/live/` est **vide** (aucun `*.cfg`) : écriture dans **`configs/live/`**.
- Si `live/` contient déjà des `*.cfg` : écriture dans **`configs/staging/`** (un message l’indique sur stderr).

Contenu : fichiers `*.cfg` + copie de l’intent.

### Option `--push` (configs completes en telnet)

Avec `--push`, la génération va **toujours** dans `configs/live/`, puis telnet. Après succès, `live` est déjà à jour.

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

- Chemins personnalises : par defaut les `.cfg` sont lus depuis `configs/live/`. Pour un dossier extrait d’une archive ou `configs/staging`, par ex. :

```bash
python -m cisco_intent sync-startup \
  --gns3-file "gns3/projet/projet.gns3" \
  --project-root "gns3/projet" \
  --configs-dir "configs/staging"
```

## Documentation complementaire

- **Index et CLI** : [`docs/README.md`](docs/README.md)
- Sujet / checklist : [`docs/NAS_Project.md`](docs/NAS_Project.md)
- Intent v4 : [`docs/intent/README.md`](docs/intent/README.md) (schema, exemples, FAQ)

## Notes

- Le mapping routeur `<name>.cfg` -> startup-config est deduit via le fichier `.gns3` (node_id + dynamips_id).
- La commande `sync-startup` utilise par defaut `configs/live/`. Utilise `--configs-dir` pour une autre source.
- **`push` manuel** : si le dossier poussé **n’est pas** `configs/live/` (ex. dossier obtenu en dézipant un `backup/modifs/*.zip`, ou `configs/staging`) et que **`configs/staging/`** contient des `*.cfg`, après push réussi copie `staging` → `live` puis vide `staging`.

## 3) Modifications a chaud (`update` → backup/modifs)

La sous-commande `update` :

- OLD par defaut : `configs/live/` (sinon `--old-configs-dir` ou `--old-intent`)
- execute le generateur avec l'intent NEW (sortie dans `configs/staging/`, sans ecraser `live/` avant le push reussi)
- compare OLD vs NEW (par blocs IOS) et enregistre les **commandes de modification** dans `configs/backup/modifs/Modifs-<timestamp>.zip` (répertoire temporaire le temps du run, supprimé ensuite ; `update --push` lit ce dossier avant destruction)
- apres `update --push` reussi, `configs/live/` est aligne sur le run NEW (configs completes), pas sur le dossier modifs
- emet les suppressions (`no ...` / `default interface ...`) pour eviter le **config ghosting**
- n'emet jamais de commandes dangereuses (reload / write erase / etc.)

Exemple (depuis la racine du projet):

```bash
python -m cisco_intent update \
  --old-intent "intent/Intent.v4.json" \
  --new-intent "intent/Intent.v4.json" \
  --only PE1
```

### Push des modifs en telnet (GNS3 consoles)

En une commande : zip `backup/modifs/` puis push telnet depuis le répertoire temporaire (transparent) :

```bash
python -m cisco_intent update \
  --new-intent intent/Intent.v4.NEW.example.json \
  --push --gns3-project gns3/projet
```

`--only` filtre a la fois les fichiers de modifs et les cibles du push. `--push-only` sert seulement au push si tu veux un sous-ensemble different (rare).

Avec `--dry-run` sur `update`, le push est ignore.

Push **ultérieur** (sans refaire `update`) : dézipper l’archive voulue dans un dossier, puis :

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
```
