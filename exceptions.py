class RecipientNotSetError(Exception):
    pass


class ErrorWhileCopying(Exception):
    pass


class EmailSendingError(Exception):
    pass


class PathDoesntExist(Exception):
    def __init__(self, message, path_of,  *args, **kwargs):
        super().__init__(message, *args, **kwargs)
        self.path_to = path_of
