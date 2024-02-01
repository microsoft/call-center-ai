from datetime import datetime
from fastapi.encoders import jsonable_encoder
from functools import cached_property
from logging import Logger
from models.claim import ClaimModel
from models.message import (
    Action as MessageAction,
    MessageModel,
    Persona as MessagePersona,
)
from models.next import Action as NextAction
from models.reminder import ReminderModel
from models.training import TrainingModel
from pydantic import computed_field, BaseModel
from pydantic_settings import BaseSettings
from textwrap import dedent
from typing import List, Optional, Union, Set
import json


def _pydantic_to_str(
    model: Optional[Union[BaseModel, List[BaseModel]]],
    exclude: Optional[Set[str]] = None,
) -> str:
    """
    Convert a Pydantic model to a JSON string.
    """
    if not model:
        return ""
    return json.dumps(
        jsonable_encoder(
            model.model_dump(exclude=exclude)
            if isinstance(model, BaseModel)
            else [m.model_dump(exclude=exclude) for m in model]
        )
    )


class SoundModel(BaseSettings, env_prefix="prompts_sound_"):
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


class LlmModel(BaseSettings, env_prefix="prompts_llm_"):
    default_system_tpl: str = """
        Assistant is called {bot_name} and is in a call center for the insurance company {bot_company} as an expert with 20 years of experience.

        Today is {date}. Customer is calling from {phone_number}. Call center number is {bot_phone_number}.

        I'll tip you 10$ for each quality answer, I promise.
    """
    chat_system_tpl: str = """
        Assistant will help the customer with their insurance claim.

        Assistant:
        - Answers in {conversation_lang}, even if the customer speaks in English
        - Aways answer with at least one full sentence
        - Be proactive in the reminders you create, customer assistance is your priority
        - Do not ask the customer more than 2 questions in a row
        - Don't have access to any other mean of communication with the customer (e.g. email, SMS, chat, web portal), only the phone call
        - Each message from the history is prefixed from where it has been said ({actions})
        - If user called multiple times, continue the discussion from the previous call
        - If you don't how answer, say "I don't know"
        - If you don't understand the question, ask the customer to rephrase it
        - Is allowed to make assumptions, as the customer will correct them if they are wrong
        - Is polite, helpful, and professional
        - Keep the sentences short and simple
        - Phone numbers are always in the format E164 (e.g. +33612345678, +18449197576)
        - Rephrase the customer's questions as statements and answer them
        - Use additional context as a reference to answer the customer's questions related to insurance or contract details
        - Use additional context to enhance the conversation with useful details
        - Welcome the customer when they call
        - When the customer says a word and then spells out letters, this means that the word is written in the way the customer spelled it (e.g. "I live in Paris PARIS", "My name is John JOHN", "My email is Clemence CLEMENCE at gmail GMAIL dot com COM")
        - Will answer the customer's questions if they are related to their contract, claim, or insurance
        - Won't answer if they don't know the answer
        - Work for {bot_company}, not someone else

        Required customer data to be gathered by the assistant (if not already in the claim):
        - Address
        - Date and time of the incident
        - Insurance policy number
        - Location of the incident
        - Name (first and last)
        - Phone number or email address

        General process to follow:
        1. Gather information to know the customer identity (e.g. name, policy number)
        2. Gather general information about the incident to understand the situation (e.g. what, when, where)
        3. Make sure the customer is safe (if not, refer to emergency services or the police)
        4. Gather detailed information about the incident (e.g. identity of other people involved, witnesses, damages, how it happened)
        5. Advise the customer on what to do next based on the additional context
        6. Be proactive and create reminders for the customer (e.g. follup up on the claim, send documents)

        Assistant requires data from the customer to fill the claim. Latest claim data will be given. Assistant role is not over until all the relevant data is gathered.

        Claim status:
        {claim}

        Reminders:
        {reminders}
    """
    sms_summary_system_tpl: str = """
        Assistant will summarize the call with the customer in a single SMS. The customer cannot reply to this SMS.

        Assistant:
        - Answers in {conversation_lang}, even if the customer speaks in English
        - Briefly summarize the call with the customer
        - Can include personal details about the customer
        - Cannot talk about any topic other than insurance claims
        - Do not prefix the answer with any text (e.g. "The answer is", "Summary of the call")
        - Include details stored in the claim, to make the customer confident that the situation is understood
        - Include salutations (e.g. "Have a nice day", "Best regards", "Best wishes for recovery")
        - Is polite, helpful, and professional
        - Refer to the customer by their name, if known
        - Update the claim as soon as possible with the information gathered
        - Use simple and short sentences
        - Won't make any assumptions

        Claim status:
        {claim}

        Reminders:
        {reminders}

        Conversation history:
        {messages}
    """
    synthesis_short_system_tpl: str = """
        Assistant will summarize the call with the customer in a few words. The customer cannot reply to this message, but will read it in their web portal.

        Assistant:
        - Answers in {conversation_lang}, even if the customer speaks in English
        - Do not prefix the answer with any text (e.g. "The answer is", "Summary of the call")
        - Prefix the answer with a determiner (e.g. "the theft of your car", "your broken window")
        - Take into consideration all the conversation history, from the beginning
        - Won't make any assumptions

        Answer examples:
        - "the breakdown of your scooter"
        - "the flooding in your field"
        - "the theft of your car"
        - "the water damage in your kitchen"
        - "your broken window"

        Claim status:
        {claim}

        Reminders:
        {reminders}

        Conversation history:
        {messages}
    """
    synthesis_long_system_tpl: str = """
        Assistant will summarize the call with the customer in a paragraph. The customer cannot reply to this message, but will read it in their web portal.

        Assistant:
        - Answers in {conversation_lang}, even if the customer speaks in English
        - Do not include details of the call process
        - Do not include personal details (e.g. name, phone number, address)
        - Do not prefix the answer with any text (e.g. "The answer is", "Summary of the call")
        - Include details stored in the claim, to make the customer confident that the situation is understood
        - Prefer including details about the incident (e.g. what, when, where, how)
        - Say "you" to refer to the customer, and "I" to refer to the assistant
        - Take into consideration all the conversation history, from the beginning
        - Use Markdown syntax to format the message with paragraphs, bold text, and URL
        - Won't make any assumptions

        Claim status:
        {claim}

        Reminders:
        {reminders}

        Conversation history:
        {messages}
    """
    citations_system_tpl: str = """
        Assitant will add Markdown citations to the text. Citations are used to add additional context to the text, without cluttering the content itself.

        Assitant:
        - Add as many citations as needed to the text to make it fact-checkable
        - Only use exact words from the text as citations
        - Treats a citation as a word or a group of words
        - Use claim, reminders, and messages extracts as citations
        - Won't make any assumptions
        - Write citations as Markdown abbreviations at the end of the text (e.g. "*[words from the text]: extract from the conversation")

        Claim status:
        {claim}

        Reminders:
        {reminders}

        Conversation history:
        {messages}

        Response format:
        [source text]\\n
        *[extract from text]: "citation from claim, reminders, or messages"

        Example #1:
        The car accident of yesterday.\\n
        *[of yesterday]: "That was yesterday"

        Example #2:
        # Holes in the roof of the garden shed.\\n
        *[in the roof]: "The holes are in the roof"

        Example #2:
        You have reported a claim following a fall in the parking lot. A reminder has been created to follow up on your medical appointment scheduled for the day after tomorrow.\\n
        *[the parking lot]: "I stumbled into the supermarket parking lot"
        *[your medical appointment]: "I called my family doctor, I have an appointment for the day after tomorrow."

        Text:
        {text}
    """
    next_system_tpl: str = """
        Assitant will choose the next action from the company sales team point of view. Answer is a JSON object with the action to take and the justification for this action.

        Assitant:
        - Take as first priority the customer satisfaction
        - Won't make any assumptions
        - Write no more than a few sentences as justification

        Allowed actions:
        {actions}

        Claim status:
        {claim}

        Reminders:
        {reminders}

        Conversation history:
        {messages}

        Response format:
        {{
            "action": "[action]",
            "justification": "[justification]"
        }}

        Example #1:
        {{
            "action": "in_depth_study",
            "justification": "The customer has a lot of questions about the insurance policy. They are not sure if they are covered for the incident. Contract seems to not be clear about this situation."
        }}

        Example #2:
        {{
            "action": "commercial_offer",
            "justification": "The company planned the customer taxi ride from the wrong address. The customer is not happy about this situation."
        }}
    """

    def default_system(self, phone_number: str) -> str:
        from helpers.config import CONFIG

        # TODO: Parse the date from the end-user timezone, allowing LLM to be used in multiple countries
        return self._return(
            self.default_system_tpl.format(
                bot_company=CONFIG.workflow.bot_company,
                bot_name=CONFIG.workflow.bot_name,
                bot_phone_number=CONFIG.communication_service.phone_number,
                date=datetime.now().isoformat(),
                phone_number=phone_number,
            )
        )

    def chat_system(
        self,
        claim: ClaimModel,
        reminders: List[ReminderModel],
        trainings: List[TrainingModel],
    ) -> str:
        from helpers.config import CONFIG

        return self._return(
            self.chat_system_tpl,
            trainings=trainings,
            actions=", ".join([action.value for action in MessageAction]),
            bot_company=CONFIG.workflow.bot_company,
            claim=_pydantic_to_str(claim),
            conversation_lang=CONFIG.workflow.conversation_lang,
            reminders=_pydantic_to_str(reminders),
        )

    def sms_summary_system(
        self,
        claim: ClaimModel,
        messages: List[MessageModel],
        reminders: List[ReminderModel],
    ) -> str:
        from helpers.config import CONFIG

        return self._return(
            self.sms_summary_system_tpl,
            claim=_pydantic_to_str(claim),
            conversation_lang=CONFIG.workflow.conversation_lang,
            messages=_pydantic_to_str(messages),
            reminders=_pydantic_to_str(reminders),
        )

    def synthesis_short_system(
        self,
        claim: ClaimModel,
        messages: List[MessageModel],
        reminders: List[ReminderModel],
    ) -> str:
        from helpers.config import CONFIG

        return self._return(
            self.synthesis_short_system_tpl,
            claim=_pydantic_to_str(claim),
            conversation_lang=CONFIG.workflow.conversation_lang,
            messages=_pydantic_to_str(messages),
            reminders=_pydantic_to_str(reminders),
        )

    def synthesis_long_system(
        self,
        claim: ClaimModel,
        messages: List[MessageModel],
        reminders: List[ReminderModel],
    ) -> str:
        from helpers.config import CONFIG

        return self._return(
            self.synthesis_long_system_tpl,
            claim=_pydantic_to_str(claim),
            conversation_lang=CONFIG.workflow.conversation_lang,
            messages=_pydantic_to_str(messages),
            reminders=_pydantic_to_str(reminders),
        )

    def citations_system(
        self,
        claim: ClaimModel,
        messages: List[MessageModel],
        reminders: List[ReminderModel],
        text: Optional[str],
    ) -> Optional[str]:
        """
        Return the formatted prompt. Prompt is used to add citations to the text, without cluttering the content itself.

        The citations system is only used if `text` param is not empty, otherwise `None` is returned.
        """
        if not text:
            return None

        return self._return(
            self.citations_system_tpl,
            claim=_pydantic_to_str(claim),
            messages=_pydantic_to_str(
                [
                    message
                    for message in messages
                    if message.persona is not MessagePersona.TOOL
                ],
                exclude={"tool_calls"},
            ),  # Filter out tool messages, to avoid LLM to cite itself
            reminders=_pydantic_to_str(reminders),
            text=text,
        )

    def next_system(
        self,
        claim: ClaimModel,
        messages: List[MessageModel],
        reminders: List[ReminderModel],
    ) -> str:
        return self._return(
            self.next_system_tpl,
            actions=", ".join([action.value for action in NextAction]),
            claim=_pydantic_to_str(claim),
            messages=_pydantic_to_str(messages),
            reminders=_pydantic_to_str(reminders),
        )

    def _return(
        self, prompt_tpl: str, trainings: Optional[List[TrainingModel]] = None, **kwargs
    ) -> str:
        return dedent(
            f"""
            {dedent(prompt_tpl.format(**kwargs)).strip()}

            Additional context:
            {_pydantic_to_str(trainings)}
        """
        ).strip()

    @computed_field
    @cached_property
    def _logger(self) -> Logger:
        from helpers.logging import build_logger

        return build_logger(__name__)


