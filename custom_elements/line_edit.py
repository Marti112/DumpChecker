from PyQt5 import QtCore, QtWidgets


class QLineEditWithEnterClickEvent(QtWidgets.QLineEdit):
    enter_pressed = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Return:
            self.enter_pressed.emit(event)
        else:
            QtWidgets.QLineEdit.keyPressEvent(self, event)
