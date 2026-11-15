"""Composition root for business capabilities. No transport or process lifecycle here."""

from dataclasses import dataclass

from buddy.access.service import AccessService
from buddy.brief.service import BriefService
from buddy.rag.index import default_index
from buddy.rag.qa import PlaybookService
from buddy.rag.search import PlaybookSearch, create_search
from buddy.team.service import TeamService


@dataclass
class Capabilities:
    access: AccessService
    team: TeamService
    playbook: PlaybookService
    brief: BriefService


def build_capabilities(llm=None, settings=None) -> Capabilities:
    access = AccessService()
    team = TeamService(access)
    search = create_search(settings) if settings is not None else PlaybookSearch(default_index())
    return Capabilities(access, team, PlaybookService(search, llm, team), BriefService(llm, settings))
