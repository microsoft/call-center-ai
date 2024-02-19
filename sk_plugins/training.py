from typing import List
from models.call import CallModel
from semantic_kernel.plugin_definition import (
    kernel_function,
    kernel_function_context_parameter,
)
from persistence.isearch import ISearch
from semantic_kernel.orchestration.kernel_context import KernelContext
from pydantic import TypeAdapter
from models.training import TrainingModel

class TrainingPlugin:
    _call: CallModel
    _search: ISearch

    def __init__(self, call: CallModel, search: ISearch):
        self._call = call
        self._search = search

    @kernel_function(
        description="Use this if you want to search for a document in the training database",
        name="search",
    )
    @kernel_function_context_parameter(
        description="Query to search for. Example: 'A document about the new car insurance policy', 'A document about the new car insurance policy'.",
        name="query",
    )
    async def search(self, context: KernelContext) -> str:
        query = context.variables.get("query")
        assert query
        trainings = await self._search.training_asearch_all(query, self._call)
        return TypeAdapter(List[TrainingModel]).dump_json(trainings or []).decode()
