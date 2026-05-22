js_hover = """
var html = rectoEscaped + ' — <kbd class="clickable_cards" tabindex="0" data-nid="' + nid + '" onclick="cards_ct_click(\\'' + nid + '\\')" ondblclick="cards_ct_click(\\'' + nid + '\\')"><span class="edn-nid">' + nid + '</span></kbd>&nbsp;';
"""
print(repr(js_hover))
