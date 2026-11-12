"""H2 (stage 3): eight team questions, «не нашёл», escalation, answers and FAQ."""

from buddy.access.service import AccessService
from buddy.demo.controller import DemoController
from buddy.executors.context import Context
from buddy.executors.core import Registry, format_response
from buddy.team.service import TeamService


def demo(step: int = 0):
    controller = DemoController(True)
    controller.command(1)
    for _ in range(step):
        controller.command(1, "next")
    return controller.runtime(1), controller.context(1)


async def ask(case: str, text: str = "", params: dict | None = None, step: int = 0):
    runtime, context = demo(step)
    team = TeamService(AccessService())
    return await team.answer(case, params or {}, text, context, runtime), team, runtime, context


async def test_fr_h2_03_q1_responsible_area_with_source():
    response, *_ = await ask("team_q1", "Кто у нас по Kafka?", {"area": "Kafka"})
    assert "Пётр" in response.result
    assert response.links == ["https://example.invalid/team/demo-orders/people/demo-petr"]
    assert response.actions[0].data.startswith("rate:bot:b-")
    assert response.actions[0].data.endswith(":1") and response.actions[1].data.endswith(":0")
    listing, *_ = await ask("team_q1")
    assert "Кто за что отвечает" in listing.result and "Олег" in listing.result


async def test_fr_h2_05_q1_unknown_area_not_found_with_escalation_buttons():
    response, team, _, context = await ask("team_q1", "Кто отвечает за биллинг?", {"area": "биллинг"})
    assert response.result.startswith("В карточке и FAQ команды ответа нет.")
    assert [action.data for action in response.actions] == ["esc:resp", "esc:mentor", "esc:defer"]
    assert team.pending[AccessService.key(context)]["responsible"] == "demo-oleg"  # first contact


async def test_fr_h2_03_q2_route_and_fr_h2_04_first_contact():
    route, *_ = await ask("team_q2", "Упал staging, кому писать?", {"situation": "упал staging"})
    assert "#orders-duty" in route.result and "30 мин" in route.result and "Олег" in route.result
    assert route.links == ["https://example.invalid/team/demo-orders/routing/staging"]
    other, *_ = await ask("team_q2", "Сломался принтер в офисе")
    assert "первый контакт — Олег" in other.result


async def test_fr_h2_03_q2_route_matches_topic_not_generic_words():
    # Route «вопрос по Kafka»: «вопрос» is generic, the topic is Kafka.
    text = "к кому обращаться если у меня проблемы с Kafka?"
    route, *_ = await ask("team_q2", text, {"situation": "проблемы с kafka"})
    assert "сначала Пётр" in route.result and "60 мин" in route.result
    assert route.links == ["https://example.invalid/team/demo-orders/routing/kafka"]


async def test_fr_h2_03_q2_area_owner_before_first_contact():
    owner, *_ = await ask("team_q2", "К кому идти по интеграциям?", {"situation": "интеграции"})
    assert "Пётр (Инженер интеграций)" in owner.result
    assert "через Олег" in owner.result  # Пётр: direct_allowed false, via Олег
    assert owner.links == ["https://example.invalid/team/demo-orders/people/demo-petr"]


async def test_fr_h2_03_q3_mentor_lead_first_contact():
    response, *_ = await ask("team_q3", "Кто мой наставник?")
    assert "наставник — Анна" in response.result and "Тимлид — Олег" in response.result
    assert len(response.links) == 2


async def test_fr_h2_03_q4_direct_rules():
    petr, *_ = await ask("team_q4", "Можно написать Петру напрямую?", {"person": "Пётр"})
    assert "прямое обращение ограничено" in petr.result and "согласования: Олег" in petr.result
    anna, *_ = await ask("team_q4", "", {"person": "Анна"})
    assert "Анна: прямое обращение разрешено" in anna.result
    unknown, *_ = await ask("team_q4", "", {"person": "Зинаида"})
    assert "Не знаю такого человека" in unknown.result
    empty, *_ = await ask("team_q4")
    assert "Кому хотите написать" in empty.result


async def test_fr_h2_03_q5_q6_project_stage_from_jira():
    stage, *_ = await ask("team_q5", "На каком этапе проект?")
    assert "эпиком ORD-100 «Фильтры в новом API»: готово 6 из 11" in stage.result
    assert stage.links == ["https://example.invalid/jira/browse/ORD-100"]
    task, *_ = await ask("team_q6", "Зачем нужна ORD-118?", {"task": "ORD-118"})
    assert task.result.startswith("ORD-118 «Учебная проверка заказов» — часть эпика ORD-100")
    assert task.links == [
        "https://example.invalid/jira/browse/ORD-118",
        "https://example.invalid/jira/browse/ORD-100",
    ]
    empty, *_ = await ask("team_q6")
    assert "ключ задачи" in empty.result
    missing, *_ = await ask("team_q6", "", {"task": "ORD-999"})
    assert "Не удалось получить ORD-999" in missing.result


async def test_fr_h2_03_q7_stack_and_q8_links_with_access():
    stack, *_ = await ask("team_q7", "На чём пишем?")
    assert "Go, PostgreSQL, Kafka" in stack.result
    board, *_ = await ask("team_q8", "Где доска планирования?", {"object": "доска"})
    assert "Miro" in board.result and "Доступа пока нет" in board.result
    assert board.actions[0].label == "Где запросить доступ"
    assert board.actions[1].data == "mark:demo-orders-miro"
    granted, *_ = await ask("team_q8", "", {"object": "доска"}, step=1)
    assert "Доступ: ✅ есть" in granted.result
    listing, *_ = await ask("team_q8")
    assert "Ссылки команды" in listing.result and len(listing.links) == 5


