"""Isolated model-policy and capture tests; no production database or credentials."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def load_policy():
    path = Path(__file__).parents[1] / 'open_webui/utils/honcho.py'
    spec = importlib.util.spec_from_file_location('honcho_policy_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HonchoPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = load_policy()
        self.user = SimpleNamespace(id='owner', role='admin')
        self.record = SimpleNamespace(meta=SimpleNamespace(capabilities={'memory': True}, honcho_write_enabled=False))
        self.models = SimpleNamespace(get_model_by_id=AsyncMock(side_effect=lambda _: self.record))
        self.metadata = {
            'chat_id': 'saved-chat',
            'features': {'memory': True},
            'model': {'id': 'stored-model'},
            'user_message_id': 'u',
            'message_id': 'a',
            '_honcho_write_at_start': True,
        }
        self.request = SimpleNamespace(state=SimpleNamespace(metadata=self.metadata))
        self.model = {'id': 'stored-model', 'info': {'meta': {'honcho_write_enabled': True}}}
        self.stack = __import__('contextlib').ExitStack()
        self.stack.enter_context(
            patch.dict(os.environ, {'HONCHO_USER_ID': 'owner', 'HONCHO_CONNECTOR_URL': 'http://connector'})
        )
        self.stack.enter_context(
            patch.dict(
                sys.modules,
                {
                    'open_webui.models.config': SimpleNamespace(
                        Config=SimpleNamespace(get=AsyncMock(return_value=True))
                    ),
                    'open_webui.models.models': SimpleNamespace(Models=self.models),
                    'open_webui.utils.chat_id': SimpleNamespace(
                        is_saved_chat_id=lambda s: bool(s) and not s.startswith(('temporary:', 'local:', 'channel:'))
                    ),
                    'open_webui.utils.misc': SimpleNamespace(get_content_from_message=lambda m: m.get('content', '')),
                },
            )
        )

    async def asyncTearDown(self):
        self.stack.close()

    async def test_only_enrolled_account_uses_honcho(self):
        self.assertTrue(self.module.enrolled(self.user))
        for user in [SimpleNamespace(id='other', role='admin'), SimpleNamespace(id='owner', role='user')]:
            self.assertEqual(await self.module.policy(self.request, user, self.model), 'native')

    async def test_stored_flag_wins_over_request_and_unknown_models_are_read_only(self):
        self.assertEqual(await self.module.policy(self.request, self.user, self.model), 'read')
        self.record.meta.honcho_write_enabled = True
        self.assertEqual(await self.module.policy(self.request, self.user, self.model), 'write')
        self.record = None
        self.assertEqual(await self.module.policy(self.request, self.user, self.model), 'read')

    async def test_temporary_tasks_and_disabled_memory_are_off(self):
        for update in [
            {'chat_id': 'temporary:x'},
            {'chat_id': 'local:x'},
            {'chat_id': 'channel:x'},
            {'internal': True},
            {'task': 'title_generation'},
            {'automation_id': 'task'},
            {'features': {'memory': False}},
        ]:
            metadata = {**self.metadata, **update}
            self.assertEqual(await self.module.policy(self.request, self.user, self.model, metadata), 'off')
        self.record.meta.capabilities['memory'] = False
        self.assertEqual(await self.module.policy(self.request, self.user, self.model), 'off')

    async def test_read_only_tool_execution_rejects_mutations(self):
        with patch.object(self.module, 'connector', AsyncMock()) as call:
            result = await self.module.memory_tool(
                'delete', self.request, self.user, self.metadata, memory_id='synthetic'
            )
            self.assertIn('error', result)
            call.assert_not_awaited()

    async def test_prefetch_failure_is_not_silently_presented_as_no_memory(self):
        fake_misc = SimpleNamespace(
            get_content_from_message=lambda message: message.get('content', ''),
            get_last_user_message_item=lambda messages: messages[-1],
        )
        with (
            patch.dict(sys.modules, {'open_webui.utils.misc': fake_misc}),
            patch.object(self.module, 'connector', AsyncMock(side_effect=Exception('synthetic timeout'))),
        ):
            result = await self.module.recall(
                self.request, self.user, [{'role': 'user', 'content': 'synthetic'}], self.model
            )
            self.assertIn('Honcho shared memory', result)
            self.assertIn('does not mean the memory is empty', result)
            self.metadata['features']['memory'] = False
            self.assertEqual(await self.module.recall(self.request, self.user, [], self.model), '')

    async def test_capture_never_backfills_previous_turns_or_regenerations(self):
        self.record.meta.honcho_write_enabled = True
        messages = [
            {'id': 'old', 'role': 'user', 'content': 'Excluded previous turn'},
            {'id': 'u', 'role': 'user', 'content': 'Current synthetic turn'},
            {'id': 'a', 'role': 'assistant', 'content': 'Synthetic answer'},
        ]
        with patch.object(self.module, 'connector', AsyncMock()) as call:
            await self.module.capture(self.request, self.user, self.model, self.metadata, messages, {})
            payload = call.await_args.args[2]
            self.assertEqual([x['id'] for x in payload['messages']], ['u', 'a'])
            call.reset_mock()
            for metadata in [
                {**self.metadata, 'regeneration_message_id': 'old'},
                {**self.metadata, 'assistant_message_id': 'resume'},
            ]:
                await self.module.capture(self.request, self.user, self.model, metadata, messages, {})
            call.assert_not_awaited()
            self.record.meta.honcho_write_enabled = False
            await self.module.capture(self.request, self.user, self.model, self.metadata, messages, {})
            call.assert_not_awaited()

    def test_non_admin_cannot_raise_model_write_flag(self):
        form = SimpleNamespace(
            meta=SimpleNamespace(honcho_write_enabled=True, model_fields_set={'honcho_write_enabled'})
        )
        with self.assertRaises(Exception) as error:
            self.module.check_model_setting(form, None, SimpleNamespace(role='user'))
        self.assertEqual(error.exception.status_code, 403)
        form.meta.model_fields_set = set()
        self.module.check_model_setting(form, self.record, SimpleNamespace(role='user'))
        self.assertFalse(form.meta.honcho_write_enabled)


if __name__ == '__main__':
    unittest.main()
