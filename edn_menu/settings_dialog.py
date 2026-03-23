"""
EDN Settings Dialog
Centralized settings for all EDN modules
"""
from aqt.qt import *
from aqt import mw
from .shared_menu import get_registered_modules, is_module_enabled, set_module_enabled
import os

class EDNSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paramètres Anki EDN")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Header with logo and title
        header_layout = QHBoxLayout()
        
        # Try to load logo
        logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
        if os.path.exists(logo_path):
            logo_label = QLabel()
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                logo_label.setPixmap(scaled_pixmap)
                header_layout.addWidget(logo_label)
        
        title_label = QLabel("<h2>Paramètres Anki EDN</h2>")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        # Description
        desc_label = QLabel(
            "Configuration des modules Anki EDN installés. "
            "Vous pouvez activer ou désactiver les modules individuellement."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(desc_label)
        
        layout.addWidget(QLabel("<hr>"))
        
        # Modules section
        modules_label = QLabel("<b>Modules Installés</b>")
        layout.addWidget(modules_label)
        
        # Scrollable area for modules
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        modules_widget = QWidget()
        modules_layout = QVBoxLayout(modules_widget)
        
        self.module_checkboxes = {}
        modules = get_registered_modules()
        
        if not modules:
            no_modules_label = QLabel("Aucun module EDN détecté.")
            no_modules_label.setStyleSheet("color: #999; font-style: italic;")
            modules_layout.addWidget(no_modules_label)
        else:
            for module_id, module_info in modules.items():
                checkbox = QCheckBox(module_info['name'])
                checkbox.setChecked(is_module_enabled(module_id))
                checkbox.setToolTip(module_info.get('description', ''))
                
                # Module description below checkbox
                desc = QLabel(f"  {module_info.get('description', 'Pas de description')}")
                desc.setStyleSheet("color: #888; font-size: 11px; margin-left: 20px;")
                
                modules_layout.addWidget(checkbox)
                modules_layout.addWidget(desc)
                modules_layout.addSpacing(8)
                
                self.module_checkboxes[module_id] = checkbox
        
        modules_layout.addStretch()
        scroll.setWidget(modules_widget)
        layout.addWidget(scroll, 1)
        
        layout.addWidget(QLabel("<hr>"))
        
        # Links section
        links_label = QLabel("<b>Ressources et Support</b>")
        layout.addWidget(links_label)
        
        links_layout = QHBoxLayout()
        links_layout.setSpacing(20)
        links_layout.addStretch()  # Center: add stretch before
        
        # Discord button with icon (larger, smoother)
        discord_logo_path = os.path.join(os.path.dirname(__file__), "discord_logo.png")
        discord_btn = QPushButton("Discord Anki EDN")
        if os.path.exists(discord_logo_path):
            # Load and scale logo with high quality
            original_pixmap = QPixmap(discord_logo_path)
            if not original_pixmap.isNull():
                # Scale to larger size for better quality
                size = 28
                scaled_pixmap = original_pixmap.scaled(
                    size, size, 
                    Qt.AspectRatioMode.KeepAspectRatio, 
                    Qt.TransformationMode.SmoothTransformation
                )
                discord_btn.setIcon(QIcon(scaled_pixmap))
                discord_btn.setIconSize(QSize(size, size))
        discord_btn.setStyleSheet("""
            QPushButton {
                background-color: #5865F2;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                font-weight: bold;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #4752C4;
            }
        """)
        discord_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://discord.gg/2A7zHAEBYt")))
        links_layout.addWidget(discord_btn)
        
        # Website button with custom icon
        website_btn = QPushButton()
        website_btn.setText("Site Anki EDN")
        website_btn.setIcon(QIcon(logo_path) if os.path.exists(logo_path) else QIcon())
        website_btn.setIconSize(QSize(24, 24))
        website_btn.setStyleSheet("""
            QPushButton {
                background-color: #2c7bb6;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                font-weight: bold;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #1e5a8a;
            }
        """)
        website_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://tools.c2su.org/Anki_EDN/book/installation.html")))
        links_layout.addWidget(website_btn)
        
        # GitHub button
        github_btn = QPushButton("🐙 GitHub")
        github_btn.setStyleSheet("""
            QPushButton {
                background-color: #24292e;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                font-weight: bold;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #1b1f23;
            }
        """)
        github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/C2SU")))
        links_layout.addWidget(github_btn)
        
        links_layout.addStretch()
        layout.addLayout(links_layout)
        
        # Shortcuts button
        shortcuts_btn = QPushButton("Configuration des Raccourcis")
        shortcuts_btn.setStyleSheet("""
            QPushButton {
                background-color: #607d8b;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #455a64;
            }
        """)
        shortcuts_btn.clicked.connect(self.open_shortcuts_dialog)
        layout.addWidget(shortcuts_btn)
        
        # Info label
        info_label = QLabel(
            "<small>Rejoignez la communauté sur Discord pour de l'aide, "
            "des astuces et des mises à jour !</small>"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; margin-top: 5px;")
        layout.addWidget(info_label)
        
        layout.addWidget(QLabel("<hr>"))
        
        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Annuler")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("Enregistrer et Redémarrer")
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
        save_btn.clicked.connect(self.save_and_restart)
        button_layout.addWidget(save_btn)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def save_and_restart(self):
        """Save settings and prompt to restart Anki"""
        from .shared_menu import set_module_enabled
        
        # Save all module states
        for module_id, checkbox in self.module_checkboxes.items():
            set_module_enabled(module_id, checkbox.isChecked())
        
        self.accept()
        
        # Prompt restart
        from aqt.utils import showInfo
        showInfo(
            "Paramètres sauvegardés !\n\n"
            "Veuillez redémarrer Anki pour que les changements prennent effet.",
            title="Redémarrage requis"
        )
    
    def open_shortcuts_dialog(self):
        """Open the shortcuts configuration dialog"""
        from .shortcuts_dialog import ShortcutsDialog
        dialog = ShortcutsDialog(self)
        dialog.exec()
