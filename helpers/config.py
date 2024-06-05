# Load "CONFIG_JSON" for debug purposes
from dotenv import load_dotenv, find_dotenv

# Load recursively from relative, like "config.yaml"
load_dotenv(find_dotenv())

# Load deps
from helpers.config_models.root import RootModel
from os import environ
from pydantic import ValidationError
import yaml


_CONFIG_ENV = "CONFIG_JSON"
_CONFIG_FILE = "config.yaml"


class ConfigNotFound(Exception):
    pass


class ConfigBadFormat(Exception):
    pass


if _CONFIG_ENV in environ:
    CONFIG = RootModel.model_validate_json(environ[_CONFIG_ENV])
    print(f'Config from env "{_CONFIG_ENV}" loaded')

else:
    print(f'Config from env "{_CONFIG_ENV}" not found')
    path = find_dotenv(filename=_CONFIG_FILE)
    if not path:
        raise ConfigNotFound(f'Cannot find config file "{_CONFIG_FILE}"')
    try:
        with open(path, encoding="utf-8") as f:
            CONFIG = RootModel.model_validate(yaml.safe_load(f))
    except ValidationError as e:
        raise ConfigBadFormat(f"Config values are not valid: {e.errors()}")
    except Exception as e:
        raise ConfigBadFormat(f"Config YAML format is not valid") from e
    print(f'Config "{path}" loaded')
