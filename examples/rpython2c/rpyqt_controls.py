"""A settings-style panel exercising the wider rpyqt widget set:
QCheckBox, QSlider, QProgressBar, and a nested QHBoxLayout of buttons inside a
QVBoxLayout.

Behaviour:
  - clicking the slider sets its value from the click x-position; the progress
    bar mirrors it,
  - toggling the checkbox flips the status label,
  - RESET zeroes the slider and bar.
"""
from rpyqt import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                   QLabel, QPushButton, QCheckBox, QSlider, QProgressBar)

_status = None
_slider = None
_bar = None
_check = None


def on_slide() -> None:
    s = _slider
    b = _bar
    if s is not None and b is not None:
        b.setValue(s.value())


def on_toggle() -> None:
    c = _check
    st = _status
    if c is not None and st is not None:
        if c.isChecked():
            st.setText("STATUS ON")
        else:
            st.setText("STATUS OFF")


def on_reset() -> None:
    s = _slider
    b = _bar
    if s is not None:
        s.setValue(0)
    if b is not None:
        b.setValue(0)


def main() -> int:
    global _status, _slider, _bar, _check
    app = QApplication()
    win = QWidget()
    win.setWindowTitle("CONTROLS")

    box = QVBoxLayout()
    box.addWidget(QLabel("SETTINGS"))

    check = QCheckBox("ENABLE")
    _check = check
    check.stateChanged.connect(on_toggle)
    box.addWidget(check)

    slider = QSlider()
    _slider = slider
    slider.setValue(40)
    slider.valueChanged.connect(on_slide)
    box.addWidget(slider)

    bar = QProgressBar()
    _bar = bar
    bar.setValue(40)
    box.addWidget(bar)

    row = QHBoxLayout()
    reset = QPushButton("RESET")
    reset.clicked.connect(on_reset)
    row.addWidget(reset)
    row.addWidget(QPushButton("CLOSE"))
    box.addLayout(row)

    status = QLabel("STATUS OFF")
    _status = status
    box.addWidget(status)

    win.setLayout(box)
    return app.exec_(win)
