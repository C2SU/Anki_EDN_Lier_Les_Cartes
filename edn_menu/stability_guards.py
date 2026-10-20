# -*- coding: utf-8 -*-
"""
Anki EDN Stability & Anti-Reload Guards
Provides active protection against Anki reviewer reload races, accidental navigation
shortcuts, stuck AddCards dialogs, and blank WebEngine views across all EDN addons.
"""

import re
import sys


_in_next_card = False


def install_stability_guards() -> None:
    """Install all EDN stability guards idempotently."""
    # Use sys._edn_stability_guards_installed to guarantee singleton installation
    # even before mw is fully initialized or across multiple edn_menu copies.
    if getattr(sys, "_edn_stability_guards_installed", False):
        return
    sys._edn_stability_guards_installed = True

    _install_main_window_guard()
    _install_reviewer_guard()
    _install_webview_crash_sentinel()
    _install_addcards_guard()
    _install_card_watchdog()


# -----------------------------------------------------------------------------
# 1. Main Window Guard (onStudyKey)
# -----------------------------------------------------------------------------
def _install_main_window_guard() -> None:
    try:
        from aqt.main import AnkiQt
    except ImportError:
        return

    orig_onStudyKey = getattr(AnkiQt, "onStudyKey", None)
    if orig_onStudyKey and not hasattr(AnkiQt, "_cr_orig_onStudyKey"):
        AnkiQt._cr_orig_onStudyKey = orig_onStudyKey

        def patched_onStudyKey(self, *args, **kwargs):
            cur_state = getattr(self, "state", None)
            reviewer = getattr(self, "reviewer", None)
            card = getattr(reviewer, "card", None)

            # Block accidental or spurious "s" from dumping user to overview during active review
            if cur_state == "review" and card is not None:
                return

            try:
                return AnkiQt._cr_orig_onStudyKey(self)
            except TypeError:
                return AnkiQt._cr_orig_onStudyKey(self, *args, **kwargs)

        AnkiQt.onStudyKey = patched_onStudyKey


