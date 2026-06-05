import json
import re
from aqt import mw
from aqt.qt import *
from aqt import gui_hooks

try:
    from aqt.operations import QueryOp
    HAS_QUERY_OP = True
except ImportError:
    HAS_QUERY_OP = False

_assistant_css = """
:root {
  --incoming-color: #f59e0b;
  --reciprocal-color: #10b981;
  --outgoing-color: #3b82f6;
  --shadow-light: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03);
}
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

body {
  font-family: 'Inter', -apple-system, system-ui, sans-serif;
  font-size: 13px;
  margin: 0;
  padding: 12px 14px;
  background: var(--canvas, var(--window-bg));
  color: var(--fg);
  line-height: 1.4;
}

h3 {
  margin-top: 18px;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg);
  opacity: 0.85;
  border-bottom: 2px solid var(--border);
  padding-bottom: 6px;
}

.section {
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-item {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--canvas-elevated, var(--bg, white));
  box-shadow: var(--shadow-light);
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
  position: relative;
  overflow: hidden;
}

/* Color-coded left borders */
.card-item.incoming {
  border-left: 4px solid var(--incoming-color);
}
.card-item.reciprocal {
  border-left: 4px solid var(--reciprocal-color);
}
.card-item.outgoing {
  border-left: 4px solid var(--outgoing-color);
}

.card-item:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -2px rgba(0,0,0,0.04);
  background: var(--highlight-bg, #f3f4f6);
}

.card-item.active-card {
  background: var(--highlight-bg, #eff6ff) !important;
  border-color: var(--link, #3b82f6) !important;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4) !important;
  transform: scale(1.01);
}

.card-title {
  font-weight: 500;
  font-size: 12.5px;
  color: var(--fg);
  line-height: 1.35;
  word-break: break-word;
}

.card-nid-badge {
  display: inline-block;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 9.5px;
  background-color: rgba(0, 0, 0, 0.04);
  color: #888;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 4px;
  padding: 1.5px 5px;
  width: fit-content;
  transition: all 0.15s ease;
}

.card-item:hover .card-nid-badge {
  color: var(--fg);
  background-color: rgba(0, 0, 0, 0.08);
}

.card-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.btn-add {
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
  font-size: 11px;
  font-weight: 500;
  transition: all 0.15s ease;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.btn-add:hover {
  background: #2563eb;
  transform: translateY(-0.5px);
  box-shadow: 0 4px 6px -1px rgba(37, 99, 235, 0.2);
}

.card-item.incoming .btn-add {
  background: var(--incoming-color);
}
.card-item.incoming .btn-add:hover {
  background: #d97706;
}

.card-item.outgoing .btn-add {
  background: var(--outgoing-color);
}
.card-item.outgoing .btn-add:hover {
  background: #2563eb;
}

.empty {
  font-style: italic;
  color: var(--fg);
  opacity: 0.55;
  font-size: 12px;
  padding: 8px 12px;
  background: rgba(0,0,0,0.02);
  border: 1px dashed var(--border);
  border-radius: 6px;
  text-align: center;
}
"""

# --- État persistant du panneau (ouvert/fermé) ---
_ASSISTANT_STATE_KEY = "_edn_assistant_open"

def _addon_package():
    """Retourne le nom du package addon (dossier racine) pour la config."""
    try:
        return mw.addonManager.addonFromModule(__name__)
    except Exception:
        # Fallback : extraire le nom de base du module
        return __name__.split('.')[0]

def _get_assistant_should_open():
    """Retourne True si l'utilisateur avait laissé le panneau ouvert la dernière fois."""
    try:
        config = mw.addonManager.getConfig(_addon_package())
        if config:
            return bool(config.get("assistant_panel_open", False))
    except Exception:
        pass
    return False

def _save_assistant_state(is_open: bool):
    """Mémorise l'état ouvert/fermé dans la config addon."""
    try:
        pkg = _addon_package()
        config = mw.addonManager.getConfig(pkg) or {}
        config["assistant_panel_open"] = is_open
        mw.addonManager.writeConfig(pkg, config)
    except Exception as e:
        print("EDN assistant: Impossible de sauvegarder l'état panneau :", e)

def _is_widget_alive(w):
    if w is None:
        return False
    try:
        w.objectName()
        return True
    except RuntimeError:
        return False

def _is_editor_alive(editor):
    if editor is None:
        return False
    try:
        if hasattr(editor, 'web') and editor.web:
            editor.web.objectName()
        return True
    except RuntimeError:
        return False

def get_active_editor(context=None):
    from aqt import mw
    # If context is an Editor, return it directly
    from aqt.editor import Editor
    if isinstance(context, Editor):
        try:
            context.web.objectName()
            return context
        except RuntimeError:
            pass

    # If context is the assistant's AnkiWebView or editor's EditorWebView, find its editor
    if context:
        # Check active window first
        if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow'):
            win = mw.app.activeWindow()
            if hasattr(win, 'editor') and win.editor:
                try:
                    editor = win.editor
                    if editor.web == context or getattr(editor, '_edn_assistant_web', None) == context:
                        return editor
                except RuntimeError:
                    pass
        # Check _current_editor
        from .linked_cards import _current_editor
        if _current_editor:
            try:
                if _current_editor.web == context or getattr(_current_editor, '_edn_assistant_web', None) == context:
                    return _current_editor
            except RuntimeError:
                pass

    # Fallback to active window's editor
    if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow'):
        win = mw.app.activeWindow()
        if hasattr(win, 'editor') and win.editor:
            try:
                win.editor.web.objectName()
                return win.editor
            except RuntimeError:
                pass

    # Fallback to _current_editor
    from .linked_cards import _current_editor
    if _current_editor:
        try:
            _current_editor.web.objectName()
            return _current_editor
        except RuntimeError:
            pass

    return None

# --- Popup preview (imitant show_preview_popup du GUI) ---

_preview_popup = None  # QWidget popup global réutilisable
_esc_shortcut = None   # Raccourci Échap temporaire
_preview_history = []  # Historique pour navigation imbriquée
_current_preview_nid = None  # NID actuellement affiché dans l'aperçu
_preview_event_filter = None  # Event filter pour lifecycle du popup


class EdnPreviewEventFilter(QObject):
    """Filtre d'événements installé sur la fenêtre parente de l'éditeur.
    
    Masque automatiquement le popup de preview miroir quand :
    - La fenêtre parente perd le focus (Alt-Tab)
    - La fenêtre parente est fermée ou masquée
    """
    def eventFilter(self, obj, event):
        etype = event.type()
        if etype in (QEvent.Type.WindowDeactivate, QEvent.Type.Close, QEvent.Type.Hide):
            if etype == QEvent.Type.WindowDeactivate and _is_preview_popup_alive() and _preview_popup.isVisible():
                pos = QCursor.pos()
                geom = _preview_popup.geometry()
                if geom.adjusted(-15, -15, 15, 15).contains(pos):
                    return False
            _hide_card_preview_popup()
        return False

