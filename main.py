import json
import logging
import os
import re
import shutil
import smtplib
import socket
import sys
from threading import Event

import arrow
from PyQt5 import QtCore
from PyQt5.QtCore import QThread, QSettings, QCoreApplication, QFileInfo, QTimer
from PyQt5.QtWidgets import QMainWindow, QApplication, QMessageBox
from gmail import Message, GMail

from checker_ui import Ui_MainWindow
from constants import PathOf, UTILITY_REG_KEY, EMAIL_RE
from custom_elements import QLineEditWithEnterClickEvent
from dumps_db import DumpsDB
from exceptions import RecipientNotSetError, PathDoesntExist, NetworkConnectionError, DailyEmailQuotaExceededError
from helpers import load_and_get_configs, CONFIG_PATH, is_admin
from logger import get_logger


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

    @property
    def file_size(self):
        return FileSize(os.path.getsize(self._dump))

    @property
    def creation_time(self):
        return arrow.get(os.path.getmtime(self._dump))


class EmailSenderThread(QThread):
    EmailSenderThreadSignal = QtCore.pyqtSignal(object)
    EmailSendSignal = QtCore.pyqtSignal(object)
    EmailSendError = QtCore.pyqtSignal(object)

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
        logger.info("Start sending email")
        try:
            gmail.send(msg)
        except socket.gaierror as er:
            logger.exception(f"Can't send message.")
            self.EmailSendError.emit(NetworkConnectionError(er))

        except smtplib.SMTPDataError as e:
            logger.exception(f"Can't send message.")
            self.EmailSendError.emit(DailyEmailQuotaExceededError(e))
        except Exception as e:
            logger.exception(f"Email sending Error: {e}")
        else:
            self.EmailSenderThreadSignal.emit(dump_file_names)
            self.EmailSendSignal.emit(self.dumps)
            logger.info("Email send.")


class CheckThread(QThread):
    CheckerThreadSignal = QtCore.pyqtSignal(object)

    def __init__(self, parent, configs):
        super().__init__(parent)
        self.CONFIGS = configs

    def dumps_in_logs(self, log_path):
        dumps = []
        cwd, _, logs = next(os.walk(log_path))
        for filename in logs:
            name, extension = os.path.splitext(filename)
            if extension == ".dmp":
                dump = Dump(os.path.join(cwd, filename))
                dumps.append(dump)
        return dumps

    def run(self):
        logger.info(f"{arrow.now().format('DD-MM-YYYY HH:mm:ss'):=^70}")
        try:
            dumps = self.dumps_in_logs(self.CONFIGS["LOGS_PATH"]["SERVER"])
            if dumps:
                self.CheckerThreadSignal.emit(dumps)
            logger.info("=" * 70 + "\n")
        except Exception as ex:
            logger.exception(f"{ex.__class__.__name__}")


