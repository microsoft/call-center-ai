from azure.ai.contentsafety.aio import ContentSafetyClient
from azure.ai.contentsafety.models import (
    AnalyzeTextOptions,
    AnalyzeTextResult,
    TextCategoriesAnalysis,
    TextCategory,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from helpers.config import CONFIG
from contextlib import asynccontextmanager
from helpers.logging import build_logger
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry_if_exception_type,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)
from typing import (
    AsyncGenerator,
    AsyncIterable,
    List,
    Optional,
    Type,
    TypeVar,
)
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
    AzureChatPromptExecutionSettings,
)
from semantic_kernel.connectors.ai.open_ai.contents.azure_streaming_chat_message_content import (
    AzureStreamingChatMessageContent,
)
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.core_plugins.math_plugin import MathPlugin
from semantic_kernel.core_plugins.time_plugin import TimePlugin
from semantic_kernel.core_plugins.conversation_summary_plugin import (
    ConversationSummaryPlugin,
)
from semantic_kernel.orchestration.kernel_function import KernelFunction
from models.message import MessageModel
from pydantic import BaseModel, TypeAdapter
from semantic_kernel.orchestration.context_variables import ContextVariables
from semantic_kernel.connectors.ai.open_ai.utils import (
    get_tool_call_object,
    execute_tool_call,
)
import asyncio
from semantic_kernel.connectors.ai.open_ai.semantic_functions.open_ai_chat_prompt_template import (
    OpenAIChatPromptTemplate,
)


_logger = build_logger(__name__)
_logger.info(f"Using OpenAI GPT model {CONFIG.openai.gpt_model}")
_logger.info(f"Using Content Safety {CONFIG.content_safety.endpoint}")

_oai_config = {
    # Azure deployment
    "ai_model_id": CONFIG.openai.gpt_model,
    "api_version": "2023-12-01-preview",
    "deployment_name": CONFIG.openai.gpt_deployment,
    "endpoint": CONFIG.openai.endpoint,
    # Authentication, either RBAC or API key
    "api_key": (
        CONFIG.openai.api_key.get_secret_value() if CONFIG.openai.api_key else None
    ),
    "ad_token_provider": (
        get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        if not CONFIG.openai.api_key
        else None
    ),
}
_chat = AzureChatCompletion(**_oai_config)

ModelType = TypeVar("ModelType", bound=BaseModel)


class SafetyCheckError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


async def chat(
    function: KernelFunction,
    kernel: Kernel,
    messages: List[MessageModel],
) -> AsyncGenerator[AzureStreamingChatMessageContent, None]:
    """
    Returns a stream of completion results.

    Catch errors for a maximum of 3 times (internal + `RateLimitError`), then raise the error.
    """
    assert isinstance(function.chat_prompt_template, OpenAIChatPromptTemplate)
    assert isinstance(
        function.prompt_execution_settings, AzureChatPromptExecutionSettings
    )

    # Prepare tools
    filter = {"exclude_plugin": ["chat"]}
    tools = get_tool_call_object(kernel, filter)

    # Settings
    function.prompt_execution_settings.tool_choice = "auto"
    function.prompt_execution_settings.tools = tools

    # Variables
    variables = ContextVariables(
        variables={
            "input": messages[-1].to_openai()[0]["content"],  # type: ignore
        }
    )

    # Populate history
    for message_model in messages:
        for message_openai in message_model.to_openai():
            kwargs = dict(message_openai)
            del kwargs["role"]
            del kwargs["content"]
            function.chat_prompt_template.add_message(  # type: ignore
                message=(
                    message_openai["content"]
                    if "content" in message_openai
                    and isinstance(message_openai["content"], str)
                    else None
                ),
                role=message_openai["role"],
                **kwargs,
            )

    # Invoke
    stream: AsyncIterable[List[AzureStreamingChatMessageContent]] = (
        function.invoke_stream(variables=variables)
    )

    # Yield res and execute tool calls
    buffer = None
    buffer_tool_calls_pointer = 0
    async for chuncks in stream:
        for chunck in chuncks:
            # Buffer stream
            if not buffer:
                buffer = chunck
            else:
                buffer += chunck
            assert isinstance(buffer, AzureStreamingChatMessageContent)

            # Tool tasks
            if buffer.tool_calls:
                local_tools = buffer.tool_calls[buffer_tool_calls_pointer:]
                buffer_tool_calls_pointer += len(local_tools)
                for local_tool in local_tools:
                    if not local_tool.function or not local_tool.id:
                        continue
                    tool_res = await execute_tool_call(
                        kernel=kernel,
                        tool_call=local_tool,
                    )
                    function.chat_prompt_template.add_tool_call_response_message(
                        content=tool_res,
                        tool_call_id=local_tool.id,
                    )

            yield buffer


