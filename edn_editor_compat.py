"""
edn_editor_compat.py — Fonctions d'aide unifiées pour la compatibilité
entre l'ancien éditeur Anki (aqt.editor.Editor) et le nouvel éditeur Svelte (aqt.editor.NewEditor).
"""

from typing import Any, Callable, Optional
import aqt
from aqt import mw

try:
    from aqt.qt import QTimer, sip
except Exception:
    QTimer = None
    sip = None


def get_editor_note(editor: Any) -> Optional[Any]:
    """
    Extrait de manière sécurisée la note courante de l'éditeur.
    Compatible avec Editor (editor.note direct) et NewEditor (Svelte, via editor.nid).
    """
    if not editor:
        return None
    note = getattr(editor, "note", None)
    if note is not None:
        return note
    nid = getattr(editor, "nid", None)
    if nid:
        col = None
        if "mw" in globals() and hasattr(mw, "col") and mw.col:
            col = mw.col
        elif getattr(editor, "mw", None) and hasattr(editor.mw, "col") and editor.mw.col:
            col = editor.mw.col
        elif getattr(aqt, "mw", None) and hasattr(aqt.mw, "col") and aqt.mw.col:
            col = aqt.mw.col
        if col:
            try:
                return col.get_note(int(nid))
            except Exception:
                return None
    return None


def editor_save_now(editor: Any, callback: Optional[Callable[[], None]] = None, keep_focus: bool = False) -> None:
    """
    Sauvegarde la note courante dans l'éditeur de manière asynchrone et appelle le callback.
    Supporte 'call_after_note_saved' (Svelte / Anki moderne) et 'saveNow' (Legacy).
    Gère les cas limites où le webview est détruit ou la page n'est pas encore prête.
    """
    if not editor:
        if callback:
            if QTimer:
                QTimer.singleShot(0, callback)
            else:
                callback()
        return

    # Vérification si le webview est détruit
    wv = getattr(editor, "web", None)
    if wv and sip:
        try:
            if sip.isdeleted(wv):
                if callback:
                    if QTimer:
                        QTimer.singleShot(0, callback)
                    else:
                        callback()
                return
        except Exception:
            pass

    def _safe_cb():
        if callback:
            try:
                callback()
            except Exception as e:
                print(f"[edn_editor_compat] callback error: {e}")

    # Préférer call_after_note_saved si présent
    if hasattr(editor, "call_after_note_saved") and callable(getattr(editor, "call_after_note_saved")):
        try:
            editor.call_after_note_saved(_safe_cb, keep_focus)
            return
        except TypeError:
            try:
                editor.call_after_note_saved(_safe_cb)
                return
            except Exception:
                pass
        except Exception:
            pass

    # Fallback saveNow
    if hasattr(editor, "saveNow") and callable(getattr(editor, "saveNow")):
        try:
            editor.saveNow(_safe_cb, keep_focus)
            return
        except TypeError:
            try:
                editor.saveNow(_safe_cb)
                return
            except Exception:
                pass
        except Exception:
            pass

    # Si aucun mécanisme n'est disponible
    if callback:
        if QTimer:
            QTimer.singleShot(0, _safe_cb)
        else:
            _safe_cb()


def editor_reload_note(editor: Any, keep_focus: bool = True) -> None:
    """
    Recharge la note courante dans l'éditeur.
    Compatible Svelte (reload_note) et Legacy (loadNoteKeepingFocus / loadNote).
    """
    if not editor:
        return

    if keep_focus:
        if hasattr(editor, "loadNoteKeepingFocus") and callable(getattr(editor, "loadNoteKeepingFocus")):
            try:
                editor.loadNoteKeepingFocus()
                return
            except Exception:
                pass
        if hasattr(editor, "load_note_keeping_focus") and callable(getattr(editor, "load_note_keeping_focus")):
            try:
                editor.load_note_keeping_focus()
                return
            except Exception:
                pass

    if hasattr(editor, "reload_note") and callable(getattr(editor, "reload_note")):
        try:
            editor.reload_note()
            return
        except Exception:
            pass

    if hasattr(editor, "loadNote") and callable(getattr(editor, "loadNote")):
        try:
            editor.loadNote()
            return
        except Exception:
            pass

    if hasattr(editor, "load_note") and callable(getattr(editor, "load_note")):
        try:
            editor.load_note()
            return
        except Exception:
            pass
