from helpers.config import CONFIG
from models.call import CallModel
from models.message import (
    StyleEnum as MessageStyleEnum,
    ActionEnum as MessageActionEnum,
)
from semantic_kernel.plugin_definition import kernel_function


class WorkflowPlugin:
    _call: CallModel

    def __init__(self, call: CallModel):
        self._call = call

    @kernel_function(
        description="Get the available styles",
        name="styles",
    )
    async def styles(self) -> str:
        res = ", ".join([style.value for style in MessageStyleEnum])
        return f"[{res}]"

    @kernel_function(
        description="Get the available actions",
        name="actions",
    )
    async def actions(self) -> str:
        res = ", ".join([action.value for action in MessageActionEnum])
        return f"[{res}]"

    @kernel_function(
        description="Get the company name of the bot",
        name="botCompany",
    )
    async def bot_company(self) -> str:
        return CONFIG.workflow.bot_company

    @kernel_function(
        description="Get the phone number of the bot",
        name="botPhoneNumber",
    )
    async def bot_phone_number(self) -> str:
        return CONFIG.communication_service.phone_number

    @kernel_function(
        description="Get the name of the bot",
        name="botName",
    )
    async def bot_name(self) -> str:
        return CONFIG.workflow.bot_name
