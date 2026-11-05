# -*- coding: utf-8 -*-
"""
Anki EDN Setup Assistant & Tutorial Defaults
Provides automated configuration according to official Anki EDN tutorial guidelines:
1. Recommended global options (AnkiCollab pull on startup, auto-suspend new cards, auto-sync).
2. NID repair for linked cards + AnkiWeb synchronization.
3. Recommended review options preset (FSRS 88%, bury siblings, leech threshold) assigned to EDN deck.
4. Initial suspension of EDN-tagged cards for progressive study.
"""
from __future__ import annotations

import os
import json
import time
from typing import Optional, Tuple, Dict, Any, List

from aqt import mw, gui_hooks
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton,
    QFrame, QTimer, QApplication, Qt, QPalette
)
from aqt.utils import tooltip, showInfo


def is_dark_mode() -> bool:
    try:
        from aqt.theme import theme_manager
        if theme_manager and hasattr(theme_manager, "night_mode"):
            return bool(theme_manager.night_mode)
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


# -----------------------------------------------------------------------------
# 1. Action: Global Options (AnkiCollab + AnkiWeb)
# -----------------------------------------------------------------------------

def apply_global_ankicollab_settings() -> Tuple[bool, str]:
    """
    Configure recommended global options:
    - AnkiCollab: Update Decks on startup, Automatically suspend new Cards.
    - AnkiWeb: Auto-sync on open/close and media sync.
    """
    updated_items = []

    # 1. AnkiCollab settings
    try:
        mgr = getattr(mw, "addonManager", None) if mw else None
        if mgr and hasattr(mgr, "getConfig") and hasattr(mgr, "writeConfig"):
            cfg = mgr.getConfig("1957538407") or {}
            settings = cfg.setdefault("settings", {})
            settings["pull_on_startup"] = True
            settings["suspend_new_cards"] = True
            mgr.writeConfig("1957538407", cfg)
            updated_items.append("AnkiCollab (mises à jour au démarrage, suspension des nouvelles cartes)")
        # Also try direct config_manager if loaded
        try:
            import importlib
            cm = importlib.import_module("1957538407.config_manager")
            cur = cm.get_stored_config() or {}
            s = cur.setdefault("settings", {})
            s["pull_on_startup"] = True
            s["suspend_new_cards"] = True
            cm.write_config(cur)
        except Exception:
            pass
    except Exception as e:
        print(f"[setup_assistant] Erreur configuration AnkiCollab: {e}")

    # 2. Native Anki preferences (auto-sync)
    try:
        if mw and hasattr(mw, "pm") and getattr(mw.pm, "profile", None) is not None:
            mw.pm.profile["autoSync"] = True
            mw.pm.profile["syncMedia"] = True
            try:
                mw.pm.save()
            except Exception:
                pass
            updated_items.append("AnkiWeb (synchronisation et médias automatiques)")
    except Exception as e:
        print(f"[setup_assistant] Erreur configuration auto-sync Anki: {e}")

    if updated_items:
        return True, "Options globales configurées : " + ", ".join(updated_items) + "."
    return False, "Aucun paramètre global n'a pu être modifié (AnkiCollab non détecté)."


# -----------------------------------------------------------------------------
# 2. Action: NID Repair & AnkiWeb Sync
# -----------------------------------------------------------------------------

