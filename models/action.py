from enum import Enum
from pydantic import BaseModel


class Style(str, Enum):
    """
    Voice styles the Azure AI Speech Service supports.

    Doc:
    - Speaking styles: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice#use-speaking-styles-and-roles
    - Support by language: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts#voice-styles-and-roles
    """

    # ADVERTISEMENT_UPBEAT = "advertisement_upbeat"
    # AFFECTIONATE = "affectionate"
    # ANGRY = "angry"
    # ASSISTANT = "assistant"
    # CALM = "calm"
    # CHAT = "chat"
    CHEERFUL = "cheerful"
    # CUSTOMERSERVICE = "customerservice"
    # DEPRESSED = "depressed"
    # DISGRUNTLED = "disgruntled"
    # DOCUMENTARY_NARRATION = "documentary-narration"
    # EMBARRASSED = "embarrassed"
    # EMPATHETIC = "empathetic"
    # ENVIOUS = "envious"
    # EXCITED = "excited"
    # FEARFUL = "fearful"
    # FRIENDLY = "friendly"
    # GENTLE = "gentle"
    # HOPEFUL = "hopeful"
    # LYRICAL = "lyrical"
    # NARRATION_PROFESSIONAL = "narration-professional"
    # NARRATION_RELAXED = "narration-relaxed"
    # NEWSCAST = "newscast"
    # NEWSCAST_CASUAL = "newscast-casual"
    # NEWSCAST_FORMAL = "newscast-formal"
    # POETRY_READING = "poetry-reading"
    SAD = "sad"
    # SERIOUS = "serious"
    # SHOUTING = "shouting"
    # SPORTS_COMMENTARY = "sports_commentary"
    # SPORTS_COMMENTARY_EXCITED = "sports_commentary_excited"
    # TERRIFIED = "terrified"
    # UNFRIENDLY = "unfriendly"
    # WHISPERING = "whispering"


class Indent(str, Enum):
    ANSWER_WITH_STYLE = "answer_with_style"
    CONTINUE = "continue"
    END_CALL = "end_call"
    NEW_CLAIM = "new_claim"
    NEW_OR_UPDATED_REMINDER = "new_or_updated_reminder"
    TALK_TO_HUMAN = "talk_to_human"
    UPDATED_CLAIM = "updated_claim"


class ActionModel(BaseModel):
    content: str
    intent: Indent
    style: Style
