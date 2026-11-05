# -*- coding: utf-8 -*-
"""
Anki EDN Field Protection Module
Preserves user-modified fields across AnkiCollab deck updates by applying
protection tags ('AnkiCollab_Protect::<Field>'), while strictly excluding
personal fields such as 'Erreurs faites'.
"""
from __future__ import annotations

import os
import json
import hashlib
from typing import Optional, Sequence, Iterable, Any, Dict, List, Set

from aqt import mw, gui_hooks
from aqt.qt import QAction, QMenu
from aqt.utils import tooltip, showInfo
from aqt.operations import QueryOp

PROTECT_PREFIX = "AnkiCollab_Protect"
EXCLUDED_FIELD_NAMES = {"erreurs faites", "erreur faites"}
ADDON_ANKICOLLAB_ID = "1957538407"

_pre_snapshots: dict[str, dict] = {}


# -----------------------------------------------------------------------------
# 1. Pure Field Comparison Logic
# -----------------------------------------------------------------------------

def is_edn_or_collab_note(note) -> bool:
    """Determine whether a note belongs to EDN / AnkiCollab curriculum."""
    if not note:
        return False
    # Check tags
    tags = getattr(note, "tags", [])
    if any(t.lower().startswith(("edn", "ankicollab")) for t in tags):
        return True
    # Check notetype name
    try:
        nt = note.note_type() if hasattr(note, "note_type") else None
        if nt:
            name = nt.get("name", "").lower()
            if any(k in name for k in ("edn", "picat", "syndrome", "sémiologie", "semiologie", "basique", "trou")):
                return True
    except Exception:
        pass
    # Check deck of card
    try:
        if mw and mw.col and hasattr(note, "cards"):
            for card in note.cards():
                d = mw.col.decks.get(card.did)
                if d and ("edn" in d.get("name", "").lower() or d.get("name", "").startswith("EDN")):
                    return True
    except Exception:
        pass
    return False


def is_excluded_field(field_name: str) -> bool:
    """Check if a field name is excluded from protection (e.g. 'Erreurs faites')."""
    if not isinstance(field_name, str):
        return False
    return field_name.strip().lower() in EXCLUDED_FIELD_NAMES


def field_hashes(flds: str) -> list[str]:
    """Compute sha1 hashes of note fields delimited by unit separator \\x1f."""
    return [hashlib.sha1(f.encode("utf-8")).hexdigest()[:12] for f in flds.split("\x1f")]


def fingerprint(rows: Iterable[tuple[int, int, str, str]]) -> dict[str, list[str]]:
    """Convert note rows (nid, mid, flds, tags) into {str(nid): [field_hash, ...]}."""
    return {str(nid): field_hashes(flds) for nid, _mid, flds, _tags in rows}


def protect_tag(field_name: str) -> str:
    """Format the AnkiCollab protection tag for a given field name."""
    safe_field = field_name.replace(" ", "_")
    return f"{PROTECT_PREFIX}::{safe_field}"


def is_protect_tag(tag: str) -> bool:
    """Check whether a tag is an AnkiCollab protection tag."""
    return tag.lower().startswith(PROTECT_PREFIX.lower() + "::")


def protected_names(tags: Iterable[str]) -> set[str]:
    """Return lower-cased field names currently protected on a note."""
    pre = PROTECT_PREFIX.lower() + "::"
    return {t[len(pre):].lower() for t in tags if t.lower().startswith(pre)}


def edits_to_protect(
    baseline: dict[str, list[str]],
    rows: Iterable[tuple[int, int, str, str]],
    field_names: dict[int, list[str]],
    prev: Optional[dict[str, list[str]]] = None
) -> dict[int, list[str]]:
    """
    Compare current note rows against baseline.
    Returns {nid: [protect_tags]} for fields that changed since baseline,
    strictly EXCLUDING 'Erreurs faites' (or 'erreur faites').
    """
    out: dict[int, list[str]] = {}
    for nid, mid, flds, tags in rows:
        old = baseline.get(str(nid))
        names = field_names.get(mid)
        new = field_hashes(flds)
        if old is None or names is None or len(old) != len(new) or len(names) != len(new):
            continue

        raw_tags = tags.split() if isinstance(tags, str) else tags
        have = protected_names(raw_tags)
        if "all" in have:
            continue

        before = (prev or {}).get(str(nid))
        if before is not None and len(before) != len(new):
            before = None

        add: list[str] = []
        for i, (a, b) in enumerate(zip(old, new)):
            if a != b and (before is None or before[i] != b):
                fname = names[i]
                # User constraint: exclude "erreur faites" which is personal anyway
                if is_excluded_field(fname):
                    continue
                safe_name = fname.replace(" ", "_").lower()
                if safe_name not in have:
                    add.append(protect_tag(fname))
        if add:
            out[nid] = add
    return out


