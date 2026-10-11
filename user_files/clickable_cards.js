// Context menu helper & Shadow DOM style injector for Anki EDN
(function() {
    function _injectEdnShadowStyles(root) {
        root = root || document;
        var css = `
            kbd.clickable_cards {
                font-family: "Lucida console", Monaco, monospace !important;
                font-weight: 600 !important;
                color: #FFA630 !important;
                background-color: #f9f9f9 !important;
                background: linear-gradient(to bottom, #f9f9f9, #e9e9e9) !important;
                border-radius: 4px !important;
                border: 1px solid gainsboro !important;
                display: inline-block !important;
                font-size: 13px !important;
                line-height: 16px !important;
                padding: 2px 6px !important;
                margin: 1px 3px !important;
                text-align: center !important;
                cursor: pointer !important;
                width: auto !important;
                max-width: fit-content !important;
                box-sizing: border-box !important;
                vertical-align: middle !important;
                white-space: nowrap !important;
            }
            kbd.clickable_cards:hover {
                color: #BD93BD !important;
                border-color: #BD93BD !important;
            }
            kbd.clickable_cards:focus,
            kbd.clickable_cards:focus-visible {
                outline: none !important;
                box-shadow: none !important;
            }
            .nightMode kbd.clickable_cards,
            .night_mode kbd.clickable_cards,
            .night-mode kbd.clickable_cards,
            body.nightMode kbd.clickable_cards,
            body.night_mode kbd.clickable_cards,
            body.night-mode kbd.clickable_cards {
                background: #2e3238 !important;
                background-color: #2e3238 !important;
                border: 1px solid #4a4d52 !important;
                color: #FFA630 !important;
            }
            .nightMode kbd.clickable_cards:hover,
            .night_mode kbd.clickable_cards:hover,
            .night-mode kbd.clickable_cards:hover,
            body.nightMode kbd.clickable_cards:hover,
            body.night_mode kbd.clickable_cards:hover,
            body.night-mode kbd.clickable_cards:hover {
                color: #BD93BD !important;
                border-color: #BD93BD !important;
            }
            kbd.clickable_cards .edn-nid {
                color: inherit !important;
                font-family: inherit !important;
                font-weight: inherit !important;
            }
            .edn-desc, span.edn-desc {
                display: inline !important;
            }
            .cartesLiees br, .items.cartesLiees br {
                display: block !important;
                content: "" !important;
            }
            [id*="edn-cursor-marker"], #edn-cursor-marker {
                display: none !important;
            }
        `;

        var editables = root.querySelectorAll ? root.querySelectorAll('anki-editable') : [];
        for (var i = 0; i < editables.length; i++) {
            var ed = editables[i];
            if (ed.shadowRoot && !ed.shadowRoot.getElementById('edn-shadow-styles')) {
                var st = document.createElement('style');
                st.id = 'edn-shadow-styles';
                st.textContent = css;
                ed.shadowRoot.appendChild(st);
            }
        }
    }

    window._ednInjectShadowStyles = _injectEdnShadowStyles;
    _injectEdnShadowStyles();

    document.addEventListener('focusin', function() {
        _injectEdnShadowStyles();
    }, true);

    if (window.MutationObserver && !window._ednShadowObserver) {
        window._ednShadowObserver = new MutationObserver(function() {
            _injectEdnShadowStyles();
        });
        if (document.body) {
            window._ednShadowObserver.observe(document.body, { childList: true, subtree: true });
        }
    }

    function _getNid(el) {
        if (!el) return '';
        if (el.dataset && el.dataset.nid) return el.dataset.nid;
        var s = el.querySelector && el.querySelector('.edn-nid');
        if (s) return s.textContent.trim();
        var oc = el.getAttribute ? el.getAttribute('onclick') : null;
        if (oc) { var m = oc.match(/\d{10,}/); if (m) return m[0]; }
        return el.innerText ? el.innerText.replace(/[^0-9]/g, '') : '';
    }

    function _findBadgeFromEvent(e) {
        if (e.composedPath) {
            var path = e.composedPath();
            for (var i = 0; i < path.length; i++) {
                var el = path[i];
                if (el.classList && el.classList.contains("clickable_cards")) {
                    return el;
                }
            }
        }
        return e.target && e.target.closest ? e.target.closest('.clickable_cards') : null;
    }

    document.addEventListener('mousedown', function(e) {
        if (e.button === 2) { // Right click
            var target = e.target;
            
            // 1. Check if clicked on a badge (supporting Shadow DOM)
            var badge = _findBadgeFromEvent(e);
            if (badge) {
                var nid = _getNid(badge);
                if (nid) {
                    if (typeof pycmd !== 'undefined') pycmd("edn_right_click_nid:" + nid);
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand("edn_right_click_nid:" + nid);
                    return;
                }
            }
            
            // 2. Check if clicked inside hover preview box
            var previewBox = target && target.closest ? target.closest('#edn-preview-box') : null;
            if (previewBox) {
                if (window._edn_hover_target) {
                    var nid = _getNid(window._edn_hover_target);
                    if (nid) {
                        if (typeof pycmd !== 'undefined') pycmd("edn_right_click_nid:" + nid);
                        else if (typeof bridgeCommand !== 'undefined') bridgeCommand("edn_right_click_nid:" + nid);
                        return;
                    }
                }
            }
            
            // 3. Check if we are inside a preview page (card)
            var cardDiv = document.querySelector('.card');
            if (cardDiv) {
                if (typeof pycmd !== 'undefined') pycmd("edn_right_click_page:true");
                else if (typeof bridgeCommand !== 'undefined') bridgeCommand("edn_right_click_page:true");
                return;
            }
            
            if (typeof pycmd !== 'undefined') pycmd("edn_right_click_nid:none");
            else if (typeof bridgeCommand !== 'undefined') bridgeCommand("edn_right_click_nid:none");
        }
    }, true);
})();
