"""
Anki EDN Linked Cards - Search and link cards.
Integrates features from 'Link Cards' addon (restored) and Multi-window support.
"""
import re
from typing import Any, Optional

import aqt
from aqt import mw, dialogs, gui_hooks
from aqt.gui_hooks import editor_did_init, webview_did_receive_js_message, editor_did_init_buttons
from aqt.editor import Editor
from aqt.qt import *
from aqt.utils import showInfo, tooltip
from aqt.browser import Browser
from aqt.clayout import CardLayout
from anki import hooks

from .logger import log, perf_log
from .edn_menu import register_module, register_action, get_shortcut

# Global reference
_current_editor = None
_active_dialog = None
_ct_click_guard: set = set()  # Déduplication: évite double ouverture browser

def _toggle_assistant_from_menu():
    """Bascule le panneau miroir pour l'éditeur courant (depuis le menu EDN)."""
    try:
        from .edn_assistant import get_active_editor, toggle_assistant
        editor = get_active_editor()
        if editor:
            toggle_assistant(editor)
    except Exception as e:
        log(f"toggle_assistant error: {e}")

def init_linked_cards():
    """Initializes the Linked Cards hooks."""
    # Enregistrer le module EN PREMIER pour que register_action_shortcut_only
    # trouve bien le module dans le registre (ordre critique)
    register_module("linked_cards", "Lier les cartes", "Lier des cartes entre elles")
    
    # Register shortcuts in shortcut config ONLY (NOT in the EDN menu dropdown)
    # NOTE: on N'utilise pas editor_did_init_shortcuts — uniquement QShortcut via
    # _setup_editor_window_shortcuts (dans on_editor_init) et setup_browser_menu.
    # Le double système causait des conflits depuis la mise à jour Anki.
    shortcut_search  = get_shortcut("linked_cards_search",  "Ctrl+Alt+L")
    shortcut_copy    = get_shortcut("linked_cards_copy",    "Ctrl+Alt+C")
    shortcut_liens   = get_shortcut("linked_cards_liens",   "Ctrl+Alt+B")
    shortcut_kbd_n   = get_shortcut("linked_cards_kbd_open",    "n")
    shortcut_kbd_p   = get_shortcut("linked_cards_kbd_preview", "p")
    shortcut_kbd_r   = get_shortcut("linked_cards_kbd_navigate", "r")
    shortcut_kbd_z   = get_shortcut("linked_cards_kbd_back",    "z")
    
    try:
        from .edn_menu import register_action_shortcut_only
        register_action_shortcut_only("linked_cards", "GUI", open_search_dialog_from_menu,
                                      shortcut=shortcut_search, shortcut_key="linked_cards_search")
        register_action_shortcut_only("linked_cards", "Copier NID", copy_nid_from_active_browser,
                                      shortcut=shortcut_copy, shortcut_key="linked_cards_copy")
        register_action_shortcut_only("linked_cards", "Liens", _toggle_assistant_from_menu,
                                      shortcut=shortcut_liens, shortcut_key="linked_cards_liens")
        # Touches simples — visibles et modifiables dans la config raccourcis
        register_action_shortcut_only("linked_cards", "Suivant", lambda: None,
                                      shortcut=shortcut_kbd_n,
                                      shortcut_key="linked_cards_kbd_open")
        register_action_shortcut_only("linked_cards", "Aperçu", lambda: None,
                                      shortcut=shortcut_kbd_p,
                                      shortcut_key="linked_cards_kbd_preview")
        register_action_shortcut_only("linked_cards", "Aperçu²", lambda: None,
                                      shortcut=shortcut_kbd_r,
                                      shortcut_key="linked_cards_kbd_navigate")
        register_action_shortcut_only("linked_cards", "Retour", lambda: None,
                                      shortcut=shortcut_kbd_z,
                                      shortcut_key="linked_cards_kbd_back")
    except Exception as e:
        log(f"Linked Cards : register_action_shortcut_only non disponible ({e})")
    
    # Editor hooks (PAS de editor_did_init_shortcuts — voir commentaire ci-dessus)
    editor_did_init.append(on_editor_init)
    editor_did_init_buttons.append(on_editor_buttons)
    webview_did_receive_js_message.append(on_js_message)
    
    # Reviewer/Preview hooks
    gui_hooks.webview_did_receive_js_message.append(on_js_message_reviewer)
    hooks.card_did_render.append(on_card_render)
    gui_hooks.webview_will_set_content.append(add_css)
    gui_hooks.state_shortcuts_will_change.append(_on_state_shortcuts_will_change)
    
    # Browser hooks (Menu & Context)
    gui_hooks.browser_menus_did_init.append(setup_browser_menu)
    gui_hooks.browser_will_show_context_menu.append(add_to_browser_context_menu)
    
    # Menu & Options Hooks
    try:
        from .edn_menu import register_interface
        register_interface("linked_cards", LinkedCardsConfigWidget)
    except Exception as e:
        log(f"Linked Cards : pas de prise en charge widget interface avancée ({e})")
        
    mw.addonManager.setWebExports(__name__, r'.+\.css')
    
    # Hook reviewer to intercept 'r' key before Anki processes it
    gui_hooks.reviewer_did_show_answer.append(_on_reviewer_show_answer)
    
    if hasattr(gui_hooks, 'previewer_will_show'):
        gui_hooks.previewer_will_show.append(_setup_previewer_shortcuts)
    
    
    # Protection crash changement de thème
    gui_hooks.theme_did_change.append(_on_theme_changed)
    
    # Init assistant
    try:
        from .edn_assistant import init_assistant
        init_assistant()
    except Exception as e:
        log(f"Failed to init assistant: {e}")
        
    # Patch Editor.call_after_note_saved (saveNow) to prevent JS ReferenceError
    try:
        _patch_editor_save_now()
    except Exception as e:
        log(f"Failed to patch Editor.saveNow: {e}")
        
    log("Linked Cards module initialized (Full Restoration).")


# on_editor_shortcuts supprimé — les raccourcis Ctrl+Alt+C/L sont gérés
# exclusivement via QShortcut dans _setup_editor_window_shortcuts (appelé depuis on_editor_init)
# et via setup_browser_menu pour le browser. Ce double système causait des conflits.




def add_css(web_content: aqt.webview.WebContent, context: Optional[Any]) -> None:
    addon_package = mw.addonManager.addonFromModule(__name__)
    base_url_css = f'/_addons/{addon_package}/user_files/clickable_cards.css'
    web_content.css.append(base_url_css)

def build_add_to_card_script():
    """Génère le script JS injecté dans chaque carte avec les raccourcis configurés."""
    from .edn_menu import get_shortcut
    kbd_open = get_shortcut("linked_cards_kbd_open", "n")
    kbd_preview = get_shortcut("linked_cards_kbd_preview", "p")
    kbd_navigate = get_shortcut("linked_cards_kbd_navigate", "r")
    kbd_back = get_shortcut("linked_cards_kbd_back", "z")
    vars_js = (
        "<script type=\"text/javascript\">"
        "window._ednKbdOpenCfg = '" + kbd_open + "';"  
        "window._ednKbdPreviewCfg = '" + kbd_preview + "';"  
        "window._ednKbdNavigateCfg = '" + kbd_navigate + "';"  
        "window._ednKbdBackCfg = '" + kbd_back + "';"  
        "</script>"
    )
    return vars_js + _add_to_card_script_body

# Alias pour compatibilité (utilisé dans on_card_render)
add_to_card_script = None  # will be built dynamically

