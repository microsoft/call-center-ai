"""
Inspired by: Microsoft AutoGen (CC-BY-4.0)
See: https://github.com/microsoft/autogen/blob/2750391f847b7168d842dfcb815ac37bd94c9a0e/autogen/function_utils.py
"""

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from functools import wraps
from inspect import getmembers, isfunction
from textwrap import dedent
from types import FunctionType
from typing import Annotated, Any, ForwardRef, TypeVar

from aiojobs import Scheduler
from azure.ai.inference.models import ChatCompletionsToolDefinition, FunctionDefinition
from azure.cognitiveservices.speech import (
    SpeechSynthesizer,
)
from azure.communication.callautomation.aio import CallAutomationClient
from jinja2 import Environment
from json_repair import repair_json
from pydantic import BaseModel, TypeAdapter
from pydantic._internal._typing_extra import eval_type_lenient
from pydantic.json_schema import JsonSchemaValue

from app.helpers.cache import lru_acache, lru_cache
from app.helpers.logging import logger
from app.helpers.monitoring import SpanAttributeEnum, start_as_current_span
from app.models.call import CallStateModel
from app.models.message import ToolModel

T = TypeVar("T")
_jinja = Environment(
    autoescape=True,
    enable_async=True,
)


class Parameters(BaseModel):
    """
    Parameters of a function as defined by the OpenAI API.
    """

    properties: dict[str, JsonSchemaValue]
    required: list[str]
    type: str = "object"


class AbstractPlugin:
    call: CallStateModel
    client: CallAutomationClient
    post_callback: Callable[[CallStateModel], Awaitable[None]]
    scheduler: Scheduler
    tts_callback: Callable[[str], Awaitable[None]]
    tts_client: SpeechSynthesizer

    def __init__(  # noqa: PLR0913
        self,
        call: CallStateModel,
        client: CallAutomationClient,
        post_callback: Callable[[CallStateModel], Awaitable[None]],
        scheduler: Scheduler,
        tts_callback: Callable[[str], Awaitable[None]],
        tts_client: SpeechSynthesizer,
    ):
        self.call = call
        self.client = client
        self.post_callback = post_callback
        self.scheduler = scheduler
        self.tts_callback = tts_callback
        self.tts_client = tts_client

    @lru_acache()
    async def to_openai(
        self,
        blacklist: frozenset[str],
    ) -> list[ChatCompletionsToolDefinition]:
        """
        Get the OpenAI SDK schema for all functions of the plugin, excluding the ones in the blacklist.
        """
        functions = self._available_functions(frozenset(blacklist))
        return await asyncio.gather(
            *[_function_schema(func, call=self.call) for func in functions]
        )

    @start_as_current_span("plugin_execute")
    async def execute(
        self,
        tool: ToolModel,
        blacklist: set[str],
    ) -> None:
        functions = [
            func.__name__ for func in self._available_functions(frozenset(blacklist))
        ]
        json_str = tool.function_arguments
        name = tool.function_name

        # Confirm the function name exists, this is a security measure to prevent arbitrary code execution, plus, Pydantic validator is not used on purpose to comply with older tools plugins
        if name not in functions:
            res = f"Invalid function names {name}, available are {functions}."
            logger.warning(res)
            # Update tool
            tool.content = res
            # Enrich span
            SpanAttributeEnum.TOOL_RESULT.attribute(tool.content)
            return

        # Try to fix JSON args to catch LLM hallucinations
        # See: https://community.openai.com/t/gpt-4-1106-preview-messes-up-function-call-parameters-encoding/478500
        args: dict[str, Any] | Any = repair_json(
            json_str=json_str,
            return_objects=True,
        )  # pyright: ignore

        # Confirm the args are a dictionary
        if not isinstance(args, dict):
            logger.warning(
                "Error decoding JSON args for function %s: %s...%s",
                name,
                json_str[:20],
                json_str[-20:],
            )
            # Update tool
            tool.content = (
                f"Bad arguments, available are {functions}. Please try again."
            )
            # Enrich span
            SpanAttributeEnum.TOOL_RESULT.attribute(tool.content)
            return

        # Enrich span
        SpanAttributeEnum.TOOL_ARGS.attribute(json.dumps(args))
        SpanAttributeEnum.TOOL_NAME.attribute(name)

        # Execute the function
        try:
            res = await getattr(self, name)(**args)
            res_log = f"{res[:20]}...{res[-20:]}"
            logger.info("Executed function %s (%s): %s", name, args, res_log)

        # Catch wrong arguments
        except TypeError:
            logger.exception("Wrong arguments for function %s: %s.", name, args)
            res = "Wrong arguments, please fix them and try again."
            res_log = res

        # Catch execution errors
        except Exception as e:
            logger.exception(
                "Error executing function %s with args %s",
                tool.function_name,
                args,
            )
            res = f"Error: {e}."
            res_log = res

        # Update tool
        tool.content = res
        # Enrich span
        SpanAttributeEnum.TOOL_RESULT.attribute(tool.content)

    @lru_cache()
    def _available_functions(
        self,
        blacklist: frozenset[str],
    ) -> list[FunctionType]:
        """
        List all available functions of the plugin, including the inherited ones.
        """
        return [
            func
            for name, func in getmembers(self.__class__, isfunction)
            if not name.startswith("_")
            and name not in [func.__name__ for func in [self.to_openai, self.execute]]
            and name not in blacklist
        ]


