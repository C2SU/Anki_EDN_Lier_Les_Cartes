import os
import sys
import types
from unittest.mock import MagicMock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Ensure addons21 root is on sys.path so relative imports work inside Anki_EDN_Lier_les_cartes
_ADDONS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ADDONS_ROOT not in sys.path:
    sys.path.insert(0, _ADDONS_ROOT)


def _install_anki_stubs():
    """Minimal stubs for aqt / anki when running pytest outside Anki."""
    if "aqt" in sys.modules and getattr(sys.modules["aqt"], "_edn_test_stub", False):
        return

    from PyQt6 import QtCore, QtGui, QtWidgets

    aqt_mod = types.ModuleType("aqt")
    aqt_mod._edn_test_stub = True
    aqt_mod.mw = MagicMock()
    aqt_mod.mw.addonManager = MagicMock()
    aqt_mod.mw.addonManager.getConfig.return_value = {}
    aqt_mod.dialogs = MagicMock()

    gui_hooks_mod = types.ModuleType("aqt.gui_hooks")
    for hook_name in (
        "editor_did_init",
        "webview_did_receive_js_message",
        "editor_did_init_buttons",
        "webview_will_set_content",
        "state_shortcuts_will_change",
        "webview_will_show_context_menu",
        "add_cards_did_add_note",
        "browser_menus_did_init",
        "browser_will_show_context_menu",
        "reviewer_did_show_answer",
        "reviewer_did_show_question",
        "reviewer_will_end",
        "previewer_will_show",
        "theme_did_change",
        "operation_did_execute",
        "editor_did_unfocus_field",
        "editor_did_fire_typing_timer",
        "editor_will_load_note",
        "editor_did_load_note",
        "main_window_did_init",
        "editor_did_init_shortcuts",
        "browser_did_change_row",
        "reviewer_did_show_question",
    ):
        setattr(gui_hooks_mod, hook_name, [])
    aqt_mod.gui_hooks = gui_hooks_mod

    qt_mod = types.ModuleType("aqt.qt")
    for mod in (QtCore, QtGui, QtWidgets):
        for attr in dir(mod):
            if not attr.startswith("_"):
                setattr(qt_mod, attr, getattr(mod, attr))
    qt_mod.QWebEngineSettings = MagicMock()
    qt_mod.QWebEngineProfile = MagicMock()

    editor_mod = types.ModuleType("aqt.editor")
    editor_mod.Editor = MagicMock

    utils_mod = types.ModuleType("aqt.utils")
    utils_mod.showInfo = MagicMock()
    utils_mod.tooltip = MagicMock()
    utils_mod.askUser = MagicMock(return_value=False)
    aqt_mod.utils = utils_mod

    webview_mod = types.ModuleType("aqt.webview")
    webview_mod.WebContent = MagicMock
    webview_mod.AnkiWebView = QtWidgets.QWidget
    aqt_mod.webview = webview_mod

    browser_mod = types.ModuleType("aqt.browser")
    browser_mod.Browser = MagicMock

    clayout_mod = types.ModuleType("aqt.clayout")
    clayout_mod.CardLayout = MagicMock

    ops_mod = types.ModuleType("aqt.operations")
    ops_mod.QueryOp = MagicMock

    anki_mod = types.ModuleType("anki")
    anki_hooks_mod = types.ModuleType("anki.hooks")
    anki_hooks_mod.card_did_render = []
    anki_mod.hooks = anki_hooks_mod

    sys.modules["aqt"] = aqt_mod
    sys.modules["aqt.gui_hooks"] = gui_hooks_mod
    sys.modules["aqt.qt"] = qt_mod
    sys.modules["aqt.editor"] = editor_mod
    sys.modules["aqt.utils"] = utils_mod
    sys.modules["aqt.webview"] = webview_mod
    sys.modules["aqt.browser"] = browser_mod
    sys.modules["aqt.clayout"] = clayout_mod
    sys.modules["aqt.operations"] = ops_mod
    sys.modules["anki"] = anki_mod
    sys.modules["anki.hooks"] = anki_hooks_mod


_install_anki_stubs()

import Anki_EDN_Lier_les_cartes.linked_cards as lc
import Anki_EDN_Lier_les_cartes.editor_assistant as ea