_add_to_card_script_body = r"""
<script type="text/javascript">
// -- Nettoyage au changement de carte --
(function() {
    var _oldBox = document.getElementById("edn-preview-box");
    if (_oldBox) { _oldBox.style.display = "none"; }
    if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
    if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
    window._edn_hover_target = null;
    window._edn_parent_badge = null;
    window._ednPositionLocked = false;
    window._ednPreviewIndex = -1;
    window._ednPreviewHistory = [];
    // Réinitialiser pour permettre la réinstallation du handler 'r' sur chaque carte
    window._ednReviewerRKeyInit = false;

    // -- Fix badges sans onclick (sanitizer Anki supprime onclick/ondblclick) --
    // Ajouter un delegated click handler sur TOUT le document pour les .clickable_cards
    // qui n'ont pas d'onclick (= attribut supprimé par le sanitizer).
    if (!window._ednDelegatedClickInit) {
        window._ednDelegatedClickInit = true;
        document.addEventListener('click', function(e) {
            var t = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
            if (!t) return;
            // Extraire le NID
            var nid = '';
            if (t.dataset && t.dataset.nid) nid = t.dataset.nid;
            else {
                var s = t.querySelector && t.querySelector('.edn-nid');
                if (s) nid = s.textContent.trim();
                else nid = (t.innerText || '').trim().replace(/[^0-9]/g, '');
            }
            if (nid && nid.length > 9) {
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
            }
        });
    }
})();
function cards_ct_click(nid) {
    if(typeof pycmd !== 'undefined') pycmd("cards_ct_click" + nid);
}

// Extraction robuste du NID depuis un badge (data-nid > span.edn-nid > innerText)
function _ednGetNid(el) {
    if (!el) return '';
    if (el.dataset && el.dataset.nid) return el.dataset.nid;
    var hiddenSpan = el.querySelector && el.querySelector('.edn-nid');
    if (hiddenSpan) return hiddenSpan.textContent.trim();
    var oc = el.getAttribute('onclick');
    if (oc) {
        var m = oc.match(/\d{10,}/);
        if (m) return m[0];
    }
    return el.innerText ? el.innerText.replace(/[^0-9]/g, '') : '';
}

// -- Aperçu des cartes liées au survol -- (guard : enregistrement unique des listeners) --

// -- Raccourcis clavier badges liés (mis à jour à chaque carte) --
window._ednKbdOpen = window._ednKbdOpenCfg || 'n';
window._ednKbdPreview = window._ednKbdPreviewCfg || 'p';
window._ednKbdNavigate = window._ednKbdNavigateCfg || 'Tab';

if (!window._ednListenersAttached) {
    window._ednListenersAttached = true;

    function _findCardBadge(e) {
        if (e.composedPath) {
            var path = e.composedPath();
            for (var i = 0; i < path.length; i++) {
                var el = path[i];
                if (el.classList && el.classList.contains("clickable_cards")) {
                    return el;
                }
            }
        }
        return e.target && e.target.closest ? e.target.closest(".clickable_cards") : null;
    }

    window._ednHoverTimer = null;
    window._ednHideTimer = null;
    window._ednPositionLocked = false;

    // Forcer tabindex sur tous les badges
    function _ednSetupBadges() {
        document.querySelectorAll('.clickable_cards').forEach(function(el) {
            el.setAttribute('tabindex', '0');
        });
    }
    // Nettoyer les sélections SAUF le badge parent (bordure rouge)
    function _ednClearPreviewSelections() {
        var previewBox = document.getElementById('edn-preview-box');
        document.querySelectorAll('.clickable_cards').forEach(function(el) {
            // Conserver le badge parent (bordure rouge)
            if (el === window._edn_parent_badge) return;
            el.classList.remove('edn-selected-badge');
            el.style.outline = "";
            el.style.outlineOffset = "";
        });
    }
    function _ednClearAllSelections() {
        document.querySelectorAll('.clickable_cards').forEach(function(el) {
            el.classList.remove('edn-selected-badge');
            el.style.outline = "";
            el.style.outlineOffset = "";
        });
        window._edn_parent_badge = null;
    }
    _ednSetupBadges();
    setTimeout(_ednSetupBadges, 300);

    document.addEventListener('keydown', function(e) {
        var kbdOpen = window._ednKbdOpenCfg || 'n';
        var kbdPreview = window._ednKbdPreviewCfg || 'p';
        var kbdNavigate = window._ednKbdNavigateCfg || 'r';
        
        var previewBox = document.getElementById('edn-preview-box');
        var previewVisible = previewBox && previewBox.style.display !== 'none';
        
        // Ignorer CapsLock et comparer de façon insensible à la casse
        var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
        
        // P : cycle à travers les badges de la carte mère et afficher preview
        if (k === kbdPreview.toLowerCase() || k === 'p') {
            e.preventDefault();
            e.stopImmediatePropagation();
            var parentCards = Array.from(document.querySelectorAll('.clickable_cards')).filter(function(c) {
                return !previewBox || !previewBox.contains(c);
            });
            if (parentCards.length > 0) {
                window._ednPreviewIndex = (window._ednPreviewIndex + 1) % parentCards.length;
                var targetBadge = parentCards[window._ednPreviewIndex];
                _ednClearAllSelections();
                targetBadge.classList.add('edn-selected-badge');
                targetBadge.style.outline = "2px solid #ff4444"; // ROUGE
                targetBadge.style.outlineOffset = "2px";
                window._edn_hover_target = targetBadge;
                window._edn_parent_badge = targetBadge;
                window._ednPreviewHistory = [];
                var nid = _ednGetNid(targetBadge);
                window._ednPositionLocked = false;
                // Activer l'indicateur de scroll intelligent pour la preview
                window._edn_needs_scroll = true;
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + nid);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_hover:' + nid);
            }
            return;
        }
        
        // N : Cycle dans les badges de la preview UNIQUEMENT (jamais la carte mère)
        if (k === kbdOpen.toLowerCase() || k === 'n') {
            if (!previewVisible) return; // N ne fait rien hors preview
            e.preventDefault();
            e.stopImmediatePropagation();
            var targetCards = Array.from(previewBox.querySelectorAll('.clickable_cards'));
            if (targetCards.length > 0) {
                var idx = -1;
                for (var i = 0; i < targetCards.length; i++) {
                    if (targetCards[i].classList.contains('edn-selected-badge')) { idx = i; break; }
                }
                var next = e.shiftKey ? idx - 1 : idx + 1;
                if (idx === -1) {
                    next = e.shiftKey ? targetCards.length - 1 : 0;
                } else {
                    if (next < 0) next = targetCards.length - 1;
                    if (next >= targetCards.length) next = 0;
                }
                if (targetCards[next]) {
                    _ednClearPreviewSelections();
                    targetCards[next].classList.add('edn-selected-badge');
                    targetCards[next].style.outline = "2px solid #007acc"; // BLEU
                    targetCards[next].style.outlineOffset = "2px";
                }
            }
            return;
        }
        
        // R : Ouvrir la carte sélectionnée en preview OU scroller (preview uniquement)
        if (k === kbdNavigate.toLowerCase() || k === 'r') {
            if (!previewVisible) return; // R ne fait rien hors preview
            e.preventDefault();
            e.stopImmediatePropagation();
            
            var selectedNode = previewBox.querySelector('.edn-selected-badge');
            
            // Si un badge preview est sélectionné et différent du parent → ouvrir sa preview
            if (selectedNode && selectedNode !== window._edn_parent_badge) {
                var nid3 = _ednGetNid(selectedNode);
                // Sauvegarder l'historique pour Z
                if (window._edn_hover_target) {
                    var currentNid = _ednGetNid(window._edn_hover_target);
                    if (currentNid && window._ednPreviewHistory.indexOf(currentNid) === -1) {
                        window._ednPreviewHistory.push(currentNid);
                    }
                }
                window._edn_hover_target = selectedNode;
                // Activer le scroll intelligent aussi pour r (preview in preview)
                window._edn_needs_scroll = true;
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + nid3);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_hover:' + nid3);
                return;
            }
            
            // Sinon → scroller la preview
            if (!e.shiftKey) {
                previewBox.scrollTop += 80;
            } else {
                previewBox.scrollTop -= 80;
            }
            return;
        }
        
        // Z : Retour arrière dans la navigation preview
        var kbdBack = window._ednKbdBackCfg || 'z';
        if (k === kbdBack.toLowerCase() || k === 'z') {
            if (!previewVisible) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            if (window._ednPreviewHistory && window._ednPreviewHistory.length > 0) {
                var prevNid = window._ednPreviewHistory.pop();
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + prevNid);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_hover:' + prevNid);
            } else {
                // Pile vide → fermer la preview
                previewBox.style.display = "none";
                _ednClearAllSelections();
                window._edn_hover_target = null;
                window._ednPositionLocked = false;
                window._ednPreviewIndex = -1;
                // Retirer le spacer de scroll
                var spacer = document.getElementById('edn-scroll-spacer');
                if (spacer) spacer.style.height = '0px';
            }
            return;
        }
        
        // Escape : Masquer la preview et réinitialiser
        if (e.key === 'Escape') {
            if (previewVisible) {
                e.preventDefault();
                e.stopImmediatePropagation();
                previewBox.style.display = "none";
                _ednClearAllSelections();
                window._edn_hover_target = null;
                window._ednPositionLocked = false;
                window._ednPreviewIndex = -1;
                window._ednPreviewHistory = [];
                // Retirer le spacer de scroll
                var spacer = document.getElementById('edn-scroll-spacer');
                if (spacer) spacer.style.height = '0px';
            }
            return;
        }
        
        if (e.key === 'Enter') {
            var activeEnt = document.activeElement;
            if (activeEnt && activeEnt.classList.contains('clickable_cards')) {
                e.preventDefault();
                var nidEnt = activeEnt.innerText.trim();
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nidEnt);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nidEnt);
            }
        }
        if (e.key === 'Tab') {
            var allCards3 = Array.from(document.querySelectorAll('.clickable_cards'));
            if (!allCards3.length) return;
            e.preventDefault();
            var focused3 = document.activeElement;
            var idx3 = allCards3.indexOf(focused3);
            var next3 = e.shiftKey ? idx3 - 1 : idx3 + 1;
            if (idx3 === -1) {
                next3 = e.shiftKey ? allCards3.length - 1 : 0;
            } else {
                if (next3 < 0) next3 = allCards3.length - 1;
                if (next3 >= allCards3.length) next3 = 0;
            }
            if (allCards3[next3]) { allCards3[next3].focus(); }
        }
    }, true);




    window._ednGetOrCreateBox = function() {
        var box = document.getElementById("edn-preview-box");
        if (!box) {
            box = document.createElement("div");
            box.id = "edn-preview-box";
            box.style.cssText = "position:fixed;z-index:99999;display:none;max-height:90vh;overflow-y:auto;";
            document.body.appendChild(box);
            box.addEventListener("mouseenter", function() {
                if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
                if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
            });
            box.addEventListener("mouseleave", function() {
                // Ignorer si la preview est en train d'être mise à jour (évite le flash)
                if (window._ednPreviewUpdating) return;
                window._ednHideTimer = setTimeout(function() {
                    if (window._ednPreviewUpdating) return;
                    box.style.display = "none";
                    window._edn_hover_target = null;
                    window._edn_parent_badge = null;
                    window._ednPositionLocked = false;
                    window._ednHideTimer = null;
                    var spacer = document.getElementById('edn-scroll-spacer');
                    if (spacer) spacer.style.height = '0px';
                }, 80);
            });
        }
        return box;
    };

    // Delegated click handler on the preview box for reliable click-before-hover
    // Fires in capture phase (before hover), cancels any pending hover navigation.
    document.addEventListener('mousedown', function(e) {
        var target = _findCardBadge(e);
        if (!target) return;
        var box = document.getElementById('edn-preview-box');
        if (!box || !box.contains(target)) return;
        // Arm the click-wins flag as early as mousedown so hover timer cannot fire
        if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
        window._ednPreviewBadgeClicked = true;
    }, true);
    document.addEventListener('click', function(e) {
        var target = _findCardBadge(e);
        if (!target) return;
        var box = document.getElementById('edn-preview-box');
        if (!box || !box.contains(target)) return;
        // Click on a badge inside the preview box → open in browser
        e.preventDefault();
        e.stopImmediatePropagation();
        if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
        window._ednPreviewBadgeClicked = true;
        var nid = _ednGetNid(target);
        if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
        else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
    }, true); // capture phase = fires before any other click handler

    document.addEventListener("mouseover", function(e) {
        var target = _findCardBadge(e);
        if(target) {
            var box = document.getElementById('edn-preview-box');
            if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
            if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
            window._ednPreviewBadgeClicked = false;
            var nid = _ednGetNid(target);

            // Badge inside the preview box: hover-navigate with generous delay (250ms)
            // so the user has time to click before the preview changes.
            if (box && box.contains(target)) {
                window._ednHoverTimer = setTimeout(function() {
                    if (window._ednPreviewBadgeClicked) return; // click won the race
                    // Save history for Z-back
                    if (window._edn_hover_target) {
                        var currentNid = _ednGetNid(window._edn_hover_target);
                        if (!window._ednPreviewHistory) window._ednPreviewHistory = [];
                        if (currentNid && window._ednPreviewHistory.indexOf(currentNid) === -1) {
                            window._ednPreviewHistory.push(currentNid);
                        }
                    }
                    window._edn_hover_target = target;
                    if(typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + nid);
                    else if(typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_hover:' + nid);
                }, 200);
                return;
            }

            // Badge on the main card: normal hover preview
            window._ednHoverTimer = setTimeout(function() {
                if(typeof pycmd !== 'undefined') {
                    pycmd("cards_ct_hover:" + nid);
                    window._edn_hover_target = target;
                } else if(typeof bridgeCommand !== 'undefined') {
                    bridgeCommand("cards_ct_hover:" + nid);
                    window._edn_hover_target = target;
                }
            }, 150);
        }
    }, true);

    document.addEventListener("mouseout", function(e) {
        var target = _findCardBadge(e);
        if(target) {
            var box = window._ednGetOrCreateBox();
            if (box && box.contains(e.relatedTarget)) {
                return;
            }
            if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
            window._ednHideTimer = setTimeout(function() {
                if(box) box.style.display = "none";
                window._edn_hover_target = null;
                window._edn_parent_badge = null;
                window._ednPositionLocked = false;
                window._ednHideTimer = null;
                var spacer = document.getElementById('edn-scroll-spacer');
                if (spacer) spacer.style.height = '0px';
            }, 80);
        }
    }, true);

    document.addEventListener("click", function(e) {
        var box = document.getElementById("edn-preview-box");
        if(box && !box.contains(e.target)) {
            box.style.display = "none";
            window._edn_hover_target = null;
            window._edn_parent_badge = null;
            window._ednPositionLocked = false;
            var spacer = document.getElementById('edn-scroll-spacer');
            if (spacer) spacer.style.height = '0px';
        }
    });

    window._ednCurrentCaretBadge = null;
    document.addEventListener("selectionchange", function() {
        var active = document.activeElement;
        if (!active) return;
        
        var sel = null;
        if (active.shadowRoot) {
            sel = active.shadowRoot.getSelection();
        } else {
            sel = window.getSelection();
        }
        
        var badge = null;
        if (sel && sel.rangeCount > 0) {
            var range = sel.getRangeAt(0);
            var node = range.startContainer;
            var curr = node;
            while (curr && curr !== document.body) {
                if (curr.classList && curr.classList.contains('clickable_cards')) {
                    badge = curr;
                    break;
                }
                curr = curr.parentNode || (curr.getRootNode && curr.getRootNode().host);
            }
        }
        
        if (badge) {
            if (window._ednCurrentCaretBadge === badge) return;
            window._ednCurrentCaretBadge = badge;
            var nid = _ednGetNid(badge);
            if (nid) {
                var box = document.getElementById('edn-preview-box');
                if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
                if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
                
                window._ednHoverTimer = setTimeout(function() {
                    window._edn_hover_target = badge;
                    if (typeof pycmd !== 'undefined') pycmd("cards_ct_hover:" + nid);
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand("cards_ct_hover:" + nid);
                }, 150);
            }
        } else {
            if (window._ednCurrentCaretBadge) {
                window._ednCurrentCaretBadge = null;
                var box = document.getElementById('edn-preview-box');
                if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
                window._ednHideTimer = setTimeout(function() {
                    if (box) box.style.display = "none";
                    window._edn_hover_target = null;
                    window._edn_parent_badge = null;
                    window._ednPositionLocked = false;
                    window._ednHideTimer = null;
                    var spacer = document.getElementById('edn-scroll-spacer');
                    if (spacer) spacer.style.height = '0px';
                }, 80);
            }
        }
    });

    // -- Scroll-sync : repositionne le box quand la carte derriere defiles --
    // Note: on ignore les scrolls provenant du box lui-meme (sinon position derivee)
    document.addEventListener("scroll", function(e) {
        var box = document.getElementById("edn-preview-box");
        if (!box || box.style.display === "none" || !window._edn_hover_target) return;
        // Si le scroll vient de l'interieur du preview box, ne pas repositionner
        if (box === e.target || box.contains(e.target)) return;
        var insideBox = box.contains(window._edn_hover_target);
        if (!insideBox) {
            // Recalcul depuis la cible originale (externe au box)
            var rect = window._edn_hover_target.getBoundingClientRect();
            var topPos = rect.top;
            var boxWidth = box.offsetWidth || box.scrollWidth || 600;
            var leftPos = rect.right + 10;
            if (leftPos + boxWidth > window.innerWidth) { leftPos = window.innerWidth - boxWidth - 20; }
            if (leftPos < 0) leftPos = 10;
            box.style.top = topPos + "px";
            box.style.left = leftPos + "px";
        }
    }, true);

    window.show_edn_preview = function(html) {
        var box = window._ednGetOrCreateBox();
        // Extraire le <style> et l'injecter dans <head> (stable) pour éviter
        // les recalculs CSS globaux qui font disparaître les logos de la carte mère
        var tempDiv = document.createElement('div');
        tempDiv.innerHTML = html;
        var styleEl = tempDiv.querySelector('style');
        if (styleEl) {
            var headStyle = document.getElementById('edn-preview-head-style');
            if (!headStyle) {
                headStyle = document.createElement('style');
                headStyle.id = 'edn-preview-head-style';
                document.head.appendChild(headStyle);
            }
            headStyle.textContent = styleEl.textContent;
            styleEl.parentNode.removeChild(styleEl);
        }
        // Flag pour bloquer le mouseleave pendant la mise à jour du contenu
        window._ednPreviewUpdating = true;
        box.innerHTML = tempDiv.innerHTML;
        box.style.display = "block";
        box.style.overflowY = "auto";
        requestAnimationFrame(function() { window._ednPreviewUpdating = false; });
        
        // Fonction de positionnement intelligent avec ajustement vertical
        function _ednPositionBox(anchorRect) {
            if (anchorRect.top === 0 && anchorRect.bottom === 0) return;
            var boxWidth = box.offsetWidth || box.scrollWidth || 600;
            var leftPos = anchorRect.right + 10;
            if (leftPos + boxWidth > window.innerWidth) { leftPos = window.innerWidth - boxWidth - 20; }
            if (leftPos < 0) leftPos = 10;
            box.style.left = leftPos + "px";
            
            // Calculer la hauteur réelle du box (ou estimer)
            var boxHeight = box.offsetHeight || box.scrollHeight || 300;
            var topPos = anchorRect.top;
            
            // Si le box dépasse en bas de la fenêtre, repositionner
            if (topPos + boxHeight > window.innerHeight - 10) {
                // Option 1 : positionner en dessous du badge
                var belowPos = anchorRect.bottom + 5;
                if (belowPos + boxHeight <= window.innerHeight - 10) {
                    topPos = belowPos;
                } else {
                    // Option 2 : remonter le box pour qu'il rentre dans la fenêtre
                    topPos = Math.max(10, window.innerHeight - boxHeight - 10);
                }
            }
            // S'assurer que le top n'est jamais négatif
            if (topPos < 0) topPos = 10;
            box.style.top = topPos + "px";
        }
        
        // Ajuster la position : seulement si changement de cible majeure ou si non locké
        if (window._edn_parent_badge) {
            var rect = window._edn_parent_badge.getBoundingClientRect();
            _ednPositionBox(rect);
            window._ednPositionLocked = true;
        } else if (window._edn_hover_target && !window._ednPositionLocked) {
            var rect = window._edn_hover_target.getBoundingClientRect();
            _ednPositionBox(rect);
            window._ednPositionLocked = true;
        }
        
        var shouldScroll = window._edn_needs_scroll;
        window._edn_needs_scroll = false;

        // Étape 1 : ajustement rapide de la hauteur (avant MathJax)
        function _ednRecalcHeight(doScroll) {
            var contentHeight = box.scrollHeight;
            var maxAllowed = window.innerHeight * 0.9;
            box.style.maxHeight = Math.min(contentHeight + 10, maxAllowed) + "px";
            
            if (doScroll) {
                var rect = box.getBoundingClientRect();
                var overflow = rect.bottom - window.innerHeight + 10;
                if (overflow > 0) {
                    var spacer = document.getElementById('edn-scroll-spacer');
                    if (!spacer) {
                        spacer = document.createElement('div');
                        spacer.id = 'edn-scroll-spacer';
                        spacer.style.cssText = 'height:0px;width:100%;pointer-events:none;';
                        document.body.appendChild(spacer);
                    }
                    spacer.style.height = (overflow + 50) + 'px';
                    window.scrollBy({top: overflow, behavior: 'smooth'});
                }
            }
        }
        requestAnimationFrame(function() {
            _ednRecalcHeight(shouldScroll);
        });
        // Étape 1b : recalcul retardé pour les previews complexes (preview-in-preview)
        // Le CSS flex layout peut ne pas être résolu au premier requestAnimationFrame
        setTimeout(function() {
            _ednRecalcHeight(false);
        }, 150);
        
        // MathJax typesetting avec retry : on boucle jusqu'à ce que MathJax soit prêt
        // (max ~1.5 s, toutes les 100 ms), puis on retraite le contenu du box.
        (function _ednTryTypeset(attempt) {
            if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                try {
                    if (MathJax.typesetClear) { MathJax.typesetClear([box]); }
                    MathJax.typesetPromise([box]).then(function() {
                        // Réajuster la hauteur après rendu LaTeX
                        _ednRecalcHeight(shouldScroll);
                    }).catch(function(){});
                } catch(e) {}
            } else if (attempt < 15) {
                setTimeout(function() { _ednTryTypeset(attempt + 1); }, 100);
            }
        })(0);
        
        // Auto-sélectionner le premier badge dans la preview (sans toucher au badge parent)
        requestAnimationFrame(function() {
            var previewBadges = box.querySelectorAll('.clickable_cards');
            if (previewBadges.length > 0) {
                // Retirer les sélections bleues précédentes dans la preview
                previewBadges.forEach(function(b) {
                    b.classList.remove('edn-selected-badge');
                    b.style.outline = '';
                    b.style.outlineOffset = '';
                });
                previewBadges[0].classList.add('edn-selected-badge');
                previewBadges[0].style.outline = '2px solid #007acc';
                previewBadges[0].style.outlineOffset = '2px';
            }
        });
    };

} // fin guard _ednListenersAttached
</script>
"""



def _clean_kbd_html(html_text):
    """Nettoie le HTML des badges <kbd> : supprime les vides, corrige les imbriqués."""
    if not html_text:
        return html_text
        
    # 1. Supprimer les <kbd> vides (y compris ceux avec seulement espaces/br)
    empty_kbd = r'<kbd\s+class="clickable_cards"[^>]*>(?:\s|<br\s*/?>)*</kbd>'
    html_text = re.sub(empty_kbd, '', html_text)
    
    # 2. Corriger de manière extrêmement robuste les <kbd> imbriqués de n'importe quelle forme.
    # En cas d'imbrication, on extrait le texte extérieur en dehors de tout kbd/span interne
    # et on reconstruit un kbd badge unique et propre à la fin.
    def flatten_nested(match):
        outer_content = match.group(1)
        inner_match = re.search(r'<kbd\s+class="clickable_cards"[^>]*data-nid="(\d{10,})"[^>]*>.*?</kbd>', outer_content)
        if inner_match:
            nid = inner_match.group(1)
            # Retirer l'élément kbd interne du texte extérieur
            text_outside = re.sub(r'<kbd\s+class="clickable_cards"[^>]*>.*?</kbd>', '', outer_content)
            # Nettoyer toutes les balises span.edn-nid résiduelles ou imbriquées
            text_outside = re.sub(r'<span[^>]*class="edn-nid"[^>]*>.*?</span>', '', text_outside)
            text_outside = re.sub(r'<span[^>]*class="edn-nid"[^>]*>', '', text_outside)
            text_outside = text_outside.replace('</span>', '')
            # Nettoyer les sauts de ligne et espaces insécables
            text_outside = text_outside.replace('&nbsp;', ' ')
            # Supprimer les tirets séparateurs orphelins restants
            text_outside = re.sub(r'(?:\s*—\s*)+$', '', text_outside.strip())
            text_outside = re.sub(r'^(?:\s*—\s*)+', '', text_outside.strip())
            text_outside = text_outside.strip()
            
            # Retourner le badge aplati et propre
            if text_outside:
                return f'{text_outside} — <kbd class="clickable_cards" tabindex="0" data-nid="{nid}"><span class="edn-nid">{nid}</span></kbd>'
            else:
                return f'<kbd class="clickable_cards" tabindex="0" data-nid="{nid}"><span class="edn-nid">{nid}</span></kbd>'
        return match.group(0)

    # Aplatir récursivement les kbd imbriqués
    html_text = re.sub(r'<kbd\s+class="clickable_cards"[^>]*>(.*?)</kbd>', flatten_nested, html_text, flags=re.DOTALL)
    
    # 3. Supprimer les <kbd> vides résiduels (après nettoyage des imbriqués)
    html_text = re.sub(empty_kbd, '', html_text)
    
    # 4. Nettoyer les <br> orphelins en début de contenu ou après un <br><kbd vide>
    html_text = re.sub(r'(?:<br\s*/?>\s*)+(?=<br\s*/?>)', '', html_text)
    
    return html_text

def on_card_render(output, context):
    script = build_add_to_card_script()
    # Nettoyer les badges <kbd> vides et imbriqués
    output.question_text = _clean_kbd_html(output.question_text)
    output.answer_text = _clean_kbd_html(output.answer_text)
    output.question_text += script
    output.answer_text += script


def _on_state_shortcuts_will_change(state: str, shortcuts: list):
    """
    Intercepte les raccourcis du reviewer pour gérer les touches 'r' et 'z'.
    R/N/Z ne doivent agir QUE si la preview EDN est ouverte.
    Sinon → comportement Anki par défaut.
    """
    if state != "review":
        return
    
    kbd_navigate = get_shortcut("linked_cards_kbd_navigate", "r")
    nav_key = kbd_navigate
    
    if nav_key.lower() == "r":
        # Trouver et retirer le raccourci 'r'/'R' par défaut (replay audio)
        original_r_handlers = [
            s for s in shortcuts 
            if (isinstance(s[0], str) and s[0].lower() == 'r') or 
               (hasattr(s[0], 'toString') and s[0].toString().lower() == 'r')
        ]
        for handler in original_r_handlers:
            if handler in shortcuts:
                shortcuts.remove(handler)
        
        def _edn_r_key_handler():
            """Handler pour 'r'/'R' : preview EDN en priorité, replay audio en fallback.
            
            IMPORTANT: Qt intercepte 'r' AVANT le webview — le JS keydown handler
            ne reçoit jamais la touche. On doit donc exécuter l'action 'r' directement
            depuis Python via eval() quand la preview est visible.
            """
            if not mw.reviewer or not mw.reviewer.web:
                return
            
            # JS qui vérifie la preview ET exécute l'action 'r' d'un coup (pas de callback)
            r_action_js = """
            (function() {
                var previewBox = document.getElementById('edn-preview-box');
                var previewVisible = previewBox && previewBox.style.display !== 'none';
                if (!previewVisible) return 'replay';
                
                var selectedNode = previewBox.querySelector('.edn-selected-badge');
                
                if (selectedNode && selectedNode !== window._edn_parent_badge) {
                    var nid = selectedNode.dataset.nid || selectedNode.innerText.trim();
                    // Sauvegarder l'historique pour Z
                    if (window._edn_hover_target) {
                        var currentNid = window._edn_hover_target.dataset.nid || (window._edn_hover_target.innerText ? window._edn_hover_target.innerText.trim() : '');
                        if (!window._ednPreviewHistory) window._ednPreviewHistory = [];
                        if (currentNid && window._ednPreviewHistory.indexOf(currentNid) === -1) {
                            window._ednPreviewHistory.push(currentNid);
                        }
                    }
                    window._edn_hover_target = selectedNode;
                    // Activer le scroll intelligent pour r (preview in preview)
                    window._edn_needs_scroll = true;
                    if (typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + nid);
                    return 'navigated';
                }
                
                // Pas de badge sélectionné → scroller
                previewBox.scrollTop += 80;
                return 'scrolled';
            })();
            """
            
            def _js_callback(result):
                if result and result.strip("'\"") == 'replay':
                    # Pas de preview → comportement Anki par défaut (replay audio)
                    for key, handler in original_r_handlers:
                        try:
                            handler()
                        except:
                            pass
            
            mw.reviewer.web.evalWithCallback(r_action_js, _js_callback)
        
        # Enregistrer en minuscules ET majuscules pour parer au CapsLock
        shortcuts.append(('r', _edn_r_key_handler))
        shortcuts.append(('R', _edn_r_key_handler))
    
    # Ctrl+Alt+C dans le reviewer : copier le NID de la carte en cours
    shortcut_copy = get_shortcut("linked_cards_copy", "Ctrl+Alt+C")
    def _copy_nid_from_reviewer():
        """Copie le NID de la carte en cours de révision dans le clipboard."""
        if not mw.reviewer or not mw.reviewer.card:
            return
        card = mw.reviewer.card
        nid = str(card.nid)
        recto = ""
        try:
            note = card.note()
            recto = strip_html(note.fields[0])[:240] if note.fields else ""
        except:
            pass
        recto = recto or "[Vide]"
        recto_escaped = recto.replace('"', '&quot;').replace("'", '&#39;')
        html = (
            f'{recto_escaped} — '
            f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid}"'
            f'><span class="edn-nid">{nid}</span></kbd>&nbsp;'
        )
        mime = QMimeData()
        mime.setText(f"{recto} — {nid}")
        mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)
        tooltip(f"NID copié (reviewer): {recto} — {nid}")
    
    shortcuts.append((shortcut_copy, _copy_nid_from_reviewer))


