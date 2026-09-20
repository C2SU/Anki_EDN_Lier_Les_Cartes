"""
EDN Settings Dialog
Centralized settings for all EDN modules, Recommended Addons, Card Styles, and Linked Cards
"""
import os
import json
from aqt import mw
from aqt.qt import *

try:
    from .shared_menu import get_registered_modules, is_module_enabled, set_module_enabled
    from .card_styles import (
        load_prefs, save_prefs, _push_prefs_to_webview,
        ANKI_FLAGS, FLAG_COLORS, DEFAULT_PREFS
    )
except ImportError:
    from shared_menu import get_registered_modules, is_module_enabled, set_module_enabled
    from card_styles import (
        load_prefs, save_prefs, _push_prefs_to_webview,
        ANKI_FLAGS, FLAG_COLORS, DEFAULT_PREFS
    )


# ── Addon catalog definition (Sans emojis) ──
RECOMMENDED_ADDONS_CATALOG = [
    # ── Essentiels ──
    {
        "id": "445658251",
        "alt_ids": ["Anki_EDN_Lier_les_cartes", "445658251"],
        "module_id": "linked_cards",
        "name": "Lier les cartes Anki EDN",
        "description": "Recherche rapide, liens bidirectionnels et prévisualisation des cartes.",
        "url": "https://ankiweb.net/shared/info/445658251",
        "is_core_edn": True,
    },
    {
        "id": "Anki_EDN_Stats",
        "alt_ids": ["Anki_EDN_Stats"],
        "module_id": "edn_progress",
        "name": "EDN Stat",
        "description": "Statistiques d'avancement, maîtrise et difficulté des items EDN.",
        "url": "https://c2su.github.io/Anki_EDN/Anki_EDN.html",
        "is_core_edn": True,
    },
    {
        "id": "2135076010",
        "alt_ids": ["Anki_EDN_nid", "2135076010"],
        "module_id": "anki_edn_nid",
        "name": "Anki EDN NID (Réparer NID)",
        "description": "Migration et synchronisation des identifiants (NID) des notes.",
        "url": "https://ankiweb.net/shared/info/2135076010",
        "is_core_edn": True,
    },
    {
        "id": "1957538407",
        "alt_ids": ["1957538407", "AnkiCollab"],
        "module_id": None,
        "name": "AnkiCollab",
        "description": "Synchronisation collaborative des decks partagés EDN.",
        "url": "https://ankiweb.net/shared/info/1957538407",
        "is_core_edn": True,
    },
    {
        "id": "1052724801",
        "alt_ids": ["1052724801", "BetterSearch"],
        "module_id": None,
        "name": "BetterSearch",
        "description": "Recherche enrichie et autocomplétion intelligente dans le navigateur.",
        "url": "https://ankiweb.net/shared/info/1052724801",
        "is_core_edn": True,
    },
    {
        "id": "2040501954",
        "alt_ids": ["2040501954", "Symbols As You Type"],
        "module_id": None,
        "name": "Symbols As You Type",
        "description": "Insertion rapide de symboles et abréviations médicales.",
        "url": "https://ankiweb.net/shared/info/2040501954",
        "is_core_edn": True,
    },
    # ── Recommandés ──
    {
        "id": "759844606",
        "alt_ids": ["759844606", "FSRS Helper"],
        "module_id": None,
        "name": "FSRS Helper",
        "description": "Outils FSRS : avance, report et équilibrage de charge.",
        "url": "https://ankiweb.net/shared/info/759844606",
        "is_core_edn": False,
    },
    {
        "id": "501542723",
        "alt_ids": ["501542723", "Auto Sync"],
        "module_id": None,
        "name": "Auto Sync",
        "description": "Synchronisation automatique à l'ouverture et fermeture d'Anki.",
        "url": "https://ankiweb.net/shared/info/501542723",
        "is_core_edn": False,
    },
    {
        "id": "161867424",
        "alt_ids": ["161867424", "Flux V3", "1771074083", "Review Heatmap"],
        "module_id": None,
        "name": "Flux V3 (Review Heatmap)",
        "description": "Visualisation de l'activité, heatmap de révision et prévisions.",
        "url": "https://ankiweb.net/shared/info/161867424",
        "is_core_edn": False,
    },
    {
        "id": "183246496",
        "alt_ids": ["183246496", "quick_image_search"],
        "module_id": None,
        "name": "Quick Image Search",
        "description": "Recherche et insertion rapide d'images dans vos cartes.",
        "url": "https://ankiweb.net/shared/info/183246496",
        "is_core_edn": False,
    },
]

DEFAULT_LINKED_CARDS_CONFIG = {
    "assistant_panel_open": False,
    "description_intelligente": True,
    "suggestions_de_liens": True,
    "gui_mirror_cb_state": True,
    "gui_recto_only_cb_state": False,
    "gui_trigger": "nid:",
    "hidden_preview_sections": [
        "sourcesMegaContainer",
        "commentsMegaContainer",
        "tagsMegaContainer"
    ],
    "mirror_link_on_paste": True
}


def is_dark_mode() -> bool:
    """Détecte si l'interface d'Anki est actuellement en mode sombre."""
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
        if mw and hasattr(mw, "pm") and hasattr(mw.pm, "night_mode"):
            return bool(mw.pm.night_mode())
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


def get_theme_colors() -> dict:
    """Retourne la palette de couleurs adaptée au thème sombre ou clair."""
    dark = is_dark_mode()
    if dark:
        return {
            "dark": True,
            "bg_dialog": "#1e2227",
            "bg_pane": "#282c34",
            "bg_card": "#21252b",
            "bg_card_hover": "#2c313a",
            "bg_tab": "#1e2227",
            "bg_tab_hover": "#2c313a",
            "bg_tab_selected": "#282c34",
            "border_pane": "#3e4451",
            "border_card": "#373b44",
            "border_card_hover": "#4b5563",
            "border_tab": "#3e4451",
            "border_input": "#3e4451",
            "border_input_hover": "#38bdf8",
            "bg_input": "#1e2227",
            "text_main": "#f1f5f9",
            "text_muted": "#94a3b8",
            "text_sub": "#cbd5e1",
            "accent": "#38bdf8",
            "accent_hover": "#0284c7",
            "btn_default_bg": "#2c313a",
            "btn_default_hover": "#3e4451",
            "btn_default_text": "#e2e8f0",
            "btn_default_border": "#4b5563",
            "btn_reset_bg": "#2d1518",
            "btn_reset_hover": "#3f171d",
            "btn_reset_text": "#f87171",
            "btn_reset_border": "#7f1d1d",
            "badge_active_bg": "#064e3b",
            "badge_active_text": "#4ade80",
            "badge_active_border": "#059669",
            "badge_disabled_bg": "#451a03",
            "badge_disabled_text": "#fb923c",
            "badge_disabled_border": "#b45309",
            "badge_not_installed_bg": "#172554",
            "badge_not_installed_text": "#60a5fa",
            "badge_not_installed_border": "#1d4ed8",
            "notice_bg": "#052e16",
            "notice_text": "#86efac",
            "notice_border": "#166534",
            "sep_color": "#373b44",
            "scrollbar_thumb": "#4b5563",
            "scrollbar_thumb_hover": "#6b7280",
            "tooltip_bg": "#0f172a",
            "tooltip_text": "#f8fafc",
            "tooltip_border": "#334155",
        }
    else:
        return {
            "dark": False,
            "bg_dialog": "#f8fafc",
            "bg_pane": "#ffffff",
            "bg_card": "#f8fafc",
            "bg_card_hover": "#f1f5f9",
            "bg_tab": "#f1f5f9",
            "bg_tab_hover": "#e2e8f0",
            "bg_tab_selected": "#ffffff",
            "border_pane": "#cbd5e1",
            "border_card": "#e2e8f0",
            "border_card_hover": "#cbd5e1",
            "border_tab": "#cbd5e1",
            "border_input": "#cbd5e1",
            "border_input_hover": "#0284c7",
            "bg_input": "#ffffff",
            "text_main": "#0f172a",
            "text_muted": "#64748b",
            "text_sub": "#1e293b",
            "accent": "#0284c7",
            "accent_hover": "#0369a1",
            "btn_default_bg": "#f1f5f9",
            "btn_default_hover": "#e2e8f0",
            "btn_default_text": "#334155",
            "btn_default_border": "#cbd5e1",
            "btn_reset_bg": "#fef2f2",
            "btn_reset_hover": "#fee2e2",
            "btn_reset_text": "#b91c1c",
            "btn_reset_border": "#fca5a5",
            "badge_active_bg": "#dcfce7",
            "badge_active_text": "#15803d",
            "badge_active_border": "#bbf7d0",
            "badge_disabled_bg": "#ffedd5",
            "badge_disabled_text": "#c2410c",
            "badge_disabled_border": "#fed7aa",
            "badge_not_installed_bg": "#e0f2fe",
            "badge_not_installed_text": "#0369a1",
            "badge_not_installed_border": "#bae6fd",
            "notice_bg": "#f0fdf4",
            "notice_text": "#166534",
            "notice_border": "#bbf7d0",
            "sep_color": "#e2e8f0",
            "scrollbar_thumb": "#cbd5e1",
            "scrollbar_thumb_hover": "#94a3b8",
            "tooltip_bg": "#0f172a",
            "tooltip_text": "#f8fafc",
            "tooltip_border": "#334155",
        }


