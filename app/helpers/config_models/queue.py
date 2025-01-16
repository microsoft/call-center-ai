from functools import cached_property

from pydantic import BaseModel


class QueueModel(BaseModel, frozen=True):
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
