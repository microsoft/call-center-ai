from os import environ

import yaml
from dotenv import find_dotenv
from pydantic import ValidationError

from app.helpers.config_models.root import RootModel


def load_config() -> RootModel:
    config: RootModel | None = None
    config_env = "CONFIG_JSON"
    config_file = "config.yaml"

    # Try to load JSON from env
    if config_env in environ:
        # Validate
        config = RootModel.model_validate_json(environ[config_env])
        print(f'Config loaded from env "{config_env}"')  # noqa: T201
        return config

    # Try to load YAML from file
    print(f'Cannot find env "{config_env}", trying to load from file')  # noqa: T201
    path = find_dotenv(filename=config_file)

    # Raise error if file not found
    if not path:
        raise ValueError(f'Cannot find config file "{config_file}"')

    # Load config from file
    with open(
        encoding="utf-8",
        file=path,
    ) as f:
        # Validate
        config = RootModel.model_validate(yaml.safe_load(f))
        print(f'Config loaded from file "{path}"')  # noqa: T201
        return config


# Load config
try:
    CONFIG = load_config()

# Pretty print validation errors
except ValidationError as e:
    err = "Config values are not valid:"
    for i, error in enumerate(e.errors()):
        err += f"\n{i + 1}. At {'.'.join(str(loc) for loc in error['loc'])}: {error['msg']} (input value: {error['input']})"
    raise ValueError(err)