def make_selectable_label(text: str) -> QLabel:
    """Crée un QLabel sélectionnable/copiable sans bordure parasite avec retour à la ligne automatique."""
    lbl = QLabel(text)
    lbl.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
        | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    lbl.setWordWrap(True)
    lbl.setOpenExternalLinks(True)
    lbl.setStyleSheet("border: none; background: transparent;")
    return lbl


def is_dark_color(hex_color: str) -> bool:
    """Détermine si une couleur hexadécimale est sombre (pour ajuster la couleur du texte)."""
    try:
        c = QColor(hex_color)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return lum < 140
    except Exception:
        return True


def create_color_icon(hex_color: str, size: int = 14) -> QIcon:
    """Crée une icône carrée/arrondie avec la couleur spécifiée."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(hex_color)))
    painter.setPen(QPen(QColor("#475569" if not is_dark_color(hex_color) else "#334155"), 1))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    painter.end()
    return QIcon(pixmap)


class NoWheelComboBox(QComboBox):
    """QComboBox qui ignore la molette de souris pour éviter les changements accidentels lors du défilement."""
    def wheelEvent(self, event):
        event.ignore()


class EDNSettingsDialog(QDialog):
    def __init__(self, parent=None, initial_tab=0):
        super().__init__(parent)
        self.setWindowTitle("Paramètres Anki EDN")
        self.setMinimumWidth(680)
        self.setMinimumHeight(600)
        self.T = get_theme_colors()
        self.prefs = load_prefs()
        self.rank_color_a = self.prefs.get("rank_color_a", DEFAULT_PREFS["rank_color_a"])
        self.rank_color_b = self.prefs.get("rank_color_b", DEFAULT_PREFS["rank_color_b"])
        self.rank_color_c = self.prefs.get("rank_color_c", DEFAULT_PREFS["rank_color_c"])
        self.addon_rows = []
        self.module_checkboxes = {}
        self.preview_section_cbs = {}
        self.linked_cards_cfg = self._load_linked_cards_config()
        self.setup_ui(initial_tab)

    def _load_linked_cards_config(self) -> dict:
        cfg = dict(DEFAULT_LINKED_CARDS_CONFIG)
        mgr = getattr(mw, "addonManager", None) if mw else None
        if mgr:
            for aid in ["Anki_EDN_Lier_les_cartes", "445658251"]:
                loaded = mgr.getConfig(aid)
                if loaded is not None:
                    cfg.update(loaded)
                    break
        return cfg

    def _save_linked_cards_config(self, updates: dict):
        mgr = getattr(mw, "addonManager", None) if mw else None
        if mgr:
            for aid in ["Anki_EDN_Lier_les_cartes", "445658251"]:
                cfg = mgr.getConfig(aid)
                if cfg is not None:
                    cfg.update(updates)
                    mgr.writeConfig(aid, cfg)

    def setup_ui(self, initial_tab=0):
        T = self.T
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {T["bg_dialog"]};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {T["text_main"]};
            }}
            QCheckBox {{
                spacing: 8px;
                color: {T["text_main"]};
                font-size: 13px;
                border: none;
                background: transparent;
            }}
            QTabWidget::pane {{
                border: 1px solid {T["border_pane"]};
                background: {T["bg_pane"]};
                border-radius: 8px;
                top: -1px;
            }}
            QTabBar::tab {{
                background: {T["bg_tab"]};
                border: 1px solid {T["border_tab"]};
                border-bottom: none;
                padding: 8px 20px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
                font-size: 13px;
                color: {T["text_muted"]};
            }}
            QTabBar::tab:selected {{
                background: {T["bg_tab_selected"]};
                border-bottom: 2px solid {T["bg_tab_selected"]};
                color: {T["accent"]};
            }}
            QTabBar::tab:hover:!selected {{
                background: {T["bg_tab_hover"]};
                color: {T["text_main"]};
            }}
            QComboBox {{
                border: 1px solid {T["border_input"]};
                border-radius: 5px;
                padding: 4px 10px;
                background: {T["bg_input"]};
                color: {T["text_main"]};
                min-height: 24px;
            }}
            QComboBox:hover, QComboBox:focus {{
                border-color: {T["border_input_hover"]};
            }}
            QComboBox QAbstractItemView {{
                background-color: {T["bg_card"]};
                color: {T["text_main"]};
                selection-background-color: {T["accent"]};
                selection-color: #ffffff;
                border: 1px solid {T["border_pane"]};
            }}
            QSpinBox {{
                border: 1px solid {T["border_input"]};
                border-radius: 5px;
                padding: 3px 8px;
                background: {T["bg_input"]};
                color: {T["text_main"]};
                min-height: 24px;
                font-weight: 600;
            }}
            QSpinBox:hover, QSpinBox:focus {{
                border-color: {T["border_input_hover"]};
            }}
            QLineEdit {{
                background: {T["bg_input"]};
                color: {T["text_main"]};
                border: 1px solid {T["border_input"]};
                border-radius: 4px;
                padding: 3px 8px;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: {T["border_input_hover"]};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {T["scrollbar_thumb"]};
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {T["scrollbar_thumb_hover"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
            QToolTip {{
                background-color: {T["tooltip_bg"]};
                color: {T["tooltip_text"]};
                border: 1px solid {T["tooltip_border"]};
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 12px;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # ── Tab widget ──
        self.tab_widget = QTabWidget()

        # Logo incrusté à droite des onglets
        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            logo_container = QWidget()
            logo_container.setStyleSheet("background: transparent;")
            logo_layout = QHBoxLayout(logo_container)
            logo_layout.setContentsMargins(0, 0, 6, 0)
            logo_layout.setSpacing(4)
            logo_label = QLabel()
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(
                    26, 26, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                logo_label.setPixmap(scaled_pixmap)
                logo_layout.addWidget(logo_label)
            self.tab_widget.setCornerWidget(logo_container, Qt.Corner.TopRightCorner)

        # Tab 1: Modules & Addons
        modules_tab = self._build_addons_tab()
        self.tab_widget.addTab(modules_tab, "Addons et Outils")

        # Tab 2: Card Styles
        cards_tab = self._build_cards_tab()
        self.tab_widget.addTab(cards_tab, "Réglages Cartes")

        # Tab 3: Linked Cards (Dynamic)
        self.linked_cards_tab = self._build_linked_cards_tab()
        self._update_linked_cards_tab_visibility()

        if initial_tab < self.tab_widget.count():
            self.tab_widget.setCurrentIndex(initial_tab)
        main_layout.addWidget(self.tab_widget, 1)

        # ── Bottom buttons ──
        button_layout = QHBoxLayout()

        reset_defaults_btn = QPushButton("Rétablir les paramètres par défaut")
        reset_defaults_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T["btn_reset_bg"]};
                color: {T["btn_reset_text"]};
                border: 1px solid {T["btn_reset_border"]};
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {T["btn_reset_hover"]};
                border-color: {T["btn_reset_text"]};
            }}
        """)
        reset_defaults_btn.clicked.connect(self.reset_to_defaults)
        button_layout.addWidget(reset_defaults_btn)

        button_layout.addStretch()

        cancel_btn = QPushButton("Annuler")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T["btn_default_bg"]};
                color: {T["btn_default_text"]};
                border: 1px solid {T["btn_default_border"]};
                padding: 8px 18px;
                border-radius: 6px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {T["btn_default_hover"]};
                color: {T["text_main"]};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Valider et Sauvegarder")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T["accent"]};
                color: white;
                padding: 8px 22px;
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {T["accent_hover"]};
            }}
        """)
        save_btn.clicked.connect(self.save_and_apply)
        button_layout.addWidget(save_btn)

        main_layout.addLayout(button_layout)

    # ─────────────────────────────────────────────
    #  Tab 1 : Addons & Outils (Essentiels vs Recommandés)
    # ─────────────────────────────────────────────

    def _get_addon_status(self, item: dict):
        mgr = getattr(mw, "addonManager", None) if mw else None
        installed_addons = set(mgr.allAddons()) if (mgr and hasattr(mgr, "allAddons")) else set()

        for aid in item.get("alt_ids", []):
            if aid in installed_addons:
                is_enabled = mgr.isEnabled(aid) if (mgr and hasattr(mgr, "isEnabled")) else True
                mod_id = item.get("module_id")
                if mod_id and not is_module_enabled(mod_id):
                    is_enabled = False
                return ("installed_active" if is_enabled else "installed_disabled", aid)

        mod_id = item.get("module_id")
        if mod_id and mod_id in get_registered_modules():
            is_enabled = is_module_enabled(mod_id)
            return ("installed_active" if is_enabled else "installed_disabled", mod_id)

        return ("not_installed", item.get("id"))

    def _create_addon_card(self, item: dict) -> QFrame:
        T = self.T
        status, matched_id = self._get_addon_status(item)

        card_frame = QFrame()
        card_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {T["bg_card"]};
                border: 1px solid {T["border_card"]};
                border-radius: 6px;
            }}
            QFrame:hover {{
                background-color: {T["bg_card_hover"]};
                border-color: {T["border_card_hover"]};
            }}
        """)
        card_layout = QHBoxLayout(card_frame)
        card_layout.setContentsMargins(12, 6, 12, 6)
        card_layout.setSpacing(10)

        # Checkbox
        cb = QCheckBox()
        if status == "installed_active":
            cb.setChecked(True)
        elif status == "installed_disabled":
            cb.setChecked(False)
        else:
            cb.setChecked(False)

        card_layout.addWidget(cb)

        # Details
        name_html = f"<a href='{item['url']}' style='color: {T['accent']}; text-decoration: none; font-weight: 600; font-size: 13px;'>{item['name']}</a>"
        name_lbl = make_selectable_label(name_html)
        card_layout.addWidget(name_lbl, 1)

        # Status Badge
        if status == "installed_active":
            badge_text = "Actif"
            badge_style = f"background: {T['badge_active_bg']}; color: {T['badge_active_text']}; border: 1px solid {T['badge_active_border']};"
        elif status == "installed_disabled":
            badge_text = "Désactivé"
            badge_style = f"background: {T['badge_disabled_bg']}; color: {T['badge_disabled_text']}; border: 1px solid {T['badge_disabled_border']};"
        else:
            badge_text = "Non installé"
            badge_style = f"background: {T['badge_not_installed_bg']}; color: {T['badge_not_installed_text']}; border: 1px solid {T['badge_not_installed_border']};"

        badge = QLabel(f"<span style='padding: 2px 7px; border-radius: 4px; font-size: 10.5px; font-weight: bold; {badge_style}'>{badge_text}</span>")
        badge.setStyleSheet("border: none; background: transparent;")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(badge)

        self.addon_rows.append({
            "item": item,
            "checkbox": cb,
            "status": status,
            "matched_id": matched_id
        })

        if item.get("module_id"):
            self.module_checkboxes[item["module_id"]] = cb
            if item["module_id"] == "linked_cards":
                cb.stateChanged.connect(lambda s: self._update_linked_cards_tab_visibility())

        # Tooltip propre au survol
        card_frame.setToolTip(f"{item['name']}\n{item['description']}\nURL : {item['url']}")
        cb.setToolTip(f"Activer / Désactiver {item['name']}")

        return card_frame

    def _build_addons_tab(self) -> QWidget:
        T = self.T
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 4, 0)
        container_layout.setSpacing(8)

        self.addon_rows = []
        self.module_checkboxes = {}

        core_addons = [item for item in RECOMMENDED_ADDONS_CATALOG if item.get("is_core_edn")]
        rec_addons = [item for item in RECOMMENDED_ADDONS_CATALOG if not item.get("is_core_edn")]

        # Section 1 : Add-ons essentiels EDN
        core_header = make_selectable_label(
            f"<span style='font-size: 13.5px; font-weight: bold; color: {T['text_main']};'>Add-ons essentiels EDN</span>"
        )
        container_layout.addWidget(core_header)

        for item in core_addons:
            card = self._create_addon_card(item)
            container_layout.addWidget(card)

        container_layout.addSpacing(6)

        # Section 2 : Add-ons recommandés
        rec_header = make_selectable_label(
            f"<span style='font-size: 13.5px; font-weight: bold; color: {T['text_main']};'>Add-ons recommandés</span>"
        )
        container_layout.addWidget(rec_header)

        for item in rec_addons:
            card = self._create_addon_card(item)
            container_layout.addWidget(card)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # ── Shortcuts & Community Links ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet(f"border: none; border-top: 1px solid {T['sep_color']}; max-height: 1px;")
        layout.addWidget(sep)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        shortcuts_btn = QPushButton("Raccourcis clavier")
        shortcuts_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T['btn_default_bg']}; color: {T['btn_default_text']}; border: 1px solid {T['btn_default_border']};
                padding: 7px 14px; border-radius: 5px; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {T['btn_default_hover']}; color: {T['text_main']}; }}
        """)
        shortcuts_btn.clicked.connect(self.open_shortcuts_dialog)
        bottom_row.addWidget(shortcuts_btn)

        bottom_row.addStretch()

        discord_btn = QPushButton("Discord")
        discord_btn.setStyleSheet("""
            QPushButton {{
                background-color: #5865F2; color: white; border: none;
                padding: 7px 12px; border-radius: 5px; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #4752C4; }}
        """)
        discord_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://discord.gg/2A7zHAEBYt")))
        bottom_row.addWidget(discord_btn)

        website_btn = QPushButton("Site")
        website_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T['accent']}; color: white; border: none;
                padding: 7px 12px; border-radius: 5px; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {T['accent_hover']}; }}
        """)
        website_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://c2su.github.io/Anki_EDN/Anki_EDN.html")))
        bottom_row.addWidget(website_btn)

        github_btn = QPushButton("GitHub")
        github_btn_bg = "#161b22" if T["dark"] else "#24292e"
        github_btn_hover = "#21262d" if T["dark"] else "#1b1f23"
        github_btn_border = f"border: 1px solid {T['border_card']};" if T["dark"] else "border: none;"
        github_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {github_btn_bg}; color: white; {github_btn_border}
                padding: 7px 12px; border-radius: 5px; font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {github_btn_hover}; }}
        """)
        github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/C2SU")))
        bottom_row.addWidget(github_btn)

        layout.addLayout(bottom_row)
        return widget

    # ─────────────────────────────────────────────
    #  Tab 2 : Réglages Cartes
    # ─────────────────────────────────────────────

    def _build_cards_tab(self) -> QWidget:
        T = self.T
        widget = QWidget()
        outer_layout = QVBoxLayout(widget)
        outer_layout.setContentsMargins(14, 14, 14, 14)
        outer_layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)

        # ── Group 1 : Styles visuels des cartes ──
        group_visual = QFrame()
        group_visual.setStyleSheet(f"""
            QFrame {{
                background-color: {T["bg_card"]};
                border: 1px solid {T["border_card"]};
                border-radius: 8px;
            }}
        """)
        gv_layout = QVBoxLayout(group_visual)
        gv_layout.setContentsMargins(14, 12, 14, 12)
        gv_layout.setSpacing(10)

        gv_layout.addWidget(make_selectable_label(f"<b style='color: {T['text_main']};'>Affichage sur les Cartes</b>"))

        # Toggle 1 : Barre de rang
        self.cb_rank = QCheckBox("Colorer la barre latérale selon le rang de la carte")
        self.cb_rank.setChecked(self.prefs.get("showRankBadge", False))
        self.cb_rank.setToolTip(
            "Colore la barre verticale d'icône (gauche) du premier champ selon le rang officiel :\n"
            "• Rang A : Essentiel (par défaut Noir)\n"
            "• Rang B : Important (par défaut Bleu)\n"
            "• Rang C : Spécialité (par défaut Vert)"
        )
        gv_layout.addWidget(self.cb_rank)

        # Conteneur des réglages de couleurs de rang
        self.rank_colors_widget = QWidget()
        self.rank_colors_widget.setStyleSheet("background: transparent; border: none;")
        rank_layout = QVBoxLayout(self.rank_colors_widget)
        rank_layout.setContentsMargins(16, 0, 4, 4)
        rank_layout.setSpacing(6)

        ranks_config = [
            ("A", "Rang A", "(Essentiel)", DEFAULT_PREFS["rank_color_a"]),
            ("B", "Rang B", "(Important)", DEFAULT_PREFS["rank_color_b"]),
            ("C", "Rang C", "(Spécialité)", DEFAULT_PREFS["rank_color_c"]),
        ]

        for letter, title_text, desc_text, default_hex in ranks_config:
            row_frame = QFrame()
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(10, 4, 10, 4)
            row_layout.setSpacing(8)

            lbl = make_selectable_label(f"<b style='color: {T['text_main']};'>{title_text}</b> <span style='color: {T['text_muted']}; font-size: 11.5px;'>{desc_text}</span>")
            lbl.setMinimumWidth(160)
            row_layout.addWidget(lbl)

            row_layout.addStretch()

            color_btn = QPushButton()
            color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            color_btn.setToolTip("Cliquer pour choisir une couleur personnalisée")
            color_btn.clicked.connect(lambda checked=False, l=letter: self._choose_rank_color(l))
            row_layout.addWidget(color_btn)

            reset_btn = QPushButton("↺ Défaut")
            reset_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {T['btn_default_bg']};
                    color: {T['btn_default_text']};
                    border: 1px solid {T['btn_default_border']};
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: {T['btn_default_hover']};
                    color: {T['text_main']};
                }}
            """)
            reset_btn.setToolTip(f"Rétablir la couleur par défaut ({default_hex})")
            reset_btn.clicked.connect(lambda checked=False, l=letter, d=default_hex: self._reset_rank_color(l, d))
            row_layout.addWidget(reset_btn)

            setattr(self, f"frame_rank_{letter.lower()}", row_frame)
            setattr(self, f"btn_rank_{letter.lower()}", color_btn)

            current_color = getattr(self, f"rank_color_{letter.lower()}")
            self._update_rank_row_style(letter, current_color)

            row_frame.setToolTip(f"Couleur du {title_text} ({desc_text}). Défaut : {default_hex}")
            rank_layout.addWidget(row_frame)

        gv_layout.addWidget(self.rank_colors_widget)
        self.cb_rank.stateChanged.connect(lambda s: self._update_rank_state())
        self._update_rank_state()

        # Toggle 2 : Pictogrammes Desktop & Mobile
        self.cb_icons = QCheckBox("Pictogrammes de matière (sur cet ordinateur)")
        self.cb_icons.setChecked(self.prefs.get("showSpecialtyIcon", True))
        self.cb_icons.setToolTip("Affiche automatiquement les icônes de spécialités médicales (Cardio, Neuro, Hémato...) au-dessus du Recto sur ordinateur.")
        gv_layout.addWidget(self.cb_icons)

        self.cb_icons_mobile = QCheckBox("Pictogrammes de matière (sur mobile)")
        self.cb_icons_mobile.setChecked(self.prefs.get("showSpecialtyIconMobile", False))
        self.cb_icons_mobile.setToolTip("Affiche les icônes de spécialités médicales lors de vos révisions sur smartphone (AnkiMobile / AnkiDroid).")
        gv_layout.addWidget(self.cb_icons_mobile)

        # Toggle 3 : Encadrement par drapeau Anki
        self.cb_flags = QCheckBox("Encadrer les cartes selon les drapeaux Anki")
        self.cb_flags.setChecked(self.prefs.get("showFlagBorders", True))
        self.cb_flags.setToolTip("Applique un contour coloré autour des cartes selon le drapeau Anki actif (Rouge, Orange, Vert, Bleu, Rose, Turquoise, Violet).")
        gv_layout.addWidget(self.cb_flags)

        # Toggle 3b : Drapeau virtuel pour les nouvelles cartes (sur cet ordinateur)
        self.cb_virtual_new_pc = QCheckBox("Drapeau virtuel pour les nouvelles cartes (sur cet ordinateur)")
        self.cb_virtual_new_pc.setChecked(self.prefs.get("virtualBlueFlagForNewCards", False))
        self.cb_virtual_new_pc.setToolTip(
            "Affiche l'encadrement coloré sur les cartes neuves lors de leur toute première révision (recto et verso) sur ordinateur,\n"
            "sans modifier le drapeau dans la base Anki. L'encadrement disparaît automatiquement dès la deuxième révision."
        )
        gv_layout.addWidget(self.cb_virtual_new_pc)

        # Toggle 3c : Drapeau virtuel pour les nouvelles cartes (sur mobile)
        self.cb_virtual_new_mobile = QCheckBox("Drapeau virtuel pour les nouvelles cartes (sur mobile)")
        self.cb_virtual_new_mobile.setChecked(self.prefs.get("virtualBlueFlagForNewCardsMobile", False))
        self.cb_virtual_new_mobile.setToolTip(
            "Affiche l'encadrement coloré sur les cartes neuves lors de leur première révision sur smartphone (AnkiMobile / AnkiDroid)."
        )
        gv_layout.addWidget(self.cb_virtual_new_mobile)

        self.frame_flag_review_new = QFrame()
        row_review_new_layout = QHBoxLayout(self.frame_flag_review_new)
        row_review_new_layout.setContentsMargins(12, 4, 10, 4)
        row_review_new_layout.setSpacing(8)

        lbl_review_new = make_selectable_label(f"Couleur des cartes <b>New</b> :")
        lbl_review_new.setMinimumWidth(160)
        self.combo_flag_review_new = NoWheelComboBox()
        for flag_id, label in ANKI_FLAGS:
            info = FLAG_COLORS.get(flag_id, {"hex": "#3182CE"})
            icon = create_color_icon(info["hex"])
            self.combo_flag_review_new.addItem(icon, f" {label}", flag_id)
        current_rev_new = self.prefs.get("virtual_flag_color_new", "flag4")
        idx_rev_new = self.combo_flag_review_new.findData(current_rev_new)
        if idx_rev_new >= 0:
            self.combo_flag_review_new.setCurrentIndex(idx_rev_new)

        row_review_new_layout.addWidget(lbl_review_new)
        row_review_new_layout.addStretch()
        row_review_new_layout.addWidget(self.combo_flag_review_new)
        gv_layout.addWidget(self.frame_flag_review_new)

        self.cb_dual_flags_review = QCheckBox("Double drapeau en révision : virtuel (50% gauche) + réel (50% droit)")
        self.cb_dual_flags_review.setChecked(self.prefs.get("reviewDualFlags", False))
        self.cb_dual_flags_review.setToolTip(
            "Pour les cartes ayant un drapeau réel ET un drapeau virtuel (nouvelle carte),\n"
            "divise l'encadrement en révision : drapeau virtuel à gauche (50%) et drapeau réel à droite (50%)."
        )
        gv_layout.addWidget(self.cb_dual_flags_review)

        def _update_review_new_state():
            is_active = (self.cb_virtual_new_pc.isChecked() or self.cb_virtual_new_mobile.isChecked()) and self.cb_flags.isChecked()
            self.frame_flag_review_new.setEnabled(is_active)
            self.cb_dual_flags_review.setEnabled(is_active)
            self._update_flag_row_style(self.combo_flag_review_new, self.frame_flag_review_new)

        self.cb_virtual_new_pc.stateChanged.connect(lambda s: _update_review_new_state())
        self.cb_virtual_new_mobile.stateChanged.connect(lambda s: _update_review_new_state())
        self.cb_flags.stateChanged.connect(lambda s: _update_review_new_state())
        self.combo_flag_review_new.currentIndexChanged.connect(
            lambda idx: self._update_flag_row_style(self.combo_flag_review_new, self.frame_flag_review_new)
        )
        _update_review_new_state()

        # Toggle 3c : Drapeau virtuel pour les cartes "leech"
        again_tooltip = (
            "Applique un drapeau virtuel autour des cartes ayant atteint le seuil d'échecs (Again) choisi (par défaut 7).\n"
            "Pour rechercher ces cartes dans le navigateur Anki, entrez : prop:lapses>=X (ex: prop:lapses>=7)."
        )
        again_row = QHBoxLayout()
        again_row.setSpacing(8)
        self.cb_again_flag = QCheckBox('Drapeau virtuel pour les cartes "leech"')
        self.cb_again_flag.setChecked(self.prefs.get("flagAgainEnabled", False))
        self.cb_again_flag.setToolTip(again_tooltip)
        again_row.addWidget(self.cb_again_flag)

        self.spin_again_count = QSpinBox()
        self.spin_again_count.setRange(1, 20)
        self.spin_again_count.setValue(self.prefs.get("flagAgainCount", 7))
        self.spin_again_count.setSuffix(" fois")
        self.spin_again_count.setMaximumWidth(90)
        self.spin_again_count.setToolTip(again_tooltip)
        again_row.addWidget(self.spin_again_count)
        again_row.addStretch()
        gv_layout.addLayout(again_row)

        self.frame_flag_review_leech = QFrame()
        self.frame_flag_review_leech.setToolTip(again_tooltip)
        row_review_leech_layout = QHBoxLayout(self.frame_flag_review_leech)
        row_review_leech_layout.setContentsMargins(12, 4, 10, 4)
        row_review_leech_layout.setSpacing(8)

        lbl_review_leech = make_selectable_label("Couleur des cartes <b>leech</b> :")
        lbl_review_leech.setMinimumWidth(160)
        self.combo_flag_review_leech = NoWheelComboBox()
        self.combo_flag_review_leech.setToolTip(again_tooltip)
        for flag_id, label in ANKI_FLAGS:
            info = FLAG_COLORS.get(flag_id, {"hex": "#DD6B20"})
            icon = create_color_icon(info["hex"])
            self.combo_flag_review_leech.addItem(icon, f" {label}", flag_id)
        current_rev_leech = self.prefs.get("virtual_flag_color_leech", "flag2")
        idx_rev_leech = self.combo_flag_review_leech.findData(current_rev_leech)
        if idx_rev_leech >= 0:
            self.combo_flag_review_leech.setCurrentIndex(idx_rev_leech)

        row_review_leech_layout.addWidget(lbl_review_leech)
        row_review_leech_layout.addStretch()
        row_review_leech_layout.addWidget(self.combo_flag_review_leech)
        gv_layout.addWidget(self.frame_flag_review_leech)

        def _update_again_state():
            is_active = self.cb_again_flag.isChecked()
            self.spin_again_count.setEnabled(is_active)
            self.frame_flag_review_leech.setEnabled(is_active)
            self._update_flag_row_style(self.combo_flag_review_leech, self.frame_flag_review_leech)

        self.cb_again_flag.stateChanged.connect(lambda s: _update_again_state())
        self.combo_flag_review_leech.currentIndexChanged.connect(
            lambda idx: self._update_flag_row_style(self.combo_flag_review_leech, self.frame_flag_review_leech)
        )
        _update_again_state()

        layout.addWidget(group_visual)

        # ── Group 2 : Multi-appareils info ──
        sentinel_info = make_selectable_label(
            f"<span style='font-size: 11.5px; color: {T['notice_text']};'><b>Synchronisation multi-appareils :</b> Les préférences "
            f"sont enregistrées localement et synchronisées automatiquement avec vos appareils mobiles via <code>_edn_prefs.js</code> et note sentinelle.</span>"
        )
        sentinel_info.setStyleSheet(
            f"background: {T['notice_bg']}; border: 1px solid {T['notice_border']}; border-radius: 6px; "
            f"padding: 8px 12px; color: {T['notice_text']};"
        )
        layout.addWidget(sentinel_info)

        layout.addStretch()
        scroll.setWidget(container)
        outer_layout.addWidget(scroll, 1)

        return widget

    # ─────────────────────────────────────────────
    #  Tab 3 : Lier les cartes (Conditionnel et Responsive)
    # ─────────────────────────────────────────────

    def _build_linked_cards_tab(self) -> QWidget:
        T = self.T
        widget = QWidget()
        outer_layout = QVBoxLayout(widget)
        outer_layout.setContentsMargins(14, 14, 14, 14)
        outer_layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)

        # ── Groupe 1 : Liaison & Automatisation ──
        group_auto = QFrame()
        group_auto.setStyleSheet(f"""
            QFrame {{
                background-color: {T["bg_card"]};
                border: 1px solid {T["border_card"]};
                border-radius: 8px;
            }}
        """)
        ga_layout = QVBoxLayout(group_auto)
        ga_layout.setContentsMargins(14, 12, 14, 12)
        ga_layout.setSpacing(8)

        ga_layout.addWidget(make_selectable_label(f"<b style='color: {T['text_main']};'>Liaison et Automatisation dans l'Éditeur</b>"))

        self.cb_mirror_on_paste = QCheckBox("Proposer le lien miroir au collage de NID")
        self.cb_mirror_on_paste.setChecked(self.linked_cards_cfg.get("mirror_link_on_paste", True))
        self.cb_mirror_on_paste.setToolTip("Propose automatiquement de créer le lien retour réciproque (A ↔ B) dans l'éditeur après avoir collé un identifiant de note (NID).")
        ga_layout.addWidget(self.cb_mirror_on_paste)

        self.cb_desc_intelligent = QCheckBox("Description contextuelle intelligente")
        self.cb_desc_intelligent.setChecked(self.linked_cards_cfg.get("description_intelligente", True))
        self.cb_desc_intelligent.setToolTip("Génère un intitulé court pertinent pour les liens (ex: Signes, Traitements, Étiologies) au lieu d'insérer le texte brut complet du recto.")
        ga_layout.addWidget(self.cb_desc_intelligent)

        self.cb_suggestions = QCheckBox("Suggestions automatiques de liens")
        self.cb_suggestions.setChecked(self.linked_cards_cfg.get("suggestions_de_liens", True))
        self.cb_suggestions.setToolTip("Affiche en direct des suggestions de cartes associées pendant la saisie dans l'éditeur pour accélérer la création de liens.")
        ga_layout.addWidget(self.cb_suggestions)

        layout.addWidget(group_auto)

        # ── Groupe 2 : Recherche & Déclencheur ──
        group_trigger = QFrame()
        group_trigger.setStyleSheet(f"""
            QFrame {{
                background-color: {T["bg_card"]};
                border: 1px solid {T["border_card"]};
                border-radius: 8px;
            }}
        """)
        gt_layout = QVBoxLayout(group_trigger)
        gt_layout.setContentsMargins(14, 12, 14, 12)
        gt_layout.setSpacing(8)

        gt_layout.addWidget(make_selectable_label(f"<b style='color: {T['text_main']};'>Recherche Rapide & Fenêtre de Liaison</b>"))

        trigger_row = QHBoxLayout()
        trigger_row.addWidget(make_selectable_label(f"<b style='color: {T['text_main']};'>Mot-clé déclencheur dans l'éditeur :</b>"))
        self.edit_trigger = QLineEdit(self.linked_cards_cfg.get("gui_trigger", "nid:"))
        self.edit_trigger.setMaximumWidth(100)
        self.edit_trigger.setStyleSheet(f"""
            QLineEdit {{
                background: {T["bg_input"]};
                color: {T["text_main"]};
                border: 1px solid {T["border_input"]};
                border-radius: 4px;
                padding: 3px 8px;
                font-family: monospace;
                font-weight: bold;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: {T["border_input_hover"]};
            }}
        """)
        self.edit_trigger.setToolTip("Texte saisi dans un champ de carte qui ouvre instantanément la boîte de recherche et de liaison (par défaut : nid:).")
        trigger_row.addWidget(self.edit_trigger)
        trigger_row.addStretch()
        gt_layout.addLayout(trigger_row)

        self.cb_gui_mirror = QCheckBox("Option 'Lien miroir' cochée par défaut dans la fenêtre")
        self.cb_gui_mirror.setChecked(self.linked_cards_cfg.get("gui_mirror_cb_state", True))
        self.cb_gui_mirror.setToolTip("Active par défaut la case 'Lien miroir' lors de l'ouverture de la boîte de recherche pour créer automatiquement le lien bidirectionnel.")
        gt_layout.addWidget(self.cb_gui_mirror)

        self.cb_gui_recto_only = QCheckBox("Rechercher uniquement dans le Recto par défaut")
        self.cb_gui_recto_only.setChecked(self.linked_cards_cfg.get("gui_recto_only_cb_state", False))
        self.cb_gui_recto_only.setToolTip("Limite la recherche de cartes cibles uniquement au champ Recto par défaut, ce qui accélère la sélection.")
        gt_layout.addWidget(self.cb_gui_recto_only)

        layout.addWidget(group_trigger)

        # ── Groupe 3 : Prévisualisation & Drapeaux Virtuels dans la Miniature ──
        group_preview = QFrame()
        group_preview.setStyleSheet(f"""
            QFrame {{
                background-color: {T["bg_card"]};
                border: 1px solid {T["border_card"]};
                border-radius: 8px;
            }}
        """)
        gp_layout = QVBoxLayout(group_preview)
        gp_layout.setContentsMargins(14, 12, 14, 12)
        gp_layout.setSpacing(8)

        gp_layout.addWidget(make_selectable_label(f"<b style='color: {T['text_main']};'>Prévisualisation au Survol (Miniature de Profil)</b>"))

        self.cb_virtual_flag_preview = QCheckBox("Drapeau virtuel pour les cartes sans drapeau dans la miniature")
        self.cb_virtual_flag_preview.setChecked(self.prefs.get("virtualBlueFlagForNewCards", False))
        self.cb_virtual_flag_preview.setToolTip("Définit la couleur de bordure affichée dans l'aperçu miniature au survol pour identifier visuellement les cartes New ou Suspendues sans drapeau réel.")
        gp_layout.addWidget(self.cb_virtual_flag_preview)

        self.flag_colors_widget = QWidget()
        self.flag_colors_widget.setStyleSheet("background: transparent; border: none;")
        flag_colors_layout = QVBoxLayout(self.flag_colors_widget)
        flag_colors_layout.setContentsMargins(16, 0, 4, 0)
        flag_colors_layout.setSpacing(6)

        # Row 1: Cartes New
        self.frame_flag_new = QFrame()
        row_new_layout = QHBoxLayout(self.frame_flag_new)
        row_new_layout.setContentsMargins(10, 4, 10, 4)
        row_new_layout.setSpacing(8)

        lbl_new = make_selectable_label(f"<span style='color: {T['text_main']};'>Cartes <b>New</b> :</span>")
        lbl_new.setMinimumWidth(140)
        self.combo_flag_new = NoWheelComboBox()
        for flag_id, label in ANKI_FLAGS:
            info = FLAG_COLORS.get(flag_id, {"hex": "#3182CE"})
            icon = create_color_icon(info["hex"])
            self.combo_flag_new.addItem(icon, f" {label}", flag_id)
        current_new = self.prefs.get("virtual_flag_color_new", "flag4")
        idx_new = self.combo_flag_new.findData(current_new)
        if idx_new >= 0:
            self.combo_flag_new.setCurrentIndex(idx_new)

        row_new_layout.addWidget(lbl_new)
        row_new_layout.addStretch()
        row_new_layout.addWidget(self.combo_flag_new)
        flag_colors_layout.addWidget(self.frame_flag_new)

        # Row 2: Cartes Suspendues
        self.frame_flag_sus = QFrame()
        row_sus_layout = QHBoxLayout(self.frame_flag_sus)
        row_sus_layout.setContentsMargins(10, 4, 10, 4)
        row_sus_layout.setSpacing(8)

        lbl_sus = make_selectable_label(f"<span style='color: {T['text_main']};'>Cartes <b>Suspendues</b> :</span>")
        lbl_sus.setMinimumWidth(140)
        self.combo_flag_sus = NoWheelComboBox()
        for flag_id, label in ANKI_FLAGS:
            info = FLAG_COLORS.get(flag_id, {"hex": "#3182CE"})
            icon = create_color_icon(info["hex"])
            self.combo_flag_sus.addItem(icon, f" {label}", flag_id)
        current_sus = self.prefs.get("virtual_flag_color_suspended", "flag1")
        idx_sus = self.combo_flag_sus.findData(current_sus)
        if idx_sus >= 0:
            self.combo_flag_sus.setCurrentIndex(idx_sus)

        row_sus_layout.addWidget(lbl_sus)
        row_sus_layout.addStretch()
        row_sus_layout.addWidget(self.combo_flag_sus)
        flag_colors_layout.addWidget(self.frame_flag_sus)

        gp_layout.addWidget(self.flag_colors_widget)

        self.cb_preview_dual_flags = QCheckBox("Afficher à la fois le drapeau virtuel (50% gauche) et le drapeau réel (50% droit)")
        self.cb_preview_dual_flags.setChecked(self.prefs.get("previewDualFlags", False))
        self.cb_preview_dual_flags.setToolTip(
            "Pour les cartes qui ont un drapeau réel ET un statut virtuel (nouvelle ou suspendue),\n"
            "divise l'encadrement de l'aperçu en deux couleurs : drapeau virtuel à gauche (50%) et drapeau réel à droite (50%)."
        )
        gp_layout.addWidget(self.cb_preview_dual_flags)

        self.cb_virtual_flag_preview.stateChanged.connect(lambda s: self._update_virtual_flags_state())
        self.combo_flag_new.currentIndexChanged.connect(
            lambda idx: self._update_flag_row_style(self.combo_flag_new, self.frame_flag_new)
        )
        self.combo_flag_sus.currentIndexChanged.connect(
            lambda idx: self._update_flag_row_style(self.combo_flag_sus, self.frame_flag_sus)
        )
        self._update_flag_row_style(self.combo_flag_new, self.frame_flag_new)
        self._update_flag_row_style(self.combo_flag_sus, self.frame_flag_sus)
        self._update_virtual_flags_state()

        layout.addWidget(group_preview)

        # ── Groupe 4 : Sections masquées dans la miniature au survol ──
        group_hidden = QFrame()
        group_hidden.setStyleSheet(f"""
            QFrame {{
                background-color: {T["bg_card"]};
                border: 1px solid {T["border_card"]};
                border-radius: 8px;
            }}
        """)
        gh_layout = QVBoxLayout(group_hidden)
        gh_layout.setContentsMargins(14, 12, 14, 12)
        gh_layout.setSpacing(8)

        gh_layout.addWidget(make_selectable_label(f"<b style='color: {T['text_main']};'>Éléments à Masquer dans la Miniature au Survol</b>"))

        hidden_sections = self.linked_cards_cfg.get("hidden_preview_sections", [
            "sourcesMegaContainer",
            "commentsMegaContainer",
            "tagsMegaContainer"
        ])

        target_fields = [
            ("sourcesMegaContainer", "Masquer les Sources"),
            ("commentsMegaContainer", "Masquer les Commentaires"),
            ("tagsMegaContainer", "Masquer les Tags"),
            ("cartesLieesMegaContainer", "Masquer les Cartes Liées"),
            ("erreursFaitesMegaContainer", "Masquer les Erreurs Précédentes"),
            ("mnemonicsMegaContainer", "Masquer les Mnémotechniques"),
            ("infoSupplementairesMegaContainer", "Masquer les Mots-clefs / Informations"),
        ]

        self.preview_section_cbs = {}
        for box_id, box_label in target_fields:
            cb = QCheckBox(box_label)
            cb.setChecked(box_id in hidden_sections)
            cb.setToolTip(f"Masque la section '{box_label.replace('Masquer les ', '')}' dans la miniature d'aperçu au survol.")
            gh_layout.addWidget(cb)
            self.preview_section_cbs[box_id] = cb

        layout.addWidget(group_hidden)

        layout.addStretch()
        scroll.setWidget(container)
        outer_layout.addWidget(scroll, 1)

        return widget

    def _is_linked_cards_active(self) -> bool:
        """Vérifie si le module 'Lier les cartes' est installé et activé."""
        if "linked_cards" in self.module_checkboxes:
            return self.module_checkboxes["linked_cards"].isChecked()
        status, _ = self._get_addon_status({
            "id": "445658251",
            "alt_ids": ["Anki_EDN_Lier_les_cartes", "445658251"],
            "module_id": "linked_cards"
        })
        return status == "installed_active"

    def _update_linked_cards_tab_visibility(self):
        """Affiche ou masque l'onglet 'Lier les cartes' selon son statut actif."""
        is_active = self._is_linked_cards_active()
        current_index = self.tab_widget.indexOf(self.linked_cards_tab)

        if is_active and current_index == -1:
            self.tab_widget.addTab(self.linked_cards_tab, "Lier les cartes")
        elif not is_active and current_index != -1:
            self.tab_widget.removeTab(current_index)

    def _choose_rank_color(self, rank_letter: str):
        current_color = getattr(self, f"rank_color_{rank_letter.lower()}")
        color = QColorDialog.getColor(QColor(current_color), self, f"Choisir la couleur pour le Rang {rank_letter}")
        if color.isValid():
            hex_color = color.name()
            setattr(self, f"rank_color_{rank_letter.lower()}", hex_color)
            self._update_rank_row_style(rank_letter, hex_color)

    def _reset_rank_color(self, rank_letter: str, default_hex: str):
        setattr(self, f"rank_color_{rank_letter.lower()}", default_hex)
        self._update_rank_row_style(rank_letter, default_hex)

    def _update_rank_row_style(self, rank_letter: str, hex_color: str):
        T = self.T
        frame = getattr(self, f"frame_rank_{rank_letter.lower()}", None)
        btn = getattr(self, f"btn_rank_{rank_letter.lower()}", None)
        if frame:
            frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {T['bg_input']};
                    border: 1px solid {T['border_input']};
                    border-left: 5px solid {hex_color};
                    border-radius: 6px;
                }}
            """)
        if btn:
            btn.setText(f"  {hex_color.upper()}  ")
            text_color = "#ffffff" if is_dark_color(hex_color) else "#0f172a"
            border_btn = "#64748b" if T["dark"] else "#94a3b8"
            hover_border = "#94a3b8" if T["dark"] else "#475569"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {hex_color};
                    color: {text_color};
                    border: 1px solid {border_btn};
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-weight: bold;
                    font-family: monospace;
                    font-size: 11.5px;
                }}
                QPushButton:hover {{
                    border: 1px solid {hover_border};
                }}
            """)

    def _update_rank_state(self):
        rank_on = self.cb_rank.isChecked()
        self.rank_colors_widget.setEnabled(rank_on)

    def _update_flag_row_style(self, combo: QComboBox, frame: QFrame):
        T = self.T
        flag_id = combo.currentData()
        info = FLAG_COLORS.get(flag_id, {"hex": "#3182CE", "border": "#93C5FD"})
        hex_color = info["hex"]
        border_color = (info.get("hex", "#3182CE") + "66") if T["dark"] else info.get("border", hex_color)
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {T['bg_input']};
                border: 1px solid {border_color};
                border-left: 6px solid {hex_color};
                border-radius: 6px;
            }}
        """)
        combo.setStyleSheet(f"""
            QComboBox {{
                border: 1.5px solid {hex_color};
                border-radius: 5px;
                padding: 4px 10px;
                background: {T['bg_input']};
                color: {T['text_main']};
                font-weight: 600;
                min-height: 24px;
                min-width: 150px;
            }}
            QComboBox:hover {{
                border: 1.5px solid {hex_color};
                background: {T['bg_card_hover']};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {T['bg_card']};
                color: {T['text_main']};
                selection-background-color: {T['accent']};
                selection-color: #ffffff;
                border: 1px solid {T['border_card']};
            }}
        """)

    def _update_virtual_flags_state(self):
        vf_on = self.cb_virtual_flag_preview.isChecked()
        self.flag_colors_widget.setEnabled(vf_on)
        self.cb_preview_dual_flags.setEnabled(vf_on)

    def reset_to_defaults(self):
        """Rétablit tous les réglages et couleurs par défaut."""
        from aqt.utils import askConfirmation, tooltip
        if not askConfirmation("Voulez-vous rétablir toutes les options et couleurs par défaut ?"):
            return

        # 1. Rétablir les réglages cartes
        self.cb_rank.setChecked(DEFAULT_PREFS["showRankBadge"])
        self.rank_color_a = DEFAULT_PREFS["rank_color_a"]
        self.rank_color_b = DEFAULT_PREFS["rank_color_b"]
        self.rank_color_c = DEFAULT_PREFS["rank_color_c"]
        self._update_rank_row_style("A", self.rank_color_a)
        self._update_rank_row_style("B", self.rank_color_b)
        self._update_rank_row_style("C", self.rank_color_c)
        self._update_rank_state()

        self.cb_icons.setChecked(DEFAULT_PREFS["showSpecialtyIcon"])
        self.cb_icons_mobile.setChecked(DEFAULT_PREFS["showSpecialtyIconMobile"])
        self.cb_flags.setChecked(DEFAULT_PREFS["showFlagBorders"])
        self.cb_virtual_new_pc.setChecked(DEFAULT_PREFS["virtualBlueFlagForNewCards"])
        self.cb_virtual_new_mobile.setChecked(DEFAULT_PREFS["virtualBlueFlagForNewCardsMobile"])
        idx_rev_new = self.combo_flag_review_new.findData(DEFAULT_PREFS["virtual_flag_color_new"])
        if idx_rev_new >= 0:
            self.combo_flag_review_new.setCurrentIndex(idx_rev_new)
        self._update_flag_row_style(self.combo_flag_review_new, self.frame_flag_review_new)
        self.cb_dual_flags_review.setChecked(DEFAULT_PREFS.get("reviewDualFlags", False))

        self.cb_again_flag.setChecked(DEFAULT_PREFS["flagAgainEnabled"])
        self.spin_again_count.setValue(DEFAULT_PREFS["flagAgainCount"])
        idx_rev_leech = self.combo_flag_review_leech.findData(DEFAULT_PREFS.get("virtual_flag_color_leech", "flag2"))
        if idx_rev_leech >= 0:
            self.combo_flag_review_leech.setCurrentIndex(idx_rev_leech)
        self._update_flag_row_style(self.combo_flag_review_leech, self.frame_flag_review_leech)

        # 2. Rétablir Lier les cartes
        self.cb_mirror_on_paste.setChecked(DEFAULT_LINKED_CARDS_CONFIG["mirror_link_on_paste"])
        self.cb_desc_intelligent.setChecked(DEFAULT_LINKED_CARDS_CONFIG["description_intelligente"])
        self.cb_suggestions.setChecked(DEFAULT_LINKED_CARDS_CONFIG["suggestions_de_liens"])
        self.edit_trigger.setText(DEFAULT_LINKED_CARDS_CONFIG["gui_trigger"])
        self.cb_gui_mirror.setChecked(DEFAULT_LINKED_CARDS_CONFIG["gui_mirror_cb_state"])
        self.cb_gui_recto_only.setChecked(DEFAULT_LINKED_CARDS_CONFIG["gui_recto_only_cb_state"])

        self.cb_virtual_flag_preview.setChecked(DEFAULT_PREFS["virtualBlueFlagForNewCards"])
        idx_new = self.combo_flag_new.findData(DEFAULT_PREFS["virtual_flag_color_new"])
        if idx_new >= 0:
            self.combo_flag_new.setCurrentIndex(idx_new)
        idx_sus = self.combo_flag_sus.findData(DEFAULT_PREFS["virtual_flag_color_suspended"])
        if idx_sus >= 0:
            self.combo_flag_sus.setCurrentIndex(idx_sus)

        self._update_flag_row_style(self.combo_flag_new, self.frame_flag_new)
        self._update_flag_row_style(self.combo_flag_sus, self.frame_flag_sus)
        self.cb_preview_dual_flags.setChecked(DEFAULT_PREFS.get("previewDualFlags", False))
        self._update_virtual_flags_state()

        default_hiddens = set(DEFAULT_LINKED_CARDS_CONFIG["hidden_preview_sections"])
        for box_id, cb in self.preview_section_cbs.items():
            cb.setChecked(box_id in default_hiddens)

        tooltip("Valeurs par défaut rétablies. Cliquez sur Enregistrer pour appliquer.")

    def save_and_apply(self):
        """Applique les installations, réactivations, et sauvegarde les préférences."""
        mgr = getattr(mw, "addonManager", None) if mw else None

        addons_to_install = []
        addons_to_enable = []
        restart_needed = False

        for row in self.addon_rows:
            item = row["item"]
            cb = row["checkbox"]
            status = row["status"]
            matched_id = row["matched_id"]

            if cb.isChecked():
                if status == "not_installed":
                    aid = item.get("id")
                    if aid and aid.isdigit() and mgr and hasattr(mgr, "install"):
                        addons_to_install.append((aid, item["name"]))
                elif status == "installed_disabled":
                    if mgr and hasattr(mgr, "toggleEnabled") and matched_id:
                        addons_to_enable.append(matched_id)
            else:
                if status == "installed_active" and matched_id:
                    mod_id = item.get("module_id")
                    if mod_id:
                        set_module_enabled(mod_id, False)
                        restart_needed = True

            mod_id = item.get("module_id")
            if mod_id:
                set_module_enabled(mod_id, cb.isChecked())

        for aid in addons_to_enable:
            try:
                mgr.toggleEnabled(aid, enable=True)
                restart_needed = True
            except Exception:
                pass

        installed_count = 0
        if addons_to_install and mgr:
            if hasattr(mw, "progress"):
                mw.progress.start(immediate=True, label="Installation des add-ons...")
            for aid, aname in addons_to_install:
                try:
                    res = mgr.install(mw, aid)
                    if res:
                        installed_count += 1
                        restart_needed = True
                except Exception as e:
                    print(f"[EDN Settings] Erreur installation {aname} ({aid}): {e}")
            if hasattr(mw, "progress"):
                mw.progress.finish()

        # Sauvegarde Réglages Cartes
        self.prefs["showRankBadge"] = self.cb_rank.isChecked()
        self.prefs["rank_color_a"] = self.rank_color_a
        self.prefs["rank_color_b"] = self.rank_color_b
        self.prefs["rank_color_c"] = self.rank_color_c
        self.prefs["showSpecialtyIcon"] = self.cb_icons.isChecked()
        self.prefs["showSpecialtyIconMobile"] = self.cb_icons_mobile.isChecked()
        self.prefs["showFlagBorders"] = self.cb_flags.isChecked()
        self.prefs["virtualBlueFlagForNewCards"] = self.cb_virtual_new_pc.isChecked()
        self.prefs["virtualBlueFlagForNewCardsMobile"] = self.cb_virtual_new_mobile.isChecked()
        self.prefs["virtual_flag_color_new"] = self.combo_flag_review_new.currentData()
        self.prefs["reviewDualFlags"] = self.cb_dual_flags_review.isChecked()
        self.prefs["flagAgainEnabled"] = self.cb_again_flag.isChecked()
        self.prefs["flagAgainCount"] = self.spin_again_count.value()
        self.prefs["virtual_flag_color_leech"] = self.combo_flag_review_leech.currentData()
        self.prefs["virtual_flag_color_suspended"] = self.combo_flag_sus.currentData()
        self.prefs["previewDualFlags"] = self.cb_preview_dual_flags.isChecked()

        save_prefs(self.prefs)
        _push_prefs_to_webview()

        # Sauvegarde Lier les cartes
        hiddens = [box_id for box_id, cb in self.preview_section_cbs.items() if cb.isChecked()]
        linked_updates = {
            "mirror_link_on_paste": self.cb_mirror_on_paste.isChecked(),
            "description_intelligente": self.cb_desc_intelligent.isChecked(),
            "suggestions_de_liens": self.cb_suggestions.isChecked(),
            "gui_trigger": self.edit_trigger.text().strip() or "nid:",
            "gui_mirror_cb_state": self.cb_gui_mirror.isChecked(),
            "gui_recto_only_cb_state": self.cb_gui_recto_only.isChecked(),
            "hidden_preview_sections": hiddens
        }
        self._save_linked_cards_config(linked_updates)

        self.accept()

        msg = "Préférences Anki EDN enregistrées !"
        if installed_count > 0:
            msg += f"\n\n{installed_count} add-on(s) installé(s) avec succès."
        if addons_to_enable:
            msg += f"\n\n{len(addons_to_enable)} add-on(s) réactivé(s)."
        if restart_needed or installed_count > 0 or addons_to_enable:
            msg += "\n\nVeuillez redémarrer Anki pour finaliser l'application."

        from aqt.utils import showInfo
        showInfo(msg, title="Paramètres Anki EDN")

    def open_shortcuts_dialog(self):
        """Ouvre la configuration des raccourcis."""
        try:
            from .shortcuts_dialog import ShortcutsDialog
        except ImportError:
            from shortcuts_dialog import ShortcutsDialog
        dialog = ShortcutsDialog(self)
        dialog.exec()

