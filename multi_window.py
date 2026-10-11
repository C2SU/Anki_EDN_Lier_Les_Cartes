from inspect import stack

import aqt
try:
    from PyQt6 import sip
except ImportError:
    import sip
from anki.hooks import remHook
from aqt import DialogManager, mw
from aqt.editcurrent import EditCurrent

if not getattr(DialogManager, "_multi_window_patched", False):
    DialogManager._multi_window_patched = True

    def shouldBeMultiple(name):
        """Whether window name may have multiple copy.

        Ensure that ["multiple"] exsits in the configuration file. The default value being True.
        """
        userOption = mw.addonManager.getConfig(__name__)
        if not userOption:
            userOption = {}
            
        if "multiple" not in userOption:
            userOption["multiple"] = {"default": True}
        multipleOption = userOption["multiple"]
        if name in multipleOption:
            return multipleOption[name]
        elif "default" in multipleOption:
            return multipleOption["default"]
        else:
            return True

    DialogManager._openDialogs = list()

    def _clean_dead_references(manager):
        clean_open = []
        for inst in manager._openDialogs:
            try:
                if inst is not None and not sip.isdeleted(inst):
                    clean_open.append(inst)
            except Exception:
                pass
        manager._openDialogs = clean_open

        if hasattr(manager, "_dialogs"):
            for name, entry in list(manager._dialogs.items()):
                try:
                    inst = entry[1]
                    if inst is not None and sip.isdeleted(inst):
                        entry[1] = None
                except Exception:
                    entry[1] = None

    # init
    old_init = DialogManager.__init__

    def __init__(self, oldDialog=None):
        if oldDialog is not None:
            DialogManagerMultiple._dialogs = oldDialog._dialogs
        old_init(self)
    DialogManager.__init__ = __init__

    # open
    DialogManager.old_open = DialogManager.open
    def open(self, name, *args, **kwargs):
        """Open a new window, with name and args.

        Or reopen the window name, if it should be single in the
        config, and is already opened.
        """
        function = self.openMany if shouldBeMultiple(name) else self.old_open
        return function(name, *args, **kwargs)
    DialogManager.open = open

    # openMany
    def openMany(self, name, *args, **kwargs):
        """Open a new window whose kind is name.

        keyword arguments:
        args -- values passed to the opener.
        name -- the name of the window to open
        """
        if name not in self._dialogs:
            # Fallback for unknown dialogs
            return self.old_open(name, *args, **kwargs)
            
        (creator, _) = self._dialogs[name]
        instance = creator(*args, **kwargs)
        self._openDialogs.append(instance)
        if name in ("EditCurrent", "NewEditCurrent") and instance:
            try:
                try:
                    from .editor_assistant import install_editcurrent_escape_shortcut, refocus_editor_field
                except ImportError:
                    from Anki_EDN_Lier_les_cartes.editor_assistant import install_editcurrent_escape_shortcut, refocus_editor_field
                install_editcurrent_escape_shortcut(instance)
                if hasattr(instance, "editor"):
                    from aqt.qt import QTimer
                    QTimer.singleShot(150, lambda: refocus_editor_field(instance.editor, 0))
                    QTimer.singleShot(350, lambda: refocus_editor_field(instance.editor, 0))
            except Exception:
                pass
        return instance
    DialogManager.openMany = openMany

    # markClosedMultiple
    def markClosedMultiple(self):
        try:
            caller = stack()[2].frame.f_locals['self']
            if caller in self._openDialogs:
                self._openDialogs.remove(caller)
        except Exception:
            pass
    DialogManager.markClosedMultiple = markClosedMultiple

    # markClosed
    old_markClosed = DialogManager.markClosed
    def markClosed(self, name):
        """Remove the window of windowName from the set of windows. """
        if shouldBeMultiple(name):
            self.markClosedMultiple()
        try:
            old_markClosed(self, name)
        except Exception:
            pass
        if hasattr(self, "_dialogs") and name in self._dialogs:
            self._dialogs[name][1] = None
    DialogManager.markClosed = markClosed

    # allClosed
    old_allClosed = DialogManager.allClosed
    def allClosed(self):
        """
        Whether all windows (except the main window) are marked as
        closed.
        """
        _clean_dead_references(self)
        try:
            return self._openDialogs == [] and old_allClosed(self)
        except Exception:
            return self._openDialogs == []
    DialogManager.allClosed = allClosed

    # closeAll
    old_closeAll = DialogManager.closeAll
    def closeAll(self, onsuccess):
        """Close all windows (except the main one). Call onsuccess when it's done.

        Return True if some window needed closing.
        None otherwise

        Keyword arguments:
        onsuccess -- the function to call when the last window is closed.
        """
        _clean_dead_references(self)
        def callback():
            """Call onsuccess if all window (except main) are closed."""
            if self.allClosed():
                onsuccess()
            else:
                # still waiting for others to close
                pass
        if self.allClosed():
            onsuccess()
            return

        closed_instances = set()
        for instance in list(self._openDialogs):
            try:
                if instance is not None and not sip.isdeleted(instance):
                    closed_instances.add(id(instance))
                    if getattr(instance, "silentlyClose", False):
                        instance.close()
                        callback()
                    else:
                        try:
                            instance.closeWithCallback(callback)
                        except AttributeError:
                            instance.close()
                            callback()
            except Exception:
                pass

        _clean_dead_references(self)
        if hasattr(self, "_dialogs"):
            for name, entry in list(self._dialogs.items()):
                inst = entry[1]
                if inst is not None and id(inst) in closed_instances:
                    entry[1] = None
        return old_closeAll(self, onsuccess)
    DialogManager.closeAll = closeAll
