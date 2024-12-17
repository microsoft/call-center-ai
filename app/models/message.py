import re
from datetime import UTC, datetime
from enum import Enum

from azure.ai.inference.models import (
    AssistantMessage,
    ChatCompletionsToolCall,
    ChatRequestMessage,
    FunctionCall,
    StreamingChatResponseToolCallUpdate,
    ToolMessage,
    UserMessage,
)
from pydantic import BaseModel, Field, field_validator

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
    NONE = "none"
    """This is not a valid style, but we use it in the code to indicate no style."""
    SAD = "sad"


class ActionEnum(str, Enum):
    CALL = "call"
    """User called the assistant."""
    HANGUP = "hangup"
    """User hung up the call."""
    SMS = "sms"
    """User sent an SMS."""
    TALK = "talk"
    """User sent a message."""


class PersonaEnum(str, Enum):
    ASSISTANT = "assistant"
    """Represents an AI assistant."""
    HUMAN = "human"
    """Represents a human user."""
    TOOL = "tool"
    """Not used but deprecated, kept for backward compatibility."""


class ToolModel(BaseModel):
    content: str = ""
    function_arguments: str = ""
    function_name: str = ""
    tool_id: str = ""

    def __add__(self, other: object) -> "ToolModel":
        if not isinstance(other, StreamingChatResponseToolCallUpdate):
            return NotImplemented
        if other.id:
            self.tool_id = other.id
        if other.function:
            if other.function.name:
                self.function_name = other.function.name
            if other.function.arguments:
                self.function_arguments += other.function.arguments
        return self

    def __hash__(self) -> int:
        return self.tool_id.__hash__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolModel):
            return False
        return self.tool_id == other.tool_id

    def to_openai(self) -> ChatCompletionsToolCall:
        return ChatCompletionsToolCall(
            id=self.tool_id,
            function=FunctionCall(
                arguments=self.function_arguments,
                name="-".join(
                    re.sub(
                        _FUNC_NAME_SANITIZER_R,
                        "-",
                        self.function_name,
                    ).split("-")
                ),  # Sanitize with dashes then deduplicate dashes, backward compatibility with old models
            ),
        )


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
    @classmethod
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
    ) -> list[ChatRequestMessage]:
        # Removing newlines from the content to avoid hallucinations issues with GPT-4 Turbo
        content = " ".join([line.strip() for line in self.content.splitlines()])

        if self.persona == PersonaEnum.HUMAN:
            return [
                UserMessage(
                    content=f"action={self.action.value} {content}",
                )
            ]

        if self.persona == PersonaEnum.ASSISTANT:
            if not self.tool_calls:
                return [
                    AssistantMessage(
                        content=f"action={self.action.value} style={self.style.value} {content}",
                    )
                ]

        res = []
        res.append(
            AssistantMessage(
                content=f"action={self.action.value} style={self.style.value} {content}",
                tool_calls=[tool_call.to_openai() for tool_call in self.tool_calls],
            )
        )
        res.extend(
            ToolMessage(
                content=tool_call.content,
                tool_call_id=tool_call.tool_id,
            )
            for tool_call in self.tool_calls
            if tool_call.content
        )
        return res


def _filter_action(text: str) -> str:
    """
    Remove action from content.

    AI often adds it by mistake event if explicitly asked not to.

    Example:
    - Input: "action=talk Hello!"
    - Output: "Hello!"
    """
    # TODO: Use JSON as LLM response instead of using a regex to parse the text
    res = re.match(_MESSAGE_ACTION_R, text)
    if not res:
        return text
    try:
        return res.group(2) or ""
    # Regex failed, return original text
    except ValueError:
        return text


def _filter_content(text: str) -> str:
    """
    Remove content from text.

    AI often adds it by mistake event if explicitly asked not to.

    Example:
    - Input: "content=Hello!"
    - Output: "Hello!"
    """
    return text.replace("content=", "")


def extract_message_style(text: str) -> tuple[StyleEnum, str]:
    """
    Detect the style of a message and extract it from the text.

    Example:
    - Input: "style=cheerful Hello!"
    - Output: (StyleEnum.CHEERFUL, "Hello!")
    """
    # Apply hallucination filters
    text = _filter_action(text)
    text = _filter_content(text)

    # Extract style
    default_style = StyleEnum.NONE
    res = re.match(_MESSAGE_STYLE_R, text)
    if not res:
        return default_style, text
    try:
        return (
            StyleEnum(res.group(1)),  # style
            (res.group(2) or ""),  # content
        )

    # Regex failed, return original text
    except ValueError:
        return default_style, text
