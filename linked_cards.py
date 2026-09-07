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

import importlib
try:
    _better_search_on_text_change = importlib.import_module("1052724801.new.onTextChange")
    better_search_on_text_change = _better_search_on_text_change.onSearchEditTextChange
except Exception:
    better_search_on_text_change = None

# Global reference
_current_editor = None
_active_dialog = None

def _is_active_dialog_alive():
    global _active_dialog
    if _active_dialog is None:
        return False
    try:
        _active_dialog.objectName()
        return True
    except RuntimeError:
        _active_dialog = None
        return False

_ct_click_guard: set = set()  # Déduplication: évite double ouverture browser
_last_right_clicked_nid = None
_last_right_clicked_default_text = ""
_right_clicked_on_preview_page = False
_last_gui_close_time = 0.0

def _is_night_mode() -> bool:
    """Helper pour détecter le Dark Mode d'Anki de façon compatible et fiable."""
    try:
        from aqt.theme import theme_manager
        return bool(theme_manager.night_mode)
    except Exception:
        try:
            return bool(mw.theme_manager.night_mode)
        except Exception:
            try:
                from aqt import theme_manager
                return bool(theme_manager.night_mode)
            except Exception:
                try:
                    return bool(mw.pm.night_mode())
                except Exception:
                    return False

def _toggle_assistant_from_menu():
    """Bascule le panneau miroir pour l'éditeur courant (depuis le menu EDN)."""
    try:
        from .editor_assistant import get_active_editor, toggle_assistant
        editor = get_active_editor()
        if editor:
            toggle_assistant(editor)
    except Exception as e:
        log(f"toggle_assistant error: {e}")

class MirrorTargetRowWidget(QWidget):
    def __init__(self, parent, nid_str, target_note, fidx, source_note=None):
        super().__init__(parent)
        self.nid_str = nid_str
        self.target_note = target_note
        self.fidx = fidx
        self.source_note = source_note
        
        is_night = _is_night_mode()
        
        # Outer vertical layout: ligne 1 = carte cible, ligne 2 = texte du lien retour
        outer = QVBoxLayout(self)
        outer.setContentsMargins(5, 4, 5, 4)
        outer.setSpacing(4)
        
        # ── Ligne 1 : checkbox + label carte cible + bouton champ ──
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        
        self.cb = QCheckBox(self)
        self.cb.setChecked(True)
        self.cb.setToolTip(f"ID Note: {nid_str}")
        row1.addWidget(self.cb)
        
        target_recto = get_note_recto(target_note, 80)
        self.target_label = QLabel(target_recto, self)
        if is_night:
            self.target_label.setStyleSheet("font-weight: bold; color: #f3f4f6;")
        else:
            self.target_label.setStyleSheet("font-weight: bold; color: #1e293b;")
        self.target_label.setToolTip(f"Carte cible (ID Note: {nid_str})")
        self.target_label.setWordWrap(False)
        row1.addWidget(self.target_label, stretch=1)
        
        field_name = target_note.note_type()['flds'][fidx]['name']
        self.field_btn = QPushButton(f"[{field_name}]", self)
        self.field_btn.setFlat(True)
        if is_night:
            self.field_btn.setStyleSheet(
                "QPushButton { color: #38bdf8; text-decoration: underline; border: none; background: transparent; text-align: left; padding: 2px; font-style: italic; }"
                "QPushButton:hover { color: #7dd3fc; }"
            )
        else:
            self.field_btn.setStyleSheet(
                "QPushButton { color: #0284c7; text-decoration: underline; border: none; background: transparent; text-align: left; padding: 2px; font-style: italic; }"
                "QPushButton:hover { color: #0369a1; }"
            )
        self.field_btn.setToolTip("Cliquer pour choisir un autre champ de la carte cible")
        self.field_btn.clicked.connect(self.choose_field)
        row1.addWidget(self.field_btn)
        
        outer.addLayout(row1)
        
        # ── Ligne 2 : label + champ de texte du lien retour ──
        row2 = QHBoxLayout()
        row2.setContentsMargins(22, 0, 0, 0)  # indent aligné avec le label de la ligne 1
        row2.setSpacing(6)
        
        lbl = QLabel("Texte du lien :", self)
        if is_night:
            lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
        else:
            lbl.setStyleSheet("color: #64748b; font-size: 11px;")
        row2.addWidget(lbl)
        
        source_recto = generate_smart_link_label(target_note, source_note) if source_note else ""
        self.desc_edit = QLineEdit(source_recto, self)
        if is_night:
            self.desc_edit.setStyleSheet(
                "QLineEdit { background: #1e1e2e; color: #f3f4f6; border: 1px solid #4b5563; border-radius: 3px; padding: 3px 6px; }"
                "QLineEdit:focus { border: 1px solid #38bdf8; background: #252836; color: #ffffff; }"
            )
        else:
            self.desc_edit.setStyleSheet(
                "QLineEdit { background: #ffffff; color: #1e293b; border: 1px solid #cbd5e1; border-radius: 3px; padding: 3px 6px; }"
                "QLineEdit:focus { border: 1px solid #0284c7; background: #f0f6ff; color: #0f172a; }"
            )
        self.desc_edit.setToolTip("Texte affiché devant le lien retour dans la carte cible")
        self.desc_edit.setPlaceholderText("Ex : Étiologie, Traitement…")
        row2.addWidget(self.desc_edit, stretch=1)
        
        outer.addLayout(row2)
        
    def choose_field(self):
        menu = QMenu(self)
        fields = self.target_note.note_type()['flds']
        current_name = fields[self.fidx]['name']
        
        actions = []
        for i, fld in enumerate(fields):
            name = fld['name']
            action = menu.addAction(name)
            if name == current_name:
                action.setCheckable(True)
                action.setChecked(True)
            actions.append((action, i, name))
            
        selected_action = menu.exec(QCursor.pos())
        if selected_action:
            for action, idx, name in actions:
                if action == selected_action:
                    self.fidx = idx
                    self.field_btn.setText(f"[{name}]")
                    break

class MirrorSelectionDialog(QDialog):
    def __init__(self, parent, targets, source_note=None):
        super().__init__(parent)
        self.source_note = source_note
        self.setWindowTitle("Liens miroirs")
        self.setMinimumWidth(580)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Info label
        self.info_label = QLabel(
            "Voulez-vous créer automatiquement les liens retours (miroirs) ?"
        )
        self.info_label.setWordWrap(True)
        font = self.info_label.font()
        font.setPointSize(font.pointSize() + 1)
        self.info_label.setFont(font)
        layout.addWidget(self.info_label)
        
        # Header label
        self.header_label = QLabel("Cartes cibles :")
        self.header_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.header_label)
        
        # Scroll Area for checkboxes
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        
        # Safe resolution of QFrame.Shape.StyledPanel for PyQt5/PyQt6 compatibility
        shape_styled = getattr(QFrame.Shape, "StyledPanel", None) or getattr(QFrame, "StyledPanel", None)
        if shape_styled is not None:
            self.scroll.setFrameShape(shape_styled)
            
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(8)
        
        align_top = getattr(Qt.AlignmentFlag, "AlignTop", None) or getattr(Qt, "AlignTop", None)
        if align_top:
            self.scroll_layout.setAlignment(align_top)
            
        self.checkbox_rows = []
        for nid_str, target_note, fidx in targets:
            row_widget = MirrorTargetRowWidget(self.scroll_content, nid_str, target_note, fidx, source_note=self.source_note)
            self.scroll_layout.addWidget(row_widget)
            self.checkbox_rows.append(row_widget)
            
        self.scroll.setWidget(self.scroll_content)
        
        # Set dynamic height for the scroll area based on item count to avoid empty spaces
        row_height = 68  # 2 lignes par row: ~28px carte + ~28px texte + marges
        content_height = len(targets) * row_height + 20
        scroll_height = min(350, max(80, content_height))
        self.scroll.setFixedHeight(scroll_height)
        
        layout.addWidget(self.scroll)
        
        # Helper layout for checking/unchecking all
        self.helper_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("Tout cocher")
        self.btn_deselect_all = QPushButton("Tout décocher")
        self.btn_select_all.clicked.connect(self.select_all)
        self.btn_deselect_all.clicked.connect(self.deselect_all)
        self.helper_layout.addWidget(self.btn_select_all)
        self.helper_layout.addWidget(self.btn_deselect_all)
        self.helper_layout.addStretch()
        layout.addLayout(self.helper_layout)
        
        # Spacer
        layout.addSpacing(5)
        
        # Standard Buttons (Yes / No)
        std_yes = getattr(QDialogButtonBox.StandardButton, "Yes", None) or getattr(QDialogButtonBox, "Yes", None)
        std_no = getattr(QDialogButtonBox.StandardButton, "No", None) or getattr(QDialogButtonBox, "No", None)
        
        self.button_box = QDialogButtonBox(std_yes | std_no, self)
        yes_btn = self.button_box.button(std_yes)
        no_btn = self.button_box.button(std_no)
        
        if yes_btn:
            yes_btn.setText("Oui")
            yes_btn.setDefault(True)
            yes_btn.setAutoDefault(True)
            yes_btn.clicked.connect(self.accept)
        if no_btn:
            no_btn.setText("Non")
            no_btn.clicked.connect(self.reject)
            
        layout.addWidget(self.button_box)
        
        # Adjust dialog size dynamically to fit contents
        self.adjustSize()
        
    def select_all(self):
        for row_widget in self.checkbox_rows:
            row_widget.cb.setChecked(True)
            
    def deselect_all(self):
        for row_widget in self.checkbox_rows:
            row_widget.cb.setChecked(False)
            
    def get_selected_targets(self):
        return [
            (row.nid_str, row.target_note, row.fidx, row.desc_edit.text())
            for row in self.checkbox_rows if row.cb.isChecked()
        ]

class InterLinkSelectionDialog(QDialog):
    def __init__(self, parent, pairs):
        super().__init__(parent)
        self.setWindowTitle("Liens croisés")
        self.setMinimumWidth(450)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Info label
        self.info_label = QLabel(
            "Voulez-vous également créer des liens mutuels (croisés) entre ces cartes cibles ?"
        )
        self.info_label.setWordWrap(True)
        font = self.info_label.font()
        font.setPointSize(font.pointSize() + 1)
        self.info_label.setFont(font)
        layout.addWidget(self.info_label)
        
        # Header label
        self.header_label = QLabel("Liens à créer :")
        self.header_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.header_label)
        
        # Scroll Area for checkboxes
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        
        shape_styled = getattr(QFrame.Shape, "StyledPanel", None) or getattr(QFrame, "StyledPanel", None)
        if shape_styled is not None:
            self.scroll.setFrameShape(shape_styled)
            
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(8)
        
        align_top = getattr(Qt.AlignmentFlag, "AlignTop", None) or getattr(Qt, "AlignTop", None)
        if align_top:
            self.scroll_layout.setAlignment(align_top)
            
        self.checkboxes = []
        for target_b, target_c in pairs:
            nid_b, note_b, _ = target_b[:3]
            nid_c, note_c, _ = target_c[:3]
            
            recto_b = get_note_recto(note_b, 40)
            recto_c = get_note_recto(note_c, 40)
            
            cb = QCheckBox(f"{recto_b}  <—>  {recto_c}")
            cb.setChecked(True)
            cb.setToolTip(f"NID {nid_b} <—> NID {nid_c}")
            self.scroll_layout.addWidget(cb)
            self.checkboxes.append((cb, (target_b, target_c)))
            
        self.scroll.setWidget(self.scroll_content)
        
        # Set dynamic height for the scroll area based on item count to avoid empty spaces
        row_height = 32
        content_height = len(pairs) * row_height + 20
        scroll_height = min(250, max(60, content_height))
        self.scroll.setFixedHeight(scroll_height)
        
        layout.addWidget(self.scroll)
        
        # Helper layout for checking/unchecking all
        self.helper_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("Tout cocher")
        self.btn_deselect_all = QPushButton("Tout décocher")
        self.btn_select_all.clicked.connect(self.select_all)
        self.btn_deselect_all.clicked.connect(self.deselect_all)
        self.helper_layout.addWidget(self.btn_select_all)
        self.helper_layout.addWidget(self.btn_deselect_all)
        self.helper_layout.addStretch()
        layout.addLayout(self.helper_layout)
        
        # Spacer
        layout.addSpacing(5)
        
        # Standard Buttons (Yes / No)
        std_yes = getattr(QDialogButtonBox.StandardButton, "Yes", None) or getattr(QDialogButtonBox, "Yes", None)
        std_no = getattr(QDialogButtonBox.StandardButton, "No", None) or getattr(QDialogButtonBox, "No", None)
        
        self.button_box = QDialogButtonBox(std_yes | std_no, self)
        yes_btn = self.button_box.button(std_yes)
        no_btn = self.button_box.button(std_no)
        
        if yes_btn:
            yes_btn.setText("Oui")
            yes_btn.setDefault(True)
            yes_btn.setAutoDefault(True)
            yes_btn.clicked.connect(self.accept)
        if no_btn:
            no_btn.setText("Non")
            no_btn.clicked.connect(self.reject)
            
        layout.addWidget(self.button_box)
        
        # Adjust dialog size dynamically to fit contents
        self.adjustSize()
        
    def select_all(self):
        for cb, _ in self.checkboxes:
            cb.setChecked(True)
            
    def deselect_all(self):
        for cb, _ in self.checkboxes:
            cb.setChecked(False)
            
    def get_selected_pairs(self):
        return [pair for cb, pair in self.checkboxes if cb.isChecked()]

def update_link_description_in_note(note, target_nid_str, new_desc):
    """Updates the description of the link to target_nid_str in the fields of note in database."""
    modified = False
    new_desc_escaped = new_desc.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
    
    # We support standard dashes —, \u2014, –, \u2013, hyphens, and various spaces/nbsp
    space_pattern = r'(?:\s|&nbsp;|&#160;|\xa0)*'
    dash_pattern = r'(?:—|\u2014|–|\u2013|-|&mdash;|&ndash;|&#8212;|&#8211;)'
    
    pattern_span = r'<span\s+[^>]*class="edn-desc"[^>]*>[^<]*</span>' + space_pattern + dash_pattern + space_pattern + r'(<kbd\s+[^>]*data-nid="' + re.escape(target_nid_str) + r'"[^>]*>)'
    pattern_text = r'([^<>\n]*?)' + space_pattern + dash_pattern + space_pattern + r'(<kbd\s+[^>]*data-nid="' + re.escape(target_nid_str) + r'"[^>]*>)'
    
    for i in range(len(note.fields)):
        content = note.fields[i]
        if target_nid_str in content:
            new_content = content
            if re.search(pattern_span, new_content):
                new_content = re.sub(pattern_span, lambda m: f'<span class="edn-desc">{new_desc_escaped}</span>&nbsp;—&nbsp;{m.group(1)}', new_content)
            else:
                new_content = re.sub(pattern_text, lambda m: f'<span class="edn-desc">{new_desc_escaped}</span>&nbsp;—&nbsp;{m.group(2)}', new_content)
            if new_content != content:
                note.fields[i] = new_content
                modified = True
    return modified

def on_add_cards_did_add_note(note: Any) -> None:
    """Triggered after a note has been successfully validated and added from the Add Cards window.
    Detects if the new note contains outgoing links (nids) in its fields and proposes to make them mirrored.
    """
    try:
        if not note or not note.id:
            return

        # 1. Search for target nids in all fields
        target_nids = []
        nid_to_source_field = {}
        for field_idx, field_value in enumerate(note.fields):
            nids = re.findall(r'data-nid="(\d+)"', field_value)
            for nid in nids:
                if nid not in target_nids:
                    target_nids.append(nid)
                    nid_to_source_field[nid] = note.note_type()['flds'][field_idx]['name']

        if not target_nids:
            return

        # 2. Filter target notes and check if they already have the mirror link
        source_nid = str(note.id)
        valid_targets = []
        
        for nid_str in target_nids:
            try:
                target_nid = int(nid_str)
                if target_nid == note.id:
                    continue
                target_note = mw.col.get_note(target_nid)
                # Check if it already has a link to source_nid
                if any(source_nid in field for field in target_note.fields):
                    continue
                
                # Determine target field for this link
                source_field_name = nid_to_source_field[nid_str]
                target_fields = target_note.note_type()['flds']
                fidx = -1
                for i, fld in enumerate(target_fields):
                    if fld['name'] == source_field_name:
                        fidx = i
                        break
                if fidx < 0:
                    for i, fld in enumerate(target_fields):
                        if fld['name'] == 'Cartes liées':
                            fidx = i
                            break
                if fidx < 0:
                    fidx = len(target_note.fields) - 1
                
                valid_targets.append((nid_str, target_note, fidx))
            except Exception:
                pass

        if not valid_targets:
            return

        # 3. Propose to mirror links
        parent = mw.app.activeWindow() or mw
        
        dialog = MirrorSelectionDialog(parent, valid_targets, source_note=note)
        result = dialog.exec()
        
        accepted_code = getattr(QDialog.DialogCode, "Accepted", None) or getattr(QDialog, "Accepted", 1)
        if result == accepted_code:
            selected_targets = dialog.get_selected_targets()
            if not selected_targets:
                return
                
            # 4. Perform the mirroring
            for nid_str, target_note, fidx, custom_recto in selected_targets:
                update_link_description_in_note(note, nid_str, custom_recto)
                
                # Utiliser le label validé dans la dialog (custom_recto) pour le badge miroir dans la cible
                source_recto_escaped = custom_recto.replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                badge_html = (
                    f'<br><span class="edn-desc">{source_recto_escaped}</span>&nbsp;\u2014&nbsp;'
                    f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{source_nid}"'
                    f'><span class="edn-nid">{source_nid}</span></kbd>'
                )
                current_field_content = target_note.fields[fidx].strip()
                tb = badge_html
                if not current_field_content or current_field_content == '<br>':
                    tb = tb.replace('<br>', '', 1)
                target_note.fields[fidx] += tb
                mw.col.update_note(target_note)
                
            mw.col.update_note(note)
            tooltip(f"Liens miroirs créés dans {len(selected_targets)} cartes.")
            
            # 5. Propose inter-linking between target cards
            if len(selected_targets) >= 2:
                pairs = []
                for i in range(len(selected_targets)):
                    for j in range(i + 1, len(selected_targets)):
                        target_b = selected_targets[i]
                        target_c = selected_targets[j]
                        
                        nid_b, note_b, _, _ = target_b
                        nid_c, note_c, _, _ = target_c
                        
                        # Check if they already have links to each other
                        already_linked = (
                            any(nid_c in field for field in note_b.fields) or
                            any(nid_b in field for field in note_c.fields)
                        )
                        if not already_linked:
                            pairs.append((target_b, target_c))
                            
                if pairs:
                    inter_dialog = InterLinkSelectionDialog(parent, pairs)
                    inter_result = inter_dialog.exec()
                    if inter_result == accepted_code:
                        selected_pairs = inter_dialog.get_selected_pairs()
                        if selected_pairs:
                            for target_b, target_c in selected_pairs:
                                nid_b, note_b, fidx_b = target_b[:3]
                                nid_c, note_c, fidx_c = target_c[:3]
                                
                                smart_desc_b = generate_smart_link_label(note_c, note_b)
                                recto_b = smart_desc_b if smart_desc_b else get_note_recto(note_b, 240)
                                
                                smart_desc_c = generate_smart_link_label(note_b, note_c)
                                recto_c = smart_desc_c if smart_desc_c else get_note_recto(note_c, 240)
                                
                                recto_b_escaped = recto_b.replace('"', '&quot;').replace("'", '&#39;').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                                recto_c_escaped = recto_c.replace('"', '&quot;').replace("'", '&#39;').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                                
                                # Add C's badge to B
                                badge_c = (
                                    f'<br>{recto_c_escaped}&nbsp;\u2014&nbsp;'
                                    f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid_c}"'
                                    f'><span class="edn-nid">{nid_c}</span></kbd>'
                                )
                                current_field_b = note_b.fields[fidx_b].strip()
                                tb_c = badge_c
                                if not current_field_b or current_field_b == '<br>':
                                    tb_c = tb_c.replace('<br>', '', 1)
                                note_b.fields[fidx_b] += tb_c
                                mw.col.update_note(note_b)
                                
                                # Add B's badge to C
                                badge_b = (
                                    f'<br>{recto_b_escaped}&nbsp;\u2014&nbsp;'
                                    f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid_b}"'
                                    f'><span class="edn-nid">{nid_b}</span></kbd>'
                                )
                                current_field_c = note_c.fields[fidx_c].strip()
                                tb_b = badge_b
                                if not current_field_c or current_field_c == '<br>':
                                    tb_b = tb_b.replace('<br>', '', 1)
                                note_c.fields[fidx_c] += tb_b
                                mw.col.update_note(note_c)
                                
                            tooltip(f"Liens croisés créés entre {len(selected_pairs)} paires de cartes.")
            
            # Refresh assistant if open
            try:
                from .editor_assistant import get_active_editor, update_assistant
                editor = get_active_editor()
                if editor:
                    update_assistant(editor)
            except Exception:
                pass
                
    except Exception as e:
        log(f"Error in on_add_cards_did_add_note: {e}")

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
    gui_hooks.webview_will_show_context_menu.append(on_webview_will_show_context_menu)
    gui_hooks.add_cards_did_add_note.append(on_add_cards_did_add_note)
    
    # Browser hooks (Menu & Context)
    gui_hooks.browser_menus_did_init.append(setup_browser_menu)
    gui_hooks.browser_will_show_context_menu.append(add_to_browser_context_menu)
    
    # Menu & Options Hooks
    try:
        from .edn_menu import register_interface
        register_interface("linked_cards", LinkedCardsConfigWidget)
    except Exception as e:
        log(f"Linked Cards : pas de prise en charge widget interface avancée ({e})")
        
    mw.addonManager.setWebExports(__name__, r'.+\.(css|js)')
    
    # Hook reviewer to clean up hover preview on card transitions
    gui_hooks.reviewer_did_show_answer.append(_on_reviewer_show_answer)
    gui_hooks.reviewer_did_show_question.append(_hide_reviewer_hover_preview)
    gui_hooks.reviewer_will_end.append(_hide_reviewer_hover_preview)
    
    if hasattr(gui_hooks, 'previewer_will_show'):
        gui_hooks.previewer_will_show.append(_setup_previewer_shortcuts)
    
    
    # Protection crash changement de thème
    gui_hooks.theme_did_change.append(_on_theme_changed)
    
    # Invalidation dynamique du cache et mise à jour live
    if hasattr(gui_hooks, 'operation_did_execute'):
        gui_hooks.operation_did_execute.append(_on_operation_did_execute)
    if hasattr(gui_hooks, 'editor_did_unfocus_field'):
        gui_hooks.editor_did_unfocus_field.append(_on_editor_unfocus_field)
    if hasattr(gui_hooks, 'editor_did_fire_typing_timer'):
        gui_hooks.editor_did_fire_typing_timer.append(_on_editor_typing_timer)
    if hasattr(gui_hooks, 'editor_did_load_note'):
        gui_hooks.editor_did_load_note.append(_on_editor_load_note)
    
    # Init assistant
    try:
        from .editor_assistant import init_assistant
        init_assistant()
    except Exception as e:
        log(f"Failed to init assistant: {e}")
        
    # Patch Editor.call_after_note_saved (saveNow) to prevent JS ReferenceError
    try:
        _patch_editor_save_now()
    except Exception as e:
        log(f"Failed to patch Editor.saveNow: {e}")
        
    # setup_edn_submenu() déplacé dans Paramètres EDN
        
    log("Linked Cards module initialized (Full Restoration).")

def setup_edn_submenu():
    """Obsolète : Les réglages de liens sont désormais intégrés dans Paramètres EDN."""
    pass


