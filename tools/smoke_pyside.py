"""Qt binding migration smoke imports (does not start aria2 or download)."""
from __future__ import annotations
import sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
class _Signals(QObject):
    ping = Signal(str)
app = QApplication.instance() or QApplication(sys.argv[:1])
modules=(
 'app.notify', 'app.pet_skin','app.ui.main_window','app.ui.models',
 'app.ui.options_dialog','app.ui.title_lookup_dialog', 'app.ui.pet',
 'app.ui.table','app.ui.theme','app.ui.workers',
)
for name in modules:
    __import__(name)
    print('OK',name)
image=QImage(8,8,QImage.Format.Format_RGBA8888)
image.fill(0xFFFFFFFF)
assert len(bytearray(image.bits()[:image.sizeInBytes()])) == image.sizeInBytes()
print('OK PySide6 basic GUI import + QImage memory access')