# -----------------------------------------------------------------------------
# 2. Reviewer Anti-Reload & Navigation Guard
# -----------------------------------------------------------------------------
def _install_reviewer_guard() -> None:
    try:
        from aqt.reviewer import Reviewer, RefreshNeeded
    except ImportError:
        try:
            from aqt.reviewer import Reviewer
            RefreshNeeded = None
        except ImportError:
            return

    # --- nextCard ---
    orig_nextCard = getattr(Reviewer, "nextCard", None)
    if orig_nextCard and not hasattr(Reviewer, "_cr_orig_nextCard"):
        Reviewer._cr_orig_nextCard = orig_nextCard

        def patched_nextCard(self, *args, **kwargs):
            global _in_next_card
            _in_next_card = True
            if RefreshNeeded is not None and getattr(self, "_refresh_needed", None) is getattr(RefreshNeeded, "QUEUES", None):
                self._refresh_needed = None
            try:
                return Reviewer._cr_orig_nextCard(self, *args, **kwargs)
            finally:
                _in_next_card = False

        Reviewer.nextCard = patched_nextCard

    # --- User-initiated reviewer actions (bury, suspend, forget, delete, undo) ---
    def _wrap_user_nav(method_name: str) -> None:
        orig_fn = getattr(Reviewer, method_name, None)
        if orig_fn and not hasattr(Reviewer, f"_cr_orig_{method_name}"):
            setattr(Reviewer, f"_cr_orig_{method_name}", orig_fn)

            def wrapped(self, *args, **kwargs):
                self._user_nav_refresh = True
                orig = getattr(Reviewer, f"_cr_orig_{method_name}")
                try:
                    return orig(self)
                except TypeError:
                    return orig(self, *args, **kwargs)

            setattr(Reviewer, method_name, wrapped)

    for _m in (
        "bury_current_card",
        "bury_current_note",
        "suspend_current_card",
        "suspend_current_note",
        "forget_current_card",
        "delete_current_note",
    ):
        _wrap_user_nav(_m)

    if hasattr(Reviewer, "bury_current_card"):
        Reviewer.onBuryCard = Reviewer.bury_current_card
    if hasattr(Reviewer, "bury_current_note"):
        Reviewer.onBuryNote = Reviewer.bury_current_note
    if hasattr(Reviewer, "suspend_current_note"):
        Reviewer.onSuspend = Reviewer.suspend_current_note
    if hasattr(Reviewer, "suspend_current_card"):
        Reviewer.onSuspendCard = Reviewer.suspend_current_card
    if hasattr(Reviewer, "delete_current_note"):
        Reviewer.onDelete = Reviewer.delete_current_note

    try:
        from aqt import gui_hooks

        def _mark_nav_from_hook(*args, **kwargs):
            from aqt import mw
            if mw and getattr(mw, "reviewer", None):
                mw.reviewer._user_nav_refresh = True

        gui_hooks.state_did_undo.append(_mark_nav_from_hook)
    except Exception:
        pass

    # --- refresh_if_needed (anti-reload guard) ---
    orig_refresh_if_needed = getattr(Reviewer, "refresh_if_needed", None)
    if orig_refresh_if_needed and not hasattr(Reviewer, "_cr_orig_refresh_if_needed"):
        Reviewer._cr_orig_refresh_if_needed = orig_refresh_if_needed

        def patched_refresh_if_needed(self, *args, **kwargs):
            card = getattr(self, "card", None)
            cur_state = getattr(self, "state", None)
            refresh_needed = getattr(self, "_refresh_needed", None)

            user_nav = getattr(self, "_user_nav_refresh", False)
            if user_nav:
                self._user_nav_refresh = False

            # Defer background RefreshNeeded.QUEUES nextCard() triggers while viewing an active card
            if (
                RefreshNeeded is not None
                and (cur_state == "transition" or card is not None or _in_next_card)
                and refresh_needed is getattr(RefreshNeeded, "QUEUES", None)
            ):
                if not user_nav:
                    self._refresh_needed = None
                    return

            try:
                return Reviewer._cr_orig_refresh_if_needed(self)
            except TypeError:
                return Reviewer._cr_orig_refresh_if_needed(self, *args, **kwargs)

        Reviewer.refresh_if_needed = patched_refresh_if_needed

    # --- _linkHandler (guard against duplicate "ans" calls) ---
    orig_linkHandler = getattr(Reviewer, "_linkHandler", None)
    if orig_linkHandler and not hasattr(Reviewer, "_cr_orig_linkHandler"):
        Reviewer._cr_orig_linkHandler = orig_linkHandler

        def patched_linkHandler(self, url, *args, **kwargs):
            cur_state = getattr(self, "state", None)
            if url == "ans" and cur_state == "answer":
                return
            return Reviewer._cr_orig_linkHandler(self, url, *args, **kwargs)

        Reviewer._linkHandler = patched_linkHandler


# -----------------------------------------------------------------------------
# 3. WebEngine Render Process Crash Sentinel
# -----------------------------------------------------------------------------
def _install_webview_crash_sentinel() -> None:
    try:
        from aqt.webview import AnkiWebPage

        orig_page_init = getattr(AnkiWebPage, "__init__", None)
        if orig_page_init and not hasattr(AnkiWebPage, "_cr_orig_page_init"):
            AnkiWebPage._cr_orig_page_init = orig_page_init

            def patched_page_init(self, *args, **kwargs):
                AnkiWebPage._cr_orig_page_init(self, *args, **kwargs)
                try:
                    def on_crashed(status, exit_code):
                        from aqt import mw
                        if mw and getattr(mw, "state", None) == "review" and getattr(mw, "reviewer", None):
                            from aqt.qt import QTimer
                            QTimer.singleShot(100, lambda: mw.reviewer._initWeb())

                    self.renderProcessTerminated.connect(on_crashed)
                except Exception:
                    pass

            AnkiWebPage.__init__ = patched_page_init
    except Exception:
        pass