def add_css(web_content: aqt.webview.WebContent, context: Optional[Any]) -> None:
    addon_package = mw.addonManager.addonFromModule(__name__)
    base_url_css = f'/_addons/{addon_package}/user_files/clickable_cards.css'
    web_content.css.append(base_url_css)
    
    # Load mouse listener from external file to prevent Content Security Policy (CSP) inline script violations
    base_url_js = f'/_addons/{addon_package}/user_files/clickable_cards.js'
    web_content.js.append(base_url_js)

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
    if (document.body) {
        if (document.body.classList.contains('nightMode') || document.body.classList.contains('night_mode')) {
            document.body.classList.add('night-mode');
        }
    }
    if (document.documentElement) {
        if (document.documentElement.classList.contains('nightMode') || document.documentElement.classList.contains('night_mode')) {
            document.documentElement.classList.add('night-mode');
        }
    }
    var _oldBox = document.getElementById("edn-preview-box");
    if (_oldBox) { _oldBox.style.display = "none"; }
    if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
    if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }
    window._edn_hover_target = null;
    window._edn_parent_badge = null;
    window._ednPositionLocked = false;
    window._ednPreviewIndex = -1;
    window._ednPreviewHistory = [];
    window._ednContextMenuActive = false;
    window._ednOriginalScrollY = null;
    var spacer = document.getElementById('edn-scroll-spacer');
    if (spacer) spacer.style.height = '0px';
    // Réinitialiser pour permettre la réinstallation du handler 'r' sur chaque carte
    window._ednReviewerRKeyInit = false;

    // -- Fix badges sans onclick (sanitizer Anki supprime onclick/ondblclick) --
    // Ajouter un delegated click handler sur TOUT le document pour les .clickable_cards
    // qui n'ont pas d'onclick (= attribut supprimé par le sanitizer).
    if (!window._ednDelegatedClickInit) {
        window._ednDelegatedClickInit = true;
        document.addEventListener('click', function(e) {
            if (window._ednTouchRecently) return;
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

    // Event listener délégué pour remplacer les attributs onclick bloqués par CSP
    if (!window._ednDelegatedOnclickInit) {
        window._ednDelegatedOnclickInit = true;
        document.addEventListener('click', function(e) {
            var el = e.target;
            while (el && el !== document.body) {
                if (el.getAttribute && el.getAttribute('data-onclick')) {
                    var clickAttr = el.getAttribute('data-onclick').trim();
                    // Match toggle('...') or toggle("...")
                    var toggleMatch = clickAttr.match(/^toggle\(['"](.*?)['"]\)$/);
                    if (toggleMatch) {
                        var arg = toggleMatch[1];
                        var fn = window.toggle || (typeof toggle !== 'undefined' ? toggle : null);
                        if (fn) {
                            try {
                                fn(arg);
                            } catch(err) {
                                console.error("Error executing toggle:", err);
                            }
                        }
                    } else {
                        // Fallback to new Function if it's something else
                        try {
                            new Function(clickAttr).call(el);
                        } catch(err) {
                            console.error("Error executing data-onclick via new Function:", err);
                        }
                    }
                    break;
                }
                el = el.parentNode;
            }
        }, true);
    }

    if (!window._ednDelegatedOndblclickInit) {
        window._ednDelegatedOndblclickInit = true;
        document.addEventListener('dblclick', function(e) {
            var el = e.target;
            while (el && el !== document.body) {
                if (el.getAttribute && el.getAttribute('data-ondblclick')) {
                    var dblAttr = el.getAttribute('data-ondblclick').trim();
                    var clickMatch = dblAttr.match(/^cards_ct_click\(['"](.*?)['"]\)$/);
                    if (clickMatch) {
                        var nid = clickMatch[1];
                        if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
                        else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
                    } else {
                        try {
                            new Function(dblAttr).call(el);
                        } catch(err) {
                            console.error("Error executing data-ondblclick:", err);
                        }
                    }
                    break;
                }
                el = el.parentNode;
            }
        }, true);
    }
})();
function cards_ct_click(nid) {
    if (window._ednTouchRecently) return;
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
    window._ednContextMenuActive = false;

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
        var activeEl = document.activeElement || e.target;
        if (activeEl && (
            activeEl.tagName === 'INPUT' || 
            activeEl.tagName === 'TEXTAREA' || 
            activeEl.isContentEditable ||
            (activeEl.closest && activeEl.closest('[contenteditable="true"]'))
        )) {
            return;
        }
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
                return (!previewBox || !previewBox.contains(c)) && (c.offsetWidth > 0 || c.offsetHeight > 0);
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
                // Défiler pour rendre le nidbox visible
                targetBadge.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
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
                if (window._ednRestoreScroll) window._ednRestoreScroll();
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
                if (window._ednRestoreScroll) window._ednRestoreScroll();
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

    window._ednOriginalScrollY = null;
    window._ednRestoreScroll = function() {
        var spacer = document.getElementById('edn-scroll-spacer');
        if (spacer) spacer.style.height = '0px';
        var originalScrollY = window._ednOriginalScrollY;
        if (originalScrollY !== undefined && originalScrollY !== null) {
            window.scrollTo({ top: originalScrollY, behavior: 'smooth' });
            window._ednOriginalScrollY = null;
        }
    };

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
                if (window._ednContextMenuActive) return;
                window._ednHideTimer = setTimeout(function() {
                    if (window._ednPreviewUpdating) return;
                    box.style.display = "none";
                    window._edn_hover_target = null;
                    window._edn_parent_badge = null;
                    window._ednPositionLocked = false;
                    window._ednHideTimer = null;
                    if (window._ednRestoreScroll) window._ednRestoreScroll();
                }, 80);
            });
            box.addEventListener("contextmenu", function(e) {
                window._ednContextMenuActive = true;
            });
            window.addEventListener("blur", function() {
                if (window._ednContextMenuActive) return;
                box.style.display = "none";
                window._edn_hover_target = null;
                window._edn_parent_badge = null;
                window._ednPositionLocked = false;
                if (window._ednRestoreScroll) window._ednRestoreScroll();
            });
        }
        return box;
    };

    window._ednPositionAndSizeBox = function(anchorRect, doScroll) {
        var box = document.getElementById("edn-preview-box");
        if (!box) return;
        if (!anchorRect || (anchorRect.width === 0 && anchorRect.height === 0)) {
            box.style.display = "none";
            return;
        }
        
        box.style.position = "fixed";
        box.style.overflowY = "auto";
        
        var boxWidth = box.offsetWidth || 400;
        var viewportWidth = window.innerWidth;
        var viewportHeight = window.innerHeight;
        
        // --- Position verticale (aligné sur la box nid) ---
        var spaceBelow = viewportHeight - anchorRect.top - 10;
        var spaceAbove = anchorRect.bottom - 10;
        
        var placeBelow = true;
        if (spaceBelow < 100 && spaceAbove > spaceBelow) {
            placeBelow = false;
        }
        
        if (placeBelow) {
            box.style.top = anchorRect.top + "px";
            box.style.bottom = "auto";
            box.style.maxHeight = Math.max(100, spaceBelow) + "px";
        } else {
            box.style.bottom = (viewportHeight - anchorRect.bottom) + "px";
            box.style.top = "auto";
            box.style.maxHeight = Math.max(100, spaceAbove) + "px";
        }
        
        // --- Position horizontale (collé à droite du box nid) ---
        // Par défaut, on place le coin gauche de la preview sur le coin droit du badge (avec un décalage de 5px)
        var left = anchorRect.right + 5;
        
        // Si le bord droit dépasse l'écran, on place la preview complètement à gauche du badge (avec un décalage de 5px)
        // afin de conserver l'accès au badge/box pour le curseur
        if (left + boxWidth > viewportWidth - 10) {
            var rightOffset = viewportWidth - anchorRect.left + 5;
            // Si cela fait dépasser la preview à gauche de l'écran, on plaque à gauche
            if (anchorRect.left - 5 - boxWidth < 10) {
                box.style.left = "10px";
                box.style.right = "auto";
            } else {
                box.style.left = "auto";
                box.style.right = rightOffset + "px";
            }
        } else {
            box.style.left = left + "px";
            box.style.right = "auto";
        }
    };

    // -- Support écran tactile (Touch / Long Press / Double Tap) & Clic droit pour le Reviewer --
    var _ednLongPressTimer = null;
    var _ednTouchStartX = 0;
    var _ednTouchStartY = 0;
    var _ednLongPressTriggered = false;
    var _ednTouchTargetBadge = null;
    var _ednLastTapNid = null;
    var _ednLastTapTime = 0;
    var _ednSingleTapTimer = null;

    function _ednClearLongPress() {
        if (_ednLongPressTimer) {
            clearTimeout(_ednLongPressTimer);
            _ednLongPressTimer = null;
        }
    }

    function _ednFindTargetForContextMenu(e) {
        var t = _findCardBadge(e);
        if (t) {
            var nid = _ednGetNid(t);
            if (nid) return { nid: nid, element: t, defText: '' };
        }
        var box = document.getElementById('edn-preview-box');
        if (box && (box === e.target || box.contains(e.target))) {
            var iso = box.querySelector('.edn-preview-isolated');
            var pNid = iso ? (iso.dataset.nid || _ednGetNid(iso)) : null;
            if (!pNid && window._edn_hover_target) {
                pNid = _ednGetNid(window._edn_hover_target);
            }
            if (pNid) return { nid: pNid, element: box, defText: '' };
        }
        return null;
    }

    function _ednSendContextMenu(nid, x, y, defText, source) {
        window._ednContextMenuActive = true;
        var msg = 'edn_show_context_menu:' + nid + '|||' + Math.round(x) + '|||' + Math.round(y) + '|||' + (defText || '') + '|||' + (source || 'reviewer');
        if (typeof pycmd !== 'undefined') pycmd(msg);
        else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
    }

    // Touch events : Long Press (450ms), Single Tap (Preview), Double Tap (Explorateur)
    document.addEventListener('touchstart', function(e) {
        window._ednTouchRecently = true;
        if (!e.touches || e.touches.length !== 1) {
            _ednClearLongPress();
            _ednTouchTargetBadge = null;
            return;
        }
        var touch = e.touches[0];
        _ednTouchStartX = touch.clientX;
        _ednTouchStartY = touch.clientY;
        _ednLongPressTriggered = false;
        _ednTouchTargetBadge = _findCardBadge(e);
        
        var targetInfo = _ednFindTargetForContextMenu(e);
        if (targetInfo && targetInfo.nid) {
            _ednClearLongPress();
            _ednLongPressTimer = setTimeout(function() {
                _ednLongPressTriggered = true;
                if (_ednSingleTapTimer) { clearTimeout(_ednSingleTapTimer); _ednSingleTapTimer = null; }
                _ednSendContextMenu(targetInfo.nid, _ednTouchStartX, _ednTouchStartY, targetInfo.defText, 'reviewer');
            }, 450);
        }
    }, { passive: true, capture: true });

    document.addEventListener('touchmove', function(e) {
        if (!e.touches || e.touches.length !== 1) {
            _ednClearLongPress();
            _ednTouchTargetBadge = null;
            return;
        }
        var touch = e.touches[0];
        var dist = Math.hypot(touch.clientX - _ednTouchStartX, touch.clientY - _ednTouchStartY);
        if (dist > 10) {
            _ednClearLongPress();
            _ednTouchTargetBadge = null;
        }
    }, { passive: true, capture: true });

    document.addEventListener('touchend', function(e) {
        _ednClearLongPress();
        window._ednTouchRecently = true;
        setTimeout(function() { window._ednTouchRecently = false; }, 450);
        if (_ednLongPressTriggered) {
            if (e.cancelable) e.preventDefault();
            e.stopPropagation();
            setTimeout(function() { _ednLongPressTriggered = false; }, 300);
            return;
        }
        var badge = _ednTouchTargetBadge || _findCardBadge(e);
        if (badge) {
            var nid = _ednGetNid(badge);
            if (nid) {
                var now = Date.now();
                if (_ednLastTapNid === nid && (now - _ednLastTapTime < 350)) {
                    // Double tap tactile : ouvrir dans l'explorateur
                    if (_ednSingleTapTimer) { clearTimeout(_ednSingleTapTimer); _ednSingleTapTimer = null; }
                    _ednLastTapNid = null;
                    _ednLastTapTime = 0;
                    if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
                    if (e.cancelable) e.preventDefault();
                    e.stopPropagation();
                    return;
                } else {
                    // Simple tap tactile : afficher la preview
                    _ednLastTapNid = nid;
                    _ednLastTapTime = now;
                    if (_ednSingleTapTimer) clearTimeout(_ednSingleTapTimer);
                    var capturedBadge = badge;
                    _ednSingleTapTimer = setTimeout(function() {
                        _ednSingleTapTimer = null;
                        var box = document.getElementById('edn-preview-box');
                        if (box && box.contains(capturedBadge)) {
                            // Badge inside preview box
                            if (window._edn_hover_target) {
                                var currentNid = _ednGetNid(window._edn_hover_target);
                                if (!window._ednPreviewHistory) window._ednPreviewHistory = [];
                                if (currentNid && window._ednPreviewHistory.indexOf(currentNid) === -1) {
                                    window._ednPreviewHistory.push(currentNid);
                                }
                            }
                        }
                        window._edn_hover_target = capturedBadge;
                        if (typeof pycmd !== 'undefined') pycmd('cards_ct_hover:' + nid);
                        else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_hover:' + nid);
                    }, 220);
                    if (e.cancelable) e.preventDefault();
                    e.stopPropagation();
                    return;
                }
            }
        }
    }, true);

    document.addEventListener('touchcancel', function(e) {
        _ednClearLongPress();
        _ednLongPressTriggered = false;
        _ednTouchTargetBadge = null;
        setTimeout(function() { window._ednTouchRecently = false; }, 300);
    }, true);

    // Delegated click handler on the preview box for reliable click-before-hover
    // Fires in capture phase (before hover), cancels any pending hover navigation.
    document.addEventListener('mousedown', function(e) {
        if (e.button === 2) {
            // Clic droit
            window._ednContextMenuActive = true;
            var targetInfo = _ednFindTargetForContextMenu(e);
            if (targetInfo && targetInfo.nid) {
                var msg = 'edn_right_click_nid:' + targetInfo.nid + '|||' + (targetInfo.defText || '');
                if (typeof pycmd !== 'undefined') pycmd(msg);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
            } else {
                var msg = 'edn_right_click_nid:none';
                if (typeof pycmd !== 'undefined') pycmd(msg);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
            }
            return;
        }
        if (window._ednTouchRecently) return;
        window._ednContextMenuActive = false;
        var target = _findCardBadge(e);
        if (!target) return;
        var box = document.getElementById('edn-preview-box');
        if (!box || !box.contains(target)) return;
        // Arm the click-wins flag as early as mousedown so hover timer cannot fire
        if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
        window._ednPreviewBadgeClicked = true;
    }, true);

    document.addEventListener('contextmenu', function(e) {
        var targetInfo = _ednFindTargetForContextMenu(e);
        if (targetInfo && targetInfo.nid) {
            window._ednContextMenuActive = true;
            var msg = 'edn_right_click_nid:' + targetInfo.nid + '|||' + (targetInfo.defText || '');
            if (typeof pycmd !== 'undefined') pycmd(msg);
            else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
        }
    }, true);

    document.addEventListener('click', function(e) {
        if (_ednLongPressTriggered || window._ednTouchRecently) {
            e.preventDefault();
            e.stopImmediatePropagation();
            return false;
        }
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

            // Badge inside the preview box: hover-navigate (100ms)
            if (box && box.contains(target)) {
                if (window._ednLastPopupTime && (Date.now() - window._ednLastPopupTime < 100)) {
                    return;
                }
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
                }, 100);
                return;
            }

            // Badge on the main card: normal hover preview
            window._ednHoverTimer = setTimeout(function() {
                if(typeof pycmd !== 'undefined') {
                    pycmd("cards_ct_hover:" + nid);
                    window._edn_hover_target = target;
                    window._edn_parent_badge = target;
                } else if(typeof bridgeCommand !== 'undefined') {
                    bridgeCommand("cards_ct_hover:" + nid);
                    window._edn_hover_target = target;
                    window._edn_parent_badge = target;
                }
            }, 100);
        }
    }, true);

    document.addEventListener("mouseout", function(e) {
        if (window._ednPreviewUpdating) return;
        var target = _findCardBadge(e);
        if(target) {
            var box = window._ednGetOrCreateBox();
            var isMouseInside = false;
            if (box) {
                if (box.contains(e.relatedTarget)) {
                    isMouseInside = true;
                } else {
                    try {
                        if (box.matches(':hover') || document.querySelector('#edn-preview-box:hover')) {
                            isMouseInside = true;
                        }
                    } catch(err) {}
                }
            }
            if (isMouseInside || window._ednContextMenuActive) {
                return;
            }
            if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
            window._ednHideTimer = setTimeout(function() {
                if (window._ednPreviewUpdating) return;
                if (window._ednContextMenuActive) return;
                if(box) box.style.display = "none";
                window._edn_hover_target = null;
                window._edn_parent_badge = null;
                window._ednPositionLocked = false;
                window._ednHideTimer = null;
                if (window._ednRestoreScroll) window._ednRestoreScroll();
            }, 300);
        }
    }, true);

    document.addEventListener("click", function(e) {
        if (_ednLongPressTriggered || window._ednTouchRecently) return;
        window._ednContextMenuActive = false;
        var box = document.getElementById("edn-preview-box");
        if(box && !box.contains(e.target)) {
            box.style.display = "none";
            window._edn_hover_target = null;
            window._edn_parent_badge = null;
            window._ednPositionLocked = false;
            if (window._ednRestoreScroll) window._ednRestoreScroll();
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
                    window._edn_parent_badge = badge;
                    if (typeof pycmd !== 'undefined') pycmd("cards_ct_hover:" + nid);
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand("cards_ct_hover:" + nid);
                }, 150);
            }
        } else {
            if (window._ednCurrentCaretBadge) {
                window._ednCurrentCaretBadge = null;
                var box = document.getElementById('edn-preview-box');
                if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; }
                if (window._ednContextMenuActive) return;
                window._ednHideTimer = setTimeout(function() {
                    if (box) box.style.display = "none";
                    window._edn_hover_target = null;
                    window._edn_parent_badge = null;
                    window._ednPositionLocked = false;
                    window._ednHideTimer = null;
                    if (window._ednRestoreScroll) window._ednRestoreScroll();
                }, 300);
            }
        }
    });

    // -- Scroll-sync : repositionne le box quand la carte derriere defiles --
    // Note: on ignore les scrolls provenant du box lui-meme (sinon position derivee)
    document.addEventListener("scroll", function(e) {
        var box = document.getElementById("edn-preview-box");
        if (!box || box.style.display === "none" || !window._edn_hover_target) return;
        // Si la cible n'est plus dans le DOM, ne rien faire
        if (!document.body.contains(window._edn_hover_target)) return;
        // Si le scroll vient de l'interieur du preview box, ne pas repositionner
        if (box === e.target || box.contains(e.target)) return;
        var insideBox = box.contains(window._edn_hover_target);
        if (!insideBox) {
            // Recalcul depuis la cible originale (externe au box)
            var rect = window._edn_hover_target.getBoundingClientRect();
            if (window._ednPositionAndSizeBox) {
                window._ednPositionAndSizeBox(rect, false);
            }
        }
    }, true);

    window.show_edn_preview = function(html, virtualFlagColor) {
        var box = window._ednGetOrCreateBox();
        var anchor = window._edn_parent_badge || window._edn_hover_target;
        if (!anchor || !document.body.contains(anchor)) {
            if (box) box.style.display = "none";
            return;
        }
        var initialRect = anchor.getBoundingClientRect();
        if (initialRect.width === 0 && initialRect.height === 0) {
            if (box) box.style.display = "none";
            return;
        }
        virtualFlagColor = virtualFlagColor || "";
        var shouldScroll = window._edn_needs_scroll;
        window._edn_needs_scroll = false;
        window._ednLastPopupTime = Date.now();
        // Extraire le <style> et l'injecter dans <head> (stable) pour éviter
        // les recalculs CSS globaux qui font disparaître les logos de la carte mère
        var tempDiv = document.createElement('div');
        tempDiv.innerHTML = html;
        var styleEl = tempDiv.querySelector('style');
        if (styleEl) {
            var headStyle = document.getElementById('edn-preview-head-style');
            if (headStyle) {
                headStyle.parentNode.removeChild(headStyle);
            }
            headStyle = document.createElement('style');
            headStyle.id = 'edn-preview-head-style';
            headStyle.textContent = styleEl.textContent;
            document.head.appendChild(headStyle);
            styleEl.parentNode.removeChild(styleEl);
        }
        window._ednPreviewUpdating = true;
        box.innerHTML = tempDiv.innerHTML;
        _capitalizePreview(box);
        
        // Appliquer les styles de rang et de flag selon les préférences EDN
        try {
            var prefs = {};
            try {
                if (typeof edn_prefs !== "undefined") {
                    prefs = edn_prefs;
                } else {
                    prefs = JSON.parse(localStorage.getItem("edn_card_prefs") || "{}");
                }
            } catch(e) {}
            var showRank = prefs.hasOwnProperty('showRankBadge') ? prefs.showRankBadge : true;
            var showFlags = prefs.hasOwnProperty('showFlagBorders') ? prefs.showFlagBorders : true;

            var tagsEl = box.querySelector("[id*='tagsData']") || box.querySelector(".tags") || box.querySelector("[id*='tags']");
            var tags = tagsEl ? tagsEl.innerHTML : "";
            var rang = tags.includes("Rang::A") ? "A" : tags.includes("Rang::B") ? "B" : tags.includes("Rang::C") ? "C" : "?";
            
            if (showRank) {
                var colorA = (prefs && prefs.rank_color_a) ? prefs.rank_color_a : '#111827';
                var colorB = (prefs && prefs.rank_color_b) ? prefs.rank_color_b : '#3182CE';
                var colorC = (prefs && prefs.rank_color_c) ? prefs.rank_color_c : '#38A169';
                var rankColor = rang === 'A' ? colorA : rang === 'B' ? colorB : rang === 'C' ? colorC : null;

                var bars = box.querySelectorAll('.bar, .barHider');
                for (var i = 0; i < bars.length; i++) {
                    var b = bars[i];
                    if (!b.classList.contains('edn-rang-' + rang)) {
                        b.classList.add('edn-rang-' + rang);
                    }
                    if (rankColor) {
                        b.style.backgroundColor = rankColor;
                        b.style.borderColor = rankColor;
                    }
                }
            }
            
            var realColor = null;
            if (showFlags) {
                var flagsEl = box.querySelector("[id*='flagsData']") || box.querySelector("[id*='flags']");
                var flags = flagsEl ? flagsEl.innerHTML.trim() : "";
                var flagColorMap = {
                    'flag1': '#E53E3E', // Rouge
                    'flag2': '#DD6B20', // Orange
                    'flag3': '#38A169', // Vert
                    'flag4': '#3182CE', // Bleu
                    'flag5': '#D53F8C', // Rose
                    'flag6': '#00A3C4', // Turquoise
                    'flag7': '#805AD5'  // Violet
                };
                for (var key in flagColorMap) {
                    if (flags.includes(key)) {
                        realColor = flagColorMap[key];
                        break;
                    }
                }
                if (!realColor && tags.includes("Difficile")) {
                    realColor = '#E53E3E';
                }
                if (!realColor && tags.includes("Bleu")) {
                    realColor = '#3182CE';
                }
            }
            
            var dualFlags = prefs.hasOwnProperty('previewDualFlags') ? prefs.previewDualFlags : false;
            var sections = box.getElementsByClassName("section");
            var hiders = box.getElementsByClassName("barHider");
            var barsList = box.getElementsByClassName("bar");
            
            if (showFlags && dualFlags && virtualFlagColor && realColor) {
                var dualGradient = "linear-gradient(to right, " + virtualFlagColor + " 50%, " + realColor + " 50%) 1";
                for (var sIdx = 0; sIdx < sections.length; sIdx++) {
                    sections[sIdx].style.borderColor = "transparent";
                    sections[sIdx].style.borderImage = dualGradient;
                }
                for (var hIdx = 0; hIdx < hiders.length; hIdx++) {
                    hiders[hIdx].style.borderColor = "transparent";
                    hiders[hIdx].style.borderImage = dualGradient;
                }
                for (var bIdx = 0; bIdx < barsList.length; bIdx++) {
                    barsList[bIdx].style.borderImage = "none";
                    barsList[bIdx].style.borderColor = virtualFlagColor;
                }
            } else {
                var activeColor = showFlags ? (realColor || virtualFlagColor) : null;
                if (activeColor) {
                    for (var sIdx = 0; sIdx < sections.length; sIdx++) {
                        sections[sIdx].style.borderImage = "none";
                        sections[sIdx].style.borderColor = activeColor;
                    }
                    for (var hIdx = 0; hIdx < hiders.length; hIdx++) {
                        hiders[hIdx].style.borderImage = "none";
                        hiders[hIdx].style.borderColor = activeColor;
                    }
                    for (var bIdx = 0; bIdx < barsList.length; bIdx++) {
                        barsList[bIdx].style.borderImage = "none";
                        barsList[bIdx].style.borderColor = activeColor;
                    }
                } else {
                    for (var sIdx = 0; sIdx < sections.length; sIdx++) {
                        sections[sIdx].style.borderImage = "none";
                        sections[sIdx].style.borderColor = "";
                    }
                    for (var hIdx = 0; hIdx < hiders.length; hIdx++) {
                        hiders[hIdx].style.borderImage = "none";
                        hiders[hIdx].style.borderColor = "";
                    }
                    for (var bIdx = 0; bIdx < barsList.length; bIdx++) {
                        barsList[bIdx].style.borderImage = "none";
                        barsList[bIdx].style.borderColor = "";
                    }
                }
            }
        } catch(e) {
            console.error("Error styling edn-preview-box:", e);
        }
        
        
        // Masquer les sections vides (par exemple tags / items vides) pour gagner de la place
        box.querySelectorAll('.section').forEach(function(section) {
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

        box.style.display = "block";
        box.style.overflowY = "auto";
        requestAnimationFrame(function() { window._ednPreviewUpdating = false; });

        // Wrap in requestAnimationFrame to guarantee layout has reflowed and scrollHeight is fully computed
        requestAnimationFrame(function() {
            var anchor = window._edn_parent_badge || window._edn_hover_target;
            if (anchor) {
                var rect = anchor.getBoundingClientRect();
                var spaceBelow = window.innerHeight - rect.top - 10;
                
                // Measure actual content height of the preview to avoid scrolling too much
                box.style.maxHeight = "90vh";
                var contentHeight = box.scrollHeight || 300;
                var targetSpaceBelow = Math.min(contentHeight + 20, Math.min(450, window.innerHeight * 0.6));
                
                if (shouldScroll && spaceBelow < targetSpaceBelow) {
                    var scrollAmount = targetSpaceBelow - spaceBelow;
                    if (window._ednOriginalScrollY === undefined || window._ednOriginalScrollY === null) {
                        window._ednOriginalScrollY = window.scrollY;
                    }
                    var spacer = document.getElementById('edn-scroll-spacer');
                    if (!spacer) {
                        spacer = document.createElement('div');
                        spacer.id = 'edn-scroll-spacer';
                        spacer.style.cssText = 'width:100%;pointer-events:none;transition:height 0.3s ease;';
                        document.body.appendChild(spacer);
                    }
                    var currentSpacerHeight = parseFloat(spacer.style.height) || 0;
                    var neededSpacerHeight = Math.max(currentSpacerHeight, scrollAmount + 100);
                    spacer.style.height = neededSpacerHeight + 'px';
                    
                    // Force a reflow of the body offsetHeight so that the browser registers 
                    // the new height before we call scrollBy.
                    void document.body.offsetHeight;
                    
                    // Scroll in a setTimeout to guarantee layout registration
                    setTimeout(function() {
                        window.scrollBy({ top: scrollAmount, behavior: 'smooth' });
                    }, 10);
                }
            }
        });
        
        function repositionAndSize(doScroll) {
            var anchor = window._edn_parent_badge || window._edn_hover_target;
            if (!anchor || !document.body.contains(anchor)) {
                if (box) box.style.display = "none";
                return;
            }
            var rect = anchor.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) {
                if (box) box.style.display = "none";
                return;
            }
            if (window._ednPositionAndSizeBox) {
                window._ednPositionAndSizeBox(rect, doScroll);
            }
        }
        
        // Ajuster la position : seulement si changement de cible majeure ou si non locké
        if (window._edn_parent_badge) {
            repositionAndSize(false);
            window._ednPositionLocked = true;
        } else if (window._edn_hover_target && !window._ednPositionLocked) {
            repositionAndSize(false);
            window._ednPositionLocked = true;
        }
        
        // Étape 1 : ajustement rapide de la hauteur (avant MathJax)
        requestAnimationFrame(function() {
            repositionAndSize(shouldScroll);
        });
        // Étape 1b : recalcul retardé pour les previews complexes (preview-in-preview)
        // Le CSS flex layout peut ne pas être résolu au premier requestAnimationFrame
        setTimeout(function() {
            repositionAndSize(false);
        }, 150);
        
        // MathJax typesetting avec retry : on boucle jusqu'à ce que MathJax soit prêt
        // (max ~1.5 s, toutes les 100 ms), puis on retraite le contenu du box.
        (function _ednTryTypeset(attempt) {
            if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                try {
                    if (MathJax.typesetClear) { MathJax.typesetClear([box]); }
                    MathJax.typesetPromise([box]).then(function() {
                        // Réajuster la hauteur après rendu LaTeX
                        repositionAndSize(shouldScroll);
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



def _move_br_outside_kbd(html_text):
    if not html_text:
        return html_text
        
    def replace_kbd(match):
        kbd_open = match.group(1)
        kbd_content = match.group(2)
        
        # Find all leading <br> tags
        leading_br_pattern = r'^(\s*<br\s*/?>\s*)+'
        leading_match = re.match(leading_br_pattern, kbd_content, re.IGNORECASE)
        leading_brs = ""
        if leading_match:
            leading_brs = leading_match.group(0)
            kbd_content = kbd_content[leading_match.end():]
            
        # Find all trailing <br> tags
        trailing_br_pattern = r'(\s*<br\s*/?>\s*)+$'
        trailing_match = re.search(trailing_br_pattern, kbd_content, re.IGNORECASE)
        trailing_brs = ""
        if trailing_match:
            trailing_brs = trailing_match.group(0)
            kbd_content = kbd_content[:trailing_match.start()]
            
        # If there are still any <br> tags in the middle, we can just move them to the end
        middle_br_count = len(re.findall(r'<br\s*/?>', kbd_content, re.IGNORECASE))
        if middle_br_count > 0:
            kbd_content = re.sub(r'<br\s*/?>', '', kbd_content, flags=re.IGNORECASE)
            trailing_brs += '<br>' * middle_br_count
            
        return f'{leading_brs}{kbd_open}{kbd_content}</kbd>{trailing_brs}'

    # Match <kbd class="clickable_cards"...>...</kbd>
    pattern = r'(<kbd\s+class="clickable_cards"[^>]*>)(.*?)(</kbd>)'
    return re.sub(pattern, replace_kbd, html_text, flags=re.DOTALL)


def _disconnect_app_state(app, dialog):
    """Déconnecte proprement le signal applicationStateChanged du dialog."""
    try:
        conn = getattr(dialog, '_app_state_conn', None)
        if conn is not None:
            app.applicationStateChanged.disconnect(conn)
            dialog._app_state_conn = None
    except Exception:
        pass


def _clean_kbd_html(html_text):

    """Nettoie le HTML des badges <kbd> : supprime les vides, corrige les imbriqués."""
    if not html_text:
        return html_text
        
    # 0. Déplacer les <br> à l'intérieur des <kbd> vers l'extérieur
    html_text = _move_br_outside_kbd(html_text)
        
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
    
    # Remplacer onclick/ondblclick par data-onclick/data-ondblclick pour éviter les violations de Content Security Policy (CSP)
    import re as re2
    if output.question_text:
        output.question_text = re2.sub(r'(?<!data-)onclick=(["\'])(.*?)\1', r'data-onclick=\1\2\1', output.question_text, flags=re2.IGNORECASE)
        output.question_text = re2.sub(r'(?<!data-)ondblclick=(["\'])(.*?)\1', r'data-ondblclick=\1\2\1', output.question_text, flags=re2.IGNORECASE)
    if output.answer_text:
        output.answer_text = re2.sub(r'(?<!data-)onclick=(["\'])(.*?)\1', r'data-onclick=\1\2\1', output.answer_text, flags=re2.IGNORECASE)
        output.answer_text = re2.sub(r'(?<!data-)ondblclick=(["\'])(.*?)\1', r'data-ondblclick=\1\2\1', output.answer_text, flags=re2.IGNORECASE)

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
            recto = get_note_recto(note, 240)
        except:
            recto = "[Vide]"
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


def _hide_reviewer_hover_preview(*args, **kwargs):
    """Ferme et nettoie immédiatement la preview hover du reviewer lors des transitions."""
    try:
        if mw.reviewer and getattr(mw.reviewer, 'web', None):
            mw.reviewer.web.eval("var b = document.getElementById('edn-preview-box'); if(b) b.style.display = 'none'; window._edn_hover_target = null; window._edn_parent_badge = null; if (window._ednHoverTimer) { clearTimeout(window._ednHoverTimer); window._ednHoverTimer = null; } if (window._ednHideTimer) { clearTimeout(window._ednHideTimer); window._ednHideTimer = null; }")
    except Exception:
        pass

def _on_reviewer_show_answer(card):
    """
    Appelé quand l'answer d'une carte est affichée dans le reviewer.
    """
    _hide_reviewer_hover_preview()
    if not mw.reviewer or not mw.reviewer.web:
        return

def _on_theme_changed():
    """Protection contre le crash lors du changement de thème.
    
    Le hook theme_did_change appelle page().setBackgroundColor() sur toutes
    les AnkiWebView, y compris la preview popup qui peut avoir été détruite.
    On la nettoie préventivement et on vide les caches de preview.
    """
    clear_hover_preview_cache()
    try:
        from .editor_assistant import clear_assistant_preview_cache
        clear_assistant_preview_cache()
    except Exception:
        pass

    global _active_dialog
    if _is_active_dialog_alive():
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


def _on_operation_did_execute(changes, handler):
    """Invalide le cache et actualise la preview si une note, carte ou modèle est modifié dans Anki."""
    try:
        if getattr(changes, "note", False) or getattr(changes, "card", False) or getattr(changes, "notetype", False):
            clear_hover_preview_cache()
            try:
                from .editor_assistant import clear_assistant_preview_cache, refresh_active_preview_if_open
                clear_assistant_preview_cache()
                refresh_active_preview_if_open()
            except Exception:
                pass
    except Exception as e:
        log(f"_on_operation_did_execute error: {e}")


def _on_editor_unfocus_field(changed, note, current_field_idx):
    """Invalide le cache de la note quand l'utilisateur quitte un champ modifié dans l'éditeur."""
    try:
        if changed and note and note.id:
            clear_hover_preview_cache(note.id)
            try:
                from .editor_assistant import clear_assistant_preview_cache, refresh_active_preview_if_open
                clear_assistant_preview_cache(note.id)
                refresh_active_preview_if_open(note.id)
            except Exception:
                pass
    except Exception as e:
        log(f"_on_editor_unfocus_field error: {e}")


def _on_editor_typing_timer(editor):
    """Invalide le cache de la note en cours d'édition pendant la frappe."""
    try:
        if editor and editor.note and editor.note.id:
            clear_hover_preview_cache(editor.note.id)
            try:
                from .editor_assistant import clear_assistant_preview_cache
                clear_assistant_preview_cache(editor.note.id)
            except Exception:
                pass
    except Exception:
        pass


def _on_editor_load_note(editor):
    """Invalide le cache hover lors du chargement de la note."""
    try:
        if editor and editor.note and editor.note.id:
            clear_hover_preview_cache(editor.note.id)
    except Exception:
        pass


def _get_virtual_flag_color(note, cards=None) -> str:
    """Détermine la couleur du drapeau virtuel (#E53E3E si suspendue, #3182CE si new, ou '' sinon)."""
    try:
        from .edn_menu.card_styles import load_prefs
        prefs = load_prefs()
        show_flags = prefs.get("showFlagBorders", True)
        if not show_flags:
            return ""
        if not prefs.get("virtualBlueFlagForNewCards", False):
            return ""
            
        _ANKI_FLAG_COLORS = {
            "flag1": "#E53E3E",  # Rouge
            "flag2": "#DD6B20",  # Orange
            "flag3": "#38A169",  # Vert
            "flag4": "#3182CE",  # Bleu
            "flag5": "#D53F8C",  # Rose
            "flag6": "#00A3C4",  # Turquoise
            "flag7": "#805AD5",  # Violet
            "flag8": "#B7791F",  # Réservé (or foncé)
            "flag9": "#718096",  # Réservé (gris)
        }
        def _resolve_flag_color(val, fallback):
            if not val:
                return fallback
            v = val.strip().lower()
            return _ANKI_FLAG_COLORS.get(v, val)

        _addon_cfg = mw.addonManager.getConfig(__name__) or {}
        _color_new = _resolve_flag_color(
            prefs.get("virtual_flag_color_new") or _addon_cfg.get("virtual_flag_color_new", "flag4"), "#3182CE"
        )
        _color_sus = _resolve_flag_color(
            prefs.get("virtual_flag_color_suspended") or _addon_cfg.get("virtual_flag_color_suspended", "flag1"), "#E53E3E"
        )

        if cards is None and note is not None:
            try:
                cards = note.cards()
            except Exception:
                cards = []
        if not cards:
            return ""

        any_suspended = any(c.queue == -1 for c in cards)
        all_new = all(c.type == 0 and c.queue >= 0 for c in cards)
        if any_suspended:
            return _color_sus
        elif all_new:
            return _color_new
    except Exception as e:
        print("Error resolving virtual flag color:", e)
    return ""


def _load_preview_edn_css() -> str:
    """Charge les règles CSS de preview depuis user_files/preview_edn.css.
    Si le fichier n'existe pas, retourne la chaîne CSS par défaut.
    """
    import os
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    css_path = os.path.join(addon_dir, "user_files", "preview_edn.css")
    if os.path.exists(css_path):
        try:
            with open(css_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            from .logger import log
            log(f"Error reading preview_edn.css: {e}")
            
    # Fallback par défaut si le fichier a été supprimé
    return """
html, body {
    margin: 0;
    font-size: 13px;
    overflow-x: hidden;
    overflow-y: auto !important;
    padding: 0 !important;
    height: auto !important;
    min-height: 0 !important;
}

.edn-specialty-icons {
    display: none !important;
}

[id*='FSRS'], [id*='fsrs'], .fsrs-info {
    display: none !important;
}

hr {
    display: none !important;
}

.edn-nid {
    display: none !important;
}

.clickable_cards .edn-nid {
    display: inline !important;
}

.edn-nid[style*="display:none"],
.edn-nid[style*="display: none"],
.clickable_cards .edn-nid[style*="display:none"],
.clickable_cards .edn-nid[style*="display: none"],
#edn-preview-box .edn-nid[style*="display:none"],
#edn-preview-box .edn-nid[style*="display: none"] {
    display: none !important;
}

.card > *:last-child {
    margin-bottom: 0 !important;
    padding-bottom: 0 !important;
}

.card {
    background: transparent !important;
    background-color: transparent !important;
    margin: 0 !important;
    padding: 2px 3px !important;
    font-size: 18px !important;
    line-height: 1.35 !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
    text-align: left !important;
    height: auto !important;
    min-height: 0 !important;
}

.preview-verso {
    text-align: left !important;
}

.preview-recto {
    text-align: center !important;
}

.recto, .recto * {
    text-align: center !important;
}

.answer, .answer *, .tags, .tags * {
    text-align: left !important;
}

a {
    font-size: 0.85em !important;
}

.section {
    display: flex !important;
    margin: 2px 0 !important;
    padding: 0 !important;
    margin-left: 0 !important;
    margin-right: 0 !important;
    width: 100% !important;
    box-sizing: border-box !important;
    min-height: 0 !important;
    border-width: 1.5px !important;
}

.card > .section:first-child {
    margin-top: 0 !important;
}

.card > .section:last-child {
    margin-bottom: 0 !important;
}

div[class*='section'] {
    display: flex !important;
}

.items {
    margin-left: 8px !important;
    margin-right: 8px !important;
    padding: 2px 0 !important;
    min-height: 0 !important;
    white-space: normal !important;
    word-break: break-word !important;
    box-sizing: border-box !important;
}

.items, .items span, .items div, .items p, .items li, .items ul, .items ol {
    font-size: 18px !important;
}

.items ul, .items ol {
    padding-top: 2px !important;
    padding-bottom: 2px !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    padding-left: 18px !important;
}

.items li {
    padding-top: 0px !important;
    padding-bottom: 0px !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
}

.items ul ul, .items ol ol, .items ul ol, .items ol ul {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
}

.items > span ul, .items > span ol {
    padding-left: 18px !important;
    margin: 0 !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}

.items li > ul, .items li > ol {
    padding-left: 14px !important;
    margin: 0 !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}

.items > span {
    display: block !important;
}

.edn-desc, .items > span.edn-desc {
    display: inline !important;
}

.items.answer {
    padding: 1px 0 !important;
}

.bar {
    flex: 0 0 31px !important;
    width: 31px !important;
    min-height: 31px !important;
    margin: 0 !important;
    padding: 0 !important;
    background-size: 23px !important;
    border-right-width: 1px !important;
}

.barHider {
    display: none !important;
}

br {
    display: block !important;
    content: '' !important;
    margin: 2px 0 !important;
}

.card > br:first-child {
    display: none !important;
}

.card > br:last-child {
    display: none !important;
}

div:empty:not(.bar), p:empty {
    display: none !important;
}

.clickable_cards {
    font-size: 14px !important;
    height: auto !important;
    line-height: 14px !important;
    padding: 3px 5px !important;
    margin: 3px !important;
    cursor: pointer;
    transition: all 0.2s ease;
}

.clickable_cards:hover {
    background-color: #3b82f6 !important;
    color: white !important;
}

.items.cartesLiees {
    flex-direction: row !important;
    flex-wrap: wrap !important;
    align-items: center !important;
    justify-content: flex-start !important;
    margin-left: 0 !important;
}

.cartesLiees br, .items.cartesLiees br {
    flex-basis: 100% !important;
    width: 100% !important;
    height: 0 !important;
    margin: 0 !important;
    display: block !important;
    content: "" !important;
}

.cloze {
    color: orange !important;
    font-weight: bold !important;
}

.cloze-inactive {
    color: orange !important;
    font-weight: bold !important;
}

#edn-preview-box {
    max-width: 480px !important;
    width: fit-content !important;
    min-width: 250px !important;
    padding: 2px 3px !important;
    box-sizing: border-box !important;
}

#edn-preview-box .card,
#edn-preview-box .edn-preview-isolated {
    padding: 0 !important;
    margin: 0 !important;
    box-sizing: border-box !important;
    width: 100% !important;
}

#edn-preview-box .edn-preview-isolated > *:last-child {
    margin-bottom: 0 !important;
    padding-bottom: 0 !important;
}

#edn-preview-box .edn-preview-isolated {
    overflow: hidden;
    padding: 0 !important;
}

#edn-preview-box .edn-nid {
    display: inline !important;
}
"""


_HOVER_PREVIEW_CACHE = {}

def clear_hover_preview_cache(nid=None):
    global _HOVER_PREVIEW_CACHE
    if nid is not None:
        _HOVER_PREVIEW_CACHE.pop(str(nid), None)
        _HOVER_PREVIEW_CACHE.pop(int(nid), None)
    else:
        _HOVER_PREVIEW_CACHE.clear()

@perf_log
def on_js_message_reviewer(handled, message, context):
    if not isinstance(message, str):
        return handled
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
        import time
        global _last_gui_close_time
        if time.time() - _last_gui_close_time < 1.0:
            return (True, None)
        nid = message.split(":")[1]
        
        try:
            import json, re
            global _HOVER_PREVIEW_CACHE
            
            note = None
            try:
                note = mw.col.get_note(int(nid))
            except Exception:
                pass

            if not note:
                try:
                    preview_css = _load_preview_edn_css()
                    scoped_css = _scope_css_for_preview(preview_css, "#edn-preview-box")
                    html = f"""
                    <style>
                        {scoped_css}
                    </style>
                    <div class="edn-preview-isolated card" style="pointer-events: auto; overflow: hidden; padding: 10px !important; font-style: italic; font-size: 18px !important; line-height: 1.35 !important;">
                        Lien mort.
                    </div>
                    """
                except:
                    html = "<div class='card' style='padding:10px !important; font-style:italic; font-size:18px !important; line-height: 1.35 !important;'>Lien mort.</div>"
                virtual_flag_color = ""
            else:
                current_mod = note.mod
                notetype_mod = note.note_type().get('mod', 0)
                cache_key = (current_mod, notetype_mod)
                
                if nid in _HOVER_PREVIEW_CACHE and _HOVER_PREVIEW_CACHE[nid][0] == cache_key:
                    _, html, virtual_flag_color = _HOVER_PREVIEW_CACHE[nid]
                else:
                    virtual_flag_color = ""
                    try:
                        cards = note.cards()
                        if not cards:
                            raise Exception()
                        card = cards[0]
                        virtual_flag_color = _get_virtual_flag_color(note, cards)
                        
                        rendered_a = card.answer()
                        
                        isolated = re.sub(r'(?<![\w-])id=(["\'])(.*?)\1', r'id="edn_preview_\2"', rendered_a)
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

                        # Réécrire les images relatives du HTML pour passer par le serveur local d'Anki
                        isolated = re.sub(r'src=(["\'])(?![a-z]+:)(?!/)([^"\']+)\1', r'src="/\2"', isolated, flags=re.IGNORECASE)

                        # --- BLACKLIST DE SECTIONS DANS L'APERÇU ---
                        config = mw.addonManager.getConfig(__name__) or {}
                        hidden_sections_config = config.get("hidden_preview_sections", [
                            "sourcesMegaContainer",
                            "commentsMegaContainer",
                            "tagsMegaContainer",
                        ])

                        # Cacher complètement les sections de la blacklist dans le HTML (display: none !important)
                        sec_to_field = {
                            "sourcesMegaContainer": "Sources",
                            "commentsMegaContainer": "Comments",
                            "tagsMegaContainer": "Tags",
                            "erreursFaitesMegaContainer": "Erreurs faites",
                            "mnemonicsMegaContainer": "Mnemonics",
                            "infoSupplementairesMegaContainer": "Infos supplémentaires",
                            "cartesLieesMegaContainer": "Cartes liées"
                        }
                        actual_hidden = []
                        for sec in hidden_sections_config:
                            field_name = sec_to_field.get(sec)
                            has_links = False
                            if field_name and field_name in note:
                                val = note[field_name]
                                if "clickable_cards" in val or "data-nid" in val or re.search(r'\b1\d{12}\b', val):
                                    has_links = True
                            if not has_links:
                                isolated = re.sub(rf'(<div[^>]*id=["\'](?:edn_preview_)?{sec}["\'][^\'>]*style=["\'])([^\'\"]*)', r'\1display: none !important;\2', isolated, flags=re.IGNORECASE)
                                isolated = re.sub(rf'(<div[^>]*id=["\'](?:edn_preview_)?{sec}["\'])(?![^>]*style=)', r'\1 style="display: none !important;"', isolated, flags=re.IGNORECASE)
                                actual_hidden.append(sec)

                        # Le HTML d'aperçu préfixe automatiquement les IDs par edn_preview_
                        selectors = [f"#edn-preview-box #edn_preview_{sec}" for sec in actual_hidden]
                        selectors.extend(["#edn-preview-box #edn_preview_FSRS_status"])
                        id_css = ", ".join(selectors) + " { display: none !important; }" if selectors else ""
                        id_css += "\n#edn-preview-box .edn-specialty-icons { display: none !important; }\n"
                        
                        # Charger et scoper l'ensemble du CSS du modèle de la note cible
                        model_css = note.note_type().get('css', '')
                        if model_css:
                            sanitized_css = _sanitize_model_css_for_preview(model_css)
                            id_css += "\n" + _scope_css_for_preview(sanitized_css, "#edn-preview-box")
                            
                        # Charger les styles de preview depuis user_files/preview_edn.css
                        preview_css = _load_preview_edn_css()
                        scoped_css = _scope_css_for_preview(preview_css, "#edn-preview-box")
                        
                        html = f"""
                        <style>
                            {id_css}
                            {scoped_css}
                        </style>
                        <div class="edn-preview-isolated card" data-nid="{nid}" style="pointer-events: auto; overflow: hidden;">
                            {isolated}
                        </div>
                        """
                        if len(_HOVER_PREVIEW_CACHE) > 300:
                            _HOVER_PREVIEW_CACHE.pop(next(iter(_HOVER_PREVIEW_CACHE)))
                        _HOVER_PREVIEW_CACHE[nid] = (cache_key, html, virtual_flag_color)
                    except:
                        try:
                            preview_css = _load_preview_edn_css()
                            scoped_css = _scope_css_for_preview(preview_css, "#edn-preview-box")
                            html = f"""
                            <style>
                                {scoped_css}
                            </style>
                            <div class="edn-preview-isolated card" style="pointer-events: auto; overflow: hidden; padding: 10px !important; font-style: italic; font-size: 18px !important; line-height: 1.35 !important;">
                                Lien mort.
                            </div>
                            """
                        except:
                            html = "<div class='card' style='padding:10px !important; font-style:italic; font-size:18px !important; line-height: 1.35 !important;'>Lien mort.</div>"
            
            js_call = f"if(typeof show_edn_preview !== 'undefined') show_edn_preview({json.dumps(html)}, {json.dumps(virtual_flag_color)});"
            web = None
            if hasattr(context, 'eval'):
                web = context
            elif hasattr(context, 'web') and hasattr(context.web, 'eval'):
                web = context.web
            elif hasattr(context, '_web') and hasattr(context._web, 'eval'):
                web = context._web
            elif hasattr(context, 'previewWeb') and hasattr(context.previewWeb, 'eval'):
                web = context.previewWeb
            elif context is None and mw.reviewer and getattr(mw.reviewer, 'web', None) and getattr(mw, "state", "") == "review":
                web = mw.reviewer.web
                
            if web:
                try:
                    web.eval(js_call)
                except Exception:
                    pass
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
    # Si plusieurs cartes sont sélectionnées, on copie toute la sélection
    if len(browser.selected_cards()) > 1:
        copy_nid_from_browser(browser)
        return
        
    # Préférence : si l'éditeur a le focus et une note, copier via copy_nid_from_editor
    if hasattr(browser, 'editor') and browser.editor and browser.editor.note:
        copy_nid_from_editor(browser.editor)
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
    cids = browser.selected_cards()
    if not cids:
        tooltip("Aucune carte sélectionnée.")
        return
        
    unique_nids = []
    seen = set()
    for cid in cids:
        try:
            card = browser.col.get_card(cid)
            nid = card.nid
            if nid not in seen:
                seen.add(nid)
                unique_nids.append((nid, card))
        except Exception as e:
            from .logger import log
            log(f"Error getting card/note for cid {cid}: {e}")
            
    if not unique_nids:
        tooltip("Impossible de récupérer les notes.")
        return

    lines_text = []
    lines_html = []
    
    for nid, card in unique_nids:
        recto = ""
        try:
            note = card.note()
            recto = get_note_recto(note, 240)
        except:
            recto = "[Vide]"
        recto_escaped = (
            recto.replace('&', '&amp;')
            .replace('"', '&quot;')
            .replace("'", '&#39;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
        )
        
        lines_text.append(f"{recto} — {nid}")
        lines_html.append(
            f'{recto_escaped} — '
            f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{nid}"'
            f'><span class="edn-nid">{nid}</span></kbd>&nbsp;'
        )
        
    text_content = "\n".join(lines_text)
    html_content = "<br>".join(lines_html)
    
    mime = QMimeData()
    mime.setText(text_content)
    mime.setHtml(html_content)
    QApplication.clipboard().setMimeData(mime)
    
    if len(unique_nids) == 1:
        tooltip(f"NID copié: {lines_text[0]}")
    else:
        tooltip(f"{len(unique_nids)} NIDs copiés")

def copy_nid_from_editor(editor: Editor):
    if editor.note and editor.note.id:
        nid = str(editor.note.id)
        recto = ""
        try:
            recto = get_note_recto(editor.note, 240)
        except:
            recto = "[Vide]"
        recto_escaped = recto.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
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
            from .editor_assistant import toggle_assistant
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
            recto = get_note_recto(note, 240, source_note=editor.note)
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
    # Dans le browser, utiliser la copie intelligente (pour gérer la sélection multiple/l'éditeur)
    from aqt.browser import Browser
    if isinstance(parent_win, Browser):
        sc_copy.activated.connect(lambda: _copy_nid_smart(parent_win))
    else:
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
    trigger = config.get("gui_trigger", "nid:") if config else "nid:"
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
                var lines = text.split(/\r?\n/);
                var nonEmptyLines = lines.filter(function(l) { return l.trim().length > 0; });
                if (nonEmptyLines.length === 0) return;
                
                var allMatch = true;
                var nidsList = [];
                for (var i = 0; i < nonEmptyLines.length; i++) {
                    var line = nonEmptyLines[i].trim();
                    var match = line.match(/^(?:(.*?) [\u2014\u2013-] )?(\d{10,})$/);
                    if (!match) {
                        allMatch = false;
                        break;
                    }
                    nidsList.push(match[2].trim());
                }
                
                if (allMatch && nidsList.length > 0) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    
                    var lineHasDash = false;
                    try {
                        var sel2 = window.getSelection();
                        var active2 = document.activeElement;
                        if (active2 && active2.shadowRoot) sel2 = active2.shadowRoot.getSelection();
                        if (sel2 && sel2.rangeCount > 0) {
                            var range2 = sel2.getRangeAt(0);
                            var container2 = range2.startContainer;
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
                                    } catch (e) { return ""; }
                                }
                                var lineText2 = getCurrentLineText(range2, editable2);
                                if (lineText2.includes('\u2014') || lineText2.includes('\u2013') || lineText2.includes('—') || lineText2.includes('–')) {
                                    lineHasDash = true;
                                }
                            }
                        }
                    } catch(err2) {}
                    
                    if (typeof pycmd !== 'undefined') {
                        pycmd('edn_paste_smart_link:' + JSON.stringify({nids: nidsList, lineHasDash: lineHasDash}));
                    }
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
                
                var nid = '';
                if (t.dataset && t.dataset.nid) nid = t.dataset.nid;
                else {
                    var s = t.querySelector && t.querySelector('.edn-nid');
                    if (s) nid = s.textContent.trim();
                    else nid = (t.innerText || '').trim().replace(/[^0-9]/g, '');
                }
                
                // Exiger Ctrl ou Cmd dans l'éditeur pour ouvrir la carte
                if (e.ctrlKey || e.metaKey) {
                    if (nid && nid.length > 9) {
                        if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
                        else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
                    }
                } else {
                    // Clic simple sans Ctrl/Cmd : sélectionner proprement le badge entier
                    var range = document.createRange();
                    range.selectNode(t);
                    var sel = window.getSelection();
                    var active = document.activeElement;
                    if (active && active.shadowRoot && active.shadowRoot.getSelection) {
                        sel = active.shadowRoot.getSelection();
                    }
                    if (sel) {
                        sel.removeAllRanges();
                        sel.addRange(range);
                    }
                }
            });
        }

        // -- Support écran tactile (Long Press) dans l'éditeur --
        if (!window._ednEditorLongPressInit) {
            window._ednEditorLongPressInit = true;
            var _ednEdLongPressTimer = null;
            var _ednEdTouchStartX = 0;
            var _ednEdTouchStartY = 0;
            var _ednEdLongPressTriggered = false;

            document.addEventListener('touchstart', function(e) {
                if (!e.touches || e.touches.length !== 1) {
                    if (_ednEdLongPressTimer) { clearTimeout(_ednEdLongPressTimer); _ednEdLongPressTimer = null; }
                    return;
                }
                var t = _findCardBadge(e);
                if (t) {
                    var touch = e.touches[0];
                    _ednEdTouchStartX = touch.clientX;
                    _ednEdTouchStartY = touch.clientY;
                    _ednEdLongPressTriggered = false;
                    var nid = '';
                    if (t.dataset && t.dataset.nid) nid = t.dataset.nid;
                    else {
                        var s = t.querySelector && t.querySelector('.edn-nid');
                        if (s) nid = s.textContent.trim();
                        else nid = (t.innerText || '').trim().replace(/[^0-9]/g, '');
                    }
                    if (nid && nid.length > 9) {
                        var defText = '';
                        var childNodes = t.childNodes;
                        var customText = "";
                        for (var i = 0; i < childNodes.length; i++) {
                            var node = childNodes[i];
                            if (node.nodeType === Node.TEXT_NODE) {
                                customText += node.textContent;
                            }
                        }
                        customText = customText.trim();
                        if (customText) {
                            defText = customText;
                        } else {
                            var prev = t.previousSibling;
                            if (prev && prev.nodeType === Node.TEXT_NODE) {
                                var txt = prev.textContent;
                                defText = txt.replace(/[\s\u2014\u2013-]+\s*$/, '').trim();
                            }
                        }
                        if (_ednEdLongPressTimer) clearTimeout(_ednEdLongPressTimer);
                        _ednEdLongPressTimer = setTimeout(function() {
                            _ednEdLongPressTriggered = true;
                            var msg = 'edn_show_context_menu:' + nid + '|||' + Math.round(_ednEdTouchStartX) + '|||' + Math.round(_ednEdTouchStartY) + '|||' + defText + '|||editor';
                            if (typeof pycmd !== 'undefined') pycmd(msg);
                            else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
                        }, 450);
                    }
                }
            }, { passive: true, capture: true });

            document.addEventListener('touchmove', function(e) {
                if (!e.touches || e.touches.length !== 1) {
                    if (_ednEdLongPressTimer) { clearTimeout(_ednEdLongPressTimer); _ednEdLongPressTimer = null; }
                    return;
                }
                var touch = e.touches[0];
                var dist = Math.hypot(touch.clientX - _ednEdTouchStartX, touch.clientY - _ednEdTouchStartY);
                if (dist > 10) {
                    if (_ednEdLongPressTimer) { clearTimeout(_ednEdLongPressTimer); _ednEdLongPressTimer = null; }
                }
            }, { passive: true, capture: true });

            document.addEventListener('touchend', function(e) {
                if (_ednEdLongPressTimer) { clearTimeout(_ednEdLongPressTimer); _ednEdLongPressTimer = null; }
                if (_ednEdLongPressTriggered) {
                    e.preventDefault();
                    e.stopPropagation();
                    setTimeout(function() { _ednEdLongPressTriggered = false; }, 300);
                }
            }, true);

            document.addEventListener('touchcancel', function(e) {
                if (_ednEdLongPressTimer) { clearTimeout(_ednEdLongPressTimer); _ednEdLongPressTimer = null; }
                _ednEdLongPressTriggered = false;
            }, true);
        }

        if (!window._ednEditorDelegatedPointerDown) {
            window._ednEditorDelegatedPointerDown = true;
            document.addEventListener('pointerdown', function(e) {
                var t = _findCardBadge(e);
                if (t) {
                    var nid = '';
                    if (t.dataset && t.dataset.nid) nid = t.dataset.nid;
                    else {
                        var s = t.querySelector && t.querySelector('.edn-nid');
                        if (s) nid = s.textContent.trim();
                        else nid = (t.innerText || '').trim().replace(/[^0-9]/g, '');
                    }
                    if (nid && nid.length > 9) {
                        var defText = '';
                        var childNodes = t.childNodes;
                        var customText = "";
                        for (var i = 0; i < childNodes.length; i++) {
                            var node = childNodes[i];
                            if (node.nodeType === Node.TEXT_NODE) {
                                customText += node.textContent;
                            }
                        }
                        customText = customText.trim();
                        if (customText) {
                            defText = customText;
                        } else {
                            var prev = t.previousSibling;
                            if (prev && prev.nodeType === Node.TEXT_NODE) {
                                var txt = prev.textContent;
                                defText = txt.replace(/[\s\u2014\u2013-]+\s*$/, '').trim();
                            }
                        }
                        
                        var msg = 'edn_right_click_nid:' + nid + '|||' + defText;
                        if (typeof pycmd !== 'undefined') pycmd(msg);
                        else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
                        return;
                    }
                }
                var msg = 'edn_right_click_nid:none';
                if (typeof pycmd !== 'undefined') pycmd(msg);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
            }, true);
        }

        if (!window._ednEditorDelegatedContextMenu) {
            window._ednEditorDelegatedContextMenu = true;
            document.addEventListener('contextmenu', function(e) {
                var t = _findCardBadge(e);
                if (t) {
                    var nid = '';
                    if (t.dataset && t.dataset.nid) nid = t.dataset.nid;
                    else {
                        var s = t.querySelector && t.querySelector('.edn-nid');
                        if (s) nid = s.textContent.trim();
                        else nid = (t.innerText || '').trim().replace(/[^0-9]/g, '');
                    }
                    if (nid && nid.length > 9) {
                        var defText = '';
                        var childNodes = t.childNodes;
                        var customText = "";
                        for (var i = 0; i < childNodes.length; i++) {
                            var node = childNodes[i];
                            if (node.nodeType === Node.TEXT_NODE) {
                                customText += node.textContent;
                            }
                        }
                        customText = customText.trim();
                        if (customText) {
                            defText = customText;
                        } else {
                            var prev = t.previousSibling;
                            if (prev && prev.nodeType === Node.TEXT_NODE) {
                                var txt = prev.textContent;
                                defText = txt.replace(/[\s\u2014\u2013-]+\s*$/, '').trim();
                            }
                        }
                        
                        var msg = 'edn_right_click_nid:' + nid + '|||' + defText;
                        if (typeof pycmd !== 'undefined') pycmd(msg);
                        else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
                        return;
                    }
                }
                var msg = 'edn_right_click_nid:none';
                if (typeof pycmd !== 'undefined') pycmd(msg);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand(msg);
            }, true);
        }

        window._ednModifyHyperlink = function(nid, newText) {
            var allKbds = [];
            function findKbds(root) {
                if (!root) return;
                var kbds = root.querySelectorAll ? root.querySelectorAll('.clickable_cards') : [];
                for (var i = 0; i < kbds.length; i++) {
                    allKbds.push(kbds[i]);
                }
                var children = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < children.length; i++) {
                    if (children[i].shadowRoot) {
                        findKbds(children[i].shadowRoot);
                    }
                }
            }
            document.querySelectorAll('anki-editable').forEach(function(el) {
                findKbds(el);
                if (el.shadowRoot) findKbds(el.shadowRoot);
            });
            findKbds(document.body);

            var targetKbd = null;
            for (var i = 0; i < allKbds.length; i++) {
                var k = allKbds[i];
                var knid = '';
                if (k.dataset && k.dataset.nid) knid = k.dataset.nid;
                else if (k.getAttribute('data-nid')) knid = k.getAttribute('data-nid');
                else {
                    var s = k.querySelector('.edn-nid');
                    if (s) knid = s.textContent.trim();
                }
                if (knid === nid) {
                    targetKbd = k;
                    break;
                }
            }

            if (!targetKbd) return false;

            var isAlreadyCustom = false;
            var span = targetKbd.querySelector('.edn-nid');
            if (span && (span.style.display === 'none' || (span.getAttribute('style') && span.getAttribute('style').indexOf('display:none') !== -1))) {
                isAlreadyCustom = true;
            }

            if (!isAlreadyCustom) {
                var prev = targetKbd.previousSibling;
                if (prev && prev.nodeType === Node.TEXT_NODE) {
                    var txt = prev.textContent;
                    var defaultText = txt.replace(/[\s\u2014\u2013-]+\s*$/, '').trim();
                    var escaped = defaultText.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
                    var regex = new RegExp(escaped + '\\s*[\\u2014\\u2013-]\\s*$', 'i');
                    if (regex.test(txt)) {
                        prev.textContent = txt.replace(regex, '');
                    }
                }
            }

            var escapedText = newText.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
            targetKbd.innerHTML = '<span class="edn-nid" style="display:none;font-size:0;line-height:0">' + nid + '</span>' + escapedText;

            var editable = targetKbd.closest('anki-editable');
            if (!editable) {
                var rootNode = targetKbd.getRootNode ? targetKbd.getRootNode() : null;
                var host = rootNode && rootNode.host ? rootNode.host : null;
                if (host && host.tagName === 'ANKI-EDITABLE') editable = host;
            }
            if (editable) {
                editable.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
            }
            return true;
        };

        window.cards_ct_click = function(nid) {
            // Exiger Ctrl ou Cmd dans l'éditeur
            var e = window.event;
            if (e && (e.ctrlKey || e.metaKey)) {
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + nid);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + nid);
            }
        };

        // Gérer la suppression du badge (Backspace / Suppr) de manière propre
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Backspace' || e.key === 'Delete') {
                var targetNode = null;
                var activeEl = document.activeElement;
                if (activeEl && activeEl.classList.contains('clickable_cards')) {
                    targetNode = activeEl;
                } else {
                    var sel = window.getSelection();
                    var active = document.activeElement;
                    if (active && active.shadowRoot && active.shadowRoot.getSelection) {
                        sel = active.shadowRoot.getSelection();
                    }
                    if (sel && sel.isCollapsed) {
                        var node = sel.startContainer;
                        var offset = sel.startOffset;
                        
                        if (node && node.nodeType === Node.TEXT_NODE) {
                            if (e.key === 'Backspace' && offset === 0) {
                                var prev = node.previousSibling;
                                while (prev && prev.nodeType === Node.TEXT_NODE && prev.textContent === '') {
                                    prev = prev.previousSibling;
                                }
                                if (prev && prev.nodeType === Node.ELEMENT_NODE && prev.classList.contains('clickable_cards')) {
                                    targetNode = prev;
                                }
                            } else if (e.key === 'Delete' && offset === node.length) {
                                var next = node.nextSibling;
                                while (next && next.nodeType === Node.TEXT_NODE && next.textContent === '') {
                                    next = next.nextSibling;
                                }
                                if (next && next.nodeType === Node.ELEMENT_NODE && next.classList.contains('clickable_cards')) {
                                    targetNode = next;
                                }
                            }
                        } else if (node && node.nodeType === Node.ELEMENT_NODE) {
                            if (e.key === 'Backspace' && offset > 0) {
                                var prevNode = node.childNodes[offset - 1];
                                if (prevNode && prevNode.nodeType === Node.ELEMENT_NODE && prevNode.classList.contains('clickable_cards')) {
                                    targetNode = prevNode;
                                }
                            } else if (e.key === 'Delete' && offset < node.childNodes.length) {
                                var nextNode = node.childNodes[offset];
                                if (nextNode && nextNode.nodeType === Node.ELEMENT_NODE && nextNode.classList.contains('clickable_cards')) {
                                    targetNode = nextNode;
                                }
                            }
                        }
                    }
                }
                
                if (targetNode) {
                    e.preventDefault();
                    e.stopPropagation();
                    var range = document.createRange();
                    range.selectNode(targetNode);
                    var sel = window.getSelection();
                    var active = document.activeElement;
                    if (active && active.shadowRoot && active.shadowRoot.getSelection) {
                        sel = active.shadowRoot.getSelection();
                    }
                    if (sel) {
                        sel.removeAllRanges();
                        sel.addRange(range);
                    }
                    document.execCommand('delete', false, null);
                }
            }
        }, true); // phase de capture pour intercepter avant Anki
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
    from .logger import log
    log(f"_handle_editor_shortcut_key: key={key}")
    from .editor_assistant import toggle_assistant
    try:
        from .edn_menu import get_shortcut
        _kbd_prev = get_shortcut("linked_cards_kbd_preview", "p")
    except Exception:
        _kbd_prev = "p"

    assistant = getattr(editor, '_editor_assistant_web', None)
    
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
            log(f"_handle_editor_shortcut_key: dispatching key '{escaped_key}' to assistant webview")
            assistant.eval(f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: '{escaped_key}', bubbles: true}}));")
        except Exception as ex:
            log(f"Error forwarding editor key to assistant: {ex}")

