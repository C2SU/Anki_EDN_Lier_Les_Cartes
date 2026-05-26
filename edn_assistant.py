import json
import re
from aqt import mw
from aqt.qt import *
from aqt import gui_hooks

_assistant_css = """
body { font-family: -apple-system, system-ui, sans-serif; font-size: 13px; margin: 0; padding: 10px; background: var(--window-bg); color: var(--fg); }
h3 { margin-top: 0; font-size: 14px; border-bottom: 1px solid var(--border); padding-bottom: 5px; }
.section { margin-bottom: 15px; }
.card-item { padding: 6px; border: 1px solid var(--border); border-radius: 4px; margin-bottom: 5px; background: var(--bg); display: flex; flex-direction: column; gap: 4px; }
.card-title { font-weight: 500; font-size: 12px; }
.card-nid { font-size: 10px; color: #888; font-family: monospace; }
.btn-add { background: #007acc; color: white; border: none; border-radius: 3px; padding: 4px 8px; cursor: pointer; align-self: flex-start; font-size: 11px; }
.btn-add:hover { background: #005f9e; }
.empty { font-style: italic; color: #888; font-size: 12px; }
"""

def extract_nids_from_fields(note):
    nids = set()
    for f in note.fields:
        # Match data-nid="1234567890" or <span class="edn-nid">1234567890</span>
        for match in re.finditer(r'data-nid=["\'](\d{10,})["\']', f):
            nids.add(match.group(1))
        for match in re.finditer(r'<span[^>]*class=["\'][^"\']*edn-nid[^"\']*["\'][^>]*>\s*(\d{10,})\s*</span>', f):
            nids.add(match.group(1))
        for match in re.finditer(r'cards_ct_click\([\'"](\d{10,})[\'"]\)', f):
            nids.add(match.group(1))
    return nids

def get_note_recto(note):
    try:
        html = note.fields[0]
        clean = re.sub(r'<[^>]+>', ' ', html).replace('&nbsp;', ' ')
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean[:100] or "[Vide]"
    except:
        return "[Erreur]"

def update_assistant(editor):
    if getattr(editor, '_edn_assistant_hidden', False):
        return
        
    assistant = getattr(editor, '_edn_assistant_web', None)
    if not assistant:
        return
        
    note = editor.note
    if not note or not note.id:
        assistant.setHtml(f"<style>{_assistant_css}</style><body><div class='empty'>Veuillez sauvegarder la note d'abord.</div></body>")
        return

    current_nid = str(note.id)
    
    # 1. Backlinks: Cards pointing to this note
    # We search the db for the NID string
    query = f'"{current_nid}"'
    backlink_nids = mw.col.find_notes(query)
    
    backlinks = []
    for bnid in backlink_nids:
        if str(bnid) == current_nid: continue
        try:
            bnote = mw.col.get_note(bnid)
            # Verify it really links to us (not just a coincidence)
            bnote_nids = extract_nids_from_fields(bnote)
            if current_nid in bnote_nids:
                backlinks.append({"nid": str(bnid), "recto": get_note_recto(bnote)})
        except:
            pass

    # 2. Outgoing links: Cards this note points to
    outgoing_nids = extract_nids_from_fields(note)
    
    outgoing = []
    for onid in outgoing_nids:
        if onid == current_nid: continue
        try:
            onote = mw.col.get_note(int(onid))
            outgoing.append({"nid": onid, "recto": get_note_recto(onote)})
        except:
            pass

    # Compare
    outgoing_nids_set = set(n["nid"] for n in outgoing)
    backlink_nids_set = set(n["nid"] for n in backlinks)
    
    missing_backlinks = [n for n in backlinks if n["nid"] not in outgoing_nids_set]
    mirrors = [n for n in backlinks if n["nid"] in outgoing_nids_set]
    missing_outgoing = [n for n in outgoing if n["nid"] not in backlink_nids_set]
    
    html = f"<style>{_assistant_css}</style><body>"
    
    # Section 1: Missing mirrors (Backlinks pointing here, but we don't point back)
    html += "<h3>Liens Entrants (Miroirs manquants)</h3><div class='section'>"
    if not missing_backlinks:
        html += "<div class='empty'>Aucun.</div>"
    else:
        for n in missing_backlinks:
            recto_esc = n['recto'].replace("'", "&#39;").replace('"', '&quot;')
            html += f"""
            <div class='card-item'>
                <div class='card-title'>{recto_esc}</div>
                <div class='card-nid'>{n['nid']}</div>
                <button class='btn-add' data-action='add_mirror' data-nid='{n['nid']}' data-recto='{recto_esc}'>Ajouter ce lien</button>
            </div>
            """
    html += "</div>"
    
    # Section 2: Complete mirrors
    html += "<h3>Liens Réciproques</h3><div class='section'>"
    if not mirrors:
        html += "<div class='empty'>Aucun.</div>"
    else:
        for n in mirrors:
            recto_esc = n['recto'].replace("'", "&#39;").replace('"', '&quot;')
            html += f"<div class='card-item'><div class='card-title'>{recto_esc}</div><div class='card-nid'>{n['nid']}</div></div>"
    html += "</div>"
    
    # Section 3: Outgoing without backlink
    html += "<h3>Liens Sortants (sans retour)</h3><div class='section'>"
    if not missing_outgoing:
        html += "<div class='empty'>Aucun.</div>"
    else:
        for n in missing_outgoing:
            recto_esc = n['recto'].replace("'", "&#39;").replace('"', '&quot;')
            html += f"""
            <div class='card-item'>
                <div class='card-title'>{recto_esc}</div>
                <div class='card-nid'>{n['nid']}</div>
                <button class='btn-add' data-action='make_reciprocal' data-nid='{n['nid']}'>Rendre réciproque</button>
            </div>
            """
    html += "</div>"
    
    html += """
    <script>
    document.addEventListener("click", function(e) {
        var btn = e.target.closest ? e.target.closest('.btn-add') : null;
        if (!btn) return;
        var action = btn.getAttribute('data-action');
        var nid = btn.getAttribute('data-nid');
        var recto = btn.getAttribute('data-recto') || '';
        if (action === 'add_mirror') {
            pycmd('add_mirror:' + nid + '|||' + recto);
        } else if (action === 'make_reciprocal') {
            pycmd('make_reciprocal:' + nid);
        }
    });
    function pycmd(cmd) {
        window.location.href = 'pycmd:' + cmd;
    }
    </script>
    </body>
    """
    
    assistant.setHtml(html)

