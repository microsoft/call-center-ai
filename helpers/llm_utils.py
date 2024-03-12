"""
Inspired by: Microsoft AutoGen (CC-BY-4.0)
See: https://github.com/microsoft/autogen/blob/2750391f847b7168d842dfcb815ac37bd94c9a0e/autogen/function_utils.py
"""

import inspect
from helpers.logging import build_logger
from typing import (
    Any,
    Callable,
    Dict,
    ForwardRef,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)
from openai.types.chat import ChatCompletionToolParam
from openai.types.shared_params.function_definition import FunctionDefinition
from pydantic import BaseModel, TypeAdapter
from pydantic._internal._typing_extra import eval_type_lenient
from pydantic.json_schema import JsonSchemaValue
from typing_extensions import Annotated


_logger = build_logger(__name__)
T = TypeVar("T")


class Parameters(BaseModel):
    """
    Parameters of a function as defined by the OpenAI API.
    """

    properties: Dict[str, JsonSchemaValue]
    required: list[str]
    type: str = "object"


def function_schema(f: Callable[..., Any]) -> ChatCompletionToolParam:
    """
    Get a JSON schema for a function as defined by the OpenAI API.

    Args:
        f: The function to get the JSON schema for

    Returns:
        A JSON schema for the function

    Raises:
        TypeError: If the function is not annotated
    """
    typed_signature = _typed_signature(f)
    default_values = _default_values(typed_signature)
    param_annotations = _param_annotations(typed_signature)
    required = _required_params(typed_signature)
    missing, unannotated_with_default = _missing_annotations(typed_signature, required)

    if unannotated_with_default != set():
        unannotated_with_default_s = [
            f"'{k}'" for k in sorted(unannotated_with_default)
        ]
        _logger.warning(
            f"The following parameters of the function '{f.__name__}' with default values are not annotated: "
            + f"{', '.join(unannotated_with_default_s)}."
        )

    if missing != set():
        missing_s = [f"'{k}'" for k in sorted(missing)]
        raise TypeError(
            f"All parameters of the function '{f.__name__}' without default values must be annotated. "
            + f"The annotations are missing for the following parameters: {', '.join(missing_s)}"
        )

    # Removing newlines from the content to avoid hallucinations issues with GPT-4 Turbo
    description = " ".join((f.__doc__ or "").splitlines()).strip()
    name = " ".join((f.__name__).splitlines()).strip()
    parameters: dict[str, object] = _parameters(
        required, param_annotations, default_values=default_values
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


def _typed_annotation(annotation: Any, globalns: Dict[str, Any]) -> Any:
    """
    Get the type annotation of a parameter.

    Args:
        annotation: The annotation of the parameter
        globalns: The global namespace of the function

    Returns:
        The type annotation of the parameter
    """
    if isinstance(annotation, str):
        annotation = ForwardRef(annotation)
        annotation = eval_type_lenient(annotation, globalns, globalns)
    return annotation


def _typed_signature(call: Callable[..., Any]) -> inspect.Signature:
    """Get the signature of a function with type annotations.

    Args:
        call: The function to get the signature for

    Returns:
        The signature of the function with type annotations
    """
    signature = inspect.signature(call)
    globalns = getattr(call, "__globals__", {})
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
) -> Dict[str, Union[Annotated[Type[Any], str], Type[Any]]]:
    """
    Get the type annotations of the parameters of a function.

    Args:
        typed_signature: The signature of the function with type annotations

    Returns:
        A dictionary of the type annotations of the parameters of the function
    """
    return {
        k: v.annotation
        for k, v in typed_signature.parameters.items()
        if v.annotation != inspect.Signature.empty and k != "self"
    }


def _parameter_json_schema(
    k: str,
    v: Union[Annotated[Type[Any], str], Type[Any]],
    default_values: Dict[str, Any],
) -> JsonSchemaValue:
    """
    Get a JSON schema for a parameter as defined by the OpenAI API.

    Args:
        k: The name of the parameter
        v: The type of the parameter
        default_values: The default values of the parameters of the function

    Returns:
        A Pydanitc model for the parameter
    """

    def _description(k: str, v: Union[Annotated[Type[Any], str], Type[Any]]) -> str:
        # Handles Annotated
        if hasattr(v, "__metadata__"):
            retval = v.__metadata__[0]
            if isinstance(retval, str):
                return retval
            else:
                raise ValueError(
                    f"Invalid description {retval} for parameter {k}, should be a string."
                )
        else:
            return k

    schema = TypeAdapter(v).json_schema()
    if k in default_values:
        dv = default_values[k]
        schema["default"] = dv

    schema["description"] = _description(k, v)

    return schema


def _required_params(typed_signature: inspect.Signature) -> list[str]:
    """
    Get the required parameters of a function.

    Args:
        signature: The signature of the function as returned by inspect.signature

    Returns:
        A list of the required parameters of the function
    """
    return [
        k
        for k, v in typed_signature.parameters.items()
        if v.default == inspect.Signature.empty and k != "self"
    ]


def _default_values(typed_signature: inspect.Signature) -> Dict[str, Any]:
    """
    Get default values of parameters of a function.

    Args:
        signature: The signature of the function as returned by inspect.signature

    Returns:
        A dictionary of the default values of the parameters of the function
    """
    return {
        k: v.default
        for k, v in typed_signature.parameters.items()
        if v.default != inspect.Signature.empty and k != "self"
    }


def _parameters(
    required: list[str],
    param_annotations: Dict[str, Union[Annotated[Type[Any], str], Type[Any]]],
    default_values: Dict[str, Any],
) -> Parameters:
    """
    Get the parameters of a function as defined by the OpenAI API.

    Args:
        required: The required parameters of the function
        hints: The type hints of the function as returned by typing.get_type_hints

    Returns:
        A Pydantic model for the parameters of the function
    """
    return Parameters(
        properties={
            k: _parameter_json_schema(k, v, default_values)
            for k, v in param_annotations.items()
            if v != inspect.Signature.empty and k != "self"
        },
        required=required,
    )


def _missing_annotations(
    typed_signature: inspect.Signature, required: list[str]
) -> Tuple[Set[str], Set[str]]:
    """
    Get the missing annotations of a function.

    Ignores the parameters with default values as they are not required to be annotated, but logs a warning.

    Args:
        typed_signature: The signature of the function with type annotations
        required: The required parameters of the function

    Returns:
        A set of the missing annotations of the function
    """
    all_missing = {
        k
        for k, v in typed_signature.parameters.items()
        if v.annotation == inspect.Signature.empty and k != "self"
    }
    missing = all_missing.intersection(set(required))
    unannotated_with_default = all_missing.difference(missing)
    return missing, unannotated_with_default
