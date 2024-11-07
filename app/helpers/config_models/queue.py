from functools import cache

from pydantic import BaseModel


class QueueModel(BaseModel, frozen=True):
    account_url: str
    call_name: str
    post_name: str
    sms_name: str

    @cache
    def call(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.call_name,
        )

    @cache
    def post(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.post_name,
        )

    @cache
    def sms(self):
        from app.persistence.azure_queue_storage import AzureQueueStorage

        return AzureQueueStorage(
            account_url=self.account_url,
            name=self.sms_name,
        )
