"""Chromium acceptance against a real local HTTP server and a scripted model."""
import socket
import threading
import time

import pytest
import uvicorn
from langchain_core.messages import AIMessage
from playwright.sync_api import expect

from roco_pvp_agent.config import Settings
from roco_pvp_agent.results import AssistantResult
from ui.server import create_chat_app
from fakes import tool_call

pytestmark = pytest.mark.browser


class BrowserLLM:
    def __init__(self):
        self.invocations = 0
        self.slow_entered = threading.Event()
        self.release = threading.Event()
        self.pause_after_first = False
        self.second_entered = threading.Event()

    def invoke(self, messages, **kwargs):
        self.invocations += 1
        if getattr(self, 'team_payload', None):
            return AIMessage(content='', tool_calls=[tool_call('submit_team_advice', {'payload': self.team_payload})])
        last = max(i for i, m in enumerate(messages) if m.type == "human")
        message = messages[last].content
        count = sum(m.type == "tool" for m in messages[last + 1:])
        if "慢" in message and count == 0:
            self.slow_entered.set(); self.release.wait(5)
        if self.pause_after_first and count == 2:
            self.second_entered.set(); self.release.wait(5)
        time.sleep(.04)
        if "长流程" in message:
            return AIMessage(content="<round_summary>继续核对当前版本。</round_summary>",
                tool_calls=[tool_call("get_catalog_version", {}, f"c{count}")])
        if count == 0:
            return AIMessage(content="<round_summary>先查询版本和迪莫档案，核对构筑依据。</round_summary>",
                additional_kwargs={"reasoning_content": "PRIVATE_SENTINEL"}, tool_calls=[
                    tool_call("get_catalog_version", {}, "c1"),
                    tool_call("get_spirit_profile", {"name": "迪莫"}, "c2")])
        if count == 2:
            return AIMessage(content="<round_summary>已有档案，继续核对闪光的能耗与属性。</round_summary>",
                tool_calls=[tool_call("get_skill_profile", {"name": "闪光"}, "c3")])
        return AIMessage(content="<round_summary>已取得工具结果，整理可读结论。</round_summary>",
            tool_calls=[tool_call("final_answer", {"text": "最终答复：" + message}, "c4")])


@pytest.fixture
def web_server(tmp_path, monkeypatch):
    import ui.routes_team as team_routes
    monkeypatch.setattr(team_routes, 'TEAMS_DIR', tmp_path / 'teams')
    llm = BrowserLLM()
    app = create_chat_app(Settings(chat_db_path=str(tmp_path / "browser.db")), llm_factory=lambda settings: llm)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while not server.started and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    yield f"http://127.0.0.1:{port}", llm, app
    llm.release.set(); server.should_exit = True; thread.join(5); sock.close()
    assert not thread.is_alive()


def ready(page, url):
    page.goto(url)
    if '/chat/' not in page.url:
        page.locator('#new-session-btn').click()
    expect(page.locator("#send-btn")).to_be_enabled()


def send(page, message="帮我配招"):
    page.locator("#input").fill(message)
    page.locator("#send-btn").click()


def completed(page, count=1):
    expect(page.locator(".reply-md")).to_have_count(count, timeout=10000)
    expect(page.locator("#send-btn")).to_be_enabled(timeout=10000)


def test_rounds_details_keyboard_refresh_navigation_and_narrow(page, web_server, tmp_path):
    url, llm, _ = web_server
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda req: requests.append(req.url))
    ready(page, url); send(page); completed(page)
    sid_url = page.url
    expect(page.locator(".round-card")).to_have_count(3)
    assert page.locator(".round-card[open]").count() == 0
    assert not any("/tools/" in req for req in requests)
    first = page.locator(".round-card").first
    first.locator(":scope > summary").focus(); page.keyboard.press("Enter")
    expect(first.locator(".round-thought")).to_contain_text("先查询版本")
    first.locator(".tool-details summary").first.click()
    expect(first.locator(".tool-details pre").first).to_contain_text("data_digest")
    assert sum("/tools/" in req for req in requests) == 1
    assert "PRIVATE_SENTINEL" not in page.content()
    page.locator(".reply-toolbar button").first.click()
    expect(page.locator(".reply-raw")).to_be_visible()
    page.reload(); completed(page)
    assert llm.invocations == 3
    expect(page.locator(".round-card")).to_have_count(3)
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator(".round-card > summary").first.click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "round-cards-mobile.png"), full_page=True)
    page.locator("#new-session-btn").click()
    expect(page).not_to_have_url(sid_url)
    expect(page.locator(".round-card")).to_have_count(0)
    page.go_back(); completed(page)
    expect(page.locator(".round-card")).to_have_count(3)
    assert llm.invocations == 3 and not errors


