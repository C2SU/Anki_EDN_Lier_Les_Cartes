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
_config_path = None  # Will be initialized lazily when needed

def _get_registry() -> dict:
    """Get the global module registry shared across all EDN addons via mw."""
    if not hasattr(mw, '_edn_registered_modules'):
        mw._edn_registered_modules = {}
    return mw._edn_registered_modules

def _get_config_path():
    """Get config path (lazy initialization to avoid profile loading issues)."""
    global _config_path
    if _config_path is None:
        # Prevent crash if profile is not yet loaded (e.g. during addon scan)
        if not mw.pm or not mw.pm.name:
            return None
        # CRITICAL: Use profile folder for shared config across all EDN addons
        _config_path = os.path.join(mw.pm.profileFolder(), "edn_shared_config.json")
    return _config_path

def get_edn_menu():
    """Get or create the shared Anki EDN menu."""
    global _edn_menu
    
    # Check if menu already exists (created by another addon instance)
    if _edn_menu is None:
        # Prevent crash if UI is not yet fully initialized
        if not hasattr(mw, "form") or not mw.form or not hasattr(mw.form, "menubar") or not mw.form.menubar:
            return None
            
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
    """
    Register a module with the EDN system.
    This function ONLY declares the module, it does NOT control initialization.
    Always returns True to allow modules to complete their initialization.
    Use should_initialize_module() if you need to check if module is enabled.
    """
    registry = _get_registry()
    registry[module_id] = {
        "name": name,
        "description": description,
        "default_enabled": default_enabled,
        "actions": []
    }
    # Always return True - module declaration should never fail
    # The module can decide whether to initialize based on should_initialize_module()
    return True

def should_initialize_module(module_id: str) -> bool:
    """
    Check if a module should initialize based on user settings.
    Use this instead of the return value of register_module().
    """
    return is_module_enabled(module_id)

def register_action(module_id: str, label: str, callback: Callable, 
                   shortcut: Optional[str] = None, icon: Optional[str] = None):
    """Register a menu action for a module."""
    if not is_module_enabled(module_id):
        return None
    
    menu = get_edn_menu()
    
    # Create action
    action = QAction(label, mw)
    action.triggered.connect(callback)
    
    # NOTE: We intentionally do NOT apply shortcuts to menu actions.
    # Shortcuts are managed independently (editor_did_init_shortcuts, browser window actions)
    # so they don't conflict across contexts. The shortcut info is stored in the registry
    # for reference and display in the config widget only.
    if shortcut:
        print(f"[shared_menu] Shortcut '{shortcut}' for '{label}' managed externally (not on menu action)")
    
    # Insert before separator (settings is last)
    actions = menu.actions()
    if len(actions) >= 2:
        menu.insertAction(actions[-2], action)  # Before separator
    else:
        menu.addAction(action)
    
    # Track action
    registry = _get_registry()
    if module_id in registry:
        registry[module_id]["actions"].append({
            "label": label,
            "shortcut": shortcut,
            "action": action
        })
    
    return action


def register_action_shortcut_only(module_id: str, label: str, callback: Callable,
                                  shortcut: Optional[str] = None, icon: Optional[str] = None,
                                  shortcut_key: Optional[str] = None):
    """
    Register an action in the shortcut config dialog ONLY.
    The action will NOT appear in the EDN dropdown menu.
    It is still listed in the registry so it's visible in ShortcutsDialog.
    
    shortcut_key: explicit config store key (e.g. 'linked_cards_search').
                  If None, the key will be computed as '{module_id}_{label}'.
    """
    registry = _get_registry()
    if module_id in registry:
        registry[module_id]["actions"].append({
            "label": label,
            "shortcut": shortcut,
            "action": None,  # No QAction in the menu
            "shortcut_key": shortcut_key  # explicit config key override
        })
    return None

def get_config() -> dict:
    """Load EDN configuration."""
    config_path = _get_config_path()
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"modules": {}, "shortcuts": {}}

def save_config(config: dict):
    """Save EDN configuration."""
    config_path = _get_config_path()
    if not config_path:
        return
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def is_module_enabled(module_id: str) -> bool:
    """Check if a module is enabled."""
    config = get_config()
    modules = config.get("modules", {})
    if module_id in modules:
        return modules[module_id]
    # Default to enabled if not configured
    registry = _get_registry()
    if module_id in registry:
        return registry[module_id].get("default_enabled", True)
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
    return _get_registry().copy()

def open_settings_dialog():
    """Open the EDN settings dialog (requires settings_dialog.py)."""
    try:
        from .settings_dialog import EDNSettingsDialog
        dialog = EDNSettingsDialog(mw)
        dialog.exec()
    except ImportError:
        from aqt.utils import showInfo
        showInfo(
            "Configuration EDN non disponible.\n\n"
            "Pour activer la gestion des modules et raccourcis, "
            "copiez aussi les fichiers suivants depuis edn_menu_shared/ :\n"
            "• settings_dialog.py\n"
            "• shortcuts_dialog.py\n"
            "• key_sequence_widget.py",
            title="Composants optionnels manquants"
        )

