"""
Anki EDN Card Styles — Module
Gère les préférences visuelles des cartes : rang badge, pictos de matières.
Intégré en tant que module dans Anki_EDN_Installer.
Persiste les préférences via localStorage injecté dans le reviewer et via une note sentinelle.
"""
from __future__ import annotations
import os
import json

from aqt import mw, gui_hooks
from aqt.qt import *
from aqt.utils import tooltip

# Import shared menu from the local package context
try:
    from .shared_menu import register_module, register_action
except ImportError:
    from shared_menu import register_module, register_action

ADDON_PATH = os.path.dirname(os.path.abspath(__file__))
PREFS_KEY = "edn_card_prefs"

DEFAULT_PREFS = {
    "showRankBadge": False,
    "rank_color_a": "#111827",
    "rank_color_b": "#3182CE",
    "rank_color_c": "#38A169",
    "showSpecialtyIcon": True,
    "showSpecialtyIconMobile": False,
    "showFlagBorders": True,
    "virtualBlueFlagForNewCards": False,
    "virtualBlueFlagForNewCardsMobile": False,
    "virtual_flag_color_new": "flag4",
    "virtual_flag_color_suspended": "flag1",
    "reviewDualFlags": False,
    "previewDualFlags": False,
    "flagAgainEnabled": False,
    "flagAgainCount": 7,
    "virtual_flag_color_leech": "flag2",
}

ANKI_FLAGS = [
    ("flag1", "Rouge (Drapeau 1)"),
    ("flag2", "Orange (Drapeau 2)"),
    ("flag3", "Vert (Drapeau 3)"),
    ("flag4", "Bleu (Drapeau 4)"),
    ("flag5", "Rose (Drapeau 5)"),
    ("flag6", "Turquoise (Drapeau 6)"),
    ("flag7", "Violet (Drapeau 7)"),
]

FLAG_COLORS = {
    "flag1": {"name": "Rouge", "hex": "#E53E3E", "border": "#FCA5A5"},
    "flag2": {"name": "Orange", "hex": "#DD6B20", "border": "#FDBA74"},
    "flag3": {"name": "Vert", "hex": "#38A169", "border": "#86EFAC"},
    "flag4": {"name": "Bleu", "hex": "#3182CE", "border": "#93C5FD"},
    "flag5": {"name": "Rose", "hex": "#D53F8C", "border": "#F472B6"},
    "flag6": {"name": "Turquoise", "hex": "#00A3C4", "border": "#67E8F9"},
    "flag7": {"name": "Violet", "hex": "#805AD5", "border": "#D8B4FE"},
}


# ─────────────────────────────────────────────
#  Lecture / Écriture des préférences
# ─────────────────────────────────────────────

def _prefs_file() -> str:
    """Chemin vers le fichier JSON de préférences (dans le dossier profil)."""
    if mw.pm and mw.pm.name:
        return os.path.join(mw.pm.profileFolder(), "edn_card_prefs.json")
    return os.path.join(ADDON_PATH, "edn_card_prefs.json")


def load_prefs() -> dict:
    try:
        p = _prefs_file()
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return {**DEFAULT_PREFS, **json.load(f)}
    except Exception:
        pass
    return dict(DEFAULT_PREFS)


def _generate_dynamic_css(prefs: dict) -> str:
    """Génère le CSS dynamique pour les couleurs de rang et variables CSS."""
    color_a = prefs.get("rank_color_a", DEFAULT_PREFS["rank_color_a"])
    color_b = prefs.get("rank_color_b", DEFAULT_PREFS["rank_color_b"])
    color_c = prefs.get("rank_color_c", DEFAULT_PREFS["rank_color_c"])
    show_rank = prefs.get("showRankBadge", False)
    
    if not show_rank:
        return ""
        
    return f"""
        :root {{
            --edn-rang-a: {color_a};
            --edn-rang-b: {color_b};
            --edn-rang-c: {color_c};
        }}
        .bar.edn-rang-A, .barHider.edn-rang-A, .edn-rang-bar.edn-rang-A {{
            background-color: {color_a} !important;
        }}
        .edn-specialty-icons.edn-rang-A {{
            border-color: {color_a} !important;
        }}
        .bar.edn-rang-B, .barHider.edn-rang-B, .edn-rang-bar.edn-rang-B {{
            background-color: {color_b} !important;
        }}
        .edn-specialty-icons.edn-rang-B {{
            border-color: {color_b} !important;
        }}
        .bar.edn-rang-C, .barHider.edn-rang-C, .edn-rang-bar.edn-rang-C {{
            background-color: {color_c} !important;
        }}
        .edn-specialty-icons.edn-rang-C {{
            border-color: {color_c} !important;
        }}
    """