def on_js_message(handled, message, context):
    if not isinstance(message, str):
        return handled
    if handled and handled[0]:
        return handled
    global _last_right_clicked_nid, _last_right_clicked_default_text, _right_clicked_on_preview_page, _active_dialog
    if message.startswith("edn_show_context_menu:"):
        payload = message[len("edn_show_context_menu:"):]
        parts = payload.split("|||")
        nid = parts[0] if len(parts) > 0 else ""
        x = int(float(parts[1])) if len(parts) > 1 and parts[1] else 0
        y = int(float(parts[2])) if len(parts) > 2 and parts[2] else 0
        default_text = parts[3] if len(parts) > 3 else ""
        source = parts[4] if len(parts) > 4 else None
        
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
            
        show_card_context_menu(nid, pos=(x, y), webview=web, default_text=default_text, source=source)
        return (True, None)
    elif message.startswith("edn_right_click_nid:"):
        val = message[len("edn_right_click_nid:"):]
        if val == "none":
            _last_right_clicked_nid = None
            _last_right_clicked_default_text = ""
            _right_clicked_on_preview_page = False
        else:
            if "|||" in val:
                nid_part, default_text = val.split("|||", 1)
            else:
                nid_part = val
                default_text = ""
            _last_right_clicked_nid = nid_part
            _last_right_clicked_default_text = default_text
            _right_clicked_on_preview_page = False
        return (True, None)
    elif message.startswith("edn_right_click_page:"):
        _last_right_clicked_nid = None
        _last_right_clicked_default_text = ""
        _right_clicked_on_preview_page = True
        return (True, None)
    if message.startswith("edn_editor_key:"):
        key = message[len("edn_editor_key:"):]
        from .editor_assistant import get_active_editor
        editor = get_active_editor(context)
        if editor:
            _handle_editor_shortcut_key(editor, key)
        return (True, None)
    elif message == "edn_nid_trigger":
        from .editor_assistant import get_active_editor
        editor = get_active_editor(context)
        if editor:
            open_search_dialog(editor)
        return (True, None)
    elif message.startswith("edn_paste_smart_link:"):
        payload_str = message[len("edn_paste_smart_link:"):]
        try:
            import json
            data = json.loads(payload_str)
            nids = data.get("nids", [])
            line_has_dash = data.get("lineHasDash", False)
            from .editor_assistant import get_active_editor
            editor = get_active_editor(context)
            if editor and editor.note and nids:
                source_note = editor.note
                items = []
                for nid in nids:
                    if line_has_dash:
                        items.append((str(nid), None))
                    else:
                        target_note = None
                        try:
                            target_note = mw.col.get_note(int(nid))
                        except Exception:
                            pass
                        smart_desc = generate_smart_link_label(source_note, target_note) if target_note else None
                        final_desc = smart_desc if smart_desc else (get_note_recto(target_note) if target_note else "")
                        items.append((str(nid), final_desc))
                
                inserter = LinkInserter(editor)
                config = mw.addonManager.getConfig(__name__) or {}
                mirror_req = config.get("mirror_link_on_paste", True)
                inserter.insert_link(items, mirror_requested=mirror_req)
        except Exception as e:
            log(f"Error handling edn_paste_smart_link: {e}")
        return (True, None)
    elif message.startswith("edn_pasted_nids:"):
        nids_str = message[len("edn_pasted_nids:"):]
        try:
            nids = [int(n.strip()) for n in nids_str.split(",") if n.strip()]
            from .editor_assistant import get_active_editor
            editor = get_active_editor(context)
            if editor and editor.note and editor.note.id:
                config = mw.addonManager.getConfig(__name__) or {}
                mirror_on_paste = config.get("mirror_link_on_paste", True)
                if mirror_on_paste:
                    source_note = editor.note
                    for target_nid in nids:
                        _proposal_queue.add(lambda done, tn=target_nid: _propose_mirror_link(
                            editor, source_note, str(tn), done
                        ))
                    if len(nids) > 1:
                        _proposal_queue.add(lambda done: _propose_cross_links(
                            editor, source_note, nids, done
                        ))
        except Exception as e:
            log(f"Error handling pasted nids: {e}")
        return (True, None)
    elif message == "edn_toggle_assistant":
        # Déclenché par le clic JS sur le bouton 'Liens' de la toolbar
        # (évite le saveNow bloquant du mécanisme func= d'Anki)
        from .editor_assistant import get_active_editor
        editor = get_active_editor(context)
        if editor:
            _focus_liens_field(editor)
        return (True, None)
    elif message == "hide_preview_popup":
        if _is_active_dialog_alive() and hasattr(_active_dialog, 'hide_preview_popup'):
            _active_dialog.hide_preview_popup()
        from .editor_assistant import _hide_card_preview_popup
        _hide_card_preview_popup()
        return (True, None)
    elif message.startswith("cards_ct_click") or message.startswith("cards_ct_hover:"):
        return on_js_message_reviewer(handled, message, context)
    elif message.startswith("gui_preview_hover:"):
        nid = message.split(":")[1]
        if _is_active_dialog_alive() and hasattr(_active_dialog, 'show_nested_preview'):
            _active_dialog.show_nested_preview(nid)
        return (True, None)
    elif message.startswith("gui_preview_mouseout:"):
        if _is_active_dialog_alive():
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
        if _is_active_dialog_alive() and hasattr(_active_dialog, 'on_gui_preview_click'):
            _active_dialog.on_gui_preview_click(nid)
        return (True, None)
    elif message == "gui_preview_key_p":
        if _is_active_dialog_alive() and hasattr(_active_dialog, 'cycle_gui_results_and_preview'):
            _active_dialog.cycle_gui_results_and_preview()
        return (True, None)
    elif message.startswith("edn_preview_resize:"):
        try:
            h = int(float(message.split(":")[1]))
            if _is_active_dialog_alive() and hasattr(_active_dialog, '_preview_dlg') and _active_dialog._preview_dlg:
                _active_dialog._adjust_preview_height(_active_dialog._preview_dlg, h)
        except Exception as e:
            print("Error resizing GUI preview:", e)
        return (True, None)
    elif message.startswith("preview_resize:") or message.startswith("preview_nested:") or message == "preview_back" or message == "escape" or message.startswith("preview_key_to_assistant:") or message.startswith("open_browser:"):
        from .editor_assistant import handle_preview_popup_message
        handle_preview_popup_message(message, context)
        return (True, None)
    elif (message.startswith("add_mirror:") or message.startswith("make_reciprocal:")
          or message.startswith("preview_card:") or message.startswith("preview_card_touch:")
          or message.startswith("hover_card:") or message == "hover_leave"
          or message.startswith("preview_key_forward:") or message.startswith("focus_preview:")
          or message.startswith("add_suggested:") or message == "edn_touch_long_press"):
        from .editor_assistant import handle_assistant_message
        handle_assistant_message(False, message, context)
        return (True, None)
    return handled

