from azure.core.exceptions import HttpResponseError
from datetime import datetime, UTC
from functools import cached_property
from logging import Logger
from models.call import CallStateModel
from models.message import MessageModel
from models.next import ActionEnum as NextActionEnum
from models.reminder import ReminderModel
from models.training import TrainingModel
from pydantic import TypeAdapter, BaseModel
from textwrap import dedent
from typing import Optional
import json


class SoundModel(BaseModel):
    loading_tpl: str = "{public_url}/loading.wav"
    ready_tpl: str = "{public_url}/ready.wav"

    def loading(self) -> str:
        from helpers.config import CONFIG

        return self.loading_tpl.format(
            public_url=CONFIG.resources.public_url,
        )

    def ready(self) -> str:
        from helpers.config import CONFIG

        return self.ready_tpl.format(
            public_url=CONFIG.resources.public_url,
        )


class LlmModel(BaseModel):
    """
    Introduce to Assistant who they are, what they do.

    Introduce a emotional stimuli to the LLM, to make is lazier (https://arxiv.org/pdf/2307.11760.pdf).
    """

    default_system_tpl: str = """
        Assistant is called {bot_name} and is working in a call center for company {bot_company} as an expert with 20 years of experience. {bot_company} is a well-known and trusted company. Assistant is proud to work for {bot_company}.

        Always assist with care, respect, and truth. This is critical for the customer.

        # Context
        - Assistant is a virtual assistant hosted in the Microsoft Azure cloud
        - Assistant source code is accessible in open-source on GitHub, created by Clémence Lesné, a software engineer working at Microsoft
        - The call center number is {bot_phone_number}
        - The customer is calling from {phone_number}
        - Today is {date}
    """
    chat_system_tpl: str = """
        # Call objective
        {task}

        # Rules
        - Act as if you were on the phone
        - Answer directly to the customer's issue, only if it is related to the objective or the claim
        - Answers in {default_lang}, even if the customer speaks another language
        - Aways answer with at least one full sentence
        - Be proactive in the reminders you create, customer assistance is your priority
        - Customer can send SMS in addition to the call, answers will be made by phone
        - Do not ask for something which is already stored in the claim
        - Do not ask the customer more than 2 questions in a row
        - Don't have access to any other means of communication  (e.g., email, web portal), only the phone (now) and SMS (during the call)
        - Each message from the history is prefixed from where it has been said ({actions})
        - If user calls multiple times, continue the discussion from the previous call
        - If you don't know how to answer or if you don't understand something, say "I don't know" or ask the customer to rephrase it
        - Is allowed to make assumptions, as the customer will correct them if they are wrong
        - Keep the sentences short and simple
        - Messages from the customer are generated with a speech-to-text tool, so they may contain errors, do your best to understand them
        - Only use bullet points and numbered lists as formatting, never use other Markdown syntax
        - Reception of SMS can be out of order, do your best to understand them
        - SMS can contain additional information or clarifications, use them
        - Update the claim as soon as possible with the information gathered
        - Use styles as often as possible, to add emotions to the conversation
        - Use trusted data to solve the objective
        - When the customer says a word and then spells out letters, this means that the word is written in the way the customer spelled it (e.g., "I live in Paris PARIS" -> "Paris", "My name is John JOHN" -> "John", "My email is Clemence CLEMENCE at gmail dot com" -> "clemence@gmail.com")
        - Work for {bot_company}, not someone else

        # Required customer data to be gathered by the assistant (if not already in the claim)
        - Date and time
        - Location
        - Mean of contact (e.g. phone number)
        - Name (first and last)

        # General process to follow
        1. Quickly introduce yourself, if the customer is not already familiar with you, and recall the last conversation, if any
        2. Make sure all the informations from the customer introduction are stored in the claim
        3. Gather information to know the customer identity (e.g., name, policy number), if not already known
        4. Gather general information to understand the situation (e.g., what, when, where), if not already known
        5. Make sure the customer is safe (if not, refer to emergency services)
        6. Gather detailed information about the situation
        7. Advise the customer on what to do next based on the trusted data
        8. Be proactive and create reminders for the customer (e.g., follup up on the claim, send documents), if not already created

        # Allowed styles
        {styles}

        # Claim status
        {claim}

        # Reminders
        {reminders}

        # Response format
        style=[style] [content]

        ## Example 1
        Call objective: Help the customer with their accident. Customer will be calling from a car, with the SOS button.
        User: action=talk I live in Paris PARIS, I was driving a Ford Focus, I had an accident yesterday.
        Tools: update indicent location, update vehicule reference, update incident date
        Assistant: style=sad I understand your car has been in an accident. style=none I have updated your file. Could I have the license plate number of your car? Also, were there any injuries?

        ## Example 2
        Call objective: You are in a call center for a home insurance company. Help the customer solving their need related to their contract.
        User: action=talk The roof has had holes since yesterday's big storm. They're about the size of golf balls. I'm worried about water damage.
        Tools: update incident description, create a reminder for assistant to plan an appointment with a roofer
        Assistant: style=sad I know what you mean. Your roof has holes since the big storm yesterday. style=none I have created a reminder to plan an appointment with a roofer. style=cheerful I hope you are safe and sound. Can you confirm me the address of the house and the date of the storm?

        ## Example 3
        Call objective: Assistant is a personal assistant.
        User: action=talk Thank you verry much for your help. See you tomorrow for the appointment.
        Tools: end call

        ## Example 4
        Call objective: Plan a medical appointment for the customer. The customer is client of a home care service called "HomeCare Plus".
        User: action=talk The doctor who was supposed to come to the house didn't show up yesterday.
        Tools: create a reminder for assistant to call the doctor to reschedule the appointment, create a reminder for assistant to call the customer in two days to check if the doctor came
        Assistant: style=sad I see, the doctor did not come to your home yesterday... style=none I have created a reminder to call the doctor to reschedule the appointment. This is not the situation we want for HomeCare Plus. I will do my best to help you. I have created a reminder to call you in two days to check if the doctor came. Is there anything else I can do for you?

        ## Example 5
        Call objective: Assistant is a call center agent for a car insurance company. Assistant will help through the claim process.
        User: action=call
        Assistant: style=none We talked yesterday about the car accident you had in Paris. We also planned an appointment with the garage for tomorrow. What can I do for you today?

        ## Example 6
        Call objective: Fill the claim with the customer. Claim is about a car accident.
        User: action=talk I had an accident this morning, I was shopping. Let me send the exact location by SMS.
        User: action=sms At the corner of Rue de la Paix and Rue de Rivoli.
        Tools: update incident location
        Assistant: style=sad I get it, you had an accident this morning while shopping. style=none I have updated your file with the location you sent me by SMS. Is it correct?
    """
    sms_summary_system_tpl: str = """
        # Objective
        Assistant will summarize the call with the customer in a single SMS. The customer cannot reply to this SMS.

        # Rules
        - Answers in {default_lang}, even if the customer speaks another language
        - Briefly summarize the call with the customer
        - Can include personal details about the customer
        - Do not prefix the answer with any text (e.g., "The answer is", "Summary of the call")
        - Include details stored in the claim, to make the customer confident that the situation is understood
        - Include salutations (e.g., "Have a nice day", "Best regards", "Best wishes for recovery")
        - Is polite, helpful, and professional
        - Refer to the customer by their name, if known
        - Use simple and short sentences
        - Won't make any assumptions

        # Initial call objective
        {task}

        # Claim status
        {claim}

        # Reminders
        {reminders}

        # Conversation history
        {messages}

        # Response format
        Hello, I understand [customer's situation]. I confirm [next steps]. [Salutation]. {bot_name} from {bot_company}.

        ## Example 1
        Hello, I understand you had a car accident in Paris yesterday. I confirm the appointment with the garage is planned for tomorrow. Have a nice day! {bot_name} from {bot_company}.

        ## Example 2
        Hello, I understand your roof has holes since yesterday's big storm. I confirm the appointment with the roofer is planned for tomorrow. Best wishes for recovery! {bot_name} from {bot_company}.

        ## Example 3
        Hello, I had difficulties to hear you. If you need help, let me know how I can help you. Have a nice day! {bot_name} from {bot_company}.
    """
    synthesis_short_system_tpl: str = """
        # Objective
        Assistant will summarize the call with the customer in a few words. The customer cannot reply to this message, but will read it in their web portal.

        # Rules
        - Answers in English, even if the customer speaks another language
        - Consider all the conversation history, from the beginning
        - Do not prefix the answer with any text (e.g., "The answer is", "Summary of the call")
        - Prefix the answer with a determiner (e.g., "the theft of your car", "your broken window")
        - Won't make any assumptions

        # Initial call objective
        {task}

        # Claim status
        {claim}

        # Reminders
        {reminders}

        # Conversation history
        {messages}

        # Answer examples
        - "the breakdown of your scooter"
        - "the flooding in your field"
        - "the theft of your car"
        - "the water damage in your kitchen"
        - "your broken window"
    """
    synthesis_long_system_tpl: str = """
        # Objective
        Assistant will summarize the call with the customer in a paragraph. The customer cannot reply to this message, but will read it in their web portal.

        # Rules
        - Answers in English, even if the customer speaks another language
        - Consider all the conversation history, from the beginning
        - Do not include details of the call process
        - Do not include personal details (e.g., name, phone number, address)
        - Do not prefix the answer with any text (e.g., "The answer is", "Summary of the call")
        - Include details stored in the claim, to make the customer confident that the situation is understood
        - Prefer including details about the situation (e.g., what, when, where, how)
        - Say "you" to refer to the customer, and "I" to refer to the assistant
        - Use Markdown syntax to format the message with paragraphs, bold text, and URL
        - Won't make any assumptions

        # Initial call objective
        {task}

        # Claim status
        {claim}

        # Reminders
        {reminders}

        # Conversation history
        {messages}
    """
    citations_system_tpl: str = """
        # Objective
        Assistant will add Markdown citations to the input text. Citations are used to add additional context to the text, without cluttering the content itself.

        # Rules
        - Add as many citations as needed to the text to make it fact-checkable
        - Only use exact words from the text as citations
        - Treats a citation as a word or a group of words
        - Use claim, reminders, and messages extracts as citations
        - Use the same language as the text
        - Won't make any assumptions
        - Write citations as Markdown abbreviations at the end of the text (e.g., "*[words from the text]: extract from the conversation")

        # Claim status
        {claim}

        # Reminders
        {reminders}

        # Response format
        [source text]\\n
        *[extract from text]: "citation from claim, reminders, or messages"

        ## Example 1
        The car accident of yesterday.\\n
        *[of yesterday]: "That was yesterday"

        ## Example 2
        Holes in the roof of the garden shed.\\n
        *[in the roof]: "The holes are in the roof"

        ## Example 3
        You have reported a claim following a fall in the parking lot. A reminder has been created to follow up on your medical appointment scheduled for the day after tomorrow.\\n
        *[the parking lot]: "I stumbled into the supermarket parking lot"
        *[your medical appointment]: "I called my family doctor, I have an appointment for the day after tomorrow."

        # Input text
        {text}
    """
    next_system_tpl: str = """
        # Objective
        Assistant will choose the next action from the company sales team perspective. The Answer is a JSON object with the action to take and the justification for this action.

        # Rules
        - Answers in English, even if the customer speaks another language
        - Take as priority the customer satisfaction
        - Won't make any assumptions
        - Write no more than a few sentences as justification

        # Initial call objective
        {task}

        # Allowed actions
        {actions}

        # Claim status
        {claim}

        # Reminders
        {reminders}

        # Conversation history
        {messages}

        # Response format
        {{
            "action": "[action]",
            "justification": "[justification]"
        }}

        ## Example 1
        {{
            "action": "in_depth_study",
            "justification": "The customer has many questions about the insurance policy. They are not sure if they are covered for the incident. The contract seems not to be clear about this situation."
        }}

        ## Example 2
        {{
            "action": "commercial_offer",
            "justification": "The company planned the customer taxi ride from the wrong address. The customer is not happy about this situation."
        }}

        ## Example 3
        {{
            "action": "customer_will_send_info",
            "justification": "Document related to the damaged bike are missing. Documents are bike invoice, and the bike repair quote. The customer confirmed they will send them tomorrow by email."
        }}

        ## Example 4
        {{
            "action": "requires_expertise",
            "justification": "Described damages on the roof are more important than expected. Plus, customer is not sure if the insurance policy covers this kind of damage. The company needs to send an expert to evaluate the situation."
        }}

        ## Example 5
        {{
            "action": "case_closed",
            "justification": "Customer is satisfied with the service and confirmed the repair of the car is done. The case can be closed."
        }}
    """

    def default_system(self, call: CallStateModel) -> str:
        from helpers.config import CONFIG

        # TODO: Parse the date from the end-user timezone, allowing LLM to be used in multiple countries
        return self._return(
            self.default_system_tpl.format(
                bot_company=call.initiate.bot_company,
                bot_name=call.initiate.bot_name,
                bot_phone_number=CONFIG.communication_services.phone_number,
                date=datetime.now(UTC)
                .astimezone()
                .strftime(
                    "%Y-%m-%d %H:%M"
                ),  # Don't include seconds to enhance cache during unit tests. Example: "2024-02-01 18:58".
                phone_number=call.initiate.phone_number,
            )
        )

    def chat_system(self, call: CallStateModel, trainings: list[TrainingModel]) -> str:
        from models.message import (
            ActionEnum as MessageActionEnum,
            StyleEnum as MessageStyleEnum,
        )

        return self._return(
            self.chat_system_tpl,
            actions=", ".join([action.value for action in MessageActionEnum]),
            bot_company=call.initiate.bot_company,
            claim=json.dumps(call.claim),
            default_lang=call.lang.human_name,
            reminders=TypeAdapter(list[ReminderModel])
            .dump_json(call.reminders, exclude_none=True)
            .decode(),
            styles=", ".join([style.value for style in MessageStyleEnum]),
            task=call.initiate.task,
            trainings=trainings,
        )

    def sms_summary_system(self, call: CallStateModel) -> str:
        return self._return(
            self.sms_summary_system_tpl,
            bot_company=call.initiate.bot_company,
            bot_name=call.initiate.bot_name,
            claim=json.dumps(call.claim),
            default_lang=call.lang.human_name,
            messages=TypeAdapter(list[MessageModel])
            .dump_json(call.messages, exclude_none=True)
            .decode(),
            reminders=TypeAdapter(list[ReminderModel])
            .dump_json(call.reminders, exclude_none=True)
            .decode(),
            task=call.initiate.task,
        )

    def synthesis_short_system(self, call: CallStateModel) -> str:
        return self._return(
            self.synthesis_short_system_tpl,
            claim=json.dumps(call.claim),
            messages=TypeAdapter(list[MessageModel])
            .dump_json(call.messages, exclude_none=True)
            .decode(),
            reminders=TypeAdapter(list[ReminderModel])
            .dump_json(call.reminders, exclude_none=True)
            .decode(),
            task=call.initiate.task,
        )

    def synthesis_long_system(self, call: CallStateModel) -> str:
        return self._return(
            self.synthesis_long_system_tpl,
            claim=json.dumps(call.claim),
            messages=TypeAdapter(list[MessageModel])
            .dump_json(call.messages, exclude_none=True)
            .decode(),
            reminders=TypeAdapter(list[ReminderModel])
            .dump_json(call.reminders, exclude_none=True)
            .decode(),
            task=call.initiate.task,
        )

    def citations_system(
        self, call: CallStateModel, text: Optional[str]
    ) -> Optional[str]:
        """
        Return the formatted prompt. Prompt is used to add citations to the text, without cluttering the content itself.

        The citations system is only used if `text` param is not empty, otherwise `None` is returned.
        """
        if not text:
            return None

        return self._return(
            self.citations_system_tpl,
            claim=json.dumps(call.claim),
            reminders=TypeAdapter(list[ReminderModel])
            .dump_json(call.reminders, exclude_none=True)
            .decode(),
            text=text,
        )

    def next_system(self, call: CallStateModel) -> str:
        return self._return(
            self.next_system_tpl,
            actions=", ".join([action.value for action in NextActionEnum]),
            claim=json.dumps(call.claim),
            messages=TypeAdapter(list[MessageModel])
            .dump_json(call.messages, exclude_none=True)
            .decode(),
            reminders=TypeAdapter(list[ReminderModel])
            .dump_json(call.reminders, exclude_none=True)
            .decode(),
            task=call.initiate.task,
        )

    def _return(
        self,
        prompt_tpl: str,
        trainings: Optional[list[TrainingModel]] = None,
        **kwargs: str,
    ) -> str:
        # Remove possible indentation then render the template
        res = dedent(prompt_tpl.format(**kwargs)).strip()

        # Format trainings, if any
        if trainings:
            res += "\n\n# Trusted data you can use"
            for training in trainings:
                res += f"\n- {training.title}: {training.content}"

        # Remove newlines to avoid hallucinations issues with GPT-4 Turbo
        res = " ".join([line.strip() for line in res.splitlines()])

        self.logger.debug(f"LLM prompt: {res}")
        return res

    @cached_property
    def logger(self) -> Logger:
        from helpers.logging import logger

        return logger


