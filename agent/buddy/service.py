from buddy.demo.controller import DemoController
from buddy.executors.core import Context, Registry
from buddy.router.service import Router


class AgentService:
    def __init__(self, router: Router, *, demo_mode: bool):
        self.router = router
        self.executors = Registry()
        self.demo = DemoController(demo_mode)

    async def handle(self, text: str, context: Context):
        decision = await self.router.route(text, context=context)
        response = await self.executors.execute(decision.case, decision.params, context)
        return decision, response

    async def trigger(self, case: str, context: Context):
        return await self.executors.trigger(case, context)
