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

_local_registry = {}

def _get_registry() -> dict:
    """Get the global module registry shared across all EDN addons via mw."""
    if mw is None:
        return _local_registry
    if not hasattr(mw, '_edn_registered_modules'):
        mw._edn_registered_modules = {}
    if _local_registry:
        mw._edn_registered_modules.update(_local_registry)
        _local_registry.clear()
    return mw._edn_registered_modules

def _get_config_path():
    """Get config path dynamically based on currently active profile or fallback."""
    try:
        if mw and hasattr(mw, "pm") and mw.pm:
            # 1. Active profile folder if open
            try:
                if getattr(mw.pm, "name", None):
                    folder = mw.pm.profileFolder()
                    path = os.path.join(folder, "edn_shared_config.json")
                    if os.path.exists(path):
                        return path
                    # Auto-seed from other profiles if not yet created in active profile
                    base_dir = getattr(mw.pm, "base", None)
                    if base_dir and os.path.exists(base_dir):
                        for sub in os.listdir(base_dir):
                            candidate = os.path.join(base_dir, sub, "edn_shared_config.json")
                            if os.path.isfile(candidate):
                                try:
                                    import shutil
                                    shutil.copyfile(candidate, path)
                                    return path
                                except Exception:
                                    pass
                    return path
            except Exception:
                pass
            # 2. Base directory fallback before profile is fully opened
            base_dir = getattr(mw.pm, "base", None)
            if base_dir and os.path.exists(base_dir):
                for sub in os.listdir(base_dir):
                    candidate = os.path.join(base_dir, sub, "edn_shared_config.json")
                    if os.path.isfile(candidate):
                        return candidate
    except Exception:
        pass
    # 3. Environment APPDATA fallback
    try:
        appdata = os.environ.get("APPDATA")
        if appdata:
            anki2_dir = os.path.join(appdata, "Anki2")
            if os.path.exists(anki2_dir):
                for sub in os.listdir(anki2_dir):
                    candidate = os.path.join(anki2_dir, sub, "edn_shared_config.json")
                    if os.path.isfile(candidate):
                        return candidate
    except Exception:
        pass
    return None

def get_edn_menu():
    """Get or create the shared Anki EDN menu."""
    global _edn_menu
    
    if not hasattr(mw, "form") or not mw.form:
        return None
    menubar = getattr(mw.form, "menubar", None)
    if not menubar:
        return None

    existing_menu = None
    for action in menubar.actions():
        menu = action.menu()
        if menu and (menu.objectName() == "AnkiEDNMenu" or menu.title() == "Anki EDN"):
            existing_menu = menu
            break

    if existing_menu is not None:
        _edn_menu = existing_menu
        _edn_menu.setObjectName("AnkiEDNMenu")
    elif _edn_menu is None:
        _edn_menu = QMenu("Anki EDN", mw)
        _edn_menu.setObjectName("AnkiEDNMenu")
        menubar.addMenu(_edn_menu)
        
        _edn_menu.addSeparator()
        settings_action = QAction("Paramètres EDN...", mw)
        settings_action.setObjectName("EDN_Settings_Action")
        settings_action.setMenuRole(QAction.MenuRole.NoRole)
        settings_action.triggered.connect(open_settings_dialog)
        _edn_menu.addAction(settings_action)

    if _edn_menu is not None:
        seen_texts = set()
        for act in list(_edn_menu.actions()):
            txt = act.text()
            if not txt or act.isSeparator() or txt == "Paramètres EDN...":
                continue
            if txt in seen_texts:
                _edn_menu.removeAction(act)
                try:
                    act.deleteLater()
                except Exception:
                    pass
            else:
                seen_texts.add(txt)
    
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
    """Register a menu action for a module idempotently."""
    if not is_module_enabled(module_id):
        return None
    
    menu = get_edn_menu()
    if not menu:
        return None

    action_obj_name = f"EDN_Action_{module_id}_{label}"
    for existing_action in menu.actions():
        if existing_action.objectName() == action_obj_name or existing_action.text() == label:
            existing_action.setObjectName(action_obj_name)
            try:
                existing_action.triggered.disconnect()
            except Exception:
                pass
            existing_action.triggered.connect(callback)
            if shortcut:
                try:
                    custom_shortcut = get_shortcut(f"{module_id}_{label}", shortcut)
                    existing_action.setShortcut(QKeySequence(custom_shortcut))
                    existing_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
                except Exception:
                    pass
            return existing_action

    # Create action
    action = QAction(label, mw)
    action.setObjectName(action_obj_name)
    action.triggered.connect(callback)
    
    if shortcut:
        try:
            custom_shortcut = get_shortcut(f"{module_id}_{label}", shortcut)
            action.setShortcut(QKeySequence(custom_shortcut))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            print(f"[shared_menu] Set shortcut '{custom_shortcut}' for {label}")
        except Exception as e:
            print(f"[shared_menu] Error setting shortcut: {e}")
    
    actions = menu.actions()
    if len(actions) >= 2:
        menu.insertAction(actions[-2], action)
    else:
        menu.addAction(action)
    
    registry = _get_registry()
    if module_id in registry:
        for act_info in registry[module_id]["actions"]:
            if act_info.get("label") == label:
                act_info["action"] = action
                act_info["shortcut"] = shortcut
                return action
        registry[module_id]["actions"].append({
            "label": label,
            "shortcut": shortcut,
            "action": action
        })
    
    return action

