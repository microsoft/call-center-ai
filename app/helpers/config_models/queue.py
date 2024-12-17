from functools import lru_cache

from pydantic import BaseModel


class QueueModel(BaseModel, frozen=True):
    account_url: str
    call_name: str
    post_name: str
    sms_name: str
    training_name: str

    @lru_cache
    def call(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.call_name,
        )

    @lru_cache
    def post(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.post_name,
        )

    @lru_cache
    def sms(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.sms_name,
        )

    @lru_cache
    def training(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.training_name,
        )
