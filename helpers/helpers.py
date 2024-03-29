import json
import os

CONFIG_PATH = os.path.join(os.environ["TEMP"], "checker_config.json")


def load_default_config():
    default_json = """{
  "LOGS_PATH": {
    "SERVER": "C:\\\\ProgramData\\\\AxxonSoft\\\\AxxonNext\\\\Logs"
    },

  "DUMPS_STORING_DIRECTORY": "C:\\\\ProgramData\\\\AxxonSoft\\\\AxxonNext\\\\Logs\\\\OldButGold",

  "DELAY": {
    "DAYS": 0,
    "HOURS": 0,
    "MINUTES": 1,
    "SECONDS": 0
    },

  "EMAIL":{

    "RECIPIENT_ADDRESSES": [

    ],
    "ATTACH_FILES_MAX_SIZE": 25,
    "SEND_DMP_FILES": false,
    "TITLE": "DMP in logs!",
    "SUBJECT": ""
  },
  
  "UTILITY_CONFIGS": {
      "CHECKER_AUTH": {
        "LOGIN": "andumpchecker@gmail.com",
        "PASSWORD": "ye9-tyF-j9L-HUR"
    },
    "LAUNCH_WITH_SYSTEM": false,
    "AUTORUN": true
    }
}

    """
    with open(CONFIG_PATH, "w") as f:
        f.write(json.loads(json.dumps(default_json)))


def load_and_get_configs():
    try:
        with open(CONFIG_PATH, encoding="UTF-8") as f:
            configs = json.load(f)
            return configs
    except (FileNotFoundError, json.decoder.JSONDecodeError) as er:
        print(f"Error '{er}' is occurred, while loading 'checker_config.json'.\n"
              f"Creating new config file with default parameters.")
        load_default_config()
        return load_and_get_configs()


def is_admin():
    import ctypes

    try:
        return os.getuid() == 0
    except AttributeError:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