def _sanitize_model_css_for_preview(model_css: str) -> str:
    """Filtre le CSS du modèle de note pour l'usage dans les popups de preview (GUI et Sidebar).

    La review preview hérite nativement du CSS de la note (même DOM), mais les popups Qt
    chargent model_css via stdHtml(). Ce CSS contient des règles qui cassent la mise en page
    compacte des popups (text-align:center sur .card, marges 5% sur .section, etc.).

    Stratégie : on conserve tout le CSS du modèle SAUF les propriétés connues pour
    conflicgter avec la mise en page popup. Les overrides restent dans scoped_css.
    Cela permet à l'addon de fonctionner avec n'importe quel type de note, pas seulement EDN.
    """
    import re as _re

    # Règles à neutraliser dans les blocs qui correspondent à .card, .section, .preview-recto, body
    # On retire uniquement les propriétés de mise en page problématiques, on garde le reste
    _PROP_PATTERNS_TO_STRIP = [
        # text-align: center dans ces sélecteurs (on force left dans scoped_css)
        (r'text-align\s*:\s*center\s*;?', ''),
        # font-size sur .card (on fixe en scoped_css)
        (r'font-size\s*:\s*\d+px\s*;?', ''),
        # background-color sur .card (on force transparent en scoped_css)
        (r'background-color\s*:\s*white\s*;?', ''),
        (r'background-color\s*:\s*#fff\s*;?', ''),
        (r'background-color\s*:\s*#ffffff\s*;?', ''),
        # max-width sur .card (on force 100% en scoped_css)
        (r'max-width\s*:\s*\d+px\s*;?', ''),
        # marges auto sur .card (on force margin:0 en scoped_css)
        (r'margin-left\s*:\s*auto\s*;?', ''),
        (r'margin-right\s*:\s*auto\s*;?', ''),
        # height et min-height (on force auto et 0 dans scoped_css)
        (r'\bmin-height\s*:\s*[^;}\s]+;?', ''),
        (r'\bheight\s*:\s*[^;}\s]+;?', ''),
        # overflow (on force auto dans preview_edn.css / scoped_css)
        (r'\boverflow\s*:\s*[^;}\s]+;?', ''),
        (r'\boverflow-y\s*:\s*[^;}\s]+;?', ''),
        (r'\boverflow-x\s*:\s*[^;}\s]+;?', ''),
    ]

    # Sélecteurs pour lesquels on applique le filtrage des propriétés problématiques
    _SELECTORS_TO_FILTER = {'.card', 'body', '.card,', '.preview-recto', '.nom', '.description', '.tags'}

    def _filter_block(selector: str, props: str) -> str:
        """Filtre les propriétés problématiques d'un bloc CSS selon le sélecteur."""
        # Vérifier si ce sélecteur fait partie de la liste à filtrer
        sel_stripped = selector.strip()
        tokens = _re.split(r'[\s,>+~]+', sel_stripped)
        tokens = [t.strip() for t in tokens if t.strip()]
        should_filter = any(t in _SELECTORS_TO_FILTER for t in tokens)
        if not should_filter:
            return f"{selector} {{{props}}}"

        filtered_props = props
        for pattern, replacement in _PROP_PATTERNS_TO_STRIP:
            filtered_props = _re.sub(pattern, replacement, filtered_props, flags=_re.IGNORECASE)
        return f"{selector} {{{filtered_props}}}"

    # Supprimer les commentaires CSS pour simplifier le parsing
    css_no_comments = _re.sub(r'/\*.*?\*/', '', model_css, flags=_re.DOTALL)

    # Normaliser les classes de night mode vers le standard moderne .night-mode
    css_no_comments = _re.sub(r'\.nightMode\b', '.night-mode', css_no_comments)
    css_no_comments = _re.sub(r'\.night_mode\b', '.night-mode', css_no_comments)

    # Réécriture des images CSS relatives pour passer par le serveur local d'Anki (seulement si pas déjà réécrit)
    css_no_comments = _re.sub(r"url\(\s*(['\"]?)(?![a-z]+:)(?!/)([^'\")\s]+)\1\s*\)", r'url("/\2")', css_no_comments, flags=_re.IGNORECASE)

    # Parser CSS simplifié : découpe en blocs selector { props }
    # Gère les blocs imbriqués (@media, @keyframes) en les conservant intégralement
    result_parts = []
    
    # Itérer sur les blocs de règles
    for m in _re.finditer(r'([^{}@]+)\{([^{}]*)\}', css_no_comments):
        selector = m.group(1)
        props = m.group(2)
        result_parts.append(_filter_block(selector, props))

    # Force left alignment on all verso/back fields of EDN card styles for preview popup/sidebar
    verso_override = """
/* Force left alignment on all verso/back fields for preview */
.answer, .answer *, 
.description, .description *, 
.mnemonics, .mnemonics *, 
.errors, .errors *, 
.infoSupplementaires, .infoSupplementaires *, 
.cartesLiees, .cartesLiees *, 
.sources, .sources *, 
.comments, .comments *,
.preview-verso, .preview-verso * {
    text-align: left !important;
}
"""

    # Si le parsing simplifié produit un résultat vide, fallback : retourner le CSS nettoyé des commentaires et réécrit
    if not result_parts:
        return css_no_comments + verso_override

    return '\n'.join(result_parts) + verso_override


def _scope_css_for_preview(css_text: str, scope_selector: str) -> str:
    """Scope toutes les règles CSS simples avec le sélecteur donné (ex: #edn-preview-box)
    et convertit les URLs relatives en chemins absolus du serveur local d'Anki (/_media/).
    """
    import re
    # Supprimer les commentaires CSS
    css_no_comments = re.sub(r'/\*.*?\*/', '', css_text, flags=re.DOTALL)
    
    # Normaliser les classes de night mode vers le standard moderne .night-mode
    css_no_comments = re.sub(r'\.nightMode\b', '.night-mode', css_no_comments)
    css_no_comments = re.sub(r'\.night_mode\b', '.night-mode', css_no_comments)
    
    # Réécriture des images CSS relatives pour passer par le serveur local (seulement si pas déjà réécrit)
    css_no_comments = re.sub(r"url\(\s*(['\"]?)(?![a-z]+:)(?!/)([^'\")\s]+)\1\s*\)", r'url("/\2")', css_no_comments, flags=re.IGNORECASE)
    
    scoped_rules = []
    # Matcher les règles simples selector { properties }
    for m in re.finditer(r'([^{}@]+)\{([^{}]+)\}', css_no_comments):
        selectors = m.group(1).strip()
        properties = m.group(2).strip()
        
        scoped_sel_list = []
        for sel in selectors.split(','):
            sel = sel.strip()
            if not sel:
                continue
            if sel in ['html', 'body']:
                continue
            if scope_selector in sel:
                scoped_sel_list.append(sel)
            else:
                # Si le sélecteur contient des classes de thème global, insérer le scope après elles
                inserted = False
                for global_class in ['.night-mode', '.nightMode', '.night_mode']:
                    if global_class in sel:
                        scoped_sel_list.append(sel.replace(global_class, f"{global_class} {scope_selector}"))
                        inserted = True
                        break
                if not inserted:
                    scoped_sel_list.append(f"{scope_selector} {sel}")
        
        if scoped_sel_list:
            scoped_selectors = ", ".join(scoped_sel_list)
            scoped_rules.append(f"{scoped_selectors} {{{properties}}}")
        
    return "\n".join(scoped_rules)


def clean_cloze(text):
    if not text:
        return ""
    import re
    return re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', text)

def detect_smart_label(source_note, target_note):
    if not source_note or not target_note:
        return None
        
    def get_raw_text(n):
        if not n or not n.fields:
            return ""
        raw = n.fields[0]
        if not raw:
            return ""
        import re
        clean = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', raw)
        clean = re.sub(r'<[^>]+>', ' ', clean).replace('&nbsp;', ' ')
        clean = re.sub(r'\s+', ' ', clean).strip()
        import html
        return html.unescape(clean).lower()
        
    t_source = get_raw_text(source_note)
    t_target = get_raw_text(target_note)
    
    if not t_source or not t_target:
        return None
        
    def get_pathology_key(title):
        title = title.replace("syndrome du", "").replace("syndrome de la", "").replace("syndrome de l'", "").replace("syndrome de", "").replace("syndrome d'", "")
        title = title.replace("sémiologie du", "").replace("sémiologie de la", "").replace("sémiologie de l'", "").replace("sémiologie de", "").replace("sémiologie d'", "")
        title = title.replace("diagnostic du", "").replace("diagnostic de la", "").replace("diagnostic de l'", "").replace("diagnostic de", "").replace("diagnostic d'", "")
        title = title.replace("traitement du", "").replace("traitement de la", "").replace("traitement de l'", "").replace("traitement de", "").replace("traitement d'", "")
        title = title.replace("évolutions et complications du", "").replace("complications du", "").replace("complications de", "")
        return title.strip()
        
    key_target = get_pathology_key(t_target)
    key_source = get_pathology_key(t_source)
    
    is_target_syndrome = False
    try:
        is_target_syndrome = "syndrome" in target_note.note_type()['name'].lower()
    except:
        pass
        
    is_source_syndrome = False
    try:
        is_source_syndrome = "syndrome" in source_note.note_type()['name'].lower()
    except:
        pass

    is_parent = False
    if is_target_syndrome and not is_source_syndrome:
        if key_target in t_source:
            is_parent = True
    elif key_target in t_source and len(key_target) > 5 and len(t_target) < len(t_source):
        is_parent = True
        
    if is_parent:
        return "Main"
        
    is_child = False
    if is_source_syndrome and not is_target_syndrome:
        if key_source in t_target or any(key_source in f.lower() for f in target_note.fields):
            is_child = True
    elif key_source in t_target and len(key_source) > 5 and len(t_source) < len(t_target):
        is_child = True
        
    if is_child:
        if any(w in t_target for w in ["traitement", "chirurgie", "thérapeutique", "médical", "infiltration", "corticoïde", "prise en charge"]):
            return "Traitements"
        if any(w in t_target for w in ["enmg", "irm", "scanner", "radio", "biologie", "examen", "complémentaire", "diagnostic", "imagerie", "échographie", "radiographie", "ponction"]):
            return "Examens complémentaires"
        is_semiology = False
        try:
            is_semiology = "sémiologie" in target_note.note_type()['name'].lower()
        except:
            pass
        if is_semiology or any(w in t_target for w in ["signe", "symptôme", "sémiologie", "clinique", "physiopathologie", "déficit", "douleur", "paresthésie"]):
            return "Signes"
            
    return None

def strip_html(text):
    import html as _html
    text = clean_cloze(text)
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = clean.replace('&nbsp;', ' ')
    clean = re.sub(r'\s+', ' ', clean)
    clean = clean.strip()
    # Décoder les entités HTML résiduelles (&amp; → &, &lt; → <, etc.)
    return _html.unescape(clean)

