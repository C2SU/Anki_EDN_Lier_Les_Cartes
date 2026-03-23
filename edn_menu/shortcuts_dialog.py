"""
Shortcuts Configuration Dialog for Anki EDN
"""
from aqt.qt import *
from aqt import mw
from .shared_menu import get_registered_modules, get_shortcut, set_shortcut
from .key_sequence_widget import KeySequenceEdit
import os

class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration des Raccourcis - Anki EDN")
        self.setMinimumWidth(700)
        self.setMinimumHeight(400)
        self.shortcut_inputs = {}
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Header
        header = QLabel("<h2>Raccourcis Clavier</h2>")
        layout.addWidget(header)
        
        desc = QLabel(
            "Cliquez dans le champ et pressez votre combinaison de touches. "
            "Validez avec Entrée, annulez avec Échap."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(desc)
        
        layout.addWidget(QLabel("<hr>"))
        
        # Scroll area for shortcuts
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        shortcuts_widget = QWidget()
        shortcuts_layout = QVBoxLayout(shortcuts_widget)
        
        modules = get_registered_modules()
        
        if not modules:
            no_shortcuts = QLabel("Aucun module avec raccourci détecté.")
            no_shortcuts.setStyleSheet("color: #999; font-style: italic;")
            shortcuts_layout.addWidget(no_shortcuts)
        else:
            # Table-like layout
            for module_id, module_info in modules.items():
                actions = module_info.get('actions', [])
                for action_info in actions:
                    shortcut = action_info.get('shortcut')
                    if not shortcut:
                        continue  # Skip actions without shortcuts
                    
                    # Create row
                    row = QWidget()
                    row_layout = QHBoxLayout(row)
                    row_layout.setContentsMargins(0, 5, 0, 5)
                    
                    # Label (module name + action)
                    label_text = f"{module_info['name']}"
                    label = QLabel(label_text)
                    label.setMinimumWidth(200)
                    label.setStyleSheet("font-weight: bold;")
                    row_layout.addWidget(label)
                    
                    # Key sequence input widget
                    shortcut_input = KeySequenceEdit()
                    current = get_shortcut(module_id, shortcut)
                    shortcut_input.set_shortcut(current)
                    shortcut_input.setMinimumWidth(250)
                    row_layout.addWidget(shortcut_input)
                    
                    # Store ref
                    self.shortcut_inputs[module_id] = {
                        'widget': shortcut_input,
                        'default': shortcut
                    }
                    
                    # Reset button
                    reset_btn = QPushButton("Réinitialiser")
                    reset_btn.setMinimumWidth(120)
                    reset_btn.clicked.connect(
                        lambda checked=False, mid=module_id: self.reset_shortcut(mid)
                    )
                    row_layout.addWidget(reset_btn)
                    
                    row_layout.addStretch()
                    shortcuts_layout.addWidget(row)
        
        shortcuts_layout.addStretch()
        scroll.setWidget(shortcuts_widget)
        layout.addWidget(scroll, 1)
        
        layout.addWidget(QLabel("<hr>"))
        
        # Info
        info = QLabel(
            "<small>⚠️ Les changements nécessitent un redémarrage d'Anki pour prendre effet.</small>"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #d32f2f; font-weight: bold;")
        layout.addWidget(info)
        
        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Annuler")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("Enregistrer")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #2c7bb6;
                color: white;
                padding: 8px 16px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1e5a8a;
            }
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
