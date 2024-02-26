from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, validator
from typing import Any, List, Union
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from inspect import getmembers, isfunction
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall
import json
import re
from json_repair import repair_json


FUNC_NAME_SANITIZER_R = r"[^a-zA-Z0-9_-]"


class StyleEnum(str, Enum):
    """
    Voice styles the Azure AI Speech Service supports.

    Doc:
    - Speaking styles: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice#use-speaking-styles-and-roles
    - Support by language: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts#voice-styles-and-roles
    """

    CHEERFUL = "cheerful"
    NONE = "none"  # This is not a valid style, but we use it in the code to indicate no style
    SAD = "sad"


class ActionEnum(str, Enum):
    CALL = "call"
    HANGUP = "hangup"
    SMS = "sms"
    TALK = "talk"


class PersonaEnum(str, Enum):
    ASSISTANT = "assistant"
    HUMAN = "human"
    TOOL = "tool"


class ToolModel(BaseModel):
    content: str = ""
    function_arguments: str = ""
    function_name: str = ""
    tool_id: str = ""

    def to_openai(self) -> ChatCompletionMessageToolCallParam:
        return ChatCompletionMessageToolCallParam(
            id=self.tool_id,
            type="function",
            function={
                "arguments": self.function_arguments,
                "name": "-".join(
                    re.sub(
                        FUNC_NAME_SANITIZER_R,
                        "-",
                        self.function_name,
                    ).split("-")
                ),  # Sanitize with dashes then deduplicate dashes, backward compatibility with old models
            },
        )

    @validator("function_name")
    def validate_function_name(cls, v, values) -> str:
        if v not in ToolModel._available_function_names():
            raise ValueError(
                f'Invalid function names "{v}", available are {ToolModel._available_function_names()}'
            )
        return v

    def __add__(self, other: ChoiceDeltaToolCall) -> "ToolModel":
        if other.id:
            self.tool_id = other.id
        if other.function:
            if other.function.name:
                self.function_name = other.function.name
            if other.function.arguments:
                self.function_arguments += other.function.arguments
        return self

    async def execute_function(self, plugins: object) -> None:
        from helpers.logging import build_logger

        logger = build_logger(__name__)
        name = self.function_name

        # Try to fix JSON args to catch some LLM hallucinations
        # See: https://community.openai.com/t/gpt-4-1106-preview-messes-up-function-call-parameters-encoding/478500
        args: dict[str, Any] = repair_json(
            json_str=self.function_arguments.replace("\\\\", "\\").replace("\\n", ""),
            return_objects=True,
        )  # type: ignore

        if args is None:
            logger.warn(
                f"Error decoding JSON args for function {name}: {self.function_arguments[:20]}...{self.function_arguments[-20:]}"
            )
            self.content = "Not executed, bad arguments format"
            return

        try:
            res = await getattr(plugins, name)(**args)
            logger.info(f"Executing function {name} ({args}): {res[:20]}...{res[-20:]}")
        except Exception as e:
            logger.warn(
                f"Error executing function {self.function_name} with args {args}: {e}"
            )
            res = f"Not executed, error: {e}"
        self.content = res

    @staticmethod
    def _available_function_names() -> List[str]:
        from helpers.llm_plugins import LlmPlugins

        return [name for name, _ in getmembers(LlmPlugins, isfunction)]


class MessageModel(BaseModel):
    # Immutable fields
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    # Editable fields
    action: ActionEnum = ActionEnum.TALK
    content: str
    persona: PersonaEnum
    style: StyleEnum = StyleEnum.NONE
    tool_calls: List[ToolModel] = []

    def to_openai(
        self,
    ) -> List[
        Union[
            ChatCompletionAssistantMessageParam,
            ChatCompletionToolMessageParam,
            ChatCompletionUserMessageParam,
        ]
    ]:
        if self.persona == PersonaEnum.HUMAN:
            return [
                ChatCompletionUserMessageParam(
                    content=f"action={self.action.value} style={self.style.value} {self.content}",
                    role="user",
                )
            ]

        elif self.persona == PersonaEnum.ASSISTANT:
            if not self.tool_calls:
                return [
                    ChatCompletionAssistantMessageParam(
                        content=f"action={self.action.value} style={self.style.value} {self.content}",
                        role="assistant",
                    )
                ]

        res = []
        res.append(
            ChatCompletionAssistantMessageParam(
                content=f"action={self.action.value} style={self.style.value} {self.content}",
                role="assistant",
                tool_calls=[tool_call.to_openai() for tool_call in self.tool_calls],
            )
        )
        for tool_call in self.tool_calls:
            res.append(
                ChatCompletionToolMessageParam(
                    content=tool_call.content,
                    role="tool",
                    tool_call_id=tool_call.tool_id,
                )
            )
        return res
