"""
Key Sequence Input Widget
A widget for capturing keyboard shortcuts with live preview
"""
from aqt.qt import *
from aqt import mw


def is_dark_mode() -> bool:
    try:
        from aqt.theme import theme_manager
        if theme_manager and hasattr(theme_manager, "night_mode"):
            return bool(theme_manager.night_mode)
    except Exception:
        pass
    try:
        if mw and hasattr(mw, "theme_manager") and hasattr(mw.theme_manager, "night_mode"):
            return bool(mw.theme_manager.night_mode)
    except Exception:
        pass
    try:
        if mw and hasattr(mw, "pm") and hasattr(mw.pm, "night_mode"):
            return bool(mw.pm.night_mode())
    except Exception:
        pass
    return False


class KeySequenceEdit(QWidget):
    """Custom widget for capturing keyboard shortcuts"""
    
    shortcutChanged = pyqtSignal(str)  # Emits the shortcut string
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_keys = []
        self.dark = is_dark_mode()
        self.setup_ui()
    
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # Display label
        self.display = QLineEdit()
        self.display.setReadOnly(True)
        self.display.setPlaceholderText("Cliquez et pressez vos touches...")
        self.display.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._apply_normal_style()
        layout.addWidget(self.display, 1)
        
        # Clear button
        clear_btn = QPushButton("×")
        clear_btn.setMaximumWidth(28)
        clear_btn.setToolTip("Effacer")
        if self.dark:
            clear_btn.setStyleSheet("""
                QPushButton {
                    background-color: #21252b;
                    color: #94a3b8;
                    border: 1px solid #3e4451;
                    border-radius: 4px;
                    font-size: 13px;
                    font-weight: bold;
                    padding: 3px;
                }
                QPushButton:hover {
                    background-color: #2c313a;
                    color: #f1f5f9;
                }
            """)
        else:
            clear_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f1f5f9;
                    color: #64748b;
                    border: 1px solid #cbd5e1;
                    border-radius: 4px;
                    font-size: 13px;
                    font-weight: bold;
                    padding: 3px;
                }
                QPushButton:hover {
                    background-color: #e2e8f0;
                    color: #0f172a;
                }
            """)
        clear_btn.clicked.connect(self.clear)
        layout.addWidget(clear_btn)
    
    def _apply_normal_style(self):
        if self.dark:
            self.display.setStyleSheet("""
                QLineEdit {
                    background: #1e2227;
                    color: #f1f5f9;
                    border: 1px solid #3e4451;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-family: monospace;
                    font-weight: 600;
                }
            """)
        else:
            self.display.setStyleSheet("""
                QLineEdit {
                    background: #ffffff;
                    color: #1e293b;
                    border: 1px solid #cbd5e1;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-family: monospace;
                    font-weight: 600;
                }
            """)

    def set_shortcut(self, shortcut: str):
        """Set the displayed shortcut"""
        self.display.setText(shortcut)
        self.current_keys = []
    
    def clear(self):
        """Clear the shortcut"""
        self.display.clear()
        self.current_keys = []
        self.shortcutChanged.emit("")
    
    def keyPressEvent(self, event):
        """Capture key press and build shortcut"""
        # Ignore just modifier keys alone
        if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return
        
        # Build shortcut string
        modifiers = []
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            modifiers.append("Ctrl")
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            modifiers.append("Shift")
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            modifiers.append("Alt")
        if event.modifiers() & Qt.KeyboardModifier.MetaModifier:
            modifiers.append("Meta")
        
        # Get key name
        key_text = QKeySequence(event.key()).toString()
        
        # Build full shortcut
        if modifiers:
            shortcut = "+".join(modifiers + [key_text])
        else:
            shortcut = key_text
        
        # Update display
        self.display.setText(shortcut)
        self.shortcutChanged.emit(shortcut)
        
        # Accept event
        event.accept()
    
    def focusInEvent(self, event):
        """Change appearance when focused"""
        accent = "#38bdf8" if self.dark else "#0284c7"
        bg = "#1e2227" if self.dark else "#ffffff"
        fg = "#f1f5f9" if self.dark else "#1e293b"
        self.display.setStyleSheet(f"""
            QLineEdit {{
                background: {bg};
                color: {fg};
                border: 2px solid {accent};
                border-radius: 4px;
                padding: 3px 7px;
                font-family: monospace;
                font-weight: 600;
            }}
        """)
        super().focusInEvent(event)
    
    def focusOutEvent(self, event):
        """Reset appearance when focus lost"""
        self._apply_normal_style()
        super().focusOutEvent(event)

