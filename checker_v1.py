import json
import os
import shutil
from time import sleep

import arrow
from gmail import GMail, Message
from tendo import singleton


class RecipientNotSetError(Exception):
    pass


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


def dumps_in_logs(logs_dir):
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


def send_email(mail_configs: dict, dumps: [Dump]):
    recipient = ";".join(mail_configs["RECIPIENT_ADDRESSES"])

    dump_file_names = "\n".join([d.file_name for d in dumps])
    message = f'{mail_configs["SUBJECT"]}\n{dump_file_names}'

    auth = mail_configs["CHECKER_AUTH"]
    gmail = GMail(auth["LOGIN"], auth["PASSWORD"])
    if bool(mail_configs.get("SEND_DMP_FILES", False)):
        attachments = [d.full_path for d in dumps]
    else:
        attachments = []
    msg = Message(mail_configs["TITLE"], to=recipient, text=message, attachments=attachments)

    print(message)
    gmail.send(msg)


def move_old_dumps(dumps, dumps_storing_dir):
    for dump in dumps:
        shutil.move(dump.full_path, dumps_storing_dir)


def check(configs):
    if not os.path.isdir(configs["DUMPS_STORING_DIRECTORY"]):
        os.makedirs(configs["DUMPS_STORING_DIRECTORY"])
    print(f"{arrow.now().format('DD-MM-YYYY HH:mm:ss'):=^70}")

    dumps = dumps_in_logs(configs["LOGS_PATH"])
    if dumps:
        send_email(dumps=dumps, mail_configs=configs["EMAIL"])
        move_old_dumps(dumps, configs["DUMPS_STORING_DIRECTORY"])

    print("=" * 70 + "\n")


def load_default_config():
    default_json = """{
  "LOGS_PATH": {
    "SERVER": "C:\\\\ProgramData\\\\AxxonSoft\\\\AxxonNext\\\\Logs"
    },

  "DUMPS_STORING_DIRECTORY": "C:\\\\ProgramData\\\\AxxonSoft\\\\AxxonNext\\\\Logs\\\\OldDumps",

  "DELAY": {
    "DAYS": 0,
    "HOURS": 0,
    "MINUTES": 1,
    "SECONDS": 0
    },

  "EMAIL":{
    "CHECKER_AUTH":{
      "LOGIN": "andumpchecker@gmail.com",
      "PASSWORD": "ye9-tyF-j9L-HUR"
    }, 

    "RECIPIENT_ADDRESSES": [

    ],
    
    "SEND_DMP_FILES": false,
    "TITLE": "DMP in logs!",
    "SUBJECT": "Next dmps in logs:"
  }
}

    """
    with open("checker_config.json", "w") as f:
        f.write(json.loads(json.dumps(default_json)))


def load_and_get_configs():
    try:
        with open("checker_config.json", encoding="UTF-8") as f:
            configs = json.load(f)
            print(22222222, configs)
            return configs
    except (FileNotFoundError, json.decoder.JSONDecodeError) as er:
        print(f"Error '{er}' is occurred, while loading 'checker_config.json'.\nCreating new config file with default parameters.")
        load_default_config()
        return load_and_get_configs()


def wait_time(delay):
    return delay["DAYS"] * 24 * 60 * 60 + delay["HOURS"] * 60 * 60 + delay["MINUTES"] * 60 + delay["SECONDS"]


def check_recipient(mail_configs):
    recipient = mail_configs["RECIPIENT_ADDRESSES"]
    if not recipient:
        raise RecipientNotSetError("Email recipient is not found in 'checker_config.json'\n"
                                   "Please enter comma separeated addresses in double quotes to field 'RECIPIENT_ADDRESSES' in 'checker_config.json' and restart.")


if __name__ == '__main__':
    me = singleton.SingleInstance()
    CONFIGS = load_and_get_configs()

    delay = wait_time(CONFIGS["DELAY"])

    while True:
        try:
            check_recipient(CONFIGS["EMAIL"])
            check(CONFIGS)
            sleep(delay)
        except RecipientNotSetError as er:
            print(er)
            sleep(5)
            exit(1)
        except Exception as ex:
            print(ex)
            sleep(30)
