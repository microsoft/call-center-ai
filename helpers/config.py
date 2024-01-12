# Load "CONFIG_JSON" for debug purposes
from dotenv import load_dotenv, find_dotenv

# Load recursively from relative, like "config.yaml"
load_dotenv(find_dotenv())

# Load deps
from helpers.config_models.root import RootModel
import os
import yaml


CONFIG_ENV = "CONFIG_JSON"
CONFIG_FILE = "config.yaml"


class ConfigNotFound(Exception):
    pass


class ConfigBadFormat(Exception):
    pass


if CONFIG_ENV in os.environ:
    CONFIG = RootModel.model_validate_json(os.environ[CONFIG_ENV])
    print(f'Config from env "{CONFIG_ENV}" loaded')

else:
    print(f'Config from env "{CONFIG_ENV}" not found')
    path = find_dotenv(filename=CONFIG_FILE)
    if not path:
        raise ConfigNotFound(f'Cannot find config file "{CONFIG_FILE}"')
    try:
        with open(path, "r", encoding="utf-8") as f:
            CONFIG = RootModel.model_validate(yaml.safe_load(f))
    except Exception as e:
        raise ConfigBadFormat(f'Config "{path}" is not valid YAML') from e
    print(f'Config "{path}" loaded')
