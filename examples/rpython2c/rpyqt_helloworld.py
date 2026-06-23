"""A counter GUI in PyQt-shaped rpython, running natively on Wayland via rpyqt.

    from rpyqt import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton

No Qt is linked: rpyqt is a software-rendered emulation layer on rwayland, and
py2c generates all the Wayland glue.
"""
from rpyqt import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton

_count = 0
_label = None


def on_increment() -> None:
    global _count
    _count = _count + 1
    lbl = _label
    if lbl is not None:
        if _count == 1:
            lbl.setText("COUNT 1")
        elif _count == 2:
            lbl.setText("COUNT 2")
        elif _count == 3:
            lbl.setText("COUNT 3")
        else:
            lbl.setText("COUNT MANY")


def main() -> int:
    global _label
    app = QApplication()
    win = QWidget()
    win.setWindowTitle("COUNTER")

    box = QVBoxLayout()
    label = QLabel("COUNT 0")
    _label = label
    button = QPushButton("INCREMENT")
    button.clicked.connect(on_increment)
    box.addWidget(label)
    box.addWidget(button)
    win.setLayout(box)

    return app.exec_(win)
