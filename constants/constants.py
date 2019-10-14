import re


class PathOf(str):
    SERVER = "Server's log path"
    DMP_STORING = "Old DMP-s storing path"


UTILITY_REG_KEY = "DumpChecker"

EMAIL_RE = re.compile(r"(^[-!#$%&'*+/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_`{}|~0-9A-Z]+)*"  # dot-atom
                      r'|^"([\001-\010\013\014\016-\037!#-\[\]-\177]|\\[\001-011\013\014\016-\177])*"'  # quoted-string
                      r')@(?:[A-Z0-9-]+\.)+[A-Z]{2,6}$', re.IGNORECASE)