# -----------------------------------------------------------------------------
# 4. AddCards Dialog Guard & Immediate Closer
# -----------------------------------------------------------------------------
def _install_addcards_guard() -> None:
    # A. Legacy AddCards Guard
    try:
        from aqt.addcards import AddCards
    except (ImportError, AttributeError):
        AddCards = None

    if AddCards:
        orig_ifCanClose = getattr(AddCards, "ifCanClose", None)
        if orig_ifCanClose and not hasattr(AddCards, "_cr_orig_ifCanClose"):
            AddCards._cr_orig_ifCanClose = orig_ifCanClose

            def patched_ifCanClose(self, onOk):
                editor = getattr(self, "editor", None)
                last_note = getattr(self, "_last_added_note", None)

                is_blank = True
                if editor and getattr(editor, "note", None):
                    try:
                        is_blank = editor.fieldsAreBlank(last_note)
                    except Exception:
                        is_blank = True

                if is_blank:
                    return onOk()

                has_executed = [False]

                def safe_afterSave():
                    if has_executed[0]:
                        return
                    has_executed[0] = True

                    try:
                        if editor and editor.fieldsAreBlank(last_note):
                            return onOk()
                    except Exception:
                        return onOk()

                    def dialog_cb(choice: int) -> None:
                        if choice == 0:
                            onOk()

                    try:
                        from aqt.utils import ask_user_dialog, tr
                        from aqt.qt import QMessageBox
                        ask_user_dialog(
                            tr.adding_discard_current_input(),
                            callback=dialog_cb,
                            buttons=[
                                QMessageBox.StandardButton.Discard,
                                (tr.adding_keep_editing(), QMessageBox.ButtonRole.RejectRole),
                            ],
                            parent=self,
                        )
                    except Exception:
                        onOk()

                try:
                    from aqt.qt import QTimer
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(safe_afterSave)
                    timer.start(300)
                except Exception:
                    pass

                try:
                    editor.call_after_note_saved(safe_afterSave)
                except Exception:
                    safe_afterSave()

            AddCards.ifCanClose = patched_ifCanClose

        orig_closeEvent = getattr(AddCards, "closeEvent", None)
        if orig_closeEvent and not hasattr(AddCards, "_cr_orig_closeEvent"):
            AddCards._cr_orig_closeEvent = orig_closeEvent

            def patched_closeEvent(self, evt):
                if getattr(self, "_close_event_has_cleaned_up", False):
                    if evt:
                        evt.accept()
                    return

                def on_can_close():
                    try:
                        self._close()
                    except Exception:
                        self._close_event_has_cleaned_up = True
                        try:
                            import aqt
                            aqt.dialogs.markClosed("AddCards")
                        except Exception:
                            pass
                        self.close()

                try:
                    self.ifCanClose(on_can_close)
                except Exception:
                    on_can_close()

                if evt:
                    evt.ignore()

            AddCards.closeEvent = patched_closeEvent

    # B. Modern Svelte NewAddCards Guard
    try:
        from aqt.addcards import NewAddCards
    except (ImportError, AttributeError):
        NewAddCards = None

    def _is_new_addcards_blank(win) -> bool:
        try:
            editor = getattr(win, "editor", None)
            if not editor:
                return True
            note = getattr(editor, "note", None)
            if not note or not hasattr(note, "fields"):
                return True
            for val in note.fields:
                if not val:
                    continue
                clean = (
                    val.replace("<br>", "")
                    .replace("<br/>", "")
                    .replace("<br />", "")
                    .replace("<div><br></div>", "")
                    .strip()
                )
                if clean:
                    return False
            return True
        except Exception:
            return True

    if NewAddCards:
        orig_new_close = getattr(NewAddCards, "_close", None)
        if orig_new_close and not hasattr(NewAddCards, "_cr_orig_close"):
            NewAddCards._cr_orig_close = orig_new_close

            def patched_new_close(self):
                if getattr(self, "_close_event_has_cleaned_up", False):
                    self.close()
                    return
                try:
                    if hasattr(self, "editor") and self.editor:
                        self.editor.cleanup()
                except Exception:
                    pass
                try:
                    if hasattr(self, "mw") and self.mw:
                        self.mw.maybeReset()
                except Exception:
                    pass
                try:
                    from aqt.utils import saveGeom
                    saveGeom(self, "add")
                except Exception:
                    pass
                try:
                    import aqt
                    aqt.dialogs.markClosed("NewAddCards")
                    aqt.dialogs.markClosed("AddCards")
                except Exception:
                    pass
                self._close_event_has_cleaned_up = True
                try:
                    if hasattr(self, "mw") and self.mw:
                        self.mw.deferred_delete_and_garbage_collect(self)
                except Exception:
                    pass
                try:
                    self.close()
                except Exception:
                    pass

            NewAddCards._close = patched_new_close

        orig_new_ifCanClose = getattr(NewAddCards, "ifCanClose", None)
        if orig_new_ifCanClose and not hasattr(NewAddCards, "_cr_orig_ifCanClose"):
            NewAddCards._cr_orig_ifCanClose = orig_new_ifCanClose

            def patched_new_ifCanClose(self, onOk):
                if _is_new_addcards_blank(self):
                    return onOk()

                has_executed = [False]

                def safe_onOk():
                    if not has_executed[0]:
                        has_executed[0] = True
                        onOk()

                self._close_callback = safe_onOk

                try:
                    from aqt.qt import QTimer
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(safe_onOk)
                    timer.start(350)
                except Exception:
                    pass

                try:
                    web = getattr(getattr(self, "editor", None), "web", None)
                    if web:
                        web.eval("closeAddCards()")
                    else:
                        safe_onOk()
                except Exception:
                    safe_onOk()

            NewAddCards.ifCanClose = patched_new_ifCanClose

        orig_new_closeEvent = getattr(NewAddCards, "closeEvent", None)
        if orig_new_closeEvent and not hasattr(NewAddCards, "_cr_orig_closeEvent"):
            NewAddCards._cr_orig_closeEvent = orig_new_closeEvent

            def patched_new_closeEvent(self, evt):
                if getattr(self, "_close_event_has_cleaned_up", False):
                    if evt:
                        evt.accept()
                    return

                has_closed = [False]

                def on_can_close():
                    if not has_closed[0]:
                        has_closed[0] = True
                        try:
                            self._close()
                        except Exception:
                            self._close_event_has_cleaned_up = True
                            try:
                                import aqt
                                aqt.dialogs.markClosed("NewAddCards")
                                aqt.dialogs.markClosed("AddCards")
                            except Exception:
                                pass
                            try:
                                self.close()
                            except Exception:
                                pass

                if _is_new_addcards_blank(self):
                    on_can_close()
                    if evt:
                        evt.accept()
                    return

                try:
                    from aqt.qt import QTimer
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(on_can_close)
                    timer.start(400)
                except Exception:
                    pass

                try:
                    self.ifCanClose(on_can_close)
                except Exception:
                    on_can_close()

                if evt:
                    evt.ignore()

            NewAddCards.closeEvent = patched_new_closeEvent

        orig_new_discard = getattr(NewAddCards, "_close_if_user_wants_to_discard_changes", None)
        if orig_new_discard and not hasattr(NewAddCards, "_cr_orig_discard"):
            NewAddCards._cr_orig_discard = orig_new_discard

            def patched_new_discard(self, prompt: bool) -> None:
                if not prompt or _is_new_addcards_blank(self):
                    cb = getattr(self, "_close_callback", None)
                    if callable(cb):
                        cb()
                    else:
                        self._close()
                    return

                def dialog_cb(choice: int) -> None:
                    if choice == 0:
                        cb = getattr(self, "_close_callback", None)
                        if callable(cb):
                            cb()
                        else:
                            self._close()

                try:
                    from aqt.utils import ask_user_dialog, tr
                    from aqt.qt import QMessageBox
                    ask_user_dialog(
                        tr.adding_discard_current_input(),
                        callback=dialog_cb,
                        buttons=[
                            QMessageBox.StandardButton.Discard,
                            (tr.adding_keep_editing(), QMessageBox.ButtonRole.RejectRole),
                        ],
                        parent=self,
                    )
                except Exception:
                    cb = getattr(self, "_close_callback", None)
                    if callable(cb):
                        cb()
                    else:
                        self._close()

            NewAddCards._close_if_user_wants_to_discard_changes = patched_new_discard

        orig_new_keyPressEvent = getattr(NewAddCards, "keyPressEvent", None)
        if orig_new_keyPressEvent and not hasattr(NewAddCards, "_cr_orig_keyPressEvent"):
            NewAddCards._cr_orig_keyPressEvent = orig_new_keyPressEvent

            def patched_new_keyPressEvent(self, evt):
                from aqt.qt import Qt
                if evt.key() == Qt.Key.Key_Escape:
                    if _is_new_addcards_blank(self):
                        try:
                            self._close()
                            return
                        except Exception:
                            pass
                    self.close()
                    return
                orig_new_keyPressEvent(self, evt)

            NewAddCards.keyPressEvent = patched_new_keyPressEvent

    # C. Mediasrv closeAddCards Endpoint Fallback
    try:
        from aqt import mediasrv
        orig_mediasrv_close = getattr(mediasrv, "close_add_cards", None)
        if orig_mediasrv_close and not hasattr(mediasrv, "_cr_orig_close_add_cards"):
            mediasrv._cr_orig_close_add_cards = orig_mediasrv_close

            def patched_mediasrv_close_add_cards():
                try:
                    from anki import generic_pb2
                    from flask import request
                    from aqt.qt import QTimer, QApplication
                    from aqt.addcards import NewAddCards
                    import aqt

                    req = generic_pb2.Bool()
                    req.ParseFromString(request.data)

                    def handle_on_main():
                        window = aqt.dialogs.getInstance(NewAddCards.__name__)
                        if not window:
                            try:
                                for w in QApplication.topLevelWidgets():
                                    if isinstance(w, NewAddCards) and w.isVisible():
                                        window = w
                                        break
                            except Exception:
                                pass
                        if window:
                            window._close_if_user_wants_to_discard_changes(req.val)

                    aqt.mw.taskman.run_on_main(lambda: QTimer.singleShot(0, handle_on_main))
                    return b""
                except Exception:
                    return orig_mediasrv_close()

            mediasrv.close_add_cards = patched_mediasrv_close_add_cards
            if hasattr(mediasrv, "post_handlers") and "closeAddCards" in mediasrv.post_handlers:
                mediasrv.post_handlers["closeAddCards"] = patched_mediasrv_close_add_cards
    except Exception:
        pass


