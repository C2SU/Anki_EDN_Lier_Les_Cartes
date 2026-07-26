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
from .shared_menu import register_module, register_action

ADDON_PATH = os.path.dirname(os.path.abspath(__file__))
PREFS_KEY = "edn_card_prefs"

DEFAULT_PREFS = {
    "showRankBadge": False,
    "showSpecialtyIcon": True,
    "showSpecialtyIconMobile": False,
    "showFlagBorders": True,
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


def _write_prefs_to_media_folder(prefs: dict):
    try:
        if mw.col:
            media_dir = mw.col.media.dir()
            js_path = os.path.join(media_dir, "_edn_prefs.js")
            js_content = f"var edn_prefs = {json.dumps(prefs, ensure_ascii=False)};\n"
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

def _push_prefs_to_webview():
    """Écrit les préférences dans localStorage via eval JS."""
    try:
        prefs = load_prefs()
        js = f"localStorage.setItem('edn_card_prefs', JSON.stringify({json.dumps(prefs)}));"
        if mw and mw.reviewer and mw.reviewer.web:
            mw.reviewer.web.eval(js)
    except Exception as e:
        print(f"[EDN Card Styles] Erreur injection: {e}")


def _on_reviewer_did_show_question(card) -> None:
    """Injecte les préférences au chargement de chaque question (recto)."""
    _push_prefs_to_webview()


# ─────────────────────────────────────────────
#  Dialog de réglages Cartes
# ─────────────────────────────────────────────

class CardStylesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🃏 Réglages Cartes EDN")
        self.setMinimumWidth(420)
        self.prefs = load_prefs()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("<h3>🃏 Réglages visuels des cartes</h3>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        desc = QLabel(
            "Ces réglages s'appliquent à toutes les cartes EDN. "
            "Ils sont persistants sur cet appareil (via localStorage).<br>"
            "<small style='color:#888'>Rechargez la carte courante après modification.</small>"
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # Toggle 1 : Couleur de rang sur le premier champ
        self.cb_rank = QCheckBox("⚫ Colorer la barre d'icône du premier champ selon le rang")
        self.cb_rank.setChecked(self.prefs.get("showRankBadge", True))
        self.cb_rank.setToolTip(
            "Colore la barre verticale d'icône du premier champ en fonction du rang de la carte.\n"
            "⚫ Noir = Rang A   🔵 Bleu = Rang B   🟢 Vert = Rang C   ⚪ Gris = non classé"
        )
        layout.addWidget(self.cb_rank)

        rank_desc = QLabel(
            "  <small>La barre verticale d'icône (gauche) prend la couleur du rang.<br>"
            "  ⚫ Noir = Rang A (Essentiel) | 🔵 Bleu = Rang B (Important) | 🟢 Vert = Rang C.</small>"
        )
        rank_desc.setStyleSheet("color: #888; margin-left: 20px;")
        layout.addWidget(rank_desc)

        layout.addSpacing(8)

        # Toggle 2 : Pictogrammes de matière (Desktop)
        self.cb_icons = QCheckBox("🧩 Afficher les pictogrammes de matière sur cet ordinateur")
        self.cb_icons.setChecked(self.prefs.get("showSpecialtyIcon", True))
        self.cb_icons.setToolTip(
            "Affiche les émojis des matières médicales concernées par la carte,\n"
            "juste au-dessus du recto."
        )
        layout.addWidget(self.cb_icons)

        # Toggle 2b : Pictogrammes de matière (Mobile)
        self.cb_icons_mobile = QCheckBox("📱 Afficher les pictogrammes de matière sur mobile")
        self.cb_icons_mobile.setChecked(self.prefs.get("showSpecialtyIconMobile", True))
        self.cb_icons_mobile.setToolTip(
            "Affiche les émojis des matières médicales concernées par la carte\n"
            "lorsque vous révisez sur mobile."
        )
        layout.addWidget(self.cb_icons_mobile)

        icons_desc = QLabel(
            "  <small>Ex : 🫀 Cardiologie, 🧠 Neurologie, 🩸 Hématologie…<br>"
            "  Basé sur les tags de spécialité racine de la carte.</small>"
        )
        icons_desc.setStyleSheet("color: #888; margin-left: 20px;")
        layout.addWidget(icons_desc)

        layout.addSpacing(8)

        # Toggle 3 : Encadrement selon drapeaux
        self.cb_flags = QCheckBox("🎴 Encadrer les cartes selon les drapeaux")
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
        flags_desc.setStyleSheet("color: #888; margin-left: 20px;")
        layout.addWidget(flags_desc)

        layout.addSpacing(16)

        # Info note sentinelle
        sentinel_info = QLabel(
            "💾 <small><b>Synchronisation multi-appareils :</b> les préférences sont sauvegardées "
            "localement et dans une note sentinelle (dans son champ personnel <b>Erreurs faites</b>). "
            "Elles seront chargées automatiquement sur les appareils mobiles lors de l'affichage.</small>"
        )
        sentinel_info.setWordWrap(True)
        sentinel_info.setStyleSheet(
            "background: #f0f4ff; border: 1px solid #c5d0e6; border-radius: 4px; "
            "padding: 8px; color: #333;"
        )
        layout.addWidget(sentinel_info)

        layout.addSpacing(8)

        # Boutons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Annuler")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("💾  Sauvegarder")
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
        save_prefs(self.prefs)
        _push_prefs_to_webview()
        tooltip("✅ Préférences cartes sauvegardées !")
        self.accept()


def open_card_styles_dialog():
    dlg = CardStylesDialog(mw)
    dlg.exec()


# ─────────────────────────────────────────────
#  Synchronisation multi-appareils via _edn_prefs.js
# ─────────────────────────────────────────────

def _sync_prefs_from_media_file():
    """
    Lit le fichier _edn_prefs.js dans le dossier média.
    Si les réglages y sont différents de la configuration locale, met à jour le fichier local.
    """
    try:
        import re
        if not mw.col:
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
                local_prefs = load_prefs()
                merged_prefs = {**DEFAULT_PREFS, **prefs}
                if local_prefs != merged_prefs:
                    # Sauvegarder localement sans réécrire le fichier média
                    p = _prefs_file()
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump(merged_prefs, f, ensure_ascii=False, indent=2)
                    print(f"[EDN Card Styles] Prefs localisees mises a jour depuis _edn_prefs.js")
    except Exception as e:
        print(f"[EDN Card Styles] Erreur sync depuis _edn_prefs.js: {e}")


def setup_card_styles_menu():
    try:
        from .shared_menu import get_edn_menu
        main_menu = get_edn_menu()
        if not main_menu:
            return
            
        # Search if the submenu "Réglages Cartes EDN" already exists
        sub_menu = None
        for action in main_menu.actions():
            menu = action.menu()
            if menu and menu.title() == "Réglages Cartes EDN":
                sub_menu = menu
                break
                
        if not sub_menu:
            sub_menu = QMenu("Réglages Cartes EDN", main_menu)
            # Insert before the separator at the bottom
            actions = main_menu.actions()
            if len(actions) >= 2:
                main_menu.insertMenu(actions[-2], sub_menu)
            else:
                main_menu.addMenu(sub_menu)
        
        # We will dynamically recreate or update actions about to show
        def refresh_menu_actions():
            sub_menu.clear()
            prefs = load_prefs()
            
            # Action 1: Colorer la barre d'icône
            action_rank = QAction("⚫ Colorer la barre selon le rang", sub_menu)
            action_rank.setCheckable(True)
            action_rank.setChecked(prefs.get("showRankBadge", True))
            def toggle_rank(checked):
                p = load_prefs()
                p["showRankBadge"] = checked
                save_prefs(p)
                _push_prefs_to_webview()
            action_rank.triggered.connect(toggle_rank)
            sub_menu.addAction(action_rank)
            
            # Action 2: Afficher les pictogrammes de matière (Ordinateur)
            action_icons = QAction("🧩 Afficher les pictogrammes sur ordinateur", sub_menu)
            action_icons.setCheckable(True)
            action_icons.setChecked(prefs.get("showSpecialtyIcon", True))
            def toggle_icons(checked):
                p = load_prefs()
                p["showSpecialtyIcon"] = checked
                save_prefs(p)
                _push_prefs_to_webview()
            action_icons.triggered.connect(toggle_icons)
            sub_menu.addAction(action_icons)

            # Action 2b: Afficher les pictogrammes de matière (Mobile)
            action_icons_mobile = QAction("📱 Afficher les pictogrammes sur mobile", sub_menu)
            action_icons_mobile.setCheckable(True)
            action_icons_mobile.setChecked(prefs.get("showSpecialtyIconMobile", True))
            def toggle_icons_mobile(checked):
                p = load_prefs()
                p["showSpecialtyIconMobile"] = checked
                save_prefs(p)
                _push_prefs_to_webview()
            action_icons_mobile.triggered.connect(toggle_icons_mobile)
            sub_menu.addAction(action_icons_mobile)
            
            # Action 3: Encadrer selon les drapeaux
            action_flags = QAction("🎴 Encadrer les cartes selon les drapeaux", sub_menu)
            action_flags.setCheckable(True)
            action_flags.setChecked(prefs.get("showFlagBorders", True))
            def toggle_flags(checked):
                p = load_prefs()
                p["showFlagBorders"] = checked
                save_prefs(p)
                _push_prefs_to_webview()
            action_flags.triggered.connect(toggle_flags)
            sub_menu.addAction(action_flags)
            
            # Separator + Paramètres
            sub_menu.addSeparator()
            action_settings = QAction("Paramètres...", sub_menu)
            action_settings.triggered.connect(open_card_styles_dialog)
            sub_menu.addAction(action_settings)
            
        sub_menu.aboutToShow.connect(refresh_menu_actions)
        # Initialize menu actions once
        refresh_menu_actions()
            
    except Exception as e:
        print(f"[EDN Card Styles] Failed to setup submenu: {e}")


# ─────────────────────────────────────────────
#  Initialisation
# ─────────────────────────────────────────────

def _on_profile_opened():
    # Synchroniser les préférences locales à partir de _edn_prefs.js
    _sync_prefs_from_media_file()
    # Forcer l'écriture des préférences locales dans le dossier média au cas où le fichier serait absent
    try:
        p = load_prefs()
        _write_prefs_to_media_folder(p)
    except Exception as e:
        print(f"[EDN Card Styles] Erreur ecriture demarrage: {e}")


def init_card_styles():
    """Initialise les hooks du reviewer."""
    gui_hooks.reviewer_did_show_question.append(_on_reviewer_did_show_question)
    gui_hooks.profile_did_open.append(_on_profile_opened)
    gui_hooks.sync_did_finish.append(_sync_prefs_from_media_file)
    print("[EDN Card Styles] Hooks installes avec succes.")
