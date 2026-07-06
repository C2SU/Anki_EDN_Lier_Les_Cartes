import json
import re
from aqt import mw
from aqt.qt import *
from aqt import gui_hooks
from .logger import log

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
_ASSISTANT_STATE_KEY = "_editor_assistant_open"

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
        print("Editor assistant: Impossible de sauvegarder l'état panneau :", e)

def _is_widget_alive(w):
    if w is None:
        return False
    try:
        w.objectName()
        return True
    except (RuntimeError, AttributeError):
        return False

def _is_editor_alive(editor):
    if editor is None:
        return False
    try:
        if hasattr(editor, 'web') and editor.web is not None:
            editor.web.objectName()
            return True
        return False
    except (RuntimeError, AttributeError):
        return False

def get_active_editor(context=None):
    from aqt import mw
    from .logger import log
    log(f"get_active_editor context={context}")
    
    # If context is an Editor, return it directly
    from aqt.editor import Editor
    if isinstance(context, Editor):
        try:
            if context.web is not None:
                context.web.objectName()
                log("get_active_editor: context is active Editor, returning directly")
                return context
        except (RuntimeError, AttributeError) as e:
            log(f"get_active_editor: context is Editor but check failed: {e}")
            pass

    # If context is the assistant's AnkiWebView or editor's EditorWebView, find its editor
    if context:
        # Check if context is the preview popup's webview itself
        if _is_preview_popup_alive() and getattr(_preview_popup, '_edn_web', None) == context:
            parent_assistant = getattr(_preview_popup, '_edn_parent_widget', None)
            if parent_assistant:
                from .linked_cards import _current_editor
                if _current_editor and getattr(_current_editor, '_editor_assistant_web', None) == parent_assistant:
                    log("get_active_editor: matched preview popup context to editor via parent assistant")
                    return _current_editor

        # Check active window first
        if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow'):
            win = mw.app.activeWindow()
            log(f"get_active_editor: checking activeWindow={win}")
            if hasattr(win, 'editor') and win.editor:
                try:
                    editor = win.editor
                    log(f"get_active_editor: activeWindow has editor. web={getattr(editor, 'web', None)}")
                    # Safe check for editor.web attribute presence and equivalence
                    if hasattr(editor, 'web') and editor.web is not None and (editor.web == context or getattr(editor, '_editor_assistant_web', None) == context):
                        log("get_active_editor: activeWindow editor matched context")
                        return editor
                except (RuntimeError, AttributeError) as e:
                    log(f"get_active_editor: activeWindow editor check exception: {e}")
                    pass
        # Check _current_editor
        from .linked_cards import _current_editor
        log(f"get_active_editor: checking _current_editor={_current_editor}")
        if _current_editor:
            try:
                log(f"get_active_editor: _current_editor web={getattr(_current_editor, 'web', None)}, assistant={getattr(_current_editor, '_editor_assistant_web', None)}")
                if hasattr(_current_editor, 'web') and _current_editor.web is not None and (_current_editor.web == context or getattr(_current_editor, '_editor_assistant_web', None) == context):
                    log("get_active_editor: _current_editor matched context")
                    return _current_editor
            except (RuntimeError, AttributeError) as e:
                log(f"get_active_editor: _current_editor check exception: {e}")
                pass

    # Fallback to active window's editor
    if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow'):
        win = mw.app.activeWindow()
        log(f"get_active_editor fallback: checking activeWindow={win}")
        if hasattr(win, 'editor') and win.editor:
            try:
                if hasattr(win.editor, 'web') and win.editor.web is not None:
                    win.editor.web.objectName()
                    log("get_active_editor fallback: returned activeWindow editor")
                    return win.editor
            except (RuntimeError, AttributeError) as e:
                log(f"get_active_editor fallback: activeWindow editor check exception: {e}")
                pass

    # Fallback to _current_editor
    from .linked_cards import _current_editor
    log(f"get_active_editor fallback: checking _current_editor={_current_editor}")
    if _current_editor:
        try:
            if hasattr(_current_editor, 'web') and _current_editor.web is not None:
                _current_editor.web.objectName()
                log("get_active_editor fallback: returned _current_editor")
                return _current_editor
        except (RuntimeError, AttributeError) as e:
            log(f"get_active_editor fallback: _current_editor exception: {e}")
            pass

    log("get_active_editor: returned None")
    return None

# --- Popup preview (imitant show_preview_popup du GUI) ---

_preview_popup = None  # QWidget popup global réutilisable
_esc_shortcut = None   # Raccourci Échap temporaire
_preview_history = []  # Historique pour navigation imbriquée
_current_preview_nid = None  # NID actuellement affiché dans l'aperçu
_preview_event_filter = None  # Event filter pour lifecycle du popup
_preview_popup_filter = None  # Event filter pour le popup lui-même


class EdnPreviewPopupFilter(QObject):
    """Filtre d'événements installé sur le popup de preview lui-même.
    
    Lance la boucle de check_hide_preview lorsque la souris entre dans le popup.
    """
    def eventFilter(self, obj, event):
        try:
            if event.type() == QEvent.Type.Enter:
                check_hide_preview()
        except Exception as e:
            print("Error in EdnPreviewPopupFilter:", e)
        return False


class EdnPreviewEventFilter(QObject):
    """Filtre d'événements global installé sur l'application pendant que le popup de preview est ouvert.
    
    Masque automatiquement le popup de preview miroir quand :
    - La fenêtre parente perd le focus (Alt-Tab)
    - Une autre fenêtre est fermée ou masquée
    - L'utilisateur clique en dehors de la preview
    """
    def eventFilter(self, obj, event):
        try:
            etype = event.type()
            # 1. Masquer sur clic en dehors du popup de preview
            if etype == QEvent.Type.MouseButtonPress:
                if _is_preview_popup_alive() and _preview_popup.isVisible():
                    # Vérifier si la souris est dans la géométrie du popup de preview
                    is_inside_preview = False
                    try:
                        if _preview_popup.rect().contains(_preview_popup.mapFromGlobal(QCursor.pos())):
                            is_inside_preview = True
                    except Exception:
                        pass
                    
                    if not is_inside_preview:
                        widget = obj
                        while widget:
                            if widget == _preview_popup:
                                is_inside_preview = True
                                break
                            try:
                                widget = widget.parent()
                            except:
                                widget = None
                                
                    if not is_inside_preview:
                        _hide_card_preview_popup()

            # 2. Masquer sur perte de focus ou masquage de la fenêtre
            elif etype in (QEvent.Type.WindowDeactivate, QEvent.Type.Close, QEvent.Type.Hide):
                # Si l'événement concerne le popup de preview lui-même, l'ignorer
                if obj == _preview_popup:
                    return False
                
                # Si c'est une désactivation de fenêtre
                if etype == QEvent.Type.WindowDeactivate and _is_preview_popup_alive() and _preview_popup.isVisible():
                    if getattr(_preview_popup, '_context_menu_active', False):
                        return False
                    try:
                        no_btn = getattr(Qt.MouseButton, "NoButton", None) or getattr(Qt, "NoButton", None)
                        if QApplication.mouseButtons() != no_btn:
                            return False
                    except Exception:
                        pass
                    
                    # Si la nouvelle fenêtre active est la preview popup elle-même, ne pas la fermer
                    active_win = QApplication.activeWindow()
                    if active_win == _preview_popup or (active_win and active_win.findChild(QWidget, "EdnAssistantPreviewPopup")):
                        return False
                        
                _hide_card_preview_popup()
        except Exception as e:
            print("Error in EdnPreviewEventFilter:", e)
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
            QApplication.instance().removeEventFilter(_preview_event_filter)
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
        assistant = getattr(editor, '_editor_assistant_web', None)
        if _is_widget_alive(assistant):
            try:
                assistant.eval("window._ednPreviewPopupOpen = false;")
            except:
                pass

