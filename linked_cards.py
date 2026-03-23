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
    # Editor hooks
    editor_did_init.append(on_editor_init)
    editor_did_init_buttons.append(on_editor_buttons)
    webview_did_receive_js_message.append(on_js_message)
    
    # Reviewer/Preview hooks
    gui_hooks.webview_did_receive_js_message.append(on_js_message_reviewer)
    hooks.card_did_render.append(on_card_render)
    gui_hooks.webview_will_set_content.append(add_css)
    
    # Browser hooks (Menu & Context)
    gui_hooks.browser_menus_did_init.append(setup_browser_menu)
    gui_hooks.browser_will_show_context_menu.append(add_to_browser_context_menu)
    # Menu & Options Hooks
    try:
        from .shared_menu import register_interface
        register_interface("linked_cards", LinkedCardsConfigWidget)
    except Exception as e:
        log(f"Linked Cards : pas de prise en charge widget interface avancée ({e})")
        
    # Declare shortcuts to EDN shared menu registry so they appear in Shortcuts Configuration
    if hasattr(mw, '_edn_registered_modules') and "linked_cards" in mw._edn_registered_modules:
        mw._edn_registered_modules["linked_cards"]["actions"] = [
            {"label": "🔗 Recherche Liens (Éditeur)", "shortcut": "Ctrl+Alt+L", "action": None},
            {"label": "📋 Copier NID (Explorateur)", "shortcut": "Ctrl+Alt+C", "action": None}
        ]
        
    mw.addonManager.setWebExports(__name__, r'.+\.css')
    log("Linked Cards module initialized (Full Restoration).")


def add_css(web_content: aqt.webview.WebContent, context: Optional[Any]) -> None:
    addon_package = mw.addonManager.addonFromModule(__name__)
    base_url_css = f'/_addons/{addon_package}/user_files/clickable_cards.css'
    web_content.css.append(base_url_css)

add_to_card_script = """
<script type="text/javascript">
// -- Nettoyage au changement de carte --
(function() {
    var _oldBox = document.getElementById("edn-preview-box");
    if (_oldBox) { _oldBox.style.display = "none"; }
    if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
    if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
    window._edn_hover_target = null;
    window._ednPositionLocked = false;
})();
function cards_ct_click(nid) {
    if(typeof pycmd !== 'undefined') pycmd("cards_ct_click" + nid);
}

// -- Aperçu des cartes liées au survol -- (guard : enregistrement unique des listeners) --
if (!window._ednListenersAttached) {
    window._ednListenersAttached = true;

    window._ednHoverTimer = null;
    window._ednHideTimer = null;
    window._ednPositionLocked = false;

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
                    window._ednPositionLocked = false;
                    window._ednHideTimer = null;
                }, 80);
            });
        }
        return box;
    };

    document.addEventListener("mouseover", function(e) {
        if(e.target && e.target.classList.contains("clickable_cards")) {
            if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
            if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
            var target = e.target;
            var nid = target.innerText.trim();
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
            var topPos = rect.bottom;
            var leftPos = rect.left;
            if (leftPos + 400 > window.innerWidth) { leftPos = window.innerWidth - 420; }
            if (leftPos < 0) leftPos = 10;
            box.style.top = topPos + "px";
            box.style.left = leftPos + "px";
        }
    }, true);

    window.show_edn_preview = function(html) {
        var box = window._ednGetOrCreateBox();
        var targetInsideBox = window._edn_hover_target && box.contains(window._edn_hover_target);
        box.innerHTML = html;
        box.style.display = "block";
        box.style.overflowY = "auto";
        if (!targetInsideBox || !window._ednPositionLocked) {
            if(window._edn_hover_target) {
                var rect = window._edn_hover_target.getBoundingClientRect();
                var topPos = rect.bottom; // 0 gap to facilitate hover
                var leftPos = rect.left;
                if (leftPos + 400 > window.innerWidth) { leftPos = window.innerWidth - 420; }
                if (leftPos < 0) leftPos = 10;
                box.style.top = topPos + "px";
                box.style.left = leftPos + "px";
                window._ednPositionLocked = true;
            }
        }
        // Si targetInsideBox && _ednPositionLocked : position conservee
    };

} // fin guard _ednListenersAttached
</script>
"""

