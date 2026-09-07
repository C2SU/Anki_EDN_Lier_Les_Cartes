"""
Shortcuts Configuration Dialog for Anki EDN
"""
from aqt.qt import *
from aqt import mw
from .shared_menu import get_registered_modules, get_shortcut, set_shortcut
from .key_sequence_widget import KeySequenceEdit
import os


def is_dark_mode() -> bool:
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
    return False


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration des Raccourcis - Anki EDN")
        self.setMinimumWidth(700)
        self.setMinimumHeight(420)
        self.dark = is_dark_mode()
        self.shortcut_inputs = {}
        self.setup_ui()
    
    def setup_ui(self):
        dark = self.dark
        bg_dialog = "#1e2227" if dark else "#f8fafc"
        bg_card = "#21252b" if dark else "#f8fafc"
        border_card = "#373b44" if dark else "#e2e8f0"
        text_main = "#f1f5f9" if dark else "#0f172a"
        text_muted = "#94a3b8" if dark else "#64748b"
        accent = "#38bdf8" if dark else "#0284c7"
        accent_hover = "#0284c7" if dark else "#0369a1"
        btn_default_bg = "#2c313a" if dark else "#f1f5f9"
        btn_default_hover = "#3e4451" if dark else "#e2e8f0"
        btn_default_text = "#e2e8f0" if dark else "#334155"
        btn_default_border = "#4b5563" if dark else "#cbd5e1"
        sep_color = "#373b44" if dark else "#e2e8f0"
        scrollbar_thumb = "#4b5563" if dark else "#cbd5e1"
        scrollbar_thumb_hover = "#6b7280" if dark else "#94a3b8"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg_dialog};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {text_main};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {scrollbar_thumb};
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {scrollbar_thumb_hover};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QLabel("<span style='font-size: 16px; font-weight: bold;'>Raccourcis Clavier</span>")
        layout.addWidget(header)
        
        desc = QLabel(
            "Cliquez dans le champ et pressez votre combinaison de touches. "
            "Validez avec Entrée, annulez avec Échap."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {text_muted}; margin-bottom: 4px;")
        layout.addWidget(desc)
        
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"border: none; border-top: 1px solid {sep_color}; max-height: 1px;")
        layout.addWidget(sep1)
        
        # Scroll area for shortcuts
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        shortcuts_widget = QWidget()
        shortcuts_widget.setStyleSheet("background: transparent;")
        shortcuts_layout = QVBoxLayout(shortcuts_widget)
        shortcuts_layout.setContentsMargins(0, 4, 4, 4)
        shortcuts_layout.setSpacing(8)
        
        modules = get_registered_modules()
        
        if not modules:
            no_shortcuts = QLabel("Aucun module avec raccourci détecté.")
            no_shortcuts.setStyleSheet(f"color: {text_muted}; font-style: italic; padding: 20px 0;")
            shortcuts_layout.addWidget(no_shortcuts)
        else:
            for module_id, module_info in modules.items():
                actions = module_info.get('actions', [])
                for action_info in actions:
                    shortcut = action_info.get('shortcut')
                    if not shortcut:
                        continue
                    
                    row = QFrame()
                    row.setStyleSheet(f"""
                        QFrame {{
                            background-color: {bg_card};
                            border: 1px solid {border_card};
                            border-radius: 6px;
                        }}
                    """)
                    row_layout = QHBoxLayout(row)
                    row_layout.setContentsMargins(12, 8, 12, 8)
                    row_layout.setSpacing(10)
                    
                    label_text = f"<b>{module_info['name']}</b> &nbsp;—&nbsp; {action_info['label']}"
                    label = QLabel(label_text)
                    label.setMinimumWidth(220)
                    row_layout.addWidget(label)
                    
                    shortcut_input = KeySequenceEdit()
                    action_key = action_info.get('shortcut_key') or f"{module_id}_{action_info['label']}"
                    current = get_shortcut(action_key, shortcut)
                    shortcut_input.set_shortcut(current)
                    shortcut_input.setMinimumWidth(220)
                    row_layout.addWidget(shortcut_input)
                    
                    self.shortcut_inputs[action_key] = {
                        'widget': shortcut_input,
                        'default': shortcut
                    }
                    
                    reset_btn = QPushButton("↺ Défaut")
                    reset_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {btn_default_bg};
                            color: {btn_default_text};
                            border: 1px solid {btn_default_border};
                            border-radius: 4px;
                            padding: 4px 10px;
                            font-size: 11.5px;
                            font-weight: 600;
                        }}
                        QPushButton:hover {{
                            background-color: {btn_default_hover};
                            color: {text_main};
                        }}
                    """)
                    reset_btn.clicked.connect(
                        lambda checked=False, ak=action_key: self.reset_shortcut(ak)
                    )
                    row_layout.addWidget(reset_btn)
                    
                    shortcuts_layout.addWidget(row)
        
        shortcuts_layout.addStretch()
        scroll.setWidget(shortcuts_widget)
        layout.addWidget(scroll, 1)
        
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"border: none; border-top: 1px solid {sep_color}; max-height: 1px;")
        layout.addWidget(sep2)
        
        # Info
        info_color = "#f87171" if dark else "#dc2626"
        info = QLabel(
            "<small>Les changements nécessitent un redémarrage d'Anki pour prendre effet.</small>"
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {info_color}; font-weight: 600;")
        layout.addWidget(info)
        
        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Annuler")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_default_bg};
                color: {btn_default_text};
                border: 1px solid {btn_default_border};
                padding: 8px 18px;
                border-radius: 6px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {btn_default_hover};
                color: {text_main};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("Enregistrer")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: white;
                padding: 8px 20px;
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {accent_hover};
            }}
        """)
        save_btn.clicked.connect(self.save_shortcuts)
        button_layout.addWidget(save_btn)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def reset_shortcut(self, module_id):
        """Reset shortcut to default"""
        if module_id in self.shortcut_inputs:
            default = self.shortcut_inputs[module_id]['default']
            self.shortcut_inputs[module_id]['widget'].set_shortcut(default)
    
    def save_shortcuts(self):
        """Save all shortcuts"""
        for module_id, data in self.shortcut_inputs.items():
            widget = data['widget']
            new_shortcut = widget.display.text().strip()
            if new_shortcut:
                set_shortcut(module_id, new_shortcut)
        
        self.accept()
        
        from aqt.utils import showInfo
        showInfo(
            "Raccourcis sauvegardés !\n\n"
            "Veuillez redémarrer Anki pour que les changements prennent effet.",
            title="Redémarrage requis"
        )