def _write_prefs_to_media_folder(prefs: dict):
    try:
        if mw.col:
            media_dir = mw.col.media.dir()
            js_path = os.path.join(media_dir, "_edn_prefs.js")
            color_a = prefs.get("rank_color_a", DEFAULT_PREFS["rank_color_a"])
            color_b = prefs.get("rank_color_b", DEFAULT_PREFS["rank_color_b"])
            color_c = prefs.get("rank_color_c", DEFAULT_PREFS["rank_color_c"])
            show_rank = "true" if prefs.get("showRankBadge", False) else "false"

            js_content = (
                f"var edn_prefs = {json.dumps(prefs, ensure_ascii=False)};\n"
                f"(function() {{\n"
                f"    if (typeof document !== 'undefined' && {show_rank}) {{\n"
                f"        var s = document.getElementById('edn-dynamic-card-styles');\n"
                f"        if (!s) {{\n"
                f"            s = document.createElement('style');\n"
                f"            s.id = 'edn-dynamic-card-styles';\n"
                f"            document.head.appendChild(s);\n"
                f"        }}\n"
                f"        s.textContent = `:root {{ --edn-rang-a: {color_a}; --edn-rang-b: {color_b}; --edn-rang-c: {color_c}; }}` +\n"
                f"            `.bar.edn-rang-A, .barHider.edn-rang-A, .edn-rang-bar.edn-rang-A {{ background-color: {color_a} !important; }}` +\n"
                f"            `.edn-specialty-icons.edn-rang-A {{ border-color: {color_a} !important; }}` +\n"
                f"            `.bar.edn-rang-B, .barHider.edn-rang-B, .edn-rang-bar.edn-rang-B {{ background-color: {color_b} !important; }}` +\n"
                f"            `.edn-specialty-icons.edn-rang-B {{ border-color: {color_b} !important; }}` +\n"
                f"            `.bar.edn-rang-C, .barHider.edn-rang-C, .edn-rang-bar.edn-rang-C {{ background-color: {color_c} !important; }}` +\n"
                f"            `.edn-specialty-icons.edn-rang-C {{ border-color: {color_c} !important; }}`;\n"
                f"    }}\n"
                f"}})();\n"
            )
            with open(js_path, "w", encoding="utf-8") as f:
                f.write(js_content)
            print(f"[EDN Card Styles] Prefs ecrites dans _edn_prefs.js")
    except Exception as e:
        print(f"[EDN Card Styles] Erreur ecriture _edn_prefs.js: {e}")


