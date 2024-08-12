from functools import lru_cache
from pathlib import Path


@lru_cache  # Cache results in memory as resources are not expected to change
def resources_dir(folder: str) -> str:
    """
    Get the absolute path to the resources folder.
    """
    app_path = Path(__file__).parent.parent
    resources_dir_path = app_path / "resources" / folder
    return str(resources_dir_path.absolute())
