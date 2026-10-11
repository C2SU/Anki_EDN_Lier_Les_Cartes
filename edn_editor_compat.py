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


def _profiler_log(category: str, message: str, elapsed_ms: Optional[float] = None, caller: Optional[str] = None):
    try:
        import sys
        profiler = sys.modules.get("00_EDN_Debug_Profiler")
        if profiler and hasattr(profiler, "log"):
            profiler.log(category, message, elapsed_ms=elapsed_ms, caller=caller or "edn_editor_compat")
            return
    except Exception:
        pass
    try:
        from .logger import log
        ms_str = f" | took {elapsed_ms:.2f}ms" if elapsed_ms is not None else ""
        c_str = f" [{caller}]" if caller else ""
        log(f"[{category}]{c_str} {message}{ms_str}")
    except Exception:
        pass


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


def editor_save_now(editor: Any, callback: Optional[Callable[[], None]] = None, keep_focus: bool = True) -> None:
    """
    Sauvegarde la note courante dans l'éditeur de manière asynchrone et appelle le callback.
    Supporte 'call_after_note_saved' (Svelte / Anki moderne) et 'saveNow' (Legacy).
    Gère les cas limites où le webview est détruit ou la page n'est pas encore prête.
    """
    caller = "edn_editor_compat:editor_save_now"
    nid = getattr(editor, "nid", None) or getattr(getattr(editor, "note", None), "id", "none")
    if not editor:
        _profiler_log("EDITOR:SAVE_NOW", f"editor is None (nid={nid})", caller=caller)
        if callback:
            if QTimer:
                QTimer.singleShot(0, callback)
            else:
                callback()
        return

    # Vérification si le webview est détruit ou absent
    wv = getattr(editor, "web", None)
    is_deleted = False
    if wv and sip:
        try:
            is_deleted = sip.isdeleted(wv)
        except Exception:
            is_deleted = True
    if not wv or is_deleted:
        _profiler_log("EDITOR:SAVE_NOW", f"webview missing/deleted (nid={nid}) -> executing callback directly", caller=caller)
        if callback:
            if QTimer:
                QTimer.singleShot(0, callback)
            else:
                callback()
        return

    def _safe_cb():
        _profiler_log("EDITOR:SAVE_NOW", f"callback triggering for nid={nid}", caller=caller)
        if callback:
            try:
                callback()
            except Exception as e:
                _profiler_log("EDITOR:SAVE_NOW", f"callback error for nid={nid}: {e}", caller=caller)
                print(f"[edn_editor_compat] callback error: {e}")

    # Préférer call_after_note_saved si présent
    if hasattr(editor, "call_after_note_saved") and callable(getattr(editor, "call_after_note_saved")):
        _profiler_log("EDITOR:SAVE_NOW", f"dispatching via editor.call_after_note_saved(keepFocus={keep_focus}, nid={nid})", caller=caller)
        try:
            editor.call_after_note_saved(_safe_cb, keep_focus)
            return
        except TypeError:
            try:
                editor.call_after_note_saved(_safe_cb)
                return
            except Exception as e:
                _profiler_log("EDITOR:SAVE_NOW", f"call_after_note_saved exception: {e}", caller=caller)
        except Exception as e:
            _profiler_log("EDITOR:SAVE_NOW", f"call_after_note_saved exception: {e}", caller=caller)

    # Fallback saveNow
    if hasattr(editor, "saveNow") and callable(getattr(editor, "saveNow")):
        _profiler_log("EDITOR:SAVE_NOW", f"dispatching via editor.saveNow(keepFocus={keep_focus}, nid={nid})", caller=caller)
        try:
            editor.saveNow(_safe_cb, keep_focus)
            return
        except TypeError:
            try:
                editor.saveNow(_safe_cb)
                return
            except Exception as e:
                _profiler_log("EDITOR:SAVE_NOW", f"saveNow exception: {e}", caller=caller)
        except Exception as e:
            _profiler_log("EDITOR:SAVE_NOW", f"saveNow exception: {e}", caller=caller)

    # Si aucun mécanisme n'est disponible
    _profiler_log("EDITOR:SAVE_NOW", f"no save mechanism found on editor (nid={nid}) -> singleShot callback", caller=caller)
    if callback:
        if QTimer:
            QTimer.singleShot(0, _safe_cb)
        else:
            _safe_cb()