def get_note_recto(note, limit=None, source_note=None):
    if not note or not note.fields:
        return "[Vide]"

    is_cloze = False
    try:
        nt_name = note.note_type()['name'].lower()
        is_cloze = note.note_type()['type'] == 1 or "cloze" in nt_name or "trou" in nt_name
    except:
        pass
    if not is_cloze:
        for field in note.fields:
            if re.search(r'\{\{c\d+::', field, re.IGNORECASE):
                is_cloze = True
                break

    if is_cloze:
        # Pour les cartes à trous, on affiche le texte entier du premier champ avec cloze (le cloze entier), sans les info supp
        for field in note.fields:
            if re.search(r'\{\{c\d+::', field, re.IGNORECASE):
                clean = strip_html(field)
                if clean:
                    return clean[:limit] if limit else clean
        # Fallback : premier champ uniquement (pas d'info supp)
        if note.fields:
            clean = strip_html(note.fields[0])
            if clean:
                return clean[:limit] if limit else clean
        return "[Vide]"

    for field in note.fields:
        clean = strip_html(field)
        if clean:
            return clean[:limit] if limit else clean
    return "[Vide]"


def generate_smart_link_label(source_note, target_note):
    """Génère une description raccourcie intelligente pour l'insertion de lien dans une note.
    
    Exemples concrets du deck EDN :
    - Source: 'Définition de l'exacerbation de l'asthme', Target: 'Traitement de l'exacerbation de l'asthme' -> 'Traitement'
    - Source: 'Traitement de l'exacerbation de l'asthme', Target: 'Définition de l'exacerbation de l'asthme' -> 'Def'
    - Source: 'Syndrome néphrotique chez l'adulte', Target: 'Syndrome néphrotique chez l'enfant' -> 'Enfant'
    - Source: 'Signes de l'hyperkaliémie', Target: 'Aspect ECG de l'hyperkaliémie' -> 'ECG'
    - Target est le syndrome parent / carte mère -> 'Main' ou 'Def'
    """
    if not target_note:
        return ""
    
    target_recto = get_note_recto(target_note, 240)
    if not source_note:
        return target_recto
        
    source_recto = get_note_recto(source_note, 240)
    if not source_recto or not target_recto:
        return target_recto
        
    import unicodedata
    import html as _html
    def _norm(s):
        if not s: return ""
        s = _html.unescape(s)
        s = re.sub(r'<[^>]+>', ' ', s)
        s = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', s)
        s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
        s = s.lower().strip()
        return re.sub(r'\s+', ' ', s)

    s_norm = _norm(source_recto)
    t_norm = _norm(target_recto)

    aspect_patterns = [
        # Aspects ultra-spécifiques en premier
        (r'\b(forme pure|pur)\b', 'Pur'),
        (r'\b(forme impure|impur)\b', 'Impur'),
        (r'\b(tremblement)\b', 'Tremblement'),
        (r'\b(akinesie)\b', 'Akinésie'),
        (r'\b(rigidite)\b', 'Rigidité'),
        (r'\b(enfant|pediatrie|pediatrique)\b', 'Enfant'),
        (r'\b(adulte)\b', 'Adulte'),
        (r'\b(ecg|electrocardiogramme)\b', 'ECG'),
        (r'\b(enmg|electromyogramme)\b', 'ENMG'),
        (r'\b(irm|scanner|tdm|radiographie|radio|echographie|imagerie)\b', 'Imagerie'),
        (r'\b(biologie|bilan biologique|bilan|examens? complementaires?)\b', 'Examens complémentaires'),
        (r'\b(facteurs? de risque|fdr|fdrcv)\b', 'FdR'),
        (r'\b(mecanisme|physiopathologie|physiopath)\b', 'Physiopathologie'),
        (r'\b(prevention|vaccination|vaccin)\b', 'Prévention'),
        (r'\b(surveillance|suivi)\b', 'Surveillance'),
        (r'\b(posologie|dose|modalites)\b', 'Posologie'),
        (r'\b(contre[- ]indications?|ci)\b', 'Contre-indications'),
        (r'\b(indications?)\b', 'Indications'),
        (r'\b(complications?|evolution|pronostic|gravite)\b', 'Complications'),
        (r'\b(causes?|etiologies?|facteurs? favorisants?)\b', 'Causes'),
        (r'\b(signes? cliniques?|signes?|clinique|symptomes?|semiologie|manifestations?)\b', 'Signes'),
        (r'\b(diagnostic|diag|criteres diagnostiques|criteres)\b', 'Diagnostic'),
        (r'\b(traitement|traitements|pec|prise en charge|therapeutique|chirurgie|infiltration|chirurgical)\b', 'Traitement'),
        # Définition / Def en dernier des patterns
        (r'\b(definition|def|qu\'est-ce que|qu\'est ce que)\b', 'Def'),
    ]

    try:
        is_target_syndrome = "syndrome" in target_note.note_type()['name'].lower()
        is_source_syndrome = "syndrome" in source_note.note_type()['name'].lower()
        if is_target_syndrome and not is_source_syndrome:
            return "Main"
    except Exception:
        pass

    for pat, label in aspect_patterns:
        m_target = re.search(pat, t_norm)
        m_source = re.search(pat, s_norm)
        if m_target and (not m_source or m_source.group(0) != m_target.group(0)):
            return label

    if len(target_recto.split()) <= 3:
        return target_recto

    return target_recto

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
                recto_escaped = recto.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                html_parts.append(
                    f'<span class="edn-desc">{recto_escaped}</span>&nbsp;—&nbsp;'
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
        if mirror_requested:
            source_note = self.editor.note
            if source_note and source_note.id:
                _proposal_queue.add(lambda done: _propose_mirror_links_group(
                    self.editor, source_note, items, done
                ))
                if len(items) > 1:
                    target_nids = [int(nid) for nid, _ in items]
                    _proposal_queue.add(lambda done: _propose_cross_links(
                        self.editor, source_note, target_nids, done
                    ))

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


def _find_target_field_idx(note, ref_note=None, ref_field_idx=-1):
    target_fields = note.note_type()['flds']
    fidx = -1
    if ref_note and ref_field_idx >= 0 and ref_field_idx < len(ref_note.note_type()['flds']):
        ref_field_name = ref_note.note_type()['flds'][ref_field_idx]['name']
        for i, fld in enumerate(target_fields):
            if fld['name'] == ref_field_name:
                fidx = i
                break
    if fidx < 0:
        for i, fld in enumerate(target_fields):
            if fld['name'] == 'Cartes liées':
                fidx = i
                break
    if fidx < 0:
        fidx = len(note.fields) - 1 if note.fields else 0
    if fidx >= len(note.fields):
        fidx = len(note.fields) - 1 if note.fields else 0
    return fidx


class ProposalQueue:
    def __init__(self):
        self.queue = []
        self.running = False
        
    def add(self, task_fn):
        """task_fn is a function that takes a callback 'done' and calls done() when finished."""
        self.queue.append(task_fn)
        self.trigger_next()
        
    def trigger_next(self):
        if self.running or not self.queue:
            return
        self.running = True
        task_fn = self.queue.pop(0)
        
        def on_done():
            self.running = False
            self.trigger_next()
            
        QTimer.singleShot(0, lambda: self._run_task(task_fn, on_done))
        
    def _run_task(self, task_fn, on_done):
        try:
            task_fn(on_done)
        except Exception as e:
            log(f"Error running queue task: {e}")
            on_done()

_proposal_queue = ProposalQueue()


def _add_badge_to_note(target_nid, fidx, source_nid, source_recto):
    try:
        target_note = mw.col.get_note(int(target_nid))
    except Exception as e:
        log(f"Error getting note {target_nid} for badge addition: {e}")
        return
    if not target_note.fields or fidx < 0 or fidx >= len(target_note.fields):
        return
    source_recto_badge = strip_html(source_recto)[:240] if source_recto else "[Vide]"
    source_recto_badge = source_recto_badge or "[Vide]"
    source_recto_escaped = source_recto_badge.replace('"', '&quot;').replace("'", '&#39;')
    
    badge_html = (
        f'<br><span class="edn-desc">{source_recto_escaped}</span>&nbsp;\u2014&nbsp;'
        f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{source_nid}"'
        f'><span class="edn-nid">{source_nid}</span></kbd>'
    )
    
    current_field_content = target_note.fields[fidx].strip()
    if not current_field_content or current_field_content == '<br>':
        badge_html = badge_html.replace('<br>', '', 1)
        
    target_note.fields[fidx] += badge_html
    mw.col.update_note(target_note)


def _propose_mirror_links_group(editor, source_note, items, on_done):
    """Propose de lier la carte source dans les cartes cibles de manière groupée."""
    try:
        if not source_note or not source_note.id:
            on_done()
            return
            
        valid_targets = []
        source_nid = str(source_note.id)
        
        # Retrieve all target notes and default field indices
        for target_nid_str, _ in items:
            try:
                target_nid = int(target_nid_str)
                target_note = mw.col.get_note(target_nid)
                
                # Check if it already has a link to source_nid
                if any(source_nid in field for field in target_note.fields):
                    continue
                
                # Determine target field
                fidx_target = _find_target_field_idx(target_note, source_note)
                valid_targets.append((target_nid_str, target_note, fidx_target))
            except Exception:
                pass
                
        if not valid_targets:
            on_done()
            return
            
        parent = editor.widget if editor.widget else mw
        dialog = MirrorSelectionDialog(parent, valid_targets, source_note=source_note)
        result = dialog.exec()
        
        accepted_code = getattr(QDialog.DialogCode, "Accepted", None) or getattr(QDialog, "Accepted", 1)
        if result == accepted_code:
            selected_targets = dialog.get_selected_targets()
            if selected_targets:
                for nid_str, target_note, fidx, custom_recto in selected_targets:
                    # Update description in editor (JavaScript)
                    safe_desc = custom_recto.replace('`', '\\`').replace('$', '\\$')
                    js = f"""
                    (function() {{
                        let nid = "{nid_str}";
                        let newDesc = `{safe_desc}`;
                        let kbds = [];
                        let allKbds = Array.from(document.querySelectorAll('kbd.clickable_cards[data-nid="' + nid + '"]'));
                        document.querySelectorAll('anki-editable').forEach(edn => {{
                            let root = edn.shadowRoot || edn;
                            root.querySelectorAll('kbd.clickable_cards[data-nid="' + nid + '"]').forEach(k => {{
                                if (!kbds.includes(k)) kbds.push(k);
                            }});
                        }});
                        allKbds.forEach(k => {{
                            if (!kbds.includes(k)) kbds.push(k);
                        }});
                        
                        kbds.forEach(kbd => {{
                            let foundSpan = null;
                            let curr = kbd.previousSibling;
                            for (let i = 0; i < 4 && curr; i++) {{
                                if (curr.nodeType === Node.ELEMENT_NODE && curr.classList && curr.classList.contains('edn-desc')) {{
                                    foundSpan = curr;
                                    break;
                                }}
                                curr = curr.previousSibling;
                            }}
                            if (foundSpan) {{
                                foundSpan.textContent = newDesc;
                            }} else {{
                                let currNode = kbd.previousSibling;
                                for (let i = 0; i < 3 && currNode; i++) {{
                                    if (currNode.nodeType === Node.TEXT_NODE) {{
                                        let val = currNode.nodeValue;
                                        let match = val.match(/^(.*?)([\\s\\xa0\\u00a0]*[\\u2014\\u2013-][\\s\\xa0\\u00a0]*)$/);
                                        if (match) {{
                                            currNode.nodeValue = newDesc + match[2];
                                            break;
                                        }} else {{
                                            let lastDashIdx = -1;
                                            for (let char of ['\\u2014', '\\u2013', '-']) {{
                                                let idx = val.lastIndexOf(char);
                                                if (idx > lastDashIdx) {{
                                                    lastDashIdx = idx;
                                                }}
                                            }}
                                            if (lastDashIdx >= 0) {{
                                                currNode.nodeValue = newDesc + val.substring(lastDashIdx);
                                                break;
                                            }}
                                        }}
                                    }}
                                    currNode = currNode.previousSibling;
                                }}
                            }}
                        }});
                        
                        let active = document.activeElement;
                        if (active) {{
                            let dispatchTarget = active.closest ? active.closest('anki-editable') : active;
                            if (dispatchTarget) {{
                                dispatchTarget.dispatchEvent(new Event("input", {{ bubbles: true, composed: true }}));
                            }}
                        }}
                    }})();
                    """
                    editor.web.eval(js)
                    
                    # Update description in DB for the source note (if it has already been saved)
                    update_link_description_in_note(source_note, nid_str, custom_recto)
                    
                    # Add mirror badge to the target note
                    # Utiliser le label validé dans la dialog (custom_recto) pour le badge miroir dans la cible
                    source_nid = str(source_note.id)
                    _add_badge_to_note(int(nid_str), fidx, source_nid, custom_recto)
                    
                mw.col.update_note(source_note)
                tooltip(f"Liens miroirs créés dans {len(selected_targets)} cartes.")
                
                # Refresh assistant side panel
                from .editor_assistant import update_assistant
                if editor:
                    try:
                        editor.saveNow(lambda: update_assistant(editor))
                    except Exception as se:
                        log(f"Error saving editor/updating assistant: {se}")
                        update_assistant(editor)
    except Exception as e:
        log(f"Error in _propose_mirror_links_group: {e}")
    finally:
        on_done()


def _propose_mirror_link(editor, source_note, target_nid_str, on_done):
    """Propose de lier la carte source dans la carte cible (lien miroir)."""
    _propose_mirror_links_group(editor, source_note, [(target_nid_str, None)], on_done)


class CrossLinksSelectionDialog(QDialog):
    def __init__(self, parent, links):
        """
        links: list of tuples (note1, note2, fidx1, fidx2, req1, req2)
        """
        super().__init__(parent)
        self.setWindowTitle("Liaison entre les cartes cibles")
        self.resize(500, 350)
        self.setMinimumWidth(450)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Info label
        self.info_label = QLabel(
            "Lier également les autres cartes entre elles ?"
        )
        self.info_label.setWordWrap(True)
        font = self.info_label.font()
        font.setPointSize(font.pointSize() + 1)
        self.info_label.setFont(font)
        layout.addWidget(self.info_label)
        
        # Header label
        self.header_label = QLabel("Liens à créer :")
        self.header_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.header_label)
        
        # Scroll Area for checkboxes
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        
        shape_styled = getattr(QFrame.Shape, "StyledPanel", None) or getattr(QFrame, "StyledPanel", None)
        if shape_styled is not None:
            self.scroll.setFrameShape(shape_styled)
            
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(8)
        
        align_top = getattr(Qt.AlignmentFlag, "AlignTop", None) or getattr(Qt, "AlignTop", None)
        if align_top:
            self.scroll_layout.setAlignment(align_top)
            
        self.checkboxes = []
        for item in links:
            note1, note2, _, _, _, _ = item
            
            recto1 = get_note_recto(note1, 60)
            recto2 = get_note_recto(note2, 60)
            
            cb = QCheckBox(f"{recto1}  <—>  {recto2}")
            cb.setChecked(True)
            cb.setToolTip(f"NID {note1.id} <—> NID {note2.id}")
            self.scroll_layout.addWidget(cb)
            self.checkboxes.append((cb, item))
            
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll)
        
        # Helper layout for checking/unchecking all
        self.helper_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("Tout cocher")
        self.btn_deselect_all = QPushButton("Tout décocher")
        self.btn_select_all.clicked.connect(self.select_all)
        self.btn_deselect_all.clicked.connect(self.deselect_all)
        self.helper_layout.addWidget(self.btn_select_all)
        self.helper_layout.addWidget(self.btn_deselect_all)
        self.helper_layout.addStretch()
        layout.addLayout(self.helper_layout)
        
        # Spacer
        layout.addSpacing(5)
        
        # Standard Buttons
        std_ok = getattr(QDialogButtonBox.StandardButton, "Ok", None) or getattr(QDialogButtonBox, "Ok", None)
        std_cancel = getattr(QDialogButtonBox.StandardButton, "Cancel", None) or getattr(QDialogButtonBox, "Cancel", None)
        
        self.button_box = QDialogButtonBox(std_ok | std_cancel, self)
        ok_btn = self.button_box.button(std_ok)
        cancel_btn = self.button_box.button(std_cancel)
        
        if ok_btn:
            ok_btn.setText("OK")
            ok_btn.setDefault(True)
            ok_btn.setAutoDefault(True)
            ok_btn.clicked.connect(self.accept)
        if cancel_btn:
            cancel_btn.setText("Annuler")
            cancel_btn.clicked.connect(self.reject)
            
        layout.addWidget(self.button_box)
        
    def select_all(self):
        for cb, _ in self.checkboxes:
            cb.setChecked(True)
            
    def deselect_all(self):
        for cb, _ in self.checkboxes:
            cb.setChecked(False)
            
    def get_selected_links(self):
        return [item for cb, item in self.checkboxes if cb.isChecked()]


