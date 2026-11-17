# -*- coding: utf-8 -*-
"""
Anki EDN - Ajout d'étiquettes à la volée (Quick Tagger)
Module intégré à Anki_EDN_Lier_les_cartes pour la gestion rapide des étiquettes
avec moteur de suggestions prédictives basé sur les tags réels de l'utilisateur.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import List, Set, Tuple, Optional, Sequence, Callable, Any

from aqt import mw, gui_hooks
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QWidget, QCompleter, Qt, QApplication, QPalette,
    QLayout, QRect, QPoint, QSize, QSizePolicy
)
from aqt.reviewer import Reviewer
from aqt.utils import tooltip, showWarning
from anki.hooks import wrap

try:
    from .logger import log
except ImportError:
    def log(msg: str) -> None:
        pass

try:
    from .edn_menu import get_config, get_shortcut
except ImportError:
    def get_config() -> dict:
        return {}
    def get_shortcut(key: str, default: str) -> str:
        return default

try:
    from .editor_assistant import (
        _normalize_for_keywords,
        _extract_keywords,
        _extract_item_numbers_from_tags,
        _extract_subspecialties_from_tags,
    )
except ImportError:
    # Fallback si editor_assistant n'est pas encore importé
    import unicodedata as _ud
    import html as _html_mod

    def _normalize_for_keywords(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'\{\{c\d+::(.*?)(?:::[^}]+)?\}\}', r'\1', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = _html_mod.unescape(text)
        text = text.lower().replace('œ', 'oe').replace('æ', 'ae')
        nfkd = _ud.normalize('NFKD', text)
        text = ''.join(c for c in nfkd if not _ud.combining(c))
        return re.sub(r'\s+', ' ', text).strip()

    def _extract_keywords(text: str) -> set:
        if not text:
            return set()
        norm = _normalize_for_keywords(text)
        tokens = re.findall(r'[a-z]{3,}', norm)
        stop = {'dans', 'avec', 'pour', 'les', 'des', 'une', 'qui', 'que', 'par', 'sur', 'son', 'ses', 'est'}
        return {t for t in tokens if len(t) >= 4 and t not in stop}

    def _extract_item_numbers_from_tags(tags_iterable) -> set:
        items = set()
        for t in tags_iterable:
            for m in re.finditer(r'item(?:-|::)(\d+)', t, re.IGNORECASE):
                try:
                    items.add(int(m.group(1)))
                except ValueError:
                    pass
        return items

    def _extract_subspecialties_from_tags(tags_iterable) -> set:
        subspecs = set()
        for t in tags_iterable:
            tl = t.lower()
            if '::' in t and not any(tl.startswith(p) for p in ('ann', 'edn::', 'source::', 'maj::', 'rang::', 'new::')):
                subspecs.add(t)
        return subspecs

DEFAULT_TAG_SHORTCUT = "T"
INVALID_TAG_CHARS = set('<>"\'&\t\n\r')


def is_dark_mode() -> bool:
    """Détecte si Anki est actuellement en mode sombre."""
    try:
        from aqt.theme import theme_manager
        if theme_manager and hasattr(theme_manager, "night_mode"):
            return bool(theme_manager.night_mode)
    except Exception:
        pass
    try:
        if mw and hasattr(mw, "theme_manager") and hasattr(mw.theme_manager, "night_mode"):
            return bool(mw.theme_manager.night_mode)
    except Exception:
        pass
    try:
        app = QApplication.instance()
        if app:
            bg_col = app.palette().color(QPalette.ColorRole.Window)
            return bg_col.lightness() < 128
    except Exception:
        pass
    return False


def get_theme_palette() -> dict:
    """Palette sobre et moderne adaptée au thème actif."""
    dark = is_dark_mode()
    if dark:
        return {
            "bg_dialog": "#1e2227",
            "bg_card": "#21252b",
            "bg_input": "#282c34",
            "border": "#3b4048",
            "border_accent": "#61afef",
            "text_main": "#abb2bf",
            "text_muted": "#7f848e",
            "accent": "#61afef",
            "accent_hover": "#528bcc",
            "pill_bg": "#2c313a",
            "pill_border": "#4b5263",
            "pill_hover": "#e06c75",
            "sug_bg": "#1e3a5f",
            "sug_border": "#2b5278",
            "sug_text": "#90caf9",
            "sug_hover": "#284b77",
            "btn_default_bg": "#2c313a",
            "btn_default_hover": "#3b4048",
            "btn_primary_bg": "#2b5278",
            "btn_primary_hover": "#346392",
            "tooltip_bg": "#0f172a",
            "tooltip_text": "#f8fafc",
            "tooltip_border": "#334155",
        }
    return {
        "bg_dialog": "#ffffff",
        "bg_card": "#f8f9fa",
        "bg_input": "#ffffff",
        "border": "#dee2e6",
        "border_accent": "#1971c2",
        "text_main": "#212529",
        "text_muted": "#6c757d",
        "accent": "#1971c2",
        "accent_hover": "#1864ab",
        "pill_bg": "#f1f3f5",
        "pill_border": "#ced4da",
        "pill_hover": "#e03131",
        "sug_bg": "#e7f5ff",
        "sug_border": "#a5d8ff",
        "sug_text": "#1971c2",
        "sug_hover": "#d0ebff",
        "btn_default_bg": "#f1f3f5",
        "btn_default_hover": "#e9ecef",
        "btn_primary_bg": "#1971c2",
        "btn_primary_hover": "#1864ab",
        "tooltip_bg": "#feffc4",
        "tooltip_text": "#000000",
        "tooltip_border": "#cbd5e1",
    }


def clean_tag_name(tag: str) -> str:
    """Nettoie le nom de tag en supprimant les caractères interdits et espaces superflus."""
    tag = tag.strip().replace(" ", "_")
    for ch in INVALID_TAG_CHARS:
        tag = tag.replace(ch, "")
    return tag


class FlowLayout(QLayout):
    """Layout flexible permettant l'alignement et le retour à la ligne automatique des badges."""
    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        if margin is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.item_list = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.item_list.append(item)

    def count(self):
        return len(self.item_list)

    def itemAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()
        for item in self.item_list:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            if x + w > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = x + w + spacing
            line_height = max(line_height, h)
        return y + line_height - rect.y()