def check_hide_preview():
    global _preview_popup
    if not _is_preview_popup_alive() or not _preview_popup.isVisible():
        return
        
    if getattr(_preview_popup, '_context_menu_active', False):
        QTimer.singleShot(200, check_hide_preview)
        return

    try:
        no_btn = getattr(Qt.MouseButton, "NoButton", None) or getattr(Qt, "NoButton", None)
        if QApplication.mouseButtons() != no_btn:
            QTimer.singleShot(200, check_hide_preview)
            return
    except Exception:
        pass
        
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
        assistant = getattr(editor, '_editor_assistant_web', None)
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

def handle_preview_popup_message(message, context=None):
    if message.startswith("preview_nested:"):
        nid = message[len("preview_nested:"):]
        editor = get_active_editor(context)
        anchor = None
        if editor:
            anchor = getattr(editor, '_editor_assistant_web', None)
        _show_card_preview_popup(nid, parent_widget=anchor, is_nested=True)
    elif message == "preview_back":
        global _preview_history, _current_preview_nid
        if _preview_history:
            prev_nid = _preview_history.pop()
            _current_preview_nid = prev_nid
            editor = get_active_editor(context)
            anchor = None
            if editor:
                anchor = getattr(editor, '_editor_assistant_web', None)
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
        editor = get_active_editor(context)
        _close_editor(editor)
    elif message.startswith("preview_key_to_assistant:"):
        try:
            key = message[len("preview_key_to_assistant:"):]
            editor = get_active_editor(context)
            if editor:
                assistant = getattr(editor, '_editor_assistant_web', None)
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

def _create_preview_popup(parent=None):
    """Crée le popup Qt de preview (singleton réutilisable, sans barre de titre)."""
    global _preview_popup, _preview_popup_filter
    flags = Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
    dlg = QWidget(parent, flags)
    dlg.setObjectName("EdnAssistantPreviewPopup")
    dlg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    dlg.setMinimumSize(500, 50)
    dlg.setMaximumSize(800, 1400)
    dlg.setStyleSheet("#EdnAssistantPreviewPopup { border: 1px solid #aaa; background: white; border-radius: 8px; }")

    v = QVBoxLayout(dlg)
    v.setContentsMargins(2, 2, 2, 2)
    v.setSpacing(0)

    from aqt.webview import AnkiWebView
    web = AnkiWebView(parent=dlg)
    v.addWidget(web)

    dlg._edn_web = web
    dlg._context_menu_active = False

    # Installer le filtre pour démarrer check_hide_preview au survol
    _preview_popup_filter = EdnPreviewPopupFilter(dlg)
    dlg.installEventFilter(_preview_popup_filter)

    _preview_popup = dlg