# -----------------------------------------------------------------------------
# 2. Baseline Fingerprint Persistence
# -----------------------------------------------------------------------------

def get_baselines_dir() -> str:
    """Directory where deck baseline fingerprints are persisted."""
    base_dir = None
    try:
        if mw and hasattr(mw, "pm") and mw.pm and getattr(mw.pm, "name", None):
            base_dir = os.path.join(mw.pm.profileFolder(), "edn_baselines")
    except Exception:
        pass
    if not base_dir:
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.join(pkg_dir, "user_files", "baselines")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def _baseline_path(deck_key: str, kind: str = "") -> str:
    safe_key = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in deck_key)
    return os.path.join(get_baselines_dir(), f"{safe_key}{kind}.json")


def load_baseline(deck_key: str, kind: str = "") -> Optional[dict]:
    try:
        path = _baseline_path(deck_key, kind)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


def save_baseline(deck_key: str, data: dict, kind: str = "") -> None:
    try:
        path = _baseline_path(deck_key, kind)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception as e:
        print(f"[field_protection] Erreur sauvegarde baseline: {e}")


# -----------------------------------------------------------------------------
# 3. Anki Collection Helpers
# -----------------------------------------------------------------------------

_DECK_NOTES_SQL = (
    "select id, mid, flds, tags from notes where id in "
    "(select nid from cards where did in {ids} or odid in {ids})"
)


def get_deck_notes(col, deck_ids: Iterable[int]) -> list[tuple[int, int, str, str]]:
    ids = [int(d) for d in deck_ids]
    if not ids or not col or not hasattr(col, "db"):
        return []
    id_list = "(" + ",".join(str(i) for i in ids) + ")"
    try:
        return [(int(r[0]), int(r[1]), r[2], r[3]) for r in col.db.all(_DECK_NOTES_SQL.format(ids=id_list))]
    except Exception:
        return []


def get_subscribed_deck_hashes() -> list[str]:
    """Retrieve subscribed deck hashes from AnkiCollab configuration."""
    if not mw or not hasattr(mw, "addonManager"):
        return []
    try:
        cfg = mw.addonManager.getConfig(ADDON_ANKICOLLAB_ID) or {}
        return [k for k, v in cfg.items() if k not in ("settings", "auth") and isinstance(v, dict)]
    except Exception:
        return []


def get_deck_ids_for_hash(col, deck_hash: Optional[str]) -> list[int]:
    """Map an AnkiCollab deck hash to its local deck and children IDs."""
    if not col:
        return []
    if deck_hash:
        try:
            import importlib
            utils = importlib.import_module(f"{ADDON_ANKICOLLAB_ID}.utils")
            did = utils.get_did_from_hash(deck_hash)
            if did and col.decks.get(int(did), default=False):
                return list(col.decks.deck_and_child_ids(int(did)))
        except Exception:
            pass

    # Fallback to all decks matching EDN
    dids: Set[int] = set()
    try:
        for d in col.decks.all():
            name = d.get("name", "")
            if "EDN" in name and not name.startswith("EDN - Paquets Filtr"):
                dids.update(col.decks.deck_and_child_ids(d["id"]))
    except Exception:
        pass
    return list(dids)


# -----------------------------------------------------------------------------
# 4. Snapshot & Protection Operations
# -----------------------------------------------------------------------------