def compute_tag_suggestions(col, note, max_results: int = 8) -> List[Tuple[str, str]]:
    """
    Moteur de suggestion de tags prédictif EDN :
    - Garanti STRICTEMENT limité aux tags présents dans la collection de l'utilisateur (col.tags.all()).
    - Exclut les tags déjà portés par la note.
    - Co-occurrence SQLite basée sur le ou les numéros d'item EDN.
    - Co-occurrence basée sur les sous-spécialités cliniques.
    - Overlap lexical basé sur les mots-clés du Titre / Recto et du Contenu / Verso.
    Retourne une liste de tuples (nom_tag, explication_source).
    """
    if not col or not note:
        return []

    try:
        all_tags = set(col.tags.all())
    except Exception:
        return []

    current_tags = set(getattr(note, "tags", []))
    candidate_tags = all_tags - current_tags
    if not candidate_tags:
        return []

    source_item_nums = _extract_item_numbers_from_tags(current_tags)
    source_subspecs = _extract_subspecialties_from_tags(current_tags)

    flds = list(note.fields) if hasattr(note, "fields") and note.fields else []
    title_text = flds[0] if len(flds) > 0 else ""
    content_text = " ".join(flds[1:3]) if len(flds) > 1 else ""

    # Détection rétroactive du numéro d'item dans le titre si la note est vierge de tag item
    if not source_item_nums and title_text:
        for m in re.finditer(r'item(?:-|::|\s+)(\d+)', title_text, re.IGNORECASE):
            try:
                source_item_nums.add(int(m.group(1)))
            except ValueError:
                pass

    title_keywords = _extract_keywords(title_text)
    content_keywords = _extract_keywords(content_text)

    tag_scores = defaultdict(float)
    tag_reasons = {}

    # 1. Co-occurrence par Item EDN (+4.0 par présence dans une carte du même item)
    for item_num in source_item_nums:
        item_patterns = [
            f'%item-{item_num}%',
            f'%item::{item_num}%'
        ]
        if item_num < 100:
            item_patterns.extend([f'%item-0{item_num}%', f'%item::0{item_num}%'])
        where_clauses = " OR ".join(["tags LIKE ?"] * len(item_patterns))
        try:
            nid = getattr(note, "id", 0) or 0
            rows = col.db.all(
                f"SELECT tags FROM notes WHERE ({where_clauses}) AND id != ? LIMIT 120",
                *item_patterns, int(nid)
            )
            for r in rows:
                if not r or not r[0]:
                    continue
                for t in r[0].strip().split():
                    if t in candidate_tags:
                        tag_scores[t] += 4.0
                        if t not in tag_reasons:
                            tag_reasons[t] = f"Item {item_num}"
        except Exception:
            pass

    # 2. Co-occurrence par Sous-spécialité (+2.0)
    for sub in list(source_subspecs)[:2]:
        sub_root = sub.split('::')[-1]
        if len(sub_root) >= 4:
            try:
                nid = getattr(note, "id", 0) or 0
                rows = col.db.all(
                    "SELECT tags FROM notes WHERE tags LIKE ? AND id != ? LIMIT 50",
                    f'%{sub_root}%', int(nid)
                )
                for r in rows:
                    if not r or not r[0]:
                        continue
                    for t in r[0].strip().split():
                        if t in candidate_tags:
                            tag_scores[t] += 2.0
                            if t not in tag_reasons:
                                tag_reasons[t] = sub_root
            except Exception:
                pass

    # 3. Overlap lexical Titre (+15) et Contenu (+5)
    for t in candidate_tags:
        clean_t = t.replace('::', ' ').replace('-', ' ').replace('_', ' ')
        t_tokens = _extract_keywords(clean_t)
        if not t_tokens:
            continue
        match_title = t_tokens & title_keywords
        match_content = t_tokens & content_keywords
        if match_title:
            boost = len(match_title) * 15.0
            tag_scores[t] += boost
            tag_reasons[t] = f"Mot-clé: {', '.join(sorted(match_title)[:2])}"
        elif match_content:
            boost = len(match_content) * 5.0
            tag_scores[t] += boost
            if t not in tag_reasons:
                tag_reasons[t] = f"Contenu: {', '.join(sorted(match_content)[:2])}"

    # 4. Tri par score décroissant
    ranked_tags = [t for t in tag_scores if tag_scores[t] > 0]
    ranked_tags.sort(key=lambda t: tag_scores[t], reverse=True)

    results = []
    for t in ranked_tags[:max_results]:
        reason = tag_reasons.get(t, "Suggestion")
        results.append((t, reason))
    return results


