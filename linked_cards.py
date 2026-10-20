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

import traceback
from .logger import log, log_error, perf_log
from .edn_menu import register_module, register_action, get_shortcut

import importlib
try:
    _better_search_on_text_change = importlib.import_module("1052724801.new.onTextChange")
    better_search_on_text_change = _better_search_on_text_change.onSearchEditTextChange
except Exception:
    better_search_on_text_change = None

from .edn_editor_compat import get_editor_note, editor_save_now, editor_reload_note

# Global reference
_current_editor = None
_active_dialog = None

def _profiler_log(category: str, message: str, elapsed_ms: Optional[float] = None, caller: Optional[str] = None):
    try:
        import sys
        profiler = sys.modules.get("00_EDN_Debug_Profiler")
        if profiler and hasattr(profiler, "log"):
            profiler.log(category, message, elapsed_ms=elapsed_ms, caller=caller or "LinkedCards")
            return
    except Exception:
        pass
    try:
        from .logger import log
        ms_str = f" | took {elapsed_ms:.2f}ms" if elapsed_ms is not None else ""
        c_str = f" [{caller}]" if caller else ""
        log(f"[{category}]{c_str} {message}{ms_str}")
    except Exception:
        pass

def _get_editor_note(editor):
    return get_editor_note(editor)

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
    """Bascule le panneau miroir pour l'éditeur courant (depuis le menu EDN ou raccourci global)."""
    try:
        from .editor_assistant import get_active_editor
        editor = get_active_editor()
        if not editor:
            active_win = mw.app.activeWindow() if (hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow')) else None
            if active_win and hasattr(active_win, 'editor') and active_win.editor:
                editor = active_win.editor
        if not editor:
            from aqt import dialogs
            b_entry = dialogs._dialogs.get("Browser")
            if isinstance(b_entry, (list, tuple)) and len(b_entry) > 1 and b_entry[1]:
                b = b_entry[1]
                if hasattr(b, 'editor') and b.editor:
                    editor = b.editor
        if editor:
            try:
                _focus_liens_field(editor)
            except NameError:
                from .editor_assistant import toggle_assistant
                toggle_assistant(editor)
        else:
            from aqt.utils import tooltip
            tooltip("Aucun éditeur actif. Ouvrez d'abord une carte en édition.")
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
    
    pattern_span = r'<span\s+[^>]*class="edn-desc"[^>]*>(?:<br\s*/?>|[^<])*</span>' + space_pattern + dash_pattern + space_pattern + r'(<kbd\s+[^>]*data-nid="' + re.escape(target_nid_str) + r'"[^>]*>)'
    pattern_text = r'([^<>\n]*?)' + space_pattern + dash_pattern + space_pattern + r'(<kbd\s+[^>]*data-nid="' + re.escape(target_nid_str) + r'"[^>]*>)'
    
    for i in range(len(note.fields)):
        content = note.fields[i]
        if target_nid_str in content:
            new_content = _move_br_outside_edn_desc(content)
            if re.search(pattern_span, new_content):
                new_content = re.sub(pattern_span, lambda m: f'<span class="edn-desc">{new_desc_escaped}</span>&nbsp;—&nbsp;{m.group(1)}', new_content)
            else:
                new_content = re.sub(pattern_text, lambda m: f'<span class="edn-desc">{new_desc_escaped}</span>&nbsp;—&nbsp;{m.group(2)}', new_content)
            if new_content != content:
                note.fields[i] = new_content
                modified = True
    return modified

def _is_note_linked_in_target(source_note, target_note) -> bool:
    """Vérifie si la carte source (note A) est déjà liée dans la carte cible (note B).
    Prend en compte :
    - Le Note ID (nid) de source_note
    - Tous les Card IDs (cid) des cartes associées à source_note
    - Les champs actuels de target_note (y compris si target_note est ouverte dans un éditeur)
    - Les NIDs/CIDs extraits via extract_nids_from_fields_list
    """
    if not source_note or not target_note:
        return False
    try:
        source_ids = {str(source_note.id)}
        try:
            for c in source_note.cards():
                source_ids.add(str(c.id))
        except Exception:
            pass

        target_fields = list(target_note.fields)
        try:
            from aqt import mw
            if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow'):
                win = mw.app.activeWindow()
                if hasattr(win, 'editor') and win.editor:
                    ed_note = _get_editor_note(win.editor)
                    if ed_note and getattr(ed_note, 'id', None) == target_note.id:
                        target_fields = list(ed_note.fields)
        except Exception:
            pass

        # 1. Présence directe d'un des IDs de la source dans les champs de la cible
        for f in target_fields:
            for sid in source_ids:
                if sid in f:
                    return True

        # 2. Analyse des NIDs extraits
        try:
            from .editor_assistant import extract_nids_from_fields_list
            target_nids = extract_nids_from_fields_list(target_fields)
            if any(sid in target_nids for sid in source_ids):
                return True
            for tn in target_nids:
                try:
                    c = mw.col.get_card(int(tn))
                    if c and c.nid == source_note.id:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
    except Exception as e:
        log(f"Error in _is_note_linked_in_target: {e}")
    return False

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
                target_note = None
                try:
                    target_note = mw.col.get_note(target_nid)
                except Exception:
                    try:
                        card = mw.col.get_card(target_nid)
                        if card:
                            target_note = card.note()
                    except Exception:
                        pass
                if not target_note:
                    continue
                # Check if it already has a link to source_nid
                if _is_note_linked_in_target(note, target_note):
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

        # 3. Propose to mirror links (différé de 120ms pour laisser AddCards exécuter setFields/focusField(0)
        # de la carte n+1 avant que la boîte de dialogue modale ne prenne le focus et ne déclenche un blur)
        def _show_mirror_dialogs():
            try:
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
                        # Utiliser le label validé dans la dialog (custom_recto) pour le badge miroir dans la cible
                        link_html = make_card_link(source_nid, custom_recto)
                        current_field_content = target_note.fields[fidx].strip()
                        if not current_field_content or current_field_content == '<br>':
                            target_note.fields[fidx] = link_html
                        else:
                            target_note.fields[fidx] = current_field_content + '<br>' + link_html
                        mw.col.update_note(target_note)

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

                                        # Add C's badge to B
                                        link_c = make_card_link(nid_c, recto_c)
                                        current_field_b = note_b.fields[fidx_b].strip()
                                        if not current_field_b or current_field_b == '<br>':
                                            note_b.fields[fidx_b] = link_c
                                        else:
                                            note_b.fields[fidx_b] = current_field_b + '<br>' + link_c
                                        mw.col.update_note(note_b)

                                        # Add B's badge to C
                                        link_b = make_card_link(nid_b, recto_b)
                                        current_field_c = note_c.fields[fidx_c].strip()
                                        if not current_field_c or current_field_c == '<br>':
                                            note_c.fields[fidx_c] = link_b
                                        else:
                                            note_c.fields[fidx_c] = current_field_c + '<br>' + link_b
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
                log(f"Error in _show_mirror_dialogs: {e}")

        QTimer.singleShot(120, _show_mirror_dialogs)

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
    if hasattr(gui_hooks, 'editor_will_load_note'):
        gui_hooks.editor_will_load_note.append(_on_editor_will_load_note)
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


def make_clickable_card_badge(nid: Any, custom_text: Optional[str] = None) -> str:
    """Génère le HTML d'un badge <kbd> pour une carte liée.
    Comprend les styles inline complets pour garantir un rendu parfait
    y compris à l'intérieur du Shadow DOM des champs Svelte d'Anki.
    """
    safe_nid = str(nid).strip()
    if custom_text:
        safe_custom = (
            str(custom_text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;')
        )
        display_content = f'<span class="edn-nid" style="display:none;font-size:0;line-height:0">{safe_nid}</span>{safe_custom}'
    else:
        display_content = f'<span class="edn-nid">{safe_nid}</span>'

    return f'<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="{safe_nid}">{display_content}</kbd>'


def make_card_link(nid: Any, recto: Optional[str] = None) -> str:
    """Génère le HTML propre d'un lien de carte (titre + tiret + badge)."""
    safe_nid = str(nid).strip()
    badge = make_clickable_card_badge(safe_nid)
    if recto is None:
        return badge
    elif recto == "":
        return f'&nbsp;—&nbsp;{badge}'
    else:
        recto_escaped = (
            str(recto)
            .replace('&', '&amp;')
            .replace('"', '&quot;')
            .replace("'", '&#39;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
        )
        return f'<span class="edn-desc">{recto_escaped}</span>&nbsp;—&nbsp;{badge}'


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
    kbd_copy = get_shortcut("linked_cards_copy", "Ctrl+Alt+C")
    vars_js = (
        "<script type=\"text/javascript\">"
        "window._ednKbdOpenCfg = '" + kbd_open + "';"  
        "window._ednKbdPreviewCfg = '" + kbd_preview + "';"  
        "window._ednKbdNavigateCfg = '" + kbd_navigate + "';"  
        "window._ednKbdBackCfg = '" + kbd_back + "';"  
        "window._ednKbdCopyCfg = '" + kbd_copy + "';"  
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
            if (e._ednCardsCtHandled) return;
            var t = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
            if (!t) return;
            var oc = (t.getAttribute('data-onclick') || '') + (t.getAttribute('onclick') || '');
            if (oc.indexOf('cards_ct_click') !== -1) return;

            // Extraire le NID
            var nid = '';
            if (t.dataset && t.dataset.nid) nid = t.dataset.nid;
            else {
                var s = t.querySelector && t.querySelector('.edn-nid');
                if (s) nid = s.textContent.trim();
                else nid = (t.innerText || '').trim().replace(/[^0-9]/g, '');
            }
            if (nid && nid.length > 9) {
                cards_ct_click(nid);
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
                    var clickAttr = el.getAttribute('data-onclick').trim().replace(/;\s*$/, '');
                    // Match toggle('...') or toggle("...")
                    var toggleMatch = clickAttr.match(/^toggle\(\s*['"]?([^'"]*?)['"]?\s*\)$/);
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
                        var ctMatch = clickAttr.match(/^cards_ct_click\(\s*['"]?([a-zA-Z0-9_-]+)['"]?(?:\s*,\s*(true|false))?\s*\)$/);
                        if (ctMatch) {
                            e._ednCardsCtHandled = true;
                            var fnCt = window.cards_ct_click || (typeof cards_ct_click !== 'undefined' ? cards_ct_click : null);
                            if (fnCt) {
                                try {
                                    if (ctMatch[2]) {
                                        fnCt(ctMatch[1], ctMatch[2] === 'true');
                                    } else {
                                        fnCt(ctMatch[1]);
                                    }
                                } catch(err) {
                                    console.error("Error executing cards_ct_click:", err);
                                }
                            }
                        } else {
                            console.warn("Blocked non-whitelisted data-onclick:", clickAttr);
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
                    var dblAttr = el.getAttribute('data-ondblclick').trim().replace(/;\s*$/, '');
                    var clickMatch = dblAttr.match(/^cards_ct_click\(\s*['"]?([a-zA-Z0-9_-]+)['"]?(?:\s*,\s*(true|false))?\s*\)$/);
                    if (clickMatch) {
                        var nid = clickMatch[1];
                        var fnCt = window.cards_ct_click || (typeof cards_ct_click !== 'undefined' ? cards_ct_click : null);
                        if (fnCt) {
                            try {
                                fnCt(nid);
                            } catch(err) {
                                console.error("Error executing cards_ct_click:", err);
                            }
                        }
                    } else {
                        console.warn("Blocked non-whitelisted data-ondblclick:", dblAttr);
                    }
                    break;
                }
                el = el.parentNode;
            }
        }, true);
    }
})();
window._ednLastClickTime = 0;
window._ednLastClickNid = null;
function cards_ct_click(nid) {
    if (window._ednTouchRecently) return;
    var cleanNid = String(nid || '').trim().replace(/[^0-9]/g, '');
    if (!cleanNid || cleanNid.length < 9) return;
    var now = Date.now();
    if (window._ednLastClickNid === cleanNid && (now - window._ednLastClickTime < 1000)) {
        return;
    }
    window._ednLastClickTime = now;
    window._ednLastClickNid = cleanNid;
    if(typeof pycmd !== 'undefined') pycmd("cards_ct_click" + cleanNid);
    else if (typeof bridgeCommand !== 'undefined') bridgeCommand("cards_ct_click" + cleanNid);
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

            function isEmptyInlineNode(node) {
                if (!node) return false;
                if (node.nodeType === Node.TEXT_NODE) {
                    return node.textContent.replace(/\u00A0/g, ' ').trim() === "";
                }
                if (node.nodeType === Node.ELEMENT_NODE) {
                    if (node.nodeName === 'BR' || node.nodeName === 'DIV' || node.nodeName === 'P' || node.nodeName === 'KBD' || node.nodeName === 'IMG' || node.nodeName === 'HR') {
                        return false;
                    }
                    if (node.querySelector && node.querySelector('kbd, img, br, div, p')) {
                        return false;
                    }
                    return (node.textContent || "").replace(/\u00A0/g, ' ').trim() === "";
                }
                return false;
            }

            var child = container.firstChild;
            while (child) {
                if (isEmptyInlineNode(child)) {
                    child = child.nextSibling;
                } else if (child.nodeName === 'BR') {
                    var next = child.nextSibling;
                    child.parentNode.removeChild(child);
                    child = next;
                } else {
                    break;
                }
            }

            var blocks = [container].concat(Array.from(container.querySelectorAll('div')));
            blocks.forEach(function(block) {
                var lastChild = block.lastChild;
                while (lastChild) {
                    if (isEmptyInlineNode(lastChild)) {
                        lastChild = lastChild.previousSibling;
                    } else if (lastChild.nodeName === 'BR') {
                        var prev = lastChild.previousSibling;
                        lastChild.parentNode.removeChild(lastChild);
                        lastChild = prev;
                    } else {
                        break;
                    }
                }
            });

            var kbds = container.querySelectorAll('kbd.clickable_cards');
            kbds.forEach(function(kbd) {
                var next = kbd.nextSibling;
                var insertBeforeNode = next;
                while (next && isEmptyInlineNode(next)) {
                    next = next.nextSibling;
                    insertBeforeNode = next;
                }
                if (next && next.nodeName !== "BR" && next.nodeName !== "DIV" && next.nodeName !== "P" && next.nodeName !== "KBD") {
                    var br = document.createElement('br');
                    kbd.parentNode.insertBefore(br, insertBeforeNode);
                }
            });
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
        var kbdCopy = window._ednKbdCopyCfg || 'Ctrl+Alt+C';
        var isCopyNid = false;
        if (kbdCopy && kbdCopy.toLowerCase() === 'ctrl+alt+c') {
            isCopyNid = (e.ctrlKey || e.metaKey) && e.altKey && (e.key === 'c' || e.key === 'C');
        } else if (kbdCopy) {
            var parts = kbdCopy.toLowerCase().split('+').map(function(s) { return s.trim(); });
            var keyTarget = parts[parts.length - 1];
            var wantCtrl = parts.indexOf('ctrl') !== -1;
            var wantAlt = parts.indexOf('alt') !== -1;
            var wantShift = parts.indexOf('shift') !== -1;
            isCopyNid = (wantCtrl === (e.ctrlKey || e.metaKey)) && (wantAlt === e.altKey) && (wantShift === e.shiftKey) && (e.key.toLowerCase() === keyTarget);
        }
        if (isCopyNid) {
            e.preventDefault();
            e.stopImmediatePropagation();
            if (typeof pycmd !== 'undefined') pycmd('edn_copy_nid_reviewer');
            else if (typeof bridgeCommand !== 'undefined') bridgeCommand('edn_copy_nid_reviewer');
            return;
        }

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
                    cards_ct_click(nid);
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
        if (nid) {
            cards_ct_click(nid);
        }
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
        if (active.shadowRoot && typeof active.shadowRoot.getSelection === 'function') {
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
            
            function applyDualBorder(el, leftColor, rightColor) {
                if (!el) return;
                el.style.borderImage = "none";
                el.style.borderColor = "transparent";
                var bg = window.getComputedStyle(el).backgroundColor;
                if (!bg || bg === "transparent" || bg === "rgba(0, 0, 0, 0)") {
                    var isNight = document.body && (document.body.classList.contains("nightMode") || document.body.classList.contains("night_mode"));
                    bg = isNight ? "#373a41" : "#f5f6f7";
                }
                var existingBg = el.style.backgroundImage;
                if (existingBg && !existingBg.includes("linear-gradient")) {
                    el.style.backgroundImage = existingBg + ", linear-gradient(" + bg + ", " + bg + "), linear-gradient(to right, " + leftColor + " 50%, " + rightColor + " 50%)";
                } else {
                    el.style.backgroundImage = "linear-gradient(" + bg + ", " + bg + "), linear-gradient(to right, " + leftColor + " 50%, " + rightColor + " 50%)";
                }
                el.style.backgroundOrigin = "border-box";
                el.style.backgroundClip = "padding-box, border-box";
            }

            function clearDualBorder(el) {
                if (!el) return;
                el.style.borderImage = "none";
                el.style.backgroundOrigin = "";
                el.style.backgroundClip = "";
                var existingBg = el.style.backgroundImage;
                if (existingBg && existingBg.includes("linear-gradient")) {
                    el.style.backgroundImage = "";
                }
            }

            var dualFlags = prefs.hasOwnProperty('previewDualFlags') ? prefs.previewDualFlags : false;
            var sections = box.getElementsByClassName("section");
            var hiders = box.getElementsByClassName("barHider");
            var barsList = box.getElementsByClassName("bar");
            
            if (showFlags && dualFlags && virtualFlagColor && realColor) {
                for (var sIdx = 0; sIdx < sections.length; sIdx++) {
                    applyDualBorder(sections[sIdx], virtualFlagColor, realColor);
                }
                for (var hIdx = 0; hIdx < hiders.length; hIdx++) {
                    applyDualBorder(hiders[hIdx], virtualFlagColor, realColor);
                }
                for (var bIdx = 0; bIdx < barsList.length; bIdx++) {
                    clearDualBorder(barsList[bIdx]);
                    barsList[bIdx].style.borderColor = virtualFlagColor;
                }
            } else {
                var activeColor = showFlags ? (realColor || virtualFlagColor) : null;
                if (activeColor) {
                    for (var sIdx = 0; sIdx < sections.length; sIdx++) {
                        clearDualBorder(sections[sIdx]);
                        sections[sIdx].style.borderColor = activeColor;
                    }
                    for (var hIdx = 0; hIdx < hiders.length; hIdx++) {
                        clearDualBorder(hiders[hIdx]);
                        hiders[hIdx].style.borderColor = activeColor;
                    }
                    for (var bIdx = 0; bIdx < barsList.length; bIdx++) {
                        clearDualBorder(barsList[bIdx]);
                        barsList[bIdx].style.borderColor = activeColor;
                    }
                } else {
                    for (var sIdx = 0; sIdx < sections.length; sIdx++) {
                        clearDualBorder(sections[sIdx]);
                        sections[sIdx].style.borderColor = "";
                    }
                    for (var hIdx = 0; hIdx < hiders.length; hIdx++) {
                        clearDualBorder(hiders[hIdx]);
                        hiders[hIdx].style.borderColor = "";
                    }
                    for (var bIdx = 0; bIdx < barsList.length; bIdx++) {
                        clearDualBorder(barsList[bIdx]);
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
        
        # Supprimer les balises <br> en début de badge (ne JAMAIS les déplacer devant le badge)
        kbd_content = re.sub(r'^(\s*<br\s*/?>\s*)+', '', kbd_content, flags=re.IGNORECASE)
            
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
            
        return f'{kbd_open}{kbd_content}</kbd>{trailing_brs}'

    # Match <kbd class="clickable_cards"...>...</kbd>
    pattern = r'(<kbd\s+class="clickable_cards"[^>]*>)(.*?)(</kbd>)'
    return re.sub(pattern, replace_kbd, html_text, flags=re.DOTALL)


def _move_br_outside_edn_desc(html_text, clean_duplicate_br=False):
    """Déplace les <br> accidentellement insérés dans <span class="edn-desc"> vers l'extérieur."""
    if not html_text or 'edn-desc' not in html_text:
        return html_text
        
    def replace_desc(match):
        span_open = match.group(1)
        desc_content = match.group(2)
        
        # Extraire les <br> au début du span
        leading_br_pattern = r'^(?:(?:\s|&nbsp;|&#160;|\xa0)*<br\s*/?>\s*)+'
        leading_match = re.match(leading_br_pattern, desc_content, re.IGNORECASE)
        leading_brs = ""
        if leading_match:
            leading_brs = "<br>"
            desc_content = desc_content[leading_match.end():]
            
        # Extraire les <br> à la fin du span
        trailing_br_pattern = r'(?:(?:\s|&nbsp;|&#160;|\xa0)*<br\s*/?>\s*)+$'
        trailing_match = re.search(trailing_br_pattern, desc_content, re.IGNORECASE)
        trailing_brs = ""
        if trailing_match:
            trailing_brs = "<br>"
            desc_content = desc_content[:trailing_match.start()]
            
        # Remplacer d'éventuels <br> résiduels internes par un espace
        desc_content = re.sub(r'<br\s*/?>', ' ', desc_content, flags=re.IGNORECASE)
        return f'{leading_brs}{span_open}{desc_content}</span>{trailing_brs}'
        
    pattern = r'(<span\s+[^>]*class=["\'][^"\']*edn-desc[^"\']*["\'][^>]*>)(.*?)(</span>)'
    html_text = re.sub(pattern, replace_desc, html_text, flags=re.DOTALL | re.IGNORECASE)
    # Nettoyer les doublons de br consécutifs créés seulement si l'option est activée
    if clean_duplicate_br:
        html_text = re.sub(r'(?:<br\s*/?>\s*)+(?=<br\s*/?>)', '', html_text)
    return html_text


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

    try:
        from .edn_menu.card_styles import load_prefs
        prefs = load_prefs()
    except Exception:
        try:
            from edn_menu.card_styles import load_prefs
            prefs = load_prefs()
        except Exception:
            prefs = {}

    clean_duplicate_br = prefs.get("cleanDuplicateLineBreaks", True)
    auto_lb_linked = prefs.get("autoLineBreakLinkedCards", True)

    # 0. Déplacer les <br> à l'intérieur des <kbd> et <span class="edn-desc"> vers l'extérieur
    if auto_lb_linked:
        html_text = _move_br_outside_kbd(html_text)
        html_text = _move_br_outside_edn_desc(html_text, clean_duplicate_br=clean_duplicate_br)
        
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
                return make_card_link(nid, text_outside)
            else:
                return make_clickable_card_badge(nid)
        return match.group(0)

    # Aplatir récursivement les kbd imbriqués
    html_text = re.sub(r'<kbd\s+class="clickable_cards"[^>]*>(.*?)</kbd>', flatten_nested, html_text, flags=re.DOTALL)
    
    # 3. Supprimer les <kbd> vides résiduels (après nettoyage des imbriqués)
    html_text = re.sub(empty_kbd, '', html_text)
    
    # 4. Nettoyer les <br> orphelins consécutifs SEULEMENT si l'option est activée
    if clean_duplicate_br:
        html_text = re.sub(r'(?:<br\s*/?>\s*)+(?=<br\s*/?>)', '', html_text)
    
    # 5. Nettoyer les <br> accidentels situés autour du tiret et du badge <kbd> si saut auto activé
    if auto_lb_linked:
        html_text = re.sub(
            r'([—–-](?:&nbsp;|\s)*)<br\s*/?>\s*(<kbd\s+class="clickable_cards")',
            r'\1\2',
            html_text,
            flags=re.IGNORECASE
        )
        html_text = re.sub(
            r'<br\s*/?>\s*([—–-](?:&nbsp;|\s)*<kbd\s+class="clickable_cards")',
            r'&nbsp;\1',
            html_text,
            flags=re.IGNORECASE
        )
        # Nettoyer les <br> accidentels placés immédiatement avant un badge incrusté dans un mot (ex: l'<br><kbd...)
        html_text = re.sub(
            r'([a-zA-Z0-9\u00C0-\u017F\'’])<br\s*/?>\s*(<kbd\s+class="clickable_cards")',
            r'\1\2',
            html_text,
            flags=re.IGNORECASE
        )

        # 6. Rapatrier les badges <kbd> orphelins placés juste après la fermeture d\'un bloc <div> ou <p>
        html_text = re.sub(
            r'(<div[^>]*>.*?)(?:&nbsp;|\s)*</div>\s*(<kbd\s+class="clickable_cards"[^>]*>.*?</kbd>(?:&nbsp;|\s)*)',
            r'\1&nbsp;\2</div>',
            html_text,
            flags=re.IGNORECASE | re.DOTALL
        )
        html_text = re.sub(
            r'(<p[^>]*>.*?)(?:&nbsp;|\s)*</p>\s*(<kbd\s+class="clickable_cards"[^>]*>.*?</kbd>(?:&nbsp;|\s)*)',
            r'\1&nbsp;\2</p>',
            html_text,
            flags=re.IGNORECASE | re.DOTALL
        )
    
    return html_text

def on_card_render(output, context):
    script = build_add_to_card_script()
    # Nettoyer les badges <kbd> vides et imbriqués UNIQUEMENT si des liens sont présents dans le contenu
    if output.question_text and ('clickable_cards' in output.question_text or 'edn-desc' in output.question_text):
        output.question_text = _clean_kbd_html(output.question_text)
    if output.answer_text and ('clickable_cards' in output.answer_text or 'edn-desc' in output.answer_text):
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
    
    # NOTE: On n'installe PAS de QShortcut ici pour Ctrl+Alt+C.
    # Ce raccourci global est déjà installé sur mw en tant que QAction
    # (ApplicationShortcut) via register_action_shortcut_only().
    # Ajouter un QShortcut ici créerait un conflit d'ambiguïté Qt sur mw
    # qui désactiverait complètement le raccourci en révision.


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
    """Invalide le cache de la note quand l'utilisateur quitte un champ modifié dans l'éditeur et nettoie tout marker résiduel."""
    try:
        if note and getattr(note, 'fields', None):
            for i, f in enumerate(note.fields):
                cleaned_val = f
                if 'edn-cursor-marker' in cleaned_val:
                    cleaned_val = re.sub(r'<span\s+id=["\']edn-cursor-marker["\'][^>]*>.*?</span>', '', cleaned_val, flags=re.IGNORECASE | re.DOTALL)
                if 'clickable_cards' in cleaned_val:
                    cleaned_val = _clean_kbd_html(cleaned_val)
                if cleaned_val != f:
                    note.fields[i] = cleaned_val
        if note and getattr(note, 'id', None):
            clear_hover_preview_cache(note.id)
            try:
                from .editor_assistant import clear_assistant_preview_cache, refresh_active_preview_if_open
                clear_assistant_preview_cache(note.id)
                refresh_active_preview_if_open(note.id)
            except Exception:
                pass
    except Exception as e:
        log(f"_on_editor_unfocus_field error: {e}")
    # IMPORTANT: Ne JAMAIS forcer changed = True ici.
    # Dans Anki, si editor_did_unfocus_field retourne True, Anki déclenche un timer
    # loadNoteKeepingFocus() qui réinitialise le focus sur le premier champ et interrompt
    # la saisie en cours.
    return changed


def _on_editor_typing_timer(note_or_editor):
    """Invalide le cache de la note en cours d'édition pendant la frappe."""
    try:
        nid = getattr(note_or_editor, 'id', None) or getattr(note_or_editor, 'nid', None)
        if not nid:
            ed_note = _get_editor_note(note_or_editor)
            if ed_note:
                nid = getattr(ed_note, 'id', None)
        if nid:
            clear_hover_preview_cache(nid)
            try:
                from .editor_assistant import clear_assistant_preview_cache
                clear_assistant_preview_cache(nid)
            except Exception:
                pass
    except Exception:
        pass


def _on_editor_will_load_note(js: str, note, editor) -> str:
    """Signale au JS que l'éditeur charge une nouvelle note ou change de note pour autoriser la remise à zéro des champs et focusField(0)."""
    try:
        is_new_note = bool(getattr(editor, "_edn_loading_new_note", False))
        if not is_new_note and editor and getattr(editor, "addMode", False) and not getattr(note, "id", 0):
            if "focusField(0)" in (js or ""):
                is_new_note = True
        if is_new_note and isinstance(js, str):
            prefix = (
                "window._ednLoadingNewNote = true; "
                "window._ednNewNoteLoadTime = Date.now(); "
                "window._ednLastInputTime = 0; "
                "window._ednLastKeyTime = 0; "
                "window._ednLastActiveField = 0; "
                "window._ednSavedFieldIndex = 0; "
            )
            return prefix + js
    except Exception:
        pass
    return js


def _on_editor_load_note(editor):
    """Invalide le cache hover lors du chargement de la note et nettoie les markers orphelins persistés sans recharger l'éditeur."""
    try:
        note = _get_editor_note(editor)
        if editor and note and getattr(note, 'id', None):
            if getattr(note, 'fields', None):
                for i, f in enumerate(note.fields):
                    cleaned_val = f
                    if 'edn-cursor-marker' in cleaned_val:
                        cleaned_val = re.sub(r'<span\s+id=["\']edn-cursor-marker["\'][^>]*>.*?</span>', '', cleaned_val, flags=re.IGNORECASE | re.DOTALL)
                    if 'clickable_cards' in cleaned_val:
                        cleaned_val = _clean_kbd_html(cleaned_val)
                    if cleaned_val != f:
                        note.fields[i] = cleaned_val
            clear_hover_preview_cache(note.id)
            try:
                from .editor_assistant import clear_assistant_preview_cache
                clear_assistant_preview_cache(note.id)
            except Exception:
                pass
    except Exception:
        pass


def _get_virtual_flag_color(note, cards=None, prefs=None) -> str:
    """Détermine la couleur du drapeau virtuel (leech, suspendue, new, ou '' sinon)."""
    try:
        if prefs is None:
            from .edn_menu.card_styles import load_prefs
            prefs = load_prefs()
        show_flags = prefs.get("showFlagBorders", True)
        if not show_flags:
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

        _color_leech = _resolve_flag_color(prefs.get("virtual_flag_color_leech"), "#DD6B20")
        _color_new = _resolve_flag_color(prefs.get("virtual_flag_color_new"), "#3182CE")
        _color_sus = _resolve_flag_color(prefs.get("virtual_flag_color_suspended"), "#E53E3E")

        if cards is None and note is not None:
            try:
                cards = note.cards()
            except Exception:
                cards = []

        if prefs.get("flagAgainEnabled", False) and cards:
            again_threshold = prefs.get("flagAgainCount", 7)
            if any(getattr(c, 'lapses', 0) >= again_threshold for c in cards):
                return _color_leech

        if not cards:
            return ""

        any_suspended = any(c.queue == -1 for c in cards)
        all_new = all(c.type == 0 and c.queue >= 0 for c in cards)
        if any_suspended:
            return _color_sus
        elif all_new and prefs.get("virtualBlueFlagForNewCards", False):
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

[id*="edn-cursor-marker"],
#edn-cursor-marker {
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
    padding-top: 0 !important;
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

[id*="edn-cursor-marker"], .items > span[id*="edn-cursor-marker"] {
    display: none !important;
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
    transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
    background: #fffbf5 !important;
    color: #b45309 !important;
    border: 1px solid #e5c98e !important;
    border-radius: 4px !important;
    box-shadow: none !important;
}

.clickable_cards:hover {
    background: #f5f3ff !important;
    color: #7c3aed !important;
    border-color: #a78bfa !important;
}

.items.cartesLiees {
    flex-direction: row !important;
    flex-wrap: wrap !important;
    align-items: center !important;
    justify-content: flex-start !important;
    margin-left: 0 !important;
}

.cartesLiees br, .items.cartesLiees br {
    display: inline !important;
    content: normal !important;
    margin: 0 !important;
    padding: 0 !important;
    height: auto !important;
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

_last_open_browser_time: float = 0.0
_last_open_browser_nid: str = ""

def open_card_in_browser(nid: Any) -> bool:
    global _last_open_browser_time, _last_open_browser_nid
    import time
    from aqt import dialogs, mw

    nid_str = str(nid).strip().replace("cards_ct_click", "").strip(" :()\"'")
    if not nid_str or not nid_str.isdigit():
        return False

    now = time.time()
    # Anti-rebond: même carte demandée dans les 1.5s ou n'importe quelle ouverture dans les 0.4s
    if (nid_str == _last_open_browser_nid and (now - _last_open_browser_time) < 1.5) or ((now - _last_open_browser_time) < 0.4):
        return True

    _last_open_browser_time = now
    _last_open_browser_nid = nid_str

    try:
        browser = dialogs.open("Browser", mw)
        if hasattr(browser, "search_for"):
            browser.search_for(f"nid:{nid_str}")
        if hasattr(browser, "activateWindow"):
            browser.activateWindow()
        if hasattr(browser, "raise_"):
            browser.raise_()
        return True
    except Exception as e:
        log(f"Erreur open_card_in_browser({nid_str}): {e}")
        return False

@perf_log
def on_js_message_reviewer(handled, message, context):
    if not isinstance(message, str):
        return handled
    if handled and handled[0]:
        return handled
    if message == "edn_copy_nid_reviewer":
        copy_nid_from_active_browser()
        return (True, None)
    if message.startswith("cards_ct_click"):
        if isinstance(context, CardLayout):
            tooltip("Liens désactivés dans l'éditeur de type.")
            return (True, None)
        nid = message.replace("cards_ct_click", "").strip(" :()\"'")
        open_card_in_browser(nid)
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
                        
                        # Nettoyer les marqueurs de curseur résiduels
                        isolated = re.sub(r'<span\s+id=["\']edn-cursor-marker["\'][^>]*>.*?</span>', '', isolated, flags=re.IGNORECASE | re.DOTALL)

                        # Forcer l'affichage SEULEMENT des sections (pour éviter de révéler les métadonnées/licences cachées)
                        isolated = re.sub(r'(<div[^>]*class=["\'][^"\']*section[^"\']*["\'][^>]*style=["\'])display:\s*none;?([^"\']*["\'])', r'\1display: flex !important;\2', isolated, flags=re.IGNORECASE)

                        # Nettoyer les balises <hr> et compresser les sauts de ligne multiples (bug de gestion de l'espace)
                        isolated = re.sub(r'<hr[^>]*>', '', isolated, flags=re.IGNORECASE)
                        isolated = re.sub(r'(?:<br\s*/?>\s*){2,}', '<br>', isolated, flags=re.IGNORECASE)

                        # Compresser et nettoyer les <br> orphelins ou inutiles dans cartesLiees
                        def _clean_cartes_liees(match):
                            inner = match.group(2)
                            inner = re.sub(r'<span\s+id=["\']edn-cursor-marker["\'][^>]*>.*?</span>', '', inner, flags=re.IGNORECASE | re.DOTALL)
                            # Supprimer les br consécutifs ou séparés par des espaces / nbsp
                            inner = re.sub(r'(?:\s*&nbsp;\s*)*<br\s*/?>\s*(?:&nbsp;\s*)*', '<br>', inner, flags=re.IGNORECASE)
                            inner = re.sub(r'(?:\s*<br\s*/?>\s*)+', '<br>', inner, flags=re.IGNORECASE)
                            # Supprimer les br au tout début
                            inner = re.sub(r'^\s*(?:&nbsp;|<br\s*/?>\s*)+', '', inner, flags=re.IGNORECASE)
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
    
    action_copy = QAction("Copier NID", browser)
    action_copy.triggered.connect(lambda: _copy_nid_smart(browser))
    
    action_search = QAction("Rechercher Liens", browser)
    action_search.triggered.connect(lambda: _open_search_smart(browser))
    
    action_focus_liens = QAction("Aller au champ Liens", browser)
    action_focus_liens.triggered.connect(lambda: _focus_liens_browser(browser))
    
    # Save reference for context menu
    browser.edn_copy_nid_action = action_copy
    browser.edn_search_action = action_search
    
    # Add to browser window (no shortcut on QAction to avoid conflicts)
    browser.addAction(action_copy)
    browser.addAction(action_search)
    browser.addAction(action_focus_liens)
    
    # Les raccourcis globaux (Ctrl+Alt+C, Ctrl+Alt+L, Ctrl+Alt+B) sont installés sur mw
    # (ApplicationShortcut). Aucun QShortcut local n'est ajouté sur browser pour éviter
    # les conflits d'ambiguïté Qt (qui désactiveraient les deux raccourcis).
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
    if hasattr(browser, 'editor') and browser.editor and _get_editor_note(browser.editor):
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
        lines_html.append(make_card_link(nid, recto))
        
    text_content = "\n".join(lines_text)
    html_content = "<br>".join(lines_html)
    
    mime = QMimeData()
    mime.setText(text_content)
    mime.setHtml(html_content)
    QApplication.clipboard().setMimeData(mime)

    try:
        import sys
        interceptor_mod = sys.modules.get("anki_import_diff.src.temp_link_interceptor")
        if interceptor_mod:
            interceptor = interceptor_mod.TempLinkInterceptor.instance()
            if interceptor.is_active():
                for c_nid, _ in unique_nids:
                    interceptor.register_copied_nid(c_nid)
    except Exception:
        pass
    
    if len(unique_nids) == 1:
        tooltip(f"NID copié: {lines_text[0]}")
    else:
        tooltip(f"{len(unique_nids)} NIDs copiés")

def copy_nid_from_editor(editor: Editor):
    note = _get_editor_note(editor)
    if note and getattr(note, 'id', None):
        nid = str(note.id)
        recto = ""
        try:
            recto = get_note_recto(note, 240)
        except:
            recto = "[Vide]"
        html = make_card_link(nid, recto)
        mime = QMimeData()
        mime.setText(f"{recto} — {nid}")
        mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)

        try:
            import sys
            interceptor_mod = sys.modules.get("anki_import_diff.src.temp_link_interceptor")
            if interceptor_mod:
                interceptor = interceptor_mod.TempLinkInterceptor.instance()
                if interceptor.is_active():
                    interceptor.register_copied_nid(nid)
        except Exception:
            pass

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
            from aqt.qt import QTimer
            def _do_toggle():
                from .editor_assistant import toggle_assistant
                toggle_assistant(editor)
            # Laisser un court délai pour que les signaux IPC (blur/key) d'Anki
            # soient pleinement intégrés par Editor.onBridgeCmd avant d'ouvrir
            QTimer.singleShot(50, _do_toggle)
        editor_save_now(editor, on_saved)
    except Exception as e:
        log(f"toggle_assistant error: {e}")

def handle_editor_button(editor):
    """Smart Link: Direct link if selection is NID, else Search GUI."""
    editor.web.evalWithCallback("""
        (function() {
            function _getAllRoots(root) {
                root = root || document.body;
                var roots = [];
                if (!root) return roots;
                if (root.shadowRoot) roots.push(root.shadowRoot);
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < all.length; i++) {
                    if (all[i].shadowRoot) {
                        roots.push(all[i].shadowRoot);
                        roots.push(..._getAllRoots(all[i].shadowRoot));
                    }
                }
                return roots;
            }
            function _queryAll(sel) {
                if (typeof window._ednQueryAllRoots === 'function') return window._ednQueryAllRoots(sel);
                var res = Array.from(document.querySelectorAll(sel));
                var sRoots = _getAllRoots(document.body);
                for (var i = 0; i < sRoots.length; i++) {
                    var list = sRoots[i].querySelectorAll(sel);
                    for (var j = 0; j < list.length; j++) {
                        if (!res.includes(list[j])) res.push(list[j]);
                    }
                }
                return res;
            }

            // Nettoyer tout ancien marker
            _queryAll('[id="edn-cursor-marker"]').forEach(function(m) {
                try { m.parentNode.removeChild(m); } catch(e) {}
            });

            window._ednSavedRange = null;
            let textValue = '';
            let isFullField = false;
            let active = document.activeElement;
            let sel = (typeof window._ednGetEditorSelection === 'function')
                ? window._ednGetEditorSelection()
                : null;

            if (!sel) {
                if (active && active.shadowRoot && active.shadowRoot.getSelection) {
                    var s = active.shadowRoot.getSelection();
                    if (s && s.rangeCount > 0) sel = s;
                }
                if (!sel) {
                    var sRoots = _getAllRoots(document.body);
                    for (var hi = 0; hi < sRoots.length; hi++) {
                        var sr = sRoots[hi];
                        if (sr.getSelection) {
                            var s2 = sr.getSelection();
                            if (s2 && s2.rangeCount > 0) { sel = s2; break; }
                        }
                    }
                }
                if (!sel) sel = window.getSelection();
            }

            // Sauvegarder l'index du champ actif pour restauration
            var curFieldIdx = (typeof window._ednResolveActiveFieldIndex === 'function')
                ? window._ednResolveActiveFieldIndex()
                : -1;
            if (curFieldIdx >= 0) {
                window._ednSavedFieldIndex = curFieldIdx;
                window._ednLastActiveField = curFieldIdx;
            } else if (typeof window._ednLastActiveField === 'number' && window._ednLastActiveField >= 0) {
                window._ednSavedFieldIndex = window._ednLastActiveField;
            }

            function _insertMarkerAtCursor(selection) {
                if (!selection || selection.rangeCount === 0) return;
                try {
                    let r = selection.getRangeAt(0);
                    r.deleteContents();
                    let m = document.createElement('span');
                    m.id = 'edn-cursor-marker';
                    r.insertNode(m);
                    let editable = m.closest ? m.closest('anki-editable') : null;
                    if (!editable) {
                        let rn = m.getRootNode ? m.getRootNode() : null;
                        if (rn && rn.host && rn.host.tagName === 'ANKI-EDITABLE') editable = rn.host;
                    }
                    if (editable) {
                        editable.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
                    }
                } catch(e) {}
            }

            if (sel && sel.rangeCount > 0) {
                window._ednSavedRange = sel.getRangeAt(0).cloneRange();
                textValue = sel.toString();
                _insertMarkerAtCursor(sel);
            }

            if (!textValue.trim() && active) {
                let editable = null;
                if (active.tagName === 'ANKI-EDITABLE' || active.isContentEditable) editable = active;
                else if (active.shadowRoot) {
                    let activeInner = active.shadowRoot.activeElement;
                    if (activeInner && (activeInner.tagName === 'ANKI-EDITABLE' || activeInner.isContentEditable)) {
                        editable = activeInner;
                    }
                }
                if (!editable && window._ednSavedRange) {
                    let node = window._ednSavedRange.commonAncestorContainer;
                    while (node) {
                        if (node.tagName === 'ANKI-EDITABLE' || node.isContentEditable || (node.classList && node.classList.contains('field'))) {
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
                        let range = document.createRange();
                        range.selectNodeContents(editable);
                        range.collapse(false);
                        if (sel) { sel.removeAllRanges(); sel.addRange(range); }
                        window._ednSavedRange = range.cloneRange();
                        
                        _queryAll('[id="edn-cursor-marker"]').forEach(function(m) {
                            try { m.parentNode.removeChild(m); } catch(e) {}
                        });
                        
                        _insertMarkerAtCursor(sel || { rangeCount: 1, getRangeAt: () => range });
                    }
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
                            if (active && active.shadowRoot && typeof active.shadowRoot.getSelection === 'function') sel = active.shadowRoot.getSelection();
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
            recto = get_note_recto(note, 240, source_note=_get_editor_note(editor))
        LinkInserter(editor).insert_link([(nid, recto)])
    except:
        tooltip(f"Note {nid} non trouvée.")
        open_search_dialog(editor)

def _setup_editor_window_shortcuts(editor: Editor):
    """Enregistre l'éditeur courant.
    
    Les raccourcis globaux (Ctrl+Alt+L, Ctrl+Alt+C, Ctrl+Alt+B) sont gérés au niveau
    de l'application via mw (ApplicationShortcut) pour éviter l'ambiguïté Qt.
    """
    global _current_editor
    _current_editor = editor


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
        
        function _injectEdnShadowStyles() {
            var css = `
                kbd.clickable_cards {
                    font-family: "Lucida console", Monaco, monospace !important;
                    font-weight: 600 !important;
                    color: #b45309 !important;
                    background: #fffbf5 !important;
                    background-color: #fffbf5 !important;
                    border-radius: 4px !important;
                    border: 1px solid #e5c98e !important;
                    display: inline-block !important;
                    font-size: 13px !important;
                    line-height: 16px !important;
                    padding: 2px 6px !important;
                    margin: 1px 3px !important;
                    text-align: center !important;
                    cursor: pointer !important;
                    width: auto !important;
                    max-width: fit-content !important;
                    box-sizing: border-box !important;
                    vertical-align: middle !important;
                    white-space: nowrap !important;
                    box-shadow: none !important;
                }
                kbd.clickable_cards:hover {
                    color: #7c3aed !important;
                    border-color: #a78bfa !important;
                    background: #f5f3ff !important;
                }
                kbd.clickable_cards:focus,
                kbd.clickable_cards:focus-visible {
                    outline: none !important;
                    box-shadow: none !important;
                }
                .nightMode kbd.clickable_cards,
                .night_mode kbd.clickable_cards,
                .night-mode kbd.clickable_cards,
                body.nightMode kbd.clickable_cards,
                body.night_mode kbd.clickable_cards,
                body.night-mode kbd.clickable_cards {
                    background: #2e3238 !important;
                    background-color: #2e3238 !important;
                    border: 1px solid #4a4d52 !important;
                    color: #FFA630 !important;
                }
                .nightMode kbd.clickable_cards:hover,
                .night_mode kbd.clickable_cards:hover,
                .night-mode kbd.clickable_cards:hover,
                body.nightMode kbd.clickable_cards:hover,
                body.night_mode kbd.clickable_cards:hover,
                body.night-mode kbd.clickable_cards:hover {
                    color: #BD93BD !important;
                    border-color: #BD93BD !important;
                }
                kbd.clickable_cards .edn-nid {
                    color: inherit !important;
                    font-family: inherit !important;
                    font-weight: inherit !important;
                }
                .edn-desc, span.edn-desc {
                    display: inline !important;
                }
                .cartesLiees br, .items.cartesLiees br {
                    display: block !important;
                    content: "" !important;
                }
                [id*="edn-cursor-marker"], #edn-cursor-marker {
                    display: none !important;
                }
            `;

            var editables = document.querySelectorAll('anki-editable');
            for (var i = 0; i < editables.length; i++) {
                var ed = editables[i];
                if (ed.shadowRoot && !ed.shadowRoot.getElementById('edn-shadow-styles')) {
                    var st = document.createElement('style');
                    st.id = 'edn-shadow-styles';
                    st.textContent = css;
                    ed.shadowRoot.appendChild(st);
                }
            }
        }
        window._ednInjectShadowStyles = _injectEdnShadowStyles;
        _injectEdnShadowStyles();
        document.addEventListener('focusin', _injectEdnShadowStyles, true);
        if (window.MutationObserver && !window._ednEditorShadowObserver) {
            window._ednEditorShadowObserver = new MutationObserver(_injectEdnShadowStyles);
            if (document.body) {
                window._ednEditorShadowObserver.observe(document.body, { childList: true, subtree: true });
            }
        }

        window.toggle = window.toggle || function(id) {
            var e = document.getElementById(id);
            if (e) {
                e.style.display = (e.style.display === 'none' || !e.style.display) ? 'block' : 'none';
            }
        };

        function _escapeHtml(s) {
            return String(s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        document.addEventListener('paste', function(e) {
            var text = (e.clipboardData || window.clipboardData).getData('text');
            if (!text) return;

            var lines = text.split(/\r?\n/);
            var nonEmptyLines = lines.filter(function(l) { return l.trim().length > 0; });
            if (nonEmptyLines.length === 0) return;

            var allMatch = true;
            var itemsList = [];
            for (var i = 0; i < nonEmptyLines.length; i++) {
                var line = nonEmptyLines[i].trim();
                var match = line.match(/^(?:(.*?)[ \t\s\u00a0\xa0]*[\u2014\u2013-][ \t\s\u00a0\xa0]*)?\[?(\d{10,})\]?$/);
                if (!match) {
                    allMatch = false;
                    break;
                }
                var pLabel = match[1] ? match[1].trim() : "";
                var pNid = match[2].trim();
                itemsList.push({nid: pNid, label: pLabel});
            }

            if (!allMatch || itemsList.length === 0) return;

            e.preventDefault();
            e.stopImmediatePropagation();

            // Obtenir la sélection actuelle (compatible shadow DOM et standard)
            var sel = null;
            var active = document.activeElement;
            if (active && active.shadowRoot && typeof active.shadowRoot.getSelection === 'function') {
                sel = active.shadowRoot.getSelection();
            }
            if (!sel && typeof window._ednGetEditorSelection === 'function') {
                sel = window._ednGetEditorSelection();
            }
            if (!sel) sel = window.getSelection();

            var textBefore = "";
            var lineEndsWithDash = false;
            if (sel && sel.rangeCount > 0) {
                try {
                    var range = sel.getRangeAt(0);
                    var block = range.startContainer;
                    while (block && block.parentNode && block !== block.parentNode) {
                        if (block.nodeType === 1 && (['P', 'DIV', 'LI', 'BR', 'ANKI-EDITABLE'].includes(block.tagName) || block.isContentEditable)) {
                            break;
                        }
                        block = block.parentNode;
                    }
                    if (!block) block = range.startContainer.parentNode || range.startContainer;

                    var preRange = document.createRange();
                    preRange.setStart(block, 0);
                    preRange.setEnd(range.startContainer, range.startOffset);
                    var divPre = document.createElement('div');
                    divPre.appendChild(preRange.cloneContents());
                    var htmlPre = divPre.innerHTML.replace(/<br\s*\/?>|<\/p>|<\/div>|<\/li>/gi, '\n');
                    var linesPre = htmlPre.split('\n');
                    var lastLineHtml = linesPre[linesPre.length - 1];
                    var tDiv = document.createElement('div');
                    tDiv.innerHTML = lastLineHtml;
                    textBefore = (tDiv.textContent || tDiv.innerText || "").trim();
                    if (/[-—–\u2014\u2013]\s*$/.test(textBefore)) {
                        lineEndsWithDash = true;
                    }
                } catch(err2) {
                    textBefore = "";
                    lineEndsWithDash = false;
                }
            }

            var htmlLines = [];
            var pastedNids = [];
            for (var i = 0; i < itemsList.length; i++) {
                var it = itemsList[i];
                var nid = it.nid;
                var label = it.label;
                pastedNids.push(nid);

                var badgeHtml = '<kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="' + nid + '"><span class="edn-nid">' + nid + '</span></kbd>&nbsp;';

                if (label) {
                    var labelEscaped = _escapeHtml(label);
                    if (i === 0 && (lineEndsWithDash || textBefore.endsWith(label))) {
                        if (lineEndsWithDash) {
                            htmlLines.push(badgeHtml);
                        } else {
                            htmlLines.push('&nbsp;—&nbsp;' + badgeHtml);
                        }
                    } else {
                        htmlLines.push('<span class="edn-desc">' + labelEscaped + '</span>&nbsp;—&nbsp;' + badgeHtml);
                    }
                } else {
                    htmlLines.push(badgeHtml);
                }
            }
            var fullHtml = htmlLines.join('<br>');

            // Insertion directe au niveau du curseur / de la sélection
            var inserted = false;
            try {
                inserted = document.execCommand('insertHTML', false, fullHtml);
            } catch(eCmd) {
                inserted = false;
            }
            if (!inserted && sel && sel.rangeCount > 0) {
                try {
                    var curRange = sel.getRangeAt(0);
                    curRange.deleteContents();
                    var tempDiv = document.createElement('div');
                    tempDiv.innerHTML = fullHtml;
                    var frag = document.createDocumentFragment();
                    var childNode, lastChildNode;
                    while ((childNode = tempDiv.firstChild)) {
                        lastChildNode = frag.appendChild(childNode);
                    }
                    curRange.insertNode(frag);
                    if (lastChildNode) {
                        var afterRange = document.createRange();
                        afterRange.setStartAfter(lastChildNode);
                        afterRange.collapse(true);
                        sel.removeAllRanges();
                        sel.addRange(afterRange);
                    }
                    inserted = true;
                } catch(eRange) {}
            }

            // Déclencher l'événement input pour synchroniser Svelte et la note Anki
            var activeElement = document.activeElement;
            if (activeElement) {
                activeElement.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                if (activeElement.shadowRoot) {
                    var innerEd = activeElement.shadowRoot.querySelector('[contenteditable="true"]');
                    if (innerEd) {
                        innerEd.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                    }
                }
            }
            if (typeof _dispatchInput === 'function') {
                _dispatchInput(activeElement);
            }

            // Proposer les liens miroirs si configuré
            if (pastedNids.length > 0 && typeof pycmd !== 'undefined') {
                pycmd('edn_pasted_nids:' + pastedNids.join(','));
            }
        }, true);

        // Post-paste repair: after Anki sanitizes HTML, restore onclick on badges
        // This handles the case where user pastes HTML (Ctrl+V after Ctrl+Alt+C)
        // and Anki strips onclick/ondblclick attributes from <kbd> elements
        document.addEventListener('paste', function(e) {
            setTimeout(function() {
                var allBadges = [];
                if (typeof window._ednQueryAllRoots === 'function') {
                    allBadges = window._ednQueryAllRoots('.clickable_cards');
                } else {
                    allBadges = Array.from(document.querySelectorAll('.clickable_cards'));
                }
                allBadges.forEach(function(kbd) {
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
                        window.cards_ct_click(nid, true);
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

        window._ednEditorLastClickTime = 0;
        window._ednEditorLastClickNid = null;
        window.cards_ct_click = function(nid, force) {
            // Exiger Ctrl ou Cmd dans l'éditeur sauf si force=true
            var e = window.event;
            if (force || (e && (e.ctrlKey || e.metaKey))) {
                var cleanNid = String(nid || '').trim().replace(/[^0-9]/g, '');
                if (!cleanNid || cleanNid.length < 9) return;
                var now = Date.now();
                if (window._ednEditorLastClickNid === cleanNid && (now - window._ednEditorLastClickTime < 1000)) return;
                window._ednEditorLastClickTime = now;
                window._ednEditorLastClickNid = cleanNid;
                if (typeof pycmd !== 'undefined') pycmd('cards_ct_click' + cleanNid);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('cards_ct_click' + cleanNid);
            }
        };

        function _getEditorSelection() {
            var active = document.activeElement;
            if (active && active.shadowRoot && active.shadowRoot.getSelection) {
                var s = active.shadowRoot.getSelection();
                if (s && s.rangeCount > 0) return s;
            }
            return window.getSelection();
        }

        function _dispatchInput(el) {
            if (!el) return;
            var root = el.closest ? el.closest('anki-editable') : null;
            if (!root) {
                var rn = el.getRootNode ? el.getRootNode() : null;
                if (rn && rn.host && rn.host.tagName === 'ANKI-EDITABLE') root = rn.host;
            }
            if (!root) root = document.querySelector('anki-editable');
            if (root) {
                root.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
            }
        }

        function _setCaretBefore(badge) {
            if (!badge || !badge.parentNode) return;
            var prev = badge.previousSibling;
            if (!prev || prev.nodeType !== Node.TEXT_NODE) {
                prev = document.createTextNode('\u200b');
                badge.parentNode.insertBefore(prev, badge);
            }
            var sel = _getEditorSelection();
            if (sel) {
                var range = document.createRange();
                range.setStart(prev, prev.length);
                range.collapse(true);
                sel.removeAllRanges();
                sel.addRange(range);
            }
            badge.blur();
        }

        function _setCaretAfter(badge) {
            if (!badge || !badge.parentNode) return;
            var next = badge.nextSibling;
            if (!next || next.nodeType !== Node.TEXT_NODE) {
                next = document.createTextNode('\u200b');
                badge.parentNode.insertBefore(next, badge.nextSibling);
            }
            var sel = _getEditorSelection();
            if (sel) {
                var range = document.createRange();
                range.setStart(next, 0);
                range.collapse(true);
            }
            badge.blur();
        }

        // Clic sur un badge dans l'éditeur : positionner le curseur devant ou après le badge
        document.addEventListener('click', function(e) {
            if (e.ctrlKey || e.metaKey) return; // Ctrl+Clic ouvre la carte
            var badge = e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
            if (badge) {
                var rect = badge.getBoundingClientRect();
                var clickX = e.clientX - rect.left;
                if (clickX <= rect.width * 0.45) {
                    _setCaretBefore(badge);
                } else {
                    _setCaretAfter(badge);
                }
            }
        }, true);

        function _findLineBreakBefore(badge) {
            if (!badge) return null;
            var curr = badge.previousSibling;
            while (curr && curr.nodeType === Node.TEXT_NODE && (curr.textContent === '' || curr.textContent === '\u200b')) {
                curr = curr.previousSibling;
            }
            if (curr && curr.nodeType === Node.ELEMENT_NODE && curr.tagName === 'BR') {
                return { type: 'br', node: curr };
            }
            if (curr && curr.nodeType === Node.ELEMENT_NODE && (curr.tagName === 'DIV' || curr.tagName === 'P')) {
                return { type: 'prev_block', block: curr };
            }
            var parent = badge.parentNode;
            if (parent && (parent.tagName === 'DIV' || parent.tagName === 'P') && parent.parentElement) {
                var isFirst = true;
                var p = badge.previousSibling;
                while (p) {
                    if (p.nodeType === Node.ELEMENT_NODE || (p.nodeType === Node.TEXT_NODE && p.textContent.replace(/[\s\u200b]/g, '').length > 0)) {
                        isFirst = false;
                        break;
                    }
                    p = p.previousSibling;
                }
                if (isFirst && parent.previousElementSibling) {
                    return { type: 'parent_block', parent: parent, prevBlock: parent.previousElementSibling };
                }
            }
            return null;
        }

        function _removeLineBreak(badge, lb) {
            if (!lb) return false;
            if (lb.type === 'br') {
                var br = lb.node;
                var p = br.previousSibling;
                while (p && p.nodeType === Node.ELEMENT_NODE && p.tagName === 'BR') {
                    var toDel = p; p = p.previousSibling; toDel.parentNode.removeChild(toDel);
                }
                br.parentNode.removeChild(br);
                _setCaretBefore(badge);
                _dispatchInput(badge);
                return true;
            } else if (lb.type === 'prev_block') {
                var prevBlock = lb.block;
                var next = badge.nextSibling;
                prevBlock.appendChild(badge);
                if (next && next.nodeType === Node.TEXT_NODE && (next.textContent === ' ' || next.textContent === '\u00a0' || next.textContent === '&nbsp;')) {
                    prevBlock.appendChild(next);
                }
                _setCaretBefore(badge);
                _dispatchInput(badge);
                return true;
            } else if (lb.type === 'parent_block') {
                var parent = lb.parent;
                var prevBlock = lb.prevBlock;
                while (parent.firstChild) {
                    prevBlock.appendChild(parent.firstChild);
                }
                if (parent.parentNode) parent.parentNode.removeChild(parent);
                _setCaretBefore(badge);
                _dispatchInput(badge);
                return true;
            }
            return false;
        }

        function _deleteBadgeNode(targetNode) {
            var range = document.createRange();
            range.selectNode(targetNode);
            var sel = _getEditorSelection();
            if (sel) {
                sel.removeAllRanges();
                sel.addRange(range);
            }
            document.execCommand('delete', false, null);
            _dispatchInput(targetNode);
        }

        // Gérer la suppression du badge ou du saut de ligne le précédant (Backspace / Suppr)
        document.addEventListener('keydown', function(e) {
            if (e.key !== 'Backspace' && e.key !== 'Delete') return;

            var activeEl = document.activeElement;
            var isBadgeFocused = activeEl && activeEl.classList && activeEl.classList.contains('clickable_cards');

            if (e.key === 'Backspace') {
                var badge = null;
                if (isBadgeFocused) {
                    badge = activeEl;
                } else {
                    var sel = _getEditorSelection();
                    if (sel && sel.isCollapsed) {
                        var node = sel.startContainer;
                        var offset = sel.startOffset;
                        if (node && node.nodeType === Node.TEXT_NODE) {
                            var textAfterCursor = node.textContent.substring(offset).replace(/[\s\u200b]/g, '');
                            if (textAfterCursor === '') {
                                var next = node.nextSibling;
                                while (next && next.nodeType === Node.TEXT_NODE && (next.textContent === '' || next.textContent === '\u200b')) {
                                    next = next.nextSibling;
                                }
                                if (next && next.nodeType === Node.ELEMENT_NODE && next.classList.contains('clickable_cards')) {
                                    badge = next;
                                }
                            }
                        } else if (node && node.nodeType === Node.ELEMENT_NODE) {
                            var nextNode = node.childNodes[offset];
                            if (nextNode && nextNode.nodeType === Node.ELEMENT_NODE && nextNode.classList.contains('clickable_cards')) {
                                badge = nextNode;
                            }
                        }
                    }
                }

                if (badge) {
                    var lb = _findLineBreakBefore(badge);
                    if (lb) {
                        e.preventDefault();
                        e.stopPropagation();
                        _removeLineBreak(badge, lb);
                        return;
                    } else if (isBadgeFocused) {
                        e.preventDefault();
                        e.stopPropagation();
                        _deleteBadgeNode(badge);
                        return;
                    }
                }

                // Cas classique : Backspace juste après un badge
                var sel = _getEditorSelection();
                if (sel && sel.isCollapsed) {
                    var node = sel.startContainer;
                    var offset = sel.startOffset;
                    var prevBadge = null;
                    if (node && node.nodeType === Node.TEXT_NODE && offset === 0) {
                        var prev = node.previousSibling;
                        while (prev && prev.nodeType === Node.TEXT_NODE && (prev.textContent === '' || prev.textContent === '\u200b')) {
                            prev = prev.previousSibling;
                        }
                        if (prev && prev.nodeType === Node.ELEMENT_NODE && prev.classList.contains('clickable_cards')) {
                            prevBadge = prev;
                        }
                    } else if (node && node.nodeType === Node.ELEMENT_NODE && offset > 0) {
                        var prevNode = node.childNodes[offset - 1];
                        if (prevNode && prevNode.nodeType === Node.ELEMENT_NODE && prevNode.classList.contains('clickable_cards')) {
                            prevBadge = prevNode;
                        }
                    }
                    if (prevBadge) {
                        e.preventDefault();
                        e.stopPropagation();
                        _deleteBadgeNode(prevBadge);
                        return;
                    }
                }
            }

            if (e.key === 'Delete') {
                if (isBadgeFocused) {
                    e.preventDefault();
                    e.stopPropagation();
                    _deleteBadgeNode(activeEl);
                    return;
                }

                var sel = _getEditorSelection();
                if (sel && sel.isCollapsed) {
                    var node = sel.startContainer;
                    var offset = sel.startOffset;

                    // 1. Curseur immédiatement devant un badge sur la même ligne
                    var nextBadge = null;
                    if (node && node.nodeType === Node.TEXT_NODE) {
                        var textAfter = node.textContent.substring(offset).replace(/[\s\u200b]/g, '');
                        if (textAfter === '') {
                            var n = node.nextSibling;
                            while (n && n.nodeType === Node.TEXT_NODE && (n.textContent === '' || n.textContent === '\u200b')) {
                                n = n.nextSibling;
                            }
                            if (n && n.nodeType === Node.ELEMENT_NODE && n.classList.contains('clickable_cards')) {
                                nextBadge = n;
                            }
                        }
                    } else if (node && node.nodeType === Node.ELEMENT_NODE) {
                        var nNode = node.childNodes[offset];
                        if (nNode && nNode.nodeType === Node.ELEMENT_NODE && nNode.classList.contains('clickable_cards')) {
                            nextBadge = nNode;
                        }
                    }

                    if (nextBadge) {
                        e.preventDefault();
                        e.stopPropagation();
                        _deleteBadgeNode(nextBadge);
                        return;
                    }

                    // 2. Curseur en fin de ligne avec un saut de ligne menant à un badge
                    // Vérifier si après un <br> ou après le div courant se trouve un badge
                    var badgeAfterBreak = null;
                    if (node && node.nodeType === Node.TEXT_NODE && offset === node.length) {
                        var nextSib = node.nextSibling;
                        while (nextSib && nextSib.nodeType === Node.TEXT_NODE && nextSib.textContent === '') {
                            nextSib = nextSib.nextSibling;
                        }
                        if (nextSib && nextSib.nodeType === Node.ELEMENT_NODE && nextSib.tagName === 'BR') {
                            var afterBr = nextSib.nextSibling;
                            while (afterBr && afterBr.nodeType === Node.TEXT_NODE && (afterBr.textContent === '' || afterBr.textContent === '\u200b')) {
                                afterBr = afterBr.nextSibling;
                            }
                            if (afterBr && afterBr.nodeType === Node.ELEMENT_NODE && afterBr.classList.contains('clickable_cards')) {
                                badgeAfterBreak = afterBr;
                            }
                        } else if (!nextSib) {
                            var pBlock = node.parentNode;
                            if (pBlock && (pBlock.tagName === 'DIV' || pBlock.tagName === 'P')) {
                                var nextBlock = pBlock.nextElementSibling;
                                if (nextBlock && nextBlock.classList && nextBlock.classList.contains('clickable_cards')) {
                                    badgeAfterBreak = nextBlock;
                                } else if (nextBlock && (nextBlock.tagName === 'DIV' || nextBlock.tagName === 'P')) {
                                    var firstChild = nextBlock.firstElementChild;
                                    if (firstChild && firstChild.classList && firstChild.classList.contains('clickable_cards')) {
                                        badgeAfterBreak = firstChild;
                                    }
                                }
                            }
                        }
                    }

                    if (badgeAfterBreak) {
                        var lb = _findLineBreakBefore(badgeAfterBreak);
                        if (lb) {
                            e.preventDefault();
                            e.stopPropagation();
                            _removeLineBreak(badgeAfterBreak, lb);
                            return;
                        }
                    }
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
        if (window._ednnidInit && window._ednFocusGuardsInstalled) return;
        window._ednnidInit = true;
        window._ednBuffer = '';
        window._ednPreventing = false;
        window._ednTrigger = '{trigger}';
        window._ednTriggerLen = {trigger_len};
        window._ednLastActiveField = 0;
        window._ednLastInputTime = 0;
        window._ednLastKeyTime = 0;
        window._ednLastPointerTime = 0;
        window._ednLastTabTime = 0;
        window._ednMiroirKeys = {{
            next: '{_kbd_next}',
            prev: '{_kbd_prev}',
            open: '{_kbd_open}',
            back: '{_kbd_back}'
        }};
        
        function _getAllShadowRoots(root) {{
            root = root || document.body;
            var roots = [];
            if (!root) return roots;
            if (root.shadowRoot) roots.push(root.shadowRoot);
            var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
            for (var i = 0; i < all.length; i++) {{
                if (all[i].shadowRoot) {{
                    roots.push(all[i].shadowRoot);
                    roots.push(..._getAllShadowRoots(all[i].shadowRoot));
                }}
            }}
            return roots;
        }}

        function _queryAllRoots(selector, root) {{
            root = root || document.body;
            var res = [];
            if (!root) return res;
            if (root.querySelectorAll) {{
                var list = root.querySelectorAll(selector);
                for (var i = 0; i < list.length; i++) res.push(list[i]);
            }}
            var sRoots = _getAllShadowRoots(root);
            for (var i = 0; i < sRoots.length; i++) {{
                var list = sRoots[i].querySelectorAll(selector);
                for (var j = 0; j < list.length; j++) {{
                    if (!res.includes(list[j])) res.push(list[j]);
                }}
            }}
            return res;
        }}
        window._ednQueryAllRoots = _queryAllRoots;

        function _getFieldIdxFromNode(node) {{
            if (!node) return -1;
            var curr = node;
            while (curr) {{
                if (curr.dataset && typeof curr.dataset.index !== 'undefined') {{
                    var di = parseInt(curr.dataset.index, 10);
                    if (!isNaN(di)) return di;
                }}
                if (curr.getAttribute && curr.getAttribute('data-index') !== null) {{
                    var ai = parseInt(curr.getAttribute('data-index'), 10);
                    if (!isNaN(ai)) return ai;
                }}
                curr = curr.parentNode || (curr.getRootNode && curr.getRootNode().host) || curr.host;
            }}
            var editables = Array.from(document.querySelectorAll('anki-editable'));
            if (editables.length === 0) {{
                editables = (typeof window._ednQueryAllRoots === 'function')
                    ? window._ednQueryAllRoots('anki-editable')
                    : Array.from(document.querySelectorAll('anki-editable'));
            }}
            if (editables.length === 0) {{
                editables = Array.from(document.querySelectorAll('.editing-area'));
            }}
            curr = node;
            while (curr) {{
                if (curr.tagName === 'ANKI-EDITABLE') {{
                    var idx = editables.indexOf(curr);
                    if (idx >= 0) return idx;
                }}
                if (curr.classList && curr.classList.contains('editing-area')) {{
                    var idx2 = editables.indexOf(curr);
                    if (idx2 >= 0) return idx2;
                }}
                curr = curr.parentNode || (curr.getRootNode && curr.getRootNode().host) || curr.host;
            }}
            return -1;
        }}

        function _resolveActiveFieldIndex(e) {{
            if (e) {{
                if (typeof e.composedPath === 'function') {{
                    var path = e.composedPath();
                    for (var i = 0; i < path.length; i++) {{
                        var idx = _getFieldIdxFromNode(path[i]);
                        if (idx >= 0) return idx;
                    }}
                }}
                if (e.target) {{
                    var tIdx = _getFieldIdxFromNode(e.target);
                    if (tIdx >= 0) return tIdx;
                }}
                if (e.nodeType) {{
                    var nIdx = _getFieldIdxFromNode(e);
                    if (nIdx >= 0) return nIdx;
                }}
                return -1;
            }}
            var act = document.activeElement;
            while (act) {{
                var idx = _getFieldIdxFromNode(act);
                if (idx >= 0) return idx;
                if (act.shadowRoot && act.shadowRoot.activeElement) {{
                    var sIdx = _getFieldIdxFromNode(act.shadowRoot.activeElement);
                    if (sIdx >= 0) return sIdx;
                }}
                act = act.parentNode || (act.getRootNode && act.getRootNode().host) || act.host;
            }}
            return -1;
        }}
        window._ednResolveActiveFieldIndex = _resolveActiveFieldIndex;

        function _findEditableAncestor(node) {{
            if (!node) return null;
            var curr = node;
            while (curr) {{
                if (curr.nodeType === 1) {{
                    if (curr.tagName === 'ANKI-EDITABLE' || curr.isContentEditable || curr.contentEditable === 'true') return curr;
                    if (curr.classList && (curr.classList.contains('editing-area') || curr.classList.contains('rich-text-input'))) return curr;
                    var tag = curr.tagName.toUpperCase();
                    if (tag === 'INPUT' || tag === 'TEXTAREA') return curr;
                }}
                curr = curr.parentNode || (curr.getRootNode && curr.getRootNode().host) || curr.host;
            }}
            return null;
        }}
        window._ednFindEditableAncestor = _findEditableAncestor;

        function _isEventInEditable(e) {{
            if (!e) return false;
            if (e.target && _findEditableAncestor(e.target)) return true;
            if (e.nodeType && _findEditableAncestor(e)) return true;
            if (typeof e.composedPath === 'function') {{
                var path = e.composedPath();
                for (var i = 0; i < path.length; i++) {{
                    if (path[i] && _findEditableAncestor(path[i])) return true;
                }}
            }}
            var act = document.activeElement;
            if (act && _findEditableAncestor(act)) return true;
            if (act && act.shadowRoot && act.shadowRoot.activeElement && _findEditableAncestor(act.shadowRoot.activeElement)) return true;
            return false;
        }}

        function _getEditorSelection() {{
            var act = document.activeElement;
            if (act && act.shadowRoot && act.shadowRoot.getSelection) {{
                var s = act.shadowRoot.getSelection();
                if (s && s.rangeCount > 0) return s;
            }}
            var sRoots = _getAllShadowRoots(document.body);
            for (var i = 0; i < sRoots.length; i++) {{
                if (sRoots[i].getSelection) {{
                    var s = sRoots[i].getSelection();
                    if (s && s.rangeCount > 0) return s;
                }}
            }}
            var winSel = window.getSelection();
            if (winSel && winSel.rangeCount > 0) return winSel;
            return null;
        }}
        window._ednGetEditorSelection = _getEditorSelection;

        function _isUserEditingNow() {{
            var now = Date.now();
            return (now - (window._ednLastInputTime || 0)) < 3000 ||
                   (now - (window._ednLastKeyTime || 0)) < 3000;
        }}

        function _hasActiveFieldFocus() {{
            try {{
                var act = document.activeElement;
                if (act && _findEditableAncestor(act)) return true;
                if (act && act.shadowRoot && act.shadowRoot.activeElement) {{
                    if (_findEditableAncestor(act.shadowRoot.activeElement)) return true;
                }}
                var sRoots = _getAllShadowRoots(document.body);
                for (var i = 0; i < sRoots.length; i++) {{
                    if (sRoots[i].activeElement && _findEditableAncestor(sRoots[i].activeElement)) return true;
                }}
            }} catch(e) {{}}
            return false;
        }}

        // --- Patchs anti-focus jump Svelte ---
        function _installSvelteFocusGuards() {{
            // 0. Intercepter window.fetch pour bloquer updateNotes stale pendant les operations EDN
            // et détecter l'ajout / création d'une nouvelle note (addNote / newNote) dans NewAddCards
            try {{
                if (!window._ednOrigFetch && typeof window.fetch === 'function') {{
                    window._ednOrigFetch = window.fetch;
                    window.fetch = async function(url, options) {{
                        if (typeof url === 'string') {{
                            if (window._ednBlockUpdateNotes && url.includes('/_anki/updateNotes')) {{
                                console.log('[EDN] Suppressed stale updateNotes during EDN programmatic reload');
                                return new Response(new Uint8Array(0), {{
                                    status: 200,
                                    headers: {{ 'Content-Type': 'application/binary' }}
                                }});
                            }}
                            if (url.includes('/_anki/addNote') || url.includes('/_anki/newNote')) {{
                                window._ednLoadingNewNote = true;
                                window._ednNewNoteLoadTime = Date.now();
                                window._ednLastInputTime = 0;
                                window._ednLastKeyTime = 0;
                                window._ednLastActiveField = 0;
                                window._ednSavedFieldIndex = 0;
                                setTimeout(function() {{ window._ednLoadingNewNote = false; }}, 800);
                            }}
                        }}
                        return window._ednOrigFetch.apply(this, arguments);
                    }};
                }}
            }} catch(e) {{}}

            // 1. Intercepter globalThis.anki.onOperationDidExecute pour empêcher le rechargement intempestif
            // déclenché par Anki Svelte NoteEditor (a2 / nt.noteText -> I4() reloadNote)
            try {{
                globalThis.anki = globalThis.anki || {{}};
                if (!globalThis.anki._ednOpHookDefined) {{
                    globalThis.anki._ednOpHookDefined = true;
                    var _realOp = globalThis.anki.onOperationDidExecute;
                    Object.defineProperty(globalThis.anki, 'onOperationDidExecute', {{
                        configurable: true,
                        enumerable: true,
                        get: function() {{
                            return function(op) {{
                                if (op && op.noteText) {{
                                    if (window._ednForceReload) {{
                                        return _realOp ? _realOp.apply(this, arguments) : undefined;
                                    }}
                                    if (_isUserEditingNow() || _hasActiveFieldFocus()) {{
                                        var safeOp = Object.assign({{}}, op, {{ noteText: false }});
                                        return _realOp ? _realOp.call(this, safeOp) : undefined;
                                    }}
                                }}
                                return _realOp ? _realOp.apply(this, arguments) : undefined;
                            }};
                        }},
                        set: function(fn) {{
                            _realOp = fn;
                        }}
                    }});
                }}
            }} catch(e) {{}}

            // 2. Intercepter globalThis.reloadNote pour empêcher la perte de focus pendant la saisie
            try {{
                if (!window._ednReloadNoteGuarded) {{
                    window._ednReloadNoteGuarded = true;
                    var _realReload = globalThis.reloadNote;
                    // Exposer la fonction réelle pour permettre un bypass intentionnel depuis Python
                    globalThis._ednRealReloadNote = function() {{
                        return _realReload ? _realReload.apply(this, arguments) : undefined;
                    }};
                    Object.defineProperty(globalThis, 'reloadNote', {{
                        configurable: true,
                        enumerable: true,
                        get: function() {{
                            return function() {{
                                if (window._ednForceReload) {{
                                    return _realReload ? _realReload.apply(this, arguments) : undefined;
                                }}
                                if (_isUserEditingNow()) {{
                                    return;
                                }}
                                return _realReload ? _realReload.apply(this, arguments) : undefined;
                            }};
                        }},
                        set: function(fn) {{
                            _realReload = fn;
                            // Mettre à jour aussi le bypass direct
                            globalThis._ednRealReloadNote = function() {{
                                return _realReload ? _realReload.apply(this, arguments) : undefined;
                            }};
                        }}
                    }});
                }}
            }} catch(e) {{}}

            // 3. Intercepter globalThis.loadNote pour préserver le champ actif uniquement lors d'un reload de la même note
            try {{
                if (!window._ednLoadNoteGuarded) {{
                    window._ednLoadNoteGuarded = true;
                    var _realLoad = globalThis.loadNote;
                    Object.defineProperty(globalThis, 'loadNote', {{
                        configurable: true,
                        enumerable: true,
                        get: function() {{
                            return function(args) {{
                                if (args && typeof args === 'object') {{
                                    var curNid = (typeof globalThis.getNoteId === 'function') ? globalThis.getNoteId() : null;
                                    var isNewOrDiffNote = !!(
                                        window._ednLoadingNewNote ||
                                        (Date.now() - (window._ednNewNoteLoadTime || 0) < 800) ||
                                        args.initial ||
                                        args.stickyFieldsFrom ||
                                        !args.nid ||
                                        (curNid && String(args.nid) !== String(curNid))
                                    );
                                    if (isNewOrDiffNote) {{
                                        window._ednLoadingNewNote = true;
                                        window._ednNewNoteLoadTime = Date.now();
                                        window._ednLastInputTime = 0;
                                        window._ednLastKeyTime = 0;
                                        window._ednLastActiveField = 0;
                                        window._ednSavedFieldIndex = 0;
                                        setTimeout(function() {{ window._ednLoadingNewNote = false; }}, 800);
                                    }} else if (_hasActiveFieldFocus() && _isUserEditingNow()) {{
                                        var curIdx = _resolveActiveFieldIndex();
                                        if (curIdx < 0 && typeof window._ednLastActiveField === 'number') {{
                                            curIdx = window._ednLastActiveField;
                                        }}
                                        if (curIdx > 0 && (args.focusTo === 0 || typeof args.focusTo === 'undefined')) {{
                                            args.focusTo = curIdx;
                                        }}
                                    }}
                                }}
                                return _realLoad ? _realLoad.apply(this, arguments) : undefined;
                            }};
                        }},
                        set: function(fn) {{
                            _realLoad = fn;
                        }}
                    }});
                }}
            }} catch(e) {{}}

            // 4. Intercepter globalThis.focusField
            try {{
                if (!window._ednFocusFieldGuarded) {{
                    window._ednFocusFieldGuarded = true;
                    var _origFocusField = globalThis.focusField;
                    Object.defineProperty(globalThis, 'focusField', {{
                        configurable: true,
                        enumerable: true,
                        get: function() {{
                            return function(idx) {{
                                var isNewLoad = window._ednLoadingNewNote || (Date.now() - (window._ednNewNoteLoadTime || 0) < 800);
                                if (!isNewLoad && !window._ednForceReload) {{
                                    var isExplicitNav = (Date.now() - (window._ednLastPointerTime || 0) < 400) ||
                                                        (Date.now() - (window._ednLastTabTime || 0) < 400);
                                    if (!isExplicitNav) {{
                                        var curIdx = _resolveActiveFieldIndex();
                                        if (curIdx < 0 && typeof window._ednLastActiveField === 'number' && window._ednLastActiveField >= 0) {{
                                            curIdx = window._ednLastActiveField;
                                        }}
                                        var hasFocus = _hasActiveFieldFocus();
                                        if (curIdx > 0) {{
                                            if (idx === 0) {{
                                                if (!hasFocus) {{
                                                    return _origFocusField ? _origFocusField.call(this, curIdx) : undefined;
                                                }}
                                                return;
                                            }}
                                            if ((idx === null || typeof idx === 'undefined' || idx === curIdx) && hasFocus) {{
                                                return;
                                            }}
                                        }} else if (curIdx === 0 && hasFocus) {{
                                            if (idx === null || typeof idx === 'undefined' || idx === 0) {{
                                                return;
                                            }}
                                        }}
                                    }}
                                }}
                                return _origFocusField ? _origFocusField.call(this, idx) : undefined;
                            }};
                        }},
                        set: function(fn) {{
                            _origFocusField = fn;
                        }}
                    }});
                }}
            }} catch(e) {{}}

            // 5. Intercepter globalThis.setFields pour synchroniser immédiatement le DOM des champs <anki-editable> focusés.
            // Dans Anki Svelte (Uge/Che dans editor.js), un <anki-editable> ayant le focus est désabonné de son store
            // sur "focus" et ne met à jour son DOM que sur "blur". Sans ce blur après _origSetFields, le Verso de la
            // carte n reste affiché dans le DOM du Verso de la carte n+1 lors de la validation dans AddCards.
            try {{
                if (!window._ednSetFieldsGuarded) {{
                    window._ednSetFieldsGuarded = true;
                    var _origSetFields = globalThis.setFields;
                    Object.defineProperty(globalThis, 'setFields', {{
                        configurable: true,
                        enumerable: true,
                        get: function() {{
                            return function() {{
                                var curNid = (typeof globalThis.getNoteId === 'function') ? globalThis.getNoteId() : null;
                                var isAddModeNote = (!curNid || String(curNid) === '0');
                                var curIdxBefore = _resolveActiveFieldIndex();
                                if (curIdxBefore < 0 && typeof window._ednLastActiveField === 'number') {{
                                    curIdxBefore = window._ednLastActiveField;
                                }}
                                var vals = (arguments.length >= 2 && Array.isArray(arguments[1]))
                                    ? arguments[1]
                                    : (Array.isArray(arguments[0]) ? arguments[0].map(function(x) {{ return Array.isArray(x) ? x[1] : x; }}) : []);
                                var isAllEmpty = (vals.length > 0 && vals.every(function(v) {{ return v === ''; }}));
                                var isClearingActiveField = isAddModeNote && (curIdxBefore > 0 && curIdxBefore < vals.length && vals[curIdxBefore] === '');
                                if (isClearingActiveField || isAllEmpty) {{
                                    window._ednLoadingNewNote = true;
                                    window._ednNewNoteLoadTime = Date.now();
                                    window._ednLastInputTime = 0;
                                    window._ednLastKeyTime = 0;
                                    window._ednLastActiveField = 0;
                                    window._ednSavedFieldIndex = 0;
                                    setTimeout(function() {{ window._ednLoadingNewNote = false; }}, 800);
                                }}
                                var res = _origSetFields ? _origSetFields.apply(this, arguments) : undefined;
                                try {{
                                    var isNewLoad = window._ednLoadingNewNote || (Date.now() - (window._ednNewNoteLoadTime || 0) < 800);
                                    if (isNewLoad || window._ednForceReload) {{
                                        var sRoots = _getAllShadowRoots(document.body);
                                        for (var i = 0; i < sRoots.length; i++) {{
                                            var act = sRoots[i].activeElement;
                                            if (act && (act.tagName === 'ANKI-EDITABLE' || act.isContentEditable)) {{
                                                act.blur();
                                                if (!isNewLoad) {{
                                                    act.focus();
                                                }}
                                            }}
                                        }}
                                        var docAct = document.activeElement;
                                        if (docAct && (docAct.tagName === 'ANKI-EDITABLE' || docAct.isContentEditable)) {{
                                            docAct.blur();
                                            if (!isNewLoad) {{
                                                docAct.focus();
                                            }}
                                        }}
                                    }}
                                }} catch(eBlur) {{}}
                                return res;
                            }};
                        }},
                        set: function(fn) {{
                            _origSetFields = fn;
                        }}
                    }});
                }}
            }} catch(e) {{}}

            window._ednFocusGuardsInstalled = true;
        }}
        _installSvelteFocusGuards();
        setTimeout(_installSvelteFocusGuards, 300);
        setTimeout(_installSvelteFocusGuards, 800);
        setTimeout(_installSvelteFocusGuards, 1500);

        function _trackSelection() {{
            try {{
                var oldM = (typeof window._ednQueryAllRoots === 'function')
                    ? window._ednQueryAllRoots('[id="edn-cursor-marker"]')
                    : Array.from(document.querySelectorAll('[id="edn-cursor-marker"]'));
                oldM.forEach(function(m) {{ try {{ m.parentNode.removeChild(m); }} catch(e) {{}} }});
            }} catch(e) {{}}
            var sel = _getEditorSelection();
            if (sel && sel.rangeCount > 0) {{
                var r = sel.getRangeAt(0);
                var node = r.commonAncestorContainer;
                var editable = _findEditableAncestor(node);
                if (editable) {{
                    window._ednLastValidRange = r.cloneRange();
                    window._ednPersistentRange = r.cloneRange();
                    window._ednSavedRange = r.cloneRange();
                    var idx = _resolveActiveFieldIndex({{ target: node }});
                    if (idx >= 0) {{
                        window._ednLastActiveField = idx;
                        window._ednSavedFieldIndex = idx;
                    }}
                }}
            }}
        }}
        document.addEventListener('selectionchange', _trackSelection, true);
        document.addEventListener('mouseup', _trackSelection, true);
        document.addEventListener('keyup', _trackSelection, true);

        document.addEventListener('focusin', function(e) {{
            _installSvelteFocusGuards();
            // Ne mettre à jour le champ actif que si l'événement vient d'un champ éditable
            // (pas d'un bouton de toolbar ou d'un widget non-champ)
            if (!_isEventInEditable(e)) return;
            var idx = _resolveActiveFieldIndex(e);
            if (idx >= 0) {{
                window._ednLastActiveField = idx;
                window._ednSavedFieldIndex = idx;
                window._ednBuffer = '';
            }}
        }}, true);

        document.addEventListener('input', function(e) {{
            window._ednLastInputTime = Date.now();
            var idx = _resolveActiveFieldIndex(e);
            if (idx >= 0) {{
                window._ednLastActiveField = idx;
                window._ednSavedFieldIndex = idx;
            }}
            _trackSelection();
        }}, true);

        document.addEventListener('beforeinput', function(e) {{
            window._ednLastInputTime = Date.now();
            var idx = _resolveActiveFieldIndex(e);
            if (idx >= 0) {{
                window._ednLastActiveField = idx;
                window._ednSavedFieldIndex = idx;
            }}
        }}, true);

        document.addEventListener('compositionstart', function(e) {{
            window._ednLastInputTime = Date.now();
        }}, true);

        document.addEventListener('compositionupdate', function(e) {{
            window._ednLastInputTime = Date.now();
        }}, true);

        document.addEventListener('pointerdown', function(e) {{
            window._ednLastPointerTime = Date.now();
            // Ne mettre à jour que si on clique dans un champ éditable
            if (!_isEventInEditable(e)) return;
            var idx = _resolveActiveFieldIndex(e);
            if (idx >= 0) {{
                window._ednLastActiveField = idx;
                window._ednSavedFieldIndex = idx;
            }}
        }}, true);

        document.addEventListener('mousedown', function(e) {{
            window._ednLastPointerTime = Date.now();
            // Ne mettre à jour que si on clique dans un champ éditable
            if (!_isEventInEditable(e)) return;
            var idx = _resolveActiveFieldIndex(e);
            if (idx >= 0) {{
                window._ednLastActiveField = idx;
                window._ednSavedFieldIndex = idx;
            }}
        }}, true);
        
        document.addEventListener('keydown', function(e) {{
            if ((e.ctrlKey || e.metaKey) && (e.key === 'Enter' || e.key === 'Return')) {{
                window._ednLoadingNewNote = true;
                window._ednNewNoteLoadTime = Date.now();
                window._ednLastInputTime = 0;
                window._ednLastKeyTime = 0;
                window._ednLastActiveField = 0;
                window._ednSavedFieldIndex = 0;
                setTimeout(function() {{ window._ednLoadingNewNote = false; }}, 800);
                return;
            }}
            if (!e.ctrlKey && !e.altKey && !e.metaKey) {{
                window._ednLastKeyTime = Date.now();
            }}
            if (e.key === 'Tab') {{
                window._ednLastTabTime = Date.now();
                window._ednLastInputTime = 0;
            }}
            var insideEditable = _isEventInEditable(e);
            if (insideEditable && !e.ctrlKey && !e.altKey && !e.metaKey) {{
                window._ednLastInputTime = Date.now();
                var curIdx = _resolveActiveFieldIndex(e);
                if (curIdx >= 0) window._ednLastActiveField = curIdx;
            }}

            // Touche Echap : TOUJOURS active, qu'on soit dans un champ editable ou non
            if (e.key === 'Escape' && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {{
                window._ednBuffer = '';
                var overlay = document.querySelector('.mathjax-overlay, [role="menu"], [role="listbox"], .popover');
                if (overlay && overlay.offsetParent !== null) {{
                    return;
                }}
                e.preventDefault();
                e.stopPropagation();
                if (typeof pycmd !== 'undefined') pycmd('edn_editor_escape');
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('edn_editor_escape');
                return;
            }}

            var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
            var isMiroirKey = (k === window._ednMiroirKeys.next.toLowerCase() ||
                               k === window._ednMiroirKeys.prev.toLowerCase() ||
                               k === window._ednMiroirKeys.open.toLowerCase() ||
                               k === window._ednMiroirKeys.back.toLowerCase());

            if (isMiroirKey && !insideEditable) {{
                e.preventDefault();
                e.stopPropagation();
                if (typeof pycmd !== 'undefined') pycmd('edn_editor_key:' + e.key);
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand('edn_editor_key:' + e.key);
                return;
            }}

            if (e.key === 'Enter') {{
                window._ednBuffer = '';
                return;
            }}
            
            if (window._ednPreventing) {{
                e.preventDefault();
                e.stopPropagation();
                window._ednPreventing = false;
                return;
            }}
            
            if (!insideEditable) {{
                window._ednBuffer = '';
                return;
            }}

            if (e.key && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {{
                window._ednBuffer += e.key;
                if (window._ednBuffer.length > window._ednTriggerLen) {{
                    window._ednBuffer = window._ednBuffer.slice(-window._ednTriggerLen);
                }}
            }} else if (e.key === 'Backspace') {{
                window._ednBuffer = window._ednBuffer.slice(0, -1);
            }}
            
            if (window._ednBuffer.toLowerCase() === window._ednTrigger.toLowerCase()) {{
                e.preventDefault();
                e.stopPropagation();
                
                window._ednBuffer = '';
                window._ednPreventing = true;
                
                // Effacer le trigger dans le champ de saisie
                for (var i = 0; i < window._ednTriggerLen - 1; i++) {{
                    document.execCommand('delete', false, null);
                }}
                
                window._ednSavedRange = null;
                window._ednSavedFieldIndex = window._ednLastActiveField;

                var curFieldIdx = _resolveActiveFieldIndex(e);
                if (curFieldIdx >= 0) {{
                    window._ednSavedFieldIndex = curFieldIdx;
                    window._ednLastActiveField = curFieldIdx;
                }}

                // Sauvegarder la position du curseur
                var sel = _getEditorSelection();
                if (sel && sel.rangeCount > 0) {{
                    window._ednSavedRange = sel.getRangeAt(0).cloneRange();
                    try {{
                        var r = window._ednSavedRange.cloneRange();
                        r.collapse(false);
                        var marker = document.createElement("span");
                        marker.id = "edn-cursor-marker";
                        r.insertNode(marker);
                    }} catch(err) {{}}
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

    if key.lower() == 'escape':
        from .editor_assistant import _close_editor, _hide_card_preview_popup, _is_preview_popup_alive, _preview_popup
        if _is_preview_popup_alive() and _preview_popup.isVisible():
            _hide_card_preview_popup()
            return
        _close_editor(editor)
        return

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
            is_visible = not assistant.isHidden()
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
    if message == "edn_editor_escape":
        from .editor_assistant import get_active_editor, _close_editor, _hide_card_preview_popup, _is_preview_popup_alive, _preview_popup
        if _is_preview_popup_alive() and _preview_popup.isVisible():
            _hide_card_preview_popup()
            return (True, None)
        editor = get_active_editor(context)
        if editor:
            _close_editor(editor)
        return (True, None)
    elif message.startswith("edn_editor_key:"):
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
            raw_items = data.get("items", [])
            if not raw_items and "nids" in data:
                raw_items = [{"nid": str(n), "label": ""} for n in data.get("nids", [])]

            has_text_before = data.get("hasTextBefore", False)
            line_ends_with_dash = data.get("lineEndsWithDash", False)
            if "lineHasDash" in data and not has_text_before and not line_ends_with_dash:
                line_ends_with_dash = data["lineHasDash"]
                has_text_before = data["lineHasDash"]
            text_before = data.get("textBefore", "")
            target_fidx = data.get("fieldIndex", None)
            if target_fidx is not None and target_fidx < 0:
                target_fidx = None

            from .editor_assistant import get_active_editor
            editor = get_active_editor(context)
            source_note = _get_editor_note(editor)
            if editor and source_note and raw_items:
                items = []
                for idx, it in enumerate(raw_items):
                    nid = str(it.get("nid", "")).strip()
                    pasted_label = str(it.get("label", "")).strip()

                    if line_ends_with_dash:
                        # La ligne se termine déjà par un tiret : insérer uniquement le badge NID
                        items.append((nid, None))
                    elif pasted_label:
                        # Un label personnalisé est présent dans le texte collé
                        items.append((nid, pasted_label))
                    else:
                        # Aucun label n'est présent dans le texte collé : insérer uniquement le badge NID
                        items.append((nid, None))
                
                inserter = LinkInserter(editor)
                config = mw.addonManager.getConfig(__name__) or {}
                mirror_req = config.get("mirror_link_on_paste", True)
                inserter.insert_link(items, mirror_requested=mirror_req, target_fidx=target_fidx)
        except Exception as e:
            log(f"Error handling edn_paste_smart_link: {e}")
        return (True, None)
    elif message.startswith("edn_pasted_nids:"):
        nids_str = message[len("edn_pasted_nids:"):]
        try:
            nids = [int(n.strip()) for n in nids_str.split(",") if n.strip()]
            from .editor_assistant import get_active_editor
            editor = get_active_editor(context)
            source_note = _get_editor_note(editor)
            if editor and source_note and getattr(source_note, 'id', None):
                config = mw.addonManager.getConfig(__name__) or {}
                mirror_on_paste = config.get("mirror_link_on_paste", True)
                if mirror_on_paste:
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
    elif message.startswith("open_browser:"):
        nid = message[len("open_browser:"):].strip(" :()\"'")
        open_card_in_browser(nid)
        return (True, None)
    elif message.startswith("preview_resize:") or message.startswith("preview_nested:") or message == "preview_back" or message == "escape" or message.startswith("preview_key_to_assistant:"):
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


_EDN_STOPWORDS = {
    'de', 'la', 'le', 'les', 'des', 'du', 'd', 'l', 'en', 'un', 'une', 'a', 'au', 'aux',
    'dans', 'sur', 'pour', 'par', 'et', 'ou', 'ce', 'cet', 'cette', 'ces', 'qui', 'que',
    'quoi', 'dont', 'avec', 'sans', 'sous', 'vers', 'chez', 'est', 'sont', 'ont', 'fait',
    'par', 'plus', 'moins', 'tres', 'trop', 'ne', 'pas', 'se', 'sa', 'son', 'ses', 's'
}

_EDN_QUESTION_PREFIXES = [
    re.compile(r"^(?:quels?|quelles?)\s+(?:sont\s+)?(?:les\s+)?(?:\d+\s+)?(?:principales?\s+)?(?:types?\s+d['’]|causes?\s+d['’]|signes?\s+d['’]|facteurs?\s+d['’]|criteres?\s+d['’])?", re.IGNORECASE),
    re.compile(r"^qu['’]est[- ]ce\s+qu[e']\s*", re.IGNORECASE),
    re.compile(r"^(?:comment|pourquoi)\s+", re.IGNORECASE),
    re.compile(r"^d[eé]finition\s+d['’e]\s*", re.IGNORECASE),
    re.compile(r"^types?\s+d['’e]\s*", re.IGNORECASE),
    re.compile(r"^quelles?\s+sont\s+les\s+", re.IGNORECASE),
    re.compile(r"^quels?\s+sont\s+les\s+", re.IGNORECASE),
    re.compile(r"^(?:3|2|4|5|6)\s+(?:situations?|types?|causes?|signes?)\s+(?:de\s+|d['’])?", re.IGNORECASE),
    re.compile(r"^diff[eé]rence\s+entre\s+.*?\s+et\s+", re.IGNORECASE),
]

_EDN_ASPECT_PATTERNS = [
    (re.compile(r'\b(forme pure|pur)\b', re.IGNORECASE), 'Pur'),
    (re.compile(r'\b(forme impure|impur)\b', re.IGNORECASE), 'Impur'),
    (re.compile(r'\b(tremblement)\b', re.IGNORECASE), 'Tremblement'),
    (re.compile(r'\b(akinesie)\b', re.IGNORECASE), 'Akinésie'),
    (re.compile(r'\b(rigidite)\b', re.IGNORECASE), 'Rigidité'),
    (re.compile(r'\b(enfant|pediatrie|pediatrique)\b', re.IGNORECASE), 'Enfant'),
    (re.compile(r'\b(adulte)\b', re.IGNORECASE), 'Adulte'),
    (re.compile(r'\b(ecg|electrocardiogramme)\b', re.IGNORECASE), 'ECG'),
    (re.compile(r'\b(enmg|electromyogramme)\b', re.IGNORECASE), 'ENMG'),
    (re.compile(r'\b(irm)\b', re.IGNORECASE), 'IRM'),
    (re.compile(r'\b(tdm|scanner)\b', re.IGNORECASE), 'Scanner'),
    (re.compile(r'\b(radiographie|radio|echographie|imagerie)\b', re.IGNORECASE), 'Imagerie'),
    (re.compile(r'\b(biologie|bilan biologique|bilan|examens? complementaires?)\b', re.IGNORECASE), 'Examens complémentaires'),
    (re.compile(r'\b(facteurs? de risque cardio[- ]vasculaire|fdrcv)\b', re.IGNORECASE), 'FdRCV'),
    (re.compile(r'\b(facteurs? de risque|fdr)\b', re.IGNORECASE), 'FdR'),
    (re.compile(r'\b(mecanisme d[\'’]action)\b', re.IGNORECASE), "Mécanisme d'action"),
    (re.compile(r'\b(mecanisme|physiopathologie|physiopath)\b', re.IGNORECASE), 'Physiopathologie'),
    (re.compile(r'\b(prevention|vaccination|vaccin)\b', re.IGNORECASE), 'Prévention'),
    (re.compile(r'\b(surveillance|suivi)\b', re.IGNORECASE), 'Surveillance'),
    (re.compile(r'\b(posologie|modalites)\b', re.IGNORECASE), 'Posologie'),
    (re.compile(r'\b(contre[- ]indications?|ci)\b', re.IGNORECASE), 'Contre-indications'),
    (re.compile(r'\b(indications?)\b', re.IGNORECASE), 'Indications'),
    (re.compile(r'\b(duree d[\'’]evolution|duree|timing|incubation)\b', re.IGNORECASE), 'Durée'),
    (re.compile(r'\b(critere?s? de gravite|gravite)\b', re.IGNORECASE), 'Critères de gravité'),
    (re.compile(r'\b(complications?|evolution|pronostic)\b', re.IGNORECASE), 'Complications'),
    (re.compile(r'\b(causes?|etiologies?|facteurs? favorisants?)\b', re.IGNORECASE), 'Causes'),
    (re.compile(r'\b(que permettent|permettent|utilite|a quoi sert)\b', re.IGNORECASE), 'Utilité'),
    (re.compile(r'\b(aspect au tdm|aspect a l[\'’]irm|aspect)\b', re.IGNORECASE), 'Aspect'),
    (re.compile(r'\b(localisation|site)\b', re.IGNORECASE), 'Localisation'),
    (re.compile(r'\b(frequence|prevalence|incidence)\b', re.IGNORECASE), 'Fréquence'),
    (re.compile(r'\b(exemples?)\b', re.IGNORECASE), 'Exemples'),
    (re.compile(r'\b(iatrogene|iatrogenie)\b', re.IGNORECASE), 'Iatrogène'),
    (re.compile(r'\b(antibiotherapie|antibiotiques?|atb)\b', re.IGNORECASE), 'ATB'),
    (re.compile(r'\b(effets? secondaires?|effets? indesirables?)\b', re.IGNORECASE), 'Effets secondaires'),
    (re.compile(r'\b(chirurgie|chirurgical)\b', re.IGNORECASE), 'Chirurgie'),
    (re.compile(r'\b(avantages?)\b', re.IGNORECASE), 'Avantages'),
    (re.compile(r'\b(inconvenients?)\b', re.IGNORECASE), 'Inconvénients'),
    (re.compile(r'\b(signes? cliniques?|signes?|clinique|symptomes?|semiologie|manifestations?)\b', re.IGNORECASE), 'Signes'),
    (re.compile(r'\b(diagnostic|diag|criteres diagnostiques|criteres)\b', re.IGNORECASE), 'Diagnostic'),
    (re.compile(r'\b(prise en charge|pec)\b', re.IGNORECASE), 'PeC'),
    (re.compile(r'\b(traitement|traitements|therapeutique)\b', re.IGNORECASE), 'Traitement'),
    (re.compile(r'\b(definition|def|qu\'est-ce que|qu\'est ce que)\b', re.IGNORECASE), 'Définition'),
]

def _clean_recto_preamble(text: str) -> str:
    if not text:
        return ""
    import unicodedata
    import html as _html
    t = text.strip()
    t_clean = t
    t_norm = unicodedata.normalize('NFKD', _html.unescape(t)).encode('ASCII', 'ignore').decode('utf-8').lower().strip()
    for qp in _EDN_QUESTION_PREFIXES:
        m = qp.search(t_norm)
        if m and m.start() == 0:
            t_clean = t_clean[m.end():].strip()
            break
    t_clean = re.sub(r'[\s\?\.:!—–-]+$', '', t_clean).strip()
    if t_clean:
        t_clean = t_clean[0].upper() + t_clean[1:]
    return t_clean or t

def generate_smart_link_label(source_note, target_note):
    """Génère une description raccourcie intelligente pour l'insertion de lien dans une note.
    
    Pipeline intelligent :
    1. Relation Enfant -> Parent / Carte mère -> 'Main'
    2. Détection des facettes médicales canoniques et abréviations (Signes, Traitement, PeC, FdR, etc.)
    3. Extraction différentielle contextuelle (ne retient que le qualificatif résiduel exclusif)
    4. Nettoyage des tournures interrogatives et concision (1 à 3 mots).
    """
    if not target_note:
        return ""
    
    target_recto = get_note_recto(target_note, 240)
    if not target_recto:
        return ""
    if not source_note:
        return _clean_recto_preamble(target_recto)
        
    source_recto = get_note_recto(source_note, 240)
    if not source_recto:
        return _clean_recto_preamble(target_recto)
        
    import unicodedata
    import html as _html

    def _norm(s):
        if not s:
            return ""
        s = _html.unescape(s)
        s = re.sub(r'<[^>]+>', ' ', s)
        s = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', s)
        s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
        s = s.lower().strip()
        return re.sub(r'\s+', ' ', s)

    s_norm = _norm(source_recto)
    t_norm = _norm(target_recto)

    # 1. Relation Parent / Carte mère -> 'Main'
    try:
        is_target_syndrome = "syndrome" in target_note.note_type()['name'].lower()
        is_source_syndrome = "syndrome" in source_note.note_type()['name'].lower()
        if is_target_syndrome and not is_source_syndrome:
            return "Main"
    except Exception:
        pass

    s_tokens = [w for w in re.findall(r'\b[a-zA-Z\u00C0-\u00FF]{2,}\b', s_norm) if w not in _EDN_STOPWORDS]
    t_tokens = [w for w in re.findall(r'\b[a-zA-Z\u00C0-\u00FF]{2,}\b', t_norm) if w not in _EDN_STOPWORDS]
    s_set = set(s_tokens)
    t_set = set(t_tokens)

    # Racine de classification, algorithme ou typologie vers sous-type
    is_classification_parent = bool(
        re.search(r'\b(classification|algorithme|typologie|sources)\b', t_norm)
        and not re.search(r'\b(classification|algorithme|typologie|sources)\b', s_norm)
    )
    is_type_instance = bool(re.search(r'\b(type [ivx\d]+|stade \d+|grade \d+)\b', s_norm))
    if is_classification_parent and (is_type_instance or len(s_set & t_set) >= 1):
        return "Main"

    # Sous-ensemble strict des termes signifiants (sans aspect spécifique nouveau dans la cible)
    has_specific_aspect_in_target = False
    for pat, label in _EDN_ASPECT_PATTERNS[:20]:
        if pat.search(t_norm) and not pat.search(s_norm):
            has_specific_aspect_in_target = True
            break

    if t_set and s_set and t_set.issubset(s_set) and len(s_set) > len(t_set) and not has_specific_aspect_in_target:
        return "Main"

    # 2. Détection d'aspect médical (avec prise en compte du contexte partagé)
    matched_aspect = None
    for pat, label in _EDN_ASPECT_PATTERNS:
        m_target = pat.search(t_norm)
        m_source = pat.search(s_norm)
        if m_target:
            if not m_source:
                matched_aspect = label
                break

    if matched_aspect:
        return matched_aspect

    # 3. Extraction différentielle contextuelle
    common_tokens = s_set & t_set
    if len(common_tokens) >= 1:
        diff_tokens = [w for w in t_tokens if w not in s_set]
        if diff_tokens:
            # Numéro ou chiffre romain
            num_match = re.search(r'\b(type\s+)?([ivx]+|\d+)\b', t_norm)
            if num_match and ('type' in diff_tokens or num_match.group(2) in diff_tokens):
                val = num_match.group(2).upper()
                return val if 'type' in s_norm else f"Type {val}"

            if len(diff_tokens) <= 3:
                diff_words_pattern = r'\b(?:' + '|'.join(re.escape(w) for w in diff_tokens) + r')\b'
                matches = list(re.finditer(diff_words_pattern, t_norm))
                if matches:
                    start_char = matches[0].start()
                    end_char = matches[-1].end()
                    raw_diff = target_recto[start_char:end_char].strip()
                    raw_diff = re.sub(r'[\s\?\.:!—–-]+$', '', raw_diff).strip()
                    if raw_diff and len(raw_diff.split()) <= 4:
                        raw_diff = raw_diff[0].upper() + raw_diff[1:]
                        return raw_diff

    # 4. Nettoyage préambule et concision fallback
    clean_t = _clean_recto_preamble(target_recto)
    words = clean_t.split()
    if len(words) <= 3:
        return clean_t

    return clean_t

class LinkInserter:
    def __init__(self, editor):
        self.editor = editor
        
    def insert_link(self, items, override_html=None, mirror_requested=False, target_fidx=None):
        """Insère les liens de cartes directement dans la note courante.
        Persistance garantie à 100% dans la collection SQLite via col_note.fields[fidx].
        100% rétro-compatible avec Svelte ON (NewEditor) et Svelte OFF (Legacy Editor).
        """
        caller = "LinkInserter:insert_link"
        _profiler_log("LINK_INSERTER", f"insert_link() CALLED: items_count={len(items)}, target_fidx={target_fidx}, mirror={mirror_requested}, override_html={'yes' if override_html else 'no'}", caller=caller)
        if not self.editor:
            _profiler_log("LINK_INSERTER", "editor is None -> aborted", caller=caller)
            return

        def _do_commit():
            commit_caller = "LinkInserter:_do_commit"
            import traceback
            try:
                source_note = _get_editor_note(self.editor)
                if not source_note:
                    _profiler_log("LINK_INSERTER:_do_commit", "source_note is None -> aborted", caller=commit_caller)
                    return

                is_existing = bool(getattr(source_note, 'id', None) and source_note.id != 0)
                _profiler_log("LINK_INSERTER:_do_commit", f"source_note id={source_note.id}, is_existing={is_existing}, num_fields={len(source_note.fields) if source_note.fields else 0}", caller=commit_caller)

                if is_existing:
                    col_note = mw.col.get_note(source_note.id)
                else:
                    col_note = source_note

                if not col_note or not col_note.fields:
                    _profiler_log("LINK_INSERTER:_do_commit", "col_note or col_note.fields is empty -> aborted", caller=commit_caller)
                    return

                # Enregistrement auprès de l'intercepteur de liens temporaires (si diff import actif)
                try:
                    import sys
                    interceptor_mod = sys.modules.get("anki_import_diff.src.temp_link_interceptor")
                    if interceptor_mod:
                        interceptor = interceptor_mod.TempLinkInterceptor.instance()
                        if interceptor.is_active():
                            src_id = getattr(col_note, 'id', None)
                            for it_nid, _ in items:
                                interceptor.register_link_creation(src_id, it_nid, col_note)
                except Exception:
                    pass

                # Déterminer le champ cible
                fidx = target_fidx
                efidx = getattr(self.editor, 'currentField', None)
                last_fld = getattr(self.editor, 'last_field_index', None)
                if fidx is None or fidx < 0 or fidx >= len(col_note.fields):
                    if efidx is not None and 0 <= efidx < len(col_note.fields):
                        fidx = efidx
                    elif last_fld is not None and 0 <= last_fld < len(col_note.fields):
                        fidx = last_fld
                    else:
                        fidx = _find_target_field_idx(col_note)

                if fidx < 0 or fidx >= len(col_note.fields):
                    fidx = len(col_note.fields) - 1

                fld_name = col_note.note_type()['flds'][fidx]['name'] if (col_note.note_type() and 'flds' in col_note.note_type() and fidx < len(col_note.note_type()['flds'])) else f"idx_{fidx}"
                _profiler_log("LINK_INSERTER:_do_commit", f"FIELD RESOLUTION: target_fidx={target_fidx}, currentField={efidx}, last_field={last_fld} -> CHOSEN fidx={fidx} ('{fld_name}') [total_fields={len(col_note.fields)}]", caller=commit_caller)

                # Construction du HTML propre des liens
                if override_html is not None:
                    html = override_html
                else:
                    html_parts = []
                    for nid, recto in items:
                        html_parts.append(make_card_link(str(nid), recto))
                    is_inline_mode = all(recto is None or recto == "" for _, recto in items)
                    if is_inline_mode:
                        html = "&nbsp;".join(html_parts)
                    else:
                        html = "<br>".join(html_parts)
                    if html:
                        html += "&nbsp;"

                # Mise à jour directe du contenu du champ
                cur_val = col_note.fields[fidx].strip()
                marker_pattern = r'<span\s+id=["\']edn-cursor-marker["\'][^>]*>.*?</span>'
                has_marker = bool(re.search(marker_pattern, cur_val, flags=re.IGNORECASE))

                is_inline = all(recto is None or recto == "" for _, recto in items) if override_html is None else False

                # Scanner le champ : s'il y a un tiret orphelin (sans contenu ni badge <kbd> à la suite),
                # on s'incruste immédiatement après ce tiret (avant un éventuel <br>) au lieu de créer une nouvelle ligne.
                pending_dash_pattern = r'([—–-](?:&nbsp;|\s)*)(?:<br\s*/?>\s*)?$'
                dash_match = re.search(pending_dash_pattern, cur_val, flags=re.IGNORECASE)

                # Nettoyage si on remplace un NID sélectionné ou en fin de ligne
                # pour éviter le doublon "Titre — 1691321445398 <kbd>1691321445398</kbd>"
                single_nid_str = str(items[0][0]) if (items and len(items) == 1) else None

                if has_marker:
                    # Emplacement exact du curseur mémorisé : insérer le HTML directement à cette position.
                    # Si le NID brut sélectionné n'a pas été effacé du DOM (ex. juste avant ou après le marker), on le retire.
                    if single_nid_str:
                        # 1) NID brut juste avant le marker (avec éventuel espace/&nbsp;)
                        cur_val = re.sub(
                            rf'(?:&nbsp;|\s)*{re.escape(single_nid_str)}(?:&nbsp;|\s)*' + marker_pattern,
                            '<span id="edn-cursor-marker"></span>',
                            cur_val,
                            count=1,
                            flags=re.IGNORECASE
                        )
                        # 2) NID brut à l'intérieur du marker
                        cur_val = re.sub(
                            rf'<span\s+id=["\']edn-cursor-marker["\'][^>]*>(?:&nbsp;|\s)*{re.escape(single_nid_str)}(?:&nbsp;|\s)*</span>',
                            '<span id="edn-cursor-marker"></span>',
                            cur_val,
                            count=1,
                            flags=re.IGNORECASE
                        )
                    new_val = re.sub(marker_pattern, html, cur_val, count=1, flags=re.IGNORECASE)
                elif is_inline:
                    # Si le champ se termine par le NID brut (ex. l'utilisateur a sélectionné le NID en bout de ligne)
                    if single_nid_str:
                        trailing_nid_pat = rf'(?:&nbsp;|\s)*{re.escape(single_nid_str)}(?:&nbsp;|\s)*(?:<br\s*/?>\s*)?$'
                        if re.search(trailing_nid_pat, cur_val, flags=re.IGNORECASE):
                            cur_val = re.sub(trailing_nid_pat, '', cur_val, count=1, flags=re.IGNORECASE).rstrip()

                    if cur_val.endswith('&nbsp;') or cur_val.endswith(' '):
                        new_val = cur_val + html
                    elif cur_val.endswith('—') or cur_val.endswith('–') or cur_val.endswith('-'):
                        new_val = cur_val + '&nbsp;' + html
                    else:
                        new_val = (cur_val + '&nbsp;' + html) if cur_val else html
                elif dash_match and len(items) == 1 and override_html is None:
                    # Un tiret en attente a été trouvé dans le champ : on insère uniquement le badge
                    # sans description ni tiret supplémentaire, exactement là où se trouve le tiret
                    single_nid = str(items[0][0])
                    badge_html = make_clickable_card_badge(single_nid)
                    prefix = cur_val[:dash_match.end(1)]
                    # S'assurer d'un espacement propre après le tiret
                    if not prefix.endswith('&nbsp;') and not prefix.endswith(' '):
                        prefix += '&nbsp;'
                    new_val = prefix + badge_html + '&nbsp;'
                else:
                    # Si le champ se termine par le NID brut, le remplacer au lieu d'ajouter après
                    if single_nid_str:
                        trailing_nid_pat = rf'(?:&nbsp;|\s)*{re.escape(single_nid_str)}(?:&nbsp;|\s)*(?:<br\s*/?>\s*)?$'
                        if re.search(trailing_nid_pat, cur_val, flags=re.IGNORECASE):
                            cur_val = re.sub(trailing_nid_pat, '', cur_val, count=1, flags=re.IGNORECASE).rstrip()
                    if not cur_val or cur_val == '<br>':
                        new_val = html
                    else:
                        new_val = cur_val + '<br>' + html

                new_val = _clean_kbd_html(new_val)

                _profiler_log("LINK_INSERTER:_do_commit", f"FIELD PREVIEW: cur_val len={len(cur_val)}: {cur_val[:60]!r}", caller=commit_caller)
                _profiler_log("LINK_INSERTER:_do_commit", f"HTML TO INSERT len={len(html)}: {html[:60]!r}", caller=commit_caller)
                _profiler_log("LINK_INSERTER:_do_commit", f"NEW_VAL len={len(new_val)}: {new_val[:60]!r}", caller=commit_caller)

                col_note.fields[fidx] = new_val

                if is_existing:
                    _profiler_log("LINK_INSERTER:_do_commit", f"calling mw.col.update_note(nid={col_note.id})", caller=commit_caller)
                    mw.col.update_note(col_note)
                    _profiler_log("LINK_INSERTER:_do_commit", f"mw.col.update_note finished", caller=commit_caller)

                    # Verification immediate dans SQLite
                    try:
                        check_note = mw.col.get_note(col_note.id)
                        f_match = (check_note.fields[fidx] == new_val) or (new_val in check_note.fields[fidx])
                        _profiler_log("LINK_INSERTER:_do_commit", f"SQLITE VERIFY 1 (immediate): nid={col_note.id}, field[{fidx}] match={f_match}, len={len(check_note.fields[fidx])}", caller=commit_caller)
                    except Exception as e_chk:
                        _profiler_log("LINK_INSERTER:_do_commit", f"SQLITE VERIFY 1 ERROR: {e_chk}", caller=commit_caller)

                # Synchronisation immediate des caches de l'editeur en memoire
                try:
                    if hasattr(self.editor, "_note_compat_cache"):
                        self.editor._note_compat_cache = col_note
                    if hasattr(self.editor, "note") and not isinstance(type(self.editor).note, property):
                        self.editor.note = col_note
                    elif hasattr(self.editor, "note"):
                        try:
                            self.editor.note = col_note
                        except Exception:
                            pass
                except Exception as e:
                    _profiler_log("LINK_INSERTER:_do_commit", f"cache sync warning: {e}", caller=commit_caller)

                # Nettoyage cote DOM des eventuels marqueurs residuels
                try:
                    self.editor.web.eval("""(function() {
                        try {
                            var markers = (typeof window._ednQueryAllRoots === 'function')
                                ? window._ednQueryAllRoots('[id*="edn-cursor-marker"]')
                                : Array.from(document.querySelectorAll('[id*="edn-cursor-marker"]'));
                            markers.forEach(function(m) { try { m.remove(); } catch(e) {} });
                        } catch(e) {}
                    })();""")
                except Exception as e:
                    _profiler_log("LINK_INSERTER:_do_commit", f"DOM marker cleanup warning: {e}", caller=commit_caller)

                # Rechargement propre de la note dans l'editeur avec synchronisation des stores et focus
                _profiler_log("LINK_INSERTER:_do_commit", f"calling editor_reload_note(keep_focus=True, target_fidx={fidx})", caller=commit_caller)
                editor_reload_note(self.editor, keep_focus=True, note=col_note, target_fidx=fidx)
                _profiler_log("LINK_INSERTER:_do_commit", "editor_reload_note call dispatched", caller=commit_caller)

                # Verification post-reload a 600ms pour s'assurer qu'aucun blur n'a ecrase SQLite
                if is_existing:
                    from aqt.qt import QTimer
                    def _verify_later(nid_to_check=col_note.id, target_idx=fidx, expected_val=new_val):
                        try:
                            c_note = mw.col.get_note(nid_to_check)
                            is_intact = (c_note.fields[target_idx] == expected_val) or (expected_val in c_note.fields[target_idx])
                            _profiler_log("LINK_INSERTER:POST_VERIFY", f"SQLITE VERIFY 2 (+600ms post-blur): nid={nid_to_check}, field[{target_idx}] intact={is_intact}, cur_len={len(c_note.fields[target_idx])}", caller="LinkInserter:_verify_later")
                        except Exception as ex:
                            _profiler_log("LINK_INSERTER:POST_VERIFY", f"SQLITE VERIFY 2 ERROR: {ex}", caller="LinkInserter:_verify_later")
                    QTimer.singleShot(600, _verify_later)

                # Mise a jour de l'assistant lateral
                try:
                    from .editor_assistant import update_assistant, clear_assistant_preview_cache
                    if is_existing:
                        clear_assistant_preview_cache(col_note.id)
                    _profiler_log("LINK_INSERTER:_do_commit", "triggering update_assistant()", caller=commit_caller)
                    update_assistant(self.editor)
                except Exception as e:
                    _profiler_log("LINK_INSERTER:_do_commit", f"update_assistant error: {e}", caller=commit_caller)

                # Notification utilisateur
                if len(items) == 1:
                    tooltip(f"Lien créé vers {items[0][0]}")
                else:
                    tooltip(f"{len(items)} liens créés")

                # Liens miroirs
                if mirror_requested and is_existing:
                    _proposal_queue.add(lambda done: _propose_mirror_links_group(
                        self.editor, col_note, items, done
                    ))
                    if len(items) > 1:
                        target_nids = [int(nid) for nid, _ in items]
                        _proposal_queue.add(lambda done: _propose_cross_links(
                            self.editor, col_note, target_nids, done
                        ))

            except Exception as e:
                _profiler_log("LINK_INSERTER:ERROR", f"CRITICAL Exception in _do_commit: {e}\n{traceback.format_exc()}", caller=commit_caller)
                log(f"LinkInserter error: {e}")

        # Bloquer preventativement les updateNotes intempestifs declenches par le blur
        if hasattr(self.editor, "web") and self.editor.web:
            try:
                self.editor.web.eval("window._ednBlockUpdateNotes = true;")
            except Exception:
                pass

        # Sauvegarder d'abord les saisies en cours dans l'editeur avant d'appliquer la mise a jour
        _profiler_log("LINK_INSERTER", "calling editor_save_now before _do_commit", caller=caller)
        editor_save_now(self.editor, _do_commit, keep_focus=True)

    def insert_link_with_text(self, nid, display_text, mirror_requested=False, target_fidx=None):
        """Insère un lien hyperlien : le texte affiché est display_text mais le NID est stocké dans data-nid
        ET dans un <span class="edn-nid"> invisible. Ce double stockage garantit que le NID
        survive au sanitizer HTML d'Anki qui supprime parfois data-* et onclick.
        """
        safe_nid = str(nid)
        badge = make_clickable_card_badge(safe_nid, custom_text=display_text)
        html = f'{badge}&nbsp;'
        self.insert_link([(safe_nid, None)], override_html=html, mirror_requested=mirror_requested, target_fidx=target_fidx)
        tooltip(f"Lien '{display_text}' -> carte {nid}")


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
        
        done_called = False
        def on_done():
            nonlocal done_called
            if done_called:
                return
            done_called = True
            self.running = False
            self.trigger_next()
            
        # Watchdog: force release apres 30 secondes en cas de blocage inattendu
        QTimer.singleShot(30000, lambda: on_done() if not done_called else None)
        # Latence minimale (40ms) : permet a Qt de traiter la fermeture du dialogue precedent sans attente perceptible
        QTimer.singleShot(40, lambda: self._run_task(task_fn, on_done))
        
    def _run_task(self, task_fn, on_done):
        try:
            task_fn(on_done)
        except Exception as e:
            log_error(f"Error running queue task: {e}\n{traceback.format_exc()}")
            _profiler_log("PROPOSAL_QUEUE:ERROR", f"Error running queue task: {e}", caller="ProposalQueue:_run_task")
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
    link_html = make_card_link(source_nid, source_recto_badge)
    current_field_content = target_note.fields[fidx].strip()
    if not current_field_content or current_field_content == '<br>':
        target_note.fields[fidx] = link_html
    else:
        target_note.fields[fidx] = current_field_content + '<br>' + link_html
    mw.col.update_note(target_note)


def _propose_mirror_links_group(editor, source_note, items, on_done):
    """Propose de lier la carte source dans les cartes cibles de manière groupée."""
    try:
        if not source_note or not source_note.id:
            on_done()
            return
            
        valid_targets = []
        source_nid = str(source_note.id)
        
        for target_nid_str, _ in items:
            try:
                target_nid = int(target_nid_str)
                target_note = None
                try:
                    target_note = mw.col.get_note(target_nid)
                except Exception:
                    try:
                        card = mw.col.get_card(target_nid)
                        if card:
                            target_note = card.note()
                    except Exception:
                        pass
                if not target_note:
                    continue
                
                # Vérifier si la carte A est déjà liée dans la B
                if _is_note_linked_in_target(source_note, target_note):
                    continue
                
                # Determine target field
                fidx_target = _find_target_field_idx(target_note, source_note)
                valid_targets.append((str(target_note.id), target_note, fidx_target))
            except Exception:
                pass
                
        if not valid_targets:
            _profiler_log("MIRROR_LINKS", "no valid_targets found (already linked or nonexistent)", caller="_propose_mirror_links_group")
            on_done()
            return
            
        parent = (
            getattr(editor, 'parentWindow', None)
            or (editor.widget.window() if getattr(editor, 'widget', None) else None)
            or (mw.app.activeWindow() if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow') else None)
            or mw
        )
        _profiler_log("MIRROR_LINKS", f"opening MirrorSelectionDialog with {len(valid_targets)} targets (parent={type(parent).__name__})", caller="_propose_mirror_links_group")
        
        dialog = MirrorSelectionDialog(parent, valid_targets, source_note=source_note)
        dialog.raise_()
        dialog.activateWindow()
        result = dialog.exec()
        
        accepted_code = getattr(QDialog.DialogCode, "Accepted", None) or getattr(QDialog, "Accepted", 1)
        _profiler_log("MIRROR_LINKS", f"MirrorSelectionDialog closed with result={result} (accepted={result == accepted_code})", caller="_propose_mirror_links_group")
        
        if result == accepted_code:
            selected_targets = dialog.get_selected_targets()
            if selected_targets:
                source_nid = str(source_note.id)
                for nid_str, target_note, fidx, custom_recto in selected_targets:
                    # Add mirror badge to the target note
                    # Utiliser le label validé dans la dialog (custom_recto) pour le badge miroir dans la cible
                    _add_badge_to_note(int(nid_str), fidx, source_nid, custom_recto)
                    
                tooltip(f"Liens miroirs créés dans {len(selected_targets)} cartes.")
                
                # Refresh assistant side panel
                from .editor_assistant import update_assistant
                if editor:
                    update_assistant(editor)
    except Exception as e:
        log_error(f"Error in _propose_mirror_links_group: {e}\n{traceback.format_exc()}")
        _profiler_log("MIRROR_LINKS:ERROR", f"Exception in _propose_mirror_links_group: {e}", caller="_propose_mirror_links_group")
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
                parent = (
                    getattr(editor, 'parentWindow', None)
                    or (editor.widget.window() if getattr(editor, 'widget', None) else None)
                    or (mw.app.activeWindow() if hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow') else None)
                    or mw
                )
                dialog = CrossLinksSelectionDialog(parent, links_to_create)
                dialog.raise_()
                dialog.activateWindow()
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
                        update_assistant(editor)
            finally:
                on_done()
                        
        called = False
        def _safe_got_field_idx(idx_str):
            nonlocal called
            if called:
                return
            called = True
            _got_field_idx(idx_str)

        try:
            editor.web.evalWithCallback(
                "(typeof window._ednSavedFieldIndex !== 'undefined') ? window._ednSavedFieldIndex : -1;",
                _safe_got_field_idx
            )
            QTimer.singleShot(400, lambda: _safe_got_field_idx(-1))
        except Exception:
            _safe_got_field_idx(-1)
            
    except Exception as e:
        log_error(f"Cross links error: {e}\n{traceback.format_exc()}")
        _profiler_log("CROSS_LINKS:ERROR", f"Cross links error: {e}", caller="_propose_cross_links")
        on_done()


def _clean_text_for_comparison(text: str) -> str:
    if not text:
        return ""
    import html
    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'^[•\-\*\d\.\)\s]+', '', text).strip()
    text = re.sub(r'[\s:;]+$', '', text).strip().lower()
    return text


def _detect_link_insertion_mode(text_before: str, has_dash: bool, candidate_names: list) -> str:
    """
    Détermine le mode d'insertion du lien :
    - 'bare'      : le tiret est déjà présent sur la ligne -> insère uniquement <kbd>[nid]</kbd>
    - 'dash_only' : le nom de la carte est déjà saisi sur la ligne mais sans tiret -> insère &nbsp;—&nbsp;<kbd>[nid]</kbd>
    - 'full'      : ligne vide ou texte différent -> insère <span class="edn-desc">Nom</span>&nbsp;—&nbsp;<kbd>[nid]</kbd>
    """
    if has_dash:
        return "bare"
    cleaned_line = _clean_text_for_comparison(text_before)
    if not cleaned_line:
        return "full"
    for cand in candidate_names:
        c_clean = _clean_text_for_comparison(cand)
        if not c_clean:
            continue
        if cleaned_line == c_clean or cleaned_line.endswith(c_clean):
            return "dash_only"
        if len(cleaned_line) >= 3 and (c_clean.startswith(cleaned_line) or c_clean.endswith(cleaned_line)):
            return "dash_only"
        c_noparen = re.sub(r'\s*[\(\[].*?[\)\]]', '', c_clean).strip()
        if c_noparen and (cleaned_line == c_noparen or cleaned_line.endswith(c_noparen)):
            return "dash_only"
    return "full"


def _profiler_log(category: str, message: str, elapsed_ms: Optional[float] = None, caller: Optional[str] = None):
    try:
        import sys
        profiler = sys.modules.get("00_EDN_Debug_Profiler")
        if profiler and hasattr(profiler, "log"):
            profiler.log(category, message, elapsed_ms=elapsed_ms, caller="LinkedCardsDialog")
            return
    except Exception:
        pass
    try:
        from .logger import log
        ms_str = f" | took {elapsed_ms:.2f}ms" if elapsed_ms is not None else ""
        log(f"[{category}] {message}{ms_str}")
    except Exception:
        pass


class _LinkedCardsAppClickFilter(QObject):
    """Filtre d'evenement global pour fermer la preview des que l'utilisateur clique hors du bouton ou de la popup."""
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog

    def eventFilter(self, watched, event):
        try:
            etype = event.type()
            mouse_press = getattr(QEvent.Type, "MouseButtonPress", None) or getattr(QEvent, "MouseButtonPress", None)
            if etype == mouse_press:
                if not self.dialog.isVisible():
                    self.dialog.hide_preview_popup()
                    return False

                dlg = getattr(self.dialog, '_preview_dlg', None)
                if dlg and dlg.isVisible():
                    pos = QCursor.pos()
                    # Si le clic se produit dans la popup preview elle-meme, laisser passer
                    if dlg.geometry().contains(pos):
                        return False

                    # Si le clic est sur le bouton Voir actif, laisser passer
                    btn = getattr(self.dialog, '_preview_current_widget', None)
                    if btn:
                        try:
                            from aqt.qt import sip
                            if not sip.isdeleted(btn) and btn.isVisible():
                                btn_global = btn.mapToGlobal(QPoint(0, 0))
                                btn_rect = QRect(btn_global, btn.size())
                                if btn_rect.contains(pos):
                                    return False
                        except Exception:
                            pass

                    # Tout autre clic (dialogue, table, fermeture, autre fenetre) ferme la preview
                    self.dialog.hide_preview_popup()
        except Exception:
            pass
        return False


class LinkedCardsDialog(QDialog):
    COL_RECTO = 0
    COL_SCORE = 1
    COL_NID = 2
    COL_ACTIONS = 3

    def __init__(self, editor, selected_text=""):
        parent = editor.parentWindow if hasattr(editor, 'parentWindow') else mw
        super().__init__(parent)
        self.editor = editor
        self.inserter = LinkInserter(editor)
        self.target_fidx = getattr(editor, 'currentField', None)
        if self.target_fidx is None:
            self.target_fidx = getattr(editor, 'last_field_index', None)
        self.selected_nid = None
        self.selected_recto = None
        self.selected_text = selected_text  # Texte à afficher dans le badge (mode hyperlien)
        self.setWindowTitle("Lier les cartes")
        self.setMinimumSize(480, 500)
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
        self._scores_map = {}
        self._all_target_nids = []
        self._search_offset = 0
        self._current_results_type = None
        self._current_query = ""
        self._search_type_label = "résultats"
        
        from collections import OrderedDict
        self._preview_cache = OrderedDict()
        self._prefetch_timer = None
        self._pending_prefetch_nids = []
        self._card_prefs_cache = None
        self._preview_edn_css_cache = None
        self._preview_shell_ready = False
        self._preview_current_widget = None
        self._preview_current_nid = None
        self._pending_show_nid = None
        self._pending_show_widget = None

        self._app_click_filter = _LinkedCardsAppClickFilter(self)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self._app_click_filter)

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
                        self._search_timer.start(380)
                    return
            except Exception as e:
                print(f"Error in BetterSearch integration: {e}")

        if not text.strip():
            self._search_timer.stop()
            self.do_search("")
        else:
            self._search_timer.start(380)  # Délai de 380ms

    def _on_search_timeout(self):
        self.do_search(self.search_bar.text())

    def keyPressEvent(self, event):
        """Transmet les touches p/r/n/z/Escape à la preview webview même si le focus est dans le GUI."""
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if hasattr(self, '_preview_dlg') and self._preview_dlg and self._preview_dlg.isVisible():
                self.hide_preview_popup()
                event.accept()
                return

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
            item_nid = self.results_table.item(next_row, self.COL_NID).text()
            btn = self.results_table.cellWidget(next_row, self.COL_ACTIONS)
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
            hint = QLabel(f"Mode hyperlien — Les mots <b>'{self.selected_text}'</b> deviendront cliquables vers la carte choisie.")
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
        
        self.results_table.setColumnCount(4)
        self.results_table.setHorizontalHeaderLabels(["Recto", "Score", "NID", "Actions"])
        self.results_table.horizontalHeader().setSectionResizeMode(self.COL_RECTO, QHeaderView.ResizeMode.Stretch)
        self.results_table.setColumnWidth(self.COL_SCORE, 65)
        self.results_table.setColumnWidth(self.COL_NID, 115)
        self.results_table.setColumnWidth(self.COL_ACTIONS, 80)
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
        
    def moveEvent(self, event):
        super().moveEvent(event)
        self._update_preview_position()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview_position()

    def hideEvent(self, event):
        self.hide_preview_popup()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.hide_preview_popup()
        self._cleanup_on_close()
        super().closeEvent(event)

    def reject(self):
        self.hide_preview_popup()
        self._cleanup_on_close()
        super().reject()

    def _should_keep_preview_alive(self):
        """Verifie si la preview popup doit rester ouverte malgre un evenement de focus/activation."""
        if getattr(self, '_context_menu_active', False):
            return True
        try:
            preview_dlg = getattr(self, '_preview_dlg', None)
            if preview_dlg is not None:
                try:
                    preview_dlg.objectName()
                    if preview_dlg.isVisible() and preview_dlg.geometry().contains(QCursor.pos()):
                        return True
                except (RuntimeError, Exception):
                    pass
        except Exception:
            pass
        return False

    def changeEvent(self, event):
        try:
            etype = event.type()
            act_change = getattr(QEvent.Type, "ActivationChange", None) or getattr(QEvent, "ActivationChange", None)
            state_change = getattr(QEvent.Type, "WindowStateChange", None) or getattr(QEvent, "WindowStateChange", None)
            if etype == act_change:
                if not self.isActiveWindow():
                    if not self._should_keep_preview_alive():
                        self.hide_preview_popup()
            elif etype == state_change:
                if self.isMinimized():
                    self.hide_preview_popup()
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
        if hasattr(self, '_app_click_filter') and self._app_click_filter:
            app = QApplication.instance()
            if app:
                try:
                    app.removeEventFilter(self._app_click_filter)
                except Exception:
                    pass
            self._app_click_filter = None
        if hasattr(self, '_prefetch_timer') and self._prefetch_timer:
            try:
                self._prefetch_timer.stop()
            except Exception:
                pass
            self._prefetch_timer = None
        if hasattr(self, '_preview_cache'):
            self._preview_cache.clear()
        if hasattr(self, '_preview_web') and self._preview_web:
            try:
                self._preview_web.cleanup()
            except:
                pass
            self._preview_web = None
        if hasattr(self, '_preview_dlg') and self._preview_dlg:
            try:
                self._preview_dlg.hide()
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
                    function _getAllShadowRoots(root) {
                        root = root || document.body;
                        var roots = [];
                        if (!root) return roots;
                        if (root.shadowRoot) roots.push(root.shadowRoot);
                        var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                        for (var i = 0; i < all.length; i++) {
                            if (all[i].shadowRoot) {
                                roots.push(all[i].shadowRoot);
                                roots.push(..._getAllShadowRoots(all[i].shadowRoot));
                            }
                        }
                        return roots;
                    }
                    function _queryAll(sel) {
                        var fn = window._ednQueryAllRoots;
                        if (typeof fn === 'function') return fn(sel);
                        var res = Array.from(document.querySelectorAll(sel));
                        var sRoots = _getAllShadowRoots(document.body);
                        for (var i = 0; i < sRoots.length; i++) {
                            var list = sRoots[i].querySelectorAll(sel);
                            for (var j = 0; j < list.length; j++) {
                                if (!res.includes(list[j])) res.push(list[j]);
                            }
                        }
                        return res;
                    }

                    var markers = _queryAll('[id="edn-cursor-marker"]');
                    var changedEditables = new Set();
                    markers.forEach(function(m) { 
                        try { 
                            var curr = m.parentNode;
                            while (curr) {
                                if (curr.tagName === 'ANKI-EDITABLE' || curr.isContentEditable) {
                                    changedEditables.add(curr);
                                    break;
                                }
                                curr = curr.parentNode || (curr.getRootNode && curr.getRootNode().host) || curr.host;
                            }
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
            note = _get_editor_note(self.editor)
            if note and note.fields:
                changed = False
                for i, field in enumerate(note.fields):
                    cleaned = _MARKER_RE.sub('', field)
                    if cleaned != field:
                        note.fields[i] = cleaned
                        changed = True
                if changed:
                    # Sauvegarder la note sans les markers en preservant le focus
                    editor_save_now(self.editor, keep_focus=True)

            # Restaurer explicitement le focus sur le champ actif precedemment memorise
            if self.editor and self.editor.web:
                self.editor.web.eval("""
                    (function() {
                        var targetIdx = (typeof window._ednSavedFieldIndex === 'number' && window._ednSavedFieldIndex >= 0)
                            ? window._ednSavedFieldIndex
                            : (typeof window._ednLastActiveField === 'number' ? window._ednLastActiveField : -1);
                        if (targetIdx >= 0) {
                            if (typeof globalThis.focusField === 'function') {
                                globalThis.focusField(targetIdx);
                            } else {
                                var editables = (typeof window._ednQueryAllRoots === 'function')
                                    ? window._ednQueryAllRoots('anki-editable')
                                    : Array.from(document.querySelectorAll('anki-editable'));
                                if (!editables.length) {
                                    editables = (typeof window._ednQueryAllRoots === 'function')
                                        ? window._ednQueryAllRoots('[contenteditable="true"]')
                                        : Array.from(document.querySelectorAll('[contenteditable="true"]'));
                                }
                                var ed = editables[targetIdx];
                                if (ed) {
                                    var inner = (ed.shadowRoot && ed.shadowRoot.querySelector('[contenteditable]')) || ed;
                                    if (inner && typeof inner.focus === 'function') {
                                        inner.focus();
                                    }
                                }
                            }
                        }
                    })();
                """)
        except Exception:
            pass

    def append_results_to_table(self, results):
        is_night = _is_night_mode()
        for res_item in results:
            if isinstance(res_item, dict):
                nid_str = str(res_item.get("nid", ""))
                recto_clean = str(res_item.get("recto", ""))
                score = res_item.get("score")
            elif isinstance(res_item, (list, tuple)):
                nid_str = str(res_item[0])
                recto_clean = str(res_item[1])
                score = res_item[2] if len(res_item) >= 3 else None
            else:
                continue

            if score is None and hasattr(self, '_scores_map') and self._scores_map:
                score = self._scores_map.get(nid_str)

            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            
            i_recto = QTableWidgetItem(recto_clean)
            i_recto.setData(Qt.ItemDataRole.UserRole, {'nid': nid_str, 'recto': recto_clean, 'score': score})
            self.results_table.setItem(row, self.COL_RECTO, i_recto)
            
            score_text = str(score) if (score is not None and score > 0) else ("-" if score is None else "0")
            i_score = QTableWidgetItem(score_text)
            try:
                i_score.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            except AttributeError:
                i_score.setTextAlignment(Qt.AlignCenter)
            if score is not None and score > 0:
                font = i_score.font()
                font.setBold(True)
                i_score.setFont(font)
                color = QColor("#a78bfa") if is_night else QColor("#7c3aed")
                i_score.setForeground(color)
            self.results_table.setItem(row, self.COL_SCORE, i_score)

            i_nid = QTableWidgetItem(nid_str)
            try:
                i_nid.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            except AttributeError:
                i_nid.setTextAlignment(Qt.AlignCenter)
            self.results_table.setItem(row, self.COL_NID, i_nid)
            
            btn = self._make_voir_button(nid_str)
            self.results_table.setCellWidget(row, self.COL_ACTIONS, btn)

    def load_next_page(self):
        if self._current_results_type == "suggestions":
            # load next chunk of suggestions
            start = self._search_offset
            end = start + 50
            chunk = self._all_target_nids[start:end]
            if not chunk:
                self.load_more_btn.setVisible(False)
                if start == 0:
                    self.results_label.setText("Aucun lien proposé")
                return
            
            self.append_results_to_table(chunk)
            self._search_offset = min(end, len(self._all_target_nids))
            
            # Show remaining count
            remaining = len(self._all_target_nids) - self._search_offset
            if remaining > 0:
                self.load_more_btn.setText(f"Charger plus de liens proposés ({remaining} restantes)")
                self.load_more_btn.setVisible(True)
            else:
                self.load_more_btn.setVisible(False)
                
            self.results_label.setText(f"{self.results_table.rowCount()} liens proposés (classés par score)")
            
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
                    self.results_label.setText(f"{row_count} {self._search_type_label} (classés par score)")
                
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
        if hasattr(self, '_prefetch_timer') and self._prefetch_timer:
            self._prefetch_timer.stop()
        
        if not query:
            config = mw.addonManager.getConfig(__name__) or {}
            if config.get("suggestions_de_liens", True):
                self._current_results_type = "suggestions"
                self.results_label.setText("Chargement des liens proposés...")
                
                # Check if suggestions are cached
                if self._suggestions_cache is not None:
                    self._all_target_nids = self._suggestions_cache
                    self._search_offset = 0
                    self.load_next_page()
                    return
                
                # Extraction sécurisée des données de la note sur le thread principal
                ed_note = _get_editor_note(self.editor)
                current_nid = str(ed_note.id) if ed_note and getattr(ed_note, 'id', None) else None
                note_fields = list(ed_note.fields) if ed_note else []
                from .editor_assistant import extract_nids_from_fields
                outgoing_nids = extract_nids_from_fields(ed_note) if ed_note else set()

                if not current_nid:
                    self.results_label.setText("Aucun lien proposé (note non enregistrée)")
                    return

                # If not cached, compute in background
                def bg_suggestions(col):
                    from .editor_assistant import _compute_suggestions, extract_nids_from_fields_list
                    try:
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
                        suggestions = _compute_suggestions(col, current_nid, note_fields, existing_nids, max_results=100)
                        formatted = [(s["nid"], s["recto"], s.get("score", 0)) for s in suggestions]
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
                    self._scores_map = {item[0]: item[2] for item in results_or_err if len(item) >= 3}
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
        source_note = _get_editor_note(self.editor)
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
                return ("nid", target_ids, {str(n): 100 for n in target_ids})

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
                    return ("text", [], {})

                if source_nid:
                    candidate_nids = [n for n in candidate_nids if str(n) != source_nid]

                if not candidate_nids:
                    return ("text", [], {})

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
                scores_map = {str(cid): score for score, cid in scored_candidates}
                return ("text", sorted_ids, scores_map)
            except Exception as e:
                return ("error", str(e), {})

        def on_success(res):
            try:
                self.objectName()
            except RuntimeError:
                return

            if current_seq != self._search_seq:
                return

            search_type = res[0]
            results_or_err = res[1]
            if search_type == "error":
                self.results_label.setText(results_or_err)
                return

            self._scores_map = res[2] if len(res) >= 3 and isinstance(res[2], dict) else {}
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
        source_note = _get_editor_note(self.editor)
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
                    scores_map = {str(cid): score for score, cid in scored_candidates}
                    return (target_ids, scores_map)
                except Exception as fe:
                    print("Erreur fuzzy score:", fe)

            return (target_ids, {})

        def on_fuzzy_success(res):
            try:
                self.objectName()
            except RuntimeError:
                return
            if seq != self._search_seq:
                return

            if isinstance(res, tuple):
                results = res[0]
                self._scores_map = res[1] if len(res) >= 2 else {}
            else:
                results = res
                self._scores_map = {}

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
                self._hover_timer = QTimer(self)
                self._hover_timer.setSingleShot(True)
                self._hover_timer.timeout.connect(self._on_hover_timeout)
                
            def on_click(self):
                self._hover_timer.stop()
                open_card_in_browser(self.nid)
                self.dialog_parent.hide_preview_popup()

            def on_context_menu(self, pos):
                self._hover_timer.stop()
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
                self._hover_timer.start(70)
                super().enterEvent(event)

            def _on_hover_timeout(self):
                try:
                    self.objectName()
                except RuntimeError:
                    return
                if not self.dialog_parent.isVisible():
                    return
                self.dialog_parent._preview_current_widget = self
                self.dialog_parent.show_preview_popup(self.nid, position_widget=self)
                
            def leaveEvent(self, event):
                self._hover_timer.stop()
                from aqt.qt import QTimer
                try:
                    dlg = self.dialog_parent
                    QTimer.singleShot(350, lambda: self._safe_check_hide())
                except RuntimeError:
                    pass
                try:
                    super().leaveEvent(event)
                except RuntimeError:
                    pass

            def focusOutEvent(self, event):
                self._hover_timer.stop()
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
        nid_item = self.results_table.item(row, self.COL_NID)
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
                    # Verifier si le bouton est toujours visible dans le viewport de la table
                    if btn.isVisible() and btn.rect().intersects(self.results_table.viewport().rect()):
                        self._update_preview_position()
                    else:
                        self.hide_preview_popup()
                except (RuntimeError, Exception):
                    self._preview_current_widget = None
                    self.hide_preview_popup()

        # Detection du defilement vers le bas pour charger plus automatiquement
        try:
            scrollbar = self.results_table.verticalScrollBar()
            if scrollbar.value() >= scrollbar.maximum() - 5:
                if hasattr(self, 'load_more_btn') and self.load_more_btn.isVisible() and self.load_more_btn.isEnabled():
                    self.load_next_page()
        except Exception as e:
            print("Error in scroll auto-load:", e)

    def _update_preview_position(self, position_widget=None, force=False):
        if not hasattr(self, '_preview_dlg') or not self._preview_dlg:
            return
        if not force and not self._preview_dlg.isVisible():
            return
        dlg = self._preview_dlg
        if position_widget is not None:
            self._preview_current_widget = position_widget
        btn = getattr(self, '_preview_current_widget', None)
        if btn is not None:
            try:
                from aqt.qt import sip
                if sip.isdeleted(btn):
                    btn = None
                    self._preview_current_widget = None
            except Exception:
                btn = None

        screen_obj = self.screen()
        if not screen_obj:
            screen_obj = QApplication.screenAt(self.geometry().center())
        if not screen_obj:
            screen_obj = QApplication.primaryScreen()
        screen = screen_obj.availableGeometry() if screen_obj else QRect(0, 0, 1920, 1080)

        preview_w = dlg.width() if dlg.width() > 100 else 680
        preview_h = dlg.height() if dlg.height() > 50 else 250

        gui_rect = self.geometry()
        space_right = screen.right() - gui_rect.right()
        space_left = gui_rect.left() - screen.left()

        # Préférer le côté ayant assez de place pour loger la largeur du popup
        if space_right >= preview_w + 10:
            preview_x = gui_rect.right() + 8
        elif space_left >= preview_w + 10:
            preview_x = gui_rect.left() - preview_w - 8
        else:
            if space_right >= space_left:
                preview_x = max(screen.left() + 8, screen.right() - preview_w - 8)
            else:
                preview_x = min(screen.right() - preview_w - 8, screen.left() + 8)

        if btn and btn.isVisible():
            try:
                btn_global_y = btn.mapToGlobal(QPoint(0, 0)).y()
                preview_y = btn_global_y - 20
            except Exception:
                preview_y = gui_rect.top() + 40
        else:
            preview_y = gui_rect.top() + 40

        if preview_y + preview_h > screen.bottom() - 10:
            preview_y = max(screen.top() + 10, screen.bottom() - preview_h - 10)
        if preview_y < screen.top() + 10:
            preview_y = screen.top() + 10

        dlg.move(preview_x, preview_y)

    def _get_cached_card_prefs(self):
        if not hasattr(self, '_card_prefs_cache') or self._card_prefs_cache is None:
            try:
                from .edn_menu.card_styles import load_prefs
                self._card_prefs_cache = load_prefs()
            except Exception:
                self._card_prefs_cache = {}
        return self._card_prefs_cache

    def _get_cached_preview_css(self):
        if not hasattr(self, '_preview_edn_css_cache') or self._preview_edn_css_cache is None:
            self._preview_edn_css_cache = _load_preview_edn_css()
        return self._preview_edn_css_cache

    def _prepare_card_preview_data(self, nid):
        nid_str = str(nid)
        if not hasattr(self, '_preview_cache'):
            from collections import OrderedDict
            self._preview_cache = OrderedDict()
        if nid_str in self._preview_cache:
            return self._preview_cache[nid_str]

        try:
            import re as re2
            note = mw.col.get_note(int(nid_str))
            cards = note.cards()
            if not cards:
                return None
            card = cards[0]
            rendered = card.answer()
            rendered = _clean_kbd_html(rendered)

            isolated = re2.sub(r'(?<!\w)id=(["\'])(.*?)\1', r'id="dlg_preview_\2"', rendered)
            isolated = re2.sub(r'src=(["\'])(?![a-z]+:)(?!/)([^"\']+)\1', r'src="/\2"', isolated, flags=re2.IGNORECASE)
            isolated = re2.sub(r'toggle\([\"\'](.*?)[\"\']\)', r"toggle('dlg_preview_\1')", isolated)
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
                    isolated = re2.sub(rf'(<div[^>]*id=["\'](?:dlg_preview_)?{sec}["\'][^\'>]*style=["\'])([^\'\"]*)', r'\1display: none !important;\2', isolated, flags=re2.IGNORECASE)
                    isolated = re2.sub(rf'(<div[^>]*id=["\'](?:dlg_preview_)?{sec}["\'])(?![^>]*style=)', r'\1 style="display: none !important;"', isolated, flags=re2.IGNORECASE)

            isolated = re2.sub(
                r'(<div[^>]*class=["\'][^"\']*(section)[^"\'][^"\']*["\'][^>]*style=["\'])display:\s*none;?',
                r'\1display: flex !important;', isolated, flags=re2.IGNORECASE)

            model_css = note.note_type().get('css', '')
            sanitized_model_css = _sanitize_model_css_for_preview(model_css) if model_css else ""

            prefs = self._get_cached_card_prefs()
            show_rank = prefs.get("showRankBadge", True)
            show_flags = prefs.get("showFlagBorders", True)
            show_dual_flags = prefs.get("previewDualFlags", False)
            try:
                virtual_flag_color = _get_virtual_flag_color(note, cards, prefs=prefs)
            except Exception:
                virtual_flag_color = ""

            data = {
                "nid": nid_str,
                "isolated": isolated,
                "model_css": sanitized_model_css,
                "show_rank": show_rank,
                "show_flags": show_flags,
                "show_dual_flags": show_dual_flags,
                "virtual_flag_color": virtual_flag_color,
            }
            if len(self._preview_cache) >= 40:
                self._preview_cache.popitem(last=False)
            self._preview_cache[nid_str] = data
            return data
        except Exception as e:
            print(f"Error preparing preview data for nid {nid}: {e}")
            return None

    def _schedule_economic_prefetch(self, current_nid):
        if not hasattr(self, 'results_table') or self.results_table.rowCount() == 0:
            return

        curr_row = -1
        for r in range(self.results_table.rowCount()):
            item = self.results_table.item(r, self.COL_NID)
            if item and item.text() == current_nid:
                curr_row = r
                break

        if curr_row < 0:
            return

        # Precharger les 2 cartes en dessous de facon econome
        nids_below = []
        for r in range(curr_row + 1, min(curr_row + 3, self.results_table.rowCount())):
            item = self.results_table.item(r, self.COL_NID)
            if item and item.text():
                nid_val = item.text()
                if not (hasattr(self, '_preview_cache') and nid_val in self._preview_cache):
                    nids_below.append(nid_val)

        if not nids_below:
            return

        self._pending_prefetch_nids = nids_below
        if not hasattr(self, '_prefetch_timer') or self._prefetch_timer is None:
            self._prefetch_timer = QTimer(self)
            self._prefetch_timer.setSingleShot(True)
            self._prefetch_timer.timeout.connect(self._run_economic_prefetch)
        self._prefetch_timer.start(250)

    def _run_economic_prefetch(self):
        if not self.isVisible():
            return
        if not hasattr(self, '_pending_prefetch_nids') or not self._pending_prefetch_nids:
            return
        import time
        t0 = time.perf_counter()
        count = 0
        for nid in list(self._pending_prefetch_nids):
            if hasattr(self, '_preview_cache') and nid in self._preview_cache:
                continue
            self._prepare_card_preview_data(nid)
            count += 1
        self._pending_prefetch_nids = []
        if count > 0:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            _profiler_log("PREVIEW:PREFETCH", f"Prechargement economique termine: {count} carte(s) en dessous", elapsed_ms=elapsed_ms)

    def _build_full_preview_html(self, data=None, is_night=False):
        import json
        addon_package = mw.addonManager.addonFromModule(__name__)
        css_link = f'<link rel="stylesheet" href="/_addons/{addon_package}/user_files/clickable_cards.css">'

        model_css_content = data.get('model_css', '') if data else ''
        model_css_tag = f'<style id="edn_model_style">\n{model_css_content}\n</style>'

        preview_css = self._get_cached_preview_css()
        blacklist_selectors = [
            f"#dlg_preview_{sec}" for sec in [
                "sourcesMegaContainer", "commentsMegaContainer", "tagsMegaContainer",
                "erreursFaitesMegaContainer", "mnemonicsMegaContainer", "infoSupplementairesMegaContainer"
            ]
        ]
        blacklist_selectors.append("#dlg_preview_FSRS_status")
        blacklist_css = ", ".join(blacklist_selectors) + " { display: none !important; }\n"
        blacklist_css += ".edn-specialty-icons { display: none !important; }\n"
        scoped_css = f'<style id="edn_scoped_style">\n{blacklist_css}{preview_css}\n</style>'

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

        main_script = r"""
        <script>
        window.MathJax = window.MathJax || {};
        window.MathJax.loader = window.MathJax.loader || {};
        window.MathJax.loader.versions = window.MathJax.loader.versions || {};
        window.MathJax.loader.versions['[tex]/mathtools'] = '3.0.0';

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

                    function isEmptyInlineNode(node) {
                        if (!node) return false;
                        if (node.nodeType === Node.TEXT_NODE) {
                            return node.textContent.replace(/\u00A0/g, ' ').trim() === "";
                        }
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            if (node.nodeName === 'BR' || node.nodeName === 'DIV' || node.nodeName === 'P' || node.nodeName === 'KBD' || node.nodeName === 'IMG' || node.nodeName === 'HR') {
                                return false;
                            }
                            if (node.querySelector && node.querySelector('kbd, img, br, div, p')) {
                                return false;
                            }
                            return (node.textContent || "").replace(/\u00A0/g, ' ').trim() === "";
                        }
                        return false;
                    }

                    var child = container.firstChild;
                    while (child) {
                        if (isEmptyInlineNode(child)) {
                            child = child.nextSibling;
                        } else if (child.nodeName === 'BR') {
                            var next = child.nextSibling;
                            child.parentNode.removeChild(child);
                            child = next;
                        } else {
                            break;
                        }
                    }

                    var blocks = [container].concat(Array.from(container.querySelectorAll('div')));
                    blocks.forEach(function(block) {
                        var lastChild = block.lastChild;
                        while (lastChild) {
                            if (isEmptyInlineNode(lastChild)) {
                                lastChild = lastChild.previousSibling;
                            } else if (lastChild.nodeName === 'BR') {
                                var prev = lastChild.previousSibling;
                                lastChild.parentNode.removeChild(lastChild);
                                lastChild = prev;
                            } else {
                                break;
                            }
                        }
                    });

                    var kbds = container.querySelectorAll('kbd.clickable_cards');
                    kbds.forEach(function(kbd) {
                        var next = kbd.nextSibling;
                        var insertBeforeNode = next;
                        while (next && isEmptyInlineNode(next)) {
                            next = next.nextSibling;
                            insertBeforeNode = next;
                        }
                        if (next && next.nodeName !== "BR" && next.nodeName !== "DIV" && next.nodeName !== "P" && next.nodeName !== "KBD") {
                            var br = document.createElement('br');
                            kbd.parentNode.insertBefore(br, insertBeforeNode);
                        }
                    });
                });
            } catch(e) {}
        }

        function _cleanAll() {
            _hideFsrsInfo();
            _hideEmptySections();
            _capitalizePreview(document);
        }

        window.toggle = window.toggle || function(id) {
            var el = document.getElementById(id);
            if (el) el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
        };

        document.addEventListener('click', function(e) {
            var el = e.target;
            while (el && el !== document.body) {
                if (el.getAttribute && el.getAttribute('data-onclick')) {
                    var clickAttr = el.getAttribute('data-onclick').trim().replace(/;\s*$/, '');
                    var toggleMatch = clickAttr.match(/^toggle\(\s*['"]?([^'"]*?)['"]?\s*\)$/);
                    if (toggleMatch) {
                        var fn = window.toggle || (typeof toggle !== 'undefined' ? toggle : null);
                        if (fn) {
                            try {
                                fn(toggleMatch[1]);
                            } catch(err) {
                                console.error("Error executing toggle:", err);
                            }
                        }
                    } else {
                        var ctMatch = clickAttr.match(/^cards_ct_click\(\s*['"]?([a-zA-Z0-9_-]+)['"]?(?:\s*,\s*(true|false))?\s*\)$/);
                        if (ctMatch) {
                            var fnCt = window.cards_ct_click || (typeof cards_ct_click !== 'undefined' ? cards_ct_click : null);
                            if (fnCt) {
                                try {
                                    if (ctMatch[2]) {
                                        fnCt(ctMatch[1], ctMatch[2] === 'true');
                                    } else {
                                        fnCt(ctMatch[1]);
                                    }
                                } catch(err) {
                                    console.error("Error executing cards_ct_click:", err);
                                }
                            }
                        } else {
                            console.warn("Blocked non-whitelisted data-onclick:", clickAttr);
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
                if (nid === _guiLastHoverNid) return;
                if (_guiHoverTimer) { clearTimeout(_guiHoverTimer); _guiHoverTimer = null; }
                _guiHoverTimer = setTimeout(function() {
                    _guiHoverTimer = null;
                    _guiLastHoverNid = nid;
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_hover:' + nid);
                }, 100);
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
                    if (_guiSingleTapTimer) { clearTimeout(_guiSingleTapTimer); _guiSingleTapTimer = null; }
                    _guiLastTapNid = null;
                    _guiLastTapTime = 0;
                    if (typeof pycmd !== 'undefined') pycmd('gui_preview_click:' + nid);
                    if (e.cancelable) e.preventDefault();
                    e.stopPropagation();
                    return;
                } else {
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

        window._guiLastClickTime = 0;
        window._guiLastClickNid = null;
        document.addEventListener("click", function(e) {
            if (_guiLongPressTriggered || _guiTouchRecently) {
                e.preventDefault();
                e.stopPropagation();
                return false;
            }
            if(e.target && e.target.classList && e.target.classList.contains("clickable_cards")) {
                var nid = _gn(e.target);
                if (nid) {
                    var cleanNid = String(nid).trim().replace(/[^0-9]/g, '');
                    if (!cleanNid) return;
                    var now = Date.now();
                    if (window._guiLastClickNid === cleanNid && (now - window._guiLastClickTime < 1000)) return;
                    window._guiLastClickTime = now;
                    window._guiLastClickNid = cleanNid;
                    if(typeof pycmd !== 'undefined') pycmd('gui_preview_click:' + cleanNid);
                }
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

        function _triggerResize() {
            var card = document.getElementById('edn_card_container') || document.querySelector('.card');
            var h = card ? Math.ceil(card.getBoundingClientRect().height) : (document.body ? document.body.scrollHeight : 0);
            if (typeof pycmd !== 'undefined') pycmd('edn_preview_resize:' + h);
        }

        try {
            var cardEl = document.getElementById('edn_card_container') || document.querySelector('.card') || document.body;
            var lastHeight = 0;
            var resizeObserver = new ResizeObserver(function(entries) {
                var card = document.getElementById('edn_card_container') || document.querySelector('.card');
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

        window._applyEdnPreviewStylesParams = function(virtualFlagColor, showRank, showFlags, dualFlags) {
            try {
                var tagsEl = document.getElementById("dlg_preview_tagsData") || document.getElementById("tagsData");
                var tags = tagsEl ? tagsEl.innerHTML : "";
                var rang = tags.includes("Rang::A") ? "A" : tags.includes("Rang::B") ? "B" : tags.includes("Rang::C") ? "C" : "?";

                if (showRank) {
                    var colorA = (typeof edn_prefs !== "undefined" && edn_prefs.rank_color_a) ? edn_prefs.rank_color_a : '#111827';
                    var colorB = (typeof edn_prefs !== "undefined" && edn_prefs.rank_color_b) ? edn_prefs.rank_color_b : '#3182CE';
                    var colorC = (typeof edn_prefs !== "undefined" && edn_prefs.rank_color_c) ? edn_prefs.rank_color_c : '#38A169';
                    var rankColor = rang === 'A' ? colorA : rang === 'B' ? colorB : rang === 'C' ? colorC : null;

                    var bars = document.querySelectorAll('.bar, .barHider');
                    bars.forEach(function(b) {
                        if (!b.classList.contains('edn-rang-' + rang)) {
                            b.classList.add('edn-rang-' + rang);
                        }
                        if (rankColor) {
                            b.style.backgroundColor = rankColor;
                            b.style.borderColor = rankColor;
                        }
                    });
                }

                var realColor = null;
                if (showFlags) {
                    var flagsEl = document.getElementById('dlg_preview_flagsData') || document.getElementById('flagsData') || document.getElementById('dlg_preview_flags') || document.getElementById('flags');
                    var flags = flagsEl ? flagsEl.innerHTML.trim() : "";
                    var flagColorMap = {
                        'flag1': '#E53E3E', 'flag2': '#DD6B20', 'flag3': '#38A169',
                        'flag4': '#3182CE', 'flag5': '#D53F8C', 'flag6': '#00A3C4', 'flag7': '#805AD5'
                    };
                    for (var key in flagColorMap) {
                        if (flags.includes(key)) {
                            realColor = flagColorMap[key];
                            break;
                        }
                    }
                    if (!realColor && tags.includes("Difficile")) realColor = '#E53E3E';
                    if (!realColor && tags.includes("Bleu")) realColor = '#3182CE';
                }

                function applyDualBorder(el, leftColor, rightColor) {
                    if (!el) return;
                    el.style.borderImage = "none";
                    el.style.borderColor = "transparent";
                    var bg = window.getComputedStyle(el).backgroundColor;
                    if (!bg || bg === "transparent" || bg === "rgba(0, 0, 0, 0)") {
                        var isNight = document.body && (document.body.classList.contains("nightMode") || document.body.classList.contains("night_mode"));
                        bg = isNight ? "#373a41" : "#f5f6f7";
                    }
                    var existingBg = el.style.backgroundImage;
                    if (existingBg && !existingBg.includes("linear-gradient")) {
                        el.style.backgroundImage = existingBg + ", linear-gradient(" + bg + ", " + bg + "), linear-gradient(to right, " + leftColor + " 50%, " + rightColor + " 50%)";
                    } else {
                        el.style.backgroundImage = "linear-gradient(" + bg + ", " + bg + "), linear-gradient(to right, " + leftColor + " 50%, " + rightColor + " 50%)";
                    }
                    el.style.backgroundOrigin = "border-box";
                    el.style.backgroundClip = "padding-box, border-box";
                }

                function clearDualBorder(el) {
                    if (!el) return;
                    el.style.borderImage = "none";
                    el.style.backgroundOrigin = "";
                    el.style.backgroundClip = "";
                    var existingBg = el.style.backgroundImage;
                    if (existingBg && existingBg.includes("linear-gradient")) {
                        el.style.backgroundImage = "";
                    }
                }

                var sections = document.getElementsByClassName("section");
                var hiders = document.getElementsByClassName("barHider");
                var bars = document.getElementsByClassName("bar");

                if (showFlags && dualFlags && virtualFlagColor && realColor) {
                    for (var s of sections) applyDualBorder(s, virtualFlagColor, realColor);
                    for (var h of hiders) applyDualBorder(h, virtualFlagColor, realColor);
                    for (var b of bars) { clearDualBorder(b); b.style.borderColor = virtualFlagColor; }
                } else {
                    var activeColor = showFlags ? (realColor || virtualFlagColor) : null;
                    if (activeColor) {
                        for (var s of sections) { clearDualBorder(s); s.style.borderColor = activeColor; }
                        for (var h of hiders) { clearDualBorder(h); h.style.borderColor = activeColor; }
                        for (var b of bars) { clearDualBorder(b); b.style.borderColor = activeColor; }
                    } else {
                        for (var s of sections) { clearDualBorder(s); s.style.borderColor = ""; }
                        for (var h of hiders) { clearDualBorder(h); h.style.borderColor = ""; }
                        for (var b of bars) { clearDualBorder(b); b.style.borderColor = ""; }
                    }
                }
                var cardEl = document.getElementById('edn_card_container') || document.querySelector('.card');
                if (cardEl) {
                    cardEl.style.border = 'none';
                }
            } catch(e) {
                console.error("Error applying preview styles:", e);
            }
        };

        window._ednUpdateCard = function(html, modelCss, virtualFlagColor, showRank, showFlags, dualFlags) {
            try {
                var styleEl = document.getElementById('edn_model_style');
                if (styleEl) styleEl.textContent = modelCss || '';

                var cardEl = document.getElementById('edn_card_container') || document.querySelector('.card');
                if (cardEl) {
                    cardEl.innerHTML = html;
                }
                window.scrollTo(0, 0);

                if (typeof _applyEdnPreviewStylesParams === 'function') {
                    _applyEdnPreviewStylesParams(virtualFlagColor, showRank, showFlags, dualFlags);
                }
                if (typeof _cleanAll === 'function') {
                    _cleanAll();
                }
                if (window.MathJax && MathJax.typesetPromise) {
                    if (MathJax.typesetClear) {
                        try { MathJax.typesetClear(); } catch(e) {}
                    }
                    var target = cardEl || document.body;
                    MathJax.typesetPromise([target]).then(function() {
                        if (typeof _triggerResize === 'function') _triggerResize();
                    }).catch(function(){});
                }
                if (typeof _triggerResize === 'function') {
                    _triggerResize();
                }
                return true;
            } catch(err) {
                console.error("Error in _ednUpdateCard:", err);
                return false;
            }
        };
        </script>
        """

        card_content = data.get('isolated', '') if data else ''
        init_style_call = ""
        if data:
            init_style_call = f"""
            <script>
            (function() {{
                if (typeof _applyEdnPreviewStylesParams === 'function') {{
                    _applyEdnPreviewStylesParams(
                        {json.dumps(data.get('virtual_flag_color', ''))},
                        {'true' if data.get('show_rank', True) else 'false'},
                        {'true' if data.get('show_flags', True) else 'false'},
                        {'true' if data.get('show_dual_flags', False) else 'false'}
                    );
                }}
                if (typeof _cleanAll === 'function') _cleanAll();
            }})();
            </script>
            """

        full_html = (
            stubs_script
            + css_link
            + model_css_tag
            + scoped_css
            + night_script
            + main_script
            + f'<div class="card" id="edn_card_container">{card_content}</div>'
            + init_style_call
        )
        return full_html

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
        self._preview_shell_ready = False

        # Charger la coquille HTML et scripts en arrière-plan pendant la construction du GUI
        try:
            is_night = _is_night_mode()
            shell_html = self._build_full_preview_html(None, is_night)
            try:
                from aqt.qt import QColor
                web.page().setBackgroundColor(QColor(0, 0, 0, 0))
            except Exception:
                pass

            def _on_shell_loaded(ok):
                self._preview_shell_ready = bool(ok)
                if ok and getattr(self, '_pending_show_nid', None) is not None:
                    pending_nid = self._pending_show_nid
                    pending_widget = getattr(self, '_pending_show_widget', None)
                    self._pending_show_nid = None
                    self._pending_show_widget = None
                    if self.isVisible():
                        self.show_preview_popup(pending_nid, position_widget=pending_widget)

            try:
                web.page().loadFinished.connect(_on_shell_loaded)
            except Exception:
                pass

            try:
                web.stdHtml(
                    shell_html,
                    js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"]
                )
            except TypeError:
                web.stdHtml(
                    shell_html,
                    js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"],
                    mathjax=True
                )
        except Exception as e:
            print("Error initializing preview shell in webview:", e)

        dlg.installEventFilter(self)

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
        dlg.destroyed.connect(lambda: _disconnect_app_state(app, self))
        dlg.hide()

    def show_preview_popup(self, nid, position_widget=None):
        """Affiche un popup Qt avec la preview HTML (CSS identique a la revision)."""
        if not self.isVisible():
            return

        import time
        t0 = time.perf_counter()

        if hasattr(self, '_prefetch_timer') and self._prefetch_timer:
            self._prefetch_timer.stop()

        nid_str = str(nid)
        was_cached = hasattr(self, '_preview_cache') and (nid_str in self._preview_cache)
        data = self._prepare_card_preview_data(nid_str)
        if not data:
            return

        is_night = _is_night_mode()

        if not hasattr(self, '_preview_dlg') or self._preview_dlg is None:
            self._init_preview_dlg()
        dlg = self._preview_dlg
        web = self._preview_web

        if is_night:
            dlg.setStyleSheet("#PreviewPopup { border: 2px solid #555; background: #373a41; border-radius: 5px; }")
        else:
            dlg.setStyleSheet("#PreviewPopup { border: 2px solid #c0c1c5; background: #f5f6f7; border-radius: 5px; }")

        self._preview_current_nid = nid_str
        if position_widget:
            self._preview_stack = []

        # Positionner la popup
        self._update_preview_position(position_widget, force=True)

        shell_ready = getattr(self, '_preview_shell_ready', False)
        if not shell_ready:
            self._pending_show_nid = nid_str
            self._pending_show_widget = position_widget
            return

        # Mettre à jour le contenu Web
        updated_in_dom = False

        if shell_ready and web:
            try:
                import json
                script = (
                    f"window._ednUpdateCard("
                    f"{json.dumps(data['isolated'])}, "
                    f"{json.dumps(data['model_css'])}, "
                    f"{json.dumps(data['virtual_flag_color'])}, "
                    f"{'true' if data['show_rank'] else 'false'}, "
                    f"{'true' if data['show_flags'] else 'false'}, "
                    f"{'true' if data['show_dual_flags'] else 'false'}"
                    f");"
                )
                web.eval(script)
                updated_in_dom = True
            except Exception as e:
                updated_in_dom = False

        if not updated_in_dom and web:
            full_preview_html = self._build_full_preview_html(data, is_night)
            try:
                web.stdHtml(
                    full_preview_html,
                    js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"]
                )
            except TypeError:
                try:
                    web.stdHtml(
                        full_preview_html,
                        js=["js/vendor/jquery.min.js", "js/mathjax.js", "js/vendor/mathjax/tex-chtml-full.js"],
                        mathjax=True
                    )
                except Exception:
                    web.stdHtml(full_preview_html)
            self._preview_shell_ready = True

        dlg.show()
        dlg.raise_()

        # Ajustement rapide de la hauteur
        def _quick_resize():
            try:
                if not self.isVisible() or not dlg or not dlg.isVisible():
                    return
                js = """
                (function() {
                    var cardEl = document.getElementById('edn_card_container') || document.querySelector('.card');
                    return cardEl ? Math.ceil(cardEl.getBoundingClientRect().height) : 0;
                })()
                """
                web.evalWithCallback(js, lambda h: self._adjust_preview_height(dlg, h))
            except Exception:
                pass

        QTimer.singleShot(40, _quick_resize)
        QTimer.singleShot(180, _quick_resize)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        mode_str = "DOM in-place" if updated_in_dom else "stdHtml full"
        _profiler_log("PREVIEW", f"Carte nid={nid_str} affichee via {mode_str} (cache={was_cached})", elapsed_ms=elapsed_ms)

        # Planification du préchargement économique des 2 cartes en dessous (après 250ms de lecture)
        self._schedule_economic_prefetch(nid_str)

    def _adjust_preview_height(self, dlg, h):
        if not self.isVisible() or not dlg or not dlg.isVisible():
            return
        if not h:
            return
        try:
            h_val = float(h)
            if h_val <= 10:
                return
        except (ValueError, TypeError):
            return

        try:
            screen_obj = self.screen()
            if not screen_obj:
                screen_obj = QApplication.screenAt(self.geometry().center())
            if not screen_obj:
                screen_obj = QApplication.primaryScreen()
            screen = screen_obj.availableGeometry() if screen_obj else QRect(0, 0, 1920, 1080)

            y_min = screen.top() + 10
            y_max = screen.bottom() - 10
            absolute_max = screen.height() - 40

            try:
                self.objectName()
                if self.height() > 100:
                    y_min = max(y_min, self.y() - 100)
                    y_max = min(y_max, self.y() + self.height() + 100)
            except Exception:
                pass
        except Exception:
            y_min = 10
            y_max = 800
            absolute_max = 600

        new_height = min(absolute_max, max(50, int(h_val) + 4))

        if abs(dlg.height() - new_height) > 4:
            dlg.resize(dlg.width(), new_height)

        if hasattr(self, '_preview_web') and self._preview_web:
            try:
                self._preview_web.eval("document.documentElement.style.overflowY = 'auto';")
            except Exception:
                pass

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
        if not self.isVisible():
            self.hide_preview_popup()
            return
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
                is_in_dlg = self._preview_dlg.geometry().adjusted(-6, -6, 6, 6).contains(pos)
                is_in_btn = False
                if hasattr(self, '_preview_current_widget') and self._preview_current_widget:
                    try:
                        self._preview_current_widget.objectName()
                        btn_global = self._preview_current_widget.mapToGlobal(QPoint(0, 0))
                        btn_rect = QRect(btn_global, self._preview_current_widget.size())
                        is_in_btn = btn_rect.adjusted(-6, -6, 6, 6).contains(pos)
                    except (RuntimeError, Exception):
                        self._preview_current_widget = None
                if is_in_dlg or is_in_btn:
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
        open_card_in_browser(nid)
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
        self.hide_preview_popup()
        rows = self.results_table.selectionModel().selectedRows()
        if not rows: return
        
        items_raw = []
        for row in rows:
            data = self.results_table.item(row.row(), self.COL_RECTO).data(Qt.ItemDataRole.UserRole)
            items_raw.append((data['nid'], data['recto']))
            
        if not items_raw:
            return
        
        self._is_inserting = True
        self.hide()
        
        mirror_req = getattr(self, 'mirror_cb', None) and self.mirror_cb.isChecked()
        _profiler_log("GUI:INSERT_SELECTED", f"User validated insertion: items_raw={items_raw}, selected_text={self.selected_text!r}, mirror_req={mirror_req}, dialog.target_fidx={self.target_fidx}", caller="LinkedCardsDialog:insert_selected")
        
        # Mode hyperlien : autorisé UNIQUEMENT si une seule carte est sélectionnée
        if self.selected_text and len(items_raw) == 1:
            nid = items_raw[0][0]
            _profiler_log("GUI:INSERT_SELECTED", f"Hyperlink mode triggered: nid={nid}, text={self.selected_text!r}", caller="LinkedCardsDialog:insert_selected")
            from aqt.qt import QTimer
            def _insert_hl():
                try:
                    self.inserter.insert_link_with_text(nid, self.selected_text, mirror_requested=mirror_req, target_fidx=self.target_fidx)
                finally:
                    self.close()
            QTimer.singleShot(0, _insert_hl)
        else:
            # Détection ultra-précise : le tiret doit être juste avant le curseur sur la ligne active
            _dash_detection_js = r"""
            (function() {
                try {
                    function _getAllRoots(root) {
                        root = root || document.body;
                        var roots = [];
                        if (!root) return roots;
                        if (root.shadowRoot) roots.push(root.shadowRoot);
                        var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                        for (var i = 0; i < all.length; i++) {
                            if (all[i].shadowRoot) {
                                roots.push(all[i].shadowRoot);
                                roots.push(..._getAllRoots(all[i].shadowRoot));
                            }
                        }
                        return roots;
                    }
                    function _queryAll(sel) {
                        if (typeof window._ednQueryAllRoots === 'function') return window._ednQueryAllRoots(sel);
                        var res = Array.from(document.querySelectorAll(sel));
                        var sRoots = _getAllRoots(document.body);
                        for (var i = 0; i < sRoots.length; i++) {
                            var list = sRoots[i].querySelectorAll(sel);
                            for (var j = 0; j < list.length; j++) {
                                if (!res.includes(list[j])) res.push(list[j]);
                            }
                        }
                        return res;
                    }

                    // 1. Chercher la position de curseur la plus récente
                    var targetRange = null;
                    if (typeof window._ednGetEditorSelection === 'function') {
                        var s = window._ednGetEditorSelection();
                        if (s && s.rangeCount > 0 && s.getRangeAt(0).startContainer.isConnected) {
                            if (typeof window._ednFindEditableAncestor === 'function' ? window._ednFindEditableAncestor(s.getRangeAt(0).startContainer) : true) {
                                targetRange = s.getRangeAt(0);
                            }
                        }
                    }
                    if (!targetRange) {
                        var candRanges = [window._ednLastValidRange, window._ednPersistentRange, window._ednSavedRange];
                        for (var i = 0; i < candRanges.length; i++) {
                            var cr = candRanges[i];
                            if (cr && cr.startContainer && cr.startContainer.isConnected) {
                                if (typeof window._ednFindEditableAncestor === 'function' ? window._ednFindEditableAncestor(cr.startContainer) : true) {
                                    targetRange = cr;
                                    break;
                                }
                            }
                        }
                    }

                    var marker = null;
                    var oldMarkers = _queryAll('[id="edn-cursor-marker"]');

                    // Si nous avons une position de curseur plus récente (notamment si l'utilisateur a tapé dans l'éditeur pendant que le dialogue était ouvert)
                    if (targetRange) {
                        oldMarkers.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });
                        try {
                            var r = targetRange.cloneRange();
                            r.collapse(false);
                            marker = document.createElement("span");
                            marker.id = "edn-cursor-marker";
                            r.insertNode(marker);
                        } catch(e) {
                            marker = null;
                        }
                    } else if (oldMarkers.length > 0) {
                        marker = oldMarkers[oldMarkers.length - 1];
                    }

                    if (!marker) return JSON.stringify({ hasDash: false, textBefore: "" });
                    
                    var node = marker.parentNode;
                    if (!node) return JSON.stringify({ hasDash: false, textBefore: "" });
                    var blockParent = node;
                    while (blockParent && blockParent.tagName !== 'ANKI-EDITABLE' && !blockParent.isContentEditable) {
                        if (['LI', 'P', 'DIV'].indexOf(blockParent.tagName) >= 0) break;
                        blockParent = blockParent.parentNode;
                    }
                    if (!blockParent) blockParent = node;
                    
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
                            if (chn[i].tagName === 'BR') { text = ''; continue; }
                            text += chn[i].textContent || '';
                        }
                        return text;
                    }
                    var trimmedBefore = _textBeforeMarkerInLine(blockParent, marker).trim();
                    var hasDash = /[-—–\u2014\u2013]\s*$/.test(trimmedBefore);
                    var curFieldIdx = (typeof window._ednResolveActiveFieldIndex === 'function')
                        ? window._ednResolveActiveFieldIndex()
                        : -1;
                    if (curFieldIdx < 0 && typeof window._ednSavedFieldIndex === 'number') {
                        curFieldIdx = window._ednSavedFieldIndex;
                    }
                    if (curFieldIdx < 0 && typeof window._ednLastActiveField === 'number') {
                        curFieldIdx = window._ednLastActiveField;
                    }
                    return JSON.stringify({ hasDash: hasDash, textBefore: trimmedBefore, fieldIndex: curFieldIdx });
                } catch(e) { return JSON.stringify({ hasDash: false, textBefore: "", fieldIndex: -1 }); }
            })();
            """
            
            def _do_insert(res_json):
                import json
                _profiler_log("GUI:DO_INSERT", f"_do_insert received res_json: {res_json!r}", caller="LinkedCardsDialog:_do_insert")
                data = {}
                try:
                    if isinstance(res_json, str):
                        data = json.loads(res_json)
                    elif isinstance(res_json, dict):
                        data = res_json
                    elif isinstance(res_json, bool):
                        data = {"hasDash": res_json, "textBefore": ""}
                except Exception as e:
                    _profiler_log("GUI:DO_INSERT", f"JSON parsing error: {e}", caller="LinkedCardsDialog:_do_insert")
                    data = {"hasDash": False, "textBefore": ""}

                has_dash = bool(data.get("hasDash", False))
                text_before = str(data.get("textBefore", ""))

                # Candidats pour la détection du mode (basé sur le premier élément ou le contexte)
                cands = []
                first_nid, first_recto = items_raw[0]
                first_smart = first_recto
                try:
                    t_note0 = mw.col.get_note(int(first_nid))
                    s_note = _get_editor_note(self.editor)
                    computed = generate_smart_link_label(s_note, t_note0)
                    if computed:
                        first_smart = computed
                    cands = [first_recto, first_smart]
                    if t_note0:
                        raw_r = get_note_recto(t_note0, 100)
                        if raw_r and raw_r not in cands:
                            cands.append(raw_r)
                        if t_note0.fields:
                            f0 = re.sub(r'<[^>]+>', '', t_note0.fields[0]).strip()
                            if f0 and f0 not in cands:
                                cands.append(f0)
                except Exception:
                    cands = [first_recto]

                mode = _detect_link_insertion_mode(text_before, has_dash, cands)

                items = []
                for idx, (nid, recto) in enumerate(items_raw):
                    smart_desc = recto
                    t_note = None
                    try:
                        t_note = mw.col.get_note(int(nid))
                        s_note = _get_editor_note(self.editor)
                        computed = generate_smart_link_label(s_note, t_note)
                        if computed:
                            smart_desc = computed
                    except Exception:
                        pass

                    if mode == "bare":
                        # Le tiret est déjà sur la ligne (ex: "Atonie ----" ou "Atonie —")
                        # TOUTES les cartes insérées doivent être de simples badges NID
                        items.append((nid, None))
                    elif mode == "dash_only":
                        # Le nom est saisi sans tiret (ex: "Atonie")
                        # Le premier insère le tiret, les suivants sont des badges NID
                        if idx == 0:
                            items.append((nid, ""))
                        else:
                            items.append((nid, None))
                    else:
                        # Mode standard / full : chaque carte a son titre sur sa propre ligne
                        items.append((nid, smart_desc))

                detected_fidx = data.get("fieldIndex", -1)
                final_fidx = detected_fidx if (detected_fidx is not None and detected_fidx >= 0) else self.target_fidx
                _profiler_log("GUI:DO_INSERT", f"DECISION: detected_fidx={detected_fidx}, dialog.target_fidx={self.target_fidx} -> final_fidx={final_fidx}, mode={mode}, items={items}", caller="LinkedCardsDialog:_do_insert")
                
                from aqt.qt import QTimer
                def _exec_insert():
                    try:
                        if hasattr(self.editor, "web") and self.editor.web:
                            try:
                                self.editor.web.eval("window._ednBlockUpdateNotes = true;")
                            except Exception:
                                pass
                        _profiler_log("GUI:EXEC_INSERT", f"calling inserter.insert_link: items={items}, target_fidx={final_fidx}", caller="LinkedCardsDialog:_exec_insert")
                        self.inserter.insert_link(items, mirror_requested=mirror_req, target_fidx=final_fidx)
                    finally:
                        self.close()
                QTimer.singleShot(0, _exec_insert)
            
            try:
                _profiler_log("GUI:INSERT_SELECTED", "evaluating _dash_detection_js in editor webview", caller="LinkedCardsDialog:insert_selected")
                self.editor.web.evalWithCallback(
                    _dash_detection_js,
                    _do_insert
                )
            except Exception as e:
                _profiler_log("GUI:INSERT_SELECTED", f"evalWithCallback failed: {e} -> fallback", caller="LinkedCardsDialog:insert_selected")
                # Fallback si l'editeur n'est plus disponible
                from aqt.qt import QTimer
                def _exec_fallback():
                    try:
                        if hasattr(self.editor, "web") and self.editor.web:
                            try:
                                self.editor.web.eval("window._ednBlockUpdateNotes = true;")
                            except Exception:
                                pass
                        _profiler_log("GUI:EXEC_FALLBACK", f"calling inserter.insert_link with fallback target_fidx={self.target_fidx}", caller="LinkedCardsDialog:_exec_fallback")
                        self.inserter.insert_link(items_raw, mirror_requested=mirror_req, target_fidx=self.target_fidx)
                    finally:
                        self.close()
                QTimer.singleShot(0, _exec_fallback)

def open_search_dialog(editor, selected_text=""):
    global _active_dialog
    if not editor: return

    # Sauvegarder : range courant + index du champ actif
    editor.web.eval("""
        (function() {
            function _getAllRoots(root) {
                root = root || document.body;
                var roots = [];
                if (!root) return roots;
                if (root.shadowRoot) roots.push(root.shadowRoot);
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < all.length; i++) {
                    if (all[i].shadowRoot) {
                        roots.push(all[i].shadowRoot);
                        roots.push(..._getAllRoots(all[i].shadowRoot));
                    }
                }
                return roots;
            }
            function _queryAll(sel) {
                if (typeof window._ednQueryAllRoots === 'function') return window._ednQueryAllRoots(sel);
                var res = Array.from(document.querySelectorAll(sel));
                var sRoots = _getAllRoots(document.body);
                for (var i = 0; i < sRoots.length; i++) {
                    var list = sRoots[i].querySelectorAll(sel);
                    for (var j = 0; j < list.length; j++) {
                        if (!res.includes(list[j])) res.push(list[j]);
                    }
                }
                return res;
            }

            // Nettoyer les anciens markers
            var oldMarkers = _queryAll('[id="edn-cursor-marker"]');
            oldMarkers.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });

            // Si _ednSavedRange est déjà défini (par handle_editor_button), le conserver
            let hadSavedRange = !!(window._ednSavedRange);

            if (!hadSavedRange) {
                // Priorité 1 : Sélection active dans l'éditeur (curseur en direct)
                let sel = (typeof window._ednGetEditorSelection === 'function')
                    ? window._ednGetEditorSelection()
                    : null;
                if (!sel) {
                    let active = document.activeElement;
                    if (active && active.shadowRoot && active.shadowRoot.getSelection) {
                        sel = active.shadowRoot.getSelection();
                    }
                    if (!sel) sel = window.getSelection();
                }
                if (sel && sel.rangeCount > 0) {
                    var r0 = sel.getRangeAt(0);
                    if (r0 && r0.startContainer && r0.startContainer.isConnected) {
                        if (typeof window._ednFindEditableAncestor === 'function' ? window._ednFindEditableAncestor(r0.startContainer) : true) {
                            window._ednSavedRange = r0.cloneRange();
                        }
                    }
                }

                // Priorité 2 : Fallback sur les ranges mémorisés
                if (!window._ednSavedRange) {
                    var candRanges = [window._ednLastValidRange, window._ednPersistentRange];
                    for (var i = 0; i < candRanges.length; i++) {
                        var cr = candRanges[i];
                        if (cr && cr.startContainer && cr.startContainer.isConnected) {
                            if (typeof window._ednFindEditableAncestor === 'function' ? window._ednFindEditableAncestor(cr.startContainer) : true) {
                                window._ednSavedRange = cr.cloneRange();
                                break;
                            }
                        }
                    }
                }
            }

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
            var curFieldIdx = (typeof window._ednResolveActiveFieldIndex === 'function')
                ? window._ednResolveActiveFieldIndex()
                : -1;
            if (curFieldIdx >= 0) {
                window._ednSavedFieldIndex = curFieldIdx;
                window._ednLastActiveField = curFieldIdx;
            } else if (typeof window._ednLastActiveField === 'number' && window._ednLastActiveField >= 0) {
                window._ednSavedFieldIndex = window._ednLastActiveField;
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
    if not editor:
        active_win = mw.app.activeWindow() if (hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow')) else None
        if active_win and hasattr(active_win, 'editor') and active_win.editor:
            editor = active_win.editor
    if not editor:
        from aqt import dialogs
        b_entry = dialogs._dialogs.get("Browser")
        if isinstance(b_entry, (list, tuple)) and len(b_entry) > 1 and b_entry[1]:
            b = b_entry[1]
            if hasattr(b, 'editor') and b.editor:
                editor = b.editor
    if editor:
        handle_editor_button(editor)
    else:
        from aqt.utils import tooltip
        tooltip("Aucun éditeur actif. Ouvrez d'abord une carte en édition.")

_last_copy_nid_timestamp = 0.0

def copy_nid_from_active_browser():
    global _last_copy_nid_timestamp
    import time
    now = time.time()
    if now - _last_copy_nid_timestamp < 0.35:
        return
    _last_copy_nid_timestamp = now

    active_win = mw.app.activeWindow() if (hasattr(mw, 'app') and hasattr(mw.app, 'activeWindow')) else None
    
    from aqt.browser import Browser
    if isinstance(active_win, Browser):
        _copy_nid_smart(active_win)
        return
        
    if active_win and hasattr(active_win, 'editor') and active_win.editor:
        note = _get_editor_note(active_win.editor)
        if note and getattr(note, 'id', None):
            copy_nid_from_editor(active_win.editor)
            return
            
    if getattr(mw, 'state', None) == 'review' and getattr(mw, 'reviewer', None) and mw.reviewer.card:
        card = mw.reviewer.card
        nid = str(card.nid)
        recto = ""
        try:
            note = card.note()
            recto = get_note_recto(note, 240)
        except:
            recto = "[Vide]"
        html = make_card_link(nid, recto)
        mime = QMimeData()
        mime.setText(f"{recto} — {nid}")
        mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)
        try:
            import sys
            interceptor_mod = sys.modules.get("anki_import_diff.src.temp_link_interceptor")
            if interceptor_mod:
                interceptor = interceptor_mod.TempLinkInterceptor.instance()
                if interceptor.is_active():
                    interceptor.register_copied_nid(nid)
        except Exception:
            pass
        from aqt.utils import tooltip
        tooltip(f"NID copié: {recto} — {nid}")
        return

    from .editor_assistant import get_active_editor
    ed = get_active_editor()
    if ed and _get_editor_note(ed):
        copy_nid_from_editor(ed)
        return

    from aqt import dialogs
    b_entry = dialogs._dialogs.get("Browser")
    browser = None
    if isinstance(b_entry, (list, tuple)) and len(b_entry) > 1:
        browser = b_entry[1]
    elif isinstance(b_entry, Browser):
        browser = b_entry
        
    if browser and hasattr(browser, 'selected_cards'):
        _copy_nid_smart(browser)
    else:
        from aqt.utils import tooltip
        tooltip("Aucun éditeur ou carte sélectionnée pour copier le NID.")

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
    try:
        import aqt.editor as editor_mod
    except ImportError:
        return

    editor_classes = [getattr(editor_mod, "Editor", None), getattr(editor_mod, "NewEditor", None)]

    for cls in editor_classes:
        if not cls:
            continue
        orig_fn = getattr(cls, "call_after_note_saved", None) or getattr(cls, "saveNow", None)
        if not orig_fn:
            continue

        def _make_patched(orig_method):
            def patched_call_after_note_saved(self, callback, *args, **kwargs):
                web = getattr(self, "web", None)
                if not web:
                    if callback:
                        try:
                            from aqt import mw
                            mw.progress.single_shot(10, callback)
                        except Exception:
                            try:
                                callback()
                            except Exception:
                                pass
                    return

                try:
                    from aqt.qt import sip
                    if sip.isdeleted(web):
                        if callback:
                            try:
                                from aqt import mw
                                mw.progress.single_shot(10, callback)
                            except Exception:
                                callback()
                        return
                except Exception:
                    pass

                def on_check(defined):
                    if defined:
                        try:
                            orig_method(self, callback, *args, **kwargs)
                        except Exception:
                            if callback:
                                try:
                                    callback()
                                except Exception:
                                    pass
                    else:
                        if callback:
                            try:
                                from aqt import mw
                                mw.progress.single_shot(10, callback)
                            except Exception:
                                callback()

                try:
                    web.evalWithCallback("typeof saveNow !== 'undefined'", on_check)
                except Exception:
                    if callback:
                        try:
                            from aqt import mw
                            mw.progress.single_shot(10, callback)
                        except Exception:
                            try:
                                callback()
                            except Exception:
                                pass

            return patched_call_after_note_saved

        patched = _make_patched(orig_fn)
        cls.call_after_note_saved = patched
        cls.saveNow = patched

        orig_keep = getattr(cls, "loadNoteKeepingFocus", None)
        if orig_keep and not hasattr(cls, "_edn_orig_loadNoteKeepingFocus"):
            cls._edn_orig_loadNoteKeepingFocus = orig_keep
            def _make_patched_keep(orig_k):
                def patched_loadNoteKeepingFocus(self, *args, **kwargs):
                    cur_fld = getattr(self, "currentField", None)
                    if cur_fld is None:
                        last_fld = getattr(self, "last_field_index", None)
                        if last_fld is not None:
                            if hasattr(self, "load_note"):
                                return self.load_note(focus_to=last_fld)
                            elif hasattr(self, "loadNote"):
                                return self.loadNote(focusTo=last_fld)
                    return orig_k(self, *args, **kwargs)
                return patched_loadNoteKeepingFocus
            cls.loadNoteKeepingFocus = _make_patched_keep(orig_keep)

        orig_load = getattr(cls, "loadNote", None)
        if orig_load and not hasattr(cls, "_edn_orig_loadNote"):
            cls._edn_orig_loadNote = orig_load
            def _make_patched_load(orig_l):
                def patched_loadNote(self, focusTo=None, *args, **kwargs):
                    note_obj = getattr(self, "note", None)
                    new_key = (getattr(note_obj, "id", 0) or id(note_obj)) if note_obj is not None else None
                    prev_key = getattr(self, "_edn_last_loaded_note_key", None)
                    is_new_note_instance = (new_key != prev_key)
                    if is_new_note_instance or (focusTo == 0 and getattr(self, "addMode", False) and not getattr(note_obj, "id", 0)):
                        self._edn_loading_new_note = True
                        if focusTo is not None:
                            self.currentField = focusTo
                            self.last_field_index = focusTo
                        from aqt.qt import QTimer
                        QTimer.singleShot(750, lambda: setattr(self, "_edn_loading_new_note", False))
                    else:
                        self._edn_loading_new_note = False
                        if focusTo is None:
                            cur_fld = getattr(self, "currentField", None)
                            if cur_fld is not None:
                                focusTo = cur_fld
                            else:
                                last_fld = getattr(self, "last_field_index", None)
                                p_win = getattr(self, "parentWindow", None)
                                if last_fld is not None and p_win and hasattr(p_win, "isActiveWindow") and p_win.isActiveWindow():
                                    focusTo = last_fld
                    self._edn_last_loaded_note_key = new_key
                    return orig_l(self, focusTo=focusTo, *args, **kwargs)
                return patched_loadNote
            cls.loadNote = _make_patched_load(orig_load)

        orig_bridge = getattr(cls, "onBridgeCmd", None)
        if orig_bridge and not hasattr(cls, "_edn_orig_onBridgeCmd"):
            cls._edn_orig_onBridgeCmd = orig_bridge
            def _make_patched_bridge(orig_b):
                def patched_onBridgeCmd(self, cmd, *args, **kwargs):
                    if isinstance(cmd, str):
                        if getattr(self, "_edn_loading_new_note", False):
                            if cmd.startswith("blur:") or cmd.startswith("key:"):
                                return None
                        if cmd.startswith("focus:"):
                            try:
                                fld_idx = int(cmd.split(":", 1)[1])
                                self.last_field_index = fld_idx
                                self.currentField = fld_idx
                            except Exception:
                                pass
                        elif cmd.startswith("key:") and not getattr(self, "addMode", False):
                            try:
                                parts = cmd.split(":", 3)
                                if len(parts) >= 4:
                                    ord_idx = int(parts[1])
                                    nid_val = int(parts[2]) if parts[2].isdigit() else 0
                                    raw_txt = parts[3]
                                    note = getattr(self, "note", None)
                                    if note and getattr(note, "id", 0) == nid_val and 0 <= ord_idx < len(note.fields):
                                        munged = self.mungeHTML(raw_txt) if hasattr(self, "mungeHTML") else raw_txt
                                        if munged == note.fields[ord_idx]:
                                            return None
                            except Exception:
                                pass
                    return orig_b(self, cmd, *args, **kwargs)
                return patched_onBridgeCmd
            cls.onBridgeCmd = _make_patched_bridge(orig_bridge)


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
        source_note = _get_editor_note(editor)
        return generate_smart_link_label(source_note, note) or get_note_recto(note, 100)
    except Exception:
        return "[Erreur]"

def link_card_to_active_editor(target_nid: str, webview=None) -> None:
    if not target_nid:
        return
    
    from .editor_assistant import get_active_editor
    editor = get_active_editor(webview)
    if not editor:
        from aqt.utils import tooltip
        tooltip("Aucun éditeur actif pour lier la carte.")
        return
        
    note = _get_editor_note(editor)
    if not note or not getattr(note, 'id', None):
        from aqt.utils import tooltip
        tooltip("Veuillez d'abord sauvegarder la note en cours.")
        return
        
    if str(note.id) == str(target_nid):
        from aqt.utils import tooltip
        tooltip("Impossible de lier une carte à elle-même.")
        return
        
    config = mw.addonManager.getConfig(__name__) or {}
    mirror_req = config.get("gui_mirror_cb_state", True)
    
    recto = _get_recto_for_nid(target_nid)
    
    # Variantes du titre pour la détection
    candidate_names = [recto]
    try:
        t_note = mw.col.get_note(int(target_nid))
        raw_recto = get_note_recto(t_note, 100)
        if raw_recto and raw_recto not in candidate_names:
            candidate_names.append(raw_recto)
        if t_note.fields:
            f0 = re.sub(r'<[^>]+>', '', t_note.fields[0]).strip()
            if f0 and f0 not in candidate_names:
                candidate_names.append(f0)
    except Exception:
        pass
    
    inserter = LinkInserter(editor)
    
    # Détecter le tiret, insérer le marker et analyser la ligne avant le curseur
    _dash_detection_js = r"""
    (function() {
        try {
            function _getAllRoots(root) {
                root = root || document.body;
                var roots = [];
                if (!root) return roots;
                if (root.shadowRoot) roots.push(root.shadowRoot);
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < all.length; i++) {
                    if (all[i].shadowRoot) {
                        roots.push(all[i].shadowRoot);
                        roots.push(..._getAllRoots(all[i].shadowRoot));
                    }
                }
                return roots;
            }

            function _findEditableAncestor(node) {
                var cur = node;
                while (cur) {
                    if (cur.nodeType === 1) {
                        if (cur.tagName === 'ANKI-EDITABLE' || cur.isContentEditable || cur.contentEditable === 'true' ||
                            (cur.classList && (cur.classList.contains('editing-area') || cur.classList.contains('rich-text-input')))) {
                            return cur;
                        }
                    }
                    cur = cur.parentNode || (cur.getRootNode && cur.getRootNode().host) || cur.host;
                }
                return null;
            }

            // 1. Nettoyer les anciens markers
            var oldMarkers = (typeof window._ednQueryAllRoots === 'function')
                ? window._ednQueryAllRoots('[id="edn-cursor-marker"]')
                : Array.from(document.querySelectorAll('[id="edn-cursor-marker"]'));
            oldMarkers.forEach(function(m) { try { m.parentNode.removeChild(m); } catch(e) {} });

            // 2. Trouver un range valide à l'intérieur d'un champ éditable
            var targetRange = null;
            var candRanges = [window._ednLastValidRange, window._ednPersistentRange, window._ednSavedRange];
            for (var i = 0; i < candRanges.length; i++) {
                var cr = candRanges[i];
                if (cr && cr.startContainer && cr.startContainer.isConnected) {
                    if (_findEditableAncestor(cr.startContainer)) {
                        targetRange = cr;
                        break;
                    }
                }
            }

            if (!targetRange) {
                if (typeof window._ednGetEditorSelection === 'function') {
                    var s = window._ednGetEditorSelection();
                    if (s && s.rangeCount > 0 && s.getRangeAt(0).startContainer.isConnected && _findEditableAncestor(s.getRangeAt(0).startContainer)) {
                        targetRange = s.getRangeAt(0);
                    }
                }
            }
            if (!targetRange) {
                var s2 = window.getSelection();
                if (s2 && s2.rangeCount > 0 && s2.getRangeAt(0).startContainer.isConnected && _findEditableAncestor(s2.getRangeAt(0).startContainer)) {
                    targetRange = s2.getRangeAt(0);
                }
            }

            var editableParent = null;
            var marker = null;

            if (targetRange) {
                editableParent = _findEditableAncestor(targetRange.startContainer);
                try {
                    var r = targetRange.cloneRange();
                    r.collapse(false);
                    marker = document.createElement("span");
                    marker.id = "edn-cursor-marker";
                    r.insertNode(marker);
                } catch(e) {
                    marker = null;
                }
            }

            // Fallback : Si aucun marker n'a pu être inséré dans un range, utiliser le champ éditable actif
            if (!marker) {
                var fIdx = (typeof window._ednSavedFieldIndex === 'number' && window._ednSavedFieldIndex >= 0)
                    ? window._ednSavedFieldIndex
                    : ((typeof window._ednLastActiveField === 'number' && window._ednLastActiveField >= 0) ? window._ednLastActiveField : 0);
                
                var editables = [];
                if (typeof window._ednQueryAllRoots === 'function') {
                    editables = window._ednQueryAllRoots('anki-editable, [contenteditable="true"]');
                } else {
                    editables = Array.from(document.querySelectorAll('anki-editable, [contenteditable="true"]'));
                }
                if (editables.length > 0) {
                    editableParent = editables[Math.min(fIdx, editables.length - 1)];
                    try {
                        marker = document.createElement("span");
                        marker.id = "edn-cursor-marker";
                        editableParent.appendChild(marker);
                    } catch(e) {
                        marker = null;
                    }
                }
            }

            if (!marker) {
                return JSON.stringify({ markerInserted: false, hasDash: false, textBefore: "" });
            }

            // Mettre à jour l'index du champ actif
            var fieldIndex = -1;
            if (editableParent && typeof window._ednResolveActiveFieldIndex === 'function') {
                fieldIndex = window._ednResolveActiveFieldIndex({ target: editableParent });
                if (fieldIndex >= 0) {
                    window._ednSavedFieldIndex = fieldIndex;
                    window._ednLastActiveField = fieldIndex;
                }
            }

            // 3. Trouver le conteneur de bloc du marker
            var blockParent = marker.parentNode;
            while (blockParent && blockParent.tagName !== 'ANKI-EDITABLE' && !blockParent.isContentEditable) {
                if (['LI', 'P', 'DIV'].indexOf(blockParent.tagName) >= 0) break;
                blockParent = blockParent.parentNode;
            }
            if (!blockParent) blockParent = marker.parentNode;

            // 4. Collecter le texte de la ligne avant le marker
            function _textBeforeMarkerInLine(cont, mkr) {
                var parts = [];
                var nodes = Array.from(cont.childNodes);
                for (var i = 0; i < nodes.length; i++) {
                    var n = nodes[i];
                    if (n === mkr) break;
                    if (n.contains && n.contains(mkr)) {
                        parts.push(_textBeforeMarkerInNode(n, mkr));
                        break;
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
                        text += _textBeforeMarkerInNode(chn[i], mkr);
                        break;
                    }
                    text += chn[i].textContent || '';
                }
                return text;
            }

            var textBefore = _textBeforeMarkerInLine(blockParent, marker).trim();
            var hasDash = /[-—–\u2014\u2013]\s*$/.test(textBefore);

            return JSON.stringify({
                markerInserted: true,
                hasDash: hasDash,
                textBefore: textBefore,
                fieldIndex: fieldIndex
            });
        } catch(err) {
            return JSON.stringify({ markerInserted: false, hasDash: false, textBefore: "", error: String(err) });
        }
    })();
    """
    
    def _do_insert(res_json):
        import json
        data = {}
        try:
            if isinstance(res_json, str):
                data = json.loads(res_json)
            elif isinstance(res_json, dict):
                data = res_json
            elif isinstance(res_json, bool):
                data = {"hasDash": res_json, "textBefore": "", "markerInserted": True}
        except Exception:
            data = {"hasDash": False, "textBefore": "", "markerInserted": False}

        has_dash = bool(data.get("hasDash", False))
        text_before = str(data.get("textBefore", ""))

        mode = _detect_link_insertion_mode(text_before, has_dash, candidate_names)
        if mode == "bare":
            recto_to_insert = None
        elif mode == "dash_only":
            recto_to_insert = ""
        else:
            recto_to_insert = recto

        fidx = data.get("fieldIndex", -1)
        try:
            inserter.insert_link([(target_nid, recto_to_insert)], mirror_requested=mirror_req, target_fidx=fidx)
            try:
                from .editor_assistant import _hide_card_preview_popup
                _hide_card_preview_popup()
            except Exception:
                pass
        except Exception as e:
            from aqt.utils import tooltip
            tooltip(f"Erreur lors de l'insertion : {e}")

    try:
        editor.web.evalWithCallback(_dash_detection_js, _do_insert)
    except Exception:
        from aqt.qt import QTimer
        def _fallback_insert():
            inserter.insert_link([(target_nid, recto)], mirror_requested=mirror_req)
            try:
                from .editor_assistant import _hide_card_preview_popup
                _hide_card_preview_popup()
            except Exception:
                pass
        QTimer.singleShot(0, _fallback_insert)

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
                editor_save_now(editor, lambda: None, keep_focus=True)
        except Exception:
            pass

def open_nid_in_browser(nid: str) -> None:
    open_card_in_browser(nid)

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
            if get_active_editor(webview):
                menu.addSeparator()
                action_link = menu.addAction("Lier cette carte")
                action_link.triggered.connect(lambda checked=False, n=nid, w=webview: link_card_to_active_editor(n, webview=w))
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
    if _is_active_dialog_alive() and webview == getattr(_active_dialog, '_preview_web', None):
        is_preview_popup = True
        _active_dialog._context_menu_active = True
        menu.aboutToHide.connect(lambda: setattr(_active_dialog, '_context_menu_active', False))
    else:
        try:
            from .editor_assistant import _preview_popup as _ast_popup, _is_preview_popup_alive
            if _is_preview_popup_alive() and hasattr(_ast_popup, '_edn_web') and webview == _ast_popup._edn_web:
                is_preview_popup = True
                _ast_popup._context_menu_active = True
                menu.aboutToHide.connect(lambda: setattr(_ast_popup, '_context_menu_active', False))
        except Exception:
            pass

    # 1. If we have a clicked badge/hover nid
    if nid:
        pass
    # 2. Check if inside search dialog's preview popup
    elif is_preview_popup and _is_active_dialog_alive() and webview == getattr(_active_dialog, '_preview_web', None):
        nid = getattr(_active_dialog, '_preview_current_nid', None)
    # 3. Check if inside sidebar/mirror assistant preview popup
    elif is_preview_popup:
        try:
            from .editor_assistant import get_current_preview_nid
            nid = get_current_preview_nid()
        except Exception:
            try:
                from . import editor_assistant
                nid = getattr(editor_assistant, '_current_preview_nid', None)
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
            if get_active_editor(webview):
                action_link = menu.addAction("Lier cette carte")
                action_link.triggered.connect(lambda checked=False, n=nid, w=webview: link_card_to_active_editor(n, webview=w))
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