def add_customer_response(
    response_examples: list[str],
    before: bool = True,
):
    """
    Decorator to add a customer response to a tool.

    Examples are used to generate the tool prompt.

    Example:

    ```python
    @add_customer_response(
        response_examples=[
            "I updated the contact information.",
            "I changed the address.",
        ],
    )
    async def update_contact_information(...) -> str:
        # ...
        return "Contact information updated."
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(
            self: AbstractPlugin,
            *args,
            customer_response: str,
            **kwargs,
        ):
            # If before, execute all in parallel
            if before:
                _, res = await asyncio.gather(
                    self.tts_callback(customer_response),
                    func(self, *args, **kwargs),
                )

            # If after, call context should change, so execute sequentially
            else:
                res = await func(self, *args, **kwargs)
                await self.tts_callback(customer_response)

            return res

        # Update the signature of the function
        func.__signature__ = inspect.signature(func).replace(
            parameters=[
                *inspect.signature(func).parameters.values(),
                inspect.Parameter(
                    kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    name="customer_response",
                    annotation=Annotated[
                        str,
                        f"""
                        Phrase used to confirm the update, in the same language as the customer. This phrase will be spoken to the user.

                        # Rules
                        - Action should be rephrased in the present tense
                        - Must be in a single short sentence
                        - Use simple language

                        # Examples
                        {"\n- ".join(response_examples)}
                        """,
                    ],
                ),
            ]
        )

        return wrapper

    return decorator


async def _function_schema(
    f: Callable[..., Any],
    **kwargs: Any,
) -> ChatCompletionsToolDefinition:
    """
    Take a function and return a JSON schema for it as defined by the OpenAI API.

    Kwargs are passed to the Jinja template for rendering the function description and parameter descriptions.

    Raise TypeError if the function is not annotated.
    """
    typed_signature = _typed_signature(f)
    default_values = _default_values(typed_signature)
    param_annotations = _param_annotations(typed_signature)
    required_params = _required_params(typed_signature)
    missing, unannotated_with_default = _missing_annotations(
        typed_signature, required_params
    )

    if unannotated_with_default != set():
        unannotated_with_default_s = [
            f"'{k}'" for k in sorted(unannotated_with_default)
        ]
        logger.warning(
            "The following parameters of the function '%s' with default values are not annotated: %s.",
            f.__name__,
            ", ".join(unannotated_with_default_s),
        )

    if missing != set():
        missing_s = [f"'{k}'" for k in sorted(missing)]
        raise TypeError(
            f"All parameters of the function '{f.__name__}' without default values must be annotated. "
            + f"The annotations are missing for the following parameters: {', '.join(missing_s)}"
        )

    description = _remove_newlines(
        await _jinja.from_string(dedent(f.__doc__ or "")).render_async(**kwargs)
    )  # Remove possible indentation, render the description, then remove newlines to avoid hallucinations
    name = f.__name__
    parameters: dict[str, object] = (
        await _parameters(
            default_values=default_values,
            param_annotations=param_annotations,
            required_params=required_params,
            **kwargs,
        )
    ).model_dump()

    return ChatCompletionsToolDefinition(
        function=FunctionDefinition(
            description=description,
            name=name,
            parameters=parameters,
        ),
    )


def _typed_annotation(annotation: Any, global_namespace: dict[str, Any]) -> Any:
    """
    Get the type annotation of a parameter and return the anotated type.
    """
    if isinstance(annotation, str):
        annotation = ForwardRef(annotation)
        annotation = eval_type_lenient(annotation, global_namespace, global_namespace)
    return annotation


def _typed_signature(func: Callable[..., Any]) -> inspect.Signature:
    """
    Get the signature of a function with type annotations and return the annotated signature.
    """
    signature = inspect.signature(func)
    globalns = getattr(func, "__globals__", {})
    typed_params = [
        inspect.Parameter(
            name=param.name,
            kind=param.kind,
            default=param.default,
            annotation=_typed_annotation(param.annotation, globalns),
        )
        for param in signature.parameters.values()
    ]
    typed_signature = inspect.Signature(typed_params)
    return typed_signature


def _param_annotations(
    typed_signature: inspect.Signature,
) -> dict[str, Annotated[type[Any], str] | type[Any]]:
    """
    Get the type annotations of the parameters of a function and return a dictionary of the annotated parameters.
    """
    return {
        name: value.annotation
        for name, value in typed_signature.parameters.items()
        if value.annotation != inspect.Signature.empty and name != "self"
    }


async def _parameter_json_schema(
    name: str,
    value: Annotated[type[Any], str] | type[Any],
    default_values: dict[str, Any],
    **kwargs: Any,
) -> JsonSchemaValue:
    """
    Get a JSON schema for a parameter as defined by the OpenAI API and return the Pydantic model for the parameter.

    Kwargs are passed to the Jinja template for rendering the parameter description.
    """

    def _description(name: str, value: Annotated[type[Any], str] | type[Any]) -> str:
        # Handles Annotated
        if hasattr(value, "__metadata__"):
            retval = value.__metadata__[0]
            if isinstance(retval, str):
                return retval
            raise ValueError(
                f"Invalid description {retval} for parameter {name}, should be a string."
            )
        return name

    schema = TypeAdapter(value).json_schema()
    if name in default_values:
        dv = default_values[name]
        schema["default"] = dv

    schema["description"] = _remove_newlines(
        await _jinja.from_string(dedent(_description(name, value))).render_async(
            **kwargs
        )
    )  # Remove possible indentation, render the description, then remove newlines to avoid hallucinations

    return schema


def _required_params(typed_signature: inspect.Signature) -> set[str]:
    """
    Get the required parameters of a function and return them as a set.
    """
    return {
        name
        for name, value in typed_signature.parameters.items()
        if value.default == inspect.Signature.empty and name != "self"
    }


def _default_values(typed_signature: inspect.Signature) -> dict[str, Any]:
    """
    Get default values of parameters of a function and return them as a dictionary.
    """
    return {
        name: value.default
        for name, value in typed_signature.parameters.items()
        if value.default != inspect.Signature.empty and name != "self"
    }


async def _parameters(
    required_params: set[str],
    param_annotations: dict[str, Annotated[type[Any], str] | type[Any]],
    default_values: dict[str, Any],
    **kwargs: Any,
) -> Parameters:
    """
    Get the parameters of a function as defined by the OpenAI API and return the Pydantic model for the parameters.

    Kwargs are passed to the Jinja template for rendering the parameter description.
    """
    return Parameters(
        properties={
            name: await _parameter_json_schema(
                default_values=default_values,
                name=name,
                value=value,
                **kwargs,
            )
            for name, value in param_annotations.items()
            if value != inspect.Signature.empty and name != "self"
        },
        required=list(required_params),
    )


def _missing_annotations(
    typed_signature: inspect.Signature, required_params: set[str]
) -> tuple[set[str], set[str]]:
    """
    Get the missing annotations of a function and return them as a set.

    Returns a tuple:
    1. Missing annotations
    2. Unannotated parameters with default values
    """
    all_missing = {
        k
        for k, v in typed_signature.parameters.items()
        if v.annotation == inspect.Signature.empty and k != "self"
    }
    missing = all_missing.intersection(required_params)
    unannotated_with_default = all_missing.difference(missing)
    return missing, unannotated_with_default


def _remove_newlines(text: str) -> str:
    """
    Remove newlines from a string and return it as a single line.
    """
    return " ".join([line.strip() for line in text.splitlines()])