class QuickTagDialog(QDialog):
    """
    Dialogue modal moderne pour consulter et modifier les étiquettes en cours de révision.
    Offre l'autocomplétion sur la totalité des tags de la collection et des suggestions
    intelligentes calculées à la volée.
    """

    def __init__(self, parent=None, note=None):
        super().__init__(parent)
        self.note = note
        self.current_tags: List[str] = list(note.tags) if note else []
        self.all_collection_tags: List[str] = []
        self.smart_suggestions: List[Tuple[str, str]] = []
        self.t = get_theme_palette()

        self._load_collection_tags()
        self._compute_suggestions()
        self.setup_ui()

    def _load_collection_tags(self):
        try:
            if mw and mw.col:
                self.all_collection_tags = sorted(mw.col.tags.all(), key=str.lower)
        except Exception:
            self.all_collection_tags = []

    def _compute_suggestions(self):
        try:
            if mw and mw.col and self.note:
                # Créer une note temporaire virtuelle avec les tags actuels pour le recalcul
                self.note.tags = list(self.current_tags)
                self.smart_suggestions = compute_tag_suggestions(mw.col, self.note, max_results=8)
            else:
                self.smart_suggestions = []
        except Exception as e:
            log(f"[quick_tagger] Erreur calcul suggestions: {e}")
            self.smart_suggestions = []

    def setup_ui(self):
        T = self.t
        self.setObjectName("quickTagDialog")
        self.setWindowTitle("Ajout d'étiquette à la volée — Anki EDN")
        self.setMinimumWidth(540)
        self.setMinimumHeight(440)

        self.setStyleSheet(f"""
            QDialog#quickTagDialog {{
                background-color: {T['bg_dialog']};
                color: {T['text_main']};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
                font-size: 13px;
            }}
            QDialog#quickTagDialog QLabel {{
                color: {T['text_main']};
                background: transparent;
            }}
            QLineEdit {{
                background-color: {T['bg_input']};
                color: {T['text_main']};
                border: 1px solid {T['border']};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid {T['border_accent']};
            }}
            QPushButton {{
                background-color: {T['btn_default_bg']};
                color: {T['text_main']};
                border: 1px solid {T['border']};
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 12.5px;
            }}
            QPushButton:hover {{
                background-color: {T['btn_default_hover']};
            }}
            QScrollArea {{
                border: 1px solid {T['border']};
                border-radius: 4px;
                background-color: {T['bg_card']};
            }}
            QToolTip {{
                background-color: {T['tooltip_bg']};
                color: {T['tooltip_text']};
                border: 1px solid {T['tooltip_border']};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # Header avec Titre de la carte
        header_text = "Étiquettes de la note"
        if self.note:
            header_text += f" (ID: {self.note.id})"
        header_label = QLabel(header_text)
        header_label.setStyleSheet(f"font-weight: 600; font-size: 14px; color: {T['accent']};")
        layout.addWidget(header_label)

        # Input row
        input_label = QLabel("Nouvelle étiquette (Entrée pour ajouter) :")
        input_label.setStyleSheet(f"color: {T['text_muted']}; font-size: 11.5px;")
        layout.addWidget(input_label)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("Ex: EDN::item-020, Rang::A, Difficile...")
        self.completer = QCompleter(self.all_collection_tags, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tag_input.setCompleter(self.completer)
        self.tag_input.returnPressed.connect(self.add_tag_from_input)

        add_btn = QPushButton("Ajouter")
        add_btn.clicked.connect(self.add_tag_from_input)

        input_layout.addWidget(self.tag_input)
        input_layout.addWidget(add_btn)
        layout.addLayout(input_layout)

        # Active tags area
        tags_section_label = QLabel("Étiquettes actuellement appliquées :")
        tags_section_label.setStyleSheet(f"color: {T['text_muted']}; font-size: 11.5px; margin-top: 2px;")
        layout.addWidget(tags_section_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(110)
        self.scroll.setMaximumHeight(160)

        self.pills_container = QWidget()
        self.pills_layout = FlowLayout(self.pills_container, margin=8, spacing=6)
        self.scroll.setWidget(self.pills_container)
        layout.addWidget(self.scroll)

        # Smart Suggestions section
        self.sug_title = QLabel("Suggestions intelligentes (issues de votre collection) :")
        self.sug_title.setStyleSheet(f"color: {T['accent']}; font-weight: 600; font-size: 11.5px; margin-top: 4px;")
        layout.addWidget(self.sug_title)

        self.sug_scroll = QScrollArea()
        self.sug_scroll.setWidgetResizable(True)
        self.sug_scroll.setMinimumHeight(70)
        self.sug_scroll.setMaximumHeight(110)

        self.sug_container = QWidget()
        self.sug_layout = FlowLayout(self.sug_container, margin=8, spacing=6)
        self.sug_scroll.setWidget(self.sug_container)
        layout.addWidget(self.sug_scroll)

        layout.addStretch()

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        cancel_btn = QPushButton("Annuler")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Enregistrer")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T['btn_primary_bg']};
                color: #ffffff;
                border: 1px solid {T['btn_primary_bg']};
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {T['btn_primary_hover']};
            }}
        """)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept_and_save)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        self.refresh_pills()
        self.refresh_suggestions()
        self.tag_input.setFocus()

    def refresh_pills(self):
        """Réaffiche les badges des tags actuellement portés par la note."""
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self.current_tags:
            empty_lbl = QLabel("Aucune étiquette sur cette carte.")
            empty_lbl.setStyleSheet(f"color: {self.t['text_muted']}; font-style: italic; font-size: 12px;")
            self.pills_layout.addWidget(empty_lbl)
            return

        T = self.t
        for tag in self.current_tags:
            badge = QWidget()
            b_layout = QHBoxLayout(badge)
            b_layout.setContentsMargins(6, 2, 4, 2)
            b_layout.setSpacing(4)
            badge.setStyleSheet(f"""
                QWidget {{
                    background-color: {T['pill_bg']};
                    border: 1px solid {T['pill_border']};
                    border-radius: 4px;
                }}
            """)

            lbl = QLabel(tag)
            lbl.setStyleSheet("border: none; font-size: 12px; padding: 0;")
            b_layout.addWidget(lbl)

            del_btn = QPushButton("×")
            del_btn.setToolTip("Retirer l'étiquette")
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    border: none;
                    background: transparent;
                    color: {T['text_muted']};
                    font-size: 13px;
                    font-weight: bold;
                    padding: 0 4px;
                    margin: 0;
                }}
                QPushButton:hover {{
                    color: {T['pill_hover']};
                }}
            """)
            del_btn.clicked.connect(lambda checked, t=tag: self.remove_tag(t))
            b_layout.addWidget(del_btn)

            self.pills_layout.addWidget(badge)

    def refresh_suggestions(self):
        """Affiche les suggestions prédictives réelles sous forme de badges cliquables."""
        while self.sug_layout.count():
            item = self.sug_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        T = self.t
        if not self.smart_suggestions:
            empty_lbl = QLabel("Toutes les étiquettes suggérées sont déjà appliquées.")
            empty_lbl.setStyleSheet(f"color: {T['text_muted']}; font-style: italic; font-size: 11.5px;")
            self.sug_layout.addWidget(empty_lbl)
            return

        for tag, reason in self.smart_suggestions:
            btn = QPushButton(f"+ {tag}")
            btn.setToolTip(f"Origine : {reason}\nCliquer pour ajouter cette étiquette")
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 11.5px;
                    padding: 3px 8px;
                    background-color: {T['sug_bg']};
                    color: {T['sug_text']};
                    border: 1px solid {T['sug_border']};
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {T['sug_hover']};
                    border-color: {T['accent']};
                }}
            """)
            btn.clicked.connect(lambda checked, s=tag: self.add_tag(s))
            self.sug_layout.addWidget(btn)

    def add_tag(self, tag: str):
        clean = clean_tag_name(tag)
        if clean and clean not in self.current_tags:
            self.current_tags.append(clean)
            self._compute_suggestions()
            self.refresh_pills()
            self.refresh_suggestions()

    def add_tag_from_input(self):
        raw = self.tag_input.text()
        if not raw.strip():
            return
        parts = re.split(r'[,;\s]+', raw.strip())
        modified = False
        for p in parts:
            clean = clean_tag_name(p)
            if clean and clean not in self.current_tags:
                self.current_tags.append(clean)
                modified = True
        self.tag_input.clear()
        if modified:
            self._compute_suggestions()
            self.refresh_pills()
            self.refresh_suggestions()

    def remove_tag(self, tag: str):
        if tag in self.current_tags:
            self.current_tags.remove(tag)
            self._compute_suggestions()
            self.refresh_pills()
            self.refresh_suggestions()

    def accept_and_save(self):
        # Valider tout résidu saisi dans l'input avant d'enregistrer
        if self.tag_input.text().strip():
            self.add_tag_from_input()

        if self.note and mw and mw.col:
            try:
                self.note.tags = list(dict.fromkeys(self.current_tags))
                mw.col.update_note(self.note)
                tooltip(f"Étiquettes enregistrées : {len(self.note.tags)} étiquette(s).", period=1800)
                log(f"[quick_tagger] Tags mis à jour pour note {self.note.id}: {self.note.tags}")

                # Rafraîchir l'affichage du reviewer si la carte active correspond
                if mw.state == "review" and getattr(mw, "reviewer", None) and getattr(mw.reviewer, "card", None):
                    if mw.reviewer.card.nid == self.note.id:
                        mw.reviewer.card.load()
            except Exception as e:
                showWarning(f"Erreur lors de l'enregistrement des étiquettes : {e}")
                log(f"[quick_tagger] Erreur update note: {e}")
                return
        super().accept()


