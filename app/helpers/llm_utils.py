"""
Inspired by: Microsoft AutoGen (CC-BY-4.0)
See: https://github.com/microsoft/autogen/blob/2750391f847b7168d842dfcb815ac37bd94c9a0e/autogen/function_utils.py
"""

import inspect
from helpers.logging import logger
from typing import (
    Any,
    Callable,
    ForwardRef,
    Tuple,
    TypeVar,
    Union,
)
from jinja2 import Environment
from openai.types.chat import ChatCompletionToolParam
from openai.types.shared_params.function_definition import FunctionDefinition
from pydantic import BaseModel, TypeAdapter
from pydantic._internal._typing_extra import eval_type_lenient
from pydantic.json_schema import JsonSchemaValue
from textwrap import dedent
from typing_extensions import Annotated


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


async def function_schema(
    f: Callable[..., Any], **kwargs: Any
) -> ChatCompletionToolParam:
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
            f"The following parameters of the function '{f.__name__}' with default values are not annotated: "
            + f"{', '.join(unannotated_with_default_s)}."
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

    function = ChatCompletionToolParam(
        type="function",
        function=FunctionDefinition(
            description=description,
            name=name,
            parameters=parameters,
        ),
    )

    return function


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
) -> dict[str, Union[Annotated[type[Any], str], type[Any]]]:
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
    value: Union[Annotated[type[Any], str], type[Any]],
    default_values: dict[str, Any],
    **kwargs: Any,
) -> JsonSchemaValue:
    """
    Get a JSON schema for a parameter as defined by the OpenAI API and return the Pydantic model for the parameter.

    Kwargs are passed to the Jinja template for rendering the parameter description.
    """

    def _description(
        name: str, value: Union[Annotated[type[Any], str], type[Any]]
    ) -> str:
        # Handles Annotated
        if hasattr(value, "__metadata__"):
            retval = value.__metadata__[0]
            if isinstance(retval, str):
                return retval
            else:
                raise ValueError(
                    f"Invalid description {retval} for parameter {name}, should be a string."
                )
        else:
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
    param_annotations: dict[str, Union[Annotated[type[Any], str], type[Any]]],
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