def _on_reviewer_show_answer(card):
    """
    Appelé quand l'answer d'une carte est affichée dans le reviewer.
    Le handler principal dans _add_to_card_script_body gère déjà N/R/Z/P/Escape
    en phase capture. On se contente ici de ré-initialiser le flag.
    """
    if not mw.reviewer or not mw.reviewer.web:
        return
    # Le script principal gère tout via le guard _ednListenersAttached
    # Pas besoin d'un second handler ici — ça causait des conflits

def _on_theme_changed():
    """Protection contre le crash lors du changement de thème.
    
    Le hook theme_did_change appelle page().setBackgroundColor() sur toutes
    les AnkiWebView, y compris la preview popup qui peut avoir été détruite.
    On la nettoie préventivement.
    """
    global _active_dialog
    if _active_dialog:
        try:
            if hasattr(_active_dialog, '_preview_web') and _active_dialog._preview_web:
                try:
                    _active_dialog._preview_web.cleanup()
                except:
                    pass
                _active_dialog._preview_web = None
            if hasattr(_active_dialog, '_preview_dlg') and _active_dialog._preview_dlg:
                try:
                    _active_dialog._preview_dlg.hide()
                    _active_dialog._preview_dlg.deleteLater()
                except:
                    pass
                _active_dialog._preview_dlg = None
        except:
            pass

@perf_log
def on_js_message_reviewer(handled, message, context):
    global _ct_click_guard
    if message.startswith("cards_ct_click"):
        if isinstance(context, CardLayout):
            tooltip("Liens désactivés dans l'éditeur de type.")
            return (True, None)
        nid = message.replace("cards_ct_click", "")
        # Déduplication : éviter double ouverture (ex: clic panneau miroir)
        if nid in _ct_click_guard:
            return (True, None)
        _ct_click_guard.add(nid)
        from aqt.qt import QTimer
        QTimer.singleShot(400, lambda n=nid: _ct_click_guard.discard(n))
        browser = dialogs.open("Browser", mw)
        browser.search_for(f"nid:{nid}")
        return (True, None)
    elif message.startswith("cards_ct_hover:"):
        nid = message.split(":")[1]
        
        try:
            import json, re
            try:
                note = mw.col.get_note(int(nid))
                cards = note.cards()
                if not cards:
                    raise Exception()
                card = cards[0]
                
                rendered_a = card.answer()
                
                isolated = re.sub(r'\bid=["\'](.*?)["\']', r'id="edn_preview_\1"', rendered_a)
                isolated = re.sub(r"toggle\(['\"](.*?)['\"]\)", r"toggle('edn_preview_\1')", isolated)
                isolated = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', isolated, flags=re.IGNORECASE)
                isolated = re.sub(r'<style\b[^>]*>.*?</style>', '', isolated, flags=re.IGNORECASE | re.DOTALL)
                
                # Supprimer le bloc de statut FSRS qui ajoute énormément d'espace en bas
                isolated = re.sub(r'(?:<br>\s*)?<span[^>]*id=["\'][^"\']*FSRS[^"\']*["\'][^>]*>.*?</span>', '', isolated, flags=re.IGNORECASE | re.DOTALL)
                
                # Forcer l'affichage SEULEMENT des sections (pour éviter de révéler les métadonnées/licences cachées)
                isolated = re.sub(r'(<div[^>]*class=["\'][^"\']*section[^"\']*["\'][^>]*style=["\'])display:\s*none;?([^"\']*["\'])', r'\1display: flex !important;\2', isolated, flags=re.IGNORECASE)

                # Nettoyer les balises <hr> et compresser les sauts de ligne multiples (bug de gestion de l'espace)
                isolated = re.sub(r'<hr[^>]*>', '', isolated, flags=re.IGNORECASE)
                isolated = re.sub(r'(?:<br\s*/?>\s*){2,}', '<br>', isolated, flags=re.IGNORECASE)

                # Compresser et nettoyer les <br> orphelins ou inutiles dans cartesLiees
                def _clean_cartes_liees(match):
                    inner = match.group(2)
                    # Supprimer les br consécutifs ou séparés par des espaces
                    inner = re.sub(r'(?:\s*<br\s*/?>\s*)+', '<br>', inner, flags=re.IGNORECASE)
                    # Supprimer les br au tout début
                    inner = re.sub(r'^\s*(?:<br\s*/?>\s*)+', '', inner, flags=re.IGNORECASE)
                    # Supprimer les br et espaces insécables à la toute fin
                    inner = re.sub(r'(?:<br\s*/?>\s*|&nbsp;\s*)+$', '', inner, flags=re.IGNORECASE)
                    return f"{match.group(1)}{inner}</div>"
                isolated = re.sub(r'(<div[^>]*class=["\'][^"\']*cartesLiees[^"\']*["\'][^>]*>)(.*?)</div>', _clean_cartes_liees, isolated, flags=re.IGNORECASE | re.DOTALL)

                # --- BLACKLIST DE SECTIONS DANS L'APERÇU ---
                config = mw.addonManager.getConfig(__name__) or {}
                hidden_sections_config = config.get("hidden_preview_sections", [
                    "sourcesMegaContainer",
                    "commentsMegaContainer",
                    "tagsMegaContainer",
                ])
                # Le HTML d'aperçu préfixe automatiquement les IDs par edn_preview_
                selectors = [f"#edn_preview_{sec}" for sec in hidden_sections_config]
                # Ces sélecteurs ID sont uniques et ne risquent pas de polluer la carte principale
                selectors.extend(["#edn_preview_FSRS_status"])
                id_css = ", ".join(selectors) + " { display: none !important; }" if selectors else ""
                
                model_css = note.note_type().get('css', '')
                icon_css_blocks = []
                for match in re.finditer(r'([^{}]+)\s*\{([^}]*background(?:-image)?\s*:[^}]+)\}', model_css, re.IGNORECASE):
                    css_selectors = match.group(1).strip()
                    css_body = match.group(2).strip()
                    if 'Icon' in css_selectors:
                        scoped_selectors = ", ".join([f"#edn-preview-box {s.strip()}" for s in css_selectors.split(",")])
                        icon_css_blocks.append(f"{scoped_selectors} {{{css_body}}}")
                if icon_css_blocks:
                    id_css += "\n" + "\n".join(icon_css_blocks)
                # Ces sélecteurs génériques DOIVENT être scopés à #edn-preview-box
                scoped_css = """
                    #edn-preview-box {
                        max-width: 480px !important;
                        width: fit-content !important;
                        min-width: 250px !important;
                    }
                    #edn-preview-box span[id*='FSRS'] { display: none !important; }
                    #edn-preview-box hr { display: none !important; }
                    #edn-preview-box .edn-preview-isolated > *:last-child { margin-bottom: 0 !important; padding-bottom: 0 !important; }
                    #edn-preview-box .edn-preview-isolated { overflow: hidden; padding: 0 !important; }
                    #edn-preview-box .card { background: transparent !important; background-color: transparent !important; margin: 0 !important; padding: 5px !important; font-size: 18px !important; line-height: 1.35 !important; max-width: 100% !important; text-align: left; }
                    #edn-preview-box a { font-size: 0.85em !important; }
                    #edn-preview-box .section { display: flex !important; margin: 1px 0 !important; padding: 0 !important; margin-left: 0 !important; margin-right: 0 !important; width: 100% !important; min-height: 0 !important; border-width: 1.5px !important; }
                    #edn-preview-box div[class*='section'] { display: flex !important; }
                    #edn-preview-box .items { margin-left: 8px !important; margin-right: 8px !important; padding: 1px 0 !important; min-height: 0 !important; white-space: normal !important; word-break: break-word !important; }
                    #edn-preview-box .items ul, #edn-preview-box .items ol { padding-top: 2px !important; padding-bottom: 2px !important; margin-top: 0 !important; margin-bottom: 0 !important; padding-left: 18px !important; }
                    #edn-preview-box .items li { padding-top: 0px !important; padding-bottom: 0px !important; margin-top: 0 !important; margin-bottom: 0 !important; }
                    #edn-preview-box .items ul ul, #edn-preview-box .items ol ol, #edn-preview-box .items ul ol, #edn-preview-box .items ol ul { padding-top: 0 !important; padding-bottom: 0 !important; margin-top: 0 !important; margin-bottom: 0 !important; }
                    #edn-preview-box .bar { flex: 0 0 31px !important; width: 31px !important; min-height: 31px !important; margin: 0 !important; padding: 0 !important; background-size: 23px !important; border-right-width: 1px !important; }
                    #edn-preview-box .barHider { display: none !important; }
                    #edn-preview-box br { display: block !important; content: '' !important; margin: 2px 0 !important; }
                    #edn-preview-box .edn-nid { display: inline !important; }
                    #edn-preview-box .clickable_cards {
                        font-size: 14px !important;
                        height: auto !important;
                        line-height: 14px !important;
                        padding: 3px 5px !important;
                        margin: 3px !important;
                        cursor: pointer;
                    }
                    #edn-preview-box .items.cartesLiees {
                        flex-direction: row !important;
                        flex-wrap: wrap !important;
                        align-items: center !important;
                        justify-content: flex-start !important;
                        margin-left: 0 !important;
                    }
                    #edn-preview-box .items.cartesLiees br {
                        flex-basis: 100% !important;
                        height: 0 !important;
                        margin: 0 !important;
                        display: block !important;
                        content: "" !important;
                    }
                    #edn-preview-box .items > span ul, #edn-preview-box .items > span ol {
                        padding-left: 18px !important; margin: 0 !important;
                        padding-top: 0 !important; padding-bottom: 0 !important;
                    }
                    #edn-preview-box .items li > ul, #edn-preview-box .items li > ol {
                        padding-left: 14px !important; margin: 0 !important;
                        padding-top: 0 !important; padding-bottom: 0 !important;
                    }
                    #edn-preview-box .items > span { display: block !important; }
                    #edn-preview-box .items.answer { padding: 1px 0 !important; }
                """
                
                html = f"""
                <style>
                    {id_css}
                    {scoped_css}
                </style>
                <div class="edn-preview-isolated card" style="pointer-events: auto; overflow: hidden;">
                    {isolated}
                </div>
                """
            except:
                html = "<div style='padding:10px; font-style:italic;'>Lien mort.</div>"
            
            js_call = f"if(typeof show_edn_preview !== 'undefined') show_edn_preview({json.dumps(html)});"
            web = None
            if hasattr(context, 'eval'):
                web = context
            elif hasattr(context, 'web') and hasattr(context.web, 'eval'):
                web = context.web
            elif hasattr(context, '_web') and hasattr(context._web, 'eval'):
                web = context._web
            elif hasattr(context, 'previewWeb') and hasattr(context.previewWeb, 'eval'):
                web = context.previewWeb
            elif mw.reviewer and getattr(mw.reviewer, 'web', None):
                web = mw.reviewer.web
                
            if web:
                web.eval(js_call)
            else:
                from .logger import log
                log("Hover preview error: Could not find webview to execute JS")
        except Exception as e:
            from .logger import log
            log(f"Hover preview error: {e}")
        return (True, None)
    return handled

# --- Browser Integration (Restored) ---

def setup_browser_menu(browser: Browser):
    """Restore the 'Linking' menu actions locally to browser window.
    
    IMPORTANT: on n'installe PAS de QShortcut ici pour Ctrl+Alt+C/L.
    Ces raccourcis sont installés via _setup_editor_window_shortcuts() quand un éditeur
    s'ouvre dans le browser (on_editor_init → _setup_editor_window_shortcuts).
    Cela évite la duplication de QShortcut sur la même fenêtre (ambiguïté Qt → aucun ne s'active).
    """
    shortcut_copy = get_shortcut("linked_cards_copy", "Ctrl+Alt+C")
    shortcut_search = get_shortcut("linked_cards_search", "Ctrl+Alt+L")
    shortcut_liens = get_shortcut("linked_cards_liens", "Ctrl+Alt+B")
    
    action_copy = QAction("📋 Copier NID", browser)
    action_copy.triggered.connect(lambda: _copy_nid_smart(browser))
    
    action_search = QAction("🔗 Rechercher Liens", browser)
    action_search.triggered.connect(lambda: _open_search_smart(browser))
    
    action_focus_liens = QAction("🔗 Aller au champ Liens", browser)
    action_focus_liens.triggered.connect(lambda: _focus_liens_browser(browser))
    
    # Save reference for context menu
    browser.edn_copy_nid_action = action_copy
    browser.edn_search_action = action_search
    
    # Add to browser window (no shortcut on QAction to avoid conflicts)
    browser.addAction(action_copy)
    browser.addAction(action_search)
    browser.addAction(action_focus_liens)
    
    # Un seul QShortcut sur le browser, avec comportement contextuel
    sc_copy = QShortcut(QKeySequence(shortcut_copy), browser)
    sc_copy.setContext(Qt.ShortcutContext.WindowShortcut)
    sc_copy.activated.connect(lambda: _copy_nid_smart(browser))
    
    sc_search = QShortcut(QKeySequence(shortcut_search), browser)
    sc_search.setContext(Qt.ShortcutContext.WindowShortcut)
    sc_search.activated.connect(lambda: _open_search_smart(browser))
    
    sc_liens = QShortcut(QKeySequence(shortcut_liens), browser)
    sc_liens.setContext(Qt.ShortcutContext.WindowShortcut)
    sc_liens.activated.connect(lambda: _focus_liens_browser(browser))
    
    # Keep references to prevent GC
    browser._edn_sc_copy = sc_copy
    browser._edn_sc_search = sc_search
    browser._edn_sc_liens = sc_liens
    
    _setup_browser_preview_shortcuts(browser)

def _add_preview_shortcuts(window, webview):
    from aqt.qt import QShortcut, QKeySequence, Qt
    from .edn_menu import get_shortcut
    kbd_open = get_shortcut("linked_cards_kbd_open", "n")
    kbd_preview = get_shortcut("linked_cards_kbd_preview", "p")
    kbd_navigate = get_shortcut("linked_cards_kbd_navigate", "r")
    kbd_back = get_shortcut("linked_cards_kbd_back", "z")

    def _dispatch(key):
        if webview and hasattr(webview, "eval"):
            try:
                if hasattr(webview, "isVisible") and not webview.isVisible(): return
                webview.eval(f"var e = new KeyboardEvent('keydown', {{'key': '{key}'}}); document.dispatchEvent(e);")
            except Exception: pass

    for key in [kbd_open, kbd_preview, kbd_navigate, kbd_back]:
        if not key: continue
        try:
            sc = QShortcut(QKeySequence(key), window)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda k=key: _dispatch(k))
            if not hasattr(window, '_edn_sc_preview_keys'):
                window._edn_sc_preview_keys = []
            window._edn_sc_preview_keys.append(sc)
        except Exception: pass

def _setup_browser_preview_shortcuts(browser):
    try:
        if hasattr(browser.form, 'previewWeb'):
            _add_preview_shortcuts(browser, browser.form.previewWeb)
    except Exception: pass

def _setup_previewer_shortcuts(previewer):
    try:
        dialog = getattr(previewer, '_dialog', None)
        if not dialog and hasattr(previewer, 'widget'): dialog = previewer.widget()
        if not dialog: return
        web = getattr(previewer, '_web', None)
        if not web and hasattr(previewer, 'web'): web = previewer.web()
        if not web: return
        _add_preview_shortcuts(dialog, web)
    except Exception: pass



def _focus_liens_browser(browser: Browser):
    """Focalise le champ 'Liens'/'Cartes liées' dans l'éditeur du browser."""
    if hasattr(browser, 'editor') and browser.editor:
        _focus_liens_field(browser.editor)
    else:
        tooltip("Aucun éditeur actif dans l'explorateur.")

def _copy_nid_smart(browser: Browser):
    """Copie le NID : depuis l'éditeur si actif, sinon depuis la sélection browser."""
    # Préférence : si l'éditeur a le focus et une note, copier son NID
    if hasattr(browser, 'editor') and browser.editor and browser.editor.note:
        note = browser.editor.note
        if note and note.id:
            QApplication.clipboard().setText(str(note.id))
            tooltip(f"NID copié (éditeur): {note.id}")
            return
    # Fallback : carte sélectionnée dans la liste browser
    copy_nid_from_browser(browser)

def _open_search_smart(browser: Browser):
    """Ouvre le GUI de recherche depuis l'éditeur du browser."""
    if hasattr(browser, 'editor') and browser.editor:
        open_search_dialog(browser.editor)
    else:
        tooltip("Ouvrez une carte en édition d'abord.")

def copy_nid_from_browser(browser: Browser):
    cards = browser.selected_cards()
    if not cards:
        tooltip("Aucune carte sélectionnée.")
        return
    nid = browser.col.get_card(cards[0]).nid
    QApplication.clipboard().setText(str(nid))
    tooltip(f"NID copié: {nid}")

def copy_nid_from_editor(editor: Editor):
    if editor.note and editor.note.id:
        nid = str(editor.note.id)
        recto = ""
        try:
            recto = strip_html(editor.note.fields[0])[:240] if editor.note.fields else ""
        except:
            pass
        recto = recto or "[Vide]"
        recto_escaped = recto.replace('"', '&quot;').replace("'", '&#39;')
        # HTML riche : recto visible dans le badge, NID uniquement dans data-nid + span caché
        # Le texte visible du <kbd> est le recto (pas le NID) pour éviter la duplication
        # quand le HTML est collé puis converti en texte brut par Anki
        html = (
            f'{recto_escaped} — '
            f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid}"'
            f'><span class="edn-nid">{nid}</span></kbd>&nbsp;'
        )
        mime = QMimeData()
        mime.setText(f"{recto} — {nid}")
        mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)
        tooltip(f"NID copié: {recto} — {nid}")

def add_to_browser_context_menu(browser: Browser, menu: QMenu):
    if hasattr(browser, 'edn_copy_nid_action'):
        menu.addAction(browser.edn_copy_nid_action)

# --- Editor Integration ---

