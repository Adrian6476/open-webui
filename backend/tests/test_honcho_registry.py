"""Exercise actual tool registration with an isolated, mocked configuration store."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class HonchoRegistryTest(unittest.IsolatedAsyncioTestCase):
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