def snapshot(deck_hash: Optional[str] = None, col=None) -> None:
    """Store fingerprint of deck notes as the current baseline."""
    if col is None and mw:
        col = mw.col
    if not col:
        return

    keys = [deck_hash] if deck_hash else (get_subscribed_deck_hashes() or [None])
    for key in keys:
        dids = get_deck_ids_for_hash(col, key)
        if not dids:
            continue
        rows = get_deck_notes(col, dids)
        if not rows:
            continue
        save_key = key or "edn_main"
        pre = _pre_snapshots.pop(save_key, None)
        post = fingerprint(rows)
        save_baseline(save_key, post)
        if pre is not None:
            save_baseline(
                save_key,
                {k: v for k, v in pre.items() if k in post and post[k] != v},
                ".prev"
            )


def protect_edits(deck_hash: Optional[str] = None, col=None) -> int:
    """
    Called before AnkiCollab installs updates on a deck.
    Detects user-edited fields and adds protection tags (except 'Erreurs faites').
    """
    from .shared_menu import is_protect_user_edits_enabled
    if not is_protect_user_edits_enabled():
        return 0

    if col is None and mw:
        col = mw.col
    if not col:
        return 0

    save_key = deck_hash or "edn_main"
    base = load_baseline(save_key)
    dids = get_deck_ids_for_hash(col, deck_hash)
    if not base or not dids:
        return 0

    rows = get_deck_notes(col, dids)
    if not rows:
        return 0

    _pre_snapshots[save_key] = fingerprint(rows)
    names: dict[int, list[str]] = {}
    for mid in {r[1] for r in rows}:
        nt = col.models.get(mid)
        if nt:
            names[mid] = [f["name"] for f in nt["flds"]]

    prev = load_baseline(save_key, ".prev")
    adds = edits_to_protect(base, rows, names, prev)
    notes = []
    for nid, tags in adds.items():
        note = col.get_note(nid)
        for t in tags:
            note.add_tag(t)
        notes.append(note)

    if notes:
        col.update_notes(notes)
    return len(notes)


def protect_all_user_edits_now(col=None, parent=None) -> int:
    """
    On-demand operation triggered by user via menu or settings button.
    Scans for edits compared to baseline and protects them.
    """
    if col is None and mw:
        col = mw.col
    if not col:
        if parent:
            showInfo("Collection Anki non disponible.", parent=parent)
        return 0

    keys = get_subscribed_deck_hashes() or [None]
    total_protected = 0
    new_baselines_created = 0

    for key in keys:
        save_key = key or "edn_main"
        base = load_baseline(save_key)
        dids = get_deck_ids_for_hash(col, key)
        if not dids:
            continue
        rows = get_deck_notes(col, dids)
        if not rows:
            continue

        if base is None:
            # Baseline missing: initialize it now
            post = fingerprint(rows)
            save_baseline(save_key, post)
            new_baselines_created += 1
            continue

        names: dict[int, list[str]] = {}
        for mid in {r[1] for r in rows}:
            nt = col.models.get(mid)
            if nt:
                names[mid] = [f["name"] for f in nt["flds"]]

        adds = edits_to_protect(base, rows, names)
        notes = []
        for nid, tags in adds.items():
            note = col.get_note(nid)
            for t in tags:
                note.add_tag(t)
            notes.append(note)

        if notes:
            col.update_notes(notes)
            total_protected += len(notes)

    if total_protected > 0:
        msg = (
            f"{total_protected} carte(s) protégée(s) contre les futures mises à jour AnkiCollab.\n\n"
            "Vos modifications sur ces champs sont désormais verrouillées "
            "(le champ 'Erreurs faites' reste personnel et exclu)."
        )
        if parent:
            showInfo(msg, parent=parent, title="Protection Anki EDN")
        else:
            tooltip(f"Anki EDN : {total_protected} carte(s) protégée(s).")
    elif new_baselines_created > 0:
        msg = (
            "Instantané de référence initialisé pour vos cartes Anki EDN.\n\n"
            "À partir de maintenant, toute modification apportée à vos cartes sera automatiquement "
            "détectée et protégée lors des prochaines synchronisations."
        )
        if parent:
            showInfo(msg, parent=parent, title="Protection Anki EDN")
        else:
            tooltip("Anki EDN : instantané de référence créé.")
    else:
        msg = (
            "Aucune modification de champ non protégée n'a été détectée par rapport au paquet de référence.\n\n"
            "Rappel : le champ 'Erreurs faites' est strictement personnel et n'a pas besoin de protection."
        )
        if parent:
            showInfo(msg, parent=parent, title="Protection Anki EDN")
        else:
            tooltip("Anki EDN : cartes déjà à jour ou protégées.")

    return total_protected


