"""
Anki EDN Shared Menu System
Distributable menu package for EDN addons

This package provides a centralized menu system for all EDN addons.
"""

from .shared_menu import (
    get_edn_menu,
    register_module,
    register_action,
    should_initialize_module,
    get_config,
    save_config,
    is_module_enabled,
    set_module_enabled,
    get_shortcut,
    set_shortcut,
    get_registered_modules
)

__all__ = [
    'get_edn_menu',
    'register_module',
    'register_action',
    'should_initialize_module',
    'get_config',
    'save_config',
    'is_module_enabled',
    'set_module_enabled',
    'get_shortcut',
    'set_shortcut',
    'get_registered_modules'
]

__version__ = '1.0.0'