def repair_nids_and_sync() -> Tuple[bool, str]:
    """
    Repair linked cards NID mapping and trigger AnkiWeb synchronization.
    """
    migrated_count = 0
    # Try calling migrate_nids from Anki_EDN_nid
    try:
        import importlib
        mod = importlib.import_module("Anki_EDN_nid")
        if hasattr(mod, "migrate_nids"):
            mod.migrate_nids()
            return True, "Réparation des NID exécutée et synchronisation AnkiWeb lancée."
    except Exception:
        pass

    # Direct fallback if mapping file exists
    try:
        if mw and mw.col:
            addon_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            mapping_file = os.path.join(addon_base, "Anki_EDN_nid", "nid_mapping.txt")
            if os.path.exists(mapping_file):
                import codecs
                with codecs.open(mapping_file, "r", "utf-8") as f:
                    lines = f.readlines()
                local_notes = dict(mw.col.db.all("SELECT guid, id FROM notes"))
                existing_ids = set(local_notes.values())
                now = int(time.time())
                planned = []
                targets = set()
                for line in lines:
                    line = line.strip()
                    if not line or "," not in line:
                        continue
                    guid, nid_str = line.rsplit(",", 1)
                    try:
                        admin_nid = int(nid_str)
                    except ValueError:
                        continue
                    local_nid = local_notes.get(guid)
                    if local_nid and local_nid != admin_nid:
                        if admin_nid in existing_ids or admin_nid in targets:
                            continue
                        targets.add(admin_nid)
                        planned.append((admin_nid, now, local_nid))
                if planned:
                    orig_fk = mw.col.db.scalar("PRAGMA foreign_keys")
                    mw.col.db.execute("PRAGMA foreign_keys = OFF")
                    try:
                        mw.col.db.executemany("UPDATE notes SET id = ?, mod = ?, usn = -1 WHERE id = ?", planned)
                        mw.col.db.executemany("UPDATE cards SET nid = ?, mod = ?, usn = -1 WHERE nid = ?", planned)
                        migrated_count = len(planned)
                        mw.col.setMod()
                        mw.col.save()
                    finally:
                        mw.col.db.execute(f"PRAGMA foreign_keys = {1 if orig_fk else 0}")
    except Exception as e:
        print(f"[setup_assistant] Erreur directe réparation NID: {e}")

    # Trigger AnkiWeb Sync
    try:
        if mw and hasattr(mw, "onSync"):
            mw.onSync()
    except Exception:
        pass

    if migrated_count > 0:
        return True, f"NID réparés ({migrated_count} notes mises à jour) et synchronisation AnkiWeb lancée."
    return True, "NID vérifiés (déjà synchronisés) et synchronisation AnkiWeb initiée."


# -----------------------------------------------------------------------------
# 3. Action: EDN Review Options Preset & Assignment to EDN Deck
# -----------------------------------------------------------------------------

def apply_edn_deck_preset(col=None) -> Tuple[bool, str]:
    """
    Create or update deck configuration preset 'Anki EDN' with tutorial recommendations:
    - FSRS enabled, desired retention 88% (0.88)
    - New cards per day: 30
    - Max reviews per day: 9999
    - Bury new and review siblings: True
    - Leech threshold: 20, Leech action: Suspend
    And assign it to all decks containing 'EDN'.
    """
    if col is None and mw:
        col = mw.col
    if not col:
        return False, "Collection Anki non disponible."

    # 1. Enable FSRS in collection
    try:
        col.set_config("fsrs", True)
    except Exception as e:
        print(f"[setup_assistant] Erreur activation FSRS: {e}")

    # 2. Find or create 'Anki EDN' preset
    preset_name = "Anki EDN"
    existing_conf = None
    try:
        for c in col.decks.all_config():
            if c.get("name") == preset_name:
                existing_conf = c
                break
    except Exception:
        pass

    try:
        if existing_conf is None:
            base_conf = col.decks.get_config(1) or col.decks.all_config()[0]
            conf = col.decks.add_config(preset_name, clone_from=base_conf)
        else:
            conf = existing_conf

        conf_id = conf["id"]
        conf.setdefault("new", {})["perDay"] = 30
        conf["new"]["bury"] = True
        conf.setdefault("rev", {})["perDay"] = 9999
        conf["rev"]["bury"] = True
        conf.setdefault("lapse", {})["leechFails"] = 20
        conf["lapse"]["leechAction"] = 1  # Suspend
        conf["desiredRetention"] = 0.88
        col.decks.update_config(conf)
    except Exception as e:
        return False, f"Erreur lors de la création du préréglage : {e}"

    # 3. Assign this preset to all EDN decks
    assigned_count = 0
    try:
        for d in col.decks.all():
            dname = d.get("name", "")
            if "EDN" in dname and not dname.startswith("EDN - Paquets Filtr"):
                col.decks.set_config_id_for_deck_dict(d, conf_id)
                col.decks.save(d)
                assigned_count += 1
    except Exception as e:
        print(f"[setup_assistant] Erreur affectation préréglage aux paquets: {e}")

    return True, (
        f"Préréglage '{preset_name}' configuré (FSRS 88%, cartes sœurs enfouies, max 30 nlles/jour) "
        f"et assigné à {assigned_count} paquet(s) EDN."
    )


