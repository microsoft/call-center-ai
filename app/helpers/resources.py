from os import getcwd
from os.path import abspath, join
from pathlib import Path

from app.helpers.cache import lru_cache


@lru_cache()  # Cache results in memory as resources are not expected to change
def resources_dir(folder: str) -> str:
    """
    Get the absolute path to the resources folder.
    """
    return join(_local_dir("resources"), folder)


def _local_dir(folder: str) -> str:
    """
    Get the absolute path to a local folder.
    """
    return str(Path(join(abspath(getcwd()), "app", folder)).resolve().absolute())
