from os import environ

import yaml
from dotenv import find_dotenv
from pydantic import ValidationError

from app.helpers.config_models.root import RootModel


class ConfigNotFound(Exception):
    pass


class ConfigBadFormat(Exception):
    pass


def load_config() -> RootModel:
    config: RootModel | None = None
    config_env = "CONFIG_JSON"
    config_file = "config.yaml"

    if config_env in environ:
        config = RootModel.model_validate_json(environ[config_env])
        print(f'Config loaded from env "{config_env}"')  # noqa: T201
        return config

    print(f'Cannot find env "{config_env}", trying to load from file')  # noqa: T201
    path = find_dotenv(filename=config_file)
    if not path:
        raise ConfigNotFound(f'Cannot find config file "{config_file}"')
    try:
        with open(
            encoding="utf-8",
            file=path,
        ) as f:
            config = RootModel.model_validate(yaml.safe_load(f))
            print(f'Config loaded from file "{path}"')  # noqa: T201
            return config
    except ValidationError as e:
        raise ConfigBadFormat(f"Config values are not valid: {e.errors()}") from e
    except Exception as e:
        raise ConfigBadFormat("Config YAML format is not valid") from e


CONFIG = load_config()