def _is_preview_popup_alive():
    global _preview_popup
    if _preview_popup is None:
        return False
    try:
        _preview_popup.objectName()
        return True
    except RuntimeError:
        _preview_popup = None
        return False

def _close_editor(editor):
    if not editor:
        return
    parent_win = None
    try:
        if hasattr(editor, 'parentWindow') and editor.parentWindow:
            parent_win = editor.parentWindow
        elif hasattr(editor, 'widget') and editor.widget:
            parent_win = editor.widget.window()
    except:
        pass
    if parent_win:
        from aqt.browser import Browser
        from aqt.main import AnkiQt
        if isinstance(parent_win, (Browser, AnkiQt)):
            # Ne pas fermer la fenêtre principale ou le navigateur !
            # Au lieu de ça, on redonne le focus à l'éditeur principal
            try:
                editor.web.setFocus()
            except:
                pass
            return
        try:
            parent_win.close()
        except Exception as e:
            print("Failed to close editor parent_win:", e)

def _handle_escape_from_shortcut(editor_window):
    _hide_card_preview_popup()
    if editor_window:
        try:
            editor_window.close()
        except Exception as e:
            print("Failed to close editor window on Escape shortcut:", e)

def _hide_card_preview_popup():
    """Masque la popup de preview de carte."""
    global _preview_popup, _esc_shortcut, _preview_event_filter
    if _esc_shortcut:
        try:
            _esc_shortcut.setEnabled(False)
            _esc_shortcut.deleteLater()
        except Exception:
            pass
        _esc_shortcut = None
    # Retirer le filtre de lifecycle
    if _preview_event_filter:
        try:
            parent = _preview_event_filter.parent()
            if parent:
                parent.removeEventFilter(_preview_event_filter)
            _preview_event_filter.deleteLater()
        except Exception:
            pass
        _preview_event_filter = None
    if _is_preview_popup_alive():
        try:
            _preview_popup.hide()
        except Exception:
            pass
    
    # Mettre à jour l'état dans l'assistant
    editor = get_active_editor()
    if editor:
        assistant = getattr(editor, '_edn_assistant_web', None)
        if _is_widget_alive(assistant):
            try:
                assistant.eval("window._ednPreviewPopupOpen = false;")
            except:
                pass

def check_hide_preview():
    global _preview_popup
    if not _is_preview_popup_alive() or not _preview_popup.isVisible():
        return
        
    pos = QCursor.pos()
    
    # 1. Check if mouse is over the preview popup itself (with 15px safe padding)
    geom = _preview_popup.geometry()
    safe_geom = geom.adjusted(-15, -15, 15, 15)
    if safe_geom.contains(pos):
        # Keep it open and re-check in 200ms
        QTimer.singleShot(200, check_hide_preview)
        return
        
    # 2. Check if mouse is over the assistant panel (the source trigger widget)
    editor = get_active_editor()
    if editor:
        assistant = getattr(editor, '_edn_assistant_web', None)
        if _is_widget_alive(assistant):
            try:
                # Map assistant geometry to global coordinates
                global_top_left = assistant.mapToGlobal(QPoint(0, 0))
                as_global_geom = QRect(global_top_left, assistant.size())
                as_safe_geom = as_global_geom.adjusted(-15, -15, 15, 15)
                if as_safe_geom.contains(pos):
                    # Keep it open and re-check in 200ms
                    QTimer.singleShot(200, check_hide_preview)
                    return
            except:
                pass
                
    # If mouse is neither over the preview popup nor the assistant panel, hide it!
    _hide_card_preview_popup()

def handle_preview_popup_message(message):
    if message.startswith("preview_nested:"):
        nid = message[len("preview_nested:"):]
        editor = get_active_editor()
        anchor = None
        if editor:
            anchor = getattr(editor, '_edn_assistant_web', None)
        _show_card_preview_popup(nid, parent_widget=anchor, is_nested=True)
    elif message == "preview_back":
        global _preview_history, _current_preview_nid
        if _preview_history:
            prev_nid = _preview_history.pop()
            _current_preview_nid = prev_nid
            editor = get_active_editor()
            anchor = None
            if editor:
                anchor = getattr(editor, '_edn_assistant_web', None)
            _show_card_preview_popup(prev_nid, parent_widget=anchor, is_nested=True)
            # Activer et redonner le focus
            if _is_preview_popup_alive():
                _preview_popup.activateWindow()
                _preview_popup._edn_web.setFocus()
        else:
            _hide_card_preview_popup()
    elif message.startswith("preview_resize:"):
        try:
            h = int(float(message[len("preview_resize:"):]))
            if _is_preview_popup_alive():
                _adjust_height(_preview_popup, h)
        except Exception as e:
            print("Error resizing preview:", e)
    elif message == "escape":
        _hide_card_preview_popup()
        editor = get_active_editor()
        _close_editor(editor)
    elif message.startswith("preview_key_to_assistant:"):
        try:
            key = message[len("preview_key_to_assistant:"):]
            editor = get_active_editor()
            if editor:
                assistant = getattr(editor, '_edn_assistant_web', None)
                if _is_widget_alive(assistant):
                    assistant.eval(f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}', bubbles: true}}));")
        except Exception as e:
            print("Error forwarding key to assistant:", e)
    elif message.startswith("open_browser:"):
        try:
            nid = message[len("open_browser:"):]
            from aqt import dialogs
            browser = dialogs.open("Browser", mw)
            browser.search_for(f"nid:{nid}")
        except Exception as e:
            print("Erreur open_browser from preview:", e)

def _create_preview_popup():
    """Crée le popup Qt de preview (singleton réutilisable, sans barre de titre)."""
    global _preview_popup
    flags = Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
    dlg = QWidget(None, flags)
    dlg.setObjectName("EdnAssistantPreviewPopup")
    dlg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    dlg.setMinimumSize(500, 100)
    dlg.setMaximumSize(800, 1400)
    dlg.setStyleSheet("#EdnAssistantPreviewPopup { border: 1px solid #aaa; background: white; border-radius: 8px; }")

    v = QVBoxLayout(dlg)
    v.setContentsMargins(2, 2, 2, 2)
    v.setSpacing(0)

    from aqt.webview import AnkiWebView
    web = AnkiWebView(parent=dlg)
    v.addWidget(web)

    dlg._edn_web = web
    _preview_popup = dlg