def _propose_cross_links(editor, source_note, target_nids, on_done):
    """Propose de lier les cartes cibles entre elles si l'utilisateur en a lié plusieurs."""
    try:
        # 1. Récupérer les notes correspondantes
        target_notes = []
        for tnid in target_nids:
            try:
                target_notes.append(mw.col.get_note(tnid))
            except:
                pass
        
        if len(target_notes) < 2:
            on_done()
            return

        # 2. Déterminer l'index du champ actif
        def _got_field_idx(idx_str):
            try:
                field_idx = int(idx_str) if idx_str and idx_str != 'null' else -1
            except:
                field_idx = -1
            try:
                _do_cross_links(field_idx)
            except Exception as e:
                log(f"Error in _do_cross_links: {e}")
                on_done()

        def _do_cross_links(fidx):
            try:
                # Re-load fresh target notes from database
                fresh_target_notes = []
                for note in target_notes:
                    try:
                        fresh_target_notes.append(mw.col.get_note(note.id))
                    except:
                        fresh_target_notes.append(note)
                
                # Construire la liste des liaisons à proposer (celles qui n'existent pas encore)
                links_to_create = [] # Liste de tuples (note1, note2, fidx1, fidx2, req1, req2)
                
                for i in range(len(fresh_target_notes)):
                    for j in range(i + 1, len(fresh_target_notes)):
                        note1 = fresh_target_notes[i]
                        note2 = fresh_target_notes[j]
                        
                        nid1_str = str(note1.id)
                        nid2_str = str(note2.id)
                        
                        # Vérifier si note1 est déjà liée à note2
                        linked_1_to_2 = any(nid2_str in field for field in note1.fields)
                        # Vérifier si note2 est déjà liée à note1
                        linked_2_to_1 = any(nid1_str in field for field in note2.fields)
                        
                        if not linked_1_to_2 or not linked_2_to_1:
                            fidx1 = _find_target_field_idx(note1, source_note, fidx)
                            fidx2 = _find_target_field_idx(note2, source_note, fidx)
                            links_to_create.append((note1, note2, fidx1, fidx2, not linked_1_to_2, not linked_2_to_1))
                
                if not links_to_create:
                    return
                
                # Show custom checklist dialog
                accepted_code = getattr(QDialog.DialogCode, "Accepted", None) or getattr(QDialog, "Accepted", 1)
                dialog = CrossLinksSelectionDialog(editor.widget if editor.widget else mw, links_to_create)
                result = dialog.exec()
                if result == accepted_code:
                    selected_links = dialog.get_selected_links()
                    for note1, note2, fidx1, fidx2, req1, req2 in selected_links:
                        # note1 -> note2
                        if req1:
                            try:
                                note2_fresh = mw.col.get_note(note2.id)
                                note1_fresh = mw.col.get_note(note1.id)
                                smart_desc = generate_smart_link_label(note1_fresh, note2_fresh)
                                r2_clean = smart_desc if smart_desc else get_note_recto(note2_fresh, 240)
                            except:
                                r2_clean = get_note_recto(note2, 240)
                            _add_badge_to_note(note1.id, fidx1, str(note2.id), r2_clean)
                        # note2 -> note1
                        if req2:
                            try:
                                note1_fresh = mw.col.get_note(note1.id)
                                note2_fresh = mw.col.get_note(note2.id)
                                smart_desc = generate_smart_link_label(note2_fresh, note1_fresh)
                                r1_clean = smart_desc if smart_desc else get_note_recto(note1_fresh, 240)
                            except:
                                r1_clean = get_note_recto(note1, 240)
                            _add_badge_to_note(note2.id, fidx2, str(note1.id), r1_clean)
                    
                    tooltip("Liaisons créées entre les autres cartes.")
                    
                    # Refresh assistant side panel
                    from .editor_assistant import update_assistant
                    if editor:
                        try:
                            editor.saveNow(lambda: update_assistant(editor))
                        except Exception as se:
                            log(f"Error saving editor/updating assistant: {se}")
                            update_assistant(editor)
            finally:
                on_done()
                        
        try:
            editor.web.evalWithCallback(
                "(typeof window._ednSavedFieldIndex !== 'undefined') ? window._ednSavedFieldIndex : -1;",
                _got_field_idx
            )
        except Exception:
            _do_cross_links(-1)
            
    except Exception as e:
        log(f"Cross links error: {e}")
        on_done()


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
        self.setMinimumSize(350, 500)
        self.setModal(False)
        self._preview_stack = []
        self._context_menu_active = False
        self._search_seq = 0
        self.col = mw.col
        
        # Debounce timer pour la recherche (évite de bloquer le main thread en cours de saisie)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_search_timeout)
        
        self._suggestions_cache = None
        self._all_target_nids = []
        self._search_offset = 0
        self._current_results_type = None
        self._current_query = ""
        self._search_type_label = "résultats"
        
        self.setup_ui()

    def _on_search_text_changed(self, text):
        if better_search_on_text_change is not None:
            try:
                pos = self.search_bar.cursorPosition()
                newtext, newpos, triggersearch = better_search_on_text_change(
                    parent=self,
                    move_dialog_in_browser=False,
                    include_filtered_in_deck=True,
                    input_text=text,
                    cursorpos=pos,
                    test_input=None,
                )
                if newtext is not None:
                    self.search_bar.blockSignals(True)
                    self.search_bar.setText(newtext)
                    if newpos:
                        self.search_bar.setCursorPosition(newpos)
                    self.search_bar.blockSignals(False)
                    
                    if triggersearch:
                        self._search_timer.stop()
                        self.do_search(newtext)
                    else:
                        self._search_timer.start(250)
                    return
            except Exception as e:
                print(f"Error in BetterSearch integration: {e}")

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
        is_night = _is_night_mode()
        if self.selected_text:
            hint = QLabel(f"🔗 Mode hyperlien — Les mots <b>'{self.selected_text}'</b> deviendront cliquables vers la carte choisie.")
            hint.setWordWrap(True)
            if is_night:
                hint.setStyleSheet("background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.3); border-radius:4px; padding:4px 8px; color:#38bdf8; margin-bottom:4px;")
            else:
                hint.setStyleSheet("background:#e8f4ff; border: 1px solid #bae6fd; border-radius:4px; padding:4px 8px; color:#0369a1; margin-bottom:4px;")
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
        self.mirror_cb.setChecked(config.get("gui_mirror_cb_state", True))
        
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
        self.results_table.itemDoubleClicked.connect(self.on_double_click)
        self.results_table.itemSelectionChanged.connect(self.on_selection_changed)
        self.results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(self.on_table_context_menu)
        self.results_table.clicked.connect(lambda index: self.hide_preview_popup())
        layout.addWidget(self.results_table)
        
        # Bouton Charger plus pour la pagination
        self.load_more_btn = QPushButton("Charger plus")
        if is_night:
            self.load_more_btn.setStyleSheet("""
                QPushButton {
                    background-color: #262936;
                    border: 1px solid #475569;
                    border-radius: 4px;
                    padding: 6px;
                    color: #e2e8f0;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #334155;
                    border-color: #64748b;
                }
                QPushButton:pressed {
                    background-color: #1e293b;
                }
                QPushButton:disabled {
                    background-color: #1a1d27;
                    color: #64748b;
                    border-color: #334155;
                }
            """)
        else:
            self.load_more_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f1f5f9;
                    border: 1px solid #cbd5e1;
                    border-radius: 4px;
                    padding: 6px;
                    color: #334155;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #e2e8f0;
                }
                QPushButton:pressed {
                    background-color: #cbd5e1;
                }
                QPushButton:disabled {
                    background-color: #f8fafc;
                    color: #94a3b8;
                    border-color: #e2e8f0;
                }
            """)
        self.load_more_btn.clicked.connect(self.load_next_page)
        self.load_more_btn.setVisible(False)
        layout.addWidget(self.load_more_btn)
        
        self.results_table.setColumnCount(3)
        self.results_table.setHorizontalHeaderLabels(["Recto", "NID", "Actions"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results_table.setColumnWidth(2, 80)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        
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
        
        self.results_table.installEventFilter(self)
        if hasattr(self.results_table, 'viewport') and self.results_table.viewport():
            self.results_table.viewport().installEventFilter(self)
            
        # Connecter la barre de défilement verticale
        self.results_table.verticalScrollBar().valueChanged.connect(self._on_table_scroll)
        
        # Pré-créer le popup preview (caché) pour éviter le flash visuel à la première ouverture
        # (la création d'un QWidget enfant lors du premier hover forçait Qt à repeindre le parent)
        try:
            self._init_preview_dlg()
        except Exception:
            pass

        # Charger les suggestions initiales ou lancer la recherche si texte sélectionné
        if self.selected_text:
            self.search_bar.setText(self.selected_text)
        else:
            self.do_search("")
        
    def closeEvent(self, event):
        self._cleanup_on_close()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_on_close()
        super().reject()

    def _should_keep_preview_alive(self):
        """Vérifie si la preview popup doit rester ouverte malgré un événement de focus/activation."""
        if getattr(self, '_context_menu_active', False):
            return True
        if getattr(self, '_is_showing_preview', False):
            return True
        try:
            from aqt.qt import Qt, QApplication, QCursor
            no_btn = getattr(Qt.MouseButton, "NoButton", None) or getattr(Qt, "NoButton", None)
            if QApplication.mouseButtons() != no_btn:
                return True
            cursor_pos = QCursor.pos()
            if self.geometry().contains(cursor_pos):
                return True
            preview_dlg = getattr(self, '_preview_dlg', None)
            if preview_dlg is not None:
                try:
                    preview_dlg.objectName()
                    if preview_dlg.isVisible() or preview_dlg.geometry().contains(cursor_pos):
                        return True
                except (RuntimeError, Exception):
                    pass
        except Exception:
            pass
        return False

    def changeEvent(self, event):
        try:
            if event.type() in [QEvent.Type.ActivationChange, QEvent.ActivationChange]:
                if not self.isActiveWindow():
                    if not self._should_keep_preview_alive():
                        self.hide_preview_popup()
                else:
                    # Ne pas re-déclencher show_preview_popup si la preview est déjà visible
                    preview_dlg = getattr(self, '_preview_dlg', None)
                    if not (preview_dlg is not None and preview_dlg.isVisible()):
                        try:
                            pos = QCursor.pos()
                            for row in range(self.results_table.rowCount()):
                                btn = self.results_table.cellWidget(row, 2)
                                if btn and hasattr(btn, 'nid'):
                                    btn_global = btn.mapToGlobal(QPoint(0, 0))
                                    btn_rect = QRect(btn_global, btn.size())
                                    if btn_rect.contains(pos):
                                        self._preview_current_widget = btn
                                        self.show_preview_popup(btn.nid, position_widget=btn)
                                        break
                        except Exception:
                            pass
        except Exception:
            pass
        super().changeEvent(event)

    def event(self, event):
        try:
            etype = event.type()
            deactivate = getattr(QEvent.Type, "WindowDeactivate", None) or getattr(QEvent, "WindowDeactivate", None)
            hide = getattr(QEvent.Type, "Hide", None) or getattr(QEvent, "Hide", None)
            close = getattr(QEvent.Type, "Close", None) or getattr(QEvent, "Close", None)
            if etype in [deactivate, hide, close]:
                if etype == deactivate:
                    if self._should_keep_preview_alive():
                        return super().event(event)
                self.hide_preview_popup()
        except Exception:
            pass
        return super().event(event)

    def _cleanup_on_close(self):
        global _active_dialog, _last_gui_close_time
        _active_dialog = None
        import time
        _last_gui_close_time = time.time()
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
                            // Notifier Anki immédiatement (sans RAF) pour que saveNow Python récupère
                            // le contenu nettoyé avant la fermeture du dialog.
                            editable.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
                        } catch(e) {}
                    });
                })();
            """)
        except Exception:
            pass
        # Filet de sécurité Python : supprimer les markers directement dans les champs de la note
        # pour garantir qu'ils ne seront pas sauvegardés même si le cleanup JS est asynchrone
        # ou si l'éditeur est dans un état inattendu (ex : browser ouvert sur une autre carte).
        try:
            import re as _re
            _MARKER_RE = _re.compile(
                r'<span\s+id=["\']edn-cursor-marker["\']\s*>.*?</span>',
                _re.IGNORECASE | _re.DOTALL
            )
            note = self.editor.note if self.editor else None
            if note and note.fields:
                changed = False
                for i, field in enumerate(note.fields):
                    cleaned = _MARKER_RE.sub('', field)
                    if cleaned != field:
                        note.fields[i] = cleaned
                        changed = True
                if changed:
                    # Sauvegarder la note sans les markers via saveNow
                    try:
                        self.editor.saveNow(lambda: None)
                    except Exception:
                        pass
        except Exception:
            pass

    def append_results_to_table(self, results):
        for nid_str, recto_clean in results:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            
            i_recto = QTableWidgetItem(recto_clean)
            i_recto.setData(Qt.ItemDataRole.UserRole, {'nid': nid_str, 'recto': recto_clean})
            self.results_table.setItem(row, 0, i_recto)
            
            self.results_table.setItem(row, 1, QTableWidgetItem(nid_str))
            
            btn = self._make_voir_button(nid_str)
            self.results_table.setCellWidget(row, 2, btn)

    def load_next_page(self):
        if self._current_results_type == "suggestions":
            # load next chunk of suggestions
            start = self._search_offset
            end = start + 50
            chunk = self._all_target_nids[start:end]
            if not chunk:
                self.load_more_btn.setVisible(False)
                if start == 0:
                    self.results_label.setText("Pas de résultats")
                return
            
            self.append_results_to_table(chunk)
            self._search_offset = min(end, len(self._all_target_nids))
            
            # Show remaining count
            remaining = len(self._all_target_nids) - self._search_offset
            if remaining > 0:
                self.load_more_btn.setText(f"Charger plus ({remaining} restantes)")
                self.load_more_btn.setVisible(True)
            else:
                self.load_more_btn.setVisible(False)
                
            self.results_label.setText(f"{self.results_table.rowCount()} suggestions de liens")
            
        elif self._current_results_type == "search":
            if self._search_offset >= len(self._all_target_nids):
                self.load_more_btn.setVisible(False)
                return
                
            self.load_more_btn.setEnabled(False)
            self.load_more_btn.setText("Chargement...")
            
            # Save the current search sequence to verify on completion
            current_seq = self._search_seq
            recto_only = self.recto_only_cb.isChecked()
            query = self._current_query
            
            # We want to fetch up to 50 qualified results
            limit = 50
            start_offset = self._search_offset
            nids_to_process = self._all_target_nids
            
            def bg_fetch_rectos(col):
                # Dummy Note class to avoid col.get_note lock contention and overhead
                class DummyNote:
                    def __init__(self, id, fields, mid, col):
                        self.id = id
                        self.fields = fields
                        self.mid = mid
                        self._col = col
                        self._note_type = None

                    def note_type(self):
                        if self._note_type is None:
                            try:
                                self._note_type = self._col.models.get(self.mid)
                            except:
                                self._note_type = {"name": "", "type": 0, "flds": []}
                        return self._note_type

                results = []
                idx = start_offset
                
                # Fetch in chunks of NIDs to find up to 50 qualified ones
                while idx < len(nids_to_process) and len(results) < limit:
                    # Take a small chunk to query DB
                    chunk_nids = nids_to_process[idx:idx + 100]
                    if not chunk_nids:
                        break
                    
                    try:
                        rows = col.db.all(f"select id, flds, mid from notes where id in ({','.join(map(str, chunk_nids))})")
                        row_map = {row[0]: row for row in rows}
                        
                        # Process NIDs in order
                        for nid in chunk_nids:
                            if len(results) >= limit:
                                break
                            idx += 1  # We processed this NID
                            
                            if nid in row_map:
                                row = row_map[nid]
                                bnid, row_flds, mid = row
                                fields = row_flds.split("\x1f")
                                dummy = DummyNote(bnid, fields, mid, col)
                                recto_clean = get_note_recto(dummy, 240)

                                if recto_only and not query.startswith('tag') and ':' not in query:
                                    search_words = query.lower().split()
                                    recto_lower = recto_clean.lower()
                                    if not all(word in recto_lower for word in search_words):
                                        continue

                                results.append((str(nid), recto_clean))
                    except Exception as e:
                        print("Error in bg_fetch_rectos chunk:", e)
                        break
                        
                return results, idx
                
            def on_fetch_success(res):
                try:
                    self.objectName()
                except RuntimeError:
                    return
                if current_seq != self._search_seq:
                    return
                    
                results, next_offset = res
                self.append_results_to_table(results)
                self._search_offset = next_offset
                
                # Update button status
                self.load_more_btn.setEnabled(True)
                remaining = len(self._all_target_nids) - self._search_offset
                if remaining > 0:
                    self.load_more_btn.setText(f"Charger plus (~{remaining} restantes)")
                    self.load_more_btn.setVisible(True)
                else:
                    self.load_more_btn.setVisible(False)
                
                row_count = self.results_table.rowCount()
                if row_count == 0 and self._search_offset >= len(self._all_target_nids):
                    # Aucun résultat trouvé — essayer une recherche approximative (fuzzy)
                    self._try_fuzzy_search(query, current_seq)
                else:
                    self.results_label.setText(f"{row_count} {self._search_type_label}")
                
            from aqt.operations import QueryOp
            op = QueryOp(
                parent=self,
                op=bg_fetch_rectos,
                success=on_fetch_success
            )
            op.run_in_background()

    @perf_log
    def do_search(self, query):
        query = query.strip()
        self._current_query = query
        
        # Incrémenter le numéro de séquence de recherche pour ignorer les résultats obsolètes
        self._search_seq += 1
        current_seq = self._search_seq

        # Vider la table immédiatement et masquer le bouton charger plus
        self.results_table.setRowCount(0)
        self.load_more_btn.setVisible(False)
        
        if not query:
            config = mw.addonManager.getConfig(__name__) or {}
            if config.get("suggestions_de_liens", True):
                self._current_results_type = "suggestions"
                self.results_label.setText("Chargement des suggestions...")
                
                # Check if suggestions are cached
                if self._suggestions_cache is not None:
                    self._all_target_nids = self._suggestions_cache
                    self._search_offset = 0
                    self.load_next_page()
                    return
                
                # If not cached, compute in background
                def bg_suggestions(col):
                    from .editor_assistant import _compute_suggestions, extract_nids_from_fields, extract_nids_from_fields_list
                    try:
                        current_nid = str(self.editor.note.id) if self.editor and self.editor.note else None
                        if not current_nid:
                            return ("suggestions", [])
                        note_fields = list(self.editor.note.fields) if self.editor and self.editor.note else []
                        # Liens sortants (même logique que la sidebar)
                        outgoing_nids = extract_nids_from_fields(self.editor.note) if self.editor and self.editor.note else set()
                        # Backlinks : cartes qui pointent vers la note courante (même logique que la sidebar)
                        backlink_nids = set()
                        try:
                            candidate_ids = col.find_notes(f'"{current_nid}"')
                            if candidate_ids:
                                rows = col.db.all(
                                    f"select id, flds from notes where id in ({','.join(map(str, candidate_ids))})"
                                )
                                for row in rows:
                                    bnid, row_flds = row[0], row[1]
                                    if str(bnid) == current_nid:
                                        continue
                                    fields = row_flds.split("\x1f")
                                    if current_nid in extract_nids_from_fields_list(fields):
                                        backlink_nids.add(str(bnid))
                        except Exception as be:
                            print("bg_suggestions: erreur récupération backlinks:", be)
                        # existing_nids = sortants ∪ entrants (identique à la sidebar)
                        existing_nids = outgoing_nids | backlink_nids
                        # On veut les 50 premières suggestions à afficher, mais on peut en précalculer plus (ex: 150)
                        suggestions = _compute_suggestions(col, current_nid, note_fields, existing_nids, max_results=50)
                        formatted = [(s["nid"], s["recto"]) for s in suggestions]
                        return ("suggestions", formatted)
                    except Exception as e:
                        return ("error", str(e))
                
                def on_suggestions_success(res):
                    try:
                        self.objectName()
                    except RuntimeError:
                        return
                    if current_seq != self._search_seq:
                        return
                    
                    search_type, results_or_err = res
                    if search_type == "error":
                        self.results_label.setText(results_or_err)
                        return
                    
                    self._suggestions_cache = results_or_err
                    self._all_target_nids = self._suggestions_cache
                    self._search_offset = 0
                    self.load_next_page()
                
                from aqt.operations import QueryOp
                op = QueryOp(
                    parent=self,
                    op=bg_suggestions,
                    success=on_suggestions_success
                )
                op.run_in_background()
            else:
                self.results_label.setText("")
            return

        self._current_results_type = "search"
        self.results_label.setText("Recherche en cours...")
        
        # Contexte de la note source (Item EDN, Spécialité, Mots-clés)
        source_note = self.editor.note if self.editor and self.editor.note else None
        source_nid = str(source_note.id) if source_note else None
        
        source_item = ""
        source_specialty = ""
        source_keywords = []
        
        if source_note:
            try:
                import unicodedata
                import html as _html
                def _norm_ctx(s):
                    if not s: return ""
                    s = _html.unescape(s)
                    s = re.sub(r'<[^>]+>', ' ', s)
                    s = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', s)
                    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
                    return s.lower().strip()
                
                src_tags_str = _norm_ctx(" ".join(source_note.tags))
                m_item = re.search(r'item[-_]\d+', src_tags_str)
                if m_item:
                    source_item = m_item.group(0)
                    
                specialties_list = [
                    'cardiologie', 'pneumologie', 'neurologie', 'endocrinologie', 'gastro-enterologie',
                    'nephrologie', 'hematologie', 'infectiologie', 'dermatologie', 'rhumatologie',
                    'pediatrie', 'gynecologie', 'urologie', 'ophtalmologie', 'orl', 'psychiatrie',
                    'reanimation', 'urgences', 'orthopedie', 'chirurgie', 'oncologie', 'geriatrie', 'sante-publique'
                ]
                for sp in specialties_list:
                    if sp in src_tags_str:
                        source_specialty = sp
                        break
                        
                src_recto_raw = get_note_recto(source_note, 240)
                src_recto_norm = _norm_ctx(src_recto_raw)
                stopwords = {'de', 'du', 'des', 'le', 'la', 'les', 'un', 'une', 'en', 'dans', 'sur', 'pour', 'par', 'est', 'sont', 'avec', 'sans', 'quel', 'quels', 'quelle', 'quelles', 'type', 'types', 'definir', 'definition'}
                source_keywords = [w for w in re.findall(r'[a-z]{3,}', src_recto_norm) if w not in stopwords]
            except Exception:
                pass

        # Détection de NIDs multiples (ex: "1575893780795 1572425651952")
        # Aussi accepter un NID seul sans préfixe 'nid:'
        nid_matches = re.findall(r'\b(\d{10,})\b', query)
        
        if not nid_matches and len(query) < 2:
            self.results_label.setText("")
            return

        def bg_query(col):
            import unicodedata
            import html as _html
            def _norm_s(s):
                if not s: return ""
                s = _html.unescape(s)
                s = re.sub(r'<[^>]+>', ' ', s)
                s = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', s)
                s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
                return s.lower().strip()

            if nid_matches:
                seen_nids = set()
                target_ids = []
                for n in nid_matches:
                    found = col.find_notes(f"nid:{n}")
                    for nid in found:
                        if source_nid and str(nid) == source_nid:
                            continue
                        if nid not in seen_nids:
                            seen_nids.add(nid)
                            target_ids.append(nid)
                return ("nid", target_ids)

            # Recherche textuelle normale (ou tag)
            if query.startswith('tag::'):
                search_query = 'tag:' + query[5:]
            elif query.startswith('tag:'):
                search_query = query
            elif any(op in query for op in (':', 'OR', 'AND', '-')):
                search_query = query
            else:
                search_query = f"*{query}*"

            try:
                candidate_nids = list(col.find_notes(search_query))
                
                # Multi-mots : si peu de résultats, rechercher chaque mot
                q_words = [w for w in re.findall(r'[a-zA-Z0-9\u00C0-\u00FF]{2,}', query) if w.lower() not in ('de', 'du', 'des', 'le', 'la', 'les', 'un', 'une', 'en', 'dans', 'sur')]
                if len(candidate_nids) < 10 and len(q_words) > 1:
                    seen = set(candidate_nids)
                    for w in q_words:
                        try:
                            for nid in col.find_notes(f"*{w}*"):
                                if nid not in seen:
                                    seen.add(nid)
                                    candidate_nids.append(nid)
                        except Exception:
                            pass

                if not candidate_nids:
                    return ("text", [])

                if source_nid:
                    candidate_nids = [n for n in candidate_nids if str(n) != source_nid]

                if not candidate_nids:
                    return ("text", [])

                # Scoring contextuel
                q_norm = _norm_s(query)
                q_terms = [w for w in q_norm.split() if w]
                
                scored_candidates = []
                chunk_size = 500
                for i in range(0, len(candidate_nids), chunk_size):
                    chunk = candidate_nids[i:i + chunk_size]
                    try:
                        rows = col.db.all(
                            f"SELECT id, flds, tags FROM notes WHERE id IN ({','.join(map(str, chunk))})"
                        )
                        for row in rows:
                            cid, flds, tags = row[0], row[1], row[2]
                            fld_list = flds.split('\x1f')
                            c_recto_raw = fld_list[0] if fld_list else ""
                            c_recto_norm = _norm_s(c_recto_raw)
                            c_tags_norm = _norm_s(tags)
                            c_body_norm = _norm_s(flds)
                            
                            score = 0
                            
                            # Correspondance texte
                            if q_norm and q_norm in c_recto_norm:
                                score += 80
                            elif q_norm and q_norm in c_body_norm:
                                score += 40
                                
                            for term in q_terms:
                                if term in c_recto_norm:
                                    score += 35
                                elif term in c_tags_norm:
                                    score += 20
                                elif term in c_body_norm:
                                    score += 10
                                    
                            # Bonus contextuels EDN
                            if source_item and source_item in c_tags_norm:
                                score += 150
                            if source_specialty and source_specialty in c_tags_norm:
                                score += 60
                            for kw in source_keywords:
                                if kw in c_recto_norm:
                                    score += 35
                                elif kw in c_tags_norm:
                                    score += 20
                                elif kw in c_body_norm:
                                    score += 10
                                    
                            scored_candidates.append((score, cid))
                    except Exception as sqle:
                        print("Erreur batch scoring:", sqle)
                        for cid in chunk:
                            scored_candidates.append((0, cid))

                scored_candidates.sort(key=lambda x: x[0], reverse=True)
                sorted_ids = [cid for _, cid in scored_candidates]
                return ("text", sorted_ids)
            except Exception as e:
                return ("error", str(e))

        def on_success(res):
            try:
                self.objectName()
            except RuntimeError:
                return

            if current_seq != self._search_seq:
                return

            search_type, results_or_err = res
            if search_type == "error":
                self.results_label.setText(results_or_err)
                return

            self._all_target_nids = results_or_err
            self._search_offset = 0
            self._search_type_label = "résultats (recherche NID)" if search_type == "nid" else "résultats"
            
            if not results_or_err:
                # Aucun résultat — essayer une recherche approximative si c'est une recherche texte simple
                if search_type == "text":
                    self._try_fuzzy_search(query, current_seq)
                else:
                    self.results_label.setText("Pas de résultats")
                return

            # Load first page of search results
            self.load_next_page()

        from aqt.operations import QueryOp
        op = QueryOp(
            parent=self,
            op=bg_query,
            success=on_success
        )
        op.run_in_background()

    def _try_fuzzy_search(self, original_query, seq):
        """Recherche approximative (fuzzy) quand la recherche exacte donne 0 résultats.
        
        Stratégie :
        1. Décomposer la requête en mots.
        2. Pour chaque mot, générer des variantes Anki (*mot*, wildcard par lettre).
        3. Si des résultats sont trouvés, les afficher avec un message spécial.
        4. Sinon, afficher 'Pas de résultats'.
        """
        source_note = self.editor.note if self.editor and self.editor.note else None
        source_nid = str(source_note.id) if source_note else None
        
        source_item = ""
        source_specialty = ""
        source_keywords = []
        if source_note:
            try:
                import unicodedata
                import html as _html
                def _norm_f_ctx(s):
                    if not s: return ""
                    s = _html.unescape(s)
                    s = re.sub(r'<[^>]+>', ' ', s)
                    s = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', s)
                    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
                    return s.lower().strip()
                
                src_tags_str = _norm_f_ctx(" ".join(source_note.tags))
                m_item = re.search(r'item[-_]\d+', src_tags_str)
                if m_item:
                    source_item = m_item.group(0)
                specialties_list = [
                    'cardiologie', 'pneumologie', 'neurologie', 'endocrinologie', 'gastro-enterologie',
                    'nephrologie', 'hematologie', 'infectiologie', 'dermatologie', 'rhumatologie',
                    'pediatrie', 'gynecologie', 'urologie', 'ophtalmologie', 'orl', 'psychiatrie',
                    'reanimation', 'urgences', 'orthopedie', 'chirurgie', 'oncologie', 'geriatrie', 'sante-publique'
                ]
                for sp in specialties_list:
                    if sp in src_tags_str:
                        source_specialty = sp
                        break
                src_recto_raw = get_note_recto(source_note, 240)
                src_recto_norm = _norm_f_ctx(src_recto_raw)
                stopwords = {'de', 'du', 'des', 'le', 'la', 'les', 'un', 'une', 'en', 'dans', 'sur', 'pour', 'par', 'est', 'sont', 'avec', 'sans'}
                source_keywords = [w for w in re.findall(r'[a-z]{3,}', src_recto_norm) if w not in stopwords]
            except Exception:
                pass

        # Éviter la recherche fuzzy pour les requêtes avancées (tags, opérateurs, NID)
        if (original_query.startswith('tag:') or any(op in original_query for op in (':', 'OR', 'AND'))
                or len(original_query) < 2):
            self.results_label.setText("Pas de résultats")
            return

        self.results_label.setText("Recherche approx. en cours...")

        def bg_fuzzy(col):
            import unicodedata
            import html as _html
            def _norm_fz(s):
                if not s: return ""
                s = _html.unescape(s)
                s = re.sub(r'<[^>]+>', ' ', s)
                s = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', s)
                s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
                return s.lower().strip()

            words = original_query.lower().split()
            candidate_queries = []

            for word in words:
                if len(word) >= 3:
                    candidate_queries.append(f"*{word}*")
                    if len(word) <= 8:
                        for i in range(len(word)):
                            truncated = word[:i] + word[i+1:]
                            if len(truncated) >= 3:
                                candidate_queries.append(f"*{truncated}*")
                        for i in range(len(word) - 1):
                            swapped = list(word)
                            swapped[i], swapped[i+1] = swapped[i+1], swapped[i]
                            swapped_w = "".join(swapped)
                            if swapped_w != word:
                                candidate_queries.append(f"*{swapped_w}*")

            candidate_queries = candidate_queries[:15]

            seen_nids = set()
            target_ids = []
            for fq in candidate_queries:
                try:
                    found = col.find_notes(fq)
                    for nid in found:
                        if source_nid and str(nid) == source_nid:
                            continue
                        if nid not in seen_nids:
                            seen_nids.add(nid)
                            target_ids.append(nid)
                except Exception:
                    pass

            # Scoring contextuel pour les résultats fuzzy
            if target_ids:
                try:
                    scored_candidates = []
                    chunk_size = 500
                    for i in range(0, len(target_ids), chunk_size):
                        chunk = target_ids[i:i + chunk_size]
                        rows = col.db.all(
                            f"SELECT id, flds, tags FROM notes WHERE id IN ({','.join(map(str, chunk))})"
                        )
                        for row in rows:
                            cid, flds, tags = row[0], row[1], row[2]
                            c_recto_raw = flds.split('\x1f')[0] if flds else ""
                            c_recto_norm = _norm_fz(c_recto_raw)
                            c_tags_norm = _norm_fz(tags)
                            c_body_norm = _norm_fz(flds)
                            
                            score = sum(1 for w in words if w in c_body_norm) * 10
                            if source_item and source_item in c_tags_norm:
                                score += 150
                            if source_specialty and source_specialty in c_tags_norm:
                                score += 60
                            for kw in source_keywords:
                                if kw in c_recto_norm:
                                    score += 35
                                elif kw in c_tags_norm:
                                    score += 20
                            scored_candidates.append((score, cid))
                    scored_candidates.sort(key=lambda x: x[0], reverse=True)
                    target_ids = [cid for _, cid in scored_candidates]
                except Exception as fe:
                    print("Erreur fuzzy score:", fe)

            return target_ids

        def on_fuzzy_success(results):
            try:
                self.objectName()
            except RuntimeError:
                return
            if seq != self._search_seq:
                return

            if not results:
                self.results_label.setText("Pas de résultats")
                return

            self._all_target_nids = results
            self._search_offset = 0
            self._search_type_label = "résultats approx."
            self._current_results_type = "search"
            self.load_next_page()

        from aqt.operations import QueryOp
        op = QueryOp(
            parent=self,
            op=bg_fuzzy,
            success=on_fuzzy_success
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
                self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                self.customContextMenuRequested.connect(self.on_context_menu)
                
            def on_click(self):
                from aqt import dialogs, mw
                browser = dialogs.open("Browser", mw)
                browser.search_for(f"nid:{self.nid}")
                self.dialog_parent.hide_preview_popup()

            def on_context_menu(self, pos):
                global_pos = self.mapToGlobal(pos)
                self.dialog_parent.show_learn_grade_menu(self.nid, global_pos)

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
                    QTimer.singleShot(400, lambda: self._safe_check_hide())
                except RuntimeError:
                    pass
                try:
                    super().leaveEvent(event)
                except RuntimeError:
                    pass

            def focusOutEvent(self, event):
                try:
                    self._safe_check_hide()
                except (RuntimeError, Exception):
                    pass
                try:
                    super().focusOutEvent(event)
                except (RuntimeError, Exception):
                    pass

            def _safe_check_hide(self):
                """Wrapper sûr pour check_hide_preview, protège contre les widgets Qt détruits."""
                try:
                    self.objectName() # throws RuntimeError if deleted
                    self.dialog_parent.check_hide_preview()
                except (RuntimeError, Exception):
                    pass
        
        return HoverButton("Voir", nid, dialog_parent)

    def on_table_context_menu(self, pos):
        item = self.results_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        nid_item = self.results_table.item(row, 1)
        if nid_item:
            nid = nid_item.text()
            global_pos = self.results_table.mapToGlobal(pos)
            self.show_learn_grade_menu(nid, global_pos)

    def show_learn_grade_menu(self, nid, global_pos):
        if not nid:
            return
        from aqt.qt import QMenu
        menu = QMenu(self)
        
        action_learn = menu.addAction("Apprendre maintenant")
        action_learn.triggered.connect(lambda checked=False, n=nid: learn_now_nid(n))
        
        grade_menu = menu.addMenu("Noter")
        for ease, label in [(1, "À revoir"), (2, "Difficile"), (3, "Correct"), (4, "Facile")]:
            action_grade = grade_menu.addAction(label)
            action_grade.triggered.connect(lambda checked=False, n=nid, e=ease: grade_now_nid(n, e))
            
        menu.exec(global_pos)

    def _on_table_scroll(self, value):
        if hasattr(self, '_preview_dlg') and self._preview_dlg and self._preview_dlg.isVisible():
            if hasattr(self, '_preview_current_widget') and self._preview_current_widget:
                btn = self._preview_current_widget
                from aqt.qt import sip
                if sip.isdeleted(btn):
                    self._preview_current_widget = None
                    self.hide_preview_popup()
                    return
                try:
                    # Check if still visible
                    rect = self.results_table.visualItemRect(self.results_table.itemAt(btn.pos()))
                    if btn.isVisible() and btn.rect().intersects(self.results_table.viewport().rect()):
                        # Repositionnement lors du scroll : même logique gauche/droite
                        screen = QApplication.primaryScreen().availableGeometry()
                        dlg = self._preview_dlg
                        preview_w = dlg.width() if dlg.width() > 100 else 680
                        gui_center_x = self.x() + self.width() // 2
                        gui_on_right = gui_center_x > screen.x() + screen.width() // 2
                        if gui_on_right:
                            preview_x = self.x() - preview_w - 8
                            if preview_x < screen.x():
                                preview_x = screen.x()
                        else:
                            preview_x = self.x() + self.width() + 8
                            if preview_x + preview_w > screen.right():
                                preview_x = screen.right() - preview_w
                        btn_global_y = btn.mapToGlobal(QPoint(0, 0)).y()
                        preview_y = btn_global_y - 30
                        if preview_y + dlg.height() > screen.bottom():
                            preview_y = screen.bottom() - dlg.height() - 10
                        if preview_y < screen.top():
                            preview_y = screen.top()
                        dlg.move(preview_x, preview_y)
                    else:
                        self.hide_preview_popup()
                except (RuntimeError, Exception):
                    self._preview_current_widget = None
                    self.hide_preview_popup()

        # Détection du défilement vers le bas pour charger plus automatiquement
        try:
            scrollbar = self.results_table.verticalScrollBar()
            if scrollbar.value() >= scrollbar.maximum() - 5:
                if hasattr(self, 'load_more_btn') and self.load_more_btn.isVisible() and self.load_more_btn.isEnabled():
                    self.load_next_page()
        except Exception as e:
            print("Error in scroll auto-load:", e)

    def toggle_preview_popup(self, nid, position_widget=None):
        """Affiche ou masque la preview sans fermer le dialog principal."""
        if (hasattr(self, '_preview_dlg') and self._preview_dlg
                and self._preview_dlg.isVisible()
                and getattr(self, '_preview_current_nid', None) == nid):
            self._preview_dlg.hide()
        else:
            self.show_preview_popup(nid, position_widget=position_widget)

    def _init_preview_dlg(self):
        """Pré-crée le popup preview caché pour éviter le flash à la première ouverture.
        Doit être appelé une seule fois depuis setup_ui()."""
        if hasattr(self, '_preview_dlg') and self._preview_dlg is not None:
            return
        flags = (Qt.WindowType.ToolTip
                 | Qt.WindowType.FramelessWindowHint)
        dlg = QWidget(self, flags)
        dlg.setObjectName("PreviewPopup")
        dlg.setWindowTitle("Apercu")
        dlg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        dlg.setMinimumSize(500, 50)
        dlg.setMaximumSize(680, 1400)

        v = QVBoxLayout(dlg)
        v.setContentsMargins(2, 2, 2, 2)

        from aqt.webview import AnkiWebView
        web = AnkiWebView(parent=dlg)
        v.addWidget(web)

        self._preview_dlg = dlg
        self._preview_web = web

        # Initialiser immédiatement le moteur WebEngine en arrière-plan pendant la construction du GUI
        # Cela évite l'initialisation tardive de WebEngine lors du premier survol qui provoquait
        # une perte d'activation et la disparition temporaire du GUI sous Windows.
        try:
            stubs_init = """
            <script>
            window.$ = window.jQuery = window.jQuery || function() { return { on: function(){}, off: function(){}, hide: function(){}, show: function(){}, css: function(){}, ready: function(fn){ if(fn) fn(); }, find: function(){ return { length: 0 }; }, each: function(){} }; };
            window._blockDefaultDragDropBehavior = window._blockDefaultDragDropBehavior || function(){};
            window._showQuestion = window._showQuestion || function(){};
            window._drawFlag = window._drawFlag || function(){};
            window._drawMark = window._drawMark || function(){};
            window._showAnswer = window._showAnswer || function(){};
            window.showQuestion = window.showQuestion || function(){};
            window.showAnswer = window.showAnswer || function(){};
            </script>
            """
            web.stdHtml(
                stubs_init + "<style>body{margin:0;background:transparent;}</style><div></div>",
                js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"]
            )
        except Exception:
            pass

        # Install event filter to track mouse leave from popup
        dlg.installEventFilter(self)

        # --- Fix Alt+Tab : cacher la preview quand Anki perd le focus ---
        # WA_ShowWithoutActivating empêche la popup de recevoir le focus,
        # donc WindowDeactivate sur dlg ne se déclenche jamais.
        # On écoute à la place le signal de l'application entière.
        def _on_app_state_changed(state):
            try:
                from aqt.qt import Qt
                active = getattr(Qt.ApplicationState, 'ApplicationActive',
                                 getattr(Qt, 'ApplicationActive', 4))
                if state != active:
                    self.hide_preview_popup()
            except Exception:
                pass
        app = QApplication.instance()
        self._app_state_conn = app.applicationStateChanged.connect(_on_app_state_changed)
        # Déconnecter proprement à la destruction de la popup
        dlg.destroyed.connect(lambda: _disconnect_app_state(app, self))
        # Rester caché jusqu'au premier hover
        dlg.hide()

    def show_preview_popup(self, nid, position_widget=None):
        """Affiche un popup Qt avec la preview HTML (CSS identique a la revision)."""
        self._is_showing_preview = True
        try:
            from aqt.qt import QTimer
            # Délai allongé : la première init de la WebView Qt (Chromium/WebEngine) peut
            # prendre plusieurs centaines de ms et déclenche un ActivationChange sur le GUI.
            # _is_showing_preview bloque hide_preview_popup() pendant toute cette période.
            QTimer.singleShot(1200, lambda: setattr(self, '_is_showing_preview', False))
        except Exception:
            pass
        try:
            import re as re2
            note = mw.col.get_note(int(nid))
            cards = note.cards()
            if not cards:
                return
            card = cards[0]
            rendered = card.answer()
            rendered = _clean_kbd_html(rendered)

            # Meme nettoyage que le hover
            # IMPORTANT: ne matcher que les attributs id= stricts (pas data-nid=, etc.)
            # Le lookbehind (?<!\w) s'assure qu'on ne touche pas à "data-nid=" etc.
            isolated = re2.sub(r'(?<!\w)id=(["\'])(.*?)\1', r'id="dlg_preview_\2"', rendered)
            isolated = re2.sub(r'src=(["\'])(?![a-z]+:)(?!/)([^"\']+)\1', r'src="/\2"', isolated, flags=re2.IGNORECASE)
            isolated = re2.sub(r'toggle\([\"\'](.*?)[\"\']\)', r"toggle('dlg_preview_\1')", isolated)
            # Remplacer onclick par data-onclick pour éviter les violations de Content Security Policy (CSP)
            isolated = re2.sub(r'\bonclick=(["\'])(.*?)\1', r'data-onclick=\1\2\1', isolated, flags=re2.IGNORECASE)
            isolated = re2.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', '', isolated, flags=re2.IGNORECASE)
            isolated = re2.sub(
                r"(?:<br>\s*)?<(span|div|p)\b[^>]*id=[^>]*fsrs[^>]*>.*?</\1>",
                "", isolated, flags=re2.IGNORECASE|re2.DOTALL)
            sec_to_field = {
                "sourcesMegaContainer": "Sources",
                "commentsMegaContainer": "Comments",
                "tagsMegaContainer": "Tags",
                "erreursFaitesMegaContainer": "Erreurs faites",
                "mnemonicsMegaContainer": "Mnemonics",
                "infoSupplementairesMegaContainer": "Infos supplémentaires",
                "cartesLieesMegaContainer": "Cartes liées"
            }
            for sec in ["sourcesMegaContainer", "commentsMegaContainer", "tagsMegaContainer",
                        "erreursFaitesMegaContainer", "mnemonicsMegaContainer", "infoSupplementairesMegaContainer"]:
                field_name = sec_to_field.get(sec)
                has_links = False
                if field_name and field_name in note:
                    val = note[field_name]
                    if "clickable_cards" in val or "data-nid" in val or re2.search(r'\b1\d{12}\b', val):
                        has_links = True
                if not has_links:
                    # Cacher le container via style inline display none pour contourner les divs imbriqués
                    isolated = re2.sub(rf'(<div[^>]*id=["\'](?:dlg_preview_)?{sec}["\'][^\'>]*style=["\'])([^\'\"]*)', r'\1display: none !important;\2', isolated, flags=re2.IGNORECASE)
                    isolated = re2.sub(rf'(<div[^>]*id=["\'](?:dlg_preview_)?{sec}["\'])(?![^>]*style=)', r'\1 style="display: none !important;"', isolated, flags=re2.IGNORECASE)

            # Forcer l'affichage des sections (comme dans le reviewer)

            isolated = re2.sub(

                r'(<div[^>]*class=["\'][^"\']*(section)[^"\'][^"\']*["\'][^>]*style=["\'])display:\s*none;?',

                r'\1display: flex !important;', isolated, flags=re2.IGNORECASE)

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

            # Le popup est pré-créé dans _init_preview_dlg() (appelé depuis setup_ui)
            # pour éviter le flash visuel lors du premier hover. Si pour une raison
            # quelconque il n'existe pas encore, on le crée maintenant.
            if not hasattr(self, '_preview_dlg') or self._preview_dlg is None:
                self._init_preview_dlg()
            dlg = self._preview_dlg
            web = self._preview_web

            if is_night:
                dlg.setStyleSheet("#PreviewPopup { border: 2px solid #555; background: #373a41; border-radius: 5px; }")
            else:
                dlg.setStyleSheet("#PreviewPopup { border: 2px solid #c0c1c5; background: #f5f6f7; border-radius: 5px; }")
                
            self._preview_current_nid = nid
            if position_widget:
                self._preview_stack = []

            if position_widget:
                screen = QApplication.primaryScreen().availableGeometry()
                preview_w = dlg.width() if dlg.width() > 100 else 680
                # Déterminer si le GUI est plutôt à droite de l'écran
                # (centre du GUI > moitié de l'écran => GUI à droite)
                gui_center_x = self.x() + self.width() // 2
                gui_on_right = gui_center_x > screen.x() + screen.width() // 2

                if gui_on_right:
                    # Placer la preview à GAUCHE du GUI
                    preview_x = self.x() - preview_w - 8
                    # Si déborde à gauche de l'écran, la coller au bord gauche
                    if preview_x < screen.x():
                        preview_x = screen.x()
                else:
                    # Placer la preview à DROITE du GUI
                    preview_x = self.x() + self.width() + 8
                    # Si déborde à droite de l'écran, la coller au bord droit
                    if preview_x + preview_w > screen.right():
                        preview_x = screen.right() - preview_w

                # Alignement vertical : centré sur le bouton "Voir", ajusté si nécessaire
                btn_global_y = position_widget.mapToGlobal(QPoint(0, 0)).y()
                preview_y = btn_global_y - 30
                # Empêcher de sortir en bas
                if preview_y + 350 > screen.bottom():
                    preview_y = screen.bottom() - 360
                # Empêcher de sortir en haut
                if preview_y < screen.top():
                    preview_y = screen.top()

                dlg.move(preview_x, preview_y)

            # Charger les préférences cartes EDN
            show_rank = True
            show_flags = True
            show_dual_flags = False
            virtual_flag_color = ""
            try:
                from .edn_menu.card_styles import load_prefs
                prefs = load_prefs()
                show_rank = prefs.get("showRankBadge", True)
                show_flags = prefs.get("showFlagBorders", True)
                show_dual_flags = prefs.get("previewDualFlags", False)
                virtual_flag_color = _get_virtual_flag_color(note, cards)
            except Exception as e:
                print("Error loading EDN card styles prefs in linked cards:", e)
                show_rank = True
                show_flags = True
                show_dual_flags = False
                virtual_flag_color = ""

            show_rank_js = "true" if show_rank else "false"
            show_flags_js = "true" if show_flags else "false"
            show_dual_flags_js = "true" if show_dual_flags else "false"
            virtual_flag_color_js = virtual_flag_color  # "" ou "#3182CE" ou "#E53E3E"

            pref_script = f"""
            <script>
            (function() {{
                function _applyEdnPreviewStyles() {{
                    try {{
                        var showRank = {show_rank_js};
                        var showFlags = {show_flags_js};
                        var dualFlags = {show_dual_flags_js};
                        var virtualFlagColor = "{virtual_flag_color_js}";
                        
                        var tagsEl = document.getElementById("dlg_preview_tagsData") || document.getElementById("tagsData");
                        var tags = tagsEl ? tagsEl.innerHTML : "";
                        var rang = tags.includes("Rang::A") ? "A" : tags.includes("Rang::B") ? "B" : tags.includes("Rang::C") ? "C" : "?";
                        
                        if (showRank) {{
                            var colorA = (typeof edn_prefs !== "undefined" && edn_prefs.rank_color_a) ? edn_prefs.rank_color_a : (typeof prefs !== "undefined" && prefs.rank_color_a) ? prefs.rank_color_a : '#111827';
                            var colorB = (typeof edn_prefs !== "undefined" && edn_prefs.rank_color_b) ? edn_prefs.rank_color_b : (typeof prefs !== "undefined" && prefs.rank_color_b) ? prefs.rank_color_b : '#3182CE';
                            var colorC = (typeof edn_prefs !== "undefined" && edn_prefs.rank_color_c) ? edn_prefs.rank_color_c : (typeof prefs !== "undefined" && prefs.rank_color_c) ? prefs.rank_color_c : '#38A169';
                            var rankColor = rang === 'A' ? colorA : rang === 'B' ? colorB : rang === 'C' ? colorC : null;

                            var bars = document.querySelectorAll('.bar, .barHider');
                            bars.forEach(function(b) {{
                                if (!b.classList.contains('edn-rang-' + rang)) {{
                                    b.classList.add('edn-rang-' + rang);
                                }}
                                if (rankColor) {{
                                    b.style.backgroundColor = rankColor;
                                    b.style.borderColor = rankColor;
                                }}
                            }});
                        }}
                        
                        var realColor = null;
                        if (showFlags) {{
                            var flagsEl = document.getElementById('dlg_preview_flagsData') || document.getElementById('flagsData') || document.getElementById('dlg_preview_flags') || document.getElementById('flags');
                            var flags = flagsEl ? flagsEl.innerHTML.trim() : "";
                            var flagColorMap = {{
                                'flag1': '#E53E3E', // Rouge
                                'flag2': '#DD6B20', // Orange
                                'flag3': '#38A169', // Vert
                                'flag4': '#3182CE', // Bleu
                                'flag5': '#D53F8C', // Rose
                                'flag6': '#00A3C4', // Turquoise
                                'flag7': '#805AD5'  // Violet
                            }};
                            for (var key in flagColorMap) {{
                                if (flags.includes(key)) {{
                                    realColor = flagColorMap[key];
                                    break;
                                }}
                            }}
                            if (!realColor && tags.includes("Difficile")) {{
                                realColor = '#E53E3E';
                            }}
                            if (!realColor && tags.includes("Bleu")) {{
                                realColor = '#3182CE';
                            }}
                        }}
                        
                        var sections = document.getElementsByClassName("section");
                        var hiders = document.getElementsByClassName("barHider");
                        var bars = document.getElementsByClassName("bar");
                        
                        if (showFlags && dualFlags && virtualFlagColor && realColor) {{
                            var dualGradient = "linear-gradient(to right, " + virtualFlagColor + " 50%, " + realColor + " 50%) 1";
                            for (var s of sections) {{
                                s.style.borderColor = "transparent";
                                s.style.borderImage = dualGradient;
                            }}
                            for (var h of hiders) {{
                                h.style.borderColor = "transparent";
                                h.style.borderImage = dualGradient;
                            }}
                            for (var b of bars) {{
                                b.style.borderImage = "none";
                                b.style.borderColor = virtualFlagColor;
                            }}
                        }} else {{
                            var activeColor = showFlags ? (realColor || virtualFlagColor) : null;
                            if (activeColor) {{
                                for (var s of sections) {{
                                    s.style.borderImage = "none";
                                    s.style.borderColor = activeColor;
                                }}
                                for (var h of hiders) {{
                                    h.style.borderImage = "none";
                                    h.style.borderColor = activeColor;
                                }}
                                for (var b of bars) {{
                                    b.style.borderImage = "none";
                                    b.style.borderColor = activeColor;
                                }}
                            }} else {{
                                for (var s of sections) {{
                                    s.style.borderImage = "none";
                                    s.style.borderColor = "";
                                }}
                                for (var h of hiders) {{
                                    h.style.borderImage = "none";
                                    h.style.borderColor = "";
                                }}
                                for (var b of bars) {{
                                    b.style.borderImage = "none";
                                    b.style.borderColor = "";
                                }}
                            }}
                        }}
                        var cardEl = document.querySelector('.card');
                        if (cardEl) {{
                            cardEl.style.border = 'none';
                        }}
                    }} catch(e) {{
                        console.error("Error applying preview styles:", e);
                    }}
                }}
                _applyEdnPreviewStyles();
                setTimeout(_applyEdnPreviewStyles, 100);
                setTimeout(_applyEdnPreviewStyles, 400);
            }})();
            </script>
            """

            addon_package = mw.addonManager.addonFromModule(__name__)
            css_link = '<link rel="stylesheet" href="/_addons/' + addon_package + '/user_files/clickable_cards.css">'

            model_css = note.note_type().get('css', '')
            if model_css:
                css_link += f'<style>\n{_sanitize_model_css_for_preview(model_css)}\n</style>'

            # Charger les styles de preview depuis user_files/preview_edn.css
            preview_css = _load_preview_edn_css()
            blacklist_selectors = [f"#dlg_preview_{sec}" for sec in ["sourcesMegaContainer", "commentsMegaContainer", "tagsMegaContainer", "erreursFaitesMegaContainer", "mnemonicsMegaContainer", "infoSupplementairesMegaContainer"]]
            blacklist_selectors.append("#dlg_preview_FSRS_status")
            blacklist_css = ", ".join(blacklist_selectors) + " { display: none !important; }\n"
            blacklist_css += ".edn-specialty-icons { display: none !important; }\n"
            scoped_css = f"<style>\n{blacklist_css}{preview_css}\n</style>"
            
            gui_hover_script = r"""
            <script>
            // Suppress MathJax mathtools version warning
            window.MathJax = window.MathJax || {};
            window.MathJax.loader = window.MathJax.loader || {};
            window.MathJax.loader.versions = window.MathJax.loader.versions || {};
            window.MathJax.loader.versions['[tex]/mathtools'] = '3.0.0';

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

            // Fonction toggle pour les sections de la carte
            window.toggle = window.toggle || function(id) {
                var el = document.getElementById(id);
                if (el) el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
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
            window._guiPopupTime = Date.now();

            document.addEventListener("mouseover", function(e) {
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    if (window._guiPopupTime && (Date.now() - window._guiPopupTime < 100)) {
                        return;
                    }
                    var nid = _gn(e.target);
                    e.target.style.outline = '2px solid #007acc';
                    if (nid === _guiLastHoverNid) return; // même badge, pas besoin de recharger
                    if (_guiHoverTimer) { clearTimeout(_guiHoverTimer); _guiHoverTimer = null; }
                    _guiHoverTimer = setTimeout(function() {
                        _guiHoverTimer = null;
                        _guiLastHoverNid = nid;
                        if(typeof pycmd !== 'undefined') pycmd('gui_preview_hover:' + nid);
                    }, 100); // 100ms : délai de survol réactif
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
            var _guiLongPressTimer = null;
            var _guiTouchStartX = 0;
            var _guiTouchStartY = 0;
            var _guiLongPressTriggered = false;
            var _guiTouchRecently = false;
            var _guiLastTapNid = null;
            var _guiLastTapTime = 0;
            var _guiSingleTapTimer = null;

            document.addEventListener('touchstart', function(e) {
                _guiTouchRecently = true;
                if (!e.touches || e.touches.length !== 1) {
                    if (_guiLongPressTimer) { clearTimeout(_guiLongPressTimer); _guiLongPressTimer = null; }
                    return;
                }
                var touch = e.touches[0];
                _guiTouchStartX = touch.clientX;
                _guiTouchStartY = touch.clientY;
                _guiLongPressTriggered = false;
                var badge = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
                var nid = badge ? _gn(badge) : null;
                if (!nid) {
                    var cardEl = document.querySelector('.card') || document.body;
                    if (cardEl && cardEl.dataset && cardEl.dataset.nid) {
                        nid = cardEl.dataset.nid;
                    }
                }
                if (nid) {
                    if (_guiLongPressTimer) clearTimeout(_guiLongPressTimer);
                    _guiLongPressTimer = setTimeout(function() {
                        _guiLongPressTriggered = true;
                        if (_guiSingleTapTimer) { clearTimeout(_guiSingleTapTimer); _guiSingleTapTimer = null; }
                        if (typeof pycmd !== 'undefined') pycmd('edn_show_context_menu:' + nid + '|||' + Math.round(_guiTouchStartX) + '|||' + Math.round(_guiTouchStartY) + '||||||gui_preview');
                    }, 450);
                }
            }, { passive: true, capture: true });

            document.addEventListener('touchmove', function(e) {
                if (!e.touches || e.touches.length !== 1) {
                    if (_guiLongPressTimer) { clearTimeout(_guiLongPressTimer); _guiLongPressTimer = null; }
                    return;
                }
                var touch = e.touches[0];
                var dist = Math.hypot(touch.clientX - _guiTouchStartX, touch.clientY - _guiTouchStartY);
                if (dist > 10) {
                    if (_guiLongPressTimer) { clearTimeout(_guiLongPressTimer); _guiLongPressTimer = null; }
                }
            }, { passive: true, capture: true });

            document.addEventListener('touchend', function(e) {
                if (_guiLongPressTimer) { clearTimeout(_guiLongPressTimer); _guiLongPressTimer = null; }
                _guiTouchRecently = true;
                setTimeout(function() { _guiTouchRecently = false; }, 450);
                if (_guiLongPressTriggered) {
                    if (e.cancelable) e.preventDefault();
                    e.stopPropagation();
                    setTimeout(function() { _guiLongPressTriggered = false; }, 300);
                    return;
                }
                var badge = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
                var nid = badge ? _gn(badge) : null;
                if (nid) {
                    var now = Date.now();
                    if (_guiLastTapNid === nid && (now - _guiLastTapTime < 350)) {
                        // Double tap : ouvrir dans l'explorateur
                        if (_guiSingleTapTimer) { clearTimeout(_guiSingleTapTimer); _guiSingleTapTimer = null; }
                        _guiLastTapNid = null;
                        _guiLastTapTime = 0;
                        if (typeof pycmd !== 'undefined') pycmd('gui_preview_click:' + nid);
                        if (e.cancelable) e.preventDefault();
                        e.stopPropagation();
                        return;
                    } else {
                        // Simple tap : afficher la preview
                        _guiLastTapNid = nid;
                        _guiLastTapTime = now;
                        if (_guiSingleTapTimer) clearTimeout(_guiSingleTapTimer);
                        _guiSingleTapTimer = setTimeout(function() {
                            _guiSingleTapTimer = null;
                            if (typeof pycmd !== 'undefined') pycmd('gui_preview_hover:' + nid);
                        }, 220);
                        if (e.cancelable) e.preventDefault();
                        e.stopPropagation();
                        return;
                    }
                }
            }, true);

            document.addEventListener('touchcancel', function(e) {
                if (_guiLongPressTimer) { clearTimeout(_guiLongPressTimer); _guiLongPressTimer = null; }
                _guiLongPressTriggered = false;
                setTimeout(function() { _guiTouchRecently = false; }, 300);
            }, true);

            document.addEventListener('pointerdown', function(e) {
                if (e.button === 2) {
                    var badge = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
                    var nid = badge ? _gn(badge) : null;
                    if (nid) {
                        if (typeof pycmd !== 'undefined') pycmd('edn_right_click_nid:' + nid);
                    }
                }
            });

            document.addEventListener('contextmenu', function(e) {
                var badge = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
                var nid = badge ? _gn(badge) : null;
                if (nid) {
                    if (typeof pycmd !== 'undefined') pycmd('edn_right_click_nid:' + nid);
                }
            });

            document.addEventListener("click", function(e) {
                if (_guiLongPressTriggered || _guiTouchRecently) {
                    e.preventDefault();
                    e.stopPropagation();
                    return false;
                }
                if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                    var nid = _gn(e.target);
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_click:' + nid);
                }
            });
            document.addEventListener("keydown", function(e) {
                if (e.key === 'Tab' && e.ctrlKey) {
                    if (typeof pycmd !== 'undefined') pycmd('hide_preview_popup');
                }
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

            // Image load listener to dynamically resize
            function _triggerResize() {
                var card = document.querySelector('.card');
                var h = card ? Math.ceil(card.getBoundingClientRect().height) : (document.body ? document.body.scrollHeight : 0);
                if (typeof pycmd !== 'undefined') pycmd('edn_preview_resize:' + h);
            }
            // Use ResizeObserver to dynamically resize whenever the content height changes
            try {
                var cardEl = document.querySelector('.card') || document.body;
                var lastHeight = 0;
                var resizeObserver = new ResizeObserver(function(entries) {
                    var card = document.querySelector('.card');
                    var h = card ? Math.ceil(card.getBoundingClientRect().height) : (document.body ? document.body.scrollHeight : 0);
                    if (h && h !== lastHeight) {
                        lastHeight = h;
                        if (typeof pycmd !== 'undefined') pycmd('edn_preview_resize:' + h);
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
            </script>
            """
            
            night_script = ""
            if is_night:
                night_script = """
                <script>
                document.documentElement.classList.add('night-mode', 'nightMode', 'night_mode');
                if (document.body) document.body.classList.add('night-mode', 'nightMode', 'night_mode');
                </script>
                """

            stubs_script = """
            <script>
            window.$ = window.jQuery = window.jQuery || function() { return { on: function(){}, off: function(){}, hide: function(){}, show: function(){}, css: function(){}, ready: function(fn){ if(fn) fn(); }, find: function(){ return { length: 0 }; }, each: function(){} }; };
            window._blockDefaultDragDropBehavior = window._blockDefaultDragDropBehavior || function(){};
            window._showQuestion = window._showQuestion || function(){};
            window._drawFlag = window._drawFlag || function(){};
            window._drawMark = window._drawMark || function(){};
            window._showAnswer = window._showAnswer || function(){};
            window.showQuestion = window.showQuestion || function(){};
            window.showAnswer = window.showAnswer || function(){};
            </script>
            """

            # stdHtml ne charge pas MathJax automatiquement — on le passe via js=
            # afin que le LaTeX \(...\) et \[...\] soit rendu correctement.
            full_preview_html = (
                stubs_script
                + css_link
                + scoped_css
                + night_script
                + gui_hover_script
                + '<div class="card">'
                + isolated
                + '</div>'
                + pref_script
            )
            try:
                web.stdHtml(
                    full_preview_html,
                    js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"],
                    mathjax=True
                )
            except TypeError:
                try:
                    web.stdHtml(
                        full_preview_html,
                        js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"]
                    )
                except Exception:
                    web.stdHtml(
                        full_preview_html,
                        mathjax=True
                    )

            dlg.show()
            dlg.raise_()
            # Relancer le flag de protection après dlg.show() : le premier show() de la
            # WebView peut provoquer un ActivationChange différé sur la fenêtre parente.
            self._is_showing_preview = True
            try:
                QTimer.singleShot(1200, lambda: setattr(self, '_is_showing_preview', False))
            except Exception:
                pass
            
            # Dimensionnement initial : commencer petit pour éviter les saccades
            # puis auto-resize rapidement via _quick_resize
            dlg.resize(dlg.width(), 200)
            from aqt.qt import QTimer

            def _quick_resize():
                try:
                    js = """
                    (function() {
                        var cardEl = document.querySelector('.card');
                        return cardEl ? Math.ceil(cardEl.getBoundingClientRect().height) : (document.body ? document.body.scrollHeight : 0);
                    })()
                    """
                    web.evalWithCallback(js, lambda h: self._adjust_preview_height(dlg, h))
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
                            "    var cardEl = document.querySelector('.card');"
                            "    var h = cardEl ? Math.ceil(cardEl.getBoundingClientRect().height) : (document.body ? document.body.scrollHeight : 0);"
                            "    if (typeof pycmd !== 'undefined') pycmd('edn_preview_resize:' + h);"
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
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            y_min = screen.top() + 10
            y_max = screen.bottom() - 10
            absolute_max = screen.height() - 40
            
            # Constrain to this dialog (self)
            try:
                self.objectName() # Check if alive
                if self.height() > 100:
                    y_min = self.y() + 10
                    y_max = self.y() + self.height() - 10
                    absolute_max = self.height() - 20
            except Exception:
                pass
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
                new_height = min(absolute_max, max(50, int(float(h)) + 4))
            except (ValueError, TypeError):
                new_height = 200
        else:
            new_height = 200

        dlg.resize(dlg.width(), new_height)


        if hasattr(self, '_preview_web') and self._preview_web:
            try:
                self._preview_web.eval("document.documentElement.style.overflowY = 'auto';")
            except Exception:
                pass

        # Slide up if it overflows the bottom of the screen/parent window
        try:
            pos = dlg.pos()
            if pos.y() + new_height > y_max:
                new_y = max(y_min, y_max - new_height)
                dlg.move(pos.x(), new_y)
        except Exception as e:
            print("Error adjusting preview popup position:", e)

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

            # Handle MousePress event on results table
            try:
                is_mouse_press = (event.type() == QEvent.Type.MouseButtonPress)
            except AttributeError:
                is_mouse_press = (event.type() == QEvent.MouseButtonPress)

            if is_mouse_press:
                if hasattr(self, 'results_table') and self.results_table:
                    if obj == self.results_table or obj == self.results_table.viewport():
                        self.hide_preview_popup()

            # Handle Leave / WindowDeactivate events on preview dialog
            if hasattr(self, '_preview_dlg') and obj == self._preview_dlg:
                try:
                    is_leave = (event.type() == QEvent.Type.Leave)
                except AttributeError:
                    is_leave = (event.type() == QEvent.Leave)
                if is_leave:
                    from aqt.qt import QTimer
                    QTimer.singleShot(400, lambda: self.check_hide_preview())

                # Note : WindowDeactivate sur la popup n'est pas utilisé car
                # WA_ShowWithoutActivating empêche la popup de jamais recevoir le focus.
                # La fermeture sur perte de focus Anki est gérée via applicationStateChanged.
        except Exception as e:
            log(f"eventFilter error: {e}")
        return super().eventFilter(obj, event)

    def check_hide_preview(self):
        try:
            if getattr(self, '_context_menu_active', False):
                from aqt.qt import QTimer
                QTimer.singleShot(400, lambda: self.check_hide_preview())
                return
            try:
                from aqt.qt import Qt, QApplication
                no_btn = getattr(Qt.MouseButton, "NoButton", None) or getattr(Qt, "NoButton", None)
                if QApplication.mouseButtons() != no_btn:
                    from aqt.qt import QTimer
                    QTimer.singleShot(400, lambda: self.check_hide_preview())
                    return
            except Exception:
                pass
            if hasattr(self, '_preview_dlg') and self._preview_dlg and self._preview_dlg.isVisible():
                pos = QCursor.pos()
                # Build an extended safe zone: union of popup rect + button rect + 30px margin
                safe_rect = QRect(self._preview_dlg.geometry())
                if hasattr(self, '_preview_current_widget') and self._preview_current_widget:
                    try:
                        self._preview_current_widget.objectName()
                        btn_global = self._preview_current_widget.mapToGlobal(QPoint(0, 0))
                        btn_rect = QRect(btn_global, self._preview_current_widget.size())
                        safe_rect = safe_rect.united(btn_rect)
                    except (RuntimeError, Exception):
                        self._preview_current_widget = None
                # Inflate safe zone by 30px on all sides (plus large pour laisser le temps
                # de passer d'un badge à l'autre dans la preview-in-preview)
                safe_rect.adjust(-30, -30, 30, 30)
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
        nid_str = str(nid)
        curr_nid_str = str(self._preview_current_nid) if hasattr(self, '_preview_current_nid') and self._preview_current_nid is not None else ""
        self._preview_stack = [str(x) for x in self._preview_stack]
        if curr_nid_str != nid_str:
            if nid_str in self._preview_stack:
                idx = self._preview_stack.index(nid_str)
                self._preview_stack = self._preview_stack[:idx]
            else:
                self._preview_stack.append(curr_nid_str)
            self.show_preview_popup(nid_str, position_widget=None) # Réutilise la popup sans la déplacer

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
                    
                    // Collecte le texte avant le marker sur la même ligne (reset aux BR),
                    // en gérant le cas où le marker est imbriqué dans un sous-élément du blockParent
                    function _textBeforeMarkerInLine(container, mkr) {
                        var parts = [];
                        var nodes = Array.from(container.childNodes);
                        for (var i = 0; i < nodes.length; i++) {
                            var n = nodes[i];
                            if (n === mkr) break;
                            if (n.contains && n.contains(mkr)) {
                                parts.push(_textBeforeMarkerInNode(n, mkr)); break;
                            }
                            if (n.tagName === 'BR') { parts = []; continue; }
                            parts.push(n.textContent || '');
                        }
                        return parts.join('');
                    }
                    function _textBeforeMarkerInNode(nd, mkr) {
                        var text = '';
                        var chn = Array.from(nd.childNodes);
                        for (var i = 0; i < chn.length; i++) {
                            if (chn[i] === mkr) break;
                            if (chn[i].contains && chn[i].contains(mkr)) {
                                text += _textBeforeMarkerInNode(chn[i], mkr); break;
                            }
                            text += chn[i].textContent || '';
                        }
                        return text;
                    }
                    var trimmedBefore = _textBeforeMarkerInLine(blockParent, marker).trim();
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
                    items = []
                    for nid, recto in items_raw:
                        smart_desc = recto
                        try:
                            t_note = mw.col.get_note(int(nid))
                            s_note = self.editor.note if self.editor else None
                            computed = generate_smart_link_label(s_note, t_note)
                            if computed:
                                smart_desc = computed
                        except Exception:
                            pass
                        items.append((nid, smart_desc))
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
            // Nettoyer les anciens markers en premier — nettoyage récursif complet (tous les shadow roots)
            function _cleanMarkers() {
                function _findAll(root, found, visited) {
                    if (!root || visited.has(root)) return;
                    visited.add(root);
                    if (root.querySelectorAll) {
                        var ms = root.querySelectorAll('[id="edn-cursor-marker"]');
                        for (var i = 0; i < ms.length; i++) { if (!found.includes(ms[i])) found.push(ms[i]); }
                    }
                    if (root.shadowRoot) _findAll(root.shadowRoot, found, visited);
                    if (root.children) {
                        for (var i = 0; i < root.children.length; i++) _findAll(root.children[i], found, visited);
                    }
                    if (root.querySelectorAll) {
                        root.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {
                            _findAll(el, found, visited);
                            if (el.shadowRoot) _findAll(el.shadowRoot, found, visited);
                        });
                    }
                }
                var found = [], visited = new Set();
                _findAll(document.body, found, visited);
                document.querySelectorAll('anki-editable, anki-editor').forEach(function(el) { _findAll(el, found, visited); });
                found.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });
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
        if _is_active_dialog_alive(): _active_dialog.close()
    except: pass
    _active_dialog = LinkedCardsDialog(editor, selected_text=selected_text)
    # Positionner et dimensionner la fenêtre AVANT show() pour éviter tout flicker
    try:
        screen = QApplication.primaryScreen().availableGeometry()
        _active_dialog.setGeometry(screen.x(), screen.y(), screen.width() // 2, screen.height() - 80)
    except Exception:
        pass
    _active_dialog.show()
    _active_dialog.activateWindow()

def open_search_dialog_from_menu():
    from .editor_assistant import get_active_editor
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
        
        # Détecter si le module EDN est actif
        self.edn_active = False
        try:
            from .edn_menu import is_module_enabled
            self.edn_active = is_module_enabled("linked_cards")
        except:
            pass
            
        # --- Raccourcis ---
        group_shortcuts = QGroupBox("Raccourcis personnalisés")
        g_layout = QFormLayout()
        group_shortcuts.setLayout(g_layout)
        from .edn_menu import get_shortcut
        
        self.edit_trigger = QLineEdit(config.get("gui_trigger", "nid:"))
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
        
        # --- Blacklist (EDN uniquement) ---
        self.checkboxes = {}
        if self.edn_active:
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

        self.mirror_link_on_paste_cb = QCheckBox("Proposer le lien miroir lors du collage de NID")
        self.mirror_link_on_paste_cb.setToolTip(
            "Si coché, propose automatiquement le lien retour A↔B dans l'éditeur après avoir collé un NID."
        )
        self.mirror_link_on_paste_cb.setChecked(config.get("mirror_link_on_paste", True))
        m_layout.addWidget(self.mirror_link_on_paste_cb)

        # Description intelligente (EDN uniquement)
        self.desc_intelligent_cb = None
        if self.edn_active:
            self.desc_intelligent_cb = QCheckBox("Description intelligente")
            self.desc_intelligent_cb.setToolTip(
                "Si coché, extrait en priorité le contenu de C2 (sinon C1) pour les cartes cloze,\n"
                "et remplace le nom du lien par 'Main', 'Signes', 'Traitements', etc. selon le contexte."
            )
            self.desc_intelligent_cb.setChecked(config.get("description_intelligente", True))
            m_layout.addWidget(self.desc_intelligent_cb)
        
        layout.addWidget(group_mirror)
        layout.addStretch()

    def save_config(self):
        config = mw.addonManager.getConfig(__name__) or {}
        config["gui_trigger"] = self.edit_trigger.text()
        
        from .edn_menu import set_shortcut
        set_shortcut("linked_cards_search", self.edit_search.text())
        set_shortcut("linked_cards_copy", self.edit_copy.text())
        set_shortcut("linked_cards_liens", self.edit_liens.text() or "Ctrl+Alt+B")

        set_shortcut("linked_cards_kbd_navigate", self.edit_kbd_navigate.text() or "r")
        set_shortcut("linked_cards_kbd_open", self.edit_kbd_open.text() or "n")
        set_shortcut("linked_cards_kbd_preview", self.edit_kbd_preview.text() or "p")
        set_shortcut("linked_cards_kbd_back", self.edit_kbd_back.text() or "z")
        
        if self.edn_active:
            hiddens = [box_id for box_id, cb in self.checkboxes.items() if cb.isChecked()]
            config["hidden_preview_sections"] = hiddens
            if self.desc_intelligent_cb:
                config["description_intelligente"] = self.desc_intelligent_cb.isChecked()
        
        config["mirror_link_on_paste"] = self.mirror_link_on_paste_cb.isChecked()
        
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


# --- Learn Now & Grade Now Context Menu Integration ---

def learn_now_nid(nid: str) -> None:
    if not nid:
        return
    try:
        note = mw.col.get_note(int(nid))
        cards = note.cards()
    except Exception as e:
        tooltip(f"Erreur note {nid}: {e}")
        return
        
    new_cards = [c for c in cards if c.type == 0 and c.queue in (0, -1)]
    if not new_cards:
        tooltip("Aucune carte nouvelle à apprendre (déjà apprise ou exclue).")
        return

    try:
        import importlib
        from aqt.operations import CollectionOp
        learn_now = importlib.import_module("1021636467.learn_now")
        
        def run_op(col):
            res = learn_now.put_cards_in_learning(col, new_cards)
            if res is not None:
                return res
            from anki.collection import OpChanges
            return OpChanges()
            
        is_reviewing = bool(
            getattr(mw, "state", None) == "review"
            and mw.reviewer
            and mw.reviewer.card
        )
        op_parent = None if is_reviewing else mw
        initiator = mw.reviewer if is_reviewing else None
        CollectionOp(parent=op_parent, op=run_op).success(
            lambda out: tooltip("Carte(s) mise(s) en apprentissage.")
        ).run_in_background(initiator=initiator)
    except Exception as e:
        tooltip(f"Erreur Learn Now: {e}")

def grade_now_nid(nid: str, ease: int) -> None:
    if not nid:
        return
    try:
        note = mw.col.get_note(int(nid))
        cards = note.cards()
    except Exception as e:
        tooltip(f"Erreur note {nid}: {e}")
        return

    if not cards:
        tooltip("Aucune carte trouvée.")
        return

    if mw.col.sched.version < 3:
        from aqt.utils import showInfo
        showInfo("Veuillez activer le planificateur V3 dans les préférences d'Anki.")
        return

    to_answer = [c for c in cards if c.queue >= 0]
    if not to_answer:
        tooltip("Aucune carte active à noter.")
        return

    if len(to_answer) > 30:
        tooltip("Impossible de noter plus de 30 cartes à la fois.")
        return

    try:
        import importlib
        from aqt.operations import CollectionOp
        grade_now = importlib.import_module("1021636467.grade_now")
        
        def run_op(col):
            res = grade_now.grade_cards(col, to_answer, ease)
            if res is not None:
                return res
            from anki.collection import OpChanges
            return OpChanges()
            
        is_reviewing_other = bool(
            getattr(mw, "state", None) == "review"
            and mw.reviewer
            and mw.reviewer.card
            and str(mw.reviewer.card.nid) != str(nid)
        )
        op_parent = None if is_reviewing_other else mw
        initiator = mw.reviewer if is_reviewing_other else None
        CollectionOp(parent=op_parent, op=run_op).success(
            lambda out: tooltip("Carte(s) notée(s).")
        ).run_in_background(initiator=initiator)
    except Exception as e:
        tooltip(f"Erreur Grade Now: {e}")

def _get_recto_for_nid(nid_str: str) -> str:
    try:
        note = mw.col.get_note(int(nid_str))
        from .editor_assistant import get_active_editor
        editor = get_active_editor()
        source_note = editor.note if editor else None
        return generate_smart_link_label(source_note, note) or get_note_recto(note, 100)
    except Exception:
        return "[Erreur]"

def link_card_to_active_editor(target_nid: str) -> None:
    if not target_nid:
        return
    
    from .editor_assistant import get_active_editor
    editor = get_active_editor()
    if not editor:
        from aqt.utils import tooltip
        tooltip("Aucun éditeur actif pour lier la carte.")
        return
        
    if not editor.note:
        from aqt.utils import tooltip
        tooltip("Veuillez d'abord sauvegarder la note en cours.")
        return
        
    if str(editor.note.id) == str(target_nid):
        from aqt.utils import tooltip
        tooltip("Impossible de lier une carte à elle-même.")
        return
        
    config = mw.addonManager.getConfig(__name__) or {}
    mirror_req = config.get("gui_mirror_cb_state", True)
    
    recto = _get_recto_for_nid(target_nid)
    
    inserter = LinkInserter(editor)
    
    # Détecter le tiret et poser le marker à la sélection courante de l'éditeur
    _dash_detection_js = """
    (function() {
        try {
            // 1. Nettoyer les anciens markers
            function _cleanMarkers() {
                function _findAll(root, found, visited) {
                    if (!root || visited.has(root)) return;
                    visited.add(root);
                    if (root.querySelectorAll) {
                        var ms = root.querySelectorAll('[id="edn-cursor-marker"]');
                        for (var i = 0; i < ms.length; i++) { if (!found.includes(ms[i])) found.push(ms[i]); }
                    }
                    if (root.shadowRoot) _findAll(root.shadowRoot, found, visited);
                    if (root.children) {
                        for (var i = 0; i < root.children.length; i++) _findAll(root.children[i], found, visited);
                    }
                    if (root.querySelectorAll) {
                        root.querySelectorAll('anki-editable, anki-editor').forEach(function(el) {
                            _findAll(el, found, visited);
                            if (el.shadowRoot) _findAll(el.shadowRoot, found, visited);
                        });
                    }
                }
                var found = [], visited = new Set();
                _findAll(document.body, found, visited);
                document.querySelectorAll('anki-editable, anki-editor').forEach(function(el) { _findAll(el, found, visited); });
                found.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });
            }
            _cleanMarkers();

            // 2. Trouver la sélection active dans l'éditeur
            var active = document.activeElement;
            var sel = null;
            function _findActiveSel() {
                if (active && active.shadowRoot) {
                    var s = active.shadowRoot.getSelection();
                    if (s && s.rangeCount > 0) return s;
                }
                var allHosts = Array.from(document.querySelectorAll('anki-editor, anki-editable'));
                for (var hi = 0; hi < allHosts.length; hi++) {
                    var h = allHosts[hi];
                    if (h.shadowRoot) {
                        var s2 = h.shadowRoot.getSelection ? h.shadowRoot.getSelection() : null;
                        if (s2 && s2.rangeCount > 0) return s2;
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

            // 3. Insérer le marker à la sélection
            var marker = null;
            if (sel && sel.rangeCount > 0) {
                window._ednSavedRange = sel.getRangeAt(0).cloneRange();
                try {
                    let r = window._ednSavedRange.cloneRange();
                    r.collapse(false);
                    marker = document.createElement("span");
                    marker.id = "edn-cursor-marker";
                    r.insertNode(marker);
                } catch(e) {}
            }

            if (!marker) return false;

            // 4. Détecter si la ligne se termine par un tiret
            var node = marker.parentNode;
            if (!node) return false;
            var blockParent = node;
            while (blockParent && blockParent.tagName !== 'ANKI-EDITABLE' && !blockParent.isContentEditable) {
                if (['LI', 'P', 'DIV'].indexOf(blockParent.tagName) >= 0) break;
                blockParent = blockParent.parentNode;
            }
            if (!blockParent) return false;

            // Collecte le texte avant le marker sur la même ligne (reset aux BR),
            // en gérant le cas où le marker est imbriqué dans un sous-élément du blockParent
            function _textBeforeMarkerInLine2(container, mkr) {
                var parts = [];
                var nodes = Array.from(container.childNodes);
                for (var i = 0; i < nodes.length; i++) {
                    var n = nodes[i];
                    if (n === mkr) break;
                    if (n.contains && n.contains(mkr)) {
                        parts.push(_textBeforeMarkerInNode2(n, mkr)); break;
                    }
                    if (n.tagName === 'BR') { parts = []; continue; }
                    parts.push(n.textContent || '');
                }
                return parts.join('');
            }
            function _textBeforeMarkerInNode2(nd, mkr) {
                var text = '';
                var chn = Array.from(nd.childNodes);
                for (var i = 0; i < chn.length; i++) {
                    if (chn[i] === mkr) break;
                    if (chn[i].contains && chn[i].contains(mkr)) {
                        text += _textBeforeMarkerInNode2(chn[i], mkr); break;
                    }
                    text += chn[i].textContent || '';
                }
                return text;
            }
            var trimmedBefore = _textBeforeMarkerInLine2(blockParent, marker).trim();
            return trimmedBefore.endsWith('—') || trimmedBefore.endsWith('-') || trimmedBefore.endsWith('\\u2014');
        } catch(e) { return false; }
    })();
    """
    
    def _do_insert(has_dash):
        recto_to_insert = None if has_dash else recto
        try:
            inserter.insert_link([(target_nid, recto_to_insert)], mirror_requested=mirror_req)
        except Exception as e:
            from aqt.utils import tooltip
            tooltip(f"Erreur lors de l'insertion : {e}")

    try:
        editor.web.evalWithCallback(_dash_detection_js, _do_insert)
    except Exception:
        from aqt.qt import QTimer
        QTimer.singleShot(0, lambda: inserter.insert_link([(target_nid, recto)], mirror_requested=mirror_req))

def prompt_modify_hyperlink(nid: str, default_text: str, webview) -> None:
    from aqt.qt import QInputDialog
    new_text, ok = QInputDialog.getText(
        webview.window(),
        "Modifier l'hyperlien",
        "Texte de l'hyperlien :",
        text=default_text
    )
    if ok and new_text.strip():
        escaped_new_text = new_text.replace('\\', '\\\\').replace("'", "\\'")
        webview.eval(f"if (typeof _ednModifyHyperlink === 'function') _ednModifyHyperlink('{nid}', '{escaped_new_text}');")
        try:
            from .editor_assistant import get_active_editor
            editor = get_active_editor()
            if editor:
                editor.saveNow(lambda: None)
        except Exception:
            pass

def open_nid_in_browser(nid: str) -> None:
    from aqt import dialogs
    browser = dialogs.open("Browser", mw)
    browser.search_for(f"nid:{nid}")

def show_card_context_menu(nid: str, pos=None, webview=None, default_text: str = "", source: str = None) -> None:
    from aqt.qt import QMenu, QCursor, QPoint
    if not nid:
        return
    
    parent_widget = webview.window() if webview else mw
    menu = QMenu(parent_widget)
    
    is_assistant = (source == "assistant")
    if not is_assistant and webview:
        try:
            from .editor_assistant import get_active_editor
            editor = get_active_editor()
            if editor and getattr(editor, '_editor_assistant_web', None) == webview:
                is_assistant = True
        except Exception:
            pass

    is_preview_popup = (source in ("preview_popup", "gui_preview", "reviewer_preview", "sidebar_preview"))
    if not is_preview_popup and webview:
        if _is_active_dialog_alive() and webview == getattr(_active_dialog, '_preview_web', None):
            is_preview_popup = True
        else:
            try:
                from .editor_assistant import _preview_popup as _ast_popup, _is_preview_popup_alive
                if _is_preview_popup_alive() and hasattr(_ast_popup, '_edn_web') and webview == _ast_popup._edn_web:
                    is_preview_popup = True
            except Exception:
                pass

    # Ouvrir dans l'explorateur
    action_open = menu.addAction("Ouvrir dans l'explorateur")
    action_open.triggered.connect(lambda checked=False, n=nid: open_nid_in_browser(n))
    
    # Hyperliens (si dans l'éditeur)
    is_editor = (source == "editor") or (webview and webview.__class__.__name__ == "EditorWebView")
    if is_editor and not is_preview_popup:
        menu.addSeparator()
        action_hyperlink = menu.addAction("Hyperliens")
        action_hyperlink.triggered.connect(lambda checked=False, n=nid, t=default_text, w=webview: prompt_modify_hyperlink(n, t, w))

    # Lier cette carte (si preview popup et éditeur actif)
    if is_preview_popup:
        try:
            from .editor_assistant import get_active_editor
            if get_active_editor():
                menu.addSeparator()
                action_link = menu.addAction("Lier cette carte")
                action_link.triggered.connect(lambda checked=False, n=nid: link_card_to_active_editor(n))
        except Exception:
            pass

    menu.addSeparator()
    action_learn = menu.addAction("Apprendre maintenant")
    action_learn.triggered.connect(lambda checked=False, n=nid: learn_now_nid(n))
    
    grade_menu = menu.addMenu("Noter")
    for ease, label in [(1, "À revoir"), (2, "Difficile"), (3, "Correct"), (4, "Facile")]:
        action_grade = grade_menu.addAction(label)
        action_grade.triggered.connect(lambda checked=False, n=nid, e=ease: grade_now_nid(n, e))

    # Garder la popup / le menu actif pendant l'affichage
    if _is_active_dialog_alive():
        _active_dialog._context_menu_active = True
        menu.aboutToHide.connect(lambda: setattr(_active_dialog, '_context_menu_active', False))
    try:
        from .editor_assistant import _preview_popup as _ast_popup, _is_preview_popup_alive
        if _is_preview_popup_alive():
            _ast_popup._context_menu_active = True
            menu.aboutToHide.connect(lambda: setattr(_ast_popup, '_context_menu_active', False))
    except Exception:
        pass

    if mw.reviewer and mw.reviewer.web:
        try:
            mw.reviewer.web.eval("window._ednContextMenuActive = true;")
            def _clear_reviewer_ctx():
                from aqt.qt import QTimer
                QTimer.singleShot(300, lambda: mw.reviewer.web.eval("window._ednContextMenuActive = false;") if mw.reviewer and mw.reviewer.web else None)
            menu.aboutToHide.connect(_clear_reviewer_ctx)
        except Exception:
            pass

    # Filtre pour ignorer le relâchement initial du doigt après l'appui long
    from aqt.qt import QObject, QEvent
    class EdnTouchMenuFilter(QObject):
        def __init__(self, menu_obj):
            super().__init__(menu_obj)
            self.menu_obj = menu_obj
            import time
            self.show_time = time.time()
            self.initial_release_done = False

        def eventFilter(self, obj, event):
            try:
                import time
                elapsed = time.time() - self.show_time
                etype = event.type()
                if not self.initial_release_done and elapsed < 0.6:
                    if etype in (
                        QEvent.Type.MouseButtonRelease,
                        QEvent.Type.NonClientAreaMouseButtonRelease,
                        QEvent.Type.TouchEnd,
                        QEvent.Type.TouchCancel,
                    ):
                        self.initial_release_done = True
                        return True
                    if etype in (
                        QEvent.Type.MouseButtonPress,
                        QEvent.Type.MouseButtonDblClick,
                    ):
                        return True
            except Exception:
                pass
            return False

    touch_filter = EdnTouchMenuFilter(menu)
    menu.installEventFilter(touch_filter)
    grade_filter = EdnTouchMenuFilter(grade_menu)
    grade_menu.installEventFilter(grade_filter)

    if pos is not None:
        if isinstance(pos, QPoint):
            global_pos = webview.mapToGlobal(pos) if webview else pos
        elif isinstance(pos, (tuple, list)) and len(pos) >= 2:
            pt = QPoint(int(pos[0]), int(pos[1]))
            global_pos = webview.mapToGlobal(pt) if webview else pt
        else:
            global_pos = QCursor.pos()
    else:
        global_pos = QCursor.pos()

    menu.exec(global_pos)

def on_webview_will_show_context_menu(webview, menu) -> None:
    global _last_right_clicked_nid, _right_clicked_on_preview_page
    
    # Capture current state and clear globals immediately
    nid = _last_right_clicked_nid
    page_clicked = _right_clicked_on_preview_page
    
    _last_right_clicked_nid = None
    _right_clicked_on_preview_page = False
    
    is_assistant = False
    try:
        from .editor_assistant import get_active_editor
        editor = get_active_editor()
        if editor and getattr(editor, '_editor_assistant_web', None) == webview:
            is_assistant = True
    except Exception:
        pass

    if is_assistant:
        menu.clear()
        
    is_preview_popup = False

    # 1. If we have a clicked badge/hover nid
    if nid:
        pass
    # 2. Check if inside search dialog's preview popup
    elif _is_active_dialog_alive() and webview == getattr(_active_dialog, '_preview_web', None):
        nid = getattr(_active_dialog, '_preview_current_nid', None)
        is_preview_popup = True
        _active_dialog._context_menu_active = True
        menu.aboutToHide.connect(lambda: setattr(_active_dialog, '_context_menu_active', False))
    # 3. Check if inside sidebar/mirror assistant preview popup
    else:
        try:
            from .editor_assistant import _preview_popup as _ast_popup, _is_preview_popup_alive, _current_preview_nid
            if _is_preview_popup_alive() and hasattr(_ast_popup, '_edn_web') and webview == _ast_popup._edn_web:
                nid = _current_preview_nid
                is_preview_popup = True
                _ast_popup._context_menu_active = True
                menu.aboutToHide.connect(lambda: setattr(_ast_popup, '_context_menu_active', False))
        except Exception:
            pass
    # 4. Check if inside standard Browser Previewer window
    if not nid and (page_clicked or "Previewer" in webview.window().__class__.__name__):
        win = webview.window()
        card = win.card() if (hasattr(win, "card") and callable(win.card)) else getattr(win, "card", None)
        if card:
            nid = card.nid

    if not nid:
        return
        
    if not is_assistant:
        menu.addSeparator()

    action_open = menu.addAction("Ouvrir dans l'explorateur")
    action_open.triggered.connect(lambda checked=False, n=nid: open_nid_in_browser(n))
    menu.addSeparator()
        
    if not is_assistant and not is_preview_popup and webview.__class__.__name__ == "EditorWebView":
        action_hyperlink = menu.addAction("Hyperliens")
        action_hyperlink.triggered.connect(lambda checked=False, n=nid, t=_last_right_clicked_default_text, w=webview: prompt_modify_hyperlink(n, t, w))
        menu.addSeparator()
        
    if is_preview_popup:
        try:
            from .editor_assistant import get_active_editor
            if get_active_editor():
                action_link = menu.addAction("Lier cette carte")
                action_link.triggered.connect(lambda checked=False, n=nid: link_card_to_active_editor(n))
                menu.addSeparator()
        except Exception:
            pass

    action_learn = menu.addAction("Apprendre maintenant")
    action_learn.triggered.connect(lambda checked=False, n=nid: learn_now_nid(n))
    
    grade_menu = menu.addMenu("Noter")
    for ease, label in [(1, "À revoir"), (2, "Difficile"), (3, "Correct"), (4, "Facile")]:
        action_grade = grade_menu.addAction(label)
        action_grade.triggered.connect(lambda checked=False, n=nid, e=ease: grade_now_nid(n, e))

    from aqt.qt import QObject, QEvent
    class EdnTouchMenuFilter(QObject):
        def __init__(self, menu_obj):
            super().__init__(menu_obj)
            self.menu_obj = menu_obj
            import time
            self.show_time = time.time()
            self.initial_release_done = False

        def eventFilter(self, obj, event):
            try:
                import time
                elapsed = time.time() - self.show_time
                etype = event.type()
                if not self.initial_release_done and elapsed < 0.6:
                    if etype in (
                        QEvent.Type.MouseButtonRelease,
                        QEvent.Type.NonClientAreaMouseButtonRelease,
                        QEvent.Type.TouchEnd,
                        QEvent.Type.TouchCancel,
                    ):
                        self.initial_release_done = True
                        return True
                    if etype in (
                        QEvent.Type.MouseButtonPress,
                        QEvent.Type.MouseButtonDblClick,
                    ):
                        return True
            except Exception:
                pass
            return False

    touch_filter = EdnTouchMenuFilter(menu)
    menu.installEventFilter(touch_filter)
    grade_filter = EdnTouchMenuFilter(grade_menu)
    grade_menu.installEventFilter(grade_filter)