def save_prefs(prefs: dict):
    try:
        p = _prefs_file()
        with open(p, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
        # Également écrire dans le dossier média d'Anki pour les mobiles
        _write_prefs_to_media_folder(prefs)
    except Exception as e:
        print(f"[EDN Card Styles] Erreur sauvegarde: {e}")


# ─────────────────────────────────────────────
#  Injection localStorage dans le reviewer
# ─────────────────────────────────────────────

def _push_prefs_to_webview(card=None):
    """Écrit les préférences dans localStorage via eval JS et injecte isNewCard / isDifficultCard."""
    try:
        prefs = load_prefs()
        is_new_js = "false"
        is_diff_js = "false"
        lapses_val = 0
        
        target_card = card
        if target_card is None and mw and mw.reviewer and mw.reviewer.card:
            target_card = mw.reviewer.card
            
        if target_card is not None:
            reps = getattr(target_card, 'reps', 0)
            card_type = getattr(target_card, 'type', 0)
            queue = getattr(target_card, 'queue', 0)
            is_new = (reps == 0 and (card_type == 0 or queue == 0 or queue == 1))
            is_new_js = "true" if (is_new and prefs.get("virtualBlueFlagForNewCards", False)) else "false"
            
            lapses = getattr(target_card, 'lapses', 0)
            lapses_val = lapses
            again_threshold = prefs.get("flagAgainCount", 7)
            is_diff = (prefs.get("flagAgainEnabled", False) and lapses >= again_threshold)
            is_diff_js = "true" if is_diff else "false"

        js = (
            f"window._ednIsNewCard = {is_new_js}; "
            f"window._ednIsDifficultCard = {is_diff_js}; "
            f"window._ednCardLapses = {lapses_val}; "
            f"localStorage.setItem('edn_card_prefs', JSON.stringify({json.dumps(prefs)}));"
        )
        if mw and mw.reviewer and mw.reviewer.web:
            mw.reviewer.web.eval(js)
    except Exception as e:
        print(f"[EDN Card Styles] Erreur injection: {e}")


def _on_card_will_show(text: str, card, kind: str) -> str:
    """Injecte l'état is_new, is_difficult et le CSS dynamique dans le HTML de la carte avant son affichage."""
    try:
        prefs = load_prefs()
        injections = []
        
        dynamic_css = _generate_dynamic_css(prefs)
        if dynamic_css:
            injections.append(f"<style id='edn-dynamic-card-styles'>{dynamic_css}</style>")
            
        if prefs.get("showFlagBorders", True):
            reps = getattr(card, 'reps', 0)
            card_type = getattr(card, 'type', 0)
            queue = getattr(card, 'queue', 0)
            is_new = (reps == 0 and (card_type == 0 or queue == 0 or queue == 1))
            is_new_js = "true" if (is_new and prefs.get("virtualBlueFlagForNewCards", False)) else "false"
            
            lapses = getattr(card, 'lapses', 0)
            again_threshold = prefs.get("flagAgainCount", 7)
            is_diff = (prefs.get("flagAgainEnabled", False) and lapses >= again_threshold)
            is_diff_js = "true" if is_diff else "false"
            
            sentinel_tags = []
            if is_new and prefs.get("virtualBlueFlagForNewCards", False):
                sentinel_tags.append("<div id='ednVirtualFlagData' style='display:none;'>new</div>")
            if is_diff:
                sentinel_tags.append("<div id='ednDifficultFlagData' style='display:none;'>difficult</div>")
                
            injections.append(
                f"<script>"
                f"window._ednIsNewCard = {is_new_js}; "
                f"window._ednIsDifficultCard = {is_diff_js}; "
                f"window._ednCardLapses = {lapses};"
                f"</script>"
            )
            if sentinel_tags:
                injections.append("".join(sentinel_tags))
            
        if injections:
            return "".join(injections) + text
    except Exception as e:
        print(f"[EDN Card Styles] Erreur card_will_show: {e}")
    return text


def _on_reviewer_did_show_question(card) -> None:
    """Injecte les préférences au chargement de chaque question (recto)."""
    _push_prefs_to_webview(card)


# ─────────────────────────────────────────────
#  Dialog de réglages Cartes
# ─────────────────────────────────────────────

class NoWheelComboBox(QComboBox):
    """QComboBox qui ignore la molette de souris pour éviter les changements accidentels lors du défilement."""
    def wheelEvent(self, event):
        event.ignore()


class CardStylesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        is_night = False
        try:
            from aqt.theme import theme_manager
            is_night = bool(theme_manager.night_mode)
        except Exception:
            try:
                is_night = bool(mw.theme_manager.night_mode)
            except Exception:
                pass

        muted_color = "#9ca3af" if is_night else "#64748b"
        self.setWindowTitle("Réglages Cartes EDN")
        self.setMinimumWidth(420)
        self.prefs = load_prefs()
        self._build_ui(is_night, muted_color)

    def _build_ui(self, is_night, muted_color):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("<h3>Réglages visuels des cartes</h3>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        desc = QLabel(
            "Ces réglages s'appliquent à toutes les cartes EDN. "
            "Ils sont persistants sur cet appareil (via localStorage).<br>"
            f"<small style='color:{muted_color}'>Rechargez la carte courante après modification.</small>"
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # Toggle 1 : Couleur de rang sur le premier champ
        self.cb_rank = QCheckBox("Colorer la barre d'icône du premier champ selon le rang")
        self.cb_rank.setChecked(self.prefs.get("showRankBadge", True))
        self.cb_rank.setToolTip(
            "Colore la barre verticale d'icône du premier champ en fonction du rang de la carte.\n"
            "Noir = Rang A   Bleu = Rang B   Vert = Rang C   Gris = non classé"
        )
        layout.addWidget(self.cb_rank)

        rank_desc = QLabel(
            "  <small>La barre verticale d'icône (gauche) prend la couleur du rang.<br>"
            "  Noir = Rang A (Essentiel) | Bleu = Rang B (Important) | Vert = Rang C.</small>"
        )
        rank_desc.setStyleSheet(f"color: {muted_color}; margin-left: 20px;")
        layout.addWidget(rank_desc)

        layout.addSpacing(8)

        # Toggle 2 : Pictogrammes de matière (Desktop)
        self.cb_icons = QCheckBox("Afficher les pictogrammes de matière sur cet ordinateur")
        self.cb_icons.setChecked(self.prefs.get("showSpecialtyIcon", True))
        self.cb_icons.setToolTip(
            "Affiche les icônes des matières médicales concernées par la carte,\n"
            "juste au-dessus du recto."
        )
        layout.addWidget(self.cb_icons)

        # Toggle 2b : Pictogrammes de matière (Mobile)
        self.cb_icons_mobile = QCheckBox("Afficher les pictogrammes de matière sur mobile")
        self.cb_icons_mobile.setChecked(self.prefs.get("showSpecialtyIconMobile", True))
        self.cb_icons_mobile.setToolTip(
            "Affiche les icônes des matières médicales concernées par la carte\n"
            "lorsque vous révisez sur mobile."
        )
        layout.addWidget(self.cb_icons_mobile)

        icons_desc = QLabel(
            "  <small>Ex : Cardiologie, Neurologie, Hématologie…<br>"
            "  Basé sur les tags de spécialité racine de la carte.</small>"
        )
        icons_desc.setStyleSheet(f"color: {muted_color}; margin-left: 20px;")
        layout.addWidget(icons_desc)

        layout.addSpacing(8)

        # Toggle 3 : Encadrement selon drapeaux
        self.cb_flags = QCheckBox("Encadrer les cartes selon les drapeaux")
        self.cb_flags.setChecked(self.prefs.get("showFlagBorders", True))
        self.cb_flags.setToolTip(
            "Encadre la carte (recto et verso) de la couleur du drapeau Anki actif.\n"
            "Prend en charge les 7 drapeaux Anki (Rouge, Orange, Vert, Bleu, Rose, Turquoise, Violet)."
        )
        layout.addWidget(self.cb_flags)

        flags_desc = QLabel(
            "  <small>La bordure extérieure des sections hérite de la couleur du drapeau.<br>"
            "  Désactivez ce réglage pour conserver la bordure grise par défaut.</small>"
        )
        flags_desc.setStyleSheet(f"color: {muted_color}; margin-left: 20px;")
        layout.addWidget(flags_desc)

        # Toggle 3b : Drapeau virtuel pour les cartes inconnues (dépend de cb_flags)
        self.cb_virtual_flag = QCheckBox("    Drapeau virtuel pour les cartes inconnues")
        self.cb_virtual_flag.setChecked(self.prefs.get("virtualBlueFlagForNewCards", False))
        self.cb_virtual_flag.setToolTip(
            "Si activé, les cartes new (jamais vues) et suspendues affichent une bordure de couleur dans les previews.\n"
            "Nécessite que 'Encadrer les cartes selon les drapeaux' soit activé.\n"
            "Un flag Anki réel reste prioritaire sur ce flag virtuel."
        )
        layout.addWidget(self.cb_virtual_flag)

        # Conteneur pour le choix graphique des couleurs de drapeaux virtuels
        self.flag_colors_widget = QWidget()
        flag_colors_layout = QGridLayout(self.flag_colors_widget)
        flag_colors_layout.setContentsMargins(36, 4, 10, 4)
        flag_colors_layout.setHorizontalSpacing(10)
        flag_colors_layout.setVerticalSpacing(6)

        label_new = QLabel("Couleur cartes <b>New</b> :")
        self.combo_flag_new = QComboBox()
        for flag_id, label in ANKI_FLAGS:
            self.combo_flag_new.addItem(label, flag_id)
        current_new = self.prefs.get("virtual_flag_color_new", "flag4")
        idx_new = self.combo_flag_new.findData(current_new)
        if idx_new >= 0:
            self.combo_flag_new.setCurrentIndex(idx_new)

        label_sus = QLabel("Couleur cartes <b>Suspendues</b> :")
        self.combo_flag_sus = QComboBox()
        for flag_id, label in ANKI_FLAGS:
            self.combo_flag_sus.addItem(label, flag_id)
        current_sus = self.prefs.get("virtual_flag_color_suspended", "flag1")
        idx_sus = self.combo_flag_sus.findData(current_sus)
        if idx_sus >= 0:
            self.combo_flag_sus.setCurrentIndex(idx_sus)

        flag_colors_layout.addWidget(label_new, 0, 0)
        flag_colors_layout.addWidget(self.combo_flag_new, 0, 1)
        flag_colors_layout.addWidget(label_sus, 1, 0)
        flag_colors_layout.addWidget(self.combo_flag_sus, 1, 1)

        layout.addWidget(self.flag_colors_widget)

        self.cb_dual_flags_review = QCheckBox("    Double drapeau en révision : virtuel (50% gauche) + réel (50% droit)")
        self.cb_dual_flags_review.setChecked(self.prefs.get("reviewDualFlags", False))
        self.cb_dual_flags_review.setToolTip(
            "Pour les cartes ayant un drapeau réel ET un drapeau virtuel (nouvelle carte),\n"
            "divise l'encadrement en révision : drapeau virtuel à gauche (50%) et drapeau réel à droite (50%)."
        )
        layout.addWidget(self.cb_dual_flags_review)

        self.cb_preview_dual_flags = QCheckBox("    Double drapeau dans les aperçus : virtuel (50% gauche) + réel (50% droit)")
        self.cb_preview_dual_flags.setChecked(self.prefs.get("previewDualFlags", False))
        self.cb_preview_dual_flags.setToolTip(
            "Pour les cartes qui ont un drapeau réel ET un statut virtuel (nouvelle ou suspendue),\n"
            "divise l'encadrement en deux : drapeau virtuel à gauche (50%) et drapeau réel à droite (50%)."
        )
        layout.addWidget(self.cb_preview_dual_flags)

        def _update_virtual_flags_state():
            flags_enabled = self.cb_flags.isChecked()
            self.cb_virtual_flag.setEnabled(flags_enabled)
            vf_enabled = flags_enabled and self.cb_virtual_flag.isChecked()
            self.flag_colors_widget.setEnabled(vf_enabled)
            self.cb_dual_flags_review.setEnabled(vf_enabled)
            self.cb_preview_dual_flags.setEnabled(vf_enabled)

        self.cb_flags.stateChanged.connect(lambda state: _update_virtual_flags_state())
        self.cb_virtual_flag.stateChanged.connect(lambda state: _update_virtual_flags_state())
        _update_virtual_flags_state()

        virtual_flag_desc = QLabel(
            "      <small>Couleur utilisée pour les cartes sans drapeau ou en mode double drapeau.</small>"
        )
        virtual_flag_desc.setStyleSheet(f"color: {muted_color}; margin-left: 36px;")
        layout.addWidget(virtual_flag_desc)

        layout.addSpacing(16)

        # Info note sentinelle
        sentinel_info = QLabel(
            "<small><b>Synchronisation multi-appareils :</b> les préférences sont sauvegardées "
            "localement et dans une note sentinelle (dans son champ personnel <b>Erreurs faites</b>). "
            "Elles seront chargées automatiquement sur les appareils mobiles lors de l'affichage.</small>"
        )
        sentinel_info.setWordWrap(True)
        if is_night:
            sentinel_info.setStyleSheet(
                "background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 4px; "
                "padding: 8px; color: #7dd3fc;"
            )
        else:
            sentinel_info.setStyleSheet(
                "background: #f0f4ff; border: 1px solid #c5d0e6; border-radius: 4px; "
                "padding: 8px; color: #1e293b;"
            )
        layout.addWidget(sentinel_info)

        layout.addSpacing(8)

        # Boutons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Annuler")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Sauvegarder")
        save_btn.setDefault(True)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #2c7bb6; color: white;
                padding: 8px 20px; border: none; border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #1e5a8a; }
        """)
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _save(self):
        self.prefs["showRankBadge"]     = self.cb_rank.isChecked()
        self.prefs["showSpecialtyIcon"] = self.cb_icons.isChecked()
        self.prefs["showSpecialtyIconMobile"] = self.cb_icons_mobile.isChecked()
        self.prefs["showFlagBorders"]   = self.cb_flags.isChecked()
        self.prefs["virtualBlueFlagForNewCards"] = self.cb_virtual_flag.isChecked()
        self.prefs["virtual_flag_color_new"] = self.combo_flag_new.currentData()
        self.prefs["virtual_flag_color_suspended"] = self.combo_flag_sus.currentData()
        self.prefs["reviewDualFlags"]   = self.cb_dual_flags_review.isChecked()
        self.prefs["previewDualFlags"]  = self.cb_preview_dual_flags.isChecked()
        save_prefs(self.prefs)
        _push_prefs_to_webview()
        tooltip("Préférences cartes sauvegardées !")
        self.accept()


def open_card_styles_dialog():
    try:
        if __package__:
            from .settings_dialog import EDNSettingsDialog
        else:
            from settings_dialog import EDNSettingsDialog
        dlg = EDNSettingsDialog(mw, initial_tab=1)
        dlg.exec()
    except Exception:
        dlg = CardStylesDialog(mw)
        dlg.exec()


# ─────────────────────────────────────────────
#  Synchronisation multi-appareils via _edn_prefs.js
# ─────────────────────────────────────────────

def _sync_prefs_from_media_file():
    """
    Lit le fichier _edn_prefs.js dans le dossier média.
    N'écrase les préférences locales QUE si elles n'existent pas encore (premier démarrage/
    appareil sans JSON local). Si le JSON local existe, il est la source de vérité.
    """
    try:
        import re
        if not mw.col:
            return

        # Si le fichier JSON local existe déjà, il est prioritaire : on ne le remplace jamais.
        local_path = _prefs_file()
        if local_path and os.path.exists(local_path):
            return

        media_dir = mw.col.media.dir()
        js_path = os.path.join(media_dir, "_edn_prefs.js")
        if not os.path.exists(js_path):
            return

        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        match = re.search(r"var\s+edn_prefs\s*=\s*(\{.*?\})\s*;?", content, re.DOTALL)
        if match:
            prefs_str = match.group(1)
            prefs = json.loads(prefs_str)
            if prefs:
                merged_prefs = {**DEFAULT_PREFS, **prefs}
                with open(local_path, "w", encoding="utf-8") as f:
                    json.dump(merged_prefs, f, ensure_ascii=False, indent=2)
                print(f"[EDN Card Styles] Prefs initialisees depuis _edn_prefs.js (premier demarrage)")
    except Exception as e:
        print(f"[EDN Card Styles] Erreur sync depuis _edn_prefs.js: {e}")


def setup_card_styles_menu():
    """Obsolète : Les réglages de cartes sont directement accessibles via Paramètres EDN."""
    pass

def _on_profile_opened():
    # 1) Initialiser depuis _edn_prefs.js uniquement si le JSON local est absent (premier démarrage sur cet appareil)
    _sync_prefs_from_media_file()
    # 2) Toujours (ré)écrire le _edn_prefs.js depuis le JSON local — le JSON local est la source de vérité
    try:
        p = load_prefs()
        _write_prefs_to_media_folder(p)
    except Exception as e:
        print(f"[EDN Card Styles] Erreur ecriture demarrage: {e}")


def init_card_styles():
    """Initialise les hooks du reviewer."""
    gui_hooks.reviewer_did_show_question.append(_on_reviewer_did_show_question)
    gui_hooks.card_will_show.append(_on_card_will_show)
    gui_hooks.profile_did_open.append(_on_profile_opened)
    gui_hooks.sync_did_finish.append(_sync_prefs_from_media_file)
    print("[EDN Card Styles] Hooks installes avec succes.")