def _show_card_preview_popup(nid_str, parent_widget=None, is_nested=False, y_offset=0):
    """Ouvre un popup Qt AnkiWebView avec la preview de la carte (même rendu que GUI 'Voir')."""
    global _preview_popup, _esc_shortcut, _preview_history, _current_preview_nid
    import re as re2
    from .logger import log
    log(f"_show_card_preview_popup nid_str={nid_str} parent_widget={parent_widget} is_nested={is_nested} y_offset={y_offset}")

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
        f"<script>"
        f"window._ednMiroirKeys = {{"
        f"next: '{_kbd_next}', prev: '{_kbd_prev}', open: '{_kbd_open}', back: '{_kbd_back}'"
        f"}};"
        f"window.MathJax = window.MathJax || {{}};"
        f"window.MathJax.loader = window.MathJax.loader || {{}};"
        f"window.MathJax.loader.versions = window.MathJax.loader.versions || {{}};"
        f"window.MathJax.loader.versions['[tex]/mathtools'] = '3.0.0';"
        f"</script>"
    )

    try:
        note = mw.col.get_note(int(nid_str))
        cards = note.cards()
        if not cards:
            return
        card = cards[0]
        rendered = card.answer()
        try:
            from .linked_cards import _clean_kbd_html
            rendered = _clean_kbd_html(rendered)
        except Exception as e:
            print("Error cleaning kbd html in editor assistant:", e)

        # Nettoyage identique au GUI show_preview_popup
        isolated = re2.sub(r'(?<![\w-])id=(["\'])(.*?)\1', r'id="dlg_preview_\2"', rendered)
        isolated = re2.sub(r'src=(["\'])(?![a-z]+:)(?!/)([^"\']+)\1', r'src="/\2"', isolated, flags=re2.IGNORECASE)
        isolated = re2.sub(r'toggle\([\"\'](.*?)[\"\']\)', r"toggle('dlg_preview_\1')", isolated)
        # Remplacer onclick par data-onclick pour éviter les violations de Content Security Policy (CSP)
        isolated = re2.sub(r'\bonclick=(["\'])(.*?)\1', r'data-onclick=\1\2\1', isolated, flags=re2.IGNORECASE)
        isolated = re2.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', isolated, flags=re2.IGNORECASE)
        isolated = re2.sub(
            r"(?:<br>\s*)?<(span|div|p)\b[^>]*id=[^>]*fsrs[^>]*>.*?</\1>",
            "", isolated, flags=re2.IGNORECASE | re2.DOTALL)
        for sec in ["sourcesMegaContainer", "commentsMegaContainer", "tagsMegaContainer",
                    "erreursFaitesMegaContainer", "mnemonicsMegaContainer", "infoSupplementairesMegaContainer"]:
            # Cacher le container via style inline display none pour contourner les divs imbriqués
            isolated = re2.sub(rf'(<div[^>]*id=["\'](?:dlg_preview_)?{sec}["\'][^\'>]*style=["\'])([^\'\"]*)', r'\1display: none !important;\2', isolated, flags=re2.IGNORECASE)
            isolated = re2.sub(rf'(<div[^>]*id=["\'](?:dlg_preview_)?{sec}["\'])(?![^>]*style=)', r'\1 style="display: none !important;"', isolated, flags=re2.IGNORECASE)
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

        # Déterminer la fenêtre de l'éditeur
        editor_window = parent_widget.window() if _is_widget_alive(parent_widget) else None

        # Créer ou réutiliser le popup
        if not _is_preview_popup_alive():
            _create_preview_popup(editor_window)
        else:
            # Si le parent a changé, reparenter le popup
            if editor_window and _preview_popup.parentWidget() != editor_window:
                try:
                    _preview_popup.setParent(editor_window, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
                except Exception as e:
                    print("Error reparenting preview popup:", e)

        dlg = _preview_popup
        dlg._edn_parent_widget = parent_widget
        web = dlg._edn_web

        # Déterminer si night mode est actif pour la bordure dynamique
        is_night = False
        try:
            from aqt.theme import theme_manager
            is_night = theme_manager.night_mode
        except:
            try:
                is_night = mw.theme_manager.night_mode
            except AttributeError:
                try:
                    from aqt import theme_manager
                    is_night = theme_manager.night_mode
                except:
                    try:
                        is_night = mw.pm.night_mode()
                    except:
                        is_night = False

        if is_night:
            dlg.setStyleSheet("#EdnAssistantPreviewPopup { border: 2px solid #555; background: #373a41; border-radius: 5px; }")
        else:
            dlg.setStyleSheet("#EdnAssistantPreviewPopup { border: 2px solid #c0c1c5; background: #f5f6f7; border-radius: 5px; }")

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

        # Installer le filtre de lifecycle pour masquer le popup quand la fenêtre perd le focus ou clique en dehors
        global _preview_event_filter
        try:
            # Retirer l'ancien filtre si existant
            if _preview_event_filter:
                try:
                    QApplication.instance().removeEventFilter(_preview_event_filter)
                    _preview_event_filter.deleteLater()
                except Exception:
                    pass
            _preview_event_filter = EdnPreviewEventFilter()
            QApplication.instance().installEventFilter(_preview_event_filter)
        except Exception as lfe:
            print("Error setting lifecycle event filter:", lfe)

        # Positionner de façon intelligente
        if _is_widget_alive(parent_widget) and not is_nested:
            try:
                parent_global_pos = parent_widget.mapToGlobal(QPoint(0, 0))
                dlg_width = dlg.width() if dlg.width() > 100 else 550
                try:
                    screen = parent_widget.screen().availableGeometry()
                except Exception:
                    screen = QApplication.primaryScreen().availableGeometry()
                
                # Par défaut à droite du panneau assistant
                x = parent_global_pos.x() + parent_widget.width() + 10
                
                # Si le coin droit dépasse l'écran, décaler à gauche
                if x + dlg_width > screen.right():
                    x = parent_global_pos.x() - dlg_width - 10
                
                y = parent_global_pos.y() + y_offset
                # Ajustement initial basé sur la hauteur de départ (200px)
                if y + 250 > screen.bottom():
                    y = max(screen.top() + 10, screen.bottom() - 270)
                
                log(f"_show_card_preview_popup: moving to x={x}, y={y} (initial screen height={screen.height()})")
                dlg.move(QPoint(x, y))
            except Exception as pe:
                log(f"_show_card_preview_popup error positioning: {pe}")
                print("Error positioning preview popup:", pe)
        else:
            log(f"_show_card_preview_popup: skip positioning (parent_widget alive={_is_widget_alive(parent_widget)}, is_nested={is_nested})")

        addon_package = mw.addonManager.addonFromModule(__name__)
        css_link = f'<link rel="stylesheet" href="/_addons/{addon_package}/user_files/clickable_cards.css">'

        model_css = note.note_type().get('css', '')
        if model_css:
            from .linked_cards import _sanitize_model_css_for_preview
            sanitized_css = _sanitize_model_css_for_preview(model_css)
            css_link += f'<style>\n{sanitized_css}\n</style>'

        # Charger les styles de preview depuis user_files/preview_edn.css
        from .linked_cards import _load_preview_edn_css
        preview_css = _load_preview_edn_css()
        
        # Injecter des règles CSS pour cacher les conteneurs de la blacklist (y compris tagsIcon)
        sec_to_field = {
            "sourcesMegaContainer": "Sources",
            "commentsMegaContainer": "Comments",
            "tagsMegaContainer": "Tags",
            "erreursFaitesMegaContainer": "Erreurs faites",
            "mnemonicsMegaContainer": "Mnemonics",
            "infoSupplementairesMegaContainer": "Infos supplémentaires"
        }
        
        active_blacklist = []
        for sec in ["sourcesMegaContainer", "commentsMegaContainer", "tagsMegaContainer", "erreursFaitesMegaContainer", "mnemonicsMegaContainer", "infoSupplementairesMegaContainer"]:
            field_name = sec_to_field.get(sec)
            has_links = False
            if field_name and field_name in note:
                val = note[field_name]
                if "clickable_cards" in val or "data-nid" in val or re.search(r'\b1\d{12}\b', val):
                    has_links = True
            if not has_links:
                active_blacklist.append(sec)
                
        blacklist_selectors = [f"#dlg_preview_{sec}" for sec in active_blacklist]
        blacklist_selectors.append("#dlg_preview_FSRS_status")
        blacklist_css = ", ".join(blacklist_selectors) + " { display: none !important; }\n"
        
        scoped_css = f"<style>\n{blacklist_css}{preview_css}\n</style>"

        nav_script = r"""
        <script>
        (function() {
            var lastCmd = null;
            var lastCmdTime = 0;
            function pycmd(cmd) {
                var now = Date.now();
                if (cmd === lastCmd && now - lastCmdTime < 100) {
                    return;
                }
                lastCmd = cmd;
                lastCmdTime = now;

                if (window.pycmd && window.pycmd !== pycmd) {
                    window.pycmd(cmd);
                } else if (typeof bridgeCommand !== 'undefined') {
                    bridgeCommand(cmd);
                }
            }
            var sendPycmd = pycmd;
            window.sendPycmd = pycmd;

            window.cards_ct_click = function(nid) {
                if (nid) {
                    pycmd('open_browser:' + nid);
                }
            };

            // Event listener délégué pour remplacer les attributs onclick bloqués par CSP
            document.addEventListener('click', function(e) {
                var el = e.target;
                while (el && el !== document.body) {
                    if (el.getAttribute && el.getAttribute('data-onclick')) {
                        var clickAttr = el.getAttribute('data-onclick');
                        if (clickAttr.includes('toggle(')) {
                            var match = clickAttr.match(/toggle\(['"](.*?)['"]\)/);
                            if (match && window.toggle) {
                                window.toggle(match[1]);
                            }
                        } else {
                            try {
                                new Function(clickAttr).call(el);
                            } catch(err) {
                                console.error("Error executing data-onclick:", err);
                            }
                        }
                        break;
                    }
                    el = el.parentNode;
                }
            }, true);

            function _gn(el) {
                if (!el) return '';
                if (el.dataset && el.dataset.nid) return el.dataset.nid;
                if (el.getAttribute('data-nid')) return el.getAttribute('data-nid');
                var s = el.querySelector && el.querySelector('.edn-nid');
                if (s) return s.textContent.trim();
                var oc = el.getAttribute('data-onclick') || el.getAttribute('onclick');
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
            // Masquer les sections vides (par exemple tags / items vides) pour gagner de la place
            function _hideEmptySections() {
                document.querySelectorAll('.section').forEach(function(section) {
                    var items = section.querySelector('.items');
                    if (items) {
                        var cleanedHtml = items.innerHTML
                            .replace(/<!--[\s\S]*?-->/g, '')
                            .replace(/<br\s*\/?>/gi, '')
                            .replace(/&nbsp;/gi, '')
                            .trim();
                        var hasImage = items.querySelector('img') !== null;
                        var hasCanvas = items.querySelector('canvas') !== null;
                        var hasSvg = items.querySelector('svg') !== null;
                        var hasKbd = items.querySelector('kbd') !== null;
                        if (cleanedHtml === '' && !hasImage && !hasCanvas && !hasSvg && !hasKbd) {
                            section.style.setProperty('display', 'none', 'important');
                            var mega = section.closest('[id*="MegaContainer"]');
                            if (mega) {
                                mega.style.setProperty('display', 'none', 'important');
                            }
                            if (section.previousElementSibling && section.previousElementSibling.classList.contains('barHider')) {
                                section.previousElementSibling.style.setProperty('display', 'none', 'important');
                            }
                        }
                    }
                });
            }
            function _capitalizePreview(root) {
                try {
                    var elementsToCapitalize = root.querySelectorAll('.items, .items li');
                    elementsToCapitalize.forEach(function (item) {
                        var foundLetter = false;
                        var afterCloze = false;

                        function capitalizeFirstTextNode(node) {
                            if (foundLetter) return true;

                            if (node.nodeType === Node.TEXT_NODE) {
                                var text = node.nodeValue;
                                var match = text.match(/[a-zA-ZÀ-ÿœŒæÆ]/);
                                if (match) {
                                    var index = match.index;

                                    if (afterCloze) {
                                        var char = text.charAt(index);
                                        if (char === char.toUpperCase() && char !== char.toLowerCase()) {
                                            if (index + 1 < text.length) {
                                                var next = text.charAt(index + 1);
                                                if (next === next.toLowerCase() && next !== next.toUpperCase()) {
                                                    node.nodeValue = text.substring(0, index) + char.toLowerCase() + text.substring(index + 1);
                                                }
                                            }
                                        }
                                        foundLetter = true;
                                        return true;
                                    }

                                    var hasUpperElsewhere = false;
                                    var wordMatch = text.substring(index).match(/^[a-zA-ZÀ-ÿœŒæÆ]+/);
                                    if (wordMatch) {
                                        var word = wordMatch[0];
                                        var restOfWord = word.substring(1);
                                        for (var i = 0; i < restOfWord.length; i++) {
                                            var char = restOfWord.charAt(i);
                                            if (char === char.toUpperCase() && char !== char.toLowerCase()) {
                                                hasUpperElsewhere = true;
                                                break;
                                            }
                                        }
                                    }

                                    var precedingText = '';
                                    var current = node;
                                    while (current && current !== item) {
                                        var sib = current.previousSibling;
                                        while (sib) {
                                            precedingText = (sib.textContent || '') + precedingText;
                                            sib = sib.previousSibling;
                                        }
                                        current = current.parentNode;
                                    }
                                    precedingText += text.substring(0, index);

                                    var hasNumberBefore = precedingText.match(/[0-9](?:%|‰|°|er|re|e|ème|nd|rd|th)?\s*$/i) ? true : false;

                                    if (!hasUpperElsewhere && !hasNumberBefore && !precedingText.trim().endsWith('\\')) {
                                        node.nodeValue = text.substring(0, index) + text.charAt(index).toUpperCase() + text.substring(index + 1);
                                    }
                                    foundLetter = true;
                                    return true;
                                }
                            } else if (node.nodeType === Node.ELEMENT_NODE) {
                                if (node.tagName === 'SCRIPT' || node.tagName === 'STYLE' || node.tagName === 'A' || node.tagName === 'SUP' || node.tagName === 'ANKI-MATHJAX' || node.tagName === 'MJX-CONTAINER' || node.tagName === 'MATH') return false;
                                if (node.classList && node.classList.contains('cloze')) {
                                    if (!foundLetter) {
                                        for (var ci = 0; ci < node.childNodes.length; ci++) {
                                            if (capitalizeFirstTextNode(node.childNodes[ci])) break;
                                        }
                                    }
                                    afterCloze = true;
                                    return false;
                                }
                                for (var i = 0; i < node.childNodes.length; i++) {
                                    if (capitalizeFirstTextNode(node.childNodes[i])) return true;
                                }
                            }
                            return false;
                        }
                        capitalizeFirstTextNode(item);
                    });
                } catch(e) {}

                try {
                    var clItems = root.querySelectorAll('.cartesLiees');
                    clItems.forEach(function(container) {
                        function capitalizeFirstInNode(node) {
                            if (node.nodeType === Node.TEXT_NODE) {
                                var txt = node.nodeValue;
                                var m = txt.match(/[a-zA-ZÀ-ÿœŒæÆ]/);
                                if (m) {
                                    var idx = m.index;
                                    var hasUpperElsewhere = false;
                                    var wordMatch = txt.substring(idx).match(/^[a-zA-ZÀ-ÿœŒæÆ]+/);
                                    if (wordMatch) {
                                        var word = wordMatch[0];
                                        var restOfWord = word.substring(1);
                                        for (var i = 0; i < restOfWord.length; i++) {
                                            var char = restOfWord.charAt(i);
                                            if (char === char.toUpperCase() && char !== char.toLowerCase()) {
                                                hasUpperElsewhere = true;
                                                break;
                                            }
                                        }
                                    }

                                    var precedingText = '';
                                    var current = node;
                                    while (current && current !== container) {
                                        var sib = current.previousSibling;
                                        while (sib) {
                                            if (sib.tagName === 'BR' || sib.tagName === 'KBD') break;
                                            precedingText = (sib.textContent || '') + precedingText;
                                            sib = sib.previousSibling;
                                        }
                                        if (sib && (sib.tagName === 'BR' || sib.tagName === 'KBD')) break;
                                        current = current.parentNode;
                                    }
                                    precedingText += txt.substring(0, idx);

                                    var hasNumberBefore = precedingText.match(/[0-9](?:%|‰|°|er|re|e|ème|nd|rd|th)?\s*$/i) ? true : false;

                                    if (!hasUpperElsewhere && !hasNumberBefore && !precedingText.trim().endsWith('\\')) {
                                        node.nodeValue = txt.substring(0, idx) + txt.charAt(idx).toUpperCase() + txt.substring(idx + 1);
                                    }
                                    return true;
                                }
                                return false;
                            } else if (node.nodeType === Node.ELEMENT_NODE) {
                                if (node.tagName === 'KBD' || node.tagName === 'BR' || node.tagName === 'SCRIPT' || node.tagName === 'STYLE' || node.tagName === 'SUP') return false;
                                if (node.tagName === 'SPAN' && node.classList.contains('edn-nid')) return false;
                                for (var j = 0; j < node.childNodes.length; j++) {
                                    if (capitalizeFirstInNode(node.childNodes[j])) return true;
                                }
                            }
                            return false;
                        }
                        var childNodes = Array.from(container.childNodes);
                        var segmentStart = true;
                        for (var ci = 0; ci < childNodes.length; ci++) {
                            var cnode = childNodes[ci];
                            if (cnode.nodeName === 'BR') { segmentStart = true; continue; }
                            if (cnode.nodeType === Node.ELEMENT_NODE && cnode.tagName === 'KBD') { segmentStart = true; continue; }
                            if (cnode.nodeType === Node.ELEMENT_NODE && cnode.tagName === 'SPAN' && cnode.classList.contains('edn-nid')) continue;
                            if (segmentStart) {
                                if (capitalizeFirstInNode(cnode)) {
                                    segmentStart = false;
                                }
                            }
                        }
                    });
                } catch(e) {}
            }
            function _cleanAll() {
                _hideFsrsInfo();
                _hideEmptySections();
                _capitalizePreview(document);
            }
            _cleanAll();
            setTimeout(_cleanAll, 100);
            setTimeout(_cleanAll, 400);

            // Utilisez la délégation pour trouver les badges dynamiquement
            function getBadges() {
                return Array.from(document.querySelectorAll('.clickable_cards'));
            }

            var hoverTimer = null;
            var previewBadgeClicked = false;
            var activeIdx = -1;

            function highlightBadge(idx) {
                var badges = getBadges();
                if (badges.length === 0) return;
                
                badges.forEach(function(b) {
                    b.classList.remove('edn-selected-badge');
                    b.style.outline = 'none';
                    b.style.boxShadow = 'none';
                    b.style.transform = 'none';
                });
                
                if (idx < 0) idx = badges.length - 1;
                if (idx >= badges.length) idx = 0;
                
                activeIdx = idx;
                var activeBadge = badges[activeIdx];
                activeBadge.classList.add('edn-selected-badge');
                activeBadge.style.outline = '2px solid #3b82f6';
                activeBadge.style.outlineOffset = '1px';
                activeBadge.style.boxShadow = '0 0 8px rgba(59, 130, 246, 0.5)';
                activeBadge.style.transform = 'scale(1.05)';
                activeBadge.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                activeBadge.focus();
            }

            document.addEventListener('mousedown', function(e) {
                var b = e.target.closest ? e.target.closest('.clickable_cards') : null;
                if (b) {
                    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
                    previewBadgeClicked = true;
                }
            });

            document.addEventListener('click', function(e) {
                var b = e.target.closest ? e.target.closest('.clickable_cards') : null;
                if (b) {
                    e.preventDefault();
                    e.stopPropagation();
                    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
                    previewBadgeClicked = true;
                    var nid = _gn(b);
                    if (nid) {
                        sendPycmd('open_browser:' + nid);
                    }
                }
            });

            document.addEventListener('mouseover', function(e) {
                var b = e.target.closest ? e.target.closest('.clickable_cards') : null;
                if (b) {
                    previewBadgeClicked = false;
                    var nid = _gn(b);
                    if (nid) {
                        var badges = getBadges();
                        var idx = badges.indexOf(b);
                        if (idx >= 0) {
                            badges.forEach(function(x) {
                                x.classList.remove('edn-selected-badge');
                                x.style.outline = 'none';
                                x.style.boxShadow = 'none';
                                x.style.transform = 'none';
                            });
                            activeIdx = idx;
                            b.classList.add('edn-selected-badge');
                            b.style.outline = '2px solid #3b82f6';
                            b.style.outlineOffset = '1px';
                            b.style.boxShadow = '0 0 8px rgba(59, 130, 246, 0.5)';
                            b.style.transform = 'scale(1.05)';
                        }
                        if (hoverTimer) clearTimeout(hoverTimer);
                        hoverTimer = setTimeout(function() {
                            if (previewBadgeClicked) return;
                            sendPycmd('preview_nested:' + nid);
                        }, 250); // 250ms hover delay
                    }
                }
            });

            document.addEventListener('mouseout', function(e) {
                var b = e.target.closest ? e.target.closest('.clickable_cards') : null;
                if (b) {
                    if (e.relatedTarget && b.contains(e.relatedTarget)) return;
                    if (hoverTimer) clearTimeout(hoverTimer);
                    b.style.outline = 'none';
                }
            });

            var _kbdNext = (window._ednMiroirKeys && window._ednMiroirKeys.next) || 'n';
            var _kbdPrev = (window._ednMiroirKeys && window._ednMiroirKeys.prev) || 'p';
            var _kbdOpen = (window._ednMiroirKeys && window._ednMiroirKeys.open) || 'r';
            var _kbdBack = (window._ednMiroirKeys && window._ednMiroirKeys.back) || 'z';

            document.addEventListener("keydown", function(e) {
                if (e.key === 'Tab' && e.ctrlKey) {
                    e.preventDefault();
                    e.stopPropagation();
                    sendPycmd('ctrl_tab_pressed:' + (e.shiftKey ? 'shift' : 'normal'));
                    return;
                }
                var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
                var badges = getBadges();

                // Toujours autoriser le cyclage des cartes dans l'assistant via p (prev)
                if (k === _kbdPrev.toLowerCase()) {
                    e.preventDefault();
                    sendPycmd('preview_key_to_assistant:p');
                    return;
                }

                if (badges.length === 0) {
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        sendPycmd('escape');
                    }
                    if (k === _kbdBack.toLowerCase()) {
                        e.preventDefault();
                        sendPycmd('preview_back');
                    }
                    return;
                }

                if (e.key === 'ArrowDown' || k === _kbdNext.toLowerCase()) {
                    e.preventDefault();
                    highlightBadge(activeIdx + 1);
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    highlightBadge(activeIdx - 1);
                } else if (k === _kbdOpen.toLowerCase()) {
                    e.preventDefault();
                    var currentActive = document.querySelector('.edn-selected-badge');
                    var idx = badges.indexOf(currentActive);
                    if (idx >= 0) {
                        var nid = _gn(badges[idx]);
                        if (nid) sendPycmd('preview_nested:' + nid);
                    } else if (badges.length > 0) {
                        highlightBadge(0);
                    }
                } else if (k === _kbdBack.toLowerCase()) {
                    e.preventDefault();
                    sendPycmd('preview_back');
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    sendPycmd('escape');
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    var currentActive = document.querySelector('.edn-selected-badge');
                    var idx = badges.indexOf(currentActive);
                    if (idx >= 0) {
                        var nid = _gn(badges[idx]);
                        if (nid) sendPycmd('open_browser:' + nid);
                    }
                }
            });

            // --- Clic droit : Apprendre maintenant / Noter ---
            document.addEventListener('pointerdown', function(e) {
                var badge = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
                var nid = badge ? _gn(badge) : null;
                if (nid) {
                    sendPycmd('edn_right_click_nid:' + nid);
                } else {
                    sendPycmd('edn_right_click_nid:none');
                }
            });

            document.addEventListener('contextmenu', function(e) {
                var badge = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
                var nid = badge ? _gn(badge) : null;
                if (nid) {
                    sendPycmd('edn_right_click_nid:' + nid);
                } else {
                    sendPycmd('edn_right_click_page:sidebar_preview');
                }
            });

            // Image load listener to dynamically resize
            function _triggerResize() {
                var card = document.querySelector('.card');
                var h = card ? card.scrollHeight : document.body.scrollHeight;
                sendPycmd('preview_resize:' + h);
            }
            // Use ResizeObserver to dynamically resize whenever the content height changes
            try {
                var cardEl = document.querySelector('.card') || document.body;
                var lastHeight = 0;
                var resizeObserver = new ResizeObserver(function(entries) {
                    var h = Math.max(document.body.scrollHeight, cardEl ? cardEl.scrollHeight : 0);
                    if (h && h !== lastHeight) {
                        lastHeight = h;
                        sendPycmd('preview_resize:' + h);
                    }
                });
                resizeObserver.observe(cardEl);
            } catch(e) {
                document.addEventListener('load', function(ev) {
                    if (ev.target && ev.target.tagName === 'IMG') {
                        _triggerResize();
                    }
                }, true);
            }
        })();
        </script>
        """

        try:
            from aqt.theme import theme_manager
            is_night = theme_manager.night_mode
        except:
            try:
                is_night = mw.theme_manager.night_mode
            except AttributeError:
                try:
                    from aqt import theme_manager
                    is_night = theme_manager.night_mode
                except:
                    try:
                        is_night = mw.pm.night_mode()
                    except:
                        is_night = False
        
        night_script = ""
        if is_night:
            night_script = """
            <script>
            document.documentElement.classList.add('night-mode', 'nightMode', 'night_mode');
            if (document.body) document.body.classList.add('night-mode', 'nightMode', 'night_mode');
            </script>
            """

        try:
            web.stdHtml(
                css_link + scoped_css + night_script + _shortcuts_script + '<div class="card">' + isolated + '</div>' + nav_script,
                mathjax=True
            )
        except TypeError:
            web.stdHtml(
                css_link + scoped_css + night_script + _shortcuts_script + '<div class="card">' + isolated + '</div>' + nav_script,
                js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"]
            )

        dlg.show()
        dlg.raise_()
        log("_show_card_preview_popup: dlg shown and raised")
        dlg.resize(dlg.width(), 200)

        # Mettre à jour l'état dans l'assistant
        if _is_widget_alive(parent_widget):
            try:
                log("_show_card_preview_popup: updating assistant state to true")
                parent_widget.eval("window._ednPreviewPopupOpen = true;")
            except Exception as e:
                log(f"_show_card_preview_popup: failed to update assistant state: {e}")
                pass

        # Redimensionner selon le contenu (calcul identique à 'Voir')
        def _quick_resize():
            try:
                js = """
                (function() {
                    var cardEl = document.querySelector('.card') || document.body;
                    return Math.max(document.body.scrollHeight, cardEl ? cardEl.scrollHeight : 0);
                })()
                """
                web.evalWithCallback(js, lambda h: _adjust_height(dlg, h))
            except Exception:
                pass

        def _try_typeset(attempts=0):
            """Retry MathJax typeset every 100ms until available (max 1.5s)."""
            def _check(ready):
                if ready:
                    web.eval(
                        "if (MathJax.typesetClear) { try { MathJax.typesetClear(); } catch(e) {} } "
                        "MathJax.typesetPromise().then(function() {"
                        "  setTimeout(function() {"
                        "    var cardEl = document.querySelector('.card') || document.body;"
                        "    var h = Math.max(document.body.scrollHeight, cardEl ? cardEl.scrollHeight : 0);"
                        "    if (typeof pycmd !== 'undefined') pycmd('preview_resize:' + h);"
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
        log(f"_show_card_preview_popup general exception: {ex}")
        print("Editor assistant preview error:", ex)


def _adjust_height(dlg, h):
    try:
        try:
            screen = dlg.screen().availableGeometry()
        except Exception:
            screen = QApplication.primaryScreen().availableGeometry()
        
        y_min = screen.top() + 10
        y_max = screen.bottom() - 10
        absolute_max = screen.height() - 40

        # Constrain to parent window if alive
        parent_widget = getattr(dlg, '_edn_parent_widget', None)
        if _is_widget_alive(parent_widget):
            parent_win = parent_widget.window()
            if _is_widget_alive(parent_win) and parent_win.height() > 100:
                y_min = parent_win.y() + 10
                y_max = parent_win.y() + parent_win.height() - 10
                absolute_max = parent_win.height() - 20
    except Exception:
        y_min = 10
        y_max = 800
        absolute_max = 600
        try:
            absolute_max = mw.height() - 40
            y_min = mw.y() + 10
            y_max = mw.y() + mw.height() - 10
        except Exception:
            pass

    if h and h > 0:
        try:
            new_height = min(absolute_max, max(50, int(float(h)) + 16))
        except (ValueError, TypeError):
            new_height = 200
        dlg.resize(dlg.width(), new_height)
    else:
        dlg.resize(dlg.width(), 200)
        new_height = 200


    try:
        dlg._edn_web.eval("document.documentElement.style.overflowY = 'auto';")
    except Exception:
        pass

    # Slide up if it overflows the bottom of the screen/parent window
    try:
        pos = dlg.pos()
        if pos.y() + new_height > y_max:
            new_y = max(y_min, y_max - new_height)
            log(f"_adjust_height: sliding up from y={pos.y()} to new_y={new_y} (height={new_height})")
            dlg.move(pos.x(), new_y)
    except Exception as e:
        print("Error adjusting assistant preview position:", e)





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
        import html as _html
        raw = fields[0]
        clean = re.sub(r'<[^>]+>', ' ', raw).replace('&nbsp;', ' ')
        clean = re.sub(r'\s+', ' ', clean).strip()
        # Décoder les entités HTML résiduelles (&amp; → &, &lt; → <, etc.)
        return _html.unescape(clean)[:100] or "[Vide]"
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

    if getattr(editor, '_editor_assistant_hidden', False):
        return
        
    assistant = getattr(editor, '_editor_assistant_web', None)
    if not assistant:
        return
        
    try:
        if not assistant.isVisible():
            return
    except Exception:
        pass
    
    # Vérifier que le QWebEngineView n'a pas été détruit par Qt
    try:
        assistant.objectName()  # lève RuntimeError si l'objet C++ est détruit
    except RuntimeError:
        editor._editor_assistant_web = None
        return
        
    note = editor.note
    if not note or not note.id:
        try:
            assistant.stdHtml(f"<style>{_assistant_css}</style><body><div class='empty'>Veuillez sauvegarder la note d'abord.</div></body>")
        except RuntimeError:
            editor._editor_assistant_web = None
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
            editor._editor_assistant_web = None
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
            recto_esc = n['recto'].replace('&', '&amp;').replace("'", '&#39;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            nid_badge = f"<span class='card-nid-badge'>{n['nid']}</span>"
            actions_div = f"<div class='card-actions'>{actions_html}</div>" if actions_html else ""
            return f"""
            <div class='card-item {card_class}' data-nid='{n['nid']}'>
                <div class='card-title'>{recto_esc}</div>
                {nid_badge}
                {actions_div}
            </div>
            """
        
        # Section 1: Outgoing without backlink
        html += "<h3>Liens Sortants (sans retour)</h3><div class='section'>"
        if not missing_outgoing:
            html += "<div class='empty'>Aucun.</div>"
        else:
            for n in missing_outgoing:
                recto_esc = n['recto'].replace('&', '&amp;').replace("'", '&#39;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                recip_btn = f"<button class='btn-add' data-action='make_reciprocal' data-nid='{n['nid']}'>↔ Rendre réciproque</button>"
                html += _card_item_html(n, recip_btn, "outgoing")
        html += "</div>"
        
        # Section 2: Complete mirrors
        html += "<h3>Liens Réciproques</h3><div class='section'>"
        if not mirrors:
            html += "<div class='empty'>Aucun.</div>"
        else:
            for n in mirrors:
                html += _card_item_html(n, card_class="reciprocal")
        html += "</div>"
        
        # Section 3: Missing mirrors
        html += "<h3>Liens Entrants (Miroirs manquants)</h3><div class='section'>"
        if not missing_backlinks:
            html += "<div class='empty'>Aucun.</div>"
        else:
            for n in missing_backlinks:
                recto_esc = n['recto'].replace('&', '&amp;').replace("'", '&#39;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                add_btn = f"<button class='btn-add' data-action='add_mirror' data-nid='{n['nid']}' data-recto='{recto_esc}'>+ Ajouter lien</button>"
                html += _card_item_html(n, add_btn, "incoming")
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
                    sendPycmd('add_mirror:' + nid + '|||' + recto);
                } else if (action === 'make_reciprocal') {
                    sendPycmd('make_reciprocal:' + nid);
                } else if (action === 'preview_card') {
                    sendPycmd('preview_card:' + nid);
                } else if (action === 'open_browser') {
                    sendPycmd('open_browser:' + nid);
                }
                e.stopPropagation();
                return;
            }
            // Clic sur l'élément de carte lui-même -> ouvre le navigateur
            var card = e.target.closest ? e.target.closest('.card-item') : null;
            if (card) {
                var nid = card.getAttribute('data-nid');
                if (nid) {
                    sendPycmd('open_browser:' + nid);
                }
            }
        });
        var _anki_pycmd = window.pycmd;
        var _anki_bridge = typeof bridgeCommand !== 'undefined' ? bridgeCommand : null;
        var lastCmd = null;
        var lastCmdTime = 0;
        function sendPycmd(cmd) {
            var now = Date.now();
            if (cmd === lastCmd && now - lastCmdTime < 100) {
                return;
            }
            lastCmd = cmd;
            lastCmdTime = now;

            var activePycmd = window.pycmd || _anki_pycmd;
            if (activePycmd && activePycmd !== sendPycmd) {
                activePycmd(cmd);
            } else if (_anki_bridge) {
                _anki_bridge(cmd);
            } else {
                console.log("pycmd not ready/available for cmd: " + cmd);
            }
        }

        if (!window.pycmd) {
            window.pycmd = sendPycmd;
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
                        sendPycmd('hover_card:' + nid + '|||' + Math.round(rect.top));
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
                    sendPycmd('hover_leave');
                }, 100); // 100ms leave delay
            }
        });
        document.addEventListener("pointerdown", function(e) {
            var card = e.target.closest ? e.target.closest('.card-item') : null;
            var nid = card ? card.getAttribute('data-nid') : null;
            if (nid) {
                sendPycmd('edn_right_click_nid:' + nid);
            } else {
                sendPycmd('edn_right_click_nid:none');
            }
        });

        document.addEventListener("contextmenu", function(e) {
            var card = e.target.closest ? e.target.closest('.card-item') : null;
            var nid = card ? card.getAttribute('data-nid') : null;
            if (nid) {
                sendPycmd('edn_right_click_nid:' + nid);
            } else {
                e.preventDefault();
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
                sendPycmd('hover_card:' + nid + '|||' + Math.round(rect.top));
            }
        }
        
        // Raccourcis lus depuis la config addon (injectés par Python via window._ednMiroirKeys)
        var _kbdNext    = (window._ednMiroirKeys && window._ednMiroirKeys.next)    || 'n';
        var _kbdPrev    = (window._ednMiroirKeys && window._ednMiroirKeys.prev)    || 'p';
        var _kbdOpen    = (window._ednMiroirKeys && window._ednMiroirKeys.open)    || 'r';
        var _kbdBack    = (window._ednMiroirKeys && window._ednMiroirKeys.back)    || 'z';

        document.addEventListener("keydown", function(e) {
            if (e.key === 'Tab' && e.ctrlKey) {
                e.preventDefault();
                e.stopPropagation();
                sendPycmd('ctrl_tab_pressed:' + (e.shiftKey ? 'shift' : 'normal'));
                return;
            }
            var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
            
            // Si la preview popup est ouverte, relayer les touches n, r, z, Escape
            if (window._ednPreviewPopupOpen) {
                if (e.key === 'Escape' || k === _kbdNext.toLowerCase() || k === _kbdOpen.toLowerCase() || k === _kbdBack.toLowerCase()) {
                    e.preventDefault();
                    sendPycmd('preview_key_forward:' + e.key);
                    return;
                }
            }

            if (e.key === 'Escape') {
                e.preventDefault();
                sendPycmd('hover_leave');
                sendPycmd('escape');
                return;
            }
            var cards = getCards();
            if (cards.length === 0) return;

            // p cycles DOWN in assistant list
            if (k === _kbdPrev.toLowerCase()) {
                e.preventDefault();
                setActiveCard(activeIdx + 1);
            // open/r : focaliser la preview popup
            } else if (k === _kbdOpen.toLowerCase()) {
                if (activeIdx >= 0 && activeIdx < cards.length) {
                    e.preventDefault();
                    var nidR = cards[activeIdx].getAttribute('data-nid');
                    var rect = cards[activeIdx].getBoundingClientRect();
                    if (nidR) sendPycmd('focus_preview:' + nidR + '|||' + Math.round(rect.top));
                }
            // back/z : fermer le popup preview
            } else if (k === _kbdBack.toLowerCase()) {
                e.preventDefault();
                sendPycmd('hover_leave');
            } else if (e.key === 'Enter') {
                if (activeIdx >= 0 && activeIdx < cards.length) {
                    e.preventDefault();
                    var nidE = cards[activeIdx].getAttribute('data-nid');
                    if (nidE) sendPycmd('open_browser:' + nidE);
                }
            }
        });
        </script>
        </body>
        """
        try:
            assistant.stdHtml(html)
        except RuntimeError:
            editor._editor_assistant_web = None

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
                anchor = getattr(editor, '_editor_assistant_web', None)
            _show_card_preview_popup(nid, parent_widget=anchor)
        except Exception as e:
            print("Erreur preview_card:", e)
        return (True, None)
    elif message.startswith("hover_card:"):
        try:
            from .logger import log
            log(f"handle_assistant_message: hover_card received message={message}")
            try:
                from .linked_cards import _last_gui_close_time
                import time
                if time.time() - _last_gui_close_time < 1.0:
                    return (True, None)
            except Exception:
                pass
            parts = message[len("hover_card:"):].split("|||")
            nid = parts[0]
            y_offset = int(parts[1]) if len(parts) > 1 else 0
            editor = get_active_editor(context)
            anchor = None
            if editor:
                anchor = getattr(editor, '_editor_assistant_web', None)
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
                anchor = getattr(editor, '_editor_assistant_web', None)
            _show_card_preview_popup(nid, parent_widget=anchor, y_offset=y_offset)
            if _is_preview_popup_alive():
                _preview_popup.activateWindow()
                _preview_popup._edn_web.setFocus()
        except Exception as e:
            print("Erreur focus_preview:", e)
        return (True, None)
 
    elif message == "hover_leave":
        from .logger import log
        log("handle_assistant_message: hover_leave received")
        check_hide_preview()
        return (True, None)
 
    elif message == "escape":
        _hide_card_preview_popup()
        editor = get_active_editor(context)
        _close_editor(editor)
        return (True, None)
 
    elif message.startswith("ctrl_tab_pressed:"):
        try:
            is_shift = (message[len("ctrl_tab_pressed:"):] == "shift")
            editor = get_active_editor(context)
            if editor:
                win = editor.parentWindow or mw
                tab_key = getattr(Qt.Key, "Key_Tab", None) or getattr(Qt, "Key_Tab", None)
                ctrl_modifier = getattr(Qt.KeyboardModifier, "ControlModifier", None) or getattr(Qt, "ControlModifier", None)
                shift_modifier = getattr(Qt.KeyboardModifier, "ShiftModifier", None) or getattr(Qt, "ShiftModifier", None)
                
                mods = ctrl_modifier
                if is_shift:
                    mods |= shift_modifier
                
                new_event = QKeyEvent(
                    QEvent.Type.KeyPress,
                    tab_key,
                    mods
                )
                QCoreApplication.postEvent(win, new_event)
        except Exception as e:
            print("Error handling ctrl_tab_pressed:", e)
        return (True, None)
 
    elif message.startswith("preview_key_forward:"):
        try:
            from .logger import log
            key = message[len("preview_key_forward:"):]
            log(f"handle_assistant_message: preview_key_forward received key={key}, alive={_is_preview_popup_alive()}")
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
                
                log(f"handle_assistant_message: eval dispatching key '{mapped_key}' to popup")
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
    if getattr(editor, '_editor_assistant_setup', False):
        return
    editor._editor_assistant_setup = True
    
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
        assistant.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        # Permettre le focus clavier pour la navigation par flèches et raccourcis
        assistant.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        splitter.addWidget(assistant)
        splitter.setSizes([splitter.width(), 0])
        assistant.hide()  # Fermé par défaut — état mémorisé restauré dans on_editor_did_init
        
        editor._editor_assistant_web = assistant
        
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
        print("Editor assistant: restore state error:", e)

def toggle_assistant(editor):
    """Affiche ou masque le panneau assistant (cartes miroir).
    
    Si le panneau n'est pas encore configuré, il est créé d'abord.
    Le panneau commence caché et s'affiche sur Ctrl+Alt+B.
    Mémorise l'état pour la prochaine session.
    """
    if not getattr(editor, '_editor_assistant_setup', False):
        # Setup non encore fait : le faire maintenant
        _setup_assistant_ui(editor)
        QTimer.singleShot(150, lambda: _show_assistant(editor))
        return

    assistant = getattr(editor, '_editor_assistant_web', None)
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
    assistant = getattr(editor, '_editor_assistant_web', None)
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
    assistant = getattr(editor, '_editor_assistant_web', None)
    if assistant:
        try:
            assistant.objectName()  # lève RuntimeError si l'objet C++ est détruit
            if assistant.isVisible():
                QTimer.singleShot(200, lambda: update_assistant(editor))
        except RuntimeError:
            editor._editor_assistant_web = None

def init_assistant():
    gui_hooks.editor_did_init.append(on_editor_did_init)
    gui_hooks.editor_did_load_note.append(on_editor_did_load_note)
    gui_hooks.webview_did_receive_js_message.append(handle_assistant_message)
