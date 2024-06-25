from datetime import datetime, UTC
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional, Union
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
_MESSAGE_ACTION_R = r"(?:action=*([a-z_]*))? *(.*)"
_MESSAGE_STYLE_R = r"(?:style=*([a-z_]*))? *(.*)"


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
        from helpers.logging import logger, tracer

        json_str = self.function_arguments
        name = self.function_name

        # Confirm the function name exists, this is a security measure to prevent arbitrary code execution, plus, Pydantic validator is not used on purpose to comply with older tools plugins
        if name not in ToolModel._available_function_names():
            res = f"Invalid function names {name}, available are {ToolModel._available_function_names()}."
            logger.warning(res)
            self.content = res
            return

        # Try to fix JSON args to catch LLM hallucinations
        # See: https://community.openai.com/t/gpt-4-1106-preview-messes-up-function-call-parameters-encoding/478500
        args: dict[str, Any] = repair_json(
            json_str=json_str,
            return_objects=True,
        )  # type: ignore

        if not isinstance(args, dict):
            logger.warning(
                f"Error decoding JSON args for function {name}: {self.function_arguments[:20]}...{self.function_arguments[-20:]}"
            )
            self.content = f"Bad arguments, available are {ToolModel._available_function_names()}. Please try again."
            return

        with tracer.start_as_current_span(
            name="execute_function",
            attributes={
                "args": json.dumps(args),
                "name": name,
            },
        ) as span:
            try:
                res = await getattr(plugins, name)(**args)
                res_log = f"{res[:20]}...{res[-20:]}"
                logger.info(f"Executing function {name} ({args}): {res_log}")
            except TypeError as e:
                logger.warning(
                    f"Wrong arguments for function {name}: {args}. Error: {e}"
                )
                res = f"Wrong arguments, please fix them and try again."
                res_log = res
            except Exception as e:
                logger.warning(
                    f"Error executing function {self.function_name} with args {args}: {e}"
                )
                res = f"Error: {e}."
                res_log = res
            span.set_attribute("result", res_log)
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
    def _validate_created_at(cls, created_at: datetime) -> datetime:
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
        content = " ".join([line.strip() for line in self.content.splitlines()])

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
    # TODO: Use JSON as LLM response instead of using a regex to parse the text
    res = re.match(_MESSAGE_ACTION_R, text)
    if not res:
        return text
    try:
        return res.group(2) or ""
    except ValueError:  # Regex failed, return original text
        return text


def extract_message_style(text: str) -> tuple[Optional[StyleEnum], str]:
    """
    Detect the style of a message.
    """
    # TODO: Use JSON as LLM response instead of using a regex to parse the text
    res = re.match(_MESSAGE_STYLE_R, text)
    if not res:
        return None, text
    try:
        return (
            StyleEnum(res.group(1)),  # style
            (res.group(2) or ""),  # content
        )
    except ValueError:  # Regex failed, return original text
        return None, text
