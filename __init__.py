"""
Anki EDN - Lier les Cartes
Recherche et liaison rapide de cartes pour Anki.

Intègre les fonctionnalités de :
- Link Cards (restored)
- Open Multiple Windows (multi_window.py)
- Menu partagé EDN (edn_menu)
"""
from aqt import gui_hooks, utils
from aqt.qt import QWebEngineSettings, QWebEngineProfile

# Activer l'inspecteur d'élément (DevTools)
try:
    QWebEngineProfile.defaultProfile().settings().setAttribute(QWebEngineSettings.WebAttribute.DeveloperExtrasEnabled, True)
except Exception:
    pass

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


# --- Crash Protection for Theme/Style Changes ---
# This fixes the crash: "RuntimeError: wrapped C/C++ object of type AnkiWebView has been deleted"
# when changing theme (light/dark mode) or body classes update if some AnkiWebView instances
# (e.g. the editor assistant panel) were destroyed by Qt/C++ but their hooks were not removed.
try:
    from aqt.qt import sip
except ImportError:
    try:
        import PyQt6.sip as sip
    except ImportError:
        try:
            import PyQt5.sip as sip
        except ImportError:
            import sip

from aqt.webview import AnkiWebView
from aqt.editor import Editor

try:
    # 1. Protect AnkiWebView theme_did_change hook
    original_on_theme_did_change = AnkiWebView.on_theme_did_change
    def safe_on_theme_did_change(self):
        if sip.isdeleted(self):
            try:
                from aqt import gui_hooks
                gui_hooks.theme_did_change.remove(self.on_theme_did_change)
                gui_hooks.body_classes_need_update.remove(self.on_body_classes_need_update)
            except Exception:
                pass
            return
        try:
            original_on_theme_did_change(self)
        except RuntimeError as e:
            if "deleted" in str(e):
                try:
                    from aqt import gui_hooks
                    gui_hooks.theme_did_change.remove(self.on_theme_did_change)
                    gui_hooks.body_classes_need_update.remove(self.on_body_classes_need_update)
                except Exception:
                    pass
            else:
                raise

    AnkiWebView.on_theme_did_change = safe_on_theme_did_change
except Exception as e:
    print(f"EDN Lier les cartes: Failed to patch AnkiWebView.on_theme_did_change: {e}")

try:
    # 2. Protect AnkiWebView body_classes_need_update hook
    original_on_body_classes_need_update = AnkiWebView.on_body_classes_need_update
    def safe_on_body_classes_need_update(self):
        if sip.isdeleted(self):
            try:
                from aqt import gui_hooks
                gui_hooks.theme_did_change.remove(self.on_theme_did_change)
                gui_hooks.body_classes_need_update.remove(self.on_body_classes_need_update)
            except Exception:
                pass
            return
        try:
            original_on_body_classes_need_update(self)
        except RuntimeError as e:
            if "deleted" in str(e):
                try:
                    from aqt import gui_hooks
                    gui_hooks.theme_did_change.remove(self.on_theme_did_change)
                    gui_hooks.body_classes_need_update.remove(self.on_body_classes_need_update)
                except Exception:
                    pass
            else:
                raise

    AnkiWebView.on_body_classes_need_update = safe_on_body_classes_need_update
except Exception as e:
    print(f"EDN Lier les cartes: Failed to patch AnkiWebView.on_body_classes_need_update: {e}")

try:
    # 3. Clean up custom assistant webview in Editor.cleanup
    original_editor_cleanup = Editor.cleanup
    def safe_editor_cleanup(self):
        assistant = getattr(self, '_editor_assistant_web', None)
        if assistant:
            try:
                if not sip.isdeleted(assistant):
                    assistant.cleanup()
            except Exception:
                pass
            self._editor_assistant_web = None
        original_editor_cleanup(self)

    Editor.cleanup = safe_editor_cleanup
except Exception as e:
    print(f"EDN Lier les cartes: Failed to patch Editor.cleanup: {e}")