def editor_reload_note(editor: Any, keep_focus: bool = True, note: Any = None, target_fidx: Any = None) -> None:
    """
    Recharge la note courante dans l'editeur.
    Compatible Svelte (reload_note / reloadNote JS / setFields) et Legacy (loadNoteKeepingFocus / loadNote).
    """
    caller = "edn_editor_compat:editor_reload_note"
    if not editor:
        _profiler_log("EDITOR:RELOAD", "editor is None -> aborted", caller=caller)
        return

    nid = getattr(editor, "nid", None)
    if not nid and hasattr(editor, "note") and getattr(editor.note, "id", None):
        nid = editor.note.id
    if not nid and note and getattr(note, "id", None):
        nid = note.id

    cur_fld = getattr(editor, "currentField", None)
    last_fld = getattr(editor, "last_field_index", None)
    _profiler_log("EDITOR:RELOAD", f"START reload_note: nid={nid}, currentField={cur_fld}, last_field={last_fld}, target_fidx={target_fidx}, keep_focus={keep_focus}", caller=caller)

    # Recuperer la note fraiche
    fresh_note = note
    try:
        col = None
        if getattr(editor, "mw", None) and getattr(editor.mw, "col", None):
            col = editor.mw.col
        elif "mw" in globals() and hasattr(mw, "col") and mw.col:
            col = mw.col
        if not fresh_note and nid and col:
            fresh_note = col.get_note(int(nid))
        elif fresh_note and col and getattr(fresh_note, "id", 0) != 0:
            try:
                db_note = col.get_note(int(fresh_note.id))
                if db_note:
                    fresh_note = db_note
            except Exception:
                pass

        if fresh_note:
            f_preview = [f[:30] for f in fresh_note.fields]
            _profiler_log("EDITOR:RELOAD", f"fresh_note loaded: id={fresh_note.id}, num_fields={len(fresh_note.fields)}, previews={f_preview}", caller=caller)
            if hasattr(editor, "_note_compat_cache"):
                editor._note_compat_cache = fresh_note
            try:
                is_property = isinstance(type(editor).note, property)
            except AttributeError:
                is_property = False
            if hasattr(editor, "note") and not is_property:
                editor.note = fresh_note
            elif hasattr(editor, "note"):
                try:
                    editor.note = fresh_note
                except Exception as e:
                    _profiler_log("EDITOR:RELOAD", f"could not assign editor.note property: {e}", caller=caller)
    except Exception as e:
        _profiler_log("EDITOR:RELOAD", f"Error updating fresh note in memory: {e}", caller=caller)

    # Rechargement Svelte (NewEditor) via Webview avec synchronisation des stores et protection anti-ecrasement
    if hasattr(editor, "web") and editor.web:
        _profiler_log("EDITOR:RELOAD", "evaluating reload in webview with store sync and guard bypass", caller=caller)

        field_names = []
        field_vals = []
        if fresh_note and hasattr(fresh_note, "fields"):
            field_vals = list(fresh_note.fields)
            try:
                nt = fresh_note.note_type() if callable(getattr(fresh_note, "note_type", None)) else None
                if nt and 'flds' in nt:
                    field_names = [f['name'] for f in nt['flds']]
            except Exception:
                pass

        target_idx = -1
        if target_fidx is not None and isinstance(target_fidx, int) and target_fidx >= 0:
            target_idx = target_fidx
        elif cur_fld is not None and isinstance(cur_fld, int) and cur_fld >= 0:
            target_idx = cur_fld
        elif last_fld is not None and isinstance(last_fld, int) and last_fld >= 0:
            target_idx = last_fld

        import json
        names_js = json.dumps(field_names)
        vals_js = json.dumps(field_vals)
        target_idx_js = str(target_idx)
        keep_focus_js = "true" if keep_focus else "false"

        def _after_js_reload(res):
            _profiler_log("EDITOR:RELOAD", f"JS reload callback response: {res!r}", caller=caller)
            if isinstance(res, dict) and (res.get("ran") or res.get("setFields")):
                _profiler_log("EDITOR:RELOAD", f"SUCCESS via JS (setFields={res.get('setFields')}, ran={res.get('ran')})", caller=caller)
                return
            _profiler_log("EDITOR:RELOAD", f"JS reload incomplete -> falling back to Python reload: {res!r}", caller=caller)
            _do_python_reload(editor, keep_focus, cur_fld, last_fld, caller)

        js_code = f"""(function() {{
            var result = {{ ran: false, setFields: false, error: null }};
            try {{
                window._ednBlockUpdateNotes = true;
                window._ednForceReload = true;

                var fldNames = {names_js};
                var fldVals = {vals_js};
                var targetIdx = {target_idx_js};
                var keepFocus = {keep_focus_js};

                // 1. Mise a jour immediate des stores Svelte et ProseMirror
                if (typeof globalThis.setFields === 'function' && Array.isArray(fldNames) && Array.isArray(fldVals) && fldVals.length > 0) {{
                    try {{
                        globalThis.setFields(fldNames, fldVals);
                        result.setFields = true;
                    }} catch(e) {{
                        result.error = 'setFields error: ' + String(e);
                    }}
                }}

                // 2. Declencher le rechargement Svelte uniquement si setFields n'est pas disponible.
                // Si setFields a reussi, les stores Svelte sont deja corrects. Appeler reloadNote()
                // en parallele provoquerait un conflit avec le reload interne declenche par
                // onOperationDidExecute (via col.update_note), causant un DOMException AbortError
                // qui fait recharger Svelte depuis l'etat reseau obsolete, ecrasant le lien insere.
                var reloadPromise = null;
                if (!result.setFields) {{
                    // setFields absent ou echoue : on doit recharger explicitement
                    if (typeof globalThis._ednRealReloadNote === 'function') {{
                        reloadPromise = globalThis._ednRealReloadNote();
                        result.ran = true;
                    }} else if (typeof window.reloadNote === 'function') {{
                        reloadPromise = window.reloadNote();
                        result.ran = true;
                    }}
                }} else {{
                    // setFields a reussi : pas besoin de reloadNote, juste restaurer le focus
                    result.ran = true;
                }}

                if (reloadPromise && typeof reloadPromise.then === 'function') {{
                    reloadPromise.then(function() {{
                        if (keepFocus && targetIdx >= 0 && typeof globalThis.focusField === 'function') {{
                            try {{ globalThis.focusField(targetIdx); }} catch(e) {{}}
                        }}
                    }}).catch(function(e) {{
                        console.error('[EDN] reloadPromise error:', e);
                    }});
                }} else {{
                    if (keepFocus && targetIdx >= 0 && typeof globalThis.focusField === 'function') {{
                        try {{ globalThis.focusField(targetIdx); }} catch(e) {{}}
                    }}
                }}
            }} catch(e) {{
                result.error = String(e);
            }} finally {{
                setTimeout(function() {{
                    window._ednBlockUpdateNotes = false;
                    window._ednForceReload = false;
                }}, 500);
            }}
            return result;
        }})();"""

        try:
            editor.web.evalWithCallback(js_code, _after_js_reload)
            return
        except Exception as e:
            _profiler_log("EDITOR:RELOAD", f"web.evalWithCallback reload failed: {e}", caller=caller)

    _do_python_reload(editor, keep_focus, cur_fld, last_fld, caller)