def get_quick_tag_shortcut() -> str:
    """Récupère le raccourci configuré via shared_menu ou la valeur par défaut."""
    try:
        cfg = get_config()
        shortcuts = cfg.get("shortcuts", {})
        if "quick_tagger_reviewer" in shortcuts and shortcuts["quick_tagger_reviewer"]:
            return shortcuts["quick_tagger_reviewer"]
        return get_shortcut("quick_tagger_reviewer", DEFAULT_TAG_SHORTCUT)
    except Exception:
        return DEFAULT_TAG_SHORTCUT


def open_quick_tag_dialog(note=None) -> None:
    """Ouvre le dialogue d'ajout d'étiquette pour la carte en cours ou passée en argument."""
    if note is None:
        if not mw or mw.state != "review":
            return
        reviewer = getattr(mw, "reviewer", None)
        if not reviewer or not reviewer.card:
            tooltip("Aucune carte en cours de révision.")
            return
        try:
            note = reviewer.card.note()
        except Exception as e:
            log(f"[quick_tagger] Impossible de charger la note : {e}")
            return

    dialog = QuickTagDialog(mw, note=note)
    dialog.exec()


def _reviewer_shortcut_keys(reviewer: Reviewer, _old: Callable) -> Sequence[Tuple[str, Callable]]:
    """Injecte le raccourci d'étiquetage rapide dans la liste du Reviewer."""
    keys = list(_old(reviewer))
    sc = get_quick_tag_shortcut()
    if sc:
        keys.append((sc, lambda: open_quick_tag_dialog()))
    return keys


def _on_state_shortcuts_will_change(state: str, shortcuts: list) -> None:
    """Garantit l'activation du raccourci dans l'état 'review' pour toutes les versions d'Anki."""
    if state == "review":
        sc = get_quick_tag_shortcut()
        if sc:
            shortcuts.append((sc, lambda: open_quick_tag_dialog()))


_QUICK_TAGGER_INITIALIZED = False

def init_quick_tagger() -> None:
    """Initialise les raccourcis et hooks du module Quick Tagger."""
    global _QUICK_TAGGER_INITIALIZED
    if _QUICK_TAGGER_INITIALIZED:
        return
    _QUICK_TAGGER_INITIALIZED = True

    try:
        Reviewer._shortcutKeys = wrap(Reviewer._shortcutKeys, _reviewer_shortcut_keys, "around")
        gui_hooks.state_shortcuts_will_change.append(_on_state_shortcuts_will_change)
        log("[quick_tagger] Module d'ajout d'étiquettes à la volée initialisé dans Lier les cartes.")
    except Exception as e:
        log(f"[quick_tagger] Erreur init_quick_tagger: {e}")