def _show_card_preview_popup(nid_str, parent_widget=None, is_nested=False, y_offset=0):
    """Ouvre un popup Qt AnkiWebView avec la preview de la carte (même rendu que GUI 'Voir')."""
    global _preview_popup, _esc_shortcut, _preview_history, _current_preview_nid
    import re as re2

    # Gérer l'historique
    if not is_nested:
        _preview_history = []
        _current_preview_nid = nid_str
    else:
        if _current_preview_nid and _current_preview_nid != nid_str:
            if not _preview_history or _preview_history[-1] != _current_preview_nid:
                _preview_history.append(_current_preview_nid)
        _current_preview_nid = nid_str

    try:
        from .edn_menu import get_shortcut
        _kbd_next = get_shortcut("linked_cards_kbd_open", "n")
        _kbd_prev = get_shortcut("linked_cards_kbd_preview", "p")
        _kbd_open = get_shortcut("linked_cards_kbd_navigate", "r")
        _kbd_back = get_shortcut("linked_cards_kbd_back", "z")
    except Exception:
        _kbd_next, _kbd_prev, _kbd_open, _kbd_back = "n", "p", "r", "z"

    _shortcuts_script = (
        f"<script>window._ednMiroirKeys = {{"
        f"next: '{_kbd_next}', prev: '{_kbd_prev}', open: '{_kbd_open}', back: '{_kbd_back}'"
        f"}};</script>"
    )

    try:
        note = mw.col.get_note(int(nid_str))
        cards = note.cards()
        if not cards:
            return
        card = cards[0]
        rendered = card.answer()

        # Nettoyage identique au GUI show_preview_popup
        isolated = re2.sub(r'(?<!\w)id=(["\'])(.*?)\1', r'id="dlg_preview_\2"', rendered)
        isolated = re2.sub(r'toggle\([\"\'](.*?)[\"\']\)', r"toggle('dlg_preview_\1')", isolated)
        isolated = re2.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', isolated, flags=re2.IGNORECASE)
        isolated = re2.sub(
            r"(?:<br>\s*)?<(span|div|p)\b[^>]*id=[^>]*fsrs[^>]*>.*?</\1>",
            "", isolated, flags=re2.IGNORECASE | re2.DOTALL)
        for sec in ["sourcesMegaContainer", "commentsMegaContainer", "tagsMegaContainer",
                    "erreursFaitesMegaContainer", "mnemonicsMegaContainer", "infoSupplementairesMegaContainer"]:
            isolated = re2.sub(
                rf'<div[^>]*id=["\']dlg_preview_{re2.escape(sec)}["\'][^>]*>.*?</div>',
                '', isolated, flags=re2.IGNORECASE | re2.DOTALL)
        isolated = re2.sub(
            r'(<div[^>]*class=["\'][^"\']*(section)[^"\']*["\'][^>]*style=["\'])display:\s*none;?',
            r'\1display: flex !important;', isolated, flags=re2.IGNORECASE)

        # Compresser et nettoyer les <br> orphelins ou inutiles dans cartesLiees
        def _clean_cartes_liees(match):
            inner = match.group(2)
            # Supprimer les br consécutifs ou séparés par des espaces
            inner = re2.sub(r'(?:\s*<br\s*/?>\s*)+', '<br>', inner, flags=re2.IGNORECASE)
            # Supprimer les br au tout début
            inner = re2.sub(r'^\s*(?:<br\s*/?>\s*)+', '', inner, flags=re2.IGNORECASE)
            # Supprimer les br et espaces insécables à la toute fin
            inner = re2.sub(r'(?:<br\s*/?>\s*|&nbsp;\s*)+$', '', inner, flags=re2.IGNORECASE)
            return f"{match.group(1)}{inner}</div>"
        isolated = re2.sub(r'(<div[^>]*class=["\'][^"\']*cartesLiees[^"\']*["\'][^>]*>)(.*?)</div>', _clean_cartes_liees, isolated, flags=re2.IGNORECASE | re2.DOTALL)

        # Créer ou réutiliser le popup
        if not _is_preview_popup_alive():
            _create_preview_popup()

        dlg = _preview_popup
        web = dlg._edn_web

        # Conserver le popup comme une fenêtre flottante autonome (pas de setParent)
        # pour éviter les restrictions de géométrie et de dimensionnement imposées par l'éditeur.
        pass

        # Installer le raccourci Échap temporaire sur la fenêtre de l'éditeur
        if _is_widget_alive(parent_widget):
            try:
                if _esc_shortcut:
                    _esc_shortcut.setEnabled(False)
                    _esc_shortcut.deleteLater()
                    _esc_shortcut = None
                editor_window = parent_widget.window()
                if _is_widget_alive(editor_window):
                    _esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), editor_window)
                    _esc_shortcut.activated.connect(lambda: _handle_escape_from_shortcut(editor_window))
                    _esc_shortcut.setEnabled(True)
            except Exception as se:
                print("Error setting escape shortcut:", se)

        # Installer le filtre de lifecycle pour masquer le popup quand la fenêtre perd le focus
        global _preview_event_filter
        if _is_widget_alive(parent_widget):
            try:
                editor_window = parent_widget.window()
                if _is_widget_alive(editor_window):
                    # Retirer l'ancien filtre si existant
                    if _preview_event_filter:
                        try:
                            editor_window.removeEventFilter(_preview_event_filter)
                        except Exception:
                            pass
                        try:
                            parent_widget.removeEventFilter(_preview_event_filter)
                        except Exception:
                            pass
                    _preview_event_filter = EdnPreviewEventFilter(editor_window)
                    editor_window.installEventFilter(_preview_event_filter)
                    parent_widget.installEventFilter(_preview_event_filter)
            except Exception as lfe:
                print("Error setting lifecycle event filter:", lfe)

        # Positionner de façon intelligente
        if _is_widget_alive(parent_widget) and not is_nested:
            try:
                parent_global_pos = parent_widget.mapToGlobal(QPoint(0, 0))
                dlg_width = dlg.width() if dlg.width() > 100 else 550
                screen = QApplication.primaryScreen().availableGeometry()
                
                # Par défaut à droite du panneau assistant
                x = parent_global_pos.x() + parent_widget.width() + 10
                
                # Si le coin droit dépasse l'écran, décaler à gauche
                if x + dlg_width > screen.right():
                    x = parent_global_pos.x() - dlg_width - 10
                
                y = parent_global_pos.y() + y_offset
                # Ajustement si on sort de l'écran bas pour un espace de hauteur confortable de 800px
                if y + 800 > screen.bottom():
                    y = max(screen.top() + 10, screen.bottom() - 820)
                
                dlg.move(QPoint(x, y))
            except Exception as pe:
                print("Error positioning preview popup:", pe)

        addon_package = mw.addonManager.addonFromModule(__name__)
        css_link = f'<link rel="stylesheet" href="/_addons/{addon_package}/user_files/clickable_cards.css">'

        model_css = note.note_type().get('css', '')
        if model_css:
            css_link += f"<style>\n{model_css}\n</style>"

        scoped_css = """
        <style>
            html, body { margin: 0; font-size: 13px; overflow-x: hidden; overflow-y: auto !important; padding: 0 !important; }
            [id*='FSRS'], [id*='fsrs'], .fsrs-info { display: none !important; }
            hr { display: none !important; }
            .edn-nid { display: none !important; }
            .clickable_cards .edn-nid { display: inline !important; }
            .card > *:last-child { margin-bottom: 0 !important; padding-bottom: 0 !important; }
            .card { background: transparent !important; background-color: transparent !important; margin: 0 !important; padding: 4px 5px !important; font-size: 16px !important; line-height: 1.35 !important; max-width: 100% !important; text-align: left; }
            a { font-size: 0.85em !important; }
            .section { display: flex !important; margin: 1px 0 !important; padding: 0 !important; width: 100% !important; min-height: 0 !important; border-width: 1.5px !important; }
            div[class*='section'] { display: flex !important; }
            .items { margin-left: 8px !important; margin-right: 8px !important; padding: 1px 0 !important; min-height: 0 !important; white-space: normal !important; word-break: break-word !important; }
            .items ul, .items ol { padding-top: 2px !important; padding-bottom: 2px !important; margin-top: 0 !important; margin-bottom: 0 !important; padding-left: 18px !important; }
            .items li { padding-top: 0 !important; padding-bottom: 0 !important; margin-top: 0 !important; margin-bottom: 0 !important; }
            .items ul ul, .items ol ol, .items ul ol, .items ol ul { padding-top: 0 !important; padding-bottom: 0 !important; margin-top: 0 !important; margin-bottom: 0 !important; }
            .items > span ul, .items > span ol { padding-left: 18px !important; margin: 0 !important; padding-top: 0 !important; padding-bottom: 0 !important; }
            .items li > ul, .items li > ol { padding-left: 14px !important; margin: 0 !important; padding-top: 0 !important; padding-bottom: 0 !important; }
            .items > span { display: block !important; }
            .items.answer { padding: 1px 0 !important; }
            .bar { flex: 0 0 28px !important; width: 28px !important; min-height: 28px !important; margin: 0 !important; padding: 0 !important; background-size: 20px !important; border-right-width: 1px !important; }
            .barHider { display: none !important; }
            br { display: block !important; content: '' !important; margin: 1px 0 !important; }
            .clickable_cards { font-size: 13px !important; height: auto !important; line-height: 13px !important; padding: 3px 5px !important; margin: 3px !important; cursor: pointer; transition: all 0.2s ease; }
            .clickable_cards:hover { background-color: #3b82f6 !important; color: white !important; }
            .items.cartesLiees { flex-direction: row !important; flex-wrap: wrap !important; align-items: center !important; justify-content: flex-start !important; margin-left: 0 !important; }
            .items.cartesLiees br { flex-basis: 100% !important; height: 0 !important; margin: 0 !important; display: block !important; content: "" !important; }
            /* Supprimer les espaces blancs vides en haut/bas */
            .card > br:first-child { display: none !important; }
            .card > br:last-child { display: none !important; }
            div:empty, p:empty { display: none !important; }
        </style>
        """

        nav_script = r"""
        <script>
        (function() {
            function pycmd(cmd) {
                if (typeof bridgeCommand !== 'undefined') {
                    bridgeCommand(cmd);
                } else if (typeof window.pycmd !== 'undefined') {
                    window.pycmd(cmd);
                } else {
                    window.location.href = 'pycmd:' + cmd;
                }
            }

            function _gn(el) {
                if (!el) return '';
                if (el.dataset && el.dataset.nid) return el.dataset.nid;
                if (el.getAttribute('data-nid')) return el.getAttribute('data-nid');
                var s = el.querySelector && el.querySelector('.edn-nid');
                if (s) return s.textContent.trim();
                var oc = el.getAttribute('onclick');
                if (oc) { var m = oc.match(/\d{10,}/); if (m) return m[0]; }
                return el.innerText ? el.innerText.replace(/[^0-9]/g, '') : '';
            }

            // Cacher de manière robuste toutes les informations FSRS
            function _hideFsrsInfo() {
                document.querySelectorAll('span, div, p, pre, font').forEach(function(el) {
                    if (el.children.length === 0 && el.textContent && (el.textContent.includes('FSRS:') || el.textContent.includes('FSRS :'))) {
                        el.style.setProperty('display', 'none', 'important');
                        var p = el.parentNode;
                        if (p && (p.id.toLowerCase().includes('fsrs') || p.className.toLowerCase().includes('fsrs'))) {
                            p.style.setProperty('display', 'none', 'important');
                        }
                    }
                });
            }
            _hideFsrsInfo();
            setTimeout(_hideFsrsInfo, 100);
            setTimeout(_hideFsrsInfo, 400);

            var badges = Array.from(document.querySelectorAll('.clickable_cards'));
            var activeIdx = -1;

            function highlightBadge(idx) {
                if (badges.length === 0) return;
                badges.forEach(function(b) {
                    b.style.outline = 'none';
                    b.style.boxShadow = 'none';
                    b.style.transform = 'none';
                });
                
                if (idx < 0) idx = badges.length - 1;
                if (idx >= badges.length) idx = 0;
                
                activeIdx = idx;
                var activeBadge = badges[activeIdx];
                activeBadge.style.outline = '2px solid #3b82f6';
                activeBadge.style.outlineOffset = '1px';
                activeBadge.style.boxShadow = '0 0 8px rgba(59, 130, 246, 0.5)';
                activeBadge.style.transform = 'scale(1.05)';
                activeBadge.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                activeBadge.focus();
            }

            var hoverTimer = null;
            var previewBadgeClicked = false;
            badges.forEach(function(b, idx) {
                b.addEventListener('mousedown', function(e) {
                    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
                    previewBadgeClicked = true;
                });
                b.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
                    previewBadgeClicked = true;
                    var nid = _gn(b);
                    if (nid) {
                        pycmd('open_browser:' + nid);
                    }
                });
                b.addEventListener('mouseover', function(e) {
                    previewBadgeClicked = false;
                    var nid = _gn(b);
                    if (nid) {
                        b.style.outline = '2px solid #3b82f6';
                        if (hoverTimer) clearTimeout(hoverTimer);
                        hoverTimer = setTimeout(function() {
                            if (previewBadgeClicked) return;
                            pycmd('preview_nested:' + nid);
                        }, 250); // 250ms hover delay
                    }
                });
                b.addEventListener('mouseout', function(e) {
                    if (e.relatedTarget && b.contains(e.relatedTarget)) return;
                    if (hoverTimer) clearTimeout(hoverTimer);
                    b.style.outline = 'none';
                });
            });

            var _kbdNext = (window._ednMiroirKeys && window._ednMiroirKeys.next) || 'n';
            var _kbdPrev = (window._ednMiroirKeys && window._ednMiroirKeys.prev) || 'p';
            var _kbdOpen = (window._ednMiroirKeys && window._ednMiroirKeys.open) || 'r';
            var _kbdBack = (window._ednMiroirKeys && window._ednMiroirKeys.back) || 'z';

            document.addEventListener("keydown", function(e) {
                if (badges.length === 0) {
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        pycmd('escape');
                    }
                    if (e.key.toLowerCase() === _kbdBack.toLowerCase()) {
                        e.preventDefault();
                        pycmd('preview_back');
                    }
                    return;
                }

                var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;

                if (e.key === 'ArrowDown' || k === _kbdNext.toLowerCase()) {
                    e.preventDefault();
                    highlightBadge(activeIdx + 1);
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    highlightBadge(activeIdx - 1);
                } else if (k === _kbdPrev.toLowerCase()) {
                    e.preventDefault();
                    pycmd('preview_key_to_assistant:p');
                } else if (k === _kbdOpen.toLowerCase()) {
                    e.preventDefault();
                    if (activeIdx >= 0 && activeIdx < badges.length) {
                        var nid = _gn(badges[activeIdx]);
                        if (nid) pycmd('preview_nested:' + nid);
                    } else {
                        highlightBadge(0);
                    }
                } else if (k === _kbdBack.toLowerCase()) {
                    e.preventDefault();
                    pycmd('preview_back');
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    pycmd('escape');
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    if (activeIdx >= 0 && activeIdx < badges.length) {
                        var nid = _gn(badges[activeIdx]);
                        if (nid) pycmd('open_browser:' + nid);
                    }
                }
            });
        })();
        </script>
        """

        try:
            web.stdHtml(
                css_link + scoped_css + _shortcuts_script + '<div class="card">' + isolated + '</div>' + nav_script,
                mathjax=True
            )
        except TypeError:
            web.stdHtml(
                css_link + scoped_css + _shortcuts_script + '<div class="card">' + isolated + '</div>' + nav_script,
                js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"]
            )

        dlg.show()
        dlg.raise_()

        # Mettre à jour l'état dans l'assistant
        if _is_widget_alive(parent_widget):
            try:
                parent_widget.eval("window._ednPreviewPopupOpen = true;")
            except:
                pass

        # Redimensionner selon le contenu (calcul identique à 'Voir')
        def _quick_resize():
            try:
                web.evalWithCallback(
                    "var card = document.querySelector('.card'); var h = card ? card.scrollHeight + 30 : document.body.scrollHeight + 30; h;",
                    lambda h: _adjust_height(dlg, h)
                )
            except Exception:
                pass

        def _try_typeset(attempts=0):
            def _check(ready):
                if ready:
                    web.eval(
                        "if (MathJax.typesetClear) { try { MathJax.typesetClear(); } catch(e) {} } "
                        "MathJax.typesetPromise().then(function() {"
                        "  setTimeout(function() {"
                        "    var card = document.querySelector('.card');"
                        "    var h = card ? card.scrollHeight + 30 : document.body.scrollHeight + 30;"
                        "    window.location.href = 'pycmd:preview_resize:' + h;"
                        "  }, 50);"
                        "}).catch(function(){});"
                    )
                    QTimer.singleShot(150, _quick_resize)
                    QTimer.singleShot(500, _quick_resize)
                elif attempts < 15:
                    QTimer.singleShot(100, lambda: _try_typeset(attempts + 1))
                else:
                    _quick_resize()
            try:
                web.evalWithCallback("typeof MathJax !== 'undefined' && !!MathJax.typesetPromise", _check)
            except Exception:
                _quick_resize()

        QTimer.singleShot(30, _quick_resize)
        QTimer.singleShot(100, _quick_resize)
        QTimer.singleShot(100, lambda: _try_typeset())
        QTimer.singleShot(500, _quick_resize)

    except Exception as ex:
        print("EDN assistant preview error:", ex)


