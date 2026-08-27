from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Keyword = Annotated[str, Field(min_length=2, max_length=60)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Topic(StrEnum):
    SPACE_SCIENCE = "space_science"
    EARTH_SCIENCE = "earth_science"
    SECURITY = "security"
    ENVIRONMENT = "environment"
    HEALTH = "health"
    WEATHER_SAFETY = "weather_safety"
    FINANCE_CURRENCY = "finance_currency"
    EMERGENCY_PREPAREDNESS = "emergency_preparedness"
    OTHER = "other"


class DocumentType(StrEnum):
    FACT_SHEET = "fact_sheet"
    GUIDE = "guide"
    REPORT = "report"
    POLICY = "policy"
    EDUCATIONAL_MATERIAL = "educational_material"
    FORM = "form"
    PRESENTATION = "presentation"
    OTHER = "other"


class Actionability(StrEnum):
    INFORMATIONAL = "informational"
    ADVISORY = "advisory"
    REQUIRED_ACTION = "required_action"


class EntityType(StrEnum):
    ORGANIZATION = "organization"
    PERSON = "person"
    LOCATION = "location"
    PRODUCT = "product"
    STANDARD = "standard"
    DATE = "date"


class Entity(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    type: EntityType


class Commitment(StrictModel):
    action: str = Field(min_length=1, max_length=300)
    owner: str | None = Field(default=None, max_length=160)
    deadline: str | None = Field(default=None, max_length=160)
    evidence: str = Field(min_length=1, max_length=500)


class DocumentAnalysis(StrictModel):
    topic: Topic
    document_type: DocumentType
    language: str = Field(pattern=r"^[a-z]{2}$")
    summary: str = Field(min_length=1, max_length=800)
    keywords: list[Keyword] = Field(min_length=3, max_length=8)
    actionability: Actionability
    entities: list[Entity] = Field(default_factory=list, max_length=20)
    commitments: list[Commitment] = Field(default_factory=list, max_length=20)


class AnalysisRun(StrictModel):
    schema_version: str = "document-analysis.v1"
    analysis: DocumentAnalysis
    route: str
    model: str
    prompt_version: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: float


def route_for(analysis):
    if analysis.actionability == Actionability.REQUIRED_ACTION:
        return "action_queue"
    if analysis.actionability == Actionability.ADVISORY and analysis.topic in {
        Topic.SECURITY, Topic.WEATHER_SAFETY, Topic.EMERGENCY_PREPAREDNESS
    }:
        return "review_queue"
    return "knowledge_library"
