import json
import os
import re
import shutil
import sys
from threading import Event
from time import sleep

import arrow
import getmac as getmac
from PyQt5 import QtCore
from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QMainWindow, QApplication, QMessageBox
from gmail import Message, GMail

from checker_ui import Ui_MainWindow
from custom_elements import QLineEditWithEnterClickEvent
from exceptions import RecipientNotSetError, ErrorWhileCopying, PathDoesntExist
from helpers import load_and_get_configs, CONFIG_PATH


class FileSize:
    def __init__(self, size):
        self.size = size

    @property
    def megabytes(self) -> float:
        return self.size / 1024 / 1024

    @property
    def kilobytes(self) -> float:
        return self.size / 1024


class Dump:
    def __init__(self, dump_full_path):
        assert dump_full_path.endswith(".dmp"), "Received file has extension different then '.dmp'"
        self._dump = dump_full_path

    @property
    def file_name(self):
        return os.path.split(self._dump)[-1]

    @property
    def full_path(self):
        return self._dump

    def file_size(self):
        return FileSize(os.path.getsize(self._dump))


class EmailSenderThread(QThread):
    EmailSenderThreadSignal = QtCore.pyqtSignal(object)
    EmailSendSignal = QtCore.pyqtSignal(object)

    def __init__(self, parent, configs, stop_event):
        super().__init__(parent)
        self.configs = configs
        self.stop_event = stop_event
        self.dumps = None

    def run(self):
        dump_file_names = "\n".join([d.file_name for d in self.dumps])

        message = f'{self.configs["EMAIL"]["SUBJECT"]}\nList of dmp:\n{dump_file_names}'
        auth = self.configs["UTILITY_CONFIGS"]["CHECKER_AUTH"]
        gmail = GMail(auth["LOGIN"], auth["PASSWORD"])
        if self.configs["EMAIL"]["SEND_DMP_FILES"] and sum([d.file_size.megabytes for d in self.dumps]) < int(self.configs["EMAIL"]["ATTACH_FILES_MAX_SIZE"]):
            attachments = [d.full_path for d in self.dumps]
        else:
            attachments = []

        email_title = self.configs["EMAIL"]["TITLE"]
        recipient = ";".join(self.configs["EMAIL"]["RECIPIENT_ADDRESSES"])
        msg = Message(email_title, to=recipient, text=message, attachments=attachments)
        self.EmailSenderThreadSignal.emit(dump_file_names)
        print("Start sending email")
        try:
            gmail.send(msg)
        except Exception as e:
            print(f"Can't send message. {e}")
        else:
            self.EmailSendSignal.emit(self.dumps)
            print("Email send.")


class CheckThread(QThread):
    CheckerThreadSignal = QtCore.pyqtSignal(object)

    def __init__(self, parent, configs, stop_event, delay):
        super().__init__(parent)
        self.CONFIGS = configs
        self.stop_event = stop_event
        self.delay = delay

    def dumps_in_logs(self, logs_dir):
        def search_dumps(log_path):
            dumps = []
            cwd, _, logs = next(os.walk(log_path))
            for log_file in logs:
                name, extension = os.path.splitext(log_file)
                if extension == ".dmp":
                    dump = Dump(os.path.join(cwd, log_file))
                    dumps.append(dump)
            return dumps

        servers_logs_dir = logs_dir["SERVER"]
        return search_dumps(servers_logs_dir)

    def check(self, configs):
        print(f"{arrow.now().format('DD-MM-YYYY HH:mm:ss'):=^70}")

        dumps = self.dumps_in_logs(configs["LOGS_PATH"])
        if dumps:
            self.CheckerThreadSignal.emit(dumps)

        print("=" * 70 + "\n")

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.check(self.CONFIGS)
                sleep(self.delay)
            except RecipientNotSetError as er:
                print(er)
                sleep(10)
                exit(1)
            except Exception as ex:
                print(ex)
                sleep(10)