def _adjust_height(dlg, h):
    try:
        screen = QApplication.primaryScreen().availableGeometry()
        absolute_max = min(1500, int(screen.height() * 0.95))
    except Exception:
        absolute_max = 950

    if h and h > 0:
        try:
            new_height = min(absolute_max, max(150, int(float(h)) + 20))
        except (ValueError, TypeError):
            new_height = 350
        dlg.resize(dlg.width(), new_height)
    else:
        dlg.resize(dlg.width(), 350)





# --- Helpers ---

def extract_nids_from_fields_list(fields):
    nids = set()
    for f in fields:
        for match in re.finditer(r'data-nid=[\"\'](\d{10,})[\"\']', f):
            nids.add(match.group(1))
        for match in re.finditer(r'<span[^>]*class=[\"\'][^\"\']*edn-nid[^\"\']*[\"\'][^>]*>\s*(\d{10,})\s*</span>', f):
            nids.add(match.group(1))
        for match in re.finditer(r'cards_ct_click\([\'""](\d{10,})[\'""]\)', f):
            nids.add(match.group(1))
        # Extraire aussi les Note IDs bruts de 13 chiffres (plain text ou autre)
        for match in re.finditer(r'\b(1\d{12})\b', f):
            nids.add(match.group(1))
    return nids