def on_editor_buttons(buttons, editor):
    # register_module déjà appelé dans init_linked_cards — pas besoin de répéter
    shortcut_legacy = get_shortcut("linked_cards_search", "Ctrl+Alt+L")
    shortcut_liens = get_shortcut("linked_cards_liens", "Ctrl+Alt+B")
    
    btn_link = editor.addButton(
        icon=None,
        cmd="edn_linked_search",
        func=lambda e=editor: handle_editor_button(e),
        tip=f"Lier carte / Rechercher ({shortcut_legacy})",
        label="Lier",
    )
    buttons.append(btn_link)
    
    # IMPORTANT: on N'utilise PAS func= pour ce bouton car Anki déclenche saveNow()
    # avant d'appeler func=, ce qui bloque le thread principal.
    # On N'utilise PAS non plus keys= car cela crée un doublon QShortcut sur la fenêtre
    # qui entre en conflit avec le QShortcut de _setup_editor_window_shortcuts → ambiguïté Qt.
    # Le clic est géré côté JS via pycmd('edn_toggle_assistant') injecté dans _do_editor_init.
    btn_liens = editor.addButton(
        icon=None,
        cmd="edn_focus_liens",
        func=lambda e=editor: None,  # noop — le clic JS envoie pycmd directement
        tip=f"Afficher/Masquer le panneau Miroirs ({shortcut_liens})",
        label="Liens",
        # pas de keys= ici (doublon QShortcut → ambiguïté Qt)
    )
    buttons.append(btn_liens)
    
    return buttons

def _focus_liens_field(editor):
    """Affiche ou masque le panneau assistant (cartes miroir) après avoir sauvegardé la note."""
    try:
        def on_saved():
            from .edn_assistant import toggle_assistant
            toggle_assistant(editor)
        editor.saveNow(on_saved)
    except Exception as e:
        log(f"toggle_assistant error: {e}")

def handle_editor_button(editor):
    """Smart Link: Direct link if selection is NID, else Search GUI."""
    editor.web.evalWithCallback("""
        (function() {
            // Nettoyer tout ancien marker
            let markers = Array.from(document.querySelectorAll('[id="edn-cursor-marker"]'));
            document.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {
                if (el.shadowRoot) {
                    markers = markers.concat(Array.from(el.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                    el.shadowRoot.querySelectorAll('anki-editable').forEach(inner => {
                       if (inner.shadowRoot) markers = markers.concat(Array.from(inner.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                    });
                }
            });
            markers.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });

            window._ednSavedRange = null;
            let textValue = '';
            let isFullField = false;
            let active = document.activeElement;
            let sel = null;
            // Chercher la sélection dans TOUS les shadow roots (pas seulement activeElement)
            // pour que cursorin fonctionne quelle que soit la box active
            function _findActiveSel() {
                // Essayer d'abord activeElement et son shadowRoot
                if (active && active.shadowRoot) {
                    var s = active.shadowRoot.getSelection();
                    if (s && s.rangeCount > 0) return s;
                }
                // Chercher dans tous les anki-editor / anki-editable shadow roots
                var allHosts = Array.from(document.querySelectorAll('anki-editor, anki-editable'));
                for (var hi = 0; hi < allHosts.length; hi++) {
                    var h = allHosts[hi];
                    if (h.shadowRoot) {
                        var s2 = h.shadowRoot.getSelection ? h.shadowRoot.getSelection() : null;
                        if (s2 && s2.rangeCount > 0) return s2;
                        // Chercher les anki-editable imbriqués
                        var inner = Array.from(h.shadowRoot.querySelectorAll('anki-editable'));
                        for (var ii = 0; ii < inner.length; ii++) {
                            if (inner[ii].shadowRoot) {
                                var s3 = inner[ii].shadowRoot.getSelection ? inner[ii].shadowRoot.getSelection() : null;
                                if (s3 && s3.rangeCount > 0) return s3;
                            }
                        }
                    }
                }
                return window.getSelection();
            }
            sel = _findActiveSel();

            function _insertMarkerAtCursor(selection) {
                if (!selection || selection.rangeCount === 0) return;
                try {
                    let r = selection.getRangeAt(0).cloneRange();
                    r.deleteContents();
                    let m = document.createElement('span');
                    m.id = 'edn-cursor-marker';
                    r.insertNode(m);
                } catch(e) {}
            }

            if (sel && sel.rangeCount > 0) {
                window._ednSavedRange = sel.getRangeAt(0).cloneRange();
                textValue = sel.toString();
                _insertMarkerAtCursor(sel);
            }

            if (!textValue.trim() && active) {
                let editable = null;
                if (active.tagName === 'ANKI-EDITABLE') editable = active;
                else if (active.shadowRoot) {
                    let activeInner = active.shadowRoot.activeElement;
                    if (activeInner && (activeInner.tagName === 'ANKI-EDITABLE' || activeInner.isContentEditable)) {
                        editable = activeInner;
                    }
                }
                if (!editable && window._ednSavedRange) {
                    let node = window._ednSavedRange.commonAncestorContainer;
                    while (node) {
                        if (node.tagName === 'ANKI-EDITABLE' || (node.classList && node.classList.contains('field'))) {
                            editable = node;
                            break;
                        }
                        node = node.parentNode || (node.getRootNode && node.getRootNode().host);
                    }
                }

                if (editable) {
                    let inner = (editable.innerText || editable.textContent || '').trim();
                    if (/^\\d{10,}$/.test(inner)) {
                        textValue = inner;
                        isFullField = true;
                        // Placer le curseur à la fin du champ
                        let range = document.createRange();
                        range.selectNodeContents(editable);
                        range.collapse(false);
                        if (sel) { sel.removeAllRanges(); sel.addRange(range); }
                        window._ednSavedRange = range.cloneRange();
                        
                        let cleanOld = Array.from(document.querySelectorAll('[id="edn-cursor-marker"]'));
                        document.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {
                            if (el.shadowRoot) {
                                cleanOld = cleanOld.concat(Array.from(el.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                                el.shadowRoot.querySelectorAll('anki-editable').forEach(inner => {
                                   if (inner.shadowRoot) cleanOld = cleanOld.concat(Array.from(inner.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                                });
                            }
                        });
                        cleanOld.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });
                        
                        _insertMarkerAtCursor(sel || { rangeCount: 1, getRangeAt: () => range });
                    }
                    // Champ vide : le marker a déjà été placé (ou il sera à la fin par défaut)
                }
            }
            return JSON.stringify({text: textValue.toString(), isFullField: isFullField});
        })();
    """, lambda res: _on_selection_check(editor, res))

def _on_selection_check(editor, result):
    import json
    try:
        data = json.loads(result)
        text = data.get("text", "")
        full_field = data.get("isFullField", False)
    except:
        text = str(result).strip() if result else ""
        full_field = False
        
    matches = re.findall(r"\d+", text)
    
    if matches:
        nid = matches[0]
        if len(nid) > 9: # timestamp check roughly
            # Vérifier le contexte de la ligne pour décider du comportement :
            # - Ligne vide / NID seul → incruster recto automatiquement
            # - Ligne avec dash "—" → juste transformer en hyperlien (pas de recto)
            # - Ligne avec du texte (sans dash) → incruster recto
            def _check_line_context(line_info):
                try:
                    info = json.loads(line_info) if isinstance(line_info, str) else {}
                except:
                    info = {}
                has_dash = info.get('hasDash', False)
                is_empty = info.get('isEmpty', False)
                
                if has_dash:
                    # La ligne a déjà un titre avec dash → juste le NID en hyperlien
                    create_link_for_nid(editor, nid, with_recto=False)
                else:
                    # Ligne vide ou sans dash → incruster le recto
                    create_link_for_nid(editor, nid, with_recto=True)
            
            # Interroger le JS pour connaître le contexte de la ligne
            try:
                editor.web.evalWithCallback(r"""
                    (function() {
                        var result = {hasDash: false, isEmpty: false};
                        try {
                            var active = document.activeElement;
                            var sel = null;
                            if (active && active.shadowRoot) sel = active.shadowRoot.getSelection();
                            else sel = window.getSelection();
                            
                            var range = (window._ednSavedRange) ? window._ednSavedRange : (sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null);
                            if (range) {
                                var container = range.startContainer;
                                var editable = container;
                                while (editable && editable.tagName !== 'ANKI-EDITABLE' && !editable.isContentEditable) {
                                    editable = editable.parentNode || (editable.getRootNode && editable.getRootNode().host);
                                }
                                if (editable) {
                                    function getCurrentLineText(r, ed) {
                                        try {
                                            var preRange = document.createRange();
                                            preRange.selectNodeContents(ed);
                                            preRange.setEnd(r.startContainer, r.startOffset);
                                            var divPre = document.createElement('div');
                                            divPre.appendChild(preRange.cloneContents());
                                            var htmlPre = divPre.innerHTML;
                                            var cleanHtmlPre = htmlPre.replace(/<br\s*\/?>|<\/p>|<\/div>|<\/li>/gi, '\n');
                                            var linesPre = cleanHtmlPre.split('\n');
                                            var lastLinePreHtml = linesPre[linesPre.length - 1];
                                            var tempDivPre = document.createElement('div');
                                            tempDivPre.innerHTML = lastLinePreHtml;
                                            var textPre = tempDivPre.textContent || tempDivPre.innerText || "";

                                            var postRange = document.createRange();
                                            postRange.selectNodeContents(ed);
                                            postRange.setStart(r.endContainer, r.endOffset);
                                            var divPost = document.createElement('div');
                                            divPost.appendChild(postRange.cloneContents());
                                            var htmlPost = divPost.innerHTML;
                                            var cleanHtmlPost = htmlPost.replace(/<br\s*\/?>|<p\s*>|<div\s*>|<li\s*>/gi, '\n');
                                            var linesPost = cleanHtmlPost.split('\n');
                                            var firstLinePostHtml = linesPost[0];
                                            var tempDivPost = document.createElement('div');
                                            tempDivPost.innerHTML = firstLinePostHtml;
                                            var textPost = tempDivPost.textContent || tempDivPost.innerText || "";

                                            return (textPre + textPost).trim();
                                        } catch (e) {
                                            return "";
                                        }
                                    }
                                    var lineText = getCurrentLineText(range, editable);
                                    result.hasDash = lineText.includes('\u2014') || lineText.includes('\u2013') || lineText.includes('—') || lineText.includes('–');
                                    var stripped = lineText.replace(/\d{10,}/g, '').trim();
                                    result.isEmpty = stripped.length === 0;
                                }
                            }
                        } catch(e) {}
                        return JSON.stringify(result);
                    })();
                """, _check_line_context)
            except:
                # Fallback : toujours incruster le recto
                create_link_for_nid(editor, nid, with_recto=True)
            return
    
    # Texte non-NID sélectionné ? Le sauvegarder pour le passage au GUI
    selected_text = text.strip() if text and text.strip() else ""
    open_search_dialog(editor, selected_text=selected_text)

def create_link_for_nid(editor, nid, with_recto=False):
    try:
        note = mw.col.get_note(int(nid))
        recto = None
        if with_recto:
            recto = note.fields[0] if note.fields else ""
            recto = strip_html(recto)[:240] or "[Vide]"
        LinkInserter(editor).insert_link([(nid, recto)])
    except:
        tooltip(f"Note {nid} non trouvée.")
        open_search_dialog(editor)

def _setup_editor_window_shortcuts(editor: Editor):
    """Place des QShortcut sur la fenêtre parente de l'éditeur.
    
    Dans le Browser : remplace les QShortcuts créés par setup_browser_menu en les désactivant
    (un seul QShortcut par combinaison de touches sur une même fenêtre — sinon Qt en désactive
    tous les deux).
    """
    parent_win = None
    try:
        if hasattr(editor, 'parentWindow') and editor.parentWindow:
            parent_win = editor.parentWindow
        elif hasattr(editor, 'widget') and editor.widget:
            parent_win = editor.widget.window()
        elif hasattr(editor, 'mw') and editor.mw:
            parent_win = editor.mw
    except:
        pass

    if not parent_win:
        return

    shortcut_search = get_shortcut("linked_cards_search", "Ctrl+Alt+L")
    shortcut_copy   = get_shortcut("linked_cards_copy",   "Ctrl+Alt+C")
    shortcut_liens  = get_shortcut("linked_cards_liens",  "Ctrl+Alt+B")

    # Désactiver les QShortcuts installés par setup_browser_menu (évite l'ambiguïté Qt)
    # Inclut maintenant _edn_sc_liens pour éviter le doublon avec le shortcut éditeur ci-dessous
    for browser_attr in ('_edn_sc_copy', '_edn_sc_search', '_edn_sc_liens'):
        old_browser_sc = getattr(parent_win, browser_attr, None)
        if old_browser_sc:
            try:
                old_browser_sc.setEnabled(False)
            except:
                pass

    # Supprimer les anciens shortcuts EDN éditeur de cette fenêtre pour éviter les doublons
    for attr in ('_edn_sc_search_editor', '_edn_sc_copy_editor', '_edn_sc_liens_editor'):
        old_sc = getattr(parent_win, attr, None)
        if old_sc:
            try:
                old_sc.setEnabled(False)
                old_sc.deleteLater()
            except:
                pass

    sc_search = QShortcut(QKeySequence(shortcut_search), parent_win)
    sc_search.setContext(Qt.ShortcutContext.WindowShortcut)
    sc_search.activated.connect(lambda: handle_editor_button(editor))

    sc_copy = QShortcut(QKeySequence(shortcut_copy), parent_win)
    sc_copy.setContext(Qt.ShortcutContext.WindowShortcut)
    # Dans le browser, copier le NID de la note éditée (= comportement le plus utile)
    sc_copy.activated.connect(lambda: copy_nid_from_editor(editor))

    # Ctrl+Alt+B : basculer le panneau miroir — géré ici pour éviter le doublon
    # avec le QShortcut du browser (un seul QShortcut par combo sur une fenêtre)
    sc_liens = QShortcut(QKeySequence(shortcut_liens), parent_win)
    sc_liens.setContext(Qt.ShortcutContext.WindowShortcut)
    sc_liens.activated.connect(lambda: _focus_liens_field(editor))

    parent_win._edn_sc_search_editor = sc_search
    parent_win._edn_sc_copy_editor   = sc_copy
    parent_win._edn_sc_liens_editor  = sc_liens


def on_editor_init(editor: Editor):
    global _current_editor
    # Déporter l'initialisation JS pour ne pas bloquer l'ouverture de l'éditeur
    from aqt.qt import QTimer
    QTimer.singleShot(0, lambda: _do_editor_init(editor))

