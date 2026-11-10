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


class TeamCard(DataModel):
    synthetic: Literal[True]
    team_id: str
    name: str
    notice: str = Field(min_length=1)
    source_url: str
    people: list[Person] = Field(min_length=1)
    routing: list[Routing] = Field(min_length=1)
    do_not_disturb: list[ContactRule]
    tools: list[str] = Field(min_length=1)
    links: dict[str, str]
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
        for value in self.links.values():
            object_url(value)
        return self


class AccessObject(DataModel):
    object_id: str
    instrument: Instrument
    external_id: str
    title: str
    url: str
    request_url: str
    owner_id: str
    instructions: str
    allowed: bool
    reason: str = ""
    _urls = field_validator("url", "request_url")(object_url)

    @model_validator(mode="after")
    def denial_reason(self):
        if not self.allowed and not self.reason:
            raise ValueError("Forbidden objects need an explanation")
        return self


class AccessCatalog(DataModel):
    synthetic: Literal[True]
    notice: str
    team_id: str
    role: Literal["backend-intern"]
    instruments: list[Instrument]
    objects: list[AccessObject]
    forbidden_objects: list[AccessObject]

    @model_validator(mode="after")
    def complete(self):
        if len(self.instruments) != 5 or set(self.instruments) != INSTRUMENTS:
            raise ValueError("Exactly five instruments required")
        if len(self.objects) != 5 or {item.instrument for item in self.objects} != INSTRUMENTS:
            raise ValueError("One base object per instrument required")
        all_objects = self.objects + self.forbidden_objects
        if len({item.object_id for item in all_objects}) != len(all_objects):
            raise ValueError("Duplicate access objects")
        if any(item.allowed for item in self.forbidden_objects) or any(
            not item.allowed for item in self.objects
        ):
            raise ValueError("Base and forbidden objects must be separated")
        return self


def validate_seed(card: TeamCard, catalog: AccessCatalog, users: list[dict], scenario: dict) -> None:
    team_users = {user["user_id"] for user in users if user["team_id"] == card.team_id}
    people = {person.user_id for person in card.people}
    if card.team_id != catalog.team_id or card.team_id != scenario["team_id"] or not people <= team_users:
        raise ValueError("Seed belongs to another team")
    if any(item.owner_id not in people for item in catalog.objects + catalog.forbidden_objects):
        raise ValueError("Unknown catalog owner")
    expected = {
        "github": scenario["resources"]["github"]["repo"],
        "jira": scenario["resources"]["jira"]["project"],
        "miro": scenario["resources"]["miro"]["board_id"],
        "google_calendar": scenario["resources"]["calendar"]["calendar_id"],
        "telegram": str(scenario["resources"]["telegram"]["chat_id"]),
    }
    if any(item.external_id != expected[item.instrument] for item in catalog.objects):
        raise ValueError("Access object differs from demo scenario")
