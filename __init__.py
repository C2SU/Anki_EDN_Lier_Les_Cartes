"""
Anki EDN - Lier les Cartes
Recherche et liaison rapide de cartes pour Anki.

Intègre les fonctionnalités de :
- Link Cards (restored)
- Open Multiple Windows (multi_window.py)
- Menu partagé EDN (edn_menu)
"""
from aqt import gui_hooks, utils

# --- Fallback for missing edn_menu ---
try:
    from .edn_menu import register_module, register_action, get_edn_menu
    MENU_AVAILABLE = True
except ImportError:
    MENU_AVAILABLE = False
    def register_module(*args, **kwargs): return False
    def register_action(*args, **kwargs): pass
    def get_edn_menu(*args, **kwargs): return None

from . import multi_window  # Enable multi-window support

def init_addon():
    if not MENU_AVAILABLE:
        utils.showInfo(
            "Anki EDN - Lier les Cartes :\n\n"
            "Le module 'edn_menu' est manquant ou corrompu.\n"
            "L'addon fonctionnera en mode dégradé (sans menu EDN).\n"
            "Veuillez réinstaller l'addon pour corriger ce problème."
        )

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
