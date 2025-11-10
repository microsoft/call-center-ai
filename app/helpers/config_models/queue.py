from enum import Enum
from functools import cached_property

from pydantic import BaseModel, ValidationInfo, field_validator


class ModeEnum(str, Enum):
    AZURE_QUEUE_STORAGE = "azure_queue_storage"
    """Use Azure Storage Queues."""
    LOCAL = "local"
    """Use local in-memory queues (development only)."""


class AzureQueueStorageModel(BaseModel, frozen=True):
    account_url: str
    call_name: str
    post_name: str
    sms_name: str
    training_name: str

    @cached_property
    def call(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.call_name,
        )

    @cached_property
    def post(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.post_name,
        )

    @cached_property
    def sms(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.sms_name,
        )

    @cached_property
    def training(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.training_name,
        )


class LocalQueueModel(BaseModel, frozen=True):
    """Local in-memory queue configuration (for development)."""

    @cached_property
    def call(self):
        from app.persistence.local_queue import LocalQueue

        return LocalQueue(name="call")

    @cached_property
    def post(self):
        from app.persistence.local_queue import LocalQueue

        return LocalQueue(name="post")

    @cached_property
    def sms(self):
        from app.persistence.local_queue import LocalQueue

        return LocalQueue(name="sms")

    @cached_property
    def training(self):
        from app.persistence.local_queue import LocalQueue

        return LocalQueue(name="training")


class QueueModel(BaseModel):
    azure_queue_storage: AzureQueueStorageModel | None = None
    mode: ModeEnum = ModeEnum.AZURE_QUEUE_STORAGE
    local: LocalQueueModel | None = LocalQueueModel()

    @field_validator("azure_queue_storage")
    @classmethod
    def _validate_azure_queue_storage(
        cls,
        azure_queue_storage: AzureQueueStorageModel | None,
        info: ValidationInfo,
    ) -> AzureQueueStorageModel | None:
        if (
            not azure_queue_storage
            and info.data.get("mode", None) == ModeEnum.AZURE_QUEUE_STORAGE
        ):
            raise ValueError("Azure Queue Storage config required")
        return azure_queue_storage

    @field_validator("local")
    @classmethod
    def _validate_local(
        cls,
        local: LocalQueueModel | None,
        info: ValidationInfo,
    ) -> LocalQueueModel | None:
        if not local and info.data.get("mode", None) == ModeEnum.LOCAL:
            raise ValueError("Local queue config required")
        return local

    @cached_property
    def call(self):
        if self.mode == ModeEnum.AZURE_QUEUE_STORAGE:
            assert self.azure_queue_storage
            return self.azure_queue_storage.call
        assert self.local
        return self.local.call

    @cached_property
    def post(self):
        if self.mode == ModeEnum.AZURE_QUEUE_STORAGE:
            assert self.azure_queue_storage
            return self.azure_queue_storage.post
        assert self.local
        return self.local.post

    @cached_property
    def sms(self):
        if self.mode == ModeEnum.AZURE_QUEUE_STORAGE:
            assert self.azure_queue_storage
            return self.azure_queue_storage.sms
        assert self.local
        return self.local.sms

    @cached_property
    def training(self):
        if self.mode == ModeEnum.AZURE_QUEUE_STORAGE:
            assert self.azure_queue_storage
            return self.azure_queue_storage.training
        assert self.local
        return self.local.training