def test_lost_create_response_retries_same_request_and_sse_polling(page, web_server):
    url, llm, _ = web_server
    ready(page, url)
    request_ids = []
    def lose_first_response(route):
        request_ids.append(route.request.post_data_json["request_id"])
        if len(request_ids) == 1:
            route.fetch(); route.abort()
        else:
            route.continue_()
    page.route("**/sessions/*/turns", lose_first_response)
    page.route("**/events?*", lambda route: route.abort())
    send(page)
    expect(page.locator("#chat-status")).to_contain_text("尚未确认")
    page.locator("#retry-send").click(); completed(page)
    assert len(request_ids) == 2 and request_ids[0] == request_ids[1]
    assert llm.invocations == 3
    expect(page.locator(".bubble.user")).to_have_count(1)
    expect(page.locator(".round-card")).to_have_count(3)


def test_new_session_does_not_cancel_old_turn_or_receive_late_updates(page, web_server):
    url, llm, _ = web_server
    ready(page, url); old_url = page.url
    send(page, "慢组队请求")
    assert llm.slow_entered.wait(2)
    page.locator("#new-session-btn").click()
    expect(page).not_to_have_url(old_url)
    expect(page.locator("#send-btn")).to_be_enabled()
    send(page, "新会话配招"); completed(page)
    llm.release.set()
    expect(page.locator(".reply-md")).to_contain_text("新会话配招")
    expect(page.locator(".bubble.user")).to_have_count(1)
    page.goto(old_url); completed(page)
    expect(page.locator(".reply-md")).to_contain_text("慢组队请求")
    assert llm.invocations == 6


def test_cancel_model_wait_and_ime_multiline(page, web_server):
    url, llm, _ = web_server
    ready(page, url)
    page.locator("#input").fill("帮我")
    page.locator("#input").dispatch_event("keydown", {"key": "Enter", "isComposing": True})
    assert llm.invocations == 0
    page.locator("#input").focus(); page.keyboard.press("End"); page.keyboard.press("Shift+Enter")
    assert "\n" in page.locator("#input").input_value()
    send(page, "慢组队")
    assert llm.slow_entered.wait(2)
    page.locator("#stop-btn").click(); completed(page)
    expect(page.locator(".turn-status")).to_have_text("已停止")
    expect(page.locator(".round-meta")).to_contain_text("已停止")
    llm.release.set()
    assert llm.invocations == 1


def test_scroll_position_and_other_pages(page, web_server):
    url, llm, _ = web_server
    ready(page, url)
    for i in range(5):
        send(page, "帮我配招 " + str(i)); completed(page, i + 1)
    page.evaluate("window.scrollTo(0, 0)")
    send(page, "最后配招"); completed(page, 6)
    expect(page.locator("#new-messages")).to_be_visible()
    assert page.evaluate("window.scrollY") < 100
    page.locator("#new-messages").click()
    expect(page.locator("#new-messages")).to_be_hidden()
    for path in ("/team", "/battle", "/spectate"):
        response = page.goto(url + path)
        assert response.status == 200
        assert not page.locator("body").evaluate("node => node.classList.contains('chat-page')")


def test_one_hundred_round_cards_remain_bounded_and_collapsed(page, web_server):
    url, llm, _ = web_server
    ready(page, url); send(page, "长流程组队")
    expect(page.locator(".round-card")).to_have_count(100, timeout=30000)
    expect(page.locator("#send-btn")).to_be_enabled(timeout=10000)
    assert llm.invocations == 100
    assert page.locator(".round-card[open]").count() == 0
    assert page.locator(".tool-details pre").all_text_contents() == ["尚未加载"] * 100
    expect(page.locator(".round-meta").last).to_contain_text("第 100 轮")
    page.reload()
    expect(page.locator(".round-card")).to_have_count(100)
    assert llm.invocations == 100


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}],
                         ids=["desktop", "narrow"])