def on_card_render(output, context):
    output.question_text += add_to_card_script
    output.answer_text += add_to_card_script

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
                
                rendered_a = card.a()
                
                isolated = re.sub(r'id=["\'](.*?)["\']', r'id="edn_preview_\1"', rendered_a)
                isolated = re.sub(r"toggle\(['\"](.*?)['\"]\)", r"toggle('edn_preview_\1')", isolated)
                isolated = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', isolated, flags=re.IGNORECASE)
                isolated = re.sub(r'<style\b[^>]*>.*?</style>', '', isolated, flags=re.IGNORECASE | re.DOTALL)
                
                # Exclure le bloc de statut FSRS qui ajoute énormément d'espace en bas
                isolated = re.sub(r'(?:<br>\s*)?<span[^>]*id=["\'][^"\']*FSRS[^"\']*["\'][^>]*>.*?</span>', '', isolated, flags=re.IGNORECASE | re.DOTALL)
                
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
                    #edn-preview-box .edn-preview-isolated { overflow: hidden; }
                    #edn-preview-box .section { display: flex !important; }
                    #edn-preview-box .barHider { display: none !important; }
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
    """Restore the 'Linking' menu and Copy NID action."""
    shortcut_copy = get_shortcut("linked_cards", "Ctrl+Alt+C")
    
    action_copy = QAction("Copier NID", browser)
    action_copy.setShortcut(shortcut_copy)
    action_copy.triggered.connect(lambda: copy_nid_from_browser(browser))
    
    # Save reference for context menu
    browser.edn_copy_nid_action = action_copy
    
    # 2. Top Level Menu Restoration
    try:
        pass
        # # Check if menu already exists to avoid duplicates
        # if hasattr(browser, 'menuLinking'):
        #     return
        #     
        # menu_linking = QMenu("&Linking", browser)
        # # Insert before "Help" or at end
        # browser.menuBar().insertMenu(browser.mw.form.menuHelp.menuAction(), menu_linking)
        # browser.menuLinking = menu_linking
        # 
        # menu_linking.addAction(action_copy)
        
        # Add action directly to browser window to enable shortcut
        browser.addAction(action_copy)
    except Exception as e:
        log(f"Error setting up browser menu: {e}")

def copy_nid_from_browser(browser: Browser):
    cards = browser.selected_cards()
    if not cards:
        return
    nid = browser.col.get_card(cards[0]).nid
    QApplication.clipboard().setText(str(nid))
    tooltip(f"NID copié: {nid}")

def add_to_browser_context_menu(browser: Browser, menu: QMenu):
    if hasattr(browser, 'edn_copy_nid_action'):
        menu.addAction(browser.edn_copy_nid_action)

# --- Editor Integration ---

def on_editor_buttons(buttons, editor):
    register_module("linked_cards", "Linked Cards", "Lier des cartes entre elles")
    
    shortcut_legacy = get_shortcut("linked_cards", "Ctrl+Alt+L")
    
    btn_link = editor.addButton(
        icon=None,
        cmd="edn_linked_search",
        func=lambda e=editor: handle_editor_button(e),
        tip=f"Lier carte / Rechercher ({shortcut_legacy})",
        label="Lier",
        keys=shortcut_legacy
    )
    buttons.append(btn_link)
    
    return buttons

def handle_editor_button(editor):
    """Smart Link: Direct link if selection is NID, else Search GUI."""
    editor.web.evalWithCallback("""
        (function() {
            window._ednSavedRange = null;
            let active = document.activeElement;
            let sel = null;
            if (active && active.shadowRoot) sel = active.shadowRoot.getSelection();
            else sel = window.getSelection();
            
            if(sel && sel.rangeCount > 0) {
                window._ednSavedRange = sel.getRangeAt(0).cloneRange();
            }
            return sel ? sel.toString() : '';
        })();
    """, lambda res: _on_selection_check(editor, res))

def _on_selection_check(editor, result):
    text = result.strip() if result else ""
    matches = re.findall(r"\d+", text)
    
    if matches:
        nid = matches[0]
        if len(nid) > 9: # timestamp check roughly
            create_link_for_nid(editor, nid)
            return
            
    open_search_dialog(editor)

def create_link_for_nid(editor, nid):
    try:
        note = mw.col.get_note(int(nid))
        LinkInserter(editor).insert_link([(nid, None)])
    except:
        tooltip(f"Note {nid} non trouvée.")
        open_search_dialog(editor)

