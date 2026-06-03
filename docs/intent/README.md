# Documentation Intent (v4.0)

Point d’entrée pour le format **Intent.json** et son utilisation avec **`cisco_intent`**.

- **[Schéma des champs](schema.md)** — référence des clés supportées par le générateur  
- **[Exemples et recettes](examples.md)** — variantes BGP/OSPF/MPLS/LAN/**MPLS-TE (RSVP)**  
- **[FAQ](faq.md)** — questions fréquentes (TE à chaud, autoroute, intent dans `live/`)  

Pour la **CLI**, les chemins du dépôt (`configs/<name>/`), les flux (`generate`, `update`, `push`, `sync-startup`, `reset`), voir **[`docs/README.md`](../README.md)**.

Module Python dédié au TE : [`cisco_intent/te.py`](../../cisco_intent/te.py) (validation, résolution nœud → loopback).