async def test_fr_h2_05_fr_h2_06_why_question_escalation_answer_and_faq():
    response, team, runtime, context = await ask("team_q7", "Почему у нас Kafka, а не RabbitMQ?")
    assert response.result.startswith("В карточке и FAQ команды ответа нет.")
    assert "Пётр" in response.result and response.actions[0].label == "Спросить: Пётр"
    reply, notes = await team.escalate("resp", context, runtime)
    assert "Передал вопрос: Пётр" in reply.result
    assert reply.actions[0].label.endswith("(демо)")
    recipient, note = notes[0]
    assert recipient == "demo-petr" and "Почему у нас Kafka" in note.result
    question_id = note.actions[0].data.removeprefix("ans:")
    messages = await team.deliver_answer(
        "demo-petr", question_id, "Нужна шина событий между сервисами.", runtime
    )
    assert messages[0][0] == "demo-daniil" and "Нужна шина событий" in messages[0][1].result
    assert messages[1][1].actions[0].data == "faq:" + question_id
    assert "Сохранено" in (await team.publish("demo-petr", question_id, runtime)).result
    assert "только автор" in (await team.publish("demo-daniil", question_id, runtime)).result
    again = await team.answer("team_q1", {}, "Зачем нам Kafka?", context, runtime)
    assert again.result.startswith("Ответ из FAQ команды") and "шина событий" in again.result
    rated = await team.rate("demo-daniil", question_id, True, runtime)
    assert "Спасибо" in rated.result and (await runtime.data.question("demo-daniil", question_id))["helpful"]
    await team.rate(
        "demo-daniil", again.actions[1].data.removeprefix("rate:").rsplit(":", 1)[0], False, runtime
    )
    assert team.metrics == {"helpful": 1, "not_helpful": 1}


async def test_fr_h2_05_escalation_variants_and_errors():
    _, team, runtime, context = await ask("team_q1", "Кто отвечает за биллинг?", {"area": "биллинг"})
    reply, notes = await team.escalate("mentor", context, runtime)
    assert notes[0][0] == "demo-anna"
    assert "Нет вопроса" in (await team.escalate("mentor", context, runtime))[0].result
    await team.answer("team_q1", {"area": "биллинг"}, "Кто отвечает за биллинг?", context, runtime)
    deferred, notes = await team.escalate("defer", context, runtime)
    assert "1:1" in deferred.result and notes == []
    wrong = await team.deliver_answer("demo-oleg", "q-unknown", "ответ", runtime)
    assert wrong[0][0] == "demo-oleg" and "недоступ" in wrong[0][1].result
    question = next(iter(runtime.data.questions))
    denied = await team.deliver_answer("demo-oleg", question, "ответ", runtime)
    assert "недоступ" in denied[0][1].result  # Oleg is neither author nor addressee


async def test_fr_h2_07_other_team_and_unavailable_card():
    runtime, _ = demo()
    team = TeamService(AccessService())
    kirill = Context("demo-kirill", "demo-payments", "intern", True, 1)
    response = await team.answer("team_q1", {}, "Кто за что отвечает?", kirill, runtime)
    assert "не загружена" in response.result
    runtime.data.users.pop("demo-daniil")
    daniil = Context("demo-daniil", "demo-orders", "intern", True, 1)
    assert "недоступны" in (await team.answer("team_q1", {}, "", daniil, runtime)).result


async def test_rt_06_registry_routes_cases_and_formats_links():
    runtime, context = demo()
    registry = Registry()
    response = await registry.execute("team_q5", {}, context, runtime, "Что делает команда?")
    text = format_response(response)
    assert "ORD-100" in response.result and text.endswith("https://example.invalid/jira/browse/ORD-100")
    status = await registry.execute("access_status", {}, context, runtime)
    assert "Ваши доступы" in status.result
    task = await registry.execute("access_task_analysis", {"task": "ORD-118"}, context, runtime)
    assert "ORD-118" in task.result
    events = await registry.trigger("access_events", context, runtime)
    assert events.result == "Изменений в доступах нет."
    mentor = Context("demo-anna", "demo-orders", "mentor", True, 1)
    assert "Доступы стажёров" in (await registry.execute("interns_status", {}, mentor, runtime)).result
    assert "Могу помочь" in (await registry.execute("help", {}, context, runtime)).result


async def test_fr_h2_04_not_found_names_first_contact_with_link():
    """B-03 (live run 12.11): no responsible for the topic — first contact, not «knows best»."""
    response, *_ = await ask("team_q1", "Кто у нас занимается бэкендом?", {"area": "бэкенд"})
    assert "Ответственного по теме в карточке нет. Первый контакт в команде — Олег" in response.result
    assert "лучше всего знает" not in response.result
    assert response.links == ["https://example.invalid/team/demo-orders/people/demo-oleg"]
    assert [action.data for action in response.actions] == ["esc:resp", "esc:mentor", "esc:defer"]


async def test_fr_h2_04_not_found_names_responsible_with_link():
    response, *_ = await ask("team_q7", "Почему у нас Kafka, а не RabbitMQ?")
    assert "По теме лучше всего знает Пётр (Инженер интеграций)." in response.result
    assert response.links == ["https://example.invalid/team/demo-orders/people/demo-petr"]
    assert not any((action.data or "").startswith("rate:bot:") for action in response.actions)