def test_page_scroll_range_grows_with_round_cards_and_expanded_details(page, web_server, viewport):
    url, _, _ = web_server
    page.set_viewport_size(viewport)
    ready(page, url)
    initial_height = page.evaluate("document.scrollingElement.scrollHeight")
    send(page, "长流程组队")
    page.wait_for_function("document.querySelectorAll('.round-card').length >= 20")
    growing_height = page.evaluate("document.scrollingElement.scrollHeight")
    completed(page)
    assert growing_height > initial_height + 500
    closed_height = page.evaluate("document.scrollingElement.scrollHeight")
    assert closed_height > growing_height + 500

    # Wheel the page itself: scrolling a hidden inner container is insufficient.
    page.evaluate("window.scrollTo(0, 0)")
    first_heading = page.locator(".round-card > summary").first
    expect(first_heading).to_be_in_viewport()
    page.mouse.move(viewport["width"] // 2, viewport["height"] // 2)
    page.mouse.wheel(0, closed_height)
    expect(page.locator(".turn-status")).to_be_in_viewport()
    assert page.evaluate("window.scrollY") > 0
    expect(page.locator("#input")).to_be_in_viewport()

    page.locator(".round-card").evaluate_all("nodes => nodes.forEach(n => n.open = true)")
    expanded_height = page.evaluate("document.scrollingElement.scrollHeight")
    assert expanded_height > closed_height + 1000
    page.mouse.wheel(0, expanded_height)
    expect(page.locator(".turn-status")).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.reload(); completed(page)
    assert page.evaluate("document.scrollingElement.scrollHeight") == closed_height


def test_live_updates_preserve_expansion_and_keyboard_focus(page, web_server):
    url, llm, _ = web_server
    llm.pause_after_first = True
    ready(page, url); send(page)
    assert llm.second_entered.wait(2)
    first = page.locator(".round-card").first
    first.locator(":scope > summary").click()
    detail_heading = first.locator(".tool-details summary").first
    detail_heading.focus(); page.keyboard.press("Enter")
    expect(first.locator(".tool-details pre").first).to_contain_text("data_digest")
    expect(page.locator(".turn-status")).to_contain_text("已用")
    llm.release.set(); completed(page)
    expect(first).to_have_attribute("open", "")
    expect(first.locator(".tool-details").first).to_have_attribute("open", "")
    expect(detail_heading).to_be_focused()


def test_welcome_sidebar_drafts_rename_archive_delete(page, web_server):
    url, llm, _ = web_server
    page.goto(url)
    expect(page.locator('.welcome')).to_be_visible()
    assert page.request.get(url + '/api/chat/sessions').json()['sessions'] == []
    page.locator('#new-session-btn').click()
    expect(page.locator('#send-btn')).to_be_enabled()
    first_url = page.url
    send(page); completed(page)
    page.locator('#input').fill('未发送草稿')
    page.locator('#new-session-btn').click()
    expect(page).not_to_have_url(first_url)
    expect(page.locator('#input')).to_have_value('')
    page.locator('.session-link', has_text='帮我配招').click()
    expect(page).to_have_url(first_url)
    expect(page.locator('#input')).to_have_value('未发送草稿')
    row = page.locator('.session-entry').filter(has=page.locator('.session-link[aria-current="page"]'))
    row.locator('summary').click()
    page.once('dialog', lambda dialog: dialog.accept('手工标题'))
    row.get_by_role('button', name='重命名').click()
    expect(page.locator('#session-title')).to_have_text('手工标题')
    row = page.locator('.session-entry').filter(has=page.get_by_role('link', name='手工标题', exact=True))
    row.locator('summary').click()
    row.get_by_role('button', name='归档', exact=True).click()
    expect(page.locator('#send-btn')).to_be_disabled()
    page.locator('#show-archived').check()
    archived = page.locator('.session-entry').filter(has=page.get_by_role('link', name='手工标题', exact=True))
    archived.locator('summary').click(); archived.get_by_role('button', name='恢复会话').click()
    expect(page.locator('#send-btn')).to_be_enabled()
    page.locator('#show-archived').uncheck()
    row = page.locator('.session-entry').filter(has=page.get_by_role('link', name='手工标题', exact=True))
    row.locator('summary').click()
    page.once('dialog', lambda dialog: dialog.accept())
    row.get_by_role('button', name='删除', exact=True).click()
    expect(page.locator('.welcome')).to_be_visible()
    assert page.request.get(first_url.replace('/chat/', '/api/chat/sessions/')).status == 410
    assert llm.invocations == 3


def test_two_tabs_busy_and_result_restoration(page, context, web_server):
    url, llm, _ = web_server
    ready(page, url)
    other = context.new_page(); other.goto(page.url)
    expect(other.locator('#send-btn')).to_be_enabled()
    send(page, '慢组队'); assert llm.slow_entered.wait(2)
    send(other, '帮我配招但需要保留草稿')
    expect(other.locator('#input')).to_have_value('帮我配招但需要保留草稿')
    llm.release.set(); completed(page); completed(other)
    expect(other.locator('.bubble.user')).to_have_count(1)
    assert llm.invocations == 3
    other.close()


def test_team_dashboard_save_download_edit_return_and_screenshot(page, web_server, tmp_path):
    from test_advisor_tool_schemas import _valid_payload
    url, llm, _ = web_server
    llm.team_payload = _valid_payload()
    ready(page, url); session_url = page.url
    send(page, '围绕迪莫组队'); completed(page)
    expect(page.locator('.advice-member')).to_have_count(3)
    expect(page.locator('.team-validity')).to_contain_text('通过')
    assert not page.locator('.reply-md').inner_text().lstrip().startswith('{')
    page.once('dialog', lambda dialog: dialog.accept(''))
    page.get_by_role('button', name='保存队伍', exact=True).click()
    expect(page.locator('.save-status')).to_contain_text('已保存')
    with page.expect_download() as info:
        page.get_by_role('link', name='下载队伍 JSON', exact=True).click()
    downloaded = tmp_path / 'download.json'; info.value.save_as(downloaded)
    import json
    data = json.loads(downloaded.read_text(encoding='utf-8'))
    assert data['version'] == 2 and data['items'] == [] and len(data['team']) == 3
    page.get_by_role('link', name='在组队页编辑', exact=True).click()
    expect(page.get_by_role('link', name='返回原会话')).to_be_visible()
    expect(page.locator('#load-changes')).to_have_count(0)
    expect(page.locator('#validate-result')).to_contain_text('队伍合法')
    nature = page.locator('#nature-select option').evaluate_all("options => options.find(o => o.value !== '坦率').value")
    page.locator('#nature-select').select_option(nature)
    expect(page.locator('#validate-result')).to_contain_text('配置已修改')
    page.locator('#btn-validate').click()
    expect(page.locator('#validate-result')).to_contain_text('队伍合法')
    page.locator('#save-path').fill('edited.json'); page.locator('#btn-save').click()
    expect(page.locator('#notice')).to_contain_text('已保存')
    edited = page.request.get(url + '/api/team/load?path=edited.json').json()
    assert edited['team'][0]['nature'] == nature
    assert edited['team'][0]['skills'] == data['team'][0]['skills'] and edited['items'] == []
    page.get_by_role('link', name='返回原会话').click()
    expect(page).to_have_url(session_url)
    expect(page.locator('.advice-member')).to_have_count(3)
    expect(page.locator('.advice-member').first).to_contain_text('性格：坦率')
    expect(page.locator('.save-status')).to_contain_text('保存记录')
    assert llm.invocations == 1
    page.screenshot(path=str(tmp_path / 'team-dashboard-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(tmp_path / 'team-dashboard-mobile.png'), full_page=True)


def test_artifact_late_response_and_untrusted_text(page, web_server):
    from test_advisor_tool_schemas import _valid_payload
    url, llm, _ = web_server
    llm.team_payload = _valid_payload()
    llm.team_payload['team'][0]['rationale'] = '<img src=x onerror="window.UNSAFE=1">'
    ready(page, url)
    held = []
    page.route('**/api/chat/artifacts/*', lambda route: held.append((route, route.fetch())))
    send(page); completed(page)
    expect(page.locator('.team-advice-card')).to_contain_text('正在加载')
    old_url = page.url
    page.locator('#new-session-btn').click()
    expect(page).not_to_have_url(old_url)
    for route, response in held:
        route.fulfill(response=response)
    expect(page.locator('.team-advice-card')).to_have_count(0)
    page.unroute('**/api/chat/artifacts/*')
    page.goto(old_url)
    expect(page.locator('.advice-member')).to_have_count(3)
    page.locator('.advice-member').first.locator('summary').click()
    expect(page.locator('.advice-member').first).to_contain_text('<img')
    assert page.locator('.advice-member img').count() == 0
    assert page.evaluate('window.UNSAFE') is None


def test_loading_older_turns_preserves_document_scroll_anchor(page, web_server):
    url, llm, app = web_server
    store = app.state.turn_coordinator.store
    sid = store.new_session()["id"]
    for i in range(21):
        snap, _ = store.create_turn(sid, str(i), f"历史消息 {i}", {})
        store.finish(snap["turn_id"], AssistantResult(message=(f"历史答复 {i}\n\n" * 5)))
    ready(page, url + "/chat/" + sid)
    expect(page.locator(".chat-turn")).to_have_count(20)
    page.evaluate("window.scrollTo(0, 0)")
    anchor = page.locator(".chat-turn").first
    anchor_id = anchor.get_attribute("data-turn-id")
    top = anchor.bounding_box()["y"]
    page.locator("#older-messages").click()
    expect(page.locator(".chat-turn")).to_have_count(21)
    restored_anchor = page.locator(f'[data-turn-id="{anchor_id}"]')
    assert abs(restored_anchor.bounding_box()["y"] - top) < 2
    assert page.evaluate("window.scrollY") > 0
    assert llm.invocations == 0
