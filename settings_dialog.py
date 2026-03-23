"""
Anki EDN Settings Dialog
GUI for managing modules and shortcuts.
"""
from aqt.qt import *
from aqt.utils import showInfo, tooltip
from typing import Dict

class EDNSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paramètres Anki EDN")
        self.setMinimumSize(500, 400)
        self.pending_changes = {}
        self.setup_ui()
        self.load_settings()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Title
        title = QLabel("Paramètres Anki EDN")
        title.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(title)
        
        # Tab widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        
        # Modules tab
        modules_scroll = QScrollArea()
        modules_scroll.setWidgetResizable(True)
        modules_scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.modules_widget = QWidget()
        self.modules_layout = QVBoxLayout()
        self.modules_widget.setLayout(self.modules_layout)
        
        modules_scroll.setWidget(self.modules_widget)
        self.tabs.addTab(modules_scroll, "📦 Modules")
        
        # Shortcuts tab
        shortcuts_scroll = QScrollArea()
        shortcuts_scroll.setWidgetResizable(True)
        shortcuts_scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.shortcuts_widget = QWidget()
        self.shortcuts_layout = QVBoxLayout()
        self.shortcuts_widget.setLayout(self.shortcuts_layout)
        
        shortcuts_scroll.setWidget(self.shortcuts_widget)
        self.tabs.addTab(shortcuts_scroll, "⌨️ Raccourcis")
        
        # Status bar for pending changes
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #2ecc71; font-weight: bold; padding: 5px;")
        layout.addWidget(self.status_label)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        self.apply_btn = QPushButton("✅ Appliquer")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.apply_changes)
        self.apply_btn.setStyleSheet("""
            QPushButton:enabled { background-color: #27ae60; color: white; font-weight: bold; }
            QPushButton:disabled { background-color: #95a5a6; }
        """)
        
        cancel_btn = QPushButton("Fermer")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
    
    def load_settings(self):
        from .shared_menu import get_registered_modules, is_module_enabled, get_shortcut
        
        modules = get_registered_modules()
        
        # Clear existing
        self.clear_layout(self.modules_layout)
        self.clear_layout(self.shortcuts_layout)
        
        self.module_toggles = {}
        self.shortcut_edits = {}
        
        if not modules:
            self.modules_layout.addWidget(QLabel("Aucun module enregistré."))
            return
        
        # Add module toggles
        for module_id, info in modules.items():
            # Module toggle
            # Module toggle
            group = QGroupBox(info["name"])
            group.setStyleSheet("QGroupBox { font-weight: bold; margin-top: 10px; }")
            group_layout = QVBoxLayout()
            group_layout.setContentsMargins(10, 20, 10, 10) # Top margin for title
            group.setLayout(group_layout)
            
            if info.get("description"):
                desc = QLabel(info["description"])
                desc.setWordWrap(True)
                desc.setStyleSheet("color: #95a5a6; font-style: italic; font-weight: normal;")
                group_layout.addWidget(desc)
            
            toggle = QCheckBox("Activer ce module")
            toggle.setChecked(is_module_enabled(module_id))
            toggle.stateChanged.connect(lambda state, mid=module_id: self.on_module_toggle(mid, state))
            group_layout.addWidget(toggle)
            
            self.module_toggles[module_id] = toggle
            self.modules_layout.addWidget(group)
            
            # Shortcuts for this module
            for action_info in info.get("actions", []):
                if action_info.get("shortcut"):
                    shortcut_layout = QHBoxLayout()
                    label = QLabel(f"{info['name']} - {action_info['label']}:")
                    edit = QLineEdit(get_shortcut(module_id, action_info["shortcut"]))
                    edit.setMaximumWidth(150)
                    edit.textChanged.connect(lambda text, mid=module_id: self.on_shortcut_change(mid, text))
                    
                    shortcut_layout.addWidget(label)
                    shortcut_layout.addWidget(edit)
                    shortcut_layout.addStretch()
                    
                    self.shortcuts_layout.addLayout(shortcut_layout)
                    self.shortcut_edits[module_id] = edit
        
        self.modules_layout.addStretch()
        self.shortcuts_layout.addStretch()
        
        self.custom_widgets = []
        for module_id, info in modules.items():
            if info.get("config_widget"):
                try:
                    from aqt import mw
                    # Instancie le widget et l'attache à son parent `tabs`
                    widget = info["config_widget"](mw)
                    
                    scroll = QScrollArea()
                    scroll.setWidgetResizable(True)
                    scroll.setFrameShape(QFrame.Shape.NoFrame)
                    scroll.setWidget(widget)
                    
                    self.tabs.addTab(scroll, f"⚙ {info['name']}")
                    self.custom_widgets.append(widget)
                except Exception as e:
                    print(f"Erreur chargement widget interactif pour {module_id} : {e}")
    
    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self.clear_layout(item.layout())
    
    def on_module_toggle(self, module_id: str, state: int):
        self.pending_changes[f"module_{module_id}"] = (module_id, state == Qt.CheckState.Checked.value)
        self.update_status()
    
    def on_shortcut_change(self, module_id: str, shortcut: str):
        self.pending_changes[f"shortcut_{module_id}"] = (module_id, shortcut)
        self.update_status()
    
    def update_status(self):
        if self.pending_changes:
            count = len(self.pending_changes)
            self.status_label.setText(f"⚠️ {count} modification(s) en attente")
            self.apply_btn.setEnabled(True)
        else:
            self.status_label.setText("")
            self.apply_btn.setEnabled(False)
    
    def apply_changes(self):
        from .shared_menu import set_module_enabled, set_shortcut
        
        for key, value in self.pending_changes.items():
            if key.startswith("module_"):
                module_id, enabled = value
                set_module_enabled(module_id, enabled)
            elif key.startswith("shortcut_"):
                module_id, shortcut = value
                set_shortcut(module_id, shortcut)
        
        for widget in self.custom_widgets:
            if hasattr(widget, "save_config"):
                try:
                    widget.save_config()
                except Exception as e:
                    print(f"Erreur de sauvegarde widget : {e}")
        
        self.pending_changes = {}
        self.update_status()
        self.status_label.setText("✅ Modifications appliquées! Redémarrez Anki.")
        self.status_label.setStyleSheet("color: #27ae60; font-weight: bold; padding: 5px;")
        tooltip("Modifications enregistrées. Redémarrez Anki pour les appliquer.")