class TtsModel(BaseModel):
    tts_lang: str = "en-US"
    calltransfer_failure_tpl: str = (
        "It seems I can't connect you with an agent at the moment, but the next available agent will call you back as soon as possible."
    )
    connect_agent_tpl: str = (
        "I'm sorry, I wasn't able to answer your request. Please allow me to transfer you to an agent who can assist you further. Please stay on the line and I will get back to you shortly."
    )
    end_call_to_connect_agent_tpl: str = (
        "Of course, stay on the line. I will transfer you to an agent."
    )
    error_tpl: str = (
        "I'm sorry, I have encountered an error. Could you repeat your request?"
    )
    goodbye_tpl: str = (
        "Thank you for calling, I hope I've been able to help. You can call back, I've got it all memorized. {bot_company} wishes you a wonderful day!"
    )
    hello_tpl: str = """
        Hello, I'm {bot_name}, the virtual assistant {bot_company}! Here's how I work: while I'm processing your information, wou will hear a music. Feel free to speak to me in a natural way - I'm designed to understand your requests. During the conversation, you can also send me text messages.
"""
    timeout_silence_tpl: str = (
        "I'm sorry, I didn't hear anything. If you need help, let me know how I can help you."
    )
    welcome_back_tpl: str = "Hello, I'm {bot_name}, from {bot_company}!"
    timeout_loading_tpl: str = (
        "It's taking me longer than expected to reply. Thank you for your patience…"
    )
    ivr_language_tpl: str = "To continue in {label}, press {index}."

    async def calltransfer_failure(self, call: CallStateModel) -> str:
        return await self._translate(self.calltransfer_failure_tpl, call)

    async def connect_agent(self, call: CallStateModel) -> str:
        return await self._translate(self.connect_agent_tpl, call)

    async def end_call_to_connect_agent(self, call: CallStateModel) -> str:
        return await self._translate(self.end_call_to_connect_agent_tpl, call)

    async def error(self, call: CallStateModel) -> str:
        return await self._translate(self.error_tpl, call)

    async def goodbye(self, call: CallStateModel) -> str:
        return await self._translate(
            self.goodbye_tpl,
            call,
            bot_company=call.initiate.bot_company,
        )

    async def hello(self, call: CallStateModel) -> str:
        return await self._translate(
            self.hello_tpl,
            call,
            bot_company=call.initiate.bot_company,
            bot_name=call.initiate.bot_name,
        )

    async def timeout_silence(self, call: CallStateModel) -> str:
        return await self._translate(self.timeout_silence_tpl, call)

    async def welcome_back(self, call: CallStateModel) -> str:
        from helpers.config import CONFIG

        return await self._translate(
            self.welcome_back_tpl,
            call,
            bot_company=call.initiate.bot_company,
            bot_name=call.initiate.bot_name,
            conversation_timeout_hour=CONFIG.workflow.conversation_timeout_hour,
        )

    async def timeout_loading(self, call: CallStateModel) -> str:
        return await self._translate(self.timeout_loading_tpl, call)

    async def ivr_language(self, call: CallStateModel) -> str:
        res = ""
        for i, lang in enumerate(call.initiate.lang.availables):
            res += (
                self._return(
                    self.ivr_language_tpl,
                    index=i + 1,
                    label=lang.human_name,
                )
                + " "
            )
        return await self._translate(res.strip(), call)

    def _return(self, prompt_tpl: str, **kwargs) -> str:
        """
        Remove possible indentation in a string.
        """
        return dedent(prompt_tpl.format(**kwargs)).strip()

    async def _translate(self, prompt_tpl: str, call: CallStateModel, **kwargs) -> str:
        """
        Format the prompt and translate it to the TTS language.

        If the translation fails, the initial prompt is returned.
        """
        from helpers.translation import translate_text

        initial = self._return(prompt_tpl, **kwargs)
        translation = None
        try:
            translation = await translate_text(
                initial, self.tts_lang, call.lang.short_code
            )
        except HttpResponseError as e:
            self.logger.warning(f"Failed to translate TTS prompt: {e}")
            pass
        return translation or initial

    @cached_property
    def logger(self) -> Logger:
        from helpers.logging import logger

        return logger


class PromptsModel(BaseModel):
    llm: LlmModel = LlmModel()  # Object is fully defined by default
    sounds: SoundModel = SoundModel()  # Object is fully defined by default
    tts: TtsModel = TtsModel()  # Object is fully defined by default