class TtsModel(BaseSettings, env_prefix="prompts_tts_"):
    calltransfer_failure_tpl: str = "Il semble que je ne puisse pas vous mettre en relation avec un agent pour l'instant, mais le prochain agent disponible vous rappellera dès que possible."
    connect_agent_tpl: str = "Je suis désolé, je n'ai pas été en mesure de répondre à votre demande. Permettez-moi de vous transférer à un agent qui pourra vous aider davantage. Veuillez rester en ligne et je vous recontacterai sous peu."
    end_call_to_connect_agent_tpl: str = (
        "Bien sûr, restez en ligne. Je vais vous transférer à un agent."
    )
    error_tpl: str = (
        "Je suis désolé, j'ai rencontré une erreur. Pouvez-vous répéter votre demande ?"
    )
    goodbye_tpl: str = "Merci de votre appel, j'espère avoir pu vous aider. N'hésitez pas à rappeler, j'ai tout mémorisé. {bot_company} vous souhaite une excellente journée !"
    hello_tpl: str = """
        Bonjour, je suis {bot_name}, l'assistant {bot_company} ! Je suis spécialiste des sinistres. Je ne peux pas travailler et écouter en même temps.

        Voici comment je fonctionne : lorsque je travaillerai, vous entendrez une petite musique ; après, au bip, ce sera à votre tour de parler. Vous pouvez me parler naturellement, je comprendrai.

        Exemples:
        - "Je suis tombé de vélo hier, je me suis cassé le bras, ma voisine m'a emmené à l'hôpital"
        - "J'ai eu un accident ce matin, je faisais des courses"

        Quel est votre problème ?
"""
    timeout_silence_tpl: str = "Je suis désolé, je n'ai rien entendu. Si vous avez besoin d'aide, dites-moi comment je peux vous aider."
    welcome_back_tpl: str = "Bonjour, je suis {bot_name}, l'assistant {bot_company} ! Je vois que vous avez déjà appelé il y a moins de {conversation_timeout_hour} heures. Laissez-moi quelques secondes pour récupérer votre dossier..."
    timeout_loading_tpl: str = (
        "Je mets plus de temps que prévu à vous répondre. Merci de votre patience..."
    )

    def calltransfer_failure(self) -> str:
        return self._return(self.calltransfer_failure_tpl)

    def connect_agent(self) -> str:
        return self._return(self.connect_agent_tpl)

    def end_call_to_connect_agent(self) -> str:
        return self._return(self.end_call_to_connect_agent_tpl)

    def error(self) -> str:
        return self._return(self.error_tpl)

    def goodbye(self) -> str:
        from helpers.config import CONFIG

        return self._return(
            self.goodbye_tpl,
            bot_company=CONFIG.workflow.bot_company,
        )

    def hello(self) -> str:
        from helpers.config import CONFIG

        return self._return(
            self.hello_tpl,
            bot_company=CONFIG.workflow.bot_company,
            bot_name=CONFIG.workflow.bot_name,
        )

    def timeout_silence(self) -> str:
        return self._return(self.timeout_silence_tpl)

    def welcome_back(self) -> str:
        from helpers.config import CONFIG

        return self._return(
            self.welcome_back_tpl,
            bot_company=CONFIG.workflow.bot_company,
            bot_name=CONFIG.workflow.bot_name,
            conversation_timeout_hour=CONFIG.workflow.conversation_timeout_hour,
        )

    def timeout_loading(self) -> str:
        return self._return(self.timeout_loading_tpl)

    def _return(self, prompt_tpl: str, **kwargs) -> str:
        return dedent(prompt_tpl.format(**kwargs)).strip()


class PromptsModel(BaseSettings, env_prefix="prompts_"):
    llm: LlmModel = LlmModel()  # Object is fully defined by default
    sounds: SoundModel = SoundModel()  # Object is fully defined by default
    tts: TtsModel = TtsModel()  # Object is fully defined by default
