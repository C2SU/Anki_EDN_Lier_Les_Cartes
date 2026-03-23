"""
Anki EDN - Lier les Cartes
Recherche et liaison rapide de cartes pour Anki.

Intègre les fonctionnalités de :
- Link Cards (restored)
- Open Multiple Windows (multi_window.py)
- Menu partagé EDN (edn_menu)
"""
from aqt import gui_hooks
from .edn_menu import register_module, register_action, get_edn_menu
from . import multi_window  # Enable multi-window support

def init_addon():
    # Créer le menu EDN
    get_edn_menu()
    
    # Enregistrer et initialiser le module
    if register_module(
        module_id="linked_cards",
        name="Lier les Cartes",
        description="Recherche et liaison rapide de cartes",
        default_enabled=True
    ):
        from .linked_cards import init_linked_cards
        init_linked_cards()

gui_hooks.main_window_did_init.append(init_addon)