def test_extract_badge_nids_supports_ankicollab_sanitized_badges():
    html = (
        '<span class="edn-desc">Card A</span>&nbsp;—&nbsp;'
        '<kbd class="clickable_cards"><span class="edn-nid">1669544461685</span></kbd><br>'
        'Voir <kbd class="clickable_cards">'
        '<span class="edn-nid" style="display:none;font-size:0;line-height:0">1666776050088</span>ici</kbd><br>'
        'Direct <kbd class="clickable_cards">1711111111111</kbd><br>'
        'Complet <kbd class="clickable_cards" contenteditable="false" tabindex="0" data-nid="1722222222222">'
        '<span class="edn-nid">1722222222222</span></kbd>'
    )
    nids = lc._extract_badge_nids(html)
    assert set(nids) == {
        "1669544461685",
        "1666776050088",
        "1711111111111",
        "1722222222222",
    }


def test_update_link_description_in_note_works_without_data_nid():
    class DummyNote:
        def __init__(self, fields):
            self.fields = fields

    nid1 = "1669544461685"
    nid2 = "1666776050088"
    original = (
        f'<span class="edn-desc">Autre</span>&nbsp;—&nbsp;'
        f'<kbd class="clickable_cards"><span class="edn-nid">{nid1}</span></kbd><br>'
        f'<span class="edn-desc">Ancien titre</span>&nbsp;—&nbsp;'
        f'<kbd class="clickable_cards"><span class="edn-nid">{nid2}</span></kbd>'
    )
    note = DummyNote([original])
    changed = lc.update_link_description_in_note(note, nid2, "Nouveau titre")
    assert changed is True
    assert '<span class="edn-desc">Autre</span>' in note.fields[0]
    assert f'<span class="edn-desc">Nouveau titre</span>&nbsp;—&nbsp;<kbd class="clickable_cards"><span class="edn-nid">{nid2}</span>' in note.fields[0]
    assert note.fields[0].count("</kbd>") == 2

    # Plain-text description before sanitized badge
    note2 = DummyNote([f'Ancien texte — <kbd class="clickable_cards"><span class="edn-nid">{nid2}</span></kbd>'])
    changed2 = lc.update_link_description_in_note(note2, nid2, "Titre mis à jour")
    assert changed2 is True
    assert note2.fields[0] == f'<span class="edn-desc">Titre mis à jour</span>&nbsp;—&nbsp;<kbd class="clickable_cards"><span class="edn-nid">{nid2}</span></kbd>'


def test_clean_kbd_html_flattens_nested_ankicollab_badge():
    nid = "1669544461685"
    nested = (
        f'<kbd class="clickable_cards">Texte externe '
        f'<kbd class="clickable_cards"><span class="edn-nid">{nid}</span></kbd>'
        f'</kbd>'
    )
    cleaned = lc._clean_kbd_html(nested)
    assert cleaned.count("<kbd") == 1
    assert f'data-nid="{nid}"' in cleaned
    assert "Texte externe" in cleaned


def test_extract_nids_from_fields_list_handles_sanitized_badges():
    fields = [
        '<span class="edn-desc">Test</span> — <kbd class="clickable_cards"><span class="edn-nid">1669544461685</span></kbd>',
        '<kbd class="clickable_cards"><span class="edn-nid">1234567890</span></kbd>',
    ]
    nids = ea.extract_nids_from_fields_list(fields)
    assert "1669544461685" in nids
    assert "1234567890" in nids


def test_link_inserter_preserves_and_replaces_sanitized_badges(monkeypatch):
    nid = "1669544461685"
    sanitized_badge = f'<kbd class="clickable_cards"><span class="edn-nid">{nid}</span></kbd>'

    class DummyNote:
        def __init__(self, flds):
            self.id = 999999
            self.fields = list(flds)

        def note_type(self):
            return {"flds": [{"name": "Recto"}, {"name": "Cartes liées"}]}

    col_note = DummyNote(["Question", f"Intro {sanitized_badge}:::EDN_MARKER:::"])
    mock_col = MagicMock()
    mock_col.get_note.return_value = col_note
    monkeypatch.setattr(lc.mw, "col", mock_col, raising=False)
    monkeypatch.setattr(lc, "editor_save_now", lambda editor, cb, keep_focus=True: cb())
    monkeypatch.setattr(lc, "editor_reload_note", lambda editor, *a, **kw: None)
    monkeypatch.setattr(lc, "_detect_link_insertion_mode", lambda *a, **kw: (1, ":::EDN_MARKER:::", False, False, False))

    editor = MagicMock()
    editor.note = col_note
    editor.web = MagicMock()

    inserter = lc.LinkInserter(editor)
    inserter.insert_link([(nid, "Titre")], target_fidx=1)

    updated = col_note.fields[1]
    assert updated.count(nid) >= 1
    assert updated.count("<kbd") == 1
    assert '<span class="edn-desc">Titre</span>' in updated
    assert ":::EDN_MARKER:::" not in updated