def _do_editor_init(editor: Editor):
    global _current_editor
    _current_editor = editor
    
    config = mw.addonManager.getConfig(__name__)
    trigger = config.get("search_trigger", "nid:") if config else "nid:"
    trigger_len = len(trigger)
    
    # Injecter les handlers hover preview dans l'éditeur (non disponibles via on_card_render)
    js_hover = r"""
    (function() {
        if (window._ednEditorHoverInit) return;
        window._ednEditorHoverInit = true;
        
        window.toggle = window.toggle || function(id) {
            var e = document.getElementById(id);
            if (e) {
                e.style.display = (e.style.display === 'none' || !e.style.display) ? 'block' : 'none';
            }
        };

        document.addEventListener('paste', function(e) {
            var text = (e.clipboardData || window.clipboardData).getData('text');
            if (text) {
                var match = text.trim().match(/^(.*?) [\u2014\u2013-] (\d{10,})$/);
                if (match) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    var recto = match[1].trim();
                    var nid = match[2].trim();
                    var rectoEscaped = recto.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

                    // --- Vérifier si la ligne courante a déjà un tiret em (—) ---
                    // Si oui, ne coller que le badge (pas le recto). Sauf si <br> suit le — (liste).
                    var lineHasDash = false;
                    try {
                        var sel2 = window.getSelection();
                        var active2 = document.activeElement;
                        if (active2 && active2.shadowRoot) sel2 = active2.shadowRoot.getSelection();
                        if (sel2 && sel2.rangeCount > 0) {
                            var range2 = sel2.getRangeAt(0);
                            var container2 = range2.startContainer;
                            var offset2 = range2.startOffset;
                            
                            var editable2 = container2;
                            while (editable2 && editable2.tagName !== 'ANKI-EDITABLE' && !editable2.isContentEditable) {
                                editable2 = editable2.parentNode || (editable2.getRootNode && editable2.getRootNode().host);
                            }
                            
                            if (editable2) {
                                function getCurrentLineText(r, ed) {
                                    try {
                                        var preRange = document.createRange();
                                        preRange.selectNodeContents(ed);
                                        preRange.setEnd(r.startContainer, r.startOffset);
                                        var divPre = document.createElement('div');
                                        divPre.appendChild(preRange.cloneContents());
                                        var htmlPre = divPre.innerHTML;
                                        var cleanHtmlPre = htmlPre.replace(/<br\s*\/?>|<\/p>|<\/div>|<\/li>/gi, '\n');
                                        var linesPre = cleanHtmlPre.split('\n');
                                        var lastLinePreHtml = linesPre[linesPre.length - 1];
                                        var tempDivPre = document.createElement('div');
                                        tempDivPre.innerHTML = lastLinePreHtml;
                                        var textPre = tempDivPre.textContent || tempDivPre.innerText || "";

                                        var postRange = document.createRange();
                                        postRange.selectNodeContents(ed);
                                        postRange.setStart(r.endContainer, r.endOffset);
                                        var divPost = document.createElement('div');
                                        divPost.appendChild(postRange.cloneContents());
                                        var htmlPost = divPost.innerHTML;
                                        var cleanHtmlPost = htmlPost.replace(/<br\s*\/?>|<p\s*>|<div\s*>|<li\s*>/gi, '\n');
                                        var linesPost = cleanHtmlPost.split('\n');
                                        var firstLinePostHtml = linesPost[0];
                                        var tempDivPost = document.createElement('div');
                                        tempDivPost.innerHTML = firstLinePostHtml;
                                        var textPost = tempDivPost.textContent || tempDivPost.innerText || "";

                                        return (textPre + textPost).trim();
                                    } catch (e) {
                                        return "";
                                    }
                                }
                                var lineText2 = getCurrentLineText(range2, editable2);
                                if (lineText2.includes('\u2014') || lineText2.includes('\u2013') || lineText2.includes('—') || lineText2.includes('–')) {
                                    lineHasDash = true;
                                }
                            }
                        }
                    } catch(err2) {}

                    var html;
                    if (lineHasDash) {
                        // Ligne a déjà un titre — : coller uniquement le badge
                        html = '<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="' + nid + '"><span class="edn-nid">' + nid + '</span></kbd>&nbsp;';
                    } else {
                        html = rectoEscaped + ' \u2014 <kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="' + nid + '"><span class="edn-nid">' + nid + '</span></kbd>&nbsp;';
                    }
                    document.execCommand('insertHTML', false, html);
                }
            }
        }, true);

        // Post-paste repair: after Anki sanitizes HTML, restore onclick on badges
        // This handles the case where user pastes HTML (Ctrl+V after Ctrl+Alt+C)
        // and Anki strips onclick/ondblclick attributes from <kbd> elements
        document.addEventListener('paste', function(e) {
            setTimeout(function() {
                document.querySelectorAll('.clickable_cards').forEach(function(kbd) {
                    var nid = '';
                    if (kbd.dataset && kbd.dataset.nid) nid = kbd.dataset.nid;
                    else if (kbd.getAttribute('data-nid')) nid = kbd.getAttribute('data-nid');
                    else {
                        var s = kbd.querySelector('.edn-nid');
                        if (s) nid = s.textContent.trim();
                        else nid = (kbd.innerText || '').trim().replace(/[^0-9]/g, '');
                    }
                    if (nid && nid.length > 9) {
                        if (!kbd.getAttribute('data-nid')) kbd.setAttribute('data-nid', nid);
                        if (!kbd.getAttribute('tabindex')) kbd.setAttribute('tabindex', '0');
                    }
                });
            }, 150); // delay to let Anki process the paste & sanitize
        }, false); // bubbling phase — runs after capture handler above

        function _findCardBadge(e) {
            if (e.composedPath) {
                var path = e.composedPath();
                for (var i = 0; i < path.length; i++) {
                    var el = path[i];
                    if (el.classList && el.classList.contains("clickable_cards")) {
                        return el;
                    }
                }
            }
            return e.target && e.target.closest ? e.target.closest(".clickable_cards") : null;
        }

        // Delegated click handler for badges in the editor
        // (the reviewer has one in _add_to_card_script_body, but editor doesn't)
        if (!window._ednEditorDelegatedClick) {
            window._ednEditorDelegatedClick = true;
            document.addEventListener('click', function(e) {
                var t = _findCardBadge(e);
                if (!t) return;
                // Exiger Ctrl ou Cmd dans l'éditeur pour éviter d'ouvrir sur simple clic ou double-clic
                if (!e.ctrlKey && !e.metaKey) return;
                var nid = '';
                if (t.dataset && t.dataset.nid) nid = t.dataset.nid;
                else {
                    var s = t.querySelector && t.querySelector('.edn-nid');
                    if (s) nid = s.textContent.trim();
                    else nid = (t.innerText || '').trim().replace(/[^0-9]/g, '');
                }
                if (nid && nid.length > 9) {
                    if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
                }
            });
        }

        window.cards_ct_click = function(nid) {
            // Exiger Ctrl ou Cmd dans l'éditeur
            var e = window.event;
            if (e && (e.ctrlKey || e.metaKey)) {
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
            }
        };
    })();
    """
    editor.web.eval(js_hover)

    # Injection JS : intercepter le clic sur le bouton "Liens" de la toolbar
    # pour envoyer pycmd DIRECTEMENT (sans passer par le cycle saveNow d'Anki).
    # MutationObserver : empêche Anki de désactiver le bouton quand aucun champ n'a le focus,
    # ce qui le rend cliquable au même titre que "Cartes…" et comme Ctrl+Alt+B.
    js_liens_click = r"""
    (function() {
        if (window._ednLiensBtnInit) return;
        window._ednLiensBtnInit = true;
        function _patchLiensBtn() {
            var btn = null;
            var allBtns = document.querySelectorAll('button');
            for (var i = 0; i < allBtns.length; i++) {
                if (allBtns[i].textContent.trim() === 'Liens') { btn = allBtns[i]; break; }
            }
            if (btn && !btn._ednPatched) {
                btn._ednPatched = true;
                // Forcer le bouton actif — retirer disabled immédiatement
                btn.removeAttribute('disabled');
                btn.style.opacity = '';
                btn.style.pointerEvents = 'auto';
                // MutationObserver : ré-activer si Anki essaie de le désactiver (hors focus champ)
                var obs = new MutationObserver(function(mutations) {
                    for (var i = 0; i < mutations.length; i++) {
                        if (mutations[i].attributeName === 'disabled') {
                            btn.removeAttribute('disabled');
                            btn.style.pointerEvents = 'auto';
                            btn.style.opacity = '';
                        }
                    }
                });
                obs.observe(btn, { attributes: true, attributeFilter: ['disabled'] });
                // Capture phase pour intercepter avant le handler natif Anki (qui lance saveNow)
                btn.addEventListener('click', function(e) {
                    e.stopImmediatePropagation();
                    e.preventDefault();
                    if (typeof pycmd !== 'undefined') pycmd('edn_toggle_assistant');
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand('edn_toggle_assistant');
                }, true);
            }
            return !!btn;
        }
        if (!_patchLiensBtn()) {
            setTimeout(_patchLiensBtn, 300);
            setTimeout(_patchLiensBtn, 800);
        }
    })();
    """
    editor.web.eval(js_liens_click)

    try:
        from .edn_menu import get_shortcut
        _kbd_next = get_shortcut("linked_cards_kbd_open", "n")
        _kbd_prev = get_shortcut("linked_cards_kbd_preview", "p")
        _kbd_open = get_shortcut("linked_cards_kbd_navigate", "r")
        _kbd_back = get_shortcut("linked_cards_kbd_back", "z")
    except Exception:
        _kbd_next, _kbd_prev, _kbd_open, _kbd_back = "n", "p", "r", "z"

    # Ajouter QShortcut sur la fenêtre parent de l'éditeur (plus fiable qu'editor_did_init_shortcuts seul)
    _setup_editor_window_shortcuts(editor)
    
    js = rf"""
    (function() {{
        if (window._ednnidInit) return;
        window._ednnidInit = true;
        window._ednBuffer = '';
        window._ednPreventing = false;
        window._ednTrigger = '{trigger}';
        window._ednTriggerLen = {trigger_len};
        window._ednMiroirKeys = {{
            next: '{_kbd_next}',
            prev: '{_kbd_prev}',
            open: '{_kbd_open}',
            back: '{_kbd_back}'
        }};
        
        function _isElementEditable(el) {{
            if (!el) return false;
            var tag = el.tagName ? el.tagName.toUpperCase() : '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'OPTION') {{
                return true;
            }}
            if (tag.startsWith('ANKI-') && tag !== 'ANKI-EDITOR') {{
                return true;
            }}
            if (el.isContentEditable || el.contentEditable === 'true') {{
                return true;
            }}
            if (el.classList && (el.classList.contains('editable') || el.classList.contains('input') || el.classList.contains('text-input'))) {{
                return true;
            }}
            return false;
        }}

        function _isEditable() {{
            var active = document.activeElement;
            if (!active) return false;
            if (active.shadowRoot && active.shadowRoot.activeElement) {{
                if (_isElementEditable(active.shadowRoot.activeElement)) return true;
            }}
            var target = active;
            while (target) {{
                if (_isElementEditable(target)) return true;
                target = target.parentNode || (target.getRootNode && target.getRootNode().host) || target.host;
            }}
            return false;
        }}

        function _isTargetEditable(e) {{
            var target = e.target;
            while (target) {{
                if (_isElementEditable(target)) return true;
                target = target.parentNode || (target.getRootNode && target.getRootNode().host) || target.host;
            }}
            return false;
        }}
        
        document.addEventListener('keydown', function(e) {{
            var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
            var isMiroirKey = (k === window._ednMiroirKeys.next.toLowerCase() ||
                               k === window._ednMiroirKeys.prev.toLowerCase() ||
                               k === window._ednMiroirKeys.open.toLowerCase() ||
                               k === window._ednMiroirKeys.back.toLowerCase() ||
                               e.key === 'Escape');
                               
            if (isMiroirKey && !_isEditable() && !_isTargetEditable(e)) {{
                e.preventDefault();
                e.stopPropagation();
                if (typeof pycmd !== 'undefined') pycmd('edn_editor_key:' + e.key);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('edn_editor_key:' + e.key);
                return;
            }}

            if (e.key === 'Escape' || e.key === 'Enter') {{
                window._ednBuffer = '';
                return;
            }}
            
            if (window._ednPreventing) {{
                e.preventDefault();
                e.stopPropagation();
                window._ednPreventing = false;
                return;
            }}
            
            if (e.key && e.key.length === 1) {{
                window._ednBuffer += e.key;
                if (window._ednBuffer.length > window._ednTriggerLen) {{
                    window._ednBuffer = window._ednBuffer.slice(-window._ednTriggerLen);
                }}
            }} else if (e.key === 'Backspace') {{
                window._ednBuffer = window._ednBuffer.slice(0, -1);
            }}
            
            if (window._ednBuffer === window._ednTrigger) {{
                e.preventDefault();
                e.stopPropagation();
                
                window._ednBuffer = '';
                window._ednPreventing = true;
                
                for (let i = 0; i < window._ednTriggerLen - 1; i++) {{
                    document.execCommand('delete', false, null);
                }}
                
                window._ednSavedRange = null;
                let active = document.activeElement;
                let sel = null;
                if (active && active.shadowRoot) sel = active.shadowRoot.getSelection();
                else sel = window.getSelection();
                
                if (sel && sel.rangeCount > 0) {{
                    window._ednSavedRange = sel.getRangeAt(0).cloneRange();
                    try {{
                        let r = window._ednSavedRange.cloneRange();
                        r.collapse(false);
                        let marker = document.createElement("span");
                        marker.id = "edn-cursor-marker";
                        r.insertNode(marker);
                    }} catch(e) {{}}
                }}
                
                if (typeof pycmd !== 'undefined') pycmd('edn_nid_trigger');
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('edn_nid_trigger');
            }}
        }}, true);
    }})();
    """
    editor.web.eval(js)
    log(f"Linked Cards: Editor initialized with trigger '{trigger}'")


def _handle_editor_shortcut_key(editor, key):
    from .edn_assistant import toggle_assistant
    try:
        from .edn_menu import get_shortcut
        _kbd_prev = get_shortcut("linked_cards_kbd_preview", "p")
    except Exception:
        _kbd_prev = "p"

    assistant = getattr(editor, '_edn_assistant_web', None)
    
    is_visible = False
    if assistant:
        try:
            is_visible = assistant.isVisible()
        except RuntimeError:
            assistant = None
            
    if not is_visible:
        if key.lower() == _kbd_prev.lower():
            # Ouvrir le panneau assistant (Mirror Side Panel)
            toggle_assistant(editor)
        return

    # Si le panneau est ouvert, lui renvoyer la touche pour traitement
    if assistant:
        try:
            escaped_key = key.replace("'", "\\'")
            assistant.eval(f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: '{escaped_key}', bubbles: true}}));")
        except Exception as ex:
            log(f"Error forwarding editor key to assistant: {ex}")

def on_js_message(handled, message, context):
    if handled and handled[0]:
        return handled
    if message.startswith("edn_editor_key:"):
        key = message[len("edn_editor_key:"):]
        from .edn_assistant import get_active_editor
        editor = get_active_editor(context)
        if editor:
            _handle_editor_shortcut_key(editor, key)
        return (True, None)
    elif message == "edn_nid_trigger":
        from .edn_assistant import get_active_editor
        editor = get_active_editor(context)
        if editor:
            open_search_dialog(editor)
        return (True, None)
    elif message == "edn_toggle_assistant":
        # Déclenché par le clic JS sur le bouton 'Liens' de la toolbar
        # (évite le saveNow bloquant du mécanisme func= d'Anki)
        from .edn_assistant import get_active_editor
        editor = get_active_editor(context)
        if editor:
            _focus_liens_field(editor)
        return (True, None)
    elif message.startswith("cards_ct_click") or message.startswith("cards_ct_hover:"):
        return on_js_message_reviewer(handled, message, context)
    elif message.startswith("gui_preview_hover:"):
        nid = message.split(":")[1]
        global _active_dialog
        if _active_dialog and hasattr(_active_dialog, 'show_nested_preview'):
            _active_dialog.show_nested_preview(nid)
        return (True, None)
    elif message.startswith("gui_preview_mouseout:"):
        if _active_dialog:
            if message == "gui_preview_mouseout:esc":
                if hasattr(_active_dialog, '_preview_stack') and _active_dialog._preview_stack:
                    _active_dialog.hide_nested_preview()
                elif hasattr(_active_dialog, 'hide_preview_popup'):
                    _active_dialog.hide_preview_popup()
            elif hasattr(_active_dialog, 'hide_nested_preview'):
                _active_dialog.hide_nested_preview()
        return (True, None)
    elif message.startswith("gui_preview_click:"):
        nid = message.split(":")[1]
        if _active_dialog and hasattr(_active_dialog, 'on_gui_preview_click'):
            _active_dialog.on_gui_preview_click(nid)
        return (True, None)
    elif message == "gui_preview_key_p":
        if _active_dialog and hasattr(_active_dialog, 'cycle_gui_results_and_preview'):
            _active_dialog.cycle_gui_results_and_preview()
        return (True, None)
    elif message.startswith("preview_resize:") or message.startswith("preview_nested:") or message == "preview_back" or message == "escape" or message.startswith("preview_key_to_assistant:") or message.startswith("open_browser:"):
        from .edn_assistant import handle_preview_popup_message
        handle_preview_popup_message(message)
        return (True, None)
    elif message.startswith("add_mirror:") or message.startswith("make_reciprocal:") or message.startswith("preview_card:") or message.startswith("hover_card:") or message == "hover_leave" or message.startswith("preview_key_forward:") or message.startswith("focus_preview:"):
        from .edn_assistant import handle_assistant_message
        handle_assistant_message(False, message, context)
        return (True, None)
    return handled

def strip_html(text):
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = clean.replace('&nbsp;', ' ')
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()

class LinkInserter:
    def __init__(self, editor):
        self.editor = editor
        
    def insert_link(self, items, override_html=None, mirror_requested=False):
        is_direct_replacement = all(recto is None for _, recto in items)
        
        html_parts = []
        for nid, recto in items:
            if recto is None:
                html_parts.append(
                    f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid}"'
                    f'><span class="edn-nid">{nid}</span></kbd>'
                )
            else:
                recto_escaped = recto.replace('"', '&quot;').replace("'", '&#39;')
                html_parts.append(
                    f'{recto_escaped}&nbsp;—&nbsp;'
                    f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid}"'
                    f'><span class="edn-nid">{nid}</span></kbd>'
                )

                
        html = "<br>".join(html_parts)
        if html:
            html += "&nbsp;"
        if override_html is not None:
            html = override_html
        
        js = f"""
        (function() {{
            let htmlToInsert = `{html}`;
            let inserted = false;

            // ── Étape 1 : trouver le champ actif et la racine DOM ──────────────────────
            // Le champ éditable Anki est un <anki-editable> dont le contenu réel
            // se trouve dans son propre document (shadowRoot ou lui-même si contenteditable).

            function _findEditableRoot(el) {{
                // Renvoie [editableElement, rootNode] où rootNode est le document dans
                // lequel on peut faire des sélections / manipulations.
                if (!el) return [null, document];
                if (el.shadowRoot) {{
                    let inner = el.shadowRoot.querySelector('[contenteditable]');
                    if (inner) return [inner, el.shadowRoot];
                }}
                if (el.isContentEditable || el.tagName === 'ANKI-EDITABLE') return [el, el.getRootNode ? el.getRootNode() : document];
                return [null, document];
            }}

            let activeEditable = null;
            let editRoot = document;  // racine DOM du champ (shadow ou document)

            // Chercher d'abord via l'élément actif
            let focused = document.activeElement;
            if (focused && focused.tagName === 'ANKI-EDITABLE') {{
                let [el, root] = _findEditableRoot(focused);
                if (el) {{ activeEditable = el; editRoot = root; }}
            }} else if (focused && focused.shadowRoot) {{
                // ex: anki-editor qui wrape les champs
                let inner = focused.shadowRoot.querySelector('anki-editable:focus, [contenteditable]:focus');
                if (!inner) inner = focused.shadowRoot.querySelector('anki-editable');
                if (inner) {{
                    let [el, root] = _findEditableRoot(inner);
                    if (el) {{ activeEditable = el; editRoot = root; }}
                }}
            }}
                // Fallback : index du champ sauvegardé
            if (!activeEditable && window._ednSavedFieldIndex >= 0) {{
                let allAE = Array.from(document.querySelectorAll('anki-editable'));
                let target = allAE[window._ednSavedFieldIndex];
                if (target) {{
                    let [el, root] = _findEditableRoot(target);
                    if (el) {{ activeEditable = el; editRoot = root; }}
                }}
            }}

            // Dernier recours : premier champ
            if (!activeEditable) {{
                let allAE = Array.from(document.querySelectorAll('anki-editable'));
                if (allAE.length) {{
                    let [el, root] = _findEditableRoot(allAE[0]);
                    if (el) {{ activeEditable = el; editRoot = root; }}
                }}
            }}

            if (!activeEditable) return;

            // ── Étape 2 : rechercher le marker dans TOUTES les racines ─────────────────
            let markers = [];
            if (editRoot) {{
                markers = Array.from(editRoot.querySelectorAll('[id="edn-cursor-marker"]'));
            }}

            if (!markers.length) {{
                document.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {{
                    if (el.shadowRoot) {{
                        markers = markers.concat(Array.from(el.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                        el.shadowRoot.querySelectorAll('anki-editable').forEach(inner => {{
                           if (inner.shadowRoot) markers = markers.concat(Array.from(inner.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                        }});
                    }}
                }});
            }}
            if (!markers.length) {{
                markers = Array.from(document.querySelectorAll('[id="edn-cursor-marker"]'));
            }}

            // Garder seulement le dernier (le plus récent)
            let marker = markers.length > 0 ? markers[markers.length - 1] : null;
            for (let i = 0; i < markers.length - 1; i++) {{
                try {{ markers[i].parentNode.removeChild(markers[i]); }} catch(e) {{}}
            }}
            
            if (!marker && !activeEditable) return;

            // ── Étape 3 : insérer le HTML ─────────────────────────────────────────────
            if (marker) {{
                let curr = marker.parentNode;
                if (!activeEditable) {{
                    while (curr) {{
                        if (curr.tagName === 'ANKI-EDITABLE' || curr.isContentEditable) {{
                            activeEditable = curr;
                            break;
                        }}
                        curr = curr.parentNode || (curr.getRootNode && curr.getRootNode().host);
                    }}
                }}
                
                let temp = document.createElement('template');
                temp.innerHTML = htmlToInsert;
                let frag = temp.content;
                if (marker.nextSibling) {{
                    marker.parentNode.insertBefore(frag, marker.nextSibling);
                }} else {{
                    marker.parentNode.appendChild(frag);
                }}
                marker.parentNode.removeChild(marker);
                inserted = true;
            }} else {{
                // Pas de marker : restaurer le range sauvegardé et execCommand
                let sel = editRoot.getSelection ? editRoot.getSelection() : window.getSelection();
                if (window._ednSavedRange && sel) {{
                    try {{
                        sel.removeAllRanges();
                        sel.addRange(window._ednSavedRange);
                    }} catch(e) {{}}
                }}
                try {{
                    inserted = document.execCommand("insertHTML", false, htmlToInsert);
                }} catch(e) {{}}

                if (!inserted) {{
                    try {{
                        let template = document.createElement('template');
                        template.innerHTML = htmlToInsert;
                        activeEditable.appendChild(template.content);
                        inserted = true;
                    }} catch(e) {{}}
                }}

                // Fallback ultime : ajouter à la fin avec <br> si non vide
                if (!inserted) {{
                    try {{
                        let innerText = (activeEditable.innerText || activeEditable.textContent || '').trim();
                        let template = document.createElement('template');
                        template.innerHTML = htmlToInsert;
                        if (innerText.length > 0) {{
                            activeEditable.appendChild(document.createElement('br'));
                        }}
                        activeEditable.appendChild(template.content);
                        inserted = true;
                    }} catch(e) {{}}
                }}
            }}

            // Notifier Anki que le contenu a changé
            let dispatchTarget = activeEditable.isContentEditable ? activeEditable
                : (activeEditable.closest ? activeEditable.closest('anki-editable') : activeEditable);
            if (!dispatchTarget) dispatchTarget = activeEditable;
            try {{
                dispatchTarget.dispatchEvent(new Event("input", {{ bubbles: true, composed: true }}));
            }} catch(e) {{}}
            window._ednSavedRange = null;
        }})();
        """
        self.editor.web.eval(js)
        if len(items) == 1:
            tooltip(f"Lien créé vers {items[0][0]}")
        else:
            tooltip(f"{len(items)} liens créés")
        
        # --- Lien miroir : proposer de lier la carte source dans la carte cible ---
        config = mw.addonManager.getConfig(__name__) or {}
        mirror_systematic = config.get("mirror_link_systematic", False)
        if mirror_systematic or mirror_requested:
            source_note = self.editor.note
            if source_note and source_note.id:
                for target_nid, _ in items:
                    try:
                        QTimer.singleShot(200, lambda tn=target_nid: _propose_mirror_link(
                            self.editor, source_note, tn
                        ))
                    except Exception:
                        pass

    def insert_link_with_text(self, nid, display_text, mirror_requested=False):
        """Insère un lien hyperlien : le texte affiché est display_text mais le NID est stocké dans data-nid
        ET dans un <span class="edn-nid"> invisible. Ce double stockage garantit que le NID
        survive au sanitizer HTML d'Anki qui supprime parfois data-* et onclick.
        
        Contrairement à insert_link(), remplace le texte sélectionné par le badge.
        Le marker edn-cursor-marker est déjà posé Là où se trouvait la sélection.
        """
        safe_text = display_text.replace('`', '\\`').replace('"', '&quot;')
        safe_nid = str(nid)
        # Le span .edn-nid est invisible (display:none CSS) mais survit au sanitizer Anki
        html = (
            f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{safe_nid}"'
            f'><span class="edn-nid" style="display:none;font-size:0;line-height:0">{safe_nid}</span>'
            f'{safe_text}</kbd>&nbsp;'
        )
        # Réutilise la même logique d'insertion que insert_link()
        self.insert_link([(safe_nid, None)], override_html=html, mirror_requested=mirror_requested)
        tooltip(f"Lien '{display_text}' → carte {nid}")