def _do_python_reload(editor: Any, keep_focus: bool, cur_fld: Any, last_fld: Any, caller: str) -> None:
    """Tentative de rechargement Python direct (load_note / reload_note / legacy)."""
    if hasattr(editor, "reload_note") and callable(getattr(editor, "reload_note")):
        try:
            _profiler_log("EDITOR:RELOAD", "calling editor.reload_note()", caller=caller)
            editor.reload_note()
            _profiler_log("EDITOR:RELOAD", "SUCCESS via editor.reload_note()", caller=caller)
            return
        except Exception as e:
            _profiler_log("EDITOR:RELOAD", f"editor.reload_note() failed: {e}", caller=caller)

    if hasattr(editor, "load_note") and callable(getattr(editor, "load_note")):
        target_f = cur_fld if cur_fld is not None else last_fld
        try:
            _profiler_log("EDITOR:RELOAD", f"calling editor.load_note(focus_to={target_f})", caller=caller)
            editor.load_note(focus_to=target_f)
            _profiler_log("EDITOR:RELOAD", f"SUCCESS via editor.load_note(focus_to={target_f})", caller=caller)
            return
        except Exception as e:
            _profiler_log("EDITOR:RELOAD", f"load_note failed: {e}", caller=caller)

    _do_legacy_reload(editor, keep_focus, cur_fld, last_fld, caller)