def test_editor_js_repairs_ankicollab_badge_attributes():
    # Ensure the injected editor JS contains the Shadow DOM & light DOM badge repair routine
    with open(lc.__file__, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "function _repairEdnBadgesInRoot(root)" in src
    assert "kbd.setAttribute('contenteditable', 'false')" in src
    assert "kbd.setAttribute('tabindex', '0')" in src
    assert "kbd.setAttribute('data-nid', nid)" in src
    assert "ed.shadowRoot._ednBadgeObserver" in src


def test_editor_js_hover_paste_handler_syntax_regression():
    import re

    editor = MagicMock()
    editor.web = MagicMock()
    lc._do_editor_init(editor)

    eval_scripts = [call.args[0] for call in editor.web.eval.call_args_list if call.args]
    hover_scripts = [s for s in eval_scripts if "_ednEditorHoverInit" in s]
    assert len(hover_scripts) == 1
    js_hover = hover_scripts[0]

    assert "if (nid && nid.length > 9)" in js_hover
    assert "if nid && nid.length > 9" not in js_hover
    assert "pycmd('edn_paste_titles:'" in js_hover

    for lineno, line in enumerate(js_hover.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        assert not re.match(
            r"^(?:else\s+)?(if|while|for|catch)\s+[A-Za-z_$!]", stripped
        ), f"Malformed JS control statement at js_hover line {lineno}: {stripped}"


def test_link_inserter_with_text_removes_selected_word_before_marker(monkeypatch):
    nid = "1669544461685"
    marker = '<span id="edn-cursor-marker" class="edn-cursor-marker">:::EDN_MARKER:::</span>'

    class DummyNote:
        def __init__(self, flds):
            self.id = 999998
            self.fields = list(flds)

        def note_type(self):
            return {"flds": [{"name": "Recto"}, {"name": "Cartes liées"}]}

    monkeypatch.setattr(lc, "editor_save_now", lambda editor, cb, keep_focus=True: cb())
    monkeypatch.setattr(lc, "editor_reload_note", lambda editor, *a, **kw: None)
    monkeypatch.setattr(lc, "tooltip", lambda *a, **kw: None)

    def _run(field_html, selected_text):
        col_note = DummyNote(["Question", field_html])
        mock_col = MagicMock()
        mock_col.get_note.return_value = col_note
        monkeypatch.setattr(lc.mw, "col", mock_col, raising=False)
        editor = MagicMock()
        editor.note = col_note
        editor.web = MagicMock()
        lc.LinkInserter(editor).insert_link_with_text(nid, selected_text, target_fidx=1)
        return col_note.fields[1]

    # 1. Simple word in the middle of a sentence
    res1 = _run(f"Le patient présente une dyspnée{marker} aiguë", "dyspnée")
    assert res1.startswith("Le patient présente une <kbd ")
    assert res1.endswith("</kbd>&nbsp; aiguë")
    assert "dyspnée<kbd" not in res1
    assert ":::EDN_MARKER:::" not in res1

    # 2. Multi-word with &nbsp;, HTML entity, and trailing space before marker
    res2 = _run(f"Voir A&nbsp;&amp;&nbsp;B {marker}ici", "A & B")
    assert res2.startswith("Voir <kbd ")
    assert res2.endswith("</kbd>&nbsp;ici")
    assert "A&nbsp;&amp;&nbsp;B" not in res2

    # 3. Earlier occurrence of the same word must remain untouched
    res3 = _run(f"Hypertension : hypertension{marker} artérielle", "hypertension")
    assert res3.startswith("Hypertension : <kbd ")
    assert res3.endswith("</kbd>&nbsp; artérielle")
    assert res3.count("hypertension") == 1  # only inside the <kbd>


