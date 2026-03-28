# -*- coding: utf-8 -*-
"""
================================================================================
__main__.py — Point d'entrée ``python -m cisco_intent``
================================================================================

Quand tu exécutes ``python -m cisco_intent``, Python charge le package ``cisco_intent``
et exécute ce module comme script.

Rôle :
  Déléguer à ``cli.main()`` qui :
  - lit les arguments après le nom du module (sous-commandes : generate, diff, push, …)
  - renvoie un code entier (0 = succès) ; ``raise SystemExit(main())`` propage ce code
    au processus (utile pour les scripts et CI).

Pourquoi si peu de lignes ?
  Convention standard : toute la logique d'arguments et de routage vit dans ``cli.py``.
================================================================================
"""

from cisco_intent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