def extract_nids_from_fields(note):
    return extract_nids_from_fields_list(note.fields)

def get_fields_recto(fields):
    try:
        html = fields[0]
        clean = re.sub(r'<[^>]+>', ' ', html).replace('&nbsp;', ' ')
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean[:100] or "[Vide]"
    except:
        return "[Erreur]"

def get_note_recto(note):
    return get_fields_recto(note.fields)

def _bg_fetch_relations(col, current_nid: str, note_fields: list[str]) -> dict:
    # 1. Backlinks: Cards pointing to this note
    query = f'"{current_nid}"'
    backlink_nids = col.find_notes(query)
    
    backlinks = []
    for bnid in backlink_nids:
        if str(bnid) == current_nid: continue
        try:
            bnote = col.get_note(bnid)
            bnote_nids = extract_nids_from_fields_list(bnote.fields)
            if current_nid in bnote_nids:
                backlinks.append({"nid": str(bnid), "recto": get_fields_recto(bnote.fields)})
        except:
            pass

    # 2. Outgoing links: Cards this note points to
    outgoing_nids = extract_nids_from_fields_list(note_fields)
    
    outgoing = []
    for onid in outgoing_nids:
        if onid == current_nid: continue
        try:
            onote = col.get_note(int(onid))
            outgoing.append({"nid": onid, "recto": get_fields_recto(onote.fields)})
        except:
            pass

    # Compare
    outgoing_nids_set = set(n["nid"] for n in outgoing)
    backlink_nids_set = set(n["nid"] for n in backlinks)
    
    missing_backlinks = [n for n in backlinks if n["nid"] not in outgoing_nids_set]
    mirrors = [n for n in backlinks if n["nid"] in outgoing_nids_set]
    missing_outgoing = [n for n in outgoing if n["nid"] not in backlink_nids_set]

    return {
        "missing_backlinks": missing_backlinks,
        "mirrors": mirrors,
        "missing_outgoing": missing_outgoing
    }

