from azure.communication.callautomation import CallConnectionClient
from fastapi import BackgroundTasks
from helpers.call import ContextEnum as CallContextEnum, handle_play
from helpers.config import CONFIG
from helpers.llm_tools import function_schema
from inspect import getmembers, isfunction
from models.call import CallModel
from models.claim import ClaimModel
from models.message import StyleEnum as MessageStyleEnum
from models.reminder import ReminderModel
from models.training import TrainingModel
from openai.types.chat import ChatCompletionToolParam
from persistence.ai_search import AiSearchSearch
from pydantic import ValidationError, TypeAdapter
from typing import Awaitable, Callable, Annotated, List
import asyncio


class LlmPlugins:
    background_tasks: BackgroundTasks
    call: CallModel
    cancellation_callback: Callable[[], Awaitable]
    client: CallConnectionClient
    post_call_next: Callable[[CallModel], Awaitable]
    post_call_synthesis: Callable[[CallModel], Awaitable]
    search: AiSearchSearch
    style: MessageStyleEnum
    user_callback: Callable[[str, MessageStyleEnum], Awaitable]

    def __init__(
        self,
        background_tasks: BackgroundTasks,
        call: CallModel,
        cancellation_callback: Callable[[], Awaitable],
        client: CallConnectionClient,
        post_call_next: Callable[[CallModel], Awaitable],
        post_call_synthesis: Callable[[CallModel], Awaitable],
        search: AiSearchSearch,
        style: MessageStyleEnum,
        user_callback: Callable[[str, MessageStyleEnum], Awaitable],
    ):
        self.background_tasks = background_tasks
        self.call = call
        self.cancellation_callback = cancellation_callback
        self.client = client
        self.post_call_next = post_call_next
        self.post_call_synthesis = post_call_synthesis
        self.search = search
        self.style = style
        self.user_callback = user_callback

    async def end_call(self) -> str:
        """
        Use this if the user wants to end the call in its last message. Use this action only at the end of a conversation. Be warnging that the call will be ended immediately. Example: 'I want to hang up', 'Good bye, see you soon'.
        """
        await self.cancellation_callback()
        await handle_play(
            call=self.call,
            client=self.client,
            context=CallContextEnum.GOODBYE,
            text=await CONFIG.prompts.tts.goodbye(self.call),
        )
        return "Call ended"

    async def new_claim(
        self,
        customer_response: Annotated[
            str,
            "The text to be read to the customer to confirm the update. Only speak about this action. Use an imperative sentence. Example: 'I am updating the involved parties to Marie-Jeanne and Jean-Pierre', 'I am updating the contact contact info to 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
        ],
    ) -> str:
        """
        Use this if the user wants to create a new claim for a totally different subject. This will reset the claim and reminder data. Old is stored but not accessible anymore. Approval from the customer must be explicitely given. Example: 'I want to create a new claim'.
        """
        await self.user_callback(customer_response, self.style)

        self.background_tasks.add_task(self.post_call_next, self.call)
        self.background_tasks.add_task(self.post_call_synthesis, self.call)

        last_message = self.call.messages[-1]
        call = CallModel(phone_number=self.call.phone_number)
        call.messages.append(last_message)

        return "Claim, reminders and messages reset"

    async def new_or_updated_reminder(
        self,
        customer_response: Annotated[
            str,
            "Contextual description of the reminder. Should be detailed enough to be understood by anyone. Example: 'Watch model is Rolex Submariner 116610LN', 'User said the witnesses car was red but the police report says it was blue. Double check with the involved parties'.",
        ],
        description: Annotated[
            str,
            "The text to be read to the customer to confirm the reminder. Only speak about this action. Use an imperative sentence. Example: 'I am creating a reminder for next week to call back the customer', 'I am creating a reminder for next week to send the report'.",
        ],
        due_date_time: Annotated[
            str,
            "Datetime when the reminder should be triggered. Should be in the future, in the ISO format.",
        ],
        owner: Annotated[
            str,
            "The owner of the reminder. Can be 'customer', 'assistant', or a third party from the claim. Try to be as specific as possible, with a name. Example: 'customer', 'assistant', 'contact', 'witness', 'police'.",
        ],
        title: Annotated[
            str,
            "Short title of the reminder. Should be short and concise, in the format 'Verb + Subject'. Title is unique and allows the reminder to be updated. Example: 'Call back customer', 'Send analysis report', 'Study replacement estimates for the stolen watch'.",
        ],
    ) -> str:
        """
        Use this if you think there is something important to do in the future, and you want to be reminded about it. If it already exists, it will be updated with the new values. Example: 'Remind Assitant thuesday at 10am to call back the customer', 'Remind Assitant next week to send the report', 'Remind the customer next week to send the documents by the end of the month'.
        """
        await self.user_callback(customer_response, self.style)

        for reminder in self.call.reminders:
            if reminder.title == title:
                try:
                    reminder.description = description
                    reminder.due_date_time = due_date_time  # type: ignore
                    reminder.owner = owner
                    return f'Reminder "{title}" updated.'
                except ValidationError as e:  # Catch error
                    return f'Failed to edit reminder "{title}": {e.json()}'

        try:
            reminder = ReminderModel(
                description=description,
                due_date_time=due_date_time,  # type: ignore
                owner=owner,
                title=title,
            )
            self.call.reminders.append(reminder)
            return f'Reminder "{title}" created.'
        except ValidationError as e:  # Catch error
            return f'Failed to create reminder "{title}": {e.json()}'

    async def updated_claim(
        self,
        customer_response: Annotated[
            str,
            "The text to be read to the customer to confirm the update. Only speak about this action. Use an imperative sentence. Example: 'I am updating the involved parties to Marie-Jeanne and Jean-Pierre', 'I am updating the contact contact info to 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
        ],
        field: Annotated[
            str, f"The claim field to update: {list(ClaimModel.editable_fields())}"
        ],
        value: Annotated[
            str,
            "The claim field value to update. For dates, use YYYY-MM-DD HH:MM format (e.g. 2024-02-01 18:58). For phone numbers, use E164 format (e.g. +33612345678).",
        ],
    ) -> str:
        """
        Use this if the user wants to update a claim field with a new value. Example: 'Update claim explanation to: I was driving on the highway when a car hit me from behind', 'Update contact contact info to: 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.
        """
        await self.user_callback(customer_response, self.style)

        if not field in ClaimModel.editable_fields():
            return f'Failed to update a non-editable field "{field}".'

        try:
            # Define the field and force to trigger validation
            copy = self.call.claim.model_dump()
            copy[field] = value
            self.call.claim = ClaimModel.model_validate(copy)
            return f'Updated claim field "{field}" with value "{value}".'
        except ValidationError as e:  # Catch error to inform LLM
            return f'Failed to edit field "{field}": {e.json()}'

    async def talk_to_human(self) -> str:
        """
        Use this if the user wants to talk to a human and Assistant is unable to help. This will transfer the customer to an human agent. Approval from the customer must be explicitely given. Never use this action directly after a recall. Example: 'I want to talk to a human', 'I want to talk to a real person'.
        """
        await self.cancellation_callback()
        await handle_play(
            call=self.call,
            client=self.client,
            context=CallContextEnum.CONNECT_AGENT,
            text=await CONFIG.prompts.tts.end_call_to_connect_agent(self.call),
        )
        return "Transferring to human agent"

    async def search_document(
        self,
        customer_response: Annotated[
            str,
            "The text to be read to the customer to confirm the update. Only speak about this action. Use an imperative sentence. Example: 'I am searching for the document about the car accident', 'I am searching for the document about the stolen watch'.",
        ],
        queries: Annotated[
            list[str],
            "The text queries to perform the search. Example: ['How much does it cost to repair a broken window', 'What are the requirements to ask for a cyber attack insurance']",
        ],
    ) -> str:
        """
        Use this if the user wants to search for a public specific information you don't have. Example: contract, law, regulation, article, etc.
        """
        await self.user_callback(customer_response, self.style)

        # Execute in parallel
        tasks = await asyncio.gather(
            *[self.search.training_asearch_all(query, self.call) for query in queries]
        )
        # Flatten, remove duplicates, and sort by score
        res = sorted(set(training for task in tasks for training in task or []))

        return f"Search results: {TypeAdapter(List[TrainingModel]).dump_json(res).decode()}"

    @staticmethod
    def to_openai() -> List[ChatCompletionToolParam]:
        return [
            function_schema(func[1])
            for func in getmembers(LlmPlugins, isfunction)
            if not func[0].startswith("_")
        ]
