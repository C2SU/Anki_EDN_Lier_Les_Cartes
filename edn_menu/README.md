# Anki EDN Shared Menu System 🎯

Système de menu partagé pour les addons Anki EDN.

## 📦 Distribution

### Niveau 1 : Minimal (Menu uniquement)

Copiez **seulement** `shared_menu.py` dans votre addon.

```
MonAddon/
├── __init__.py
└── edn_menu/
    └── shared_menu.py    ← Menu fonctionne, pas d'UI de config
```

**Résultat** : Menu "Anki EDN" fonctionne, mais le bouton "⚙️ Paramètres" affiche un message d'information.

---

### Niveau 2 : Standard (avec Configuration)

Copiez **tout le dossier** `edn_menu/` dans votre addon.

```
MonAddon/
├── __init__.py
└── edn_menu/
    ├── __init__.py
    ├── shared_menu.py
    ├── settings_dialog.py
    ├── shortcuts_dialog.py
    ├── key_sequence_widget.py
    ├── logo.png              ← Logos inclus !
    └── discord_logo.png      ← Logos inclus !
```

**Résultat** : Menu + Configuration complète des modules et raccourcis + Logos Anki EDN.

> **Note** : La configuration est partagée entre tous les addons EDN via `edn_shared_config.json` dans le profil Anki. Le registre des modules est global via `mw._edn_registered_modules`.

---

## 🚀 Intégration rapide

```python
# Dans votre __init__.py
from .edn_menu import register_module, register_action

def init_mon_addon():
    # Enregistrer le module
    if not register_module(
        module_id="mon_addon",
        name="Mon Addon",
        description="Description",
        default_enabled=True
    ):
        return  # Module désactivé
    
    # Ajouter une action au menu
    register_action(
        module_id="mon_addon",
        label="🎯 Ma Fonctionnalité",
        callback=ma_fonction,
        shortcut="Ctrl+Shift+M"
    )

from aqt import gui_hooks
gui_hooks.main_window_did_init.append(init_mon_addon)
```

---

## 📚 Documentation complète

Consultez [GUIDE_INTEGRATION_MENU_EDN.md](GUIDE_INTEGRATION_MENU_EDN.md) pour :
- Exemples détaillés
- API complète
- Bonnes pratiques
- Dépannage

---

## ⚙️ Fichiers

| Fichier | Requis | Description |
|---------|--------|-------------|
| `shared_menu.py` | ✅ **Obligatoire** | Core du système de menu et registre global |
| `__init__.py` | ✅ **Recommandé** | API publique du package |
| `settings_dialog.py` | 🔹 Optionnel | UI de configuration des modules |
| `shortcuts_dialog.py` | 🔹 Optionnel | UI de gestion des raccourcis |
| `key_sequence_widget.py` | 🔹 Optionnel | Widget pour saisir les raccourcis |
| `GUIDE_INTEGRATION_MENU_EDN.md` | 📖 Documentation | Guide complet d'intégration |

---

## 🎨 Interopérabilité

Ce système permet à **plusieurs addons EDN** de partager le même menu "Anki EDN" :
- ✅ Détection automatique du menu existant via objectName
- ✅ Registre de modules global (`mw._edn_registered_modules`)
- ✅ Configuration centralisée dans `edn_shared_config.json` au niveau du profil
- ✅ Fonctionne quel que soit l'ordre de chargement des Addons

---

**Version** : 2.0.0 (Registre global)  
**Licence** : Compatible avec vos addons EDN