def on_editor_init(editor: Editor):
    global _current_editor
    _current_editor = editor
    
    config = mw.addonManager.getConfig(__name__)
    trigger = config.get("search_trigger", "nid:") if config else "nid:"
    trigger_len = len(trigger)
    
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
                html_parts.append(f'<kbd class="clickable_cards" onclick="cards_ct_click(\'{nid}\')" ondblclick="cards_ct_click(\'{nid}\')">{nid}</kbd>&nbsp;')
            else:
                recto_escaped = recto.replace('"', '&quot;').replace("'", "\\'")
                html_parts.append(f'{recto_escaped}&nbsp;—&nbsp;<kbd class="clickable_cards" onclick="cards_ct_click(\'{nid}\')" ondblclick="cards_ct_click(\'{nid}\')">{nid}</kbd>&nbsp;')
                
        html = "<br>".join(html_parts)
        if html:
            html += "&nbsp;"
        
        js = f"""
        (function() {{
            let activeEditable = null;

            if (window._ednSavedRange) {{
                let node = window._ednSavedRange.commonAncestorContainer;
                while(node) {{
                    if (node.tagName === 'ANKI-EDITABLE' || (node.classList && node.classList.contains('field'))) {{
                        activeEditable = node;
                        break;
                    }}
                    node = node.parentNode;
                }}
            }}

            if (!activeEditable) {{
                let active = document.activeElement;
                if (active && active.shadowRoot) {{
                    let insideList = active.shadowRoot.querySelectorAll('anki-editable, .field');
                    for (let el of insideList) {{
                        if (el.isContentEditable) {{
                            activeEditable = el;
                            break;
                        }}
                    }}
                }} else if (active && active.isContentEditable) {{
                    activeEditable = active;
                }} else if (active && active.tagName === 'ANKI-EDITABLE') {{
                    activeEditable = active;
                }}
            }}

            if (activeEditable && typeof activeEditable.focus === 'function') {{
                activeEditable.focus();
            }}

            let sel = activeEditable && activeEditable.shadowRoot ? activeEditable.shadowRoot.getSelection() : window.getSelection();

            if (window._ednSavedRange && sel) {{
                try {{
                    sel.removeAllRanges();
                    sel.addRange(window._ednSavedRange);
                }} catch(e) {{}}
            }} else if (activeEditable && sel) {{
                try {{
                    let range = document.createRange();
                    range.selectNodeContents(activeEditable);
                    range.collapse(false);
                    sel.removeAllRanges();
                    sel.addRange(range);
                }} catch(e) {{}}
            }}

            let htmlToInsert = `{html}`;
            try {{
                document.execCommand("insertHTML", false, htmlToInsert);
            }} catch(e) {{}}

            if (activeEditable) {{
                activeEditable.dispatchEvent(new Event("input", {{ bubbles: true, composed: true }}));
            }}
            window._ednSavedRange = null; // reset
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
                    recto_clean = strip_html(recto)[:80] or "[Vide]"
                    
                    row = self.results_table.rowCount()
                    self.results_table.insertRow(row)
                    
                    i_recto = QTableWidgetItem(recto_clean)
                    i_recto.setData(Qt.ItemDataRole.UserRole, {'nid': str(nid), 'recto': recto_clean})
                    self.results_table.setItem(row, 0, i_recto)
                    
                    self.results_table.setItem(row, 1, QTableWidgetItem(str(nid)))
                    
                    btn = QPushButton("Voir")
                    btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; border-radius: 4px;")
                    btn.clicked.connect(lambda _, n=nid: self.open_in_browser(n))
                    self.results_table.setCellWidget(row, 2, btn)
                    
                    count += 1
                except: continue
            self.results_label.setText(f"{count} résultats")
        except Exception as e:
            self.results_label.setText(str(e))

    def open_in_browser(self, nid):
        b = dialogs.open("Browser", mw)
        b.search_for(f"nid:{nid}")

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
            self.close()
            if hasattr(self, 'editor') and hasattr(self.editor, 'web') and self.editor.web:
                self.editor.web.setFocus()
            
            from aqt.qt import QTimer
            QTimer.singleShot(50, lambda: self.inserter.insert_link(items))

def open_search_dialog(editor):
    global _active_dialog
    if not editor: return
    
    editor.web.eval("""
        (function() {
            if (!window._ednSavedRange) {
                let active = document.activeElement;
                let sel = null;
                if (active && active.shadowRoot) sel = active.shadowRoot.getSelection();
                else sel = window.getSelection();
                
                if(sel && sel.rangeCount > 0) {
                    window._ednSavedRange = sel.getRangeAt(0).cloneRange();
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
    if _current_editor:
        editor = _current_editor
    elif hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow'):
        win = mw.app.activeWindow()
        if hasattr(win, 'editor'):
            editor = win.editor
    
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
        
        self.edit_search = QLineEdit(get_shortcut("linked_cards", "Ctrl+Alt+L"))
        g_layout.addRow("Appeler Fenêtre Liste Liens (Éditeur):", self.edit_search)
        
        self.edit_copy = QLineEdit(get_shortcut("linked_cards", "Ctrl+Alt+C"))
        g_layout.addRow("Copier directement le NID (Explorateur):", self.edit_copy)
        
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
        set_shortcut("linked_cards", self.edit_search.text())
        set_shortcut("linked_cards", self.edit_copy.text())
        
        hiddens = [box_id for box_id, cb in self.checkboxes.items() if cb.isChecked()]
        config["hidden_preview_sections"] = hiddens
        
        mw.addonManager.writeConfig(__name__, config)
