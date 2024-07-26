import os
from functools import lru_cache
from os import path
from pathlib import Path


@lru_cache  # Cache results in memory as resources are not expected to change
def resources_dir(folder: str) -> str:
    """
    Get the absolute path to the resources folder.
    """
    return str(
        Path(
            path.join(
                os.path.abspath(os.getcwd()),
                "resources",
                folder,
            )
        )
        .resolve()
        .absolute()
    )