def update_assistant(editor):
    # Cacher le popup de preview existant lors d'une mise à jour/chargement
    try:
        global _preview_popup
        if _preview_popup:
            _preview_popup.hide()
    except:
        pass

    if getattr(editor, '_edn_assistant_hidden', False):
        return
        
    assistant = getattr(editor, '_edn_assistant_web', None)
    if not assistant:
        return
    
    # Vérifier que le QWebEngineView n'a pas été détruit par Qt
    try:
        assistant.objectName()  # lève RuntimeError si l'objet C++ est détruit
    except RuntimeError:
        editor._edn_assistant_web = None
        return
        
    note = editor.note
    if not note or not note.id:
        try:
            assistant.stdHtml(f"<style>{_assistant_css}</style><body><div class='empty'>Veuillez sauvegarder la note d'abord.</div></body>")
        except RuntimeError:
            editor._edn_assistant_web = None
        return

    current_nid = str(note.id)
    note_fields = list(note.fields)
    
    # Lire les raccourcis configurés pour les injecter dans le JS
    try:
        from .edn_menu import get_shortcut
        _kbd_next = get_shortcut("linked_cards_kbd_open", "n")      # N = suivant dans preview
        _kbd_prev = get_shortcut("linked_cards_kbd_preview", "p")   # P = précédent
        _kbd_open = get_shortcut("linked_cards_kbd_navigate", "r")  # R = ouvrir browser
        _kbd_back = get_shortcut("linked_cards_kbd_back", "z")      # Z = fermer popup
    except Exception:
        _kbd_next, _kbd_prev, _kbd_open, _kbd_back = "n", "p", "r", "z"

    def on_success(res):
        try:
            assistant.objectName()
        except RuntimeError:
            editor._edn_assistant_web = None
            return

        current_note = getattr(editor, 'note', None)
        if not current_note or str(current_note.id) != current_nid:
            return  # Discard if note changed or editor closed
            
        missing_backlinks = res["missing_backlinks"]
        mirrors = res["mirrors"]
        missing_outgoing = res["missing_outgoing"]

        _shortcuts_script = (
            f"<script>window._ednMiroirKeys = {{"
            f"next: '{_kbd_next}', prev: '{_kbd_prev}', open: '{_kbd_open}', back: '{_kbd_back}'"
            f"}};\nwindow._ednPreviewPopupOpen = false;</script>"
        )
        html = f"<style>{_assistant_css}</style>{_shortcuts_script}<body>"
        
        def _card_item_html(n, actions_html="", card_class=""):
            recto_esc = n['recto'].replace("'", "&#39;").replace('"', '&quot;')
            nid_badge = f"<span class='card-nid-badge'>{n['nid']}</span>"
            actions_div = f"<div class='card-actions'>{actions_html}</div>" if actions_html else ""
            return f"""
            <div class='card-item {card_class}' data-nid='{n['nid']}'>
                <div class='card-title'>{recto_esc}</div>
                {nid_badge}
                {actions_div}
            </div>
            """
        
        # Section 1: Missing mirrors
        html += "<h3>Liens Entrants (Miroirs manquants)</h3><div class='section'>"
        if not missing_backlinks:
            html += "<div class='empty'>Aucun.</div>"
        else:
            for n in missing_backlinks:
                recto_esc = n['recto'].replace("'", "&#39;").replace('"', '&quot;')
                add_btn = f"<button class='btn-add' data-action='add_mirror' data-nid='{n['nid']}' data-recto='{recto_esc}'>+ Ajouter lien</button>"
                html += _card_item_html(n, add_btn, "incoming")
        html += "</div>"
        
        # Section 2: Complete mirrors
        html += "<h3>Liens Réciproques</h3><div class='section'>"
        if not mirrors:
            html += "<div class='empty'>Aucun.</div>"
        else:
            for n in mirrors:
                html += _card_item_html(n, card_class="reciprocal")
        html += "</div>"
        
        # Section 3: Outgoing without backlink
        html += "<h3>Liens Sortants (sans retour)</h3><div class='section'>"
        if not missing_outgoing:
            html += "<div class='empty'>Aucun.</div>"
        else:
            for n in missing_outgoing:
                recto_esc = n['recto'].replace("'", "&#39;").replace('"', '&quot;')
                recip_btn = f"<button class='btn-add' data-action='make_reciprocal' data-nid='{n['nid']}'>↔ Rendre réciproque</button>"
                html += _card_item_html(n, recip_btn, "outgoing")
        html += "</div>"
        
        html += """
        <script>
        document.addEventListener("click", function(e) {
            // Priorité : bouton action (btn-add / btn-preview)
            var btn = e.target.closest ? e.target.closest('[data-action]') : null;
            if (btn) {
                var action = btn.getAttribute('data-action');
                var nid = btn.getAttribute('data-nid');
                var recto = btn.getAttribute('data-recto') || '';
                if (action === 'add_mirror') {
                    pycmd('add_mirror:' + nid + '|||' + recto);
                } else if (action === 'make_reciprocal') {
                    pycmd('make_reciprocal:' + nid);
                } else if (action === 'preview_card') {
                    pycmd('preview_card:' + nid);
                } else if (action === 'open_browser') {
                    pycmd('open_browser:' + nid);
                }
                e.stopPropagation();
                return;
            }
            // Clic sur l'élément de carte lui-même -> ouvre le navigateur
            var card = e.target.closest ? e.target.closest('.card-item') : null;
            if (card) {
                var nid = card.getAttribute('data-nid');
                if (nid) {
                    pycmd('open_browser:' + nid);
                }
            }
        });
        function pycmd(cmd) {
            if (typeof bridgeCommand !== 'undefined') {
                bridgeCommand(cmd);
            } else if (typeof window.pycmd !== 'undefined') {
                window.pycmd(cmd);
            } else {
                window.location.href = 'pycmd:' + cmd;
            }
        }

        var hoverTimer = null;
        var currentHoveredNid = null;
        document.addEventListener("mouseover", function(e) {
            var card = e.target.closest ? e.target.closest('.card-item') : null;
            if (card) {
                var nid = card.getAttribute('data-nid');
                if (nid && nid !== currentHoveredNid) {
                    if (hoverTimer) clearTimeout(hoverTimer);
                    currentHoveredNid = nid;
                    var rect = card.getBoundingClientRect();
                    hoverTimer = setTimeout(function() {
                        pycmd('hover_card:' + nid + '|||' + Math.round(rect.top));
                    }, 150); // 150ms hover delay
                }
            }
        });
        document.addEventListener("mouseout", function(e) {
            var card = e.target.closest ? e.target.closest('.card-item') : null;
            if (card) {
                if (e.relatedTarget && card.contains(e.relatedTarget)) return;
                if (hoverTimer) clearTimeout(hoverTimer);
                currentHoveredNid = null;
                hoverTimer = setTimeout(function() {
                    pycmd('hover_leave');
                }, 100); // 100ms leave delay
            }
        });

        var activeIdx = -1;
        function getCards() {
            return Array.from(document.querySelectorAll('.card-item'));
        }
        function setActiveCard(idx) {
            var cards = getCards();
            if (cards.length === 0) return;
            
            cards.forEach(function(c) { c.classList.remove('active-card'); });
            
            if (idx < 0) idx = cards.length - 1;
            if (idx >= cards.length) idx = 0;
            
            activeIdx = idx;
            var activeCard = cards[activeIdx];
            activeCard.classList.add('active-card');
            activeCard.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            
            var nid = activeCard.getAttribute('data-nid');
            if (nid) {
                if (hoverTimer) clearTimeout(hoverTimer);
                var rect = activeCard.getBoundingClientRect();
                pycmd('hover_card:' + nid + '|||' + Math.round(rect.top));
            }
        }
        
        // Raccourcis lus depuis la config addon (injectés par Python via window._ednMiroirKeys)
        var _kbdNext    = (window._ednMiroirKeys && window._ednMiroirKeys.next)    || 'n';
        var _kbdPrev    = (window._ednMiroirKeys && window._ednMiroirKeys.prev)    || 'p';
        var _kbdOpen    = (window._ednMiroirKeys && window._ednMiroirKeys.open)    || 'r';
        var _kbdBack    = (window._ednMiroirKeys && window._ednMiroirKeys.back)    || 'z';

        document.addEventListener("keydown", function(e) {
            var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
            
            // Si la preview popup est ouverte, relayer les touches n, r, z, Escape
            if (window._ednPreviewPopupOpen) {
                if (e.key === 'Escape' || k === _kbdNext.toLowerCase() || k === _kbdOpen.toLowerCase() || k === _kbdBack.toLowerCase()) {
                    e.preventDefault();
                    pycmd('preview_key_forward:' + e.key);
                    return;
                }
            }

            if (e.key === 'Escape') {
                e.preventDefault();
                pycmd('hover_leave');
                pycmd('escape');
                return;
            }
            var cards = getCards();
            if (cards.length === 0) return;

            // p ou ArrowDown cycles DOWN in assistant list
            if (e.key === 'ArrowDown' || k === _kbdPrev.toLowerCase()) {
                e.preventDefault();
                setActiveCard(activeIdx + 1);
            // ArrowUp cycles UP
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setActiveCard(activeIdx - 1);
            // open/r : focaliser la preview popup
            } else if (k === _kbdOpen.toLowerCase()) {
                if (activeIdx >= 0 && activeIdx < cards.length) {
                    e.preventDefault();
                    var nidR = cards[activeIdx].getAttribute('data-nid');
                    var rect = cards[activeIdx].getBoundingClientRect();
                    if (nidR) pycmd('focus_preview:' + nidR + '|||' + Math.round(rect.top));
                }
            // back/z : fermer le popup preview
            } else if (k === _kbdBack.toLowerCase()) {
                e.preventDefault();
                pycmd('hover_leave');
            } else if (e.key === 'Enter') {
                if (activeIdx >= 0 && activeIdx < cards.length) {
                    e.preventDefault();
                    var nidE = cards[activeIdx].getAttribute('data-nid');
                    if (nidE) pycmd('open_browser:' + nidE);
                }
            }
        });
        </script>
        </body>
        """
        try:
            assistant.stdHtml(html)
        except RuntimeError:
            editor._edn_assistant_web = None

    if HAS_QUERY_OP:
        op = QueryOp(
            parent=mw,
            op=lambda col: _bg_fetch_relations(col, current_nid, note_fields),
            success=on_success
        )
        op.run_in_background()
    else:
        res = _bg_fetch_relations(mw.col, current_nid, note_fields)
        on_success(res)