# -----------------------------------------------------------------------------
# 4. Action: Initial Suspension of EDN Cards
# -----------------------------------------------------------------------------

def suspend_initial_edn_cards(col=None) -> Tuple[bool, str]:
    """
    Suspend all cards tagged EDN (or in EDN decks) so the student can unsuspend
    them progressively item by item after studying their medical lectures.
    """
    if col is None and mw:
        col = mw.col
    if not col:
        return False, "Collection Anki non disponible."

    cids = col.find_cards("tag:EDN*")
    if not cids:
        cids = col.find_cards('"deck:*EDN*"')

    if not cids:
        return True, "Aucune carte EDN détectée à suspendre."

    try:
        col.sched.suspend_cards(cids)
        if mw and hasattr(mw, "reset"):
            mw.reset()
        return True, f"{len(cids)} carte(s) EDN suspendue(s) pour débuter l'apprentissage pas-à-pas."
    except Exception as e:
        return False, f"Erreur lors de la suspension des cartes : {e}"


# -----------------------------------------------------------------------------
# 5. Assistant UI Dialog
# -----------------------------------------------------------------------------

class EDNSetupAssistantDialog(QDialog):
    """Sleek modal dialog presenting the 4 tutorial setup options pre-checked."""

    def __init__(self, parent=None, is_startup: bool = False):
        super().__init__(parent or mw)
        self.is_startup = is_startup
        self.setWindowTitle("Assistant d'installation — Anki EDN")
        self.resize(580, 460)
        self._init_ui()

    def _init_ui(self):
        dark = is_dark_mode()
        bg_card = "#21252b" if dark else "#f8f9fa"
        border_card = "#3b4048" if dark else "#dee2e6"
        text_main = "#abb2bf" if dark else "#212529"
        text_muted = "#7f848e" if dark else "#6c757d"
        accent = "#61afef" if dark else "#1971c2"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # Header
        title_lbl = QLabel(
            f"<b style='font-size: 15px; color: {accent};'>"
            "Configuration recommandée du tutoriel Anki EDN"
            "</b>"
        )
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(
            f"<span style='font-size: 12px; color: {text_muted};'>"
            "Cet assistant permet d'appliquer automatiquement les réglages méthodologiques et techniques "
            "recommandés pour préparer sereinement vos EDN. Cochez les actions souhaitées :"
            "</span>"
        )
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        # Options Container Frame
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_card};
                border: 1px solid {border_card};
                border-radius: 6px;
            }}
        """)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(14, 12, 14, 12)
        frame_layout.setSpacing(10)

        # 1. Options globales
        self.cb_options = QCheckBox(
            "Options globales AnkiCollab & AnkiWeb (mises à jour au lancement, suspension auto, sync auto)"
        )
        self.cb_options.setChecked(True)
        self.cb_options.setToolTip(
            "Active le téléchargement automatique des mises à jour au démarrage, la suspension native des cartes ajoutées,\n"
            "et la synchronisation automatique des médias et de la collection avec AnkiWeb."
        )
        frame_layout.addWidget(self.cb_options)

        # 2. Réparation NID + sync
        self.cb_nids = QCheckBox(
            "Réparation des NID des cartes liées & synchronisation avec AnkiWeb"
        )
        self.cb_nids.setChecked(True)
        self.cb_nids.setToolTip(
            "Aligne les identifiants locaux des notes pour rendre opérationnelle la navigation entre cartes liées,\n"
            "puis déclenche une synchronisation avec AnkiWeb."
        )
        frame_layout.addWidget(self.cb_nids)

        # 3. Préréglage options révision
        self.cb_preset = QCheckBox(
            "Préréglage de révision 'Anki EDN' (FSRS 88%, cartes sœurs enfouies) assigné au paquet"
        )
        self.cb_preset.setChecked(True)
        self.cb_preset.setToolTip(
            "Active FSRS, configure la rétention cible à 88%, enfouit les cartes sœurs nouvelles et en révision,\n"
            "fixe les nouvelles cartes à 30/jour et affecte ce préréglage à tous vos paquets EDN."
        )
        frame_layout.addWidget(self.cb_preset)

        # 4. Suspension initiale
        self.cb_suspend = QCheckBox(
            "Suspension initiale des cartes EDN (recommandé : déblocage item par item après le cours)"
        )
        self.cb_suspend.setChecked(True)
        self.cb_suspend.setToolTip(
            "Suspend l'intégralité du paquet au départ. Vous désuspendrez uniquement les cartes correspondant\n"
            "aux items médicaux au fur et à mesure de votre apprentissage des cours."
        )
        frame_layout.addWidget(self.cb_suspend)

        layout.addWidget(frame)

        hint_lbl = QLabel(
            f"<span style='font-size: 11px; color: {text_muted};'>"
            "Vous pourrez réexécuter cet assistant à tout moment depuis les "
            "<b>Paramètres EDN &gt; Rétablir les réglages du tuto</b>."
            "</span>"
        )
        hint_lbl.setWordWrap(True)
        layout.addWidget(hint_lbl)

        layout.addStretch()

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        cancel_text = "Plus tard" if self.is_startup else "Annuler"
        self.cancel_btn = QPushButton(cancel_text)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                padding: 7px 16px; border-radius: 5px; font-weight: 600; font-size: 12px;
            }
        """)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()

        self.apply_btn = QPushButton("Appliquer les réglages sélectionnés")
        self.apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent}; color: white; border: none;
                padding: 7px 18px; border-radius: 5px; font-weight: bold; font-size: 12px;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
        """)
        self.apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.apply_btn)

        layout.addLayout(btn_layout)

    def _on_cancel(self):
        self.reject()
        if self.is_startup:
            tooltip(
                "Assistant reporté. Retrouvez-le à tout moment dans Paramètres EDN > Rétablir les réglages du tuto.",
                period=3500
            )

    def _on_apply(self):
        reports = []

        # 1. Options globales
        if self.cb_options.isChecked():
            ok, msg = apply_global_ankicollab_settings()
            reports.append(msg)

        # 2. NIDs + sync
        if self.cb_nids.isChecked():
            ok, msg = repair_nids_and_sync()
            reports.append(msg)

        # 3. Préréglage EDN
        if self.cb_preset.isChecked():
            ok, msg = apply_edn_deck_preset()
            reports.append(msg)

        # 4. Suspension
        if self.cb_suspend.isChecked():
            ok, msg = suspend_initial_edn_cards()
            reports.append(msg)

        # Mark completed in config
        try:
            from .shared_menu import get_config, save_config
            cfg = get_config()
            cfg["setup_assistant_completed"] = True
            save_config(cfg)
        except Exception:
            pass

        self.accept()

        report_txt = "\n• " + "\n• ".join(reports) if reports else "Aucune option sélectionnée."
        showInfo(
            f"Configuration terminée avec succès !\n{report_txt}",
            parent=self.parent(),
            title="Assistant Anki EDN"
        )


def open_setup_assistant_dialog(parent=None, is_startup: bool = False) -> None:
    """Open the setup assistant modal dialog."""
    d = EDNSetupAssistantDialog(parent=parent or mw, is_startup=is_startup)
    d.exec()


def check_startup_assistant() -> None:
    """Prompt the user on profile open if the assistant was not completed yet."""
    if not mw:
        return
    try:
        from .shared_menu import get_config
        cfg = get_config()
        if cfg.get("setup_assistant_completed", False):
            return

        timer = QTimer(mw)

        def tick():
            if mw.col is None:
                timer.stop()
            elif not mw.progress.busy() and QApplication.activeModalWidget() is None:
                timer.stop()
                open_setup_assistant_dialog(parent=mw, is_startup=True)

        timer.timeout.connect(tick)
        QTimer.singleShot(3500, lambda: timer.start(1000))
    except Exception as e:
        print(f"[setup_assistant] Erreur vérification démarrage: {e}")


def init_setup_assistant_hooks() -> None:
    """Hook startup verification into profile_did_open."""
    gui_hooks.profile_did_open.append(check_startup_assistant)
