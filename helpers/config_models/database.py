from pydantic import BaseModel
from os import environ


class DatabaseModel(BaseModel):
    sqlite_path: str = environ.get("SQLITE_PATH", ".local.sqlite")