# -----------------------------------------------------------------------------
# 5. Card Watchdog & Desktop Script Sanitizer
# -----------------------------------------------------------------------------
WATCHDOG_SCRIPT = """
<script>
(function() {
    if (window._ednCardWatchdogInstalled) return;
    window._ednCardWatchdogInstalled = true;

    function triggerRepaintNeeded() {
        try {
            if (document.body) {
                void document.body.offsetHeight;
            }
        } catch(e) {}
    }

    function initQaWatchdog() {
        var qa = document.getElementById('qa');
        if (!qa) return;

        var opTimer = null;
        var opObserver = new MutationObserver(function() {
            if (qa.style.opacity === '0') {
                if (opTimer) clearTimeout(opTimer);
                opTimer = setTimeout(function() {
                    if (qa.style.opacity === '0') {
                        qa.style.opacity = '1';
                        triggerRepaintNeeded();
                    }
                }, 350);
            } else if (qa.style.opacity === '1') {
                if (opTimer) { clearTimeout(opTimer); opTimer = null; }
                triggerRepaintNeeded();
            }
        });
        opObserver.observe(qa, { attributes: true, attributeFilter: ['style'] });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initQaWatchdog);
    } else {
        initQaWatchdog();
    }
})();
</script>
"""


def _install_card_watchdog() -> None:
    try:
        from aqt import gui_hooks

        def _sanitize_card_html(text: str, card, kind: str) -> str:
            if "_edn_prefs.js" in text:
                text = re.sub(
                    r'<script\s+src=["\']_edn_prefs\.js["\']\s*>\s*</script>',
                    "",
                    text,
                    flags=re.IGNORECASE,
                )
            return text

        def _inject_watchdog(web_content, context) -> None:
            try:
                from aqt.reviewer import Reviewer
                if isinstance(context, Reviewer):
                    if "_ednCardWatchdogInstalled" not in web_content.head:
                        web_content.head += WATCHDOG_SCRIPT
            except Exception:
                pass

        gui_hooks.card_will_show.append(_sanitize_card_html)
        gui_hooks.webview_will_set_content.append(_inject_watchdog)
    except Exception:
        pass
