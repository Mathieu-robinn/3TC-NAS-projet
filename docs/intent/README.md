# Intent.json (v4.0) — Documentation

Cette documentation décrit le format du fichier `Intent.json` utilisé par le générateur `script/script_intent_to_configs.py` pour produire des configurations Cisco IOS (c7200, IOS 15.2).

## Sommaire

- [Quickstart](#quickstart)
- [Fichiers importants](#fichiers-importants)
- [Glossaire](#glossaire)
- [Aller plus loin](#aller-plus-loin)

## Quickstart

Depuis la racine du dépôt:

```bash
python script/script_intent_to_configs.py script/Intent.json
```

Le script crée un répertoire `script/Configs/Configs-YYYYMMDD-HHMMSS/` contenant:
- les fichiers `*.cfg` générés (PE/P/CE)
- une copie de l’intent utilisé (backup)

Tester les variantes incluses:

```bash
python script/script_intent_to_configs.py script/examples/Intent_full_mesh.json
python script/script_intent_to_configs.py script/examples/Intent_dual_rr_multi_area.json
```

## Fichiers importants

- **Intent principal**: `script/Intent.json`
- **Générateur**: `script/script_intent_to_configs.py`
- **Exemples**:
  - `script/examples/Intent_full_mesh.json`
  - `script/examples/Intent_dual_rr_multi_area.json`
- **Migration (ancien intent → v4.0)**: `script/migrate_intent.py`

## Glossaire

- **CE**: Customer Edge (routeur client, eBGP vers le PE).
- **PE**: Provider Edge (routeur opérateur côté client; VRF + MP-BGP vpnv4).
- **P**: Provider (routeur core MPLS, pas de VRF).
- **VRF**: Virtual Routing and Forwarding (table de routage par VPN).
- **RD**: Route Distinguisher (rend uniques les préfixes VRF dans BGP vpnv4; généré automatiquement).
- **RT**: Route Target (communautés BGP qui contrôlent import/export VPN).
- **RR**: Route Reflector (réduit le nombre de sessions iBGP nécessaires).

## Aller plus loin

- Référence complète du schéma: [schema.md](schema.md)
- Explication des exemples et “recettes”: [examples.md](examples.md)
- FAQ: [faq.md](faq.md)