# -----------------------------------------------------------------------------
def protect_notes_manually(parent, nids: Sequence[int]) -> int:
    """Apply field protection tags to selected notes for all non-excluded fields."""
    if not mw or not mw.col or not nids:
        return 0
    col = mw.col
    notes = []
    for nid in nids:
        try:
            note = col.get_note(nid)
            nt = note.note_type()
            if not nt:
                continue
            added = False
            for f in nt.get("flds", []):
                fname = f.get("name", "")
                if is_excluded_field(fname):
                    continue
                ptag = protect_tag(fname)
                if ptag not in note.tags:
                    note.add_tag(ptag)
                    added = True
            if added:
                notes.append(note)
        except Exception:
            pass

    if notes:
        col.update_notes(notes)
        tooltip(f"Anki EDN : {len(notes)} carte(s) protégée(s) contre les mises à jour AnkiCollab.", period=3500)
    else:
        tooltip("Anki EDN : aucune nouvelle étiquette de protection nécessaire sur cette sélection.")
    return len(notes)


def _on_editor_did_unfocus_field(changed: bool, note_or_editor, current_field_idx: int) -> bool:
    """
    Real-time protection hook called when editing a field in the Anki Editor.
    Applies 'AnkiCollab_Protect::<Field>' immediately to protect the modified field.
    Strictly excludes personal fields such as 'Erreurs faites'.
    """
    if not changed:
        return changed

    from .shared_menu import is_protect_user_edits_enabled
    if not is_protect_user_edits_enabled():
        return changed

    try:
        editor = None
        if hasattr(note_or_editor, "note"):
            note = note_or_editor.note
            editor = note_or_editor
        else:
            note = note_or_editor

        if not note or not getattr(note, "id", None):
            return changed

        nt = note.note_type() if hasattr(note, "note_type") else None
        if not nt or "flds" not in nt:
            return changed

        flds = nt["flds"]
        if current_field_idx < 0 or current_field_idx >= len(flds):
            return changed

        fname = flds[current_field_idx].get("name", "")
        if is_excluded_field(fname):
            return changed

        if not is_edn_or_collab_note(note):
            return changed

        ptag = protect_tag(fname)
        if ptag not in note.tags:
            note.add_tag(ptag)
            if editor and hasattr(editor, "load_tags"):
                try:
                    editor.load_tags()
                except Exception:
                    pass
            elif hasattr(note_or_editor, "load_tags"):
                try:
                    note_or_editor.load_tags()
                except Exception:
                    pass
    except Exception as e:
        print(f"[field_protection] Erreur auto-protection champ: {e}")

    return changed


# 5. Unprotect / Accept Updates Operations
# -----------------------------------------------------------------------------

def accept_deck_updates_for_notes(parent, nids: Sequence[int]) -> None:
    """Drop protection tags on selected notes so official deck updates apply again."""
    if not mw or not mw.col:
        return
    col = mw.col
    notes = []
    for nid in nids:
        note = col.get_note(nid)
        if any(is_protect_tag(t) for t in note.tags):
            note.tags = [t for t in note.tags if not is_protect_tag(t)]
            notes.append(note)

    if not notes:
        return

    # Update baselines so the current text does not re-trigger immediate protection
    keys = get_subscribed_deck_hashes() or ["edn_main"]
    for key in keys:
        base = load_baseline(key)
        if base is not None:
            for note in notes:
                base[str(note.id)] = field_hashes("\x1f".join(note.fields))
            save_baseline(key, base)

    from aqt.operations.note import update_notes
    update_notes(parent=parent, notes=notes).run_in_background()
    tooltip(
        f"Anki EDN : {len(notes)} carte(s) accepteront de nouveau les mises à jour officielles.",
        period=3500
    )


