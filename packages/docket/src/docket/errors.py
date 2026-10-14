"""The one error type a user acts on."""


class DocketError(Exception):
    """A failure the user can do something about: the CLI prints it as one line, without a traceback."""