def _propose_mirror_link(editor, source_note, target_nid_str):
    """Propose de lier la carte source dans la carte cible (lien miroir).
    
    Quand l'utilisateur lie la carte B dans un champ de la carte A,
    cette fonction propose d'ajouter automatiquement un badge vers A
    dans le même champ de la carte B.
    """
    try:
        target_nid = int(target_nid_str)
        target_note = mw.col.get_note(target_nid)
        source_nid = str(source_note.id)
        
        # Déterminer dans quel champ on a inséré le lien
        # On cherche le champ courant de l'éditeur (sauvegardé par le JS)
        field_idx = -1
        
        def _got_field_idx(idx_str):
            nonlocal field_idx
            try:
                field_idx = int(idx_str) if idx_str and idx_str != 'null' else -1
            except:
                field_idx = -1
            _do_mirror(field_idx)
        
        def _do_mirror(fidx):
            # Chercher le bon champ dans la note cible
            # 1. Si fidx est valide, récupérer le nom du champ source et chercher le même dans la cible
            # 2. Sinon, chercher un champ nommé "Cartes liées"
            # 3. En dernier recours, utiliser le dernier champ
            target_fields = target_note.note_type()['flds']
            
            if fidx >= 0 and fidx < len(source_note.note_type()['flds']):
                source_field_name = source_note.note_type()['flds'][fidx]['name']
                # Chercher le même nom dans la cible
                found = False
                for i, fld in enumerate(target_fields):
                    if fld['name'] == source_field_name:
                        fidx = i
                        found = True
                        break
                if not found:
                    fidx = -1  # pas trouvé, on continue la recherche
            
            if fidx < 0:
                # Chercher un champ nommé "Cartes liées"
                for i, fld in enumerate(target_fields):
                    if fld['name'] == 'Cartes liées':
                        fidx = i
                        break
            
            if fidx < 0:
                # Dernier recours : le dernier champ
                fidx = len(target_note.fields) - 1
            
            # Vérifier que le champ existe dans la note cible
            if fidx >= len(target_note.fields):
                fidx = len(target_note.fields) - 1
            
            if fidx < 0:
                return
            
            # Vérifier si le lien miroir existe déjà dans la note cible
            if any(source_nid in field for field in target_note.fields):
                log(f"Lien miroir ignoré : la carte cible {target_nid_str} contient déjà un lien vers {source_nid}")
                return
            
            # Récupérer le recto de la note source pour l'affichage dans la popup
            source_recto = ""
            try:
                source_recto = strip_html(source_note.fields[0])[:80] if source_note.fields else ""
            except:
                pass
            source_recto = source_recto or "[Vide]"
            
            target_recto = ""
            try:
                target_recto = strip_html(target_note.fields[0])[:80] if target_note.fields else ""
            except:
                pass
            target_recto = target_recto or "[Vide]"
            
            # Popup de confirmation
            msg = QMessageBox(editor.widget if editor.widget else mw)
            msg.setWindowTitle("Lien miroir")
            target_field_name = target_note.note_type()['flds'][fidx]['name']
            msg.setText(
                f"Lier aussi la carte source dans la carte cible ?\n\n"
                f"→ Ajouter un lien vers :\n"
                f"   {source_recto}\n"
                f"   dans le champ [{target_field_name}] de la carte :\n"
                f"   {target_recto}"
            )
            msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(QMessageBox.StandardButton.Ok)
            
            result = msg.exec()
            if result == QMessageBox.StandardButton.Ok:
                # Insérer le badge dans le champ de la note cible
                source_recto_badge = strip_html(source_note.fields[0])[:240] if source_note.fields else "[Vide]"
                source_recto_badge = source_recto_badge or "[Vide]"
                source_recto_escaped = source_recto_badge.replace('"', '&quot;').replace("'", '&#39;')
                
                badge_html = (
                    f'<br>{source_recto_escaped}&nbsp;\u2014&nbsp;'
                    f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{source_nid}"'
                    f'><span class="edn-nid">{source_nid}</span></kbd>'
                )
                
                # Pas de <br> si le champ est vide
                current_field_content = target_note.fields[fidx].strip()
                if not current_field_content or current_field_content == '<br>':
                    badge_html = badge_html.replace('<br>', '', 1)
                
                target_note.fields[fidx] += badge_html
                mw.col.update_note(target_note)
                tooltip(f"Lien miroir créé dans la carte {target_nid_str}")
            
            # Refresh assistant side panel in all cases after dialog decision
            from .edn_assistant import update_assistant
            if editor:
                try:
                    editor.saveNow(lambda: update_assistant(editor))
                except Exception as se:
                    log(f"Error saving editor/updating assistant: {se}")
                    update_assistant(editor)
        
        # Récupérer l'index du champ actif depuis le JS
        try:
            editor.web.evalWithCallback(
                "(typeof window._ednSavedFieldIndex !== 'undefined') ? window._ednSavedFieldIndex : -1;",
                _got_field_idx
            )
        except Exception:
            _do_mirror(-1)
    except Exception as e:
        log(f"Mirror link error: {e}")


