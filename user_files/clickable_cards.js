// Context menu helper for Anki EDN
(function() {
    function _getNid(el) {
        if (el.dataset && el.dataset.nid) return el.dataset.nid;
        var s = el.querySelector && el.querySelector('.edn-nid');
        if (s) return s.textContent.trim();
        var oc = el.getAttribute('onclick');
        if (oc) { var m = oc.match(/\d{10,}/); if (m) return m[0]; }
        return el.innerText ? el.innerText.replace(/[^0-9]/g, '') : '';
    }

    document.addEventListener('mousedown', function(e) {
        if (e.button === 2) { // Right click
            var target = e.target;
            
            // 1. Check if clicked on a badge
            var badge = target.closest('.clickable_cards');
            if (badge) {
                var nid = _getNid(badge);
                if (nid) {
                    if (typeof pycmd !== 'undefined') pycmd("edn_right_click_nid:" + nid);
                    else if (typeof bridgeCommand !== 'undefined') bridgeCommand("edn_right_click_nid:" + nid);
                    return;
                }
            }
            
            // 2. Check if clicked inside hover preview box
            var previewBox = target.closest('#edn-preview-box');
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
