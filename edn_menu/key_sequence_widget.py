"""
Key Sequence Input Widget
A widget for capturing keyboard shortcuts with live preview
"""
from aqt.qt import *

class KeySequenceEdit(QWidget):
    """Custom widget for capturing keyboard shortcuts"""
    
    shortcutChanged = pyqtSignal(str)  # Emits the shortcut string
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_keys = []
        self.setup_ui()
    
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Display label
        self.display = QLineEdit()
        self.display.setReadOnly(True)
        self.display.setPlaceholderText("Cliquez et pressez vos touches...")
        self.display.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout.addWidget(self.display, 1)
        
        # Clear button
        clear_btn = QPushButton("×")
        clear_btn.setMaximumWidth(30)
        clear_btn.setToolTip("Effacer")
        clear_btn.clicked.connect(self.clear)
        layout.addWidget(clear_btn)
    
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
        self.display.setStyleSheet("QLineEdit { border: 2px solid #2c7bb6; }")
        super().focusInEvent(event)
    
    def focusOutEvent(self, event):
        """Reset appearance when focus lost"""
        self.display.setStyleSheet("")
        super().focusOutEvent(event)