class DumpChecker(QMainWindow):
    def __init__(self, database):
        super(DumpChecker, self).__init__()
        self.database = database
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle(f"Dump checker {'(Administrator)' if is_admin() else ''}")

        self.settings = QSettings("HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", QSettings.NativeFormat)
        self.utility_full_path = QFileInfo(QCoreApplication.applicationFilePath()).filePath().replace("/", "\\")

        self.configs = self.load_default_values()
        # self.set_value_to_registy_key()

        self.check_thread = CheckThread(self, configs=self.configs)
        self.check_thread.CheckerThreadSignal.connect(self.send_email)

        self.email_sender_stop_event = Event()
        self.email_sender_thread = EmailSenderThread(self, configs=self.configs, stop_event=self.email_sender_stop_event)
        self.email_sender_thread.EmailSenderThreadSignal.connect(self.refresh_log_view_message)
        self.email_sender_thread.EmailSendSignal.connect(self.move_old_dumps)
        self.email_sender_thread.EmailSendError.connect(self.email_send_error)

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

        self.ui.pushButtonSave.clicked.connect(self.save_new_configs)
        self.ui.pushButtonSave.setDisabled(True)

        self.ui.spinBoxDays.valueChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))
        self.ui.spinBoxHours.valueChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))
        self.ui.spinBoxMinutes.valueChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))
        self.ui.spinBoxSeconds.valueChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))

        self.ui.lineEditLogPath.textChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))

        self.ui.CheckBoxSendDmp.stateChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))
        self.ui.сheckBoxRunWithSystem.stateChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))

        self.ui.lineEditEmailTitle.textChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))
        self.ui.textEditEmailMessage.textChanged.connect(lambda: self.ui.pushButtonSave.setEnabled(True))

        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.check)

        if self.configs["UTILITY_CONFIGS"]["AUTORUN"]:
            self.start_check()

    def set_value_to_registy_key(self):
        if self.ui.сheckBoxRunWithSystem.isChecked():
            current_registry_value = self.settings.value(UTILITY_REG_KEY)
            if current_registry_value is None:
                logger.info(f"Try to set new value '{self.utility_full_path}'")
                self.settings.setValue(UTILITY_REG_KEY, self.utility_full_path)
                if self.settings.value(UTILITY_REG_KEY) != self.utility_full_path:
                    logger.error(f"Can't add new key '{UTILITY_REG_KEY}' with value '{self.utility_full_path}' to '{self.settings.fileName()}'")
                    self.ui.сheckBoxRunWithSystem.setChecked(False)

            elif current_registry_value != self.utility_full_path:
                logger.info(f"Current registry value '{current_registry_value}' differs from new value '{self.utility_full_path}'")
                logger.info(f"Try to set new value '{self.utility_full_path}'")
                self.settings.setValue(UTILITY_REG_KEY, self.utility_full_path)
                if self.settings.value(UTILITY_REG_KEY) != self.utility_full_path:
                    logger.error(f"Can't add new key '{UTILITY_REG_KEY}' with value '{self.utility_full_path}' to '{self.settings.fileName()}'")
                    self.ui.сheckBoxRunWithSystem.setChecked(False)

                # self.settings.setValue(UTILITY_REG_KEY, self.utility_full_path)
                # if self.settings.value(UTILITY_REG_KEY) is None:
                #     logger.error(f"Can't add new key '{UTILITY_REG_KEY}' with value '{self.utility_full_path}' to '{self.settings.fileName()}'")
                #     self.ui.сheckBoxRunWithSystem.setChecked(False)

        else:
            self.settings.remove(UTILITY_REG_KEY)
            if self.settings.value(UTILITY_REG_KEY) is not None:
                logger.error(f"Can't remove utility registry key '{UTILITY_REG_KEY}' from '{self.settings.fileName()}'")
                self.ui.сheckBoxRunWithSystem.setChecked(True)
            else:
                logger.info(f"Registry key '{UTILITY_REG_KEY}' successfully")

        self.settings.sync()

    def refresh_log_view_message(self, message):
        old_value = self.ui.textEditLogView.toPlainText()
        self.ui.textEditLogView.setText(f"{old_value}\n{arrow.now().format('DD-MM-YYYY HH:mm:ss'):=^70}\n{message}")
        self.ui.textEditLogView.verticalScrollBar().setValue(self.ui.textEditLogView.verticalScrollBar().maximum())

    def email_send_error(self, error):
        if isinstance(error, DailyEmailQuotaExceededError):
            logger.error(f"Limit exceeded: {error}")
            # self._warning(f"{error.args[0]}.", "Gmail daily quota limit warning")
            self.refresh_log_view_message(f"Gmail warning: {error.args[0]}.")

        elif isinstance(error, NetworkConnectionError):
            logger.error(f"Network connection error: {error}")
            # self.stop_check()
            # self._warning("Please, check your internet connection and restart.", "Network error")
            self.refresh_log_view_message(f"Network error. Please, check your internet connection.")

    def add_new_recipient_by_enter_click(self, event):
        if event.key() == QtCore.Qt.Key_Return:
            if self.validate_entered_email():
                self.add_new_recipient()

    def validate_entered_email(self):
        add_email_line_edit_current_value = self.ui.lineEditAddNewRecipient.text()

        if add_email_line_edit_current_value and EMAIL_RE.match(add_email_line_edit_current_value):
            self.ui.lineEditAddNewRecipient.setStyleSheet("color: rgb(0, 150, 10);")
            self.ui.pushButtonAddNewRecipient.setEnabled(True)
            return True
        elif not add_email_line_edit_current_value:
            self.ui.lineEditAddNewRecipient.setStyleSheet("color: rgb(137, 137, 137);")
            return False

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

        self.ui.lineEditEmailTitle.setText(CONFIGS["EMAIL"]["TITLE"])

        self.ui.textEditEmailMessage.setText(CONFIGS["EMAIL"]["SUBJECT"])

        self.ui.CheckBoxSendDmp.setChecked(CONFIGS["EMAIL"]["SEND_DMP_FILES"])

        self.ui.listWidgetRecipients.addItems(sorted(CONFIGS["EMAIL"]["RECIPIENT_ADDRESSES"]))
        print(self.settings.value(UTILITY_REG_KEY) == self.utility_full_path, 33333)
        self.ui.сheckBoxRunWithSystem.setChecked(self.settings.value(UTILITY_REG_KEY) == self.utility_full_path)

        return CONFIGS

    def save_new_configs(self):
        CONFIGS = self.configs
        self.set_value_to_registy_key()

        delay = CONFIGS["DELAY"]
        delay["DAYS"] = self.ui.spinBoxDays.value()
        delay["HOURS"] = self.ui.spinBoxHours.value()
        delay["MINUTES"] = self.ui.spinBoxMinutes.value()
        delay["SECONDS"] = self.ui.spinBoxSeconds.value()

        log_path = self.ui.lineEditLogPath.text().strip()
        if os.path.isdir(log_path):
            CONFIGS["LOGS_PATH"]["SERVER"] = log_path
        else:
            self._warning(f"Directory '{log_path}' doesn't exist.\nLoad default value")
            self.ui.lineEditLogPath.setText(CONFIGS["LOGS_PATH"]["SERVER"])

        CONFIGS["EMAIL"]["TITLE"] = self.ui.lineEditEmailTitle.text()

        CONFIGS["EMAIL"]["SUBJECT"] = self.ui.textEditEmailMessage.toPlainText()

        CONFIGS["EMAIL"]["SEND_DMP_FILES"] = self.ui.CheckBoxSendDmp.isChecked()

        CONFIGS["EMAIL"]["RECIPIENT_ADDRESSES"] = self._recipients
        with open(CONFIG_PATH, "w") as f:
            f.write(json.dumps(CONFIGS, indent=4))

        self.configs = load_and_get_configs()
        self.ui.pushButtonSave.setDisabled(True)
        self.ui.textEditLogView.setFocus()

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

    def check_path_exist(self, path: str, path_of):
        if not os.path.isdir(path):
            self._warning(f"Please add correct {path_of}")
            raise PathDoesntExist("Incorrect log path!", path_of)

    def start_check(self):
        self.ui.textEditLogView.setFocus()
        self.check()

    def check(self):
        try:
            self.check_recipient()
            self.check_path_exist(self.configs["LOGS_PATH"]["SERVER"], PathOf.SERVER)
        except RecipientNotSetError as er:
            logger.error(er)
            self.ui.lineEditAddNewRecipient.setFocus()
        except PathDoesntExist as er:
            logger.error(er)
            if er.path_to == PathOf.SERVER:
                self.ui.lineEditLogPath.setFocus()

        else:
            self.check_timer.start(self._wait_time() * 1000)
            self.check_thread.start()
            self.check_thread.CONFIGS = self.configs
            self.ui.pushButtonStart.setDisabled(True)
            self.ui.pushButtonStop.setEnabled(True)

    def stop_check(self):
        self.check_timer.stop()

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
            self.ui.pushButtonSave.setEnabled(True)

    def remove_recipient(self):
        self.ui.listWidgetRecipients.takeItem(self.ui.listWidgetRecipients.currentRow())
        self.ui.listWidgetRecipients.clearSelection()
        self.ui.pushButtonRemoveRecipient.setDisabled(True)
        self.ui.lineEditAddNewRecipient.setFocus()
        self.ui.pushButtonSave.setEnabled(True)

    def send_email(self, dumps: [Dump]):
        self.email_sender_stop_event.clear()
        d = []
        db_current_items = self.database.all_values

        for dump in dumps:
            if not self.database.check_exist(dump.file_name):
                d.append(dump)
        if not d:
            return
        self.email_sender_thread.dumps = d
        self.email_sender_thread.configs = self.configs
        self.email_sender_thread.start()

        items_for_remove_from_db = set(db_current_items) - set([item.file_name for item in dumps])
        logger.info(f"Remove old dump names: {items_for_remove_from_db}")
        for item in items_for_remove_from_db:
            self.database.delete(item)

    def move_old_dumps(self, dumps):
        for dump in dumps:
            try:
                self.database.insert(dump.file_name)
            except Exception as e:
                logger.error(e)


if __name__ == '__main__':
    from tempfile import NamedTemporaryFile

    argv = sys.argv
    log_levels = {
        "INFO":  logging.INFO,
        "DEBUG": logging.DEBUG,
    }
    level = str(argv[1]).upper() if len(argv) == 2 else logging.NOTSET
    logger = get_logger("DumpChecker", log_level=log_levels.get(level, logging.NOTSET))
    logger.info("Starting utility...")

    if not [f for f in os.listdir(os.environ["TEMP"]) if f.find('lock01_dchecker') != -1]:
        NamedTemporaryFile(prefix='lock01_dchecker', delete=True)
        try:
            app = QApplication([])
            application = DumpChecker(DumpsDB())
            application.show()
            exit_code = app.exec_()
        except Exception as e:
            exit_code = 1
            logger.exception(f"Fatal error. Exit code {exit_code}. {e}")
            sys.exit(exit_code)

        else:
            logger.info(f"exit code: {exit_code}\n")
            sys.exit(exit_code)
    else:
        logger.info("Another utility instance already running. Exit.")
        sys.exit()