def _do_legacy_reload(editor: Any, keep_focus: bool, cur_fld: Any, last_fld: Any, caller: str) -> None:
    """Fallback reload pour l'éditeur legacy (loadNoteKeepingFocus / loadNote)."""
    if keep_focus:
        if hasattr(editor, "loadNoteKeepingFocus") and callable(getattr(editor, "loadNoteKeepingFocus")):
            try:
                _profiler_log("EDITOR:RELOAD", f"calling editor.loadNoteKeepingFocus() [currentField={cur_fld}, last_field={last_fld}]", caller=caller)
                editor.loadNoteKeepingFocus()
                _profiler_log("EDITOR:RELOAD", "SUCCESS via editor.loadNoteKeepingFocus()", caller=caller)
                return
            except Exception as e:
                _profiler_log("EDITOR:RELOAD", f"loadNoteKeepingFocus failed: {e}", caller=caller)

        if hasattr(editor, "load_note_keeping_focus") and callable(getattr(editor, "load_note_keeping_focus")):
            try:
                _profiler_log("EDITOR:RELOAD", "calling editor.load_note_keeping_focus()", caller=caller)
                editor.load_note_keeping_focus()
                _profiler_log("EDITOR:RELOAD", "SUCCESS via editor.load_note_keeping_focus()", caller=caller)
                return
            except Exception as e:
                _profiler_log("EDITOR:RELOAD", f"load_note_keeping_focus failed: {e}", caller=caller)

        if hasattr(editor, "load_note") and callable(getattr(editor, "load_note")):
            target_f = cur_fld if cur_fld is not None else last_fld
            if target_f is not None:
                try:
                    _profiler_log("EDITOR:RELOAD", f"calling editor.load_note(focus_to={target_f})", caller=caller)
                    editor.load_note(focus_to=target_f)
                    _profiler_log("EDITOR:RELOAD", f"SUCCESS via editor.load_note(focus_to={target_f})", caller=caller)
                    return
                except Exception as e:
                    _profiler_log("EDITOR:RELOAD", f"load_note(focus_to={target_f}) failed: {e}", caller=caller)

    if hasattr(editor, "loadNote") and callable(getattr(editor, "loadNote")):
        try:
            target_f = cur_fld if cur_fld is not None else last_fld
            _profiler_log("EDITOR:RELOAD", f"calling editor.loadNote(focusTo={target_f})", caller=caller)
            editor.loadNote(focusTo=target_f)
            _profiler_log("EDITOR:RELOAD", "SUCCESS via editor.loadNote()", caller=caller)
            return
        except Exception as e:
            _profiler_log("EDITOR:RELOAD", f"loadNote failed: {e}", caller=caller)

    if hasattr(editor, "load_note") and callable(getattr(editor, "load_note")):
        try:
            _profiler_log("EDITOR:RELOAD", "calling fallback editor.load_note()", caller=caller)
            editor.load_note()
            _profiler_log("EDITOR:RELOAD", "SUCCESS via fallback editor.load_note()", caller=caller)
            return
        except Exception as e:
            _profiler_log("EDITOR:RELOAD", f"fallback load_note failed: {e}", caller=caller)

    _profiler_log("EDITOR:RELOAD", "WARNING: No reload method succeeded on editor", caller=caller)

