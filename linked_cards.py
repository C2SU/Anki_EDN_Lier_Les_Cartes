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
    
    # Protection crash changement de thème
    gui_hooks.theme_did_change.append(_on_theme_changed)
    
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

_add_to_card_script_body = """
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
})();
function cards_ct_click(nid) {
    if(typeof pycmd !== 'undefined') pycmd("cards_ct_click" + nid);
}

// -- Aperçu des cartes liées au survol -- (guard : enregistrement unique des listeners) --

// -- Raccourcis clavier badges liés (mis à jour à chaque carte) --
window._ednKbdOpen = window._ednKbdOpenCfg || 'n';
window._ednKbdPreview = window._ednKbdPreviewCfg || 'p';
window._ednKbdNavigate = window._ednKbdNavigateCfg || 'Tab';

if (!window._ednListenersAttached) {
    window._ednListenersAttached = true;

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
        
        // P : cycle à travers les badges de la carte mère et afficher preview
        if (e.key === kbdPreview || e.key === 'p') {
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
                var nid = targetBadge.innerText.trim();
                window._ednPositionLocked = false;
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + nid);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_hover:' + nid);
            }
            return;
        }
        
        // N : Cycle dans les badges de la preview UNIQUEMENT (jamais la carte mère)
        if (e.key === kbdOpen || e.key === 'n' || e.key === 'N') {
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
        if (e.key === kbdNavigate || e.key === 'r' || e.key === 'R') {
            if (!previewVisible) return; // R ne fait rien hors preview
            e.preventDefault();
            e.stopImmediatePropagation();
            
            var selectedNode = previewBox.querySelector('.edn-selected-badge');
            
            // Si un badge preview est sélectionné et différent du parent → ouvrir sa preview
            if (selectedNode && selectedNode !== window._edn_parent_badge) {
                var nid3 = selectedNode.innerText.trim();
                // Sauvegarder l'historique pour Z
                if (window._edn_hover_target) {
                    var currentNid = window._edn_hover_target.innerText ? window._edn_hover_target.innerText.trim() : '';
                    if (currentNid && window._ednPreviewHistory.indexOf(currentNid) === -1) {
                        window._ednPreviewHistory.push(currentNid);
                    }
                }
                window._edn_hover_target = selectedNode;
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
        if (e.key === kbdBack || e.key === 'z' || e.key === 'Z') {
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
            box.style.cssText = "position:fixed;z-index:99999;display:none;max-height:60vh;overflow-y:auto;";
            document.body.appendChild(box);
            box.addEventListener("mouseenter", function() {
                if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
                if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
            });
            box.addEventListener("mouseleave", function() {
                window._ednHideTimer = setTimeout(function() {
                    box.style.display = "none";
                    window._edn_hover_target = null;
                    window._edn_parent_badge = null;
                    window._ednPositionLocked = false;
                    window._ednHideTimer = null;
                }, 80);
            });
        }
        return box;
    };

    // Delegated click handler on the preview box for reliable click-before-hover
    // This fires immediately on click, cancelling any pending hover navigation.
    document.addEventListener('click', function(e) {
        if (!e.target || !e.target.classList || !e.target.classList.contains('clickable_cards')) return;
        var box = document.getElementById('edn-preview-box');
        if (!box || !box.contains(e.target)) return;
        // Click on a badge inside the preview box → open in browser
        e.preventDefault();
        e.stopImmediatePropagation();
        // Cancel any pending hover that would replace the content
        if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
        window._ednPreviewBadgeClicked = true;
        var nid = e.target.innerText.trim();
        if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
        else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
    }, true); // capture phase = fires before any other click handler

    document.addEventListener("mouseover", function(e) {
        if(e.target && e.target.classList.contains("clickable_cards")) {
            var box = document.getElementById('edn-preview-box');
            if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
            if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
            window._ednPreviewBadgeClicked = false;
            var target = e.target;
            var nid = target.innerText.trim();

            // Badge inside the preview box: hover-navigate with generous delay
            // so the user has time to click before the preview changes.
            if (box && box.contains(e.target)) {
                window._ednHoverTimer = setTimeout(function() {
                    if (window._ednPreviewBadgeClicked) return; // click won the race
                    // Save history for Z-back
                    if (window._edn_hover_target) {
                        var currentNid = window._edn_hover_target.innerText ? window._edn_hover_target.innerText.trim() : '';
                        if (!window._ednPreviewHistory) window._ednPreviewHistory = [];
                        if (currentNid && window._ednPreviewHistory.indexOf(currentNid) === -1) {
                            window._ednPreviewHistory.push(currentNid);
                        }
                    }
                    window._edn_hover_target = target;
                    if(typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + nid);
                    else if(typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_hover:' + nid);
                }, 100);
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
    });

    document.addEventListener("mouseout", function(e) {
        if(e.target && e.target.classList.contains("clickable_cards")) {
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
            }, 80);
        }
    });

    document.addEventListener("click", function(e) {
        var box = document.getElementById("edn-preview-box");
        if(box && !box.contains(e.target)) {
            box.style.display = "none";
            window._edn_hover_target = null;
            window._edn_parent_badge = null;
            window._ednPositionLocked = false;
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
            var leftPos = rect.right + 10;
            if (leftPos + 400 > window.innerWidth) { leftPos = window.innerWidth - 420; }
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
        box.innerHTML = tempDiv.innerHTML;
        box.style.display = "block";
        box.style.overflowY = "auto";
        
        // Ajuster la position : seulement si changement de cible majeure ou si non locké
        if (window._edn_parent_badge) {
            // Toujours positionner par rapport au badge parent (rouge)
            var rect = window._edn_parent_badge.getBoundingClientRect();
            var topPos = rect.top;
            var leftPos = rect.right + 10;
            if (leftPos + 400 > window.innerWidth) { leftPos = window.innerWidth - 420; }
            if (leftPos < 0) leftPos = 10;
            if (!(rect.top === 0 && rect.bottom === 0)) {
                box.style.top = topPos + "px";
                box.style.left = leftPos + "px";
            }
            window._ednPositionLocked = true;
        } else if (window._edn_hover_target && !window._ednPositionLocked) {
            var rect = window._edn_hover_target.getBoundingClientRect();
            var topPos = rect.top;
            var leftPos = rect.right + 10;
            if (leftPos + 400 > window.innerWidth) { leftPos = window.innerWidth - 420; }
            if (leftPos < 0) leftPos = 10;
            if (!(rect.top === 0 && rect.bottom === 0)) {
                box.style.top = topPos + "px";
                box.style.left = leftPos + "px";
                window._ednPositionLocked = true;
            }
        }
        
        // Étape 1 : ajustement rapide de la hauteur (avant MathJax)
        requestAnimationFrame(function() {
            var contentHeight = box.scrollHeight;
            var maxAllowed = window.innerHeight * 0.6;
            box.style.maxHeight = Math.min(contentHeight + 10, maxAllowed) + "px";
        });
        
        // MathJax typesetting : on vide d'abord l'état précédent du box (typesetClear)
        // pour forcer MathJax à retraiter le nouveau contenu, puis on appelle typesetPromise.
        // On utilise Promise.resolve() pour différer après le rendu synchrone courant.
        Promise.resolve().then(function() {
            if (typeof MathJax === 'undefined' || !MathJax.typesetPromise) return;
            try {
                // typesetClear indique à MathJax que ce nœud n'est pas encore traité
                if (MathJax.typesetClear) { MathJax.typesetClear([box]); }
                return MathJax.typesetPromise([box]);
            } catch(e) { return Promise.resolve(); }
        }).then(function() {
            // Réajuster la hauteur après rendu LaTeX (les formules prennent plus de place)
            if (!box) return;
            var contentHeight = box.scrollHeight;
            var maxAllowed = window.innerHeight * 0.6;
            box.style.maxHeight = Math.min(contentHeight + 10, maxAllowed) + "px";
        }).catch(function(){});
        
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



def on_card_render(output, context):
    script = build_add_to_card_script()
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
    
    if nav_key == "r":
        # Trouver et retirer le raccourci 'r' par défaut (replay audio)
        original_r_handlers = [s for s in shortcuts if s[0] == 'r']
        for handler in original_r_handlers:
            shortcuts.remove(handler)
        
        def _edn_r_key_handler():
            """Handler pour 'r' : preview EDN en priorité, replay audio en fallback.
            
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
                    var nid = selectedNode.innerText.trim();
                    // Sauvegarder l'historique pour Z
                    if (window._edn_hover_target) {
                        var currentNid = window._edn_hover_target.innerText ? window._edn_hover_target.innerText.trim() : '';
                        if (!window._ednPreviewHistory) window._ednPreviewHistory = [];
                        if (currentNid && window._ednPreviewHistory.indexOf(currentNid) === -1) {
                            window._ednPreviewHistory.push(currentNid);
                        }
                    }
                    window._edn_hover_target = selectedNode;
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
        
        shortcuts.append(('r', _edn_r_key_handler))


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
    if message.startswith("cards_ct_click"):
        if isinstance(context, CardLayout):
            tooltip("Liens désactivés dans l'éditeur de type.")
            return (True, None)
        nid = message.replace("cards_ct_click", "")
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
                
                isolated = re.sub(r'id=["\'](.*?)["\']', r'id="edn_preview_\1"', rendered_a)
                isolated = re.sub(r"toggle\(['\"](.*?)['\"]\)", r"toggle('edn_preview_\1')", isolated)
                isolated = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', isolated, flags=re.IGNORECASE)
                isolated = re.sub(r'<style\b[^>]*>.*?</style>', '', isolated, flags=re.IGNORECASE | re.DOTALL)
                
                # Supprimer le bloc de statut FSRS qui ajoute énormément d'espace en bas
                isolated = re.sub(r'(?:<br>\s*)?<span[^>]*id=["\'][^"\']*FSRS[^"\']*["\'][^>]*>.*?</span>', '', isolated, flags=re.IGNORECASE | re.DOTALL)
                
                # Forcer l'affichage SEULEMENT des sections (pour éviter de révéler les métadonnées/licences cachées)
                isolated = re.sub(r'(<div[^>]*class=["\'][^"\']*section[^"\']*["\'][^>]*style=["\'])display:\s*none;?([^"\']*["\'])', r'\1display: flex !important;\2', isolated, flags=re.IGNORECASE)

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
                # Ces sélecteurs génériques DOIVENT être scopés à #edn-preview-box
                scoped_css = """
                    #edn-preview-box span[id*='FSRS'] { display: none !important; }
                    #edn-preview-box hr { display: none !important; }
                    #edn-preview-box .edn-preview-isolated > *:last-child { margin-bottom: 0 !important; padding-bottom: 0 !important; }
                    #edn-preview-box .edn-preview-isolated { overflow: hidden; padding: 0 !important; }
                    #edn-preview-box .card { background: transparent !important; background-color: transparent !important; margin: 0 !important; padding: 5px !important; font-size: 14px !important; line-height: 1.3 !important; max-width: 100% !important; text-align: left !important; }
                    #edn-preview-box a { font-size: 0.85em !important; }
                    #edn-preview-box .section { display: flex !important; margin: 1px 0 !important; padding: 0 !important; margin-left: 0 !important; margin-right: 0 !important; width: 100% !important; min-height: 0 !important; border-width: 1.5px !important; }
                    #edn-preview-box div[class*='section'] { display: flex !important; }
                    #edn-preview-box .items { margin-left: 8px !important; margin-right: 8px !important; padding: 1px 0 !important; min-height: 0 !important; }
                    #edn-preview-box .items ul, #edn-preview-box .items ol { padding-top: 5px !important; padding-bottom: 5px !important; margin-top: 0 !important; margin-bottom: 0 !important; }
                    #edn-preview-box .items li { padding-top: 1px !important; padding-bottom: 1px !important; }
                    #edn-preview-box .bar { flex: 0 0 24px !important; width: 24px !important; min-height: 24px !important; margin: 0 !important; padding: 0 !important; background-size: 18px !important; border-right-width: 1px !important; }
                    #edn-preview-box .barHider { display: none !important; }
                    #edn-preview-box br { line-height: 1px !important; margin: 0 !important; }
                    #edn-preview-box .clickable_cards {
                        font-size: 11px !important;
                        height: 11px !important;
                        line-height: 11px !important;
                        padding: 3px !important;
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
    
    action_copy = QAction("📋 Copier NID", browser)
    action_copy.triggered.connect(lambda: _copy_nid_smart(browser))
    
    action_search = QAction("🔗 Rechercher Liens", browser)
    action_search.triggered.connect(lambda: _open_search_smart(browser))
    
    # Save reference for context menu
    browser.edn_copy_nid_action = action_copy
    browser.edn_search_action = action_search
    
    # Add to browser window (no shortcut on QAction to avoid conflicts)
    browser.addAction(action_copy)
    browser.addAction(action_search)
    
    # Un seul QShortcut sur le browser, avec comportement contextuel
    sc_copy = QShortcut(QKeySequence(shortcut_copy), browser)
    sc_copy.setContext(Qt.ShortcutContext.WindowShortcut)
    sc_copy.activated.connect(lambda: _copy_nid_smart(browser))
    
    sc_search = QShortcut(QKeySequence(shortcut_search), browser)
    sc_search.setContext(Qt.ShortcutContext.WindowShortcut)
    sc_search.activated.connect(lambda: _open_search_smart(browser))
    
    # Keep references to prevent GC
    browser._edn_sc_copy = sc_copy
    browser._edn_sc_search = sc_search



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
        QApplication.clipboard().setText(str(editor.note.id))
        tooltip(f"NID copié: {editor.note.id}")

def add_to_browser_context_menu(browser: Browser, menu: QMenu):
    if hasattr(browser, 'edn_copy_nid_action'):
        menu.addAction(browser.edn_copy_nid_action)

# --- Editor Integration ---

def on_editor_buttons(buttons, editor):
    # register_module déjà appelé dans init_linked_cards — pas besoin de répéter
    shortcut_legacy = get_shortcut("linked_cards_search", "Ctrl+Alt+L")
    
    btn_link = editor.addButton(
        icon=None,
        cmd="edn_linked_search",
        func=lambda e=editor: handle_editor_button(e),
        tip=f"Lier carte / Rechercher ({shortcut_legacy})",
        label="Lier",
    )
    buttons.append(btn_link)
    
    return buttons

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
            if (active && active.shadowRoot) sel = active.shadowRoot.getSelection();
            else sel = window.getSelection();

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
            create_link_for_nid(editor, nid, with_recto=True)
            return
            
    open_search_dialog(editor)

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

    # Désactiver les QShortcuts installés par setup_browser_menu (évite l'ambiguïté Qt)
    for browser_attr in ('_edn_sc_copy', '_edn_sc_search'):
        old_browser_sc = getattr(parent_win, browser_attr, None)
        if old_browser_sc:
            try:
                old_browser_sc.setEnabled(False)
            except:
                pass

    # Supprimer les anciens shortcuts EDN éditeur de cette fenêtre pour éviter les doublons
    for attr in ('_edn_sc_search_editor', '_edn_sc_copy_editor'):
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

    parent_win._edn_sc_search_editor = sc_search
    parent_win._edn_sc_copy_editor   = sc_copy


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
    js_hover = """
    (function() {
        if (window._ednEditorHoverInit) return;
        window._ednEditorHoverInit = true;

        window._ednHoverTimer = null;
        window._ednHideTimer = null;
        window._ednPositionLocked = false;
        window._edn_hover_target = null;

        window._ednGetOrCreateBox = function() {
            var box = document.getElementById("edn-preview-box");
            if (!box) {
                box = document.createElement("div");
                box.id = "edn-preview-box";
                box.style.cssText = "position:fixed;z-index:99999;display:none;max-height:60vh;overflow-y:auto;background:#fff;border:1px solid #ccc;border-radius:6px;padding:4px;box-shadow:0 4px 16px rgba(0,0,0,0.18);min-width:300px;max-width:480px;";
                document.body.appendChild(box);
                box.addEventListener("mouseenter", function() {
                    if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
                    if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
                });
                box.addEventListener("mouseleave", function() {
                    window._ednHideTimer = setTimeout(function() {
                        box.style.display = "none";
                        window._edn_hover_target = null;
                        window._ednPositionLocked = false;
                        window._ednHideTimer = null;
                    }, 80);
                });
            }
            return box;
        };

        window.show_edn_preview = function(html) {
            var box = window._ednGetOrCreateBox();
            var tempDiv = document.createElement('div');
            tempDiv.innerHTML = html;
            // Extraire les styles et les appliquer au head
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
            box.innerHTML = tempDiv.innerHTML;
            box.style.display = "block";
            box.style.overflowY = "auto";
            if (window._edn_hover_target && !window._ednPositionLocked) {
                var rect = window._edn_hover_target.getBoundingClientRect();
                var topPos = rect.top;
                var leftPos = rect.right + 10;
                if (leftPos + 400 > window.innerWidth) { leftPos = window.innerWidth - 420; }
                if (leftPos < 0) leftPos = 10;
                box.style.top = topPos + "px";
                box.style.left = leftPos + "px";
                window._ednPositionLocked = true;
            }
            setTimeout(function() {
                var kbdOpen = window._ednKbdOpenCfg || window._ednKbdOpen || 'n';
                document.dispatchEvent(new KeyboardEvent('keydown', {key: kbdOpen, bubbles: true}));
            }, 50);
        };

        document.addEventListener("mouseover", function(e) {
            if (e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
                if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
                var target = e.target;
                var nid = target.innerText.trim();
                window._ednHoverTimer = setTimeout(function() {
                    window._edn_hover_target = target;
                    if (typeof pycmd !== 'undefined') pycmd("cards_ct_hover:" + nid);
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand("cards_ct_hover:" + nid);
                }, 150);
            }
        });

        document.addEventListener("mouseout", function(e) {
            if (e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                var box = window._ednGetOrCreateBox();
                if (box && box.contains(e.relatedTarget)) return;
                if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
                window._ednHideTimer = setTimeout(function() {
                    if (box) box.style.display = "none";
                    window._edn_hover_target = null;
                    window._ednPositionLocked = false;
                    window._ednHideTimer = null;
                }, 80);
            }
        });

        document.addEventListener("click", function(e) {
            var box = document.getElementById("edn-preview-box");
            if (box && !box.contains(e.target)) {
                box.style.display = "none";
                window._edn_hover_target = null;
                window._ednPositionLocked = false;
            }
        });

        document.addEventListener("keydown", function(e) {
            if (e.key === 'Escape') {
                var box = document.getElementById("edn-preview-box");
                if (box && box.style.display !== 'none') {
                    e.preventDefault();
                    box.style.display = "none";
                    window._edn_hover_target = null;
                    window._ednPositionLocked = false;
                }
            }
        });
    })();
    """
    editor.web.eval(js_hover)

    # Ajouter QShortcut sur la fenêtre parent de l'éditeur (plus fiable qu'editor_did_init_shortcuts seul)
    _setup_editor_window_shortcuts(editor)
    
    js = f"""
    (function() {{
        if (window._ednnidInit) return;
        window._ednnidInit = true;
        window._ednBuffer = '';
        window._ednPreventing = false;
        window._ednTrigger = '{trigger}';
        window._ednTriggerLen = {trigger_len};
        
        document.addEventListener('keydown', function(e) {{
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


def on_js_message(handled, message, context):
    if message == "edn_nid_trigger":
        editor = context if isinstance(context, Editor) else _current_editor
        if editor:
            open_search_dialog(editor)
        return (True, None)
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
    return handled

def strip_html(text):
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = clean.replace('&nbsp;', ' ')
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()

class LinkInserter:
    def __init__(self, editor):
        self.editor = editor
        
    def insert_link(self, items):
        is_direct_replacement = all(recto is None for _, recto in items)
        
        html_parts = []
        for nid, recto in items:
            if recto is None:
                html_parts.append(f'<kbd class="clickable_cards" tabindex="0" onclick="cards_ct_click(\'{nid}\')" ondblclick="cards_ct_click(\'{nid}\')">{nid}</kbd>')
            else:
                recto_escaped = recto.replace('"', '&quot;').replace("'", "\\'")
                html_parts.append(f'{recto_escaped}&nbsp;—&nbsp;<kbd class="clickable_cards" tabindex="0" onclick="cards_ct_click(\'{nid}\')" ondblclick="cards_ct_click(\'{nid}\')">{nid}</kbd>')
                
        html = "<br>".join(html_parts)
        if html:
            html += "&nbsp;"
        
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


class LinkedCardsDialog(QDialog):
    def __init__(self, editor):
        parent = editor.parentWindow if hasattr(editor, 'parentWindow') else mw
        super().__init__(parent)
        self.editor = editor
        self.inserter = LinkInserter(editor)
        self.selected_nid = None
        self.selected_recto = None
        self.setWindowTitle("Rechercher une carte liée")
        self.setMinimumSize(800, 500)
        self.setModal(False)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Rechercher dans les cartes...")
        self.search_bar.textChanged.connect(self.do_search)
        self.search_bar.returnPressed.connect(self.insert_selected)
        layout.addWidget(self.search_bar)
        
        self.results_label = QLabel("Tapez pour rechercher...")
        layout.addWidget(self.results_label)
        
        self.results_table = QTableWidget()
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
        
    def closeEvent(self, event):
        self._cleanup_on_close()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_on_close()
        super().reject()

    def _cleanup_on_close(self):
        # Fermer la preview popup si elle est ouverte
        self.hide_preview_popup()
        if hasattr(self, '_preview_web') and self._preview_web:
            try:
                self._preview_web.cleanup()
            except:
                pass
            self._preview_web = None
        if hasattr(self, '_preview_dlg') and self._preview_dlg:
            self._preview_dlg.deleteLater()
            self._preview_dlg = None
        if getattr(self, '_is_inserting', False):
            return
        # Clean up the marker on close if it was not consumed string link insertion.
        self.editor.web.eval("""
            (function() {
                function findAllMarkers(root, found) {
                    if (!root) return;
                    if (root.querySelectorAll) {
                        let ms = root.querySelectorAll('[id="edn-cursor-marker"]');
                        for (let i = 0; i < ms.length; i++) {
                            if (!found.includes(ms[i])) found.push(ms[i]);
                        }
                    }
                    if (root.shadowRoot) findAllMarkers(root.shadowRoot, found);
                    if (root.children) {
                        for (let i = 0; i < root.children.length; i++) findAllMarkers(root.children[i], found);
                    }
                }
                let markers = [];
                findAllMarkers(document.body, markers);
                document.querySelectorAll('anki-editable, anki-editor').forEach(el => findAllMarkers(el, markers));
                
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
                        editable.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
                    } catch(e) {}
                });
            })();
        """)
    @perf_log
    def do_search(self, query):
        if len(query) < 2:
            self.results_table.setRowCount(0)
            return
            
        self.results_table.setRowCount(0)
        try:
            nids = mw.col.find_notes(f"*{query}*")
            count = 0
            for nid in nids:
                if count >= 50: break
                try:
                    note = mw.col.get_note(nid)
                    recto = note.fields[0] if note.fields else ""
                    recto_clean = strip_html(recto)[:240] or "[Vide]"
                    
                    row = self.results_table.rowCount()
                    self.results_table.insertRow(row)
                    
                    i_recto = QTableWidgetItem(recto_clean)
                    i_recto.setData(Qt.ItemDataRole.UserRole, {'nid': str(nid), 'recto': recto_clean})
                    self.results_table.setItem(row, 0, i_recto)
                    
                    self.results_table.setItem(row, 1, QTableWidgetItem(str(nid)))
                    
                    class HoverButton(QPushButton):
                        def __init__(self, text, nid, dialog_parent):
                            super().__init__(text)
                            self.nid = nid
                            self.dialog_parent = dialog_parent
                            self.setCursor(Qt.CursorShape.PointingHandCursor)
                            self.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; border-radius: 4px;")
                            self.clicked.connect(self.on_click)
                            
                        def on_click(self):
                            # Sur click, on ouvre la carte dans le navigateur Anki
                            # Le GUI reste ouvert pour continuer la navigation
                            from aqt import dialogs, mw
                            browser = dialogs.open("Browser", mw)
                            browser.search_for(f"nid:{self.nid}")
                            self.dialog_parent.hide_preview_popup()

                        def enterEvent(self, event):
                            self.dialog_parent._preview_current_widget = self
                            self.dialog_parent.show_preview_popup(self.nid, position_widget=self)
                            super().enterEvent(event)
                            
                        def leaveEvent(self, event):
                            # On ne cache pas immédiatement pour permettre le survol de la popup
                            # La popup se cachera via les événements de survol du dialogue si besoin
                            from aqt.qt import QTimer
                            QTimer.singleShot(150, lambda: self.dialog_parent.check_hide_preview())
                            super().leaveEvent(event)

                    btn = HoverButton("Voir", str(nid), self)
                    self.results_table.setCellWidget(row, 2, btn)
                    
                    count += 1
                except: continue
            self.results_label.setText(f"{count} résultats")
        except Exception as e:
            self.results_label.setText(str(e))
        
        # Ancrage dynamique au défilement
        if not hasattr(self, '_scroll_connected'):
            self.results_table.verticalScrollBar().valueChanged.connect(self._on_table_scroll)
            self._scroll_connected = True

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
            isolated = re2.sub(r'id=["\'](.*?)["\']', r'id="dlg_preview_\1"', rendered)
            isolated = re2.sub(r'toggle\([\"\'](.*?)[\"\']\)', r"toggle('dlg_preview_\\1')", isolated)
            isolated = re2.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', '', isolated, flags=re2.IGNORECASE)
            isolated = re2.sub(
                r"(?:<br>\s*)?<span[^>]*id=[^>]*FSRS[^>]*>.*?</span>",
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
                dlg.setMinimumSize(450, 100)
                dlg.setMaximumSize(600, 520)
                dlg.setStyleSheet("#PreviewPopup { border: 2px solid #007acc; background: white; border-radius: 8px; }")

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
                pos = position_widget.mapToGlobal(QPoint(position_widget.width() + 10, -50))
                # Ajustement si on sort de l'écran bas
                screen = QApplication.primaryScreen().availableGeometry()
                if pos.y() + 300 > screen.height():
                    pos.setY(screen.height() - 320)
                dlg.move(pos)

            addon_package = mw.addonManager.addonFromModule(__name__)
            css_link = '<link rel="stylesheet" href="/_addons/' + addon_package + '/user_files/clickable_cards.css">'
            scoped_css = """
            <style>
                body { margin: 2px; font-size: 12px; overflow-x: hidden; padding: 0 !important; }
                span[id*='FSRS'] { display: none !important; }
                hr { display: none !important; }
                .card > *:last-child { margin-bottom: 0 !important; padding-bottom: 0 !important; }
                .card { background: transparent !important; background-color: transparent !important; margin: 0 !important; padding: 5px !important; font-size: 14px !important; line-height: 1.3 !important; max-width: 100% !important; text-align: left !important; }
                a { font-size: 0.85em !important; }
                .section { display: flex !important; margin: 1px 0 !important; padding: 0 !important; margin-left: 0 !important; margin-right: 0 !important; width: 100% !important; min-height: 0 !important; border-width: 1.5px !important; }
                div[class*='section'] { display: flex !important; }
                .items { margin-left: 8px !important; margin-right: 8px !important; padding: 1px 0 !important; min-height: 0 !important; }
                .items ul, .items ol { padding-top: 5px !important; padding-bottom: 5px !important; margin-top: 0 !important; margin-bottom: 0 !important; }
                .items li { padding-top: 1px !important; padding-bottom: 1px !important; }
                .bar { flex: 0 0 24px !important; width: 24px !important; min-height: 24px !important; margin: 0 !important; padding: 0 !important; background-size: 18px !important; border-right-width: 1px !important; }
                .barHider { display: none !important; }
                br { line-height: 1px !important; margin: 0 !important; }
                .clickable_cards { font-size: 11px !important; height: 11px !important; line-height: 11px !important; padding: 3px !important; margin: 3px !important; cursor: pointer; }
                .items.cartesLiees { flex-direction: row !important; flex-wrap: wrap !important; align-items: center !important; justify-content: flex-start !important; margin-left: 0 !important; }
            </style>
            """
            
            gui_hover_script = """
            <script>
            document.addEventListener("mouseover", function(e) {
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    var nid = e.target.innerText.trim();
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_hover:' + nid);
                    e.target.style.outline = '2px solid #007acc';
                }
            });
            document.addEventListener("mouseout", function(e) {
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    var nid = e.target.innerText.trim();
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_mouseout:' + nid);
                    e.target.style.outline = '';
                }
            });
            document.addEventListener("click", function(e) {
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    var nid = e.target.innerText.trim();
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_click:' + nid);
                }
            });
            document.addEventListener("keydown", function(e) {
                if(e.key === 'Escape') {
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_mouseout:esc'); // Hack pour cacher via python
                    else if(typeof bridgeCommand !== 'undefined') bridgeCommand('gui_preview_mouseout:esc');
                }
                if(e.key === 'r' || e.key === 'R') {
                    var sel = document.querySelector('.edn-selected-badge');
                    if(!sel) {
                        var badges = Array.from(document.querySelectorAll('.clickable_cards'));
                        for(var i=0; i<badges.length; i++) {
                            if(badges[i].style.outline) { sel = badges[i]; break; }
                        }
                        if(!sel && badges.length > 0) sel = badges[0];
                    }
                    
                    // Si une sélection existe ET qu'elle n'est pas déjà celle survolée/ouverte, on l'ouvre
                    if(sel) {
                        e.preventDefault();
                        var nid = sel.innerText.trim();
                        if(typeof pycmd !== 'undefined') pycmd('gui_preview_hover:' + nid);
                        else if(typeof bridgeCommand !== 'undefined') bridgeCommand('gui_preview_hover:' + nid);
                    } else {
                        // Scroll fallback pour la navigation clavier
                        if (!e.shiftKey) {
                            window.scrollBy(0, 100);
                        } else {
                            window.scrollBy(0, -100);
                        }
                    }
                }
            });
            </script>
            """
            
            # stdHtml ne charge pas MathJax automatiquement — on le passe via js=
            # afin que le LaTeX \(...\) et \[...\] soit rendu correctement.
            web.stdHtml(
                css_link + scoped_css + gui_hover_script + '<div class="card">' + isolated + '</div>',
                js=["/_anki/js/mathjax.js"]
            )

            dlg.show()
            dlg.raise_()
            
            # Ajustement rapide de la hauteur sans attendre MathJax
            dlg.resize(dlg.width(), 100)
            from aqt.qt import QTimer

            def _quick_resize():
                try:
                    web.evalWithCallback(
                        "document.documentElement.scrollHeight;",
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
                            "MathJax.typesetPromise().catch(function(){});"
                        )
                        # Réajuster la hauteur 150ms après le typeset
                        QTimer.singleShot(150, _quick_resize)
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

            # Ajustement initial rapide (~50ms)
            QTimer.singleShot(50, _quick_resize)
            # Lancement du typeset MathJax
            QTimer.singleShot(100, lambda: _try_typeset())
        except Exception as ex:
            from aqt.utils import tooltip
            tooltip("Erreur preview : " + str(ex))

    def _adjust_preview_height(self, dlg, h):
        if h and h > 0:
            new_height = min(520, max(100, h + 20))
            dlg.resize(dlg.width(), new_height)
        else:
            dlg.resize(dlg.width(), 100)

    def eventFilter(self, obj, event):
        if hasattr(self, '_preview_dlg') and obj == self._preview_dlg:
            if event.type() == QEvent.Type.Leave:
                from aqt.qt import QTimer
                QTimer.singleShot(150, lambda: self.check_hide_preview())
        return super().eventFilter(obj, event)

    def check_hide_preview(self):
        if hasattr(self, '_preview_dlg') and self._preview_dlg and self._preview_dlg.isVisible():
            # Si la souris est sur le bouton ou sur le dlg, on ne cache pas
            pos = QCursor.pos()
            if self._preview_dlg.geometry().contains(pos):
                return
            if hasattr(self, '_preview_current_widget') and self._preview_current_widget and self._preview_current_widget.geometry().contains(self._preview_current_widget.parentWidget().mapFromGlobal(pos)):
                return
            self.hide_preview_popup()

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
        
        items = []
        for row in rows:
            data = self.results_table.item(row.row(), 0).data(Qt.ItemDataRole.UserRole)
            items.append((data['nid'], data['recto']))
            
        if items:
            self._is_inserting = True
            self.close()
            editor = self.editor
            # Ne pas appeler setFocus() ici : ça réinitialise la sélection et perd le marqueur de curseur
            from aqt.qt import QTimer
            QTimer.singleShot(0, lambda: self.inserter.insert_link(items))

def open_search_dialog(editor):
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
        })();
    """)

    try:
        if _active_dialog: _active_dialog.close()
    except: pass
    _active_dialog = LinkedCardsDialog(editor)
    _active_dialog.show()
    _active_dialog.activateWindow()

def open_search_dialog_from_menu():
    from aqt import mw
    editor = None
    
    # Préférer l'éditeur de la fenêtre active
    if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow'):
        win = mw.app.activeWindow()
        if hasattr(win, 'editor'):
            editor = win.editor
            
    # Fallback sécurisé : 
    if not editor and _current_editor:
        # S'assurer qu'il n'est pas détruit/fermé (hasattr web est un bon check)
        try:
            if hasattr(_current_editor, "web") and not _current_editor.web.isHidden():
                editor = _current_editor
        except: pass
        
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
        layout.addStretch()

    def save_config(self):
        config = mw.addonManager.getConfig(__name__) or {}
        config["search_trigger"] = self.edit_trigger.text()
        
        from .edn_menu import set_shortcut
        set_shortcut("linked_cards_search", self.edit_search.text())
        set_shortcut("linked_cards_copy", self.edit_copy.text())

        set_shortcut("linked_cards_kbd_navigate", self.edit_kbd_navigate.text() or "r")
        set_shortcut("linked_cards_kbd_open", self.edit_kbd_open.text() or "n")
        set_shortcut("linked_cards_kbd_preview", self.edit_kbd_preview.text() or "p")
        set_shortcut("linked_cards_kbd_back", self.edit_kbd_back.text() or "z")
        
        hiddens = [box_id for box_id, cb in self.checkboxes.items() if cb.isChecked()]
        config["hidden_preview_sections"] = hiddens
        
        mw.addonManager.writeConfig(__name__, config) 
