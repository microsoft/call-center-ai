from azure.communication.callautomation import CallConnectionClient
from helpers.call_utils import ContextEnum as CallContextEnum, handle_play
from helpers.config import CONFIG
from helpers.llm_utils import function_schema
from helpers.logging import build_logger
from inspect import getmembers, isfunction
from models.call import CallModel
from models.claim import ClaimModel
from models.message import StyleEnum as MessageStyleEnum
from models.reminder import ReminderModel
from openai.types.chat import ChatCompletionToolParam
from pydantic import ValidationError
from typing import Awaitable, Callable, Annotated, Literal
import asyncio


_logger = build_logger(__name__)
_search = CONFIG.ai_search.instance()


class LlmPlugins:
    call: CallModel
    cancellation_callback: Callable[[], Awaitable]
    client: CallConnectionClient
    post_call_intelligence: Callable[[CallModel], None]
    style: MessageStyleEnum = MessageStyleEnum.NONE
    user_callback: Callable[[str, MessageStyleEnum], Awaitable]

    def __init__(
        self,
        call: CallModel,
        cancellation_callback: Callable[[], Awaitable],
        client: CallConnectionClient,
        post_call_intelligence: Callable[[CallModel], None],
        user_callback: Callable[[str, MessageStyleEnum], Awaitable],
    ):
        self.call = call
        self.cancellation_callback = cancellation_callback
        self.client = client
        self.post_call_intelligence = post_call_intelligence
        self.user_callback = user_callback

    async def end_call(self) -> str:
        """
        Use this if the customer said they want to end the call. Requires an explicit verbal validation from the customer. This will hang up the call. Never use this action directly after a recall. Example: 'I want to hang up', 'Goodbye, see you tomorrow'.
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
            "Phrase used to confirm the creation of a new claim. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am creating a new claim for a car accident.', 'A new claim for a stolen watch is being created.'.",
        ],
    ) -> str:
        """
        Use this if the customer wants to create a new claim for a totally different subject. This will reset the claim and reminder data. Old is stored but not accessible anymore. Approval from the customer must be explicitely given. Example: 'I want to create a new claim'.
        """
        await self.user_callback(customer_response, self.style)
        # Launch post-call intelligence for the current call
        self.post_call_intelligence(self.call)
        # Store the last message and use it at first message of the new claim
        last_message = self.call.messages[-1]
        call = CallModel(phone_number=self.call.phone_number)
        call.messages.append(last_message)
        return "Claim, reminders and messages reset"

    async def new_or_updated_reminder(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the update. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am creating a reminder for next week to call you back.', 'A todo for next week is planned.'.",
        ],
        description: Annotated[
            str,
            "Description of the reminder. Should be detailed enough to be understood by anyone. Example: 'Call back customer to get more details about the accident', 'Send analysis report to the customer'.",
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
        Use this if you think there is something important to do in the future, and you want to be reminded about it. If it already exists, it will be updated with the new values.
        """
        await self.user_callback(customer_response, self.style)

        # Check if reminder already exists, if so update it
        for reminder in self.call.reminders:
            if reminder.title == title:
                try:
                    reminder.description = description
                    reminder.due_date_time = due_date_time  # type: ignore
                    reminder.owner = owner
                    return f'Reminder "{title}" updated.'
                except ValidationError as e:  # Catch error
                    return f'Failed to edit reminder "{title}": {e.json()}'

        # Create new reminder
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
            "Phrase used to confirm the update. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am updating the involved parties to Marie-Jeanne and Jean-Pierre.', 'The contact contact info for your home address is now, 123 rue De La Paix.'.",
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
        Use this if the customer wants to update a claim field with a new value. It is OK to approximate dates if the customer is not precise (e.g., "last night" -> today 04h, "I'm stuck on the highway" -> now).
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
        Use this if the customer wants to talk to a human and Assistant is unable to help. Requires an explicit verbal validation from the customer. This will transfer the customer to an human agent. Never use this action directly after a recall. Example: 'I want to talk to a human', 'I want to talk to a real person'.
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
            "Phrase used to confirm the search. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am searching for the document about the car accident.', 'I am looking for the contract details.'.",
        ],
        queries: Annotated[
            list[str],
            "The text queries to perform the search. Example: ['How much does it cost to repair a broken window', 'What are the requirements to ask for a cyber attack insurance']",
        ],
    ) -> str:
        """
        Use this if the customer wants to search for a public specific information you don't have. Examples: contract, law, regulation, article.
        """
        await self.user_callback(customer_response, self.style)
        # Execute in parallel
        tasks = await asyncio.gather(
            *[_search.training_asearch_all(query, self.call) for query in queries]
        )
        # Flatten, remove duplicates, and sort by score
        trainings = sorted(set(training for task in tasks for training in task or []))
        # Format results
        res = "# Search results"
        for training in trainings:
            res += f"\n- {training.title}: {training.content}"
        return res

    async def notify_emergencies(
        self,
        customer_response: Annotated[
            str,
            "Phrase used to confirm the action. This phrase will be spoken to the user. Describe what you're doing in one sentence. Example: 'I am notifying the emergency services.', 'Police number is confirmed.'.",
        ],
        reason: Annotated[
            str,
            "The reason to notify the emergency services. Should be detailed enough to be understood by anyone. Example: 'A person is having a heart attack', 'A child is being attacked by a dog'.",
        ],
        location: Annotated[
            str,
            "The location of the emergency. Should be detailed enough to be understood by anyone. Should contains details like the floor, the building, the code to enter, etc. Example: '123 rue de la paix 75000 Paris, Building A, 3rd floor, code 1234', '12 avenue de la manivelle 13000 Marseille, behind the red door'.",
        ],
        contact: Annotated[
            str,
            "The local contact of a person on site. Should be detailed enough to be understood by anyone. Should contains details like the name, the phone number, etc. Example: 'Marie-Jeanne, +33735119775', 'Jean-Pierre, wear a red hat'.",
        ],
        service: Annotated[
            Literal["police", "firefighters", "pharmacy", "veterinarian", "hospital"],
            "The emergency service to notify.",
        ],
    ) -> str:
        """
        Use this if the customer wants to notify the emergency services for a specific reason. This will notify the emergency services. Use it only if the situation is critical and requires immediate intervention. Examples: 'My neighbor is having a heart attack', 'A child is lying on the ground and is not moving', 'I am stuck in a car in fire'.
        """
        await self.user_callback(customer_response, self.style)
        # TODO: Implement notification to emergency services for production usage
        _logger.info(
            f"Notifying {service}, location {location}, contact {contact}, reason {reason}."
        )
        return f"Notifying {service} for {reason}."

    @staticmethod
    def to_openai() -> list[ChatCompletionToolParam]:
        return [
            function_schema(func[1])
            for func in getmembers(LlmPlugins, isfunction)
            if not func[0].startswith("_")
        ]
