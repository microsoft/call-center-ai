import asyncio
from html import escape
from typing import Annotated, Literal, TypedDict

from pydantic import ValidationError

from app.helpers.call_utils import (
    handle_realtime_tts,
    handle_transfer,
)
from app.helpers.config import CONFIG
from app.helpers.llm_utils import AbstractPlugin, add_customer_response
from app.helpers.logging import logger
from app.models.call import CallStateModel
from app.models.message import (
    ActionEnum as MessageActionEnum,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
)
from app.models.reminder import ReminderModel
from app.models.training import TrainingModel

_db = CONFIG.database.instance
_search = CONFIG.ai_search.instance
_sms = CONFIG.sms.instance


class UpdateClaimDict(TypedDict):
    field: str
    value: str


class DefaultPlugin(AbstractPlugin):
    # No customer response, we have a pre-defined response
    async def end_call(self) -> str:
        """
        Use this if the customer said they want to end the call.

        # Behavior
        1. Hangup the call for everyone
        2. The call with Assistant is ended

        # Rules
        - Requires an explicit verbal validation from the customer
        - Never use this action directly after a recall

        # Usage examples
        - All participants are satisfied and agree to end the call
        - Customer said "bye bye"
        """
        from app.helpers.call_events import hangup_realtime_now

        await hangup_realtime_now(
            call=self.call,
            client=self.client,
            post_callback=self.post_callback,
            scheduler=self.scheduler,
            tts_client=self.tts_client,
        )
        return "Call ended"

    @add_customer_response(
        [
            "I'am creating it right now.",
            "We'll start a case.",
        ]
    )
    async def new_claim(
        self,
    ) -> str:
        """
        Use this if the customer wants to open a new claim.

        # Behavior
        1. Old claim is stored but not accessible anymore
        2. Reset the conversation

        # Rules
        - Approval from the customer must be explicitely given (e.g. 'I want to create a new claim')
        - This should be used only when the subject is totally different

        # Usage examples
        - Customer is talking about a totally different subject and confirmed they was done with the previous one
        - Customer wants explicitely to create a new claim
        """
        # Launch post-call intelligence for the current call
        await self.post_callback(self.call)

        # Store the last message and use it at first message of the new claim
        self.call = await _db.call_create(
            CallStateModel(
                initiate=self.call.initiate.model_copy(),
                voice_id=self.call.voice_id,
                messages=[
                    # Reinsert the call action
                    MessageModel(
                        action=MessageActionEnum.CALL,
                        content="",
                        persona=MessagePersonaEnum.HUMAN,
                    ),
                    # TODO: Should it be a reminder for the last conversation subject? It would allow to keep the context of the conversation. Keeping the last message in the history is felt as weird for users (see: https://github.com/microsoft/call-center-ai/issues/397).
                ],
            )
        )
        return "Claim, reminders and messages reset"

    @add_customer_response(
        [
            "A todo for next week is planned.",
            "I'm creating a reminder for the company to manage this for you.",
            "The rendez-vous is scheduled for tomorrow.",
        ]
    )
    async def new_or_updated_reminder(
        self,
        description: Annotated[
            str,
            "Description of the reminder, in English. Should be detailed enough to be understood by anyone. Example: 'Call back customer to get more details about the accident', 'Send analysis report to the customer'.",
        ],
        due_date_time: Annotated[
            str,
            "Datetime when the reminder should be triggered. Should be in the future, in the ISO format.",
        ],
        owner: Annotated[
            str,
            "The owner of the reminder, in English. Can be 'customer', 'assistant', or a third party from the claim. Try to be as specific as possible, with a name. Example: 'customer', 'assistant', 'contact', 'witness', 'police'.",
        ],
        title: Annotated[
            str,
            "Short title of the reminder, in English. Should be short and concise, in the format 'Verb + Subject'. Title is unique and allows the reminder to be updated. Example: 'Call back customer', 'Send analysis report', 'Study replacement estimates for the stolen watch'.",
        ],
    ) -> str:
        """
        Use this if you think there is something important to do.

        # Behavior
        1. Create a reminder with the given values
        2. Return a confirmation message

        # Rules
        - A reminder should be as specific as possible
        - If a reminder already exists, it will be updated with the new values
        - The due date should be in the future

        # Usage examples
        - Ask precisions to an expert or the backoffice
        - Call back for a follow-up
        - Wait for customer to send a document
        """
        # Check if reminder already exists, if so update it
        for reminder in self.call.reminders:
            if reminder.title == title:
                try:
                    reminder.description = description
                    reminder.due_date_time = due_date_time  # pyright: ignore
                    reminder.owner = owner
                    return f'Reminder "{title}" updated.'
                except ValidationError as e:
                    return f'Failed to edit reminder "{title}": {e.json()}'

        # Create new reminder
        try:
            reminder = ReminderModel(
                description=description,
                due_date_time=due_date_time,  # pyright: ignore
                owner=owner,
                title=title,
            )
            self.call.reminders.append(reminder)
            return f'Reminder "{title}" created.'
        except ValidationError as e:
            return f'Failed to create reminder "{title}": {e.json()}'

    @add_customer_response(
        [
            "I am updating the claim with your new address.",
            "The phone number is now stored in the case.",
            "Your birthdate is written down.",
        ]
    )
    async def updated_claim(
        self,
        updates: Annotated[
            list[UpdateClaimDict],
            """
            The field to update, in English.

            # Available fields
            {% for field in call.initiate.claim %}
            {% if not field.description %}
            - {{ field.name }}
            {% else %}
            - '{{ field.name }}', {{ field.description }}
            {% endif %}
            {% endfor %}

            # Data format
            - For dates, use YYYY-MM-DD HH:MM format (e.g. 2024-02-01 18:58)
            - For phone numbers, use E164 format (e.g. +33612345678)

            # Data format
            [{'field': '[field]', 'value': '[value]'}]

            # Examples
            - [{'field': 'policyholder_email', 'value': 'mariejeanne@gmail.com'}]
            - [{'field': 'policyholder_name', 'value': 'Marie-Jeanne Duchemin'}, {'field': 'policyholder_phone', 'value': '+33612345678'}]
            """,
        ],
    ) -> str:
        """
        Use this if the customer wants to update one or more fields in the claim.

        # Behavior
        1. Update the claim with the new values
        2. Return a confirmation message

        # Rules
        - For values, it is OK to approximate dates if the customer is not precise (e.g., "last night" -> today 04h, "I'm stuck on the highway" -> now)
        - It is best to update multiple fields at once

        # Usage examples
        - Change the incident date
        - Correct the name of the customer
        - Store details about the conversation
        - Update the claim with a new phone number
        """
        # Update all claim fields
        res = "# Updated fields"
        for field in updates:
            res += f"\n- {self._update_claim_field(field)}"
        return res

    def _update_claim_field(self, update: UpdateClaimDict) -> str:
        field = update["field"]
        new_value = update["value"]

        # Update field
        old_value = self.call.claim.get(field, None)
        try:
            self.call.claim[field] = new_value
            CallStateModel.model_validate(self.call)  # Force a re-validation
            return f'Updated claim field "{field}" with value "{new_value}".'
        # Catch error to inform LLM and rollback changes
        except ValidationError as e:
            self.call.claim[field] = old_value
            return f'Failed to edit field "{field}": {e.json()}'

    @add_customer_response(
        [
            "Connecting you to a human agent.",
            "I'm calling a human to help you.",
            "Transfer to a human agent in progress.",
        ]
    )
    async def talk_to_human(self) -> str:
        """
        Use this if the customer wants to talk to a human and Assistant is unable to help.

        # Behavior
        1. Transfer the customer to an human agent
        2. The call with Assistant is ended

        # Rules
        - Requires an explicit verbal validation from the customer
        - Never use this action directly after a recall

        # Usage examples
        - Customer wants to talk to a human
        - No more information available and customer insists
        - Not satisfied with the answers
        """
        # Play TTS
        await handle_realtime_tts(
            call=self.call,
            scheduler=self.scheduler,
            text=await CONFIG.prompts.tts.end_call_to_connect_agent(self.call),
            tts_client=self.tts_client,
        )
        # Transfer
        await handle_transfer(
            call=self.call,
            client=self.client,
            target=self.call.initiate.agent_phone_number,
        )
        return "Transferring to human agent"

    @add_customer_response(
        [
            "I am looking for the article about the new law on cyber security.",
            "I am looking in our database for your car insurance contract.",
            "I am searching for the procedure to declare a stolen luxury watch.",
            "I'm looking for this document in our database.",
        ]
    )
    async def search_document(
        self,
        queries: Annotated[
            list[str],
            "The text queries to perform the search, in English. Example: ['How much does it cost to repair a broken window?', 'What are the requirements to ask for a cyber attack insurance?']",
        ],
    ) -> str:
        """
        Use this if the customer wants to search for a public specific information you don't have.

        # Rules
        - Multiple queries should be used with different viewpoints and vocabulary
        - The search should be as specific as possible

        # Searchable topics
        contract, law, regulation, article, procedure, guide

        # Usage examples
        - Find the article about the new law
        - Know the procedure to declare a stolen luxury watch
        - Understand the requirements to ask for a cyber attack insurance
        """
        # Execute in parallel
        tasks = await asyncio.gather(
            *[
                _search.training_search_all(text=query, lang="en-US")
                for query in queries
            ]
        )

        # Flatten, remove duplicates, and sort by score
        trainings = sorted(set(training for task in tasks for training in task or []))

        # Format documents for Content Safety scan compatibility
        # See: https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/content-filter?tabs=warning%2Cpython-new#embedding-documents-in-your-prompt
        trainings_str = "\n".join(
            [
                f"<documents>{escape(training.model_dump_json(exclude=TrainingModel.excluded_fields_for_llm()))}</documents>"
                for training in trainings
            ]
        )

        # Format results
        res = "# Search results"
        res += f"\n{trainings_str}"
        return res

    @add_customer_response(
        [
            "I am calling the firefighters to help you with the fire.",
            "I am notifying the emergency services right now.",
            "The pharmacy is notified for the emergency.",
        ]
    )
    async def notify_emergencies(
        self,
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
        Use this if the customer wants to notify the emergency services for a specific reason.

        # Behavior
        1. A record is stored in the system
        2. A notification is sent to the emergency services

        # Rules
        - Use it only if the situation is critical and requires immediate intervention

        # Usage examples
        - A child is lying on the ground and is not moving
        - A neighbor is having a heart attack
        - Someons is stuck in a car accident
        """
        # TODO: Implement notification to emergency services for production usage
        logger.info(
            "Notifying %s, location %s, contact %s, reason %s",
            service,
            location,
            contact,
            reason,
        )
        return f"Notifying {service} for {reason}"

    @add_customer_response(
        [
            "I am sending a SMS to your phone number.",
            "I am texting you the information right now.",
            "I'am sending it.",
            "SMS with the details is sent.",
        ]
    )
    async def send_sms(
        self,
        message: Annotated[
            str,
            "The message to send to the customer.",
        ],
    ) -> str:
        """
        Use when there is a real need to send a SMS to the customer.

        # Usage examples
        - Ask a question, if the call quality is bad
        - Confirm a detail like a reference number, if there is a misunderstanding
        - Send a confirmation, if the customer wants to have a written proof
        """
        # Send SMS
        success = await _sms.send(
            content=message,
            phone_number=self.call.initiate.phone_number,
        )
        if not success:
            return "Failed to send SMS"

        # Add message to call
        self.call.messages.append(
            MessageModel(
                action=MessageActionEnum.SMS,
                content=message,
                lang_short_code=self.call.lang.short_code,
                persona=MessagePersonaEnum.ASSISTANT,
            )
        )
        return "SMS sent"

    @add_customer_response(
        [
            "I am slowing down the speech.",
            "Is it better now that I am speaking slower?",
            "My voice is now faster.",
        ],
        before=False,  # Speak after the speed change
    )
    async def speech_speed(
        self,
        speed: Annotated[
            float,
            "The new speed of the voice. Should be between 0.75 and 1.25, where 1.0 is the normal speed.",
        ],
    ) -> str:
        """
        Use this if the customer wants to change the speed of the voice.

        # Behavior
        1. Update the voice speed
        2. Return a confirmation message

        # Usage examples
        - Speed up or slow down the voice
        - Trouble understanding the voice because it is too fast or too slow
        """
        # Clamp speed between min and max
        speed = max(0.75, min(speed, 1.25))

        # Update voice
        initial_speed = self.call.initiate.prosody_rate
        self.call.initiate.prosody_rate = speed

        # LLM confirmation
        return f"Voice speed set to {speed} (was {initial_speed})"

    @add_customer_response(
        [
            "For de-DE, 'Ich spreche jetzt auf Deutsch.'",
            "For en-ES, 'Espero que me entiendas mejor en español.'",
            "For fr-FR, 'Cela devrait être mieux en français.'",
        ],
        before=False,  # Speak after the language change
    )
    async def speech_lang(
        self,
        lang: Annotated[
            str,
            """
            The new language of the conversation.

            # Available short codes
            {% for available in call.initiate.lang.availables %}
            - {{ available.short_code }} ({{ available.pronunciations_en[0] }})
            {% endfor %}

            # Data format
            short code

            # Examples
            - 'en-US'
            - 'es-ES'
            - 'zh-CN'
            """,
        ],
    ) -> str:
        """
        Use this if the customer wants to speak in another language.

        # Behavior
        1. Update the conversation language
        2. Return a confirmation message

        # Usage examples
        - A participant wants to speak in another language
        - Customer made a mistake in the language selection
        - Trouble understanding the voice in the current language
        """
        # Check if lang is available
        if not any(
            lang == available.short_code
            for available in self.call.initiate.lang.availables
        ):
            return f"Language {lang} not available"

        # Update lang
        initial_lang = self.call.lang.short_code
        self.call.lang_short_code = lang

        # LLM confirmation
        return f"Voice language set to {lang} (was {initial_lang})"
