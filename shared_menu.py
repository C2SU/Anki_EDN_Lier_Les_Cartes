"""
Anki EDN Shared Menu Manager
Provides a centralized menu system for all EDN modules.
"""
from aqt import mw
from aqt.qt import *
from typing import Callable, Optional, Dict, List
import json
import os

# Singleton menu instance
_edn_menu = None
_registered_modules = {}
_config_path = os.path.join(os.path.dirname(__file__), "edn_config.json")

def get_edn_menu():
    """Get or create the shared Anki EDN menu."""
    global _edn_menu
    
    # Check if menu already exists (created by another addon instance)
    if _edn_menu is None:
        # Search for existing menu by object name in menubar
        for action in mw.form.menubar.actions():
            menu = action.menu()
            if menu and menu.objectName() == "AnkiEDNMenu":
                _edn_menu = menu
                print("[shared_menu] ✓ Using existing Anki EDN menu")
                return _edn_menu
        
        # Create new menu if not found
        _edn_menu = QMenu("Anki EDN", mw)
        _edn_menu.setObjectName("AnkiEDNMenu")  # CRITICAL for cross-addon detection
        mw.form.menubar.addMenu(_edn_menu)
        
        # Add settings action at bottom
        _edn_menu.addSeparator()
        settings_action = QAction("⚙️ Paramètres EDN...", mw)
        settings_action.triggered.connect(open_settings_dialog)
        _edn_menu.addAction(settings_action)
        
        print("[shared_menu] ✓ Created new Anki EDN menu")
    
    return _edn_menu

def register_module(module_id: str, name: str, description: str = "", 
                   default_enabled: bool = True):
    """Register a module with the EDN system."""
    global _registered_modules
    if module_id not in _registered_modules:
        _registered_modules[module_id] = {
            "name": name,
            "description": description,
            "default_enabled": default_enabled,
            "actions": [],
            "config_widget": None
        }
    return is_module_enabled(module_id)

def register_interface(module_id: str, widget_class):
    """Register a custom QWidget for the settings dialog tab."""
    if module_id in _registered_modules:
        _registered_modules[module_id]["config_widget"] = widget_class

def register_action(module_id: str, label: str, callback: Callable, 
                   shortcut: Optional[str] = None, icon: Optional[str] = None):
    """Register a menu action for a module."""
    if not is_module_enabled(module_id):
        return None
    
    menu = get_edn_menu()
    
    # Create action
    action = QAction(label, mw)
    action.triggered.connect(callback)
    
    if shortcut:
        try:
            # Get custom shortcut from config or use default
            custom_shortcut = get_shortcut(module_id, shortcut)
            action.setShortcut(QKeySequence(custom_shortcut))
            # CRITICAL: Set shortcut context to ApplicationShortcut so it works globally
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            print(f"[shared_menu] Set shortcut '{custom_shortcut}' for {label}")
        except Exception as e:
            print(f"[shared_menu] Error setting shortcut: {e}")
    
    # Insert before separator (settings is last)
    actions = menu.actions()
    if len(actions) >= 2:
        menu.insertAction(actions[-2], action)  # Before separator
    else:
        menu.addAction(action)
    
    # Track action
    if module_id in _registered_modules:
        _registered_modules[module_id]["actions"].append({
            "label": label,
            "shortcut": shortcut,
            "action": action
        })
    
    return action

def get_config() -> dict:
    """Load EDN configuration."""
    if os.path.exists(_config_path):
        try:
            with open(_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"modules": {}, "shortcuts": {}}

def save_config(config: dict):
    """Save EDN configuration."""
    with open(_config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def is_module_enabled(module_id: str) -> bool:
    """Check if a module is enabled."""
    config = get_config()
    modules = config.get("modules", {})
    if module_id in modules:
        return modules[module_id]
    # Default to enabled if not configured
    if module_id in _registered_modules:
        return _registered_modules[module_id].get("default_enabled", True)
    return True

def set_module_enabled(module_id: str, enabled: bool):
    """Enable or disable a module."""
    config = get_config()
    if "modules" not in config:
        config["modules"] = {}
    config["modules"][module_id] = enabled
    save_config(config)

def get_shortcut(module_id: str, default: str) -> str:
    """Get custom shortcut or default."""
    config = get_config()
    shortcuts = config.get("shortcuts", {})
    return shortcuts.get(module_id, default)

def set_shortcut(module_id: str, shortcut: str):
    """Set custom shortcut for a module."""
    config = get_config()
    if "shortcuts" not in config:
        config["shortcuts"] = {}
    config["shortcuts"][module_id] = shortcut
    save_config(config)

def get_registered_modules() -> Dict:
    """Get all registered modules."""
    return _registered_modules.copy()

def open_settings_dialog():
    """Open the EDN settings dialog."""
    from .settings_dialog import EDNSettingsDialog
    dialog = EDNSettingsDialog(mw)
    dialog.exec()
