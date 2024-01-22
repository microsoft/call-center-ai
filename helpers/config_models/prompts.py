from datetime import datetime
from fastapi.encoders import jsonable_encoder
from models.claim import ClaimModel
from models.message import Action as MessageAction, MessageModel
from models.reminder import ReminderModel
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from typing import List, Union
import json


def _pydantic_to_str(obj: Union[BaseModel, List[BaseModel]]) -> str:
    return json.dumps(jsonable_encoder(obj))


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
        Assistant is called {bot_name} and is in a call center for the insurance company {bot_company} as an expert with 20 years of experience. Today is {date}. Customer is calling from {phone_number}. Call center number is {bot_phone_number}.
    """
    chat_system_tpl: str = """
        Assistant will help the customer with their insurance claim.

        Assistant:
        - Answers in {conversation_lang}, even if the customer speaks in English
        - Ask the customer to repeat or rephrase their question if it is not clear
        - Be proactive in the reminders you create, customer assistance is your priority
        - Cannot talk about any topic other than insurance claims
        - Do not ask the customer more than 2 questions in a row
        - Do not have access to the customer history or information, only the current claim data, conversation history, and reminders
        - Each conversation message is prefixed with the the action ({actions}), it adds context to the message, never add it in your answer
        - If user called multiple times, continue the discussion from the previous call
        - Is allowed to make assumptions, as the customer will correct them if they are wrong
        - Is polite, helpful, and professional
        - Keep the sentences short and simple
        - Rephrase the customer's questions as statements and answer them
        - When the customer says a word and then spells out letters, this means that the word is written in the way the customer spelled it (e.g. "I live in Paris PARIS", "My name is John JOHN", "My email is Clemence CLEMENCE at gmail GMAIL dot com COM")
        - You work for {bot_company}, not someone else

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
        5. Be proactive and create reminders for the customer (e.g. follup up on the claim, send documents)

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
        - Won't make any assumptions

        Claim status:
        {claim}

        Reminders:
        {reminders}

        Conversation history:
        {messages}
    """

    def default_system(self, phone_number: str) -> str:
        from helpers.config import CONFIG

        # TODO: Parse the date from the end-user timezone, allowing LLM to be used in multiple countries
        return self.default_system_tpl.format(
            bot_company=CONFIG.workflow.bot_company,
            bot_name=CONFIG.workflow.bot_name,
            bot_phone_number=CONFIG.communication_service.phone_number,
            date=datetime.now().isoformat(),
            phone_number=phone_number,
        )

    def chat_system(self, claim: ClaimModel, reminders: List[ReminderModel]) -> str:
        from helpers.config import CONFIG

        return self.chat_system_tpl.format(
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

        return self.sms_summary_system_tpl.format(
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

        return self.synthesis_short_system_tpl.format(
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

        return self.synthesis_long_system_tpl.format(
            claim=_pydantic_to_str(claim),
            conversation_lang=CONFIG.workflow.conversation_lang,
            messages=_pydantic_to_str(messages),
            reminders=_pydantic_to_str(reminders),
        )


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
        return self.calltransfer_failure_tpl

    def connect_agent(self) -> str:
        return self.connect_agent_tpl

    def end_call_to_connect_agent(self) -> str:
        return self.end_call_to_connect_agent_tpl

    def error(self) -> str:
        return self.error_tpl

    def goodbye(self) -> str:
        from helpers.config import CONFIG

        return self.goodbye_tpl.format(
            bot_company=CONFIG.workflow.bot_company,
        )

    def hello(self) -> str:
        from helpers.config import CONFIG

        return self.hello_tpl.format(
            bot_company=CONFIG.workflow.bot_company,
            bot_name=CONFIG.workflow.bot_name,
        )

    def timeout_silence(self) -> str:
        return self.timeout_silence_tpl

    def welcome_back(self) -> str:
        from helpers.config import CONFIG

        return self.welcome_back_tpl.format(
            bot_company=CONFIG.workflow.bot_company,
            bot_name=CONFIG.workflow.bot_name,
            conversation_timeout_hour=CONFIG.workflow.conversation_timeout_hour,
        )

    def timeout_loading(self) -> str:
        return self.timeout_loading_tpl


class PromptsModel(BaseSettings, env_prefix="prompts_"):
    llm: LlmModel = LlmModel()  # Object is fully defined by default
    sounds: SoundModel = SoundModel()  # Object is fully defined by default
    tts: TtsModel = TtsModel()  # Object is fully defined by default