def handle_assistant_message(handled, message, context):
    if message.startswith("add_mirror:"):
        try:
            parts = message.split("add_mirror:")[1].split("|||")
            nid = parts[0]
            recto = parts[1] if len(parts) > 1 else ""
            
            from .linked_cards import _current_editor
            if not _current_editor or not _current_editor.note:
                return (True, None)
                
            editor = _current_editor
            
            recto_escaped = recto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", "&#39;")
            badge_html = f'{recto_escaped} — <kbd class="clickable_cards" tabindex="0" data-nid="{nid}" onclick="cards_ct_click(\'{nid}\')" ondblclick="cards_ct_click(\'{nid}\')"><span class="edn-nid">{nid}</span></kbd>&nbsp;'
            
            flds = editor.note.note_type()['flds']
            target_fidx = next((i for i, fld in enumerate(flds) if fld['name'].lower() in ["cartes liées", "cartes liees", "liens", "linked cards"]), len(flds) - 1)
                
            def on_saved():
                editor.note.fields[target_fidx] += "<br>" + badge_html
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
            from .linked_cards import _current_editor
            if not _current_editor or not _current_editor.note:
                return (True, None)
                
            editor = _current_editor
            current_note = editor.note
            current_nid = str(current_note.id)
            current_recto = get_note_recto(current_note)
            
            target_note = mw.col.get_note(int(target_nid))
            
            recto_escaped = current_recto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", "&#39;")
            badge_html = f'{recto_escaped} — <kbd class="clickable_cards" tabindex="0" data-nid="{current_nid}" onclick="cards_ct_click(\'{current_nid}\')" ondblclick="cards_ct_click(\'{current_nid}\')"><span class="edn-nid">{current_nid}</span></kbd>&nbsp;'
            
            flds = target_note.note_type()['flds']
            target_fidx = next((i for i, fld in enumerate(flds) if fld['name'].lower() in ["cartes liées", "cartes liees", "liens", "linked cards"]), len(flds) - 1)
            
            target_note.fields[target_fidx] += "<br>" + badge_html
            mw.col.update_note(target_note)
            
            # The current note in editor hasn't changed its text, but its reciprocal status did
            update_assistant(editor)
            
        except Exception as e:
            print("Erreur make_reciprocal:", e)
        return (True, None)

    return handled

class EdnAssistantWeb(QWebEngineView):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor

class EdnAssistantPage(QWebEnginePage):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        
    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if url.scheme() == "pycmd":
            message = url.path()
            # on simule un bridgeCommand
            handle_assistant_message(False, message, self)
            return False
        return super().acceptNavigationRequest(url, _type, isMainFrame)

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
        
        assistant = QWebEngineView()
        # Custom page pour intercepter les URLs pycmd si bridgeCommand n'est pas dispo
        page = EdnAssistantPage(editor, assistant)
        assistant.setPage(page)
        
        assistant.setMinimumWidth(200)
        assistant.setMaximumWidth(400)
        assistant.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        
        splitter.addWidget(assistant)
        splitter.setSizes([800, 250])
        
        editor._edn_assistant_web = assistant
        
    except Exception as e:
        print("Failed to setup editor assistant:", e)

def on_editor_did_init(editor):
    # Retardons légèrement pour que le layout soit construit
    QTimer.singleShot(100, lambda: _setup_assistant_ui(editor))

def on_editor_did_load_note(editor):
    QTimer.singleShot(200, lambda: update_assistant(editor))

def init_assistant():
    gui_hooks.editor_did_init.append(on_editor_did_init)
    gui_hooks.editor_did_load_note.append(on_editor_did_load_note)
    gui_hooks.webview_did_receive_js_message.append(handle_assistant_message)
