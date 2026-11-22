"""Exercise actual tool registration with an isolated, mocked configuration store."""

import os
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx


class HonchoRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_returns_explicit_honcho_failure_instead_of_empty_memory(self):
        from open_webui.tools import honcho as shared_tools
        from open_webui.utils import honcho

        original_client = httpx.AsyncClient

        async def transport(request):
            raise httpx.ReadTimeout('', request=request)

        def client(**kwargs):
            return original_client(**kwargs, transport=httpx.MockTransport(transport))

        with (
            patch.dict(
                os.environ, {'HONCHO_CONNECTOR_URL': 'http://synthetic-connector', 'HONCHO_CONNECTOR_KEY': 'synthetic'}
            ),
            patch.object(honcho, 'policy', AsyncMock(return_value='read')),
            patch.object(httpx, 'AsyncClient', client),
        ):
            for function, kwargs in [
                (shared_tools.list_memories, {}),
                (shared_tools.search_memories, {'query': 'synthetic'}),
            ]:
                result = json.loads(await function(__request__=None, __user__={}, __metadata__={}, **kwargs))
                self.assertEqual(result['backend'], 'honcho')
                self.assertEqual(result['status'], 'unavailable')
                self.assertIn('does not mean the memory is empty', result['error'])

    async def test_memory_tools_return_slow_shared_profile_instead_of_empty_error(self):
        from open_webui.tools import honcho as shared_tools
        from open_webui.utils import honcho
        from open_webui.utils.middleware import execute_tool_call
        from open_webui.utils.tools import get_async_tool_function_and_apply_extra_params, get_builtin_tool_spec

        original_client = httpx.AsyncClient

        async def transport(request):
            # The connector can legitimately need six seconds for a remote read.
            # Observe the real HTTPX timeout contract without a six-second sleep.
            if request.extensions['timeout']['read'] < 6:
                raise httpx.ReadTimeout('', request=request)
            return httpx.Response(200, json={'context': 'Synthetic shared profile', 'card': ['Synthetic fact']})

        def client(**kwargs):
            return original_client(**kwargs, transport=httpx.MockTransport(transport))

        metadata = {'chat_id': 'synthetic-chat', 'model': {'id': 'synthetic-model'}}
        request = SimpleNamespace(state=SimpleNamespace(metadata=metadata))
        with (
            patch.dict(
                os.environ, {'HONCHO_CONNECTOR_URL': 'http://synthetic-connector', 'HONCHO_CONNECTOR_KEY': 'synthetic'}
            ),
            patch.object(honcho, 'policy', AsyncMock(return_value='read')),
            patch.object(httpx, 'AsyncClient', client),
            patch('open_webui.utils.middleware.requires_tool_approval', AsyncMock(return_value=False)),
        ):
            for name, arguments in [('list_memories', '{}'), ('search_memories', '{"query":"synthetic"}')]:
                function = getattr(shared_tools, name)
                bound = await get_async_tool_function_and_apply_extra_params(
                    function,
                    {
                        '__request__': request,
                        '__user__': {'id': 'synthetic'},
                        '__metadata__': metadata,
                    },
                )
                metadata['tools'] = {name: {'callable': bound, 'spec': get_builtin_tool_spec(function)}}
                call = {'name': name, 'arguments': arguments, 'call_id': 'synthetic-call'}
                _, result, _, _, _ = await execute_tool_call({'messages': []}, metadata, None, call)
                self.assertIn('Synthetic shared profile', str(result), f'{name} lost shared memory: {result}')

    async def test_native_read_only_writable_and_disabled_tool_sets(self):
        from open_webui.models.config import Config
        from open_webui.models.models import Models
        from open_webui.utils.tools import get_builtin_tools

        model = {
            'id': 'synthetic-model',
            'info': {
                'meta': {
                    'capabilities': {'memory': True},
                    'builtinTools': {
                        name: False
                        for name in [
                            'time',
                            'user_input',
                            'chats',
                            'notes',
                            'knowledge',
                            'files',
                            'channels',
                            'notifications',
                            'web_search',
                            'image_generation',
                            'code_interpreter',
                            'tasks',
                            'automations',
                            'calendar',
                            'subagents',
                            'skills',
                            'tools',
                        ]
                    },
                }
            },
        }
        metadata = {'chat_id': 'synthetic-chat', 'model': model, 'features': {'memory': True}}
        request = SimpleNamespace(
            state=SimpleNamespace(metadata=metadata), app=SimpleNamespace(state=SimpleNamespace())
        )
        user = {'id': 'owner', 'role': 'admin'}
        extra = {'__user__': user, '__metadata__': metadata}
        record = SimpleNamespace(meta=SimpleNamespace(capabilities={'memory': True}, honcho_write_enabled=False))
        with (
            patch.dict(os.environ, {'HONCHO_USER_ID': 'owner', 'HONCHO_CONNECTOR_URL': 'http://connector'}),
            patch.object(Config, 'get_many', AsyncMock(return_value={'memories.enable': True})),
            patch.object(Config, 'get', AsyncMock(return_value=True)),
            patch.object(Models, 'get_model_by_id', AsyncMock(return_value=record)),
        ):
            tools = await get_builtin_tools(request, extra, {'memory': True}, model)
            self.assertEqual(set(tools), {'search_memories', 'list_memories'})
            record.meta.honcho_write_enabled = True
            tools = await get_builtin_tools(request, extra, {'memory': True}, model)
            self.assertEqual(
                set(tools),
                {'search_memories', 'list_memories', 'add_memory', 'replace_memory_content', 'delete_memory'},
            )
            metadata['chat_id'] = 'temporary:test'
            self.assertEqual(await get_builtin_tools(request, extra, {'memory': True}, model), {})
            metadata['chat_id'] = 'synthetic-chat'
            user['id'] = 'other-account'
            tools = await get_builtin_tools(request, extra, {'memory': True}, model)
            self.assertIn('update_memory', tools)
            self.assertIn('read_memory_path', tools)
            self.assertIn('add_memory', tools)


if __name__ == '__main__':
    unittest.main()
