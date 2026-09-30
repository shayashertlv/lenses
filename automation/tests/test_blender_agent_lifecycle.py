"""Offline shutdown witnesses: no model requests, Blender processes, or sockets."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('agents')
from agents.exceptions import MaxTurnsExceeded
from agents.tool_context import ToolContext

from blender_agent.agent import checkpoint_on_exit, make_budget_status, with_budget_context
from blender_agent.budget import BudgetExceeded


class ConnectedServer:
    def __init__(self, events, failure=None):
        self.events, self.failure, self.connected = events, failure, False

    async def __aenter__(self):
        self.connected = True
        self.events.append('connected')
        return self

    async def __aexit__(self, *_):
        self.events.append('disconnected')
        self.connected = False

    async def call_tool(self, name, arguments):
        assert self.connected, 'Checkpoint must be attempted before MCP disconnects'
        assert name == 'execute_blender_code'
        self.events.append('save_attempted')
        tree = ast.parse(arguments['code'])
        save = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == 'save_as_mainfile')
        kwargs = {item.arg: ast.literal_eval(item.value) for item in save.keywords}
        assert kwargs['copy'] is True, 'Stopping must preserve the working-file identity'
        if self.failure == 'exception':
            raise ConnectionError('offline simulated save failure')
        if self.failure != 'missing_file':
            Path(kwargs['filepath']).write_bytes(b'offline checkpoint fixture')
        return SimpleNamespace(isError=self.failure == 'tool_error')


class EventLog:
    def __init__(self, events):
        self.events, self.records = events, []

    def event(self, name, **fields):
        self.events.append(name)
        self.records.append((name, fields))


@pytest.mark.parametrize('error_type', [None, BudgetExceeded, MaxTurnsExceeded, RuntimeError])
def test_checkpoint_saved_before_disconnect_and_original_error_survives(tmp_path, error_type):
    events, summary = [], {}
    log = EventLog(events)
    original = error_type('original inference stop') if error_type else None

    async def run():
        async with ConnectedServer(events) as server:
            async with checkpoint_on_exit(server, tmp_path, summary, log):
                events.append('inference_stopped')
                if original is not None:
                    raise original

    if original is None:
        asyncio.run(run())
    else:
        with pytest.raises(error_type) as caught:
            asyncio.run(run())
        assert caught.value is original
    assert events == ['connected', 'inference_stopped', 'save_attempted', 'stop_checkpoint', 'disconnected']
    checkpoint = tmp_path / 'stop-checkpoint.blend'
    assert checkpoint.is_file()
    assert summary['stop_checkpoint'] == {'path': str(checkpoint), 'saved': True}
    assert log.records == [('stop_checkpoint', summary['stop_checkpoint'])]


@pytest.mark.parametrize('failure', ['exception', 'tool_error', 'missing_file'])
def test_checkpoint_failure_is_reported_without_replacing_original_error(tmp_path, failure):
    events, summary = [], {}
    log, original = EventLog(events), BudgetExceeded('original budget refusal')

    async def run():
        async with ConnectedServer(events, failure) as server:
            async with checkpoint_on_exit(server, tmp_path, summary, log):
                raise original

    with pytest.raises(BudgetExceeded) as caught:
        asyncio.run(run())
    assert caught.value is original
    assert events == ['connected', 'save_attempted', 'stop_checkpoint', 'disconnected']
    assert summary['stop_checkpoint']['saved'] is False
    assert summary['stop_checkpoint']['path'] == str(tmp_path / 'stop-checkpoint.blend')
    expected = 'ConnectionError' if failure == 'exception' else 'RuntimeError'
    assert summary['stop_checkpoint']['error'].startswith(expected + ':')
    assert log.records == [('stop_checkpoint', summary['stop_checkpoint'])]


def test_budget_status_sdk_tool_reads_current_total_trial_allowance():
    budget = object()
    state = {'maximum_usd': '17.000000', 'remaining_usd': '12.500000'}
    calls = []

    def summary(current):
        calls.append(current)
        return dict(state)

    tool = make_budget_status(SimpleNamespace(summary=summary), budget)
    assert tool.name == 'budget_status'
    assert tool.params_json_schema['properties'] == {}

    async def invoke():
        context = ToolContext(context=None, tool_name='budget_status',
                              tool_call_id='offline-budget-status', tool_arguments='{}')
        first = await tool.on_invoke_tool(context, '{}')
        state['remaining_usd'] = '10.000000'
        second = await tool.on_invoke_tool(context, '{}')
        return first, second

    first, second = asyncio.run(invoke())
    assert first['remaining_usd'] == '12.500000'
    assert second['remaining_usd'] == '10.000000'
    assert calls == [budget, budget]


def test_budget_context_preserves_observation_blocks_and_only_reads_accounting():
    from agents import function_tool
    from agents.tool import ToolOutputText, ToolOutputImage
    image = ToolOutputImage(image_url='data:image/png;base64,YQ==')
    observation = ToolOutputText(text='Exact diagnostic result')
    @function_tool
    def preview() -> list[ToolOutputText | ToolOutputImage]:
        return [observation, image]
    original = preview.on_invoke_tool
    state = {'remaining_usd': '4.2', 'current_invocation': {'planning': {
        'status': 'finalize_now', 'next_reservation_at_last_size_usd': '3.6',
        'suggested_finish_balance_usd': '4.5'}}}
    trial = SimpleNamespace(summary=lambda _: state)
    wrapped = with_budget_context(preview, trial, object())
    context = ToolContext(context=None, tool_name=wrapped.name, tool_call_id='offline-budget', tool_arguments='{}')
    result = asyncio.run(wrapped.on_invoke_tool(context, '{}'))
    assert result[0] is observation and result[1] is image
    assert 'finalize_now' in result[2].text and '4.2' in result[2].text
    assert preview.on_invoke_tool is original
    assert wrapped.params_json_schema == preview.params_json_schema