def _find_field_by_name_or_default(note, preferred_name):
    flds = note.note_type()['flds']
    if preferred_name:
        for i, fld in enumerate(flds):
            if fld['name'].lower() == preferred_name.lower():
                return i
    # Fallback par défaut sur les noms standards
    return next((i for i, fld in enumerate(flds) if fld['name'].lower() in ["cartes liées", "cartes liees", "liens", "linked cards"]), len(flds) - 1)

def handle_assistant_message(handled, message, context):
    if handled and handled[0]:
        return handled
    if message.startswith("add_mirror:"):
        try:
            parts = message.split("add_mirror:")[1].split("|||")
            nid = parts[0]
            recto = parts[1] if len(parts) > 1 else ""
            
            editor = get_active_editor(context)
            if not editor or not editor.note:
                return (True, None)
                
            recto_escaped = recto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", "&#39;")
            badge_html = f'{recto_escaped} — <kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid}"><span class="edn-nid">{nid}</span></kbd>&nbsp;'
            
            # Recherche intelligente du champ correspondant
            source_field_name = None
            try:
                source_note = mw.col.get_note(int(nid))
                for fld in source_note.note_type()['flds']:
                    if str(editor.note.id) in source_note[fld['name']]:
                        source_field_name = fld['name']
                        break
            except Exception as fe:
                print("Error finding source field name:", fe)
                
            target_fidx = _find_field_by_name_or_default(editor.note, source_field_name)
                
            def on_saved():
                current_val = editor.note.fields[target_fidx].strip()
                if not current_val or current_val == "<br>":
                    new_val = badge_html
                else:
                    new_val = current_val + "<br>" + badge_html
                
                editor.note.fields[target_fidx] = new_val
                mw.col.update_note(editor.note)
                
                if hasattr(editor, 'loadNoteKeepingFocus'):
                    editor.loadNoteKeepingFocus()
                else:
                    editor.loadNote()
                update_assistant(editor)
            
            editor.saveNow(on_saved)
            
        except Exception as e:
            print("Erreur add_mirror:", e)
        return (True, None)
        
    elif message.startswith("make_reciprocal:"):
        try:
            target_nid = message.split("make_reciprocal:")[1]
            editor = get_active_editor(context)
            if not editor or not editor.note:
                return (True, None)
                
            current_note = editor.note
            current_nid = str(current_note.id)
            current_recto = get_note_recto(current_note)
            
            target_note = mw.col.get_note(int(target_nid))
            
            recto_escaped = current_recto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", "&#39;")
            badge_html = f'{recto_escaped} — <kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{current_nid}"><span class="edn-nid">{current_nid}</span></kbd>&nbsp;'
            
            # Recherche intelligente du champ correspondant
            source_field_name = None
            try:
                for fld in current_note.note_type()['flds']:
                    if target_nid in current_note[fld['name']]:
                        source_field_name = fld['name']
                        break
            except Exception as fe:
                print("Error finding source field name:", fe)
                
            target_fidx = _find_field_by_name_or_default(target_note, source_field_name)
            
            current_val = target_note.fields[target_fidx].strip()
            if not current_val or current_val == "<br>":
                new_val = badge_html
            else:
                new_val = current_val + "<br>" + badge_html
            
            target_note.fields[target_fidx] = new_val
            mw.col.update_note(target_note)
            
            update_assistant(editor)
            
        except Exception as e:
            print("Erreur make_reciprocal:", e)
        return (True, None)
 
    elif message.startswith("open_browser:"):
        try:
            nid = message[len("open_browser:"):]
            from aqt import dialogs
            browser = dialogs.open("Browser", mw)
            browser.search_for(f"nid:{nid}")
        except Exception as e:
            print("Erreur open_browser:", e)
        return (True, None)
 
    elif message.startswith("preview_card:"):
        try:
            nid = message[len("preview_card:"):]
            editor = get_active_editor(context)
            anchor = None
            if editor:
                anchor = getattr(editor, '_edn_assistant_web', None)
            _show_card_preview_popup(nid, parent_widget=anchor)
        except Exception as e:
            print("Erreur preview_card:", e)
        return (True, None)
    elif message.startswith("hover_card:"):
        try:
            parts = message[len("hover_card:"):].split("|||")
            nid = parts[0]
            y_offset = int(parts[1]) if len(parts) > 1 else 0
            editor = get_active_editor(context)
            anchor = None
            if editor:
                anchor = getattr(editor, '_edn_assistant_web', None)
            _show_card_preview_popup(nid, parent_widget=anchor, y_offset=y_offset)
        except Exception as e:
            print("Erreur hover_card:", e)
        return (True, None)
  
    elif message.startswith("focus_preview:"):
        try:
            parts = message[len("focus_preview:"):].split("|||")
            nid = parts[0]
            y_offset = int(parts[1]) if len(parts) > 1 else 0
            editor = get_active_editor(context)
            anchor = None
            if editor:
                anchor = getattr(editor, '_edn_assistant_web', None)
            _show_card_preview_popup(nid, parent_widget=anchor, y_offset=y_offset)
            if _is_preview_popup_alive():
                _preview_popup.activateWindow()
                _preview_popup._edn_web.setFocus()
        except Exception as e:
            print("Erreur focus_preview:", e)
        return (True, None)
 
    elif message == "hover_leave":
        check_hide_preview()
        return (True, None)
 
    elif message == "escape":
        _hide_card_preview_popup()
        editor = get_active_editor(context)
        _close_editor(editor)
        return (True, None)

    elif message.startswith("preview_key_forward:"):
        try:
            key = message[len("preview_key_forward:"):]
            if _is_preview_popup_alive():
                # Mapper vers la touche physique attendue par le JS de la preview
                from .edn_menu import get_shortcut
                kbd_next = get_shortcut("linked_cards_kbd_open", "n").lower()
                kbd_prev = get_shortcut("linked_cards_kbd_preview", "p").lower()
                kbd_open = get_shortcut("linked_cards_kbd_navigate", "r").lower()
                kbd_back = get_shortcut("linked_cards_kbd_back", "z").lower()
                
                mapped_key = key
                k_low = key.lower()
                if k_low == kbd_next:
                    mapped_key = "n"
                elif k_low == kbd_prev:
                    mapped_key = "p"
                elif k_low == kbd_open:
                    mapped_key = "r"
                elif k_low == kbd_back:
                    mapped_key = "z"
                
                _preview_popup._edn_web.eval(
                    f"document.dispatchEvent(new KeyboardEvent('keydown', "
                    f"{{key: '{mapped_key}', bubbles: true, cancelable: true, composed: true}}));"
                )
        except Exception as e:
            # EdnAssistantWeb et EdnAssistantPage ne sont plus nécessaires car nous utilisons AnkiWebView avec son pont natif et ses styles.
            pass
        return (True, None)

    return handled