@retry(
    reraise=True,
    retry=retry_if_exception_type(SafetyCheckError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def completion_text(
    function: KernelFunction,
    messages: List[MessageModel],
    json_output: bool = False,
) -> Optional[str]:
    """
    Returns a completion result.

    Catch errors for a maximum of 3 times (internal + `RateLimitError` + `SafetyCheckError`), then raise the error. Safety check is only performed for text responses (= not JSON).
    """
    assert isinstance(
        function.prompt_execution_settings, AzureChatPromptExecutionSettings
    )

    # Settings
    function.prompt_execution_settings.response_format = (
        "json_object" if json_output else "text"
    )

    # Variables
    variables = ContextVariables(
        variables={
            "history": TypeAdapter(List[MessageModel]).dump_json(messages).decode(),
        }
    )

    # Invoke
    res = await function.invoke(variables=variables)
    content = res.result

    # Safety check
    if not content:
        return None
    if not json_output:
        await safety_check(content)

    return content


@retry(
    reraise=True,
    retry=retry_if_exception_type(ValidationError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def completion_model(
    function: KernelFunction,
    messages: List[MessageModel],
    model: Type[ModelType],
) -> Optional[ModelType]:
    """
    Returns an object validated against a given model, from a completion result.

    Catch errors for a maximum of 3 times, but not `SafetyCheckError`.
    """
    res = await completion_text(
        function=function,
        json_output=True,
        messages=messages,
    )
    if not res:
        return None
    return model.model_validate_json(res)


async def safety_check(text: str) -> None:
    """
    Raise `SafetyCheckError` if the text is safe, nothing otherwise.

    Text can be returned both safe and censored, before containing unsafe content.
    """
    if not text:
        return
    try:
        res = await _contentsafety_analysis(text)
    except HttpResponseError as e:
        _logger.error(f"Failed to run safety check: {e}")
        return  # Assume safe

    if not res:
        _logger.error("Failed to run safety check: No result")
        return  # Assume safe

    for match in res.blocklists_match or []:
        _logger.debug(f"Matched blocklist item: {match.blocklist_item_text}")
        text = text.replace(
            match.blocklist_item_text, "*" * len(match.blocklist_item_text)
        )

    hate_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.HATE,
        CONFIG.content_safety.category_hate_score,
    )
    self_harm_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.SELF_HARM,
        CONFIG.content_safety.category_self_harm_score,
    )
    sexual_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.SEXUAL,
        CONFIG.content_safety.category_sexual_score,
    )
    violence_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.VIOLENCE,
        CONFIG.content_safety.category_violence_score,
    )

    safety = hate_result and self_harm_result and sexual_result and violence_result
    _logger.debug(f'Text safety "{safety}" for text: {text}')

    if not safety:
        raise SafetyCheckError(
            f"Unsafe content detected, hate={hate_result}, self_harm={self_harm_result}, sexual={sexual_result}, violence={violence_result}"
        )


@retry(
    reraise=True,
    retry=retry_if_exception_type(HttpResponseError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def _contentsafety_analysis(text: str) -> AnalyzeTextResult:
    async with _use_contentsafety() as client:
        return await client.analyze_text(
            AnalyzeTextOptions(
                blocklist_names=CONFIG.content_safety.blocklists,
                halt_on_blocklist_hit=False,
                output_type="EightSeverityLevels",
                text=text,
            )
        )


def _contentsafety_category_test(
    res: List[TextCategoriesAnalysis],
    category: TextCategory,
    score: int,
) -> bool:
    """
    Returns `True` if the category is safe or the severity is low, `False` otherwise, meaning the category is unsafe.
    """
    if score == 0:
        return True  # No need to check severity

    detection = next(item for item in res if item.category == category)

    if detection and detection.severity and detection.severity > score:
        _logger.debug(f"Matched {category} with severity {detection.severity}")
        return False
    return True


@asynccontextmanager
async def _use_contentsafety() -> AsyncGenerator[ContentSafetyClient, None]:
    client = ContentSafetyClient(
        # Azure deployment
        endpoint=CONFIG.content_safety.endpoint,
        # Authentication with API key
        credential=AzureKeyCredential(
            CONFIG.content_safety.access_key.get_secret_value()
        ),
    )
    yield client
    await client.close()


def build_kernel() -> Kernel:
    kernel = Kernel()

    # Link LLM
    kernel.add_chat_service("openai", _chat)

    # Import core plugins
    kernel.import_plugin(ConversationSummaryPlugin(kernel), "ConversationSummary")
    kernel.import_plugin(MathPlugin(), "Math")
    kernel.import_plugin(TimePlugin(), "Time")

    return kernel