# -----------------------------------------------------------------------------
# 6. Context Menus & Hooks Registration
# -----------------------------------------------------------------------------

def _on_browser_context_menu(browser, menu: QMenu) -> None:
    col = mw.col if mw else None
    if not col:
        return
    selected_nids = browser.selected_notes()
    if not selected_nids:
        return
    has_protected = False
    for nid in selected_nids:
        try:
            note = col.get_note(nid)
            if any(is_protect_tag(t) for t in note.tags):
                has_protected = True
                break
        except Exception:
            pass

    act_protect = QAction("Anki EDN : Protéger les champs de ces cartes", menu)
    act_protect.triggered.connect(lambda: protect_notes_manually(browser, selected_nids))
    menu.addAction(act_protect)

    if has_protected:
        act = QAction("Anki EDN : Accepter les mises à jour du paquet pour ces cartes", menu)
        act.triggered.connect(lambda: accept_deck_updates_for_notes(browser, selected_nids))
        menu.addAction(act)


def _on_reviewer_context_menu(reviewer, menu: QMenu) -> None:
    card = reviewer.card
    if not card or not mw or not mw.col:
        return
    try:
        note = mw.col.get_note(card.nid)
        if any(is_protect_tag(t) for t in note.tags):
            act = QAction("Anki EDN : Accepter les mises à jour du paquet pour cette carte", menu)
            act.triggered.connect(lambda: accept_deck_updates_for_notes(mw, [card.nid]))
            menu.addAction(act)
    except Exception:
        pass


def _on_profile_did_open() -> None:
    from .shared_menu import is_protect_user_edits_enabled
    if not is_protect_user_edits_enabled():
        return
    # Ensure baseline exists for all subscribed decks
    keys = get_subscribed_deck_hashes() or [None]
    missing = [k for k in keys if load_baseline(k or "edn_main") is None]
    if missing and mw:
        QueryOp(
            parent=mw,
            op=lambda col: [snapshot(k, col=col) for k in missing],
            success=lambda _r: None
        ).run_in_background()


_ankicollab_hooked = False

def _try_hook_ankicollab() -> bool:
    global _ankicollab_hooked
    if _ankicollab_hooked:
        return True
    if mw is None or getattr(mw, "pm", None) is None:
        return False
    try:
        import importlib
        im = importlib.import_module(f"{ADDON_ANKICOLLAB_ID}.import_manager")
        if not hasattr(im, "_install_deck_op") or getattr(im._install_deck_op, "_edn_hooked", False):
            return False

        orig_op = im._install_deck_op
        orig_done = getattr(im, "_on_deck_installed", None)

        def hooked_install_deck_op(deck, config, *args, **kwargs):
            deck_hash = getattr(config, "deck_hash", None)
            try:
                protect_edits(deck_hash)
            except Exception as e:
                print(f"[field_protection] Erreur protect_edits: {e}")
            try:
                return orig_op(deck, config, *args, **kwargs)
            finally:
                try:
                    snapshot(deck_hash)
                except Exception as e:
                    print(f"[field_protection] Erreur snapshot: {e}")

        hooked_install_deck_op._edn_hooked = True
        im._install_deck_op = hooked_install_deck_op

        if orig_done and not getattr(orig_done, "_edn_hooked", False):
            def hooked_done(res, deck, subscription, *args, **kwargs):
                try:
                    return orig_done(res, deck, subscription, *args, **kwargs)
                finally:
                    deck_hash = subscription.get("deck_hash") if isinstance(subscription, dict) else None
                    if deck_hash:
                        snapshot(deck_hash)
            hooked_done._edn_hooked = True
            im._on_deck_installed = hooked_done

        _ankicollab_hooked = True
        return True
    except Exception:
        return False


def init_field_protection_hooks() -> None:
    """Initialize field protection lifecycle hooks."""
    gui_hooks.profile_did_open.append(_on_profile_did_open)
    gui_hooks.browser_will_show_context_menu.append(_on_browser_context_menu)
    gui_hooks.reviewer_will_show_context_menu.append(_on_reviewer_context_menu)
    gui_hooks.editor_did_unfocus_field.append(_on_editor_did_unfocus_field)
    _try_hook_ankicollab()