def _setup_assistant_ui(editor):
    if getattr(editor, '_edn_assistant_setup', False):
        return
    editor._edn_assistant_setup = True
    
    try:
        # Get the webview's parent layout
        web_widget = editor.web
        parent_widget = web_widget.parentWidget()
        if not parent_widget: return
        parent_layout = parent_widget.layout()
        if not parent_layout: return
        
        # Replace web_widget with a QSplitter
        idx = parent_layout.indexOf(web_widget)
        if idx == -1: return
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        parent_layout.insertWidget(idx, splitter)
        
        splitter.addWidget(web_widget)
        
        from aqt.webview import AnkiWebView
        assistant = AnkiWebView(parent=splitter)
        
        assistant.setMinimumWidth(200)
        assistant.setMaximumWidth(450)
        assistant.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # Permettre le focus clavier pour la navigation par flèches et raccourcis
        assistant.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        splitter.addWidget(assistant)
        splitter.setSizes([splitter.width(), 0])
        assistant.hide()  # Fermé par défaut — état mémorisé restauré dans on_editor_did_init
        
        editor._edn_assistant_web = assistant
        
    except Exception as e:
        print("Failed to setup editor assistant:", e)

def on_editor_did_init(editor):
    # Retardons légèrement pour que le layout soit construit
    QTimer.singleShot(100, lambda: _setup_assistant_ui(editor))
    # Restaurer l'état ouvert/fermé — délai supplémentaire pour laisser le splitter se créer
    QTimer.singleShot(250, lambda: _restore_assistant_state(editor))

def _restore_assistant_state(editor):
    """Ouvre le panneau si l'utilisateur l'avait laissé ouvert la dernière fois."""
    try:
        if _get_assistant_should_open():
            _show_assistant(editor)
    except Exception as e:
        print("EDN assistant: restore state error:", e)

def toggle_assistant(editor):
    """Affiche ou masque le panneau assistant (cartes miroir).
    
    Si le panneau n'est pas encore configuré, il est créé d'abord.
    Le panneau commence caché et s'affiche sur Ctrl+Alt+B.
    Mémorise l'état pour la prochaine session.
    """
    if not getattr(editor, '_edn_assistant_setup', False):
        # Setup non encore fait : le faire maintenant
        _setup_assistant_ui(editor)
        QTimer.singleShot(150, lambda: _show_assistant(editor))
        return

    assistant = getattr(editor, '_edn_assistant_web', None)
    if not assistant:
        return

    if assistant.isVisible():
        assistant.hide()
        _save_assistant_state(False)
        # Masquer aussi le popup de preview
        _hide_card_preview_popup()
    else:
        _show_assistant(editor)
        _save_assistant_state(True)

def _show_assistant(editor):
    """Affiche le panneau assistant et le met à jour."""
    assistant = getattr(editor, '_edn_assistant_web', None)
    if not assistant:
        return
    splitter = assistant.parent()
    if hasattr(splitter, 'sizes') and hasattr(splitter, 'setSizes'):
        try:
            total = sum(splitter.sizes())
            if total > 0:
                splitter.setSizes([max(300, total * 3 // 4), max(200, total // 4)])
            else:
                splitter.setSizes([600, 200])
        except Exception:
            pass
    assistant.show()
    update_assistant(editor)

def on_editor_did_load_note(editor):
    # Ne mettre à jour que si le panneau est visible (évite les requêtes DB inutiles)
    assistant = getattr(editor, '_edn_assistant_web', None)
    if assistant:
        try:
            assistant.objectName()  # lève RuntimeError si l'objet C++ est détruit
            if assistant.isVisible():
                QTimer.singleShot(200, lambda: update_assistant(editor))
        except RuntimeError:
            editor._edn_assistant_web = None

def init_assistant():
    gui_hooks.editor_did_init.append(on_editor_did_init)
    gui_hooks.editor_did_load_note.append(on_editor_did_load_note)
    gui_hooks.webview_did_receive_js_message.append(handle_assistant_message)
