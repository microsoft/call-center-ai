from datetime import datetime, UTC
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional, Tuple, Union
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from inspect import getmembers, isfunction
from json_repair import repair_json
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall
import re
import json


_FUNC_NAME_SANITIZER_R = r"[^a-zA-Z0-9_-]"
_MESSAGE_ACTION_R = r"action=([a-z_]*)( .*)?"
_MESSAGE_STYLE_R = r"style=([a-z_]*)( .*)?"


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
                        _FUNC_NAME_SANITIZER_R,
                        "-",
                        self.function_name,
                    ).split("-")
                ),  # Sanitize with dashes then deduplicate dashes, backward compatibility with old models
            },
        )

    @field_validator("function_name")
    def validate_function_name(cls, function_name: str) -> str:
        if function_name not in ToolModel._available_function_names():
            raise ValueError(
                f'Invalid function names "{function_name}", available are {ToolModel._available_function_names()}'
            )
        return function_name

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
        from helpers.logging import build_logger, TRACER

        logger = build_logger(__name__)
        json_str = self.function_arguments
        name = self.function_name

        # Try to fix JSON args to catch LLM hallucinations
        # See: https://community.openai.com/t/gpt-4-1106-preview-messes-up-function-call-parameters-encoding/478500
        args: dict[str, Any] = repair_json(
            json_str=json_str,
            return_objects=True,
        )  # type: ignore

        if not args:
            logger.warn(
                f"Error decoding JSON args for function {name}: {self.function_arguments[:20]}...{self.function_arguments[-20:]}"
            )
            self.content = f"Bad arguments, available are {ToolModel._available_function_names()}. Please try again."
            return

        with TRACER.start_as_current_span("execute_function") as span:
            span.set_attribute("name", name)
            span.set_attribute("args", json.dumps(args))
            try:
                res = await getattr(plugins, name)(**args)
                logger.info(
                    f"Executing function {name} ({args}): {res[:20]}...{res[-20:]}"
                )
            except Exception as e:
                logger.warn(
                    f"Error executing function {self.function_name} with args {args}: {e}"
                )
                res = f"Error: {e}. Please try again."
            self.content = res

    @staticmethod
    def _available_function_names() -> list[str]:
        from helpers.llm_tools import LlmPlugins

        return [name for name, _ in getmembers(LlmPlugins, isfunction)]


class MessageModel(BaseModel):
    # Immutable fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), frozen=True)
    # Editable fields
    action: ActionEnum = ActionEnum.TALK
    content: str
    persona: PersonaEnum
    style: StyleEnum = StyleEnum.NONE
    tool_calls: list[ToolModel] = []

    @field_validator("created_at")
    def validate_created_at(cls, created_at: datetime) -> datetime:
        """
        Ensure the created_at field is timezone-aware.

        Backward compatibility with models created before the timezone was added. All dates require the same timezone to be compared.
        """
        if not created_at.tzinfo:
            return created_at.replace(tzinfo=UTC)
        return created_at

    def to_openai(
        self,
    ) -> list[
        Union[
            ChatCompletionAssistantMessageParam,
            ChatCompletionToolMessageParam,
            ChatCompletionUserMessageParam,
        ]
    ]:
        # Removing newlines from the content to avoid hallucinations issues with GPT-4 Turbo
        content = " ".join(self.content.splitlines()).strip()

        if self.persona == PersonaEnum.HUMAN:
            return [
                ChatCompletionUserMessageParam(
                    content=f"action={self.action.value} {content}",
                    role="user",
                )
            ]

        elif self.persona == PersonaEnum.ASSISTANT:
            if not self.tool_calls:
                return [
                    ChatCompletionAssistantMessageParam(
                        content=f"action={self.action.value} style={self.style.value} {content}",
                        role="assistant",
                    )
                ]

        res = []
        res.append(
            ChatCompletionAssistantMessageParam(
                content=f"action={self.action.value} style={self.style.value} {content}",
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


def remove_message_action(text: str) -> str:
    """
    Remove action from content. AI often adds it by mistake event if explicitly asked not to.
    """
    res = re.match(_MESSAGE_ACTION_R, text)
    if not res:
        return text.strip()
    content = res.group(2)
    return content.strip() if content else ""


def extract_message_style(text: str) -> Tuple[Optional[StyleEnum], str]:
    """
    Detect the style of a message.
    """
    res = re.match(_MESSAGE_STYLE_R, text)
    if not res:
        return None, text
    try:
        content = res.group(2)
        return StyleEnum(res.group(1)), (content.strip() if content else "")
    except ValueError:
        return None, text