def register_action_shortcut_only(module_id: str, label: str, callback: Optional[Callable] = None, 
                                  shortcut: Optional[str] = None, shortcut_key: Optional[str] = None):
    """
    Register a shortcut for a module without adding an item to the EDN menu bar dropdown.
    Installs the shortcut globally on mw with ApplicationShortcut context,
    and registers it in the EDN registry for configuration in the Shortcuts dialog.
    """
    action_key = shortcut_key or f"{module_id}_{label}"
    effective_shortcut = get_shortcut(action_key, shortcut) if shortcut else ""
    
    action = None
    is_noop_lambda = getattr(callback, "__name__", "") == "<lambda>"
    if mw and callback and effective_shortcut and not is_noop_lambda:
        obj_name = f"EDN_ShortcutOnly_{action_key}"
        for act in mw.actions():
            if act.objectName() == obj_name:
                action = act
                break
        
        if action is None:
            action = QAction(label, mw)
            action.setObjectName(obj_name)
            mw.addAction(action)
        
        try:
            action.triggered.disconnect()
        except Exception:
            pass
        action.triggered.connect(callback)
        action.setShortcut(QKeySequence(effective_shortcut))
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

    registry = _get_registry()
    if module_id in registry:
        for item in registry[module_id]["actions"]:
            if item.get("shortcut_key") == action_key:
                item["shortcut"] = shortcut
                item["action"] = action
                item["callback"] = callback
                return action
        registry[module_id]["actions"].append({
            "label": label,
            "shortcut": shortcut,
            "shortcut_key": action_key,
            "action": action,
            "callback": callback
        })
    return action

def register_interface(module_id: str, widget_class):
    """Register custom configuration widget for a module in settings dialog."""
    registry = _get_registry()
    if module_id in registry:
        registry[module_id]["config_widget"] = widget_class

_CACHED_CONFIG = None

DEFAULT_MULTI_WINDOW_CONFIG = {
    "browser": True,
    "add_cards": True,
    "edit_current": False,
}

def get_config() -> dict:
    """Load EDN configuration."""
    global _CACHED_CONFIG
    if mw and getattr(mw, "_edn_cached_shared_config", None) is not None:
        _CACHED_CONFIG = mw._edn_cached_shared_config
        return _CACHED_CONFIG
    if _CACHED_CONFIG is not None:
        return _CACHED_CONFIG
    config_path = _get_config_path()
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                _CACHED_CONFIG = json.load(f)
                if mw:
                    mw._edn_cached_shared_config = _CACHED_CONFIG
                return _CACHED_CONFIG
        except:
            pass
    _CACHED_CONFIG = {"modules": {}, "shortcuts": {}}
    if mw:
        mw._edn_cached_shared_config = _CACHED_CONFIG
    return _CACHED_CONFIG

def save_config(config: dict):
    """Save EDN configuration."""
    global _CACHED_CONFIG
    _CACHED_CONFIG = dict(config)
    if mw:
        mw._edn_cached_shared_config = _CACHED_CONFIG
    config_path = _get_config_path()
    if not config_path:
        return
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def get_multi_window_config() -> dict:
    """Get multi-window configuration with targeted native defaults."""
    cfg = get_config()
    mw_cfg = dict(DEFAULT_MULTI_WINDOW_CONFIG)
    if "multi_window" in cfg and isinstance(cfg["multi_window"], dict):
        mw_cfg.update(cfg["multi_window"])
    return mw_cfg

def set_multi_window_config(multi_window_cfg: dict):
    """Save multi-window configuration."""
    cfg = get_config()
    if "multi_window" not in cfg or not isinstance(cfg["multi_window"], dict):
        cfg["multi_window"] = {}
    cfg["multi_window"].update(multi_window_cfg)
    save_config(cfg)

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


def _initialize_card_styles_on_start():
    if not mw:
        return
    if not hasattr(mw, "_edn_card_styles_initialized"):
        mw._edn_card_styles_initialized = True
        try:
            if __package__:
                from . import card_styles
            else:
                import card_styles
            
            card_styles.init_card_styles()
            
            # Forcer l'écriture immédiate et la synchronisation initiale
            card_styles._on_profile_opened()
            
            # Enregistrer la construction du menu après l'initialisation de la fenêtre principale
            from aqt import gui_hooks
        except ImportError:
            pass
        except Exception as e:
            print(f"[shared_menu] Erreur initialisation card_styles: {e}")

from aqt import gui_hooks
gui_hooks.profile_did_open.append(_initialize_card_styles_on_start)

try:
    if __package__:
        from .stability_guards import install_stability_guards
    else:
        from stability_guards import install_stability_guards
    install_stability_guards()
except Exception as e:
    print(f"[shared_menu] Erreur installation stability_guards: {e}")
