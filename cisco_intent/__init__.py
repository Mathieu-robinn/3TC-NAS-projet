# -*- coding: utf-8 -*-
"""
================================================================================
cisco_intent — Package racine
================================================================================

Ce package regroupe la chaîne « intent JSON → configuration Cisco IOS → GNS3 ».

Pour un débutant :
  - L'usage courant est la ligne de commande : ``python -m cisco_intent`` (voir
    ``__main__.py`` puis ``cli.py``).
  - ``__version__`` sert au suivi de version si tu documentes un lab ou publies le package.

Ordre de lecture conseillé pour comprendre le flux complet :
  1. paths       — configs/<topology>/ (live, staging, backup), etc.
  2. intent      — charger, normaliser, valider le JSON
  3. allocation  — calculer les adresses IP (loopbacks, liens, clients, LAN)
  4. generator   — produire les fichiers ``<nom>.cfg``
  5. config_update — mise à jour incrémentale : comparer deux runs → modifs (hot-push)
  6. gns3_push   — envoyer les lignes IOS via telnet (consoles GNS3)
  7. gns3_sync   — copier des .cfg vers les startup-config Dynamips sur disque

Pour ajouter une fonctionnalité : commence souvent par ``intent`` (schéma + validation),
puis ``allocation`` ou ``generator``, enfin une sous-commande dans ``cli.py`` si besoin.
================================================================================
"""

__version__ = "1.0.0"