class LinkedCardsDialog(QDialog):
    def __init__(self, editor, selected_text=""):
        parent = editor.parentWindow if hasattr(editor, 'parentWindow') else mw
        super().__init__(parent)
        self.editor = editor
        self.inserter = LinkInserter(editor)
        self.selected_nid = None
        self.selected_recto = None
        self.selected_text = selected_text  # Texte à afficher dans le badge (mode hyperlien)
        self.setWindowTitle("Rechercher une carte liée")
        self.setMinimumSize(800, 500)
        self.setModal(False)
        self._preview_stack = []
        self._search_seq = 0
        
        # Debounce timer pour la recherche (évite de bloquer le main thread en cours de saisie)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_search_timeout)
        
        self.setup_ui()

    def _on_search_text_changed(self, text):
        if not text.strip():
            self._search_timer.stop()
            self.do_search("")
        else:
            self._search_timer.start(250)  # Délai de 250ms

    def _on_search_timeout(self):
        self.do_search(self.search_bar.text())

    def keyPressEvent(self, event):
        """Transmet les touches p/r/n/z/Escape à la preview webview même si le focus est dans le GUI."""
        key = event.key()
        if key == Qt.Key.Key_P:
            self.cycle_gui_results_and_preview()
            event.accept()
            return
            
        key_map = {
            Qt.Key.Key_R: 'r',
            Qt.Key.Key_N: 'n',
            Qt.Key.Key_Z: 'z',
        }
        if key in key_map and hasattr(self, '_preview_web') and self._preview_web:
            try:
                k = key_map[key]
                self._preview_web.eval(
                    f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: '{k}', bubbles: true}}));"
                )
                event.accept()
                return
            except Exception:
                pass
        super().keyPressEvent(event)

    def cycle_gui_results_and_preview(self):
        if not hasattr(self, 'results_table') or not self.results_table:
            return
        row_count = self.results_table.rowCount()
        if row_count <= 0:
            return
        
        selected_indexes = self.results_table.selectedIndexes()
        if selected_indexes:
            current_row = selected_indexes[0].row()
            next_row = (current_row + 1) % row_count
        else:
            next_row = 0
            
        self.results_table.selectRow(next_row)
        self.results_table.scrollTo(self.results_table.model().index(next_row, 0))
        
        try:
            item_nid = self.results_table.item(next_row, 1).text()
            btn = self.results_table.cellWidget(next_row, 2)
            if item_nid and btn:
                self._preview_current_widget = btn
                self.show_preview_popup(item_nid, position_widget=btn)
        except Exception as e:
            print("Error cycling GUI results preview:", e)
        
    def setup_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Bandeau indicateur si on est en mode hyperlien
        if self.selected_text:
            hint = QLabel(f"🔗 Mode hyperlien — Les mots <b>'{self.selected_text}'</b> deviendront cliquables vers la carte choisie.")
            hint.setWordWrap(True)
            hint.setStyleSheet("background:#e8f4ff; border-radius:4px; padding:4px 8px; color:#005a9e; margin-bottom:4px;")
            layout.addWidget(hint)
        
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Rechercher dans les cartes... (NID, texte, ou tag::...)")
        self.search_bar.textChanged.connect(self._on_search_text_changed)
        self.search_bar.returnPressed.connect(self.insert_selected)
        layout.addWidget(self.search_bar)
        
        # Options de recherche
        search_opts = QHBoxLayout()
        config = mw.addonManager.getConfig(__name__) or {}
        
        self.recto_only_cb = QCheckBox("Uniquement le recto")
        self.recto_only_cb.setToolTip("Afficher uniquement les cartes dont le recto contient la recherche")
        self.recto_only_cb.setChecked(config.get("gui_recto_only_cb_state", False))
        
        def _on_recto_cb_changed(state):
            cfg = mw.addonManager.getConfig(__name__) or {}
            cfg["gui_recto_only_cb_state"] = bool(state)
            mw.addonManager.writeConfig(__name__, cfg)
            self.do_search(self.search_bar.text())
            
        self.recto_only_cb.stateChanged.connect(_on_recto_cb_changed)
        search_opts.addWidget(self.recto_only_cb)
        
        self.mirror_cb = QCheckBox("Lien miroir")
        self.mirror_cb.setToolTip("Ajouter automatiquement un lien retour dans la carte cible (popup de confirmation)")
        self.mirror_cb.setChecked(config.get("gui_mirror_cb_state", config.get("mirror_link_systematic", False)))
        
        def _on_mirror_cb_changed(state):
            cfg = mw.addonManager.getConfig(__name__) or {}
            cfg["gui_mirror_cb_state"] = bool(state)
            mw.addonManager.writeConfig(__name__, cfg)
            
        self.mirror_cb.stateChanged.connect(_on_mirror_cb_changed)
        search_opts.addWidget(self.mirror_cb)
        
        search_opts.addStretch()
        layout.addLayout(search_opts)
        
        self.results_label = QLabel("Tapez pour rechercher...")
        layout.addWidget(self.results_label)
        
        self.results_table = QTableWidget()
        try:
            self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        except:
            try:
                self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            except:
                pass
        self.results_table.setColumnCount(3)
        self.results_table.setHorizontalHeaderLabels(["Recto", "NID", "Actions"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results_table.setColumnWidth(2, 80)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.results_table.itemDoubleClicked.connect(self.on_double_click)
        self.results_table.itemSelectionChanged.connect(self.on_selection_changed)
        layout.addWidget(self.results_table)
        
        btn_layout = QHBoxLayout()
        self.insert_btn = QPushButton("Insérer")
        self.insert_btn.setEnabled(False)
        self.insert_btn.setDefault(True)
        self.insert_btn.clicked.connect(self.insert_selected)
        
        close_btn = QPushButton("Fermer")
        close_btn.setAutoDefault(False)
        close_btn.clicked.connect(self.close)
        
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addWidget(self.insert_btn)
        layout.addLayout(btn_layout)
        
        self.search_bar.setFocus()
        
        # Install event filter on all focusable children (except search bar) to intercept key presses
        for widget in self.findChildren(QWidget):
            if widget != self.search_bar:
                widget.installEventFilter(self)
        
    def closeEvent(self, event):
        self._cleanup_on_close()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_on_close()
        super().reject()

    def _cleanup_on_close(self):
        # Fermer la preview popup si elle est ouverte
        try:
            self.hide_preview_popup()
        except:
            pass
        if hasattr(self, '_preview_web') and self._preview_web:
            try:
                self._preview_web.cleanup()
            except:
                pass
            self._preview_web = None
        if hasattr(self, '_preview_dlg') and self._preview_dlg:
            try:
                self._preview_dlg.deleteLater()
            except:
                pass
            self._preview_dlg = None
        if getattr(self, '_is_inserting', False):
            return
        # Clean up the marker on close if it was not consumed by link insertion.
        # Guard against editor/web being None (editor destroyed before dialog close)
        if not self.editor or not self.editor.web:
            return
        try:
            self.editor.web.eval("""
                (function() {
                    // Nettoyage agressif : chercher dans TOUS les shadow roots récursivement
                    function findAllMarkers(root, found, visited) {
                        if (!root || visited.has(root)) return;
                        visited.add(root);
                        if (root.querySelectorAll) {
                            let ms = root.querySelectorAll('[id="edn-cursor-marker"]');
                            for (let i = 0; i < ms.length; i++) {
                                if (!found.includes(ms[i])) found.push(ms[i]);
                            }
                        }
                        if (root.shadowRoot) findAllMarkers(root.shadowRoot, found, visited);
                        if (root.children) {
                            for (let i = 0; i < root.children.length; i++) findAllMarkers(root.children[i], found, visited);
                        }
                        // Chercher aussi dans les nested anki-editable
                        if (root.querySelectorAll) {
                            root.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {
                                findAllMarkers(el, found, visited);
                                if (el.shadowRoot) findAllMarkers(el.shadowRoot, found, visited);
                            });
                        }
                    }
                    let markers = [];
                    let visited = new Set();
                    findAllMarkers(document.body, markers, visited);
                    document.querySelectorAll('anki-editable, anki-editor').forEach(el => findAllMarkers(el, markers, visited));
                    
                    let changedEditables = new Set();
                    markers.forEach(function(m) { 
                        try { 
                            let rootNode = m.getRootNode ? m.getRootNode() : null;
                            let host = rootNode && rootNode.host ? rootNode.host : null;
                            let editable = m.closest ? m.closest('anki-editable') : null;
                            if (!editable && host && host.tagName === 'ANKI-EDITABLE') editable = host;
                            if (editable) changedEditables.add(editable);
                            
                            if (m.parentNode) m.parentNode.removeChild(m); 
                        } catch(e) {} 
                    });
                    changedEditables.forEach(function(editable) {
                        try {
                            // RAF pour laisser le DOM se stabiliser avant de notifier Anki
                            // (évite le repliement du champ "Cartes liées" causé par un re-parse prématuré)
                            requestAnimationFrame(function() {
                                try {
                                    editable.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
                                } catch(e) {}
                            });
                        } catch(e) {}
                    });
                })();
            """)
        except Exception:
            pass

    @perf_log
    def do_search(self, query):
        query = query.strip()
        recto_only = self.recto_only_cb.isChecked()
        
        # NID de la note source (à exclure des résultats)
        source_nid = str(self.editor.note.id) if self.editor and self.editor.note else None
        
        # Incrémenter le numéro de séquence de recherche pour ignorer les résultats obsolètes
        self._search_seq += 1
        current_seq = self._search_seq

        # Vider la table immédiatement et indiquer que la recherche est en cours
        self.results_table.setRowCount(0)
        
        # Détection de NIDs multiples (ex: "1575893780795 1572425651952")
        # Aussi accepter un NID seul sans préfixe 'nid:'
        nid_matches = re.findall(r'\b(\d{10,})\b', query)
        
        if not nid_matches and len(query) < 2:
            self.results_label.setText("")
            return

        self.results_label.setText("Recherche en cours...")

        def bg_query(col):
            results = []
            if nid_matches:
                seen_nids = set()
                for n in nid_matches:
                    found = col.find_notes(f"nid:{n}")
                    for nid in found:
                        if nid in seen_nids or len(results) >= 50:
                            break
                        if source_nid and str(nid) == source_nid:
                            continue
                        seen_nids.add(nid)
                        try:
                            note = col.get_note(nid)
                            recto = note.fields[0] if note.fields else ""
                            recto_clean = strip_html(recto)[:240] or "[Vide]"
                            results.append((str(nid), recto_clean))
                        except:
                            continue
                return ("nid", results)

            # Recherche textuelle normale (ou tag)
            if query.startswith('tag::'):
                search_query = 'tag:' + query[5:]
            elif query.startswith('tag:'):
                search_query = query
            else:
                search_query = f"*{query}*"

            try:
                nids = col.find_notes(search_query)
                for nid in nids:
                    if len(results) >= 50:
                        break
                    if source_nid and str(nid) == source_nid:
                        continue
                    try:
                        note = col.get_note(nid)
                        recto = note.fields[0] if note.fields else ""
                        recto_clean = strip_html(recto)[:240] or "[Vide]"

                        if recto_only and not query.startswith('tag'):
                            search_words = query.lower().split()
                            recto_lower = recto_clean.lower()
                            if not all(word in recto_lower for word in search_words):
                                continue

                        results.append((str(nid), recto_clean))
                    except:
                        continue
            except Exception as e:
                # Transmettre l'erreur sous forme de tuple spécial
                return ("error", str(e))
                
            return ("text", results)

        def on_success(res):
            # S'assurer que le dialogue n'a pas été détruit/fermé
            try:
                self.objectName()
            except RuntimeError:
                return

            # Ignorer si une recherche plus récente a été lancée
            if current_seq != self._search_seq:
                return

            search_type, results_or_err = res
            if search_type == "error":
                self.results_label.setText(results_or_err)
                return

            results = results_or_err
            self.results_table.setRowCount(0)
            count = 0
            
            for nid_str, recto_clean in results:
                row = self.results_table.rowCount()
                self.results_table.insertRow(row)
                
                i_recto = QTableWidgetItem(recto_clean)
                i_recto.setData(Qt.ItemDataRole.UserRole, {'nid': nid_str, 'recto': recto_clean})
                self.results_table.setItem(row, 0, i_recto)
                
                self.results_table.setItem(row, 1, QTableWidgetItem(nid_str))
                
                btn = self._make_voir_button(nid_str)
                self.results_table.setCellWidget(row, 2, btn)
                
                count += 1

            if search_type == "nid":
                self.results_label.setText(f"{count} résultats (recherche NID)")
            else:
                self.results_label.setText(f"{count} résultats")

            if not hasattr(self, '_scroll_connected'):
                self.results_table.verticalScrollBar().valueChanged.connect(self._on_table_scroll)
                self._scroll_connected = True

        from aqt.operations import QueryOp
        op = QueryOp(
            parent=self,
            op=bg_query,
            success=on_success
        )
        op.run_in_background()

    def _make_voir_button(self, nid):
        """Crée le bouton 'Voir' pour une ligne de résultats."""
        dialog_parent = self
        
        class HoverButton(QPushButton):
            def __init__(self, text, nid, dlg):
                super().__init__(text)
                self.nid = nid
                self.dialog_parent = dlg
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; border-radius: 4px;")
                self.clicked.connect(self.on_click)
                
            def on_click(self):
                from aqt import dialogs, mw
                browser = dialogs.open("Browser", mw)
                browser.search_for(f"nid:{self.nid}")
                self.dialog_parent.hide_preview_popup()

            def enterEvent(self, event):
                # Guard: don't re-create preview if same NID is already displayed
                if (hasattr(self.dialog_parent, '_preview_current_nid')
                        and self.dialog_parent._preview_current_nid == self.nid
                        and hasattr(self.dialog_parent, '_preview_dlg')
                        and self.dialog_parent._preview_dlg
                        and self.dialog_parent._preview_dlg.isVisible()):
                    self.dialog_parent._preview_current_widget = self
                    super().enterEvent(event)
                    return
                self.dialog_parent._preview_current_widget = self
                self.dialog_parent.show_preview_popup(self.nid, position_widget=self)
                super().enterEvent(event)
                
            def leaveEvent(self, event):
                from aqt.qt import QTimer
                try:
                    dlg = self.dialog_parent
                    QTimer.singleShot(150, lambda: self._safe_check_hide())
                except RuntimeError:
                    pass
                try:
                    super().leaveEvent(event)
                except RuntimeError:
                    pass

            def _safe_check_hide(self):
                """Wrapper sûr pour check_hide_preview, protège contre les widgets Qt détruits."""
                try:
                    self.objectName() # throws RuntimeError if deleted
                    self.dialog_parent.check_hide_preview()
                except (RuntimeError, Exception):
                    pass
        
        return HoverButton("Voir", nid, dialog_parent)

    def _on_table_scroll(self, value):
        if hasattr(self, '_preview_dlg') and self._preview_dlg and self._preview_dlg.isVisible():
            if hasattr(self, '_preview_current_widget') and self._preview_current_widget:
                btn = self._preview_current_widget
                # Check if still visible
                rect = self.results_table.visualItemRect(self.results_table.itemAt(btn.pos()))
                if btn.isVisible() and btn.rect().intersects(self.results_table.viewport().rect()):
                    pos = btn.mapToGlobal(QPoint(btn.width() + 10, -50))
                    self._preview_dlg.move(pos)
                else:
                    self.hide_preview_popup()

    def toggle_preview_popup(self, nid, position_widget=None):
        """Affiche ou masque la preview sans fermer le dialog principal."""
        if (hasattr(self, '_preview_dlg') and self._preview_dlg
                and self._preview_dlg.isVisible()
                and getattr(self, '_preview_current_nid', None) == nid):
            self._preview_dlg.hide()
        else:
            self.show_preview_popup(nid, position_widget=position_widget)

    def show_preview_popup(self, nid, position_widget=None):
        """Affiche un popup Qt avec la preview HTML (CSS identique a la revision)."""
        try:
            import re as re2
            note = mw.col.get_note(int(nid))
            cards = note.cards()
            if not cards:
                return
            card = cards[0]
            rendered = card.answer()

            # Meme nettoyage que le hover
            # IMPORTANT: ne matcher que les attributs id= stricts (pas data-nid=, etc.)
            # Le lookbehind (?<!\w) s'assure qu'on ne touche pas à "data-nid=" etc.
            isolated = re2.sub(r'(?<!\w)id=(["\'])(.*?)\1', r'id="dlg_preview_\2"', rendered)
            isolated = re2.sub(r'toggle\([\"\'](.*?)[\"\']\)', r"toggle('dlg_preview_\1')", isolated)
            isolated = re2.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', '', isolated, flags=re2.IGNORECASE)
            isolated = re2.sub(
                r"(?:<br>\s*)?<(span|div|p)\b[^>]*id=[^>]*fsrs[^>]*>.*?</\1>",
                "", isolated, flags=re2.IGNORECASE|re2.DOTALL)
            for sec in ["sourcesMegaContainer", "commentsMegaContainer", "tagsMegaContainer",

                        "erreursFaitesMegaContainer", "mnemonicsMegaContainer", "infoSupplementairesMegaContainer"]:

                isolated = re2.sub(

                    rf'<div[^>]*id=["\']dlg_preview_{re2.escape(sec)}["\'][^>]*>.*?</div>',

                    '', isolated, flags=re2.IGNORECASE|re2.DOTALL

                )

            # Forcer l'affichage des sections (comme dans le reviewer)

            isolated = re2.sub(

                r'(<div[^>]*class=["\'][^"\']*(section)[^"\'][^"\']*["\'][^>]*style=["\'])display:\s*none;?',

                r'\1display: flex !important;', isolated, flags=re2.IGNORECASE)

            if not hasattr(self, '_preview_dlg') or self._preview_dlg is None:
                flags = Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                dlg = QWidget(None, flags)
                dlg.setObjectName("PreviewPopup")
                dlg.setWindowTitle("Apercu")
                dlg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                dlg.setMinimumSize(500, 150)
                dlg.setMaximumSize(680, 1400)
                dlg.setStyleSheet("#PreviewPopup { border: 1px solid #ccc; background: white; border-radius: 8px; }")

                v = QVBoxLayout(dlg)
                v.setContentsMargins(4, 4, 4, 4)

                from aqt.webview import AnkiWebView
                web = AnkiWebView(parent=dlg)
                v.addWidget(web)

                self._preview_dlg = dlg
                self._preview_web = web

                # Install event filter to track mouse leave from popup
                dlg.installEventFilter(self)
            else:
                dlg = self._preview_dlg
                web = self._preview_web
                
            self._preview_current_nid = nid
            if position_widget:
                self._preview_stack = []

            if position_widget:
                pos = position_widget.mapToGlobal(QPoint(position_widget.width() + 10, -50))
                # Ajustement si on sort de l'écran bas
                screen = QApplication.primaryScreen().availableGeometry()
                if pos.y() + 300 > screen.height():
                    pos.setY(screen.height() - 320)
                dlg.move(pos)

            addon_package = mw.addonManager.addonFromModule(__name__)
            css_link = '<link rel="stylesheet" href="/_addons/' + addon_package + '/user_files/clickable_cards.css">'
            
            model_css = note.note_type().get('css', '')
            if model_css:
                css_link += f"<style>\n{model_css}\n</style>"
            scoped_css = """
            <style>
                body { margin: 2px; font-size: 13px; overflow-x: hidden; padding: 0 !important; }
                [id*='FSRS'], [id*='fsrs'], .fsrs-info { display: none !important; }
                hr { display: none !important; }
                .edn-nid { display: none !important; }
                .clickable_cards .edn-nid { display: inline !important; }
                .card > *:last-child { margin-bottom: 0 !important; padding-bottom: 0 !important; }
                .card { background: transparent !important; background-color: transparent !important; margin: 0 !important; padding: 5px !important; font-size: 16px !important; line-height: 1.35 !important; max-width: 100% !important; text-align: left; }
                a { font-size: 0.85em !important; }
                .section { display: flex !important; margin: 1px 0 !important; padding: 0 !important; margin-left: 0 !important; margin-right: 0 !important; width: 100% !important; min-height: 0 !important; border-width: 1.5px !important; }
                div[class*='section'] { display: flex !important; }
                .items { margin-left: 8px !important; margin-right: 8px !important; padding: 1px 0 !important; min-height: 0 !important; white-space: normal !important; word-break: break-word !important; }
                .items ul, .items ol { padding-top: 2px !important; padding-bottom: 2px !important; margin-top: 0 !important; margin-bottom: 0 !important; padding-left: 18px !important; }
                .items li { padding-top: 0px !important; padding-bottom: 0px !important; margin-top: 0 !important; margin-bottom: 0 !important; }
                .items ul ul, .items ol ol, .items ul ol, .items ol ul { padding-top: 0 !important; padding-bottom: 0 !important; margin-top: 0 !important; margin-bottom: 0 !important; }
                .items > span ul, .items > span ol { padding-left: 18px !important; margin: 0 !important; padding-top: 0 !important; padding-bottom: 0 !important; }
                .items li > ul, .items li > ol { padding-left: 14px !important; margin: 0 !important; padding-top: 0 !important; padding-bottom: 0 !important; }
                .items > span { display: block !important; }
                .items.answer { padding: 1px 0 !important; }
                .bar { flex: 0 0 28px !important; width: 28px !important; min-height: 28px !important; margin: 0 !important; padding: 0 !important; background-size: 20px !important; border-right-width: 1px !important; }
                .barHider { display: none !important; }
                br { display: block !important; content: '' !important; margin: 2px 0 !important; }
                .clickable_cards { font-size: 13px !important; height: auto !important; line-height: 13px !important; padding: 3px 5px !important; margin: 3px !important; cursor: pointer; }
                .items.cartesLiees { flex-direction: row !important; flex-wrap: wrap !important; align-items: center !important; justify-content: flex-start !important; margin-left: 0 !important; }
            </style>
            """
            
            gui_hover_script = r"""
            <script>
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

            // Fonction toggle pour les sections de la carte
            window.toggle = window.toggle || function(id) {
                var el = document.getElementById(id);
                if (el) el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
            };

            function _gn(el) {
                if (el.dataset && el.dataset.nid) return el.dataset.nid;
                var s = el.querySelector && el.querySelector('.edn-nid');
                if (s) return s.textContent.trim();
                var oc = el.getAttribute('onclick');
                if (oc) { var m = oc.match(/\d{10,}/); if (m) return m[0]; }
                return el.innerText ? el.innerText.replace(/[^0-9]/g, '') : '';
            }

            // Debounce hover pour éviter le scintillement (preview-in-preview)
            var _guiHoverTimer = null;
            var _guiLastHoverNid = null;

            document.addEventListener("mouseover", function(e) {
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    var nid = _gn(e.target);
                    e.target.style.outline = '2px solid #007acc';
                    if (nid === _guiLastHoverNid) return; // même badge, pas besoin de recharger
                    if (_guiHoverTimer) { clearTimeout(_guiHoverTimer); _guiHoverTimer = null; }
                    _guiHoverTimer = setTimeout(function() {
                        _guiHoverTimer = null;
                        _guiLastHoverNid = nid;
                        if(typeof pycmd !== 'undefined') pycmd('gui_preview_hover:' + nid);
                    }, 200);
                }
            });
            document.addEventListener("mouseout", function(e) {
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    if (e.relatedTarget && e.target.contains(e.relatedTarget)) return;
                    if (_guiHoverTimer) { clearTimeout(_guiHoverTimer); _guiHoverTimer = null; }
                    e.target.style.outline = '';
                    _guiLastHoverNid = null;
                }
            });
            document.addEventListener("click", function(e) {
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    var nid = _gn(e.target);
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_click:' + nid);
                }
            });
            document.addEventListener("keydown", function(e) {
                var badges = Array.from(document.querySelectorAll('.clickable_cards'));
                var k = e.key.toLowerCase();

                if (e.key === 'ArrowDown' || k === 'n') {
                    e.preventDefault();
                    var activeBadge = document.querySelector('.edn-selected-badge');
                    var activeIdx = badges.indexOf(activeBadge);
                    
                    badges.forEach(function(b) {
                        b.style.outline = 'none';
                        b.style.boxShadow = 'none';
                        b.style.transform = 'none';
                        b.classList.remove('edn-selected-badge');
                    });
                    
                    var nextIdx = activeIdx + 1;
                    if (nextIdx >= badges.length) nextIdx = 0;
                    
                    var newSel = badges[nextIdx];
                    if (newSel) {
                        newSel.style.outline = '2px solid #3b82f6';
                        newSel.style.outlineOffset = '1px';
                        newSel.style.boxShadow = '0 0 8px rgba(59, 130, 246, 0.5)';
                        newSel.style.transform = 'scale(1.05)';
                        newSel.classList.add('edn-selected-badge');
                        newSel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                    }
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    var activeBadge = document.querySelector('.edn-selected-badge');
                    var activeIdx = badges.indexOf(activeBadge);
                    
                    badges.forEach(function(b) {
                        b.style.outline = 'none';
                        b.style.boxShadow = 'none';
                        b.style.transform = 'none';
                        b.classList.remove('edn-selected-badge');
                    });
                    
                    var nextIdx = activeIdx - 1;
                    if (nextIdx < 0) nextIdx = badges.length - 1;
                    
                    var newSel = badges[nextIdx];
                    if (newSel) {
                        newSel.style.outline = '2px solid #3b82f6';
                        newSel.style.outlineOffset = '1px';
                        newSel.style.boxShadow = '0 0 8px rgba(59, 130, 246, 0.5)';
                        newSel.style.transform = 'scale(1.05)';
                        newSel.classList.add('edn-selected-badge');
                        newSel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                    }
                } else if (k === 'p') {
                    e.preventDefault();
                    if (typeof pycmd !== 'undefined') pycmd('gui_preview_key_p');
                } else if (k === 'r') {
                    e.preventDefault();
                    var sel = document.querySelector('.edn-selected-badge');
                    if (!sel && badges.length > 0) sel = badges[0];
                    if (sel) {
                        var nid = _gn(sel);
                        _guiLastHoverNid = null;
                        if (typeof pycmd !== 'undefined') pycmd('gui_preview_hover:' + nid);
                    }
                } else if (k === 'z' || e.key === 'Escape') {
                    e.preventDefault();
                    if (typeof pycmd !== 'undefined') pycmd('gui_preview_mouseout:esc');
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    var sel = document.querySelector('.edn-selected-badge');
                    if (sel) {
                        var nid = _gn(sel);
                        if (typeof pycmd !== 'undefined') pycmd('gui_preview_click:' + nid);
                    }
                }
            });
            </script>
            """
            
            # stdHtml ne charge pas MathJax automatiquement — on le passe via js=
            # afin que le LaTeX \(...\) et \[...\] soit rendu correctement.
            try:
                web.stdHtml(
                    css_link + scoped_css + gui_hover_script + '<div class="card">' + isolated + '</div>',
                    mathjax=True
                )
            except TypeError:
                web.stdHtml(
                    css_link + scoped_css + gui_hover_script + '<div class="card">' + isolated + '</div>',
                    js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"]
                )

            dlg.show()
            dlg.raise_()
            
            # Dimensionnement initial : commencer petit pour éviter les saccades
            # puis auto-resize rapidement via _quick_resize
            dlg.resize(dlg.width(), 200)
            from aqt.qt import QTimer

            def _quick_resize():
                try:
                    web.evalWithCallback(
                        "var card = document.querySelector('.card'); var h = card ? card.scrollHeight + 30 : document.body.scrollHeight + 30; Math.min(h, 1000);",
                        lambda h: self._adjust_preview_height(dlg, h)
                    )
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
                            "    var card = document.querySelector('.card'); var h = card ? card.scrollHeight + 30 : document.body.scrollHeight + 30;"
                            "    if (typeof pycmd !== 'undefined') pycmd('edn_preview_resize:' + Math.min(h, 1000));"
                            "  }, 50);"
                            "}).catch(function(){});"
                        )
                        # Réajuster la hauteur 150ms après le typeset
                        QTimer.singleShot(150, _quick_resize)
                        # Réajustement tardif pour les formules complexes
                        QTimer.singleShot(500, _quick_resize)
                    elif attempts < 15:
                        QTimer.singleShot(100, lambda: _try_typeset(attempts + 1))
                    else:
                        # MathJax non disponible : redimensionner quand même
                        _quick_resize()
                try:
                    web.evalWithCallback(
                        "typeof MathJax !== 'undefined' && !!MathJax.typesetPromise",
                        _check
                    )
                except Exception:
                    _quick_resize()

            # Ajustements initiaux : rapide (30ms), moyen (100ms), tardif (300ms)
            QTimer.singleShot(30, _quick_resize)
            QTimer.singleShot(100, _quick_resize)
            # Lancement du typeset MathJax
            QTimer.singleShot(100, lambda: _try_typeset())
            # Ajustement final tardif pour les contenus lents
            QTimer.singleShot(500, _quick_resize)
        except Exception as ex:
            from aqt.utils import tooltip
            tooltip("Erreur preview : " + str(ex))

    def _adjust_preview_height(self, dlg, h):
        if h and h > 0:
            new_height = min(1400, max(150, int(h) + 20))
            dlg.resize(dlg.width(), new_height)
        else:
            dlg.resize(dlg.width(), 300)

    def eventFilter(self, obj, event):
        try:
            # Handle key events on child widgets
            try:
                is_keypress = (event.type() == QEvent.Type.KeyPress)
            except AttributeError:
                is_keypress = (event.type() == QEvent.KeyPress)

            if is_keypress:
                if obj != self.search_bar:
                    key = event.key()
                    if key in [Qt.Key.Key_P, Qt.Key.Key_R, Qt.Key.Key_N, Qt.Key.Key_Z]:
                        self.keyPressEvent(event)
                        return True

            # Handle Leave event on preview dialog
            if hasattr(self, '_preview_dlg') and obj == self._preview_dlg:
                try:
                    is_leave = (event.type() == QEvent.Type.Leave)
                except AttributeError:
                    is_leave = (event.type() == QEvent.Leave)
                if is_leave:
                    from aqt.qt import QTimer
                    QTimer.singleShot(150, lambda: self.check_hide_preview())
        except Exception as e:
            log(f"eventFilter error: {e}")
        return super().eventFilter(obj, event)

    def check_hide_preview(self):
        try:
            if hasattr(self, '_preview_dlg') and self._preview_dlg and self._preview_dlg.isVisible():
                pos = QCursor.pos()
                # Build an extended safe zone: union of popup rect + button rect + 15px margin
                safe_rect = QRect(self._preview_dlg.geometry())
                if hasattr(self, '_preview_current_widget') and self._preview_current_widget:
                    try:
                        self._preview_current_widget.objectName()
                        btn_global = self._preview_current_widget.mapToGlobal(QPoint(0, 0))
                        btn_rect = QRect(btn_global, self._preview_current_widget.size())
                        safe_rect = safe_rect.united(btn_rect)
                    except (RuntimeError, Exception):
                        self._preview_current_widget = None
                # Inflate safe zone by 15px on all sides
                safe_rect.adjust(-15, -15, 15, 15)
                if safe_rect.contains(pos):
                    return
                self.hide_preview_popup()
        except RuntimeError:
            pass

    def show_nested_preview(self, nid):
        # Pour les nested links, on remplace temporairement le NID
        if not hasattr(self, '_preview_dlg') or not self._preview_dlg:
            return
        if not hasattr(self, '_preview_stack'):
            self._preview_stack = []
        if self._preview_current_nid != nid:
            self._preview_stack.append(self._preview_current_nid)
            self.show_preview_popup(nid, position_widget=None) # Réutilise la popup sans la déplacer

    def hide_nested_preview(self):
        # On ressort la carte parente
        if hasattr(self, '_preview_stack') and self._preview_stack:
            parent_nid = self._preview_stack.pop()
            self.show_preview_popup(parent_nid, position_widget=None)

    def on_gui_preview_click(self, nid):
        self.hide_preview_popup()
        from aqt import dialogs, mw
        browser = dialogs.open("Browser", mw)
        browser.search_for(f"nid:{nid}")
        self.close()

    def hide_preview_popup(self):
        if hasattr(self, '_preview_dlg') and self._preview_dlg:
            self._preview_dlg.hide()

    def on_selection_changed(self):
        rows = self.results_table.selectionModel().selectedRows()
        self.insert_btn.setEnabled(len(rows) > 0)

    def on_double_click(self, item):
        self.on_selection_changed()
        self.insert_selected()

    def insert_selected(self):
        rows = self.results_table.selectionModel().selectedRows()
        if not rows: return
        
        items_raw = []
        for row in rows:
            data = self.results_table.item(row.row(), 0).data(Qt.ItemDataRole.UserRole)
            items_raw.append((data['nid'], data['recto']))
            
        if not items_raw:
            return
        
        self._is_inserting = True
        self.close()
        
        mirror_req = getattr(self, 'mirror_cb', None) and self.mirror_cb.isChecked()
        # Mode hyperlien : autorisé UNIQUEMENT si une seule carte est sélectionnée
        if self.selected_text and len(items_raw) == 1:
            nid = items_raw[0][0]
            from aqt.qt import QTimer
            QTimer.singleShot(0, lambda: self.inserter.insert_link_with_text(nid, self.selected_text, mirror_requested=mirror_req))
        else:
            # Détection ultra-précise : le tiret doit être juste avant le curseur sur la ligne active
            _dash_detection_js = """
            (function() {
                try {
                    var marker = null;
                    function findMarker(root) {
                        if (!root) return null;
                        if (root.querySelector) {
                            var m = root.querySelector('[id="edn-cursor-marker"]');
                            if (m) return m;
                        }
                        if (root.shadowRoot) { var m = findMarker(root.shadowRoot); if (m) return m; }
                        if (root.children) {
                            for (var i = 0; i < root.children.length; i++) {
                                var m = findMarker(root.children[i]); if (m) return m;
                            }
                        }
                        return null;
                    }
                    marker = findMarker(document.body);
                    if (!marker) {
                        document.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {
                            if (!marker) marker = findMarker(el);
                        });
                    }
                    if (!marker) return false;
                    
                    var node = marker.parentNode;
                    if (!node) return false;
                    var blockParent = node;
                    while (blockParent && blockParent.tagName !== 'ANKI-EDITABLE' && !blockParent.isContentEditable) {
                        if (['LI', 'P', 'DIV'].indexOf(blockParent.tagName) >= 0) break;
                        blockParent = blockParent.parentNode;
                    }
                    if (!blockParent) return false;
                    
                    var children = Array.from(blockParent.childNodes);
                    var cursorIdx = -1;
                    for (var i = 0; i < children.length; i++) {
                        if (children[i] === marker || children[i].contains(marker)) {
                            cursorIdx = i; break;
                        }
                    }
                    // Récupérer uniquement le texte précédant le curseur sur la ligne active
                    var textBefore = [];
                    for (var i = cursorIdx - 1; i >= 0; i--) {
                        if (children[i].tagName === 'BR') break;
                        textBefore.unshift(children[i].textContent || '');
                    }
                    var trimmedBefore = textBefore.join('').trim();
                    // Renvoie vrai si la ligne se termine par un tiret (ignorant les espaces)
                    return trimmedBefore.endsWith('—') || trimmedBefore.endsWith('-') || trimmedBefore.endsWith('\\u2014');
                } catch(e) { return false; }
            })();
            """
            
            def _do_insert(has_dash):
                if has_dash:
                    # Tiret détecté : masquer le recto UNIQUE pour le premier élément (déjà saisi),
                    # mais conserver les rectos des éléments suivants sur des lignes distinctes !
                    items = []
                    for idx, (nid, recto) in enumerate(items_raw):
                        if idx == 0:
                            items.append((nid, None))
                        else:
                            items.append((nid, recto))
                else:
                    items = items_raw
                from aqt.qt import QTimer
                QTimer.singleShot(0, lambda: self.inserter.insert_link(items, mirror_requested=mirror_req))
            
            try:
                self.editor.web.evalWithCallback(
                    _dash_detection_js,
                    _do_insert
                )
            except Exception:
                # Fallback si l'éditeur n'est plus disponible
                from aqt.qt import QTimer
                QTimer.singleShot(0, lambda: self.inserter.insert_link(items_raw, mirror_requested=mirror_req))

def open_search_dialog(editor, selected_text=""):
    global _active_dialog
    if not editor: return

    # Sauvegarder : range courant + index du champ actif
    editor.web.eval("""
        (function() {
            // Nettoyer les anciens markers en premier — chercher aussi dans les shadow DOM
            function _cleanMarkers() {
                let markers = Array.from(document.querySelectorAll('[id="edn-cursor-marker"]'));
                document.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {
                    if (el.shadowRoot) {
                        markers = markers.concat(Array.from(el.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                        el.shadowRoot.querySelectorAll('anki-editable').forEach(inner => {
                           if (inner.shadowRoot) markers = markers.concat(Array.from(inner.shadowRoot.querySelectorAll('[id="edn-cursor-marker"]')));
                        });
                    }
                });
                markers.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });
            }

            // Si _ednSavedRange est déjà défini (par handle_editor_button), le conserver
            // et juste replacer le marker à cet endroit. Ne pas écraser le range sauvegardé.
            let hadSavedRange = !!(window._ednSavedRange);

            if (!hadSavedRange) {
                // Pas de range sauvegardé : en capturer un maintenant
                let active = document.activeElement;
                let sel = null;
                if (active && active.shadowRoot) sel = active.shadowRoot.getSelection();
                else sel = window.getSelection();

                if (window._ednPersistentRange) {
                    window._ednSavedRange = window._ednPersistentRange;
                } else if (sel && sel.rangeCount > 0) {
                    window._ednSavedRange = sel.getRangeAt(0).cloneRange();
                }
            }

            // Nettoyer les anciens markers avant d'en placer un nouveau
            _cleanMarkers();

            if (window._ednSavedRange) {
                try {
                    let r = window._ednSavedRange.cloneRange();
                    r.deleteContents();
                    let marker = document.createElement("span");
                    marker.id = "edn-cursor-marker";
                    r.insertNode(marker);
                } catch(e) {}
            }

            // Sauvegarder l'index du champ actif pour restauration apres fermeture dialog
            window._ednSavedFieldIndex = -1;
            let editables = Array.from(document.querySelectorAll('anki-editable'));
            for (let i = 0; i < editables.length; i++) {
                let el = editables[i];
                if (window._ednSavedRange) {
                    let node = window._ednSavedRange.commonAncestorContainer;
                    while (node) {
                        if (node === el) {
                            window._ednSavedFieldIndex = i;
                            break;
                        }
                        node = node.parentNode || (node.getRootNode && node.getRootNode().host);
                    }
                    if (window._ednSavedFieldIndex >= 0) break;
                }
                if (document.activeElement === el || (el.shadowRoot && el.shadowRoot.activeElement)) {
                    window._ednSavedFieldIndex = i;
                    break;
                }
            }

            // Détecter si la LIGNE courante (entre deux <br>) contient déjà un titre (em dash —)
            // Si oui, on n'ajoutera pas le recto lors de l'insertion
            window._ednCurrentLineHasDash = false;
            try {
                if (window._ednSavedRange) {
                    let container = window._ednSavedRange.startContainer;
                    // Extraire uniquement le texte de la ligne courante (entre <br>)
                    // En parcourant les siblings avant et après le curseur
                    let lineText = '';
                    let node = container;
                    if (node && node.nodeType === 3) {
                        node = node.parentNode;
                    }
                    if (node) {
                        // Collecter le texte de la ligne : remonter jusqu'au <br> ou début du parent
                        let lineNodes = [];
                        // Trouver le conteneur de bloc (le parent éditable direct)
                        let blockParent = node;
                        while (blockParent && blockParent.tagName !== 'ANKI-EDITABLE' && !blockParent.isContentEditable) {
                            if (['LI', 'P', 'DIV'].indexOf(blockParent.tagName) >= 0) break;
                            blockParent = blockParent.parentNode;
                        }
                        if (blockParent) {
                            // Parcourir tous les childNodes du bloc et extraire la ligne contenant le curseur
                            let children = Array.from(blockParent.childNodes);
                            let cursorIdx = -1;
                            // Trouver l'index du noeud contenant le curseur
                            let cursorNode = window._ednSavedRange.startContainer;
                            for (let i = 0; i < children.length; i++) {
                                if (children[i] === cursorNode || children[i].contains(cursorNode) || children[i] === node) {
                                    cursorIdx = i;
                                    break;
                                }
                            }
                            // Collecter le texte en arrière jusqu'au <br>
                            let parts = [];
                            for (let i = cursorIdx; i >= 0; i--) {
                                if (children[i].tagName === 'BR') break;
                                parts.unshift(children[i].textContent || '');
                            }
                            // Collecter le texte en avant jusqu'au <br>
                            for (let i = cursorIdx + 1; i < children.length; i++) {
                                if (children[i].tagName === 'BR') break;
                                parts.push(children[i].textContent || '');
                            }
                            lineText = parts.join('');
                        }
                        window._ednCurrentLineHasDash = lineText.includes('\u2014') || lineText.includes('—');
                    }
                }
            } catch(e) {}
        })();
    """)

    try:
        if _active_dialog: _active_dialog.close()
    except: pass
    _active_dialog = LinkedCardsDialog(editor, selected_text=selected_text)
    _active_dialog.show()
    _active_dialog.activateWindow()
    # Positionner la fenêtre sur la moitié gauche de l'écran
    try:
        screen = QApplication.primaryScreen().availableGeometry()
        _active_dialog.move(screen.x(), screen.y())
        _active_dialog.resize(screen.width() // 2, screen.height() - 80)
    except Exception:
        pass

def open_search_dialog_from_menu():
    from .edn_assistant import get_active_editor
    editor = get_active_editor()
    if editor:
        open_search_dialog(editor)
    else:
        from aqt.utils import tooltip
        tooltip("Aucun éditeur actif. Ouvrez d'abord une carte en édition.")

def copy_nid_from_active_browser():
    from aqt import mw, dialogs
    from aqt.utils import tooltip
    browser = dialogs._dialogs.get("Browser")
    if browser and isinstance(browser, tuple):
        browser = browser[1]
    
    if browser and hasattr(browser, 'selected_cards'):
        copy_nid_from_browser(browser)
    else:
        tooltip("Aucun browser ouvert ou aucune carte sélectionnée.")

# --- Settings Widget (Menu EDN) ---

class LinkedCardsConfigWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        config = mw.addonManager.getConfig(__name__) or {}
        
        # --- Raccourcis ---
        group_shortcuts = QGroupBox("Raccourcis personnalisés")
        g_layout = QFormLayout()
        group_shortcuts.setLayout(g_layout)
        from .edn_menu import get_shortcut
        
        self.edit_trigger = QLineEdit(config.get("search_trigger", "nid:"))
        g_layout.addRow("Symbole texte déclencheur recherche:", self.edit_trigger)
        
        self.edit_search = QLineEdit(get_shortcut("linked_cards_search", "Ctrl+Alt+L"))
        g_layout.addRow("Appeler Fenêtre Liste Liens (Éditeur):", self.edit_search)
        
        self.edit_copy = QLineEdit(get_shortcut("linked_cards_copy", "Ctrl+Alt+C"))
        g_layout.addRow("Copier directement le NID (Explorateur):", self.edit_copy)
        
        self.edit_liens = QLineEdit(get_shortcut("linked_cards_liens", "Ctrl+Alt+B"))
        g_layout.addRow("Aller au champ Liens (Explorateur/Éditeur):", self.edit_liens)
        
        self.edit_kbd_navigate = QLineEdit(get_shortcut("linked_cards_kbd_navigate", "r"))
        g_layout.addRow("Touche : Naviguer entre les badges:", self.edit_kbd_navigate)

        self.edit_kbd_open = QLineEdit(get_shortcut("linked_cards_kbd_open", "n"))
        g_layout.addRow("Touche : Ouvrir carte focusée dans l'explorateur:", self.edit_kbd_open)

        self.edit_kbd_preview = QLineEdit(get_shortcut("linked_cards_kbd_preview", "p"))
        g_layout.addRow("Touche : Afficher preview carte focusée:", self.edit_kbd_preview)

        self.edit_kbd_back = QLineEdit(get_shortcut("linked_cards_kbd_back", "z"))
        g_layout.addRow("Touche : Retour arrière dans la preview:", self.edit_kbd_back)

        layout.addWidget(group_shortcuts)
        
        # --- Blacklist ---
        group_blacklist = QGroupBox("Éléments à masquer dans la Miniature de Profil (Survol)")
        b_layout = QVBoxLayout()
        group_blacklist.setLayout(b_layout)
        
        hidden_sections = config.get("hidden_preview_sections", [
            "sourcesMegaContainer",
            "commentsMegaContainer",
            "tagsMegaContainer",
            "erreursFaitesMegaContainer",
            "mnemonicsMegaContainer",
            "infoSupplementairesMegaContainer"
        ])
        
        self.checkboxes = {}
        target_fields = {
            "sourcesMegaContainer": "Masquer : Sources",
            "commentsMegaContainer": "Masquer : Commentaires",
            "tagsMegaContainer": "Masquer : Tags",
            "cartesLieesMegaContainer": "Masquer : Cartes Liées",
            "erreursFaitesMegaContainer": "Masquer : Erreurs Précédentes",
            "mnemonicsMegaContainer": "Masquer : Mnémotechniques",
            "infoSupplementairesMegaContainer": "Masquer : Mots Clefs / Infos"
        }
        
        for box_id, box_label in target_fields.items():
            cb = QCheckBox(box_label)
            cb.setChecked(box_id in hidden_sections)
            b_layout.addWidget(cb)
            self.checkboxes[box_id] = cb
            
        layout.addWidget(group_blacklist)
        
        # --- Lien miroir ---
        group_mirror = QGroupBox("Lien miroir")
        m_layout = QVBoxLayout()
        group_mirror.setLayout(m_layout)
        
        self.mirror_link_cb = QCheckBox("Lien miroir systématique (activé par défaut)")
        self.mirror_link_cb.setToolTip(
            "Si coché, propose automatiquement le lien retour A↔B dans l'éditeur.\n"
            "Sinon, vous devrez cocher la case dans la fenêtre de recherche."
        )
        self.mirror_link_cb.setChecked(config.get("mirror_link_systematic", False))
        m_layout.addWidget(self.mirror_link_cb)
        
        layout.addWidget(group_mirror)
        layout.addStretch()

    def save_config(self):
        config = mw.addonManager.getConfig(__name__) or {}
        config["search_trigger"] = self.edit_trigger.text()
        
        from .edn_menu import set_shortcut
        set_shortcut("linked_cards_search", self.edit_search.text())
        set_shortcut("linked_cards_copy", self.edit_copy.text())
        set_shortcut("linked_cards_liens", self.edit_liens.text() or "Ctrl+Alt+B")

        set_shortcut("linked_cards_kbd_navigate", self.edit_kbd_navigate.text() or "r")
        set_shortcut("linked_cards_kbd_open", self.edit_kbd_open.text() or "n")
        set_shortcut("linked_cards_kbd_preview", self.edit_kbd_preview.text() or "p")
        set_shortcut("linked_cards_kbd_back", self.edit_kbd_back.text() or "z")
        
        hiddens = [box_id for box_id, cb in self.checkboxes.items() if cb.isChecked()]
        config["hidden_preview_sections"] = hiddens
        
        config["mirror_link_systematic"] = self.mirror_link_cb.isChecked()
        
        mw.addonManager.writeConfig(__name__, config) 

def _patch_editor_save_now():
    """Patches Editor.call_after_note_saved (saveNow) to be robust against undefined JS saveNow.
    This prevents 'Uncaught ReferenceError: saveNow is not defined' which can happen if the
    editor is closed/destroyed before Svelte is fully initialized, or when called on a non-editor webview.
    """
    from aqt.editor import Editor
    
    # Store original method
    original_call_after_note_saved = Editor.call_after_note_saved
    
    def patched_call_after_note_saved(self, callback, keepFocus=False):
        # Quick check for deleted or uninitialized webview
        if not getattr(self, "web", None):
            original_call_after_note_saved(self, callback, keepFocus)
            return
            
        try:
            from aqt.qt import sip
            if sip.isdeleted(self.web):
                from aqt import mw
                mw.progress.single_shot(10, callback)
                return
        except:
            pass
            
        def on_check(defined):
            if defined:
                original_call_after_note_saved(self, callback, keepFocus)
            else:
                # saveNow is not defined in JS (e.g. editor is closing/loading or it's a non-editor webview).
                # Run the callback directly via single_shot to maintain asynchronous execution contract.
                from aqt import mw
                mw.progress.single_shot(10, callback)
                
        try:
            self.web.evalWithCallback("typeof saveNow !== 'undefined'", on_check)
        except Exception:
            # Fallback if eval fails or object is destroyed
            original_call_after_note_saved(self, callback, keepFocus)
            
    # Apply patches
    Editor.call_after_note_saved = patched_call_after_note_saved
    Editor.saveNow = patched_call_after_note_saved
