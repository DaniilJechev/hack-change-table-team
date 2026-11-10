"""Synthetic seed contracts shared by validators and future backend integration."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Instrument = Literal["github", "jira", "miro", "google_calendar", "telegram"]
INSTRUMENTS = {"github", "jira", "miro", "google_calendar", "telegram"}


def object_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "example.invalid"
        or parsed.username
        or parsed.password
        or not parsed.path.strip("/")
    ):
        raise ValueError("Synthetic source must reference an example.invalid object")
    return value


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Person(DataModel):
    user_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    areas: list[str] = Field(min_length=1)
    first_contact: bool = False
    contact_url: str
    _url = field_validator("contact_url")(object_url)


class Routing(DataModel):
    situation: str
    go_to: str
    owner_id: str
    wait_minutes: int = Field(ge=0, le=1440)
    then_id: str
    source_url: str
    _url = field_validator("source_url")(object_url)


class ContactRule(DataModel):
    person_id: str
    direct_allowed: bool
    reason: str
    via_id: str


class Link(DataModel):
    service: str = Field(min_length=1)
    url: str
    _url = field_validator("url")(object_url)


class Stage(DataModel):
    epic_key: str = Field(min_length=1)
    source_url: str
    _url = field_validator("source_url")(object_url)


class TeamCard(DataModel):
    synthetic: Literal[True]
    team_id: str
    name: str
    notice: str = Field(min_length=1)
    source_url: str
    stage: Stage
    people: list[Person] = Field(min_length=1)
    routing: list[Routing] = Field(min_length=1)
    do_not_disturb: list[ContactRule]
    tools: list[str] = Field(min_length=1)
    links: list[Link] = Field(min_length=1)
    faq: list[dict]
    _url = field_validator("source_url")(object_url)

    @model_validator(mode="after")
    def references(self):
        ids = [person.user_id for person in self.people]
        if len(ids) != len(set(ids)) or sum(person.first_contact for person in self.people) != 1:
            raise ValueError("Unique people and exactly one first contact required")
        for route in self.routing:
            if route.owner_id not in ids or route.then_id not in ids:
                raise ValueError("Unknown route owner")
        for rule in self.do_not_disturb:
            if rule.person_id not in ids or rule.via_id not in ids:
                raise ValueError("Unknown contact rule person")
        services = [link.service for link in self.links]
        if len(services) != len(set(services)):
            raise ValueError("Duplicate link services")
        return self


Role = Literal["intern", "mentor", "teamlead"]


class AccessObject(DataModel):
    """Backend catalog object (backend/internal/store CatalogObject)."""

    id: str = Field(min_length=1)
    instrument: Instrument
    name: str = Field(min_length=1)
    url: str
    request_url: str = ""
    owner_id: str
    roles: list[Role] = Field(min_length=1)
    forbidden: bool = False
    reason: str = ""
    _url = field_validator("url")(object_url)

    @model_validator(mode="after")
    def denial_reason(self):
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("Duplicate roles")
        if self.forbidden:
            if not self.reason:
                raise ValueError("Forbidden objects need an explanation")
        else:
            object_url(self.request_url)
        return self


class AccessCatalog(DataModel):
    synthetic: Literal[True]
    notice: str
    team_id: str
    instruments: list[Instrument]
    objects: list[AccessObject]

    @property
    def allowed(self) -> list[AccessObject]:
        return [item for item in self.objects if not item.forbidden]

    @property
    def forbidden(self) -> list[AccessObject]:
        return [item for item in self.objects if item.forbidden]

    @model_validator(mode="after")
    def complete(self):
        if len(self.instruments) != 5 or set(self.instruments) != INSTRUMENTS:
            raise ValueError("Exactly five instruments required")
        allowed = self.allowed
        if len(allowed) != 5 or {item.instrument for item in allowed} != INSTRUMENTS:
            raise ValueError("One base object per instrument required")
        if len({item.id for item in self.objects}) != len(self.objects):
            raise ValueError("Duplicate access objects")
        if any("intern" not in item.roles for item in self.objects):
            raise ValueError("Every object must be described for the intern role")
        return self


def access_urls(scenario: dict) -> dict[str, str]:
    urls = {
        name: scenario["resources"]["calendar" if name == "google_calendar" else name]["url"]
        for name in INSTRUMENTS
    }
    urls["github"] = "https://example.invalid/github/" + scenario["resources"]["github"]["repo"]
    urls["jira"] = "https://example.invalid/jira/projects/" + scenario["resources"]["jira"]["project"]
    return urls


def validate_seed(card: TeamCard, catalog: AccessCatalog, users: list[dict], scenario: dict) -> None:
    team_users = {user["user_id"] for user in users if user["team_id"] == card.team_id}
    people = {person.user_id for person in card.people}
    if card.team_id != catalog.team_id or card.team_id != scenario["team_id"] or not people <= team_users:
        raise ValueError("Seed belongs to another team")
    if any(item.owner_id not in people for item in catalog.objects):
        raise ValueError("Unknown catalog owner")
    expected = access_urls(scenario)
    if any(item.url != expected[item.instrument] for item in catalog.allowed):
        raise ValueError("Access object differs from demo scenario")
