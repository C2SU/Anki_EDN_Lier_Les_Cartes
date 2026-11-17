// Context menu helper & Shadow DOM style injector for Anki EDN
(function() {
    function _injectEdnShadowStyles(root) {
        root = root || document;
        var css = `
            kbd.clickable_cards {
              font-family: "Lucida console", Monaco, monospace !important;
              font-weight: 600 !important;
              color: #b45309 !important;
              background: #fffbf5 !important;
              background-color: #fffbf5 !important;
              border-radius: 4px !important;
              border: 1px solid #e5c98e !important;
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
              box-shadow: none !important;
            }
            kbd.clickable_cards:hover {
              color: #2c7bb6 !important;
              border-color: #93c5fd !important;
              background: #f0f7ff !important;
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
              color: #64b5f6 !important;
              border-color: #1976d2 !important;
            }

            /* Hyperliens textuels (hors NID numeriques) : style lien inline sans encadrement, lisible sur desktop et mobile */
            kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            kbd.clickable_cards.edn-link-text {
              display: inline !important;
              font-family: inherit !important;
              font-weight: 500 !important;
              font-size: inherit !important;
              line-height: inherit !important;
              letter-spacing: normal !important;
              color: #1d4ed8 !important;
              background: transparent !important;
              background-color: transparent !important;
              border: none !important;
              border-radius: 0 !important;
              box-shadow: none !important;
              padding: 0 1px !important;
              margin: 0 !important;
              text-decoration: underline !important;
              text-decoration-color: rgba(29, 78, 216, 0.45) !important;
              text-underline-offset: 3px !important;
              text-decoration-thickness: 1.5px !important;
              vertical-align: baseline !important;
              text-align: left !important;
              max-width: 100% !important;
              white-space: normal !important;
              overflow-wrap: break-word !important;
              -webkit-box-decoration-break: clone;
              box-decoration-break: clone;
              -webkit-tap-highlight-color: transparent;
              touch-action: manipulation;
            }
            kbd.clickable_cards:has(.edn-nid[style*="display:none"]):hover,
            kbd.clickable_cards:has(.edn-nid[style*="display: none"]):hover,
            kbd.clickable_cards.edn-link-text:hover {
              color: #1e40af !important;
              background: rgba(37, 99, 235, 0.08) !important;
              background-color: rgba(37, 99, 235, 0.08) !important;
              border-radius: 3px !important;
              text-decoration-color: #1e40af !important;
            }
            .nightMode kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            .night_mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            .night-mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            body.nightMode kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            body.night_mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            body.night-mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            .nightMode kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            .night_mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            .night-mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            body.nightMode kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            body.night_mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            body.night-mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            .nightMode kbd.clickable_cards.edn-link-text,
            .night_mode kbd.clickable_cards.edn-link-text,
            .night-mode kbd.clickable_cards.edn-link-text {
              color: #93c5fd !important;
              background: transparent !important;
              background-color: transparent !important;
              border: none !important;
              text-decoration-color: rgba(147, 197, 253, 0.45) !important;
            }
            .nightMode kbd.clickable_cards:has(.edn-nid[style*="display:none"]):hover,
            .night_mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]):hover,
            .night-mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]):hover,
            body.nightMode kbd.clickable_cards:has(.edn-nid[style*="display:none"]):hover,
            body.night_mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]):hover,
            body.night-mode kbd.clickable_cards:has(.edn-nid[style*="display:none"]):hover,
            .nightMode kbd.clickable_cards:has(.edn-nid[style*="display: none"]):hover,
            .night_mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]):hover,
            .night-mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]):hover,
            body.nightMode kbd.clickable_cards:has(.edn-nid[style*="display: none"]):hover,
            body.night_mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]):hover,
            body.night-mode kbd.clickable_cards:has(.edn-nid[style*="display: none"]):hover,
            .nightMode kbd.clickable_cards.edn-link-text:hover,
            .night_mode kbd.clickable_cards.edn-link-text:hover,
            .night-mode kbd.clickable_cards.edn-link-text:hover {
              color: #bfdbfe !important;
              background: rgba(59, 130, 246, 0.14) !important;
              background-color: rgba(59, 130, 246, 0.14) !important;
              border-radius: 3px !important;
              text-decoration-color: #bfdbfe !important;
            }

            @media (pointer: coarse), (max-width: 600px) {
              kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
              kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
              kbd.clickable_cards.edn-link-text {
                padding: 2px 2px !important;
                text-underline-offset: 3px !important;
              }
            }
            .mobile kbd.clickable_cards:has(.edn-nid[style*="display:none"]),
            .mobile kbd.clickable_cards:has(.edn-nid[style*="display: none"]),
            .mobile kbd.clickable_cards.edn-link-text {
              padding: 2px 2px !important;
              text-underline-offset: 3px !important;
            }

            kbd.clickable_cards .edn-nid {
              color: inherit !important;
              font-family: inherit !important;
              font-weight: inherit !important;
            }

            .cartesLiees, .items.cartesLiees {
              line-height: 1.45 !important;
            }
            .edn-desc, span.edn-desc {
                display: inline !important;
            }
            .cartesLiees br, .items.cartesLiees br {
                display: block !important;
                content: "" !important;
            }
            [id*="edn-cursor-marker"], #edn-cursor-marker, .edn-cursor-marker {
                display: none !important;
                visibility: hidden !important;
                font-size: 0 !important;
                width: 0 !important;
                height: 0 !important;
                opacity: 0 !important;
                pointer-events: none !important;
                user-select: none !important;
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