class DumpChecker(QMainWindow):
    def __init__(self):
        super(DumpChecker, self).__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.configs = self.load_default_values()

        self.checker_stop_event = Event()
        self.check_thread = CheckThread(self, configs=self.configs, stop_event=self.checker_stop_event, delay=self._wait_time())
        self.check_thread.CheckerThreadSignal.connect(self.send_email)

        self.email_sender_stop_event = Event()
        self.email_sender_thread = EmailSenderThread(self, configs=self.configs, stop_event=self.email_sender_stop_event)
        self.email_sender_thread.EmailSenderThreadSignal.connect(self.refresh_log_view_message)
        self.email_sender_thread.EmailSendSignal.connect(self.move_old_dumps)

        self.ui.pushButtonStart.clicked.connect(self.start_check)
        self.ui.pushButtonStop.clicked.connect(self.stop_check)

        self.ui.pushButtonAddNewRecipient.setDisabled(True)
        self.ui.pushButtonAddNewRecipient.clicked.connect(self.add_new_recipient)
        self.ui.pushButtonRemoveRecipient.clicked.connect(self.remove_recipient)

        self.ui.listWidgetRecipients.itemClicked.connect(self.in_case_of_item_selected)
        self.ui.pushButtonRemoveRecipient.setDisabled(True)

        self.ui.textEditLogView.setReadOnly(True)
        if isinstance(self.ui.lineEditAddNewRecipient, QLineEditWithEnterClickEvent):
            self.ui.lineEditAddNewRecipient.enter_pressed.connect(self.add_new_recipient_by_enter_click)
        self.ui.lineEditAddNewRecipient.textChanged.connect(self.validate_entered_email)

        self.ui.CheckBoxSendDmp.setToolTip(f'Send *.dmp files if their size in total less then {self.configs["EMAIL"]["ATTACH_FILES_MAX_SIZE"]}Mb.')

        if self.configs["UTILITY_CONFIGS"]["AUTORUN"]:
            self.start_check()

    def refresh_log_view_message(self, message):
        old_value = self.ui.textEditLogView.toPlainText()
        self.ui.textEditLogView.setText(f"{old_value}\n{arrow.now().format('DD-MM-YYYY HH:mm:ss'):=^70}\n{message}")

    def add_new_recipient_by_enter_click(self, event):
        if event.key() == QtCore.Qt.Key_Return:
            if self.validate_entered_email():
                self.add_new_recipient()

    def validate_entered_email(self):
        add_email_line_edit_current_value = self.ui.lineEditAddNewRecipient.text()

        email_re = re.compile(r"(^[-!#$%&'*+/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_`{}|~0-9A-Z]+)*"  # dot-atom
            r'|^"([\001-\010\013\014\016-\037!#-\[\]-\177]|\\[\001-011\013\014\016-\177])*"'  # quoted-string
            r')@(?:[A-Z0-9-]+\.)+[A-Z]{2,6}$', re.IGNORECASE)
        if add_email_line_edit_current_value and email_re.match(add_email_line_edit_current_value):
            self.ui.lineEditAddNewRecipient.setStyleSheet("color: rgb(0, 150, 10);")
            self.ui.pushButtonAddNewRecipient.setEnabled(True)
            return True
        elif not add_email_line_edit_current_value:
            self.ui.lineEditAddNewRecipient.setStyleSheet("color: rgb(137, 137, 137);")

        else:
            self.ui.lineEditAddNewRecipient.setStyleSheet("color: rgb(150, 10, 0);")
            self.ui.pushButtonAddNewRecipient.setDisabled(True)
            return False

    def load_default_values(self):

        CONFIGS = load_and_get_configs()

        delay = CONFIGS["DELAY"]
        self.ui.spinBoxDays.setValue(int(delay["DAYS"]))
        self.ui.spinBoxHours.setValue(int(delay["HOURS"]))
        self.ui.spinBoxMinutes.setValue(int(delay["MINUTES"]))
        self.ui.spinBoxSeconds.setValue(int(delay["SECONDS"]))

        self.ui.lineEditLogPath.setText(CONFIGS["LOGS_PATH"]["SERVER"])
        self.ui.lineEditDumpStoringDirPath.setText(CONFIGS["DUMPS_STORING_DIRECTORY"])

        cur_val = CONFIGS["EMAIL"]["TITLE"]
        new_val = f'{getmac.get_mac_address()}: {cur_val}' if getmac.get_mac_address() not in cur_val else cur_val
        self.ui.lineEditEmailTitle.setText(new_val)

        self.ui.textEditEmailMessage.setText(CONFIGS["EMAIL"]["SUBJECT"])

        self.ui.CheckBoxSendDmp.setChecked(CONFIGS["EMAIL"]["SEND_DMP_FILES"])

        self.ui.listWidgetRecipients.addItems(sorted(CONFIGS["EMAIL"]["RECIPIENT_ADDRESSES"]))

        if not os.path.isdir(CONFIGS["DUMPS_STORING_DIRECTORY"]):
            os.makedirs(CONFIGS["DUMPS_STORING_DIRECTORY"])
        return CONFIGS

    def save_new_configs(self):
        CONFIGS = self.configs

        delay = CONFIGS["DELAY"]
        delay["DAYS"] = self.ui.spinBoxDays.value()
        delay["HOURS"] = self.ui.spinBoxHours.value()
        delay["MINUTES"] = self.ui.spinBoxMinutes.value()
        delay["SECONDS"] = self.ui.spinBoxSeconds.value()

        log_path = self.ui.lineEditLogPath.text()
        if os.path.isdir(log_path):
            CONFIGS["LOGS_PATH"]["SERVER"] = self.ui.lineEditLogPath.text()
        else:
            self._warning(f"Directory '{log_path}' doesn't exist.\nLoad default value")
            self.ui.lineEditLogPath.setText(CONFIGS["LOGS_PATH"]["SERVER"])

        dump_storing_path = self.ui.lineEditDumpStoringDirPath.text()
        if os.path.isdir(dump_storing_path):
            CONFIGS["DUMPS_STORING_DIRECTORY"] = dump_storing_path
        elif not os.path.isdir(dump_storing_path):
            answer = QMessageBox.question(self.ui.centralwidget, "", f"Directory {dump_storing_path} doesn't exist.\n"
                                                                 f"Create?", QMessageBox.No | QMessageBox.Yes, QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                try:
                    os.makedirs(dump_storing_path)
                except Exception as e:
                    self._warning(f"{e}\nLoad default value")
                    self.ui.lineEditDumpStoringDirPath.setText(CONFIGS["DUMPS_STORING_DIRECTORY"])

                else:
                    CONFIGS["DUMPS_STORING_DIRECTORY"] = dump_storing_path
                    self.ui.lineEditDumpStoringDirPath.setText(dump_storing_path)

        else:
            self._warning(f"Dmps storing directory '{log_path}' is incorrect.\nLoad default value")
            self.ui.lineEditDumpStoringDirPath.setText(CONFIGS["DUMPS_STORING_DIRECTORY"])

        cur_val = self.ui.lineEditEmailTitle.text()
        new_val = f'{getmac.get_mac_address()}: {cur_val}' if getmac.get_mac_address() not in cur_val else cur_val
        CONFIGS["EMAIL"]["TITLE"] = new_val

        CONFIGS["EMAIL"]["SUBJECT"] = self.ui.textEditEmailMessage.toPlainText()

        CONFIGS["EMAIL"]["SEND_DMP_FILES"] = self.ui.CheckBoxSendDmp.isChecked()

        CONFIGS["EMAIL"]["RECIPIENT_ADDRESSES"] = self._recipients
        with open(CONFIG_PATH, "w") as f:
            f.write(json.dumps(CONFIGS, indent=4))
        self.configs = load_and_get_configs()

    def in_case_of_item_selected(self, p):
        self.ui.pushButtonRemoveRecipient.setEnabled(True)

    def _wait_time(self):
        return self.ui.spinBoxDays.value() * 24 * 60 * 60 \
               + self.ui.spinBoxHours.value() * 60 * 60 \
               + self.ui.spinBoxMinutes.value() * 60 \
               + self.ui.spinBoxSeconds.value()

    def _warning(self, message, title=""):
        QMessageBox.warning(self.ui.centralwidget, title, message, QMessageBox.Ok)

    def check_recipient(self):
        recipient = self._recipients
        if not recipient:
            self._warning("Please add email recipient addresses.")
            raise RecipientNotSetError("Email recipients list is empty!")

    def check_log_path_exist(self):
        if not os.path.isdir(self.configs["LOGS_PATH"]["SERVER"]):
            self._warning("Please add Server's log path.")
            raise PathDoesntExist("Incorrect log path!")

    def start_check(self):
        self.save_new_configs()

        try:
            self.check_recipient()
            self.check_log_path_exist()
        except RecipientNotSetError as er:
            print(er)
            self.ui.lineEditAddNewRecipient.setFocus()
        except PathDoesntExist as er:
            print(er)
            self.ui.lineEditLogPath.setFocus()
        else:
            self.checker_stop_event.clear()
            self.check_thread.delay = self._wait_time()
            self.check_thread.start()
            self.ui.pushButtonStart.setDisabled(True)
            self.ui.pushButtonStop.setEnabled(True)

    def stop_check(self):
        self.checker_stop_event.set()

        self.ui.pushButtonStart.setEnabled(True)
        self.ui.pushButtonStop.setEnabled(False)

    @property
    def _recipients(self):
        return [str(self.ui.listWidgetRecipients.item(i).text()) for i in range(self.ui.listWidgetRecipients.count())]

    def add_new_recipient(self):
        value = self.ui.lineEditAddNewRecipient.text()
        email_items = self._recipients
        if value in email_items:
            self._warning(f"{value} already in recipients list.")
        else:
            self.ui.listWidgetRecipients.addItem(value)
            self.ui.lineEditAddNewRecipient.setText("")
            self.ui.lineEditAddNewRecipient.setStyleSheet("color: rgb(137, 137, 137);")

    def remove_recipient(self):
        self.ui.listWidgetRecipients.takeItem(self.ui.listWidgetRecipients.currentRow())
        self.ui.listWidgetRecipients.clearSelection()
        self.ui.pushButtonRemoveRecipient.setDisabled(True)
        self.ui.lineEditAddNewRecipient.setFocus()

    def send_email_in_new_thread(self, dumps: [Dump]):
        self.email_sender_stop_event.clear()
        self.email_sender_thread.dumps = dumps
        self.email_sender_thread.start()

    def send_email(self, dumps: [Dump]):
        self.send_email_in_new_thread(dumps)

    def move_old_dumps(self, dumps):
        for dump in dumps:
            try:
                shutil.move(dump.full_path, self.ui.lineEditDumpStoringDirPath.text())
            except Exception as e:
                print(e)


if __name__ == '__main__':
    from tempfile import NamedTemporaryFile

    f = NamedTemporaryFile(prefix='lock01_dchecker', delete=True) if not [f for f in os.listdir(os.environ["TEMP"]) if
                                                                  f.find('lock01_dchecker') != -1] else sys.exit()

    app = QApplication([])
    application = DumpChecker()
    application.show()
    sys.exit(app.exec())
