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
    # 3. Crash protection for Editor & NewEditor cleanup and set_note
    import aqt.editor as editor_mod
    editor_classes = []
    for cls_name in ["Editor", "NewEditor"]:
        c = getattr(editor_mod, cls_name, None)
        if c and c not in editor_classes:
            editor_classes.append(c)

    for ed_cls in editor_classes:
        orig_cleanup = ed_cls.cleanup
        def make_safe_cleanup(orig):
            def safe_editor_cleanup(self):
                if getattr(self, "_edn_cleaned_up", False):
                    return
                assistant = getattr(self, '_editor_assistant_web', None)
                if assistant:
                    try:
                        if not sip.isdeleted(assistant):
                            assistant.cleanup()
                    except Exception:
                        pass
                    self._editor_assistant_web = None

                try:
                    w = getattr(self, "widget", None)
                    if w is not None and sip.isdeleted(w):
                        self._edn_cleaned_up = True
                        return
                except Exception:
                    pass

                self._edn_cleaned_up = True
                try:
                    orig(self)
                except RuntimeError as e:
                    if "has been deleted" in str(e):
                        pass
                    else:
                        raise
            return safe_editor_cleanup

        ed_cls.cleanup = make_safe_cleanup(orig_cleanup)

        orig_set_note = getattr(ed_cls, "set_note", None)
        if orig_set_note:
            def make_safe_set_note(orig):
                def safe_set_note(self, note, hide=True, focusTo=None):
                    try:
                        w = getattr(self, "widget", None)
                        if w is not None and sip.isdeleted(w):
                            return
                    except Exception:
                        pass
                    try:
                        return orig(self, note, hide=hide, focusTo=focusTo)
                    except RuntimeError as e:
                        if "has been deleted" in str(e):
                            pass
                        else:
                            raise
                return safe_set_note
            ed_cls.set_note = make_safe_set_note(orig_set_note)
except Exception as e:
    print(f"EDN Lier les cartes: Failed to patch Editor safety: {e}")

try:
    # 3b. Crash protection for EditCurrent & NewEditCurrent (prevents QWidget has been deleted on close/cleanup)
    ec_classes = []
    for mod_name in ["aqt.editcurrent", "aqt.editcurrent_legacy"]:
        try:
            m = __import__(mod_name, fromlist=["EditCurrent", "NewEditCurrent"])
            for c_name in ["EditCurrent", "NewEditCurrent"]:
                c = getattr(m, c_name, None)
                if c and c not in ec_classes:
                    ec_classes.append(c)
        except Exception:
            pass

    for ec_cls in ec_classes:
        orig_ec_cleanup = ec_cls.cleanup
        def make_safe_ec_cleanup(orig):
            def safe_ec_cleanup(self):
                if getattr(self, "_edn_ec_cleaned_up", False):
                    return
                try:
                    if sip.isdeleted(self):
                        self._edn_ec_cleaned_up = True
                        return
                except Exception:
                    pass
                self._edn_ec_cleaned_up = True
                try:
                    orig(self)
                except RuntimeError as e:
                    if "has been deleted" in str(e):
                        pass
                    else:
                        raise
            return safe_ec_cleanup
        ec_cls.cleanup = make_safe_ec_cleanup(orig_ec_cleanup)

        orig_ec_close_event = getattr(ec_cls, "closeEvent", None)
        if orig_ec_close_event:
            def make_safe_ec_close_event(orig):
                def safe_ec_close_event(self, evt):
                    if getattr(self, "_edn_ec_cleaned_up", False):
                        if evt:
                            evt.accept()
                        return
                    try:
                        if sip.isdeleted(self):
                            if evt:
                                evt.accept()
                            return
                    except Exception:
                        pass
                    try:
                        orig(self, evt)
                    except RuntimeError as e:
                        if "has been deleted" in str(e):
                            if evt:
                                evt.accept()
                        else:
                            raise
                return safe_ec_close_event
            ec_cls.closeEvent = make_safe_ec_close_event(orig_ec_close_event)

        orig_ec_save_close = getattr(ec_cls, "_saveAndClose", None)
        if orig_ec_save_close:
            def make_safe_save_close(orig):
                def safe_save_and_close(self):
                    self._edn_ec_cleaned_up = True
                    try:
                        orig(self)
                    except RuntimeError as e:
                        if "has been deleted" in str(e):
                            pass
                        else:
                            raise
                return safe_save_and_close
            ec_cls._saveAndClose = make_safe_save_close(orig_ec_save_close)
except Exception as e:
    print(f"EDN Lier les cartes: Failed to patch EditCurrent safety: {e}")

try:
    # 4. Debounce Editor._check_and_update_duplicate_display_async to avoid concurrent QueryOp bursts
    orig_check_dup = getattr(Editor, "_check_and_update_duplicate_display_async", None)
    if orig_check_dup and not hasattr(Editor, "_edn_orig_check_dup"):
        from aqt.qt import QTimer
        Editor._edn_orig_check_dup = orig_check_dup

        def debounced_check_and_update_duplicate_display_async(self):
            timer = getattr(self, "_edn_dup_check_timer", None)
            if timer is not None:
                timer.stop()
            else:
                timer = QTimer(getattr(self, "parentWindow", None))
                timer.setSingleShot(True)
                self._edn_dup_check_timer = timer

            try:
                timer.timeout.disconnect()
            except Exception:
                pass

            def _run():
                if getattr(self, "note", None):
                    Editor._edn_orig_check_dup(self)

            timer.timeout.connect(_run)
            timer.start(250)

        Editor._check_and_update_duplicate_display_async = debounced_check_and_update_duplicate_display_async
except Exception as e:
    print(f"EDN Lier les cartes: Failed to patch Editor._check_and_update_duplicate_display_async: {e}")

