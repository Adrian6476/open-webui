"""Account-bound Honcho access and per-model update policy.

The upstream credential stays in the connector. Never infer write permission
from client-supplied capabilities or from a model's parameter count.
"""

from __future__ import annotations

import hashlib
import logging
import os

import httpx
from fastapi import HTTPException

log = logging.getLogger(__name__)


def enrolled(user) -> bool:
    uid = user.get('id') if isinstance(user, dict) else getattr(user, 'id', None)
    role = user.get('role') if isinstance(user, dict) else getattr(user, 'role', None)
    owner = os.getenv('HONCHO_USER_ID', '')
    return bool(owner and uid == owner and role == 'admin' and os.getenv('HONCHO_CONNECTOR_URL'))


def check_model_setting(form, previous, user) -> None:
    """Every model create/update/import/restore path calls this check."""
    old = bool(previous and previous.meta.honcho_write_enabled)
    if 'honcho_write_enabled' not in form.meta.model_fields_set:
        form.meta.honcho_write_enabled = old
    elif user.role != 'admin' and form.meta.honcho_write_enabled != old:
        raise HTTPException(403, 'Only administrators can change Honcho memory updates.')


def eligible(metadata: dict) -> bool:
    from open_webui.utils.chat_id import is_saved_chat_id

    return bool(
        is_saved_chat_id(metadata.get('chat_id'))
        and not metadata.get('internal')
        and not metadata.get('direct')
        and not metadata.get('automation_id')
        and not metadata.get('task')
        and (metadata.get('features') or {}).get('memory')
    )


async def policy(request, user, model=None, metadata=None) -> str:
    if not enrolled(user):
        return 'native'
    metadata = metadata if metadata is not None else getattr(request.state, 'metadata', {})
    if not eligible(metadata) or getattr(request.state, 'internal', False) or getattr(request.state, 'direct', False):
        return 'off'
    from open_webui.models.config import Config
    from open_webui.models.models import Models

    if not await Config.get('memories.enable'):
        return 'off'
    model = model or metadata.get('model') or {}
    model_id = model.get('id') if isinstance(model, dict) else model
    record = await Models.get_model_by_id(model_id) if model_id else None
    if record and (record.meta.capabilities or {}).get('memory', True) is False:
        return 'off'
    if not record and isinstance(model, dict):
        if ((model.get('info') or {}).get('meta', {}).get('capabilities') or {}).get('memory', True) is False:
            return 'off'
    return (
        'write'
        if record and record.meta.honcho_write_enabled is True and metadata.get('_honcho_write_at_start') is not False
        else 'read'
    )


async def connector(method: str, path: str, payload=None, *, timeout=5):
    url = os.environ['HONCHO_CONNECTOR_URL'].rstrip('/')
    key = os.environ['HONCHO_CONNECTOR_KEY']
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.request(method, url + path, json=payload, headers={'Authorization': f'Bearer {key}'})
    if response.is_error:
        # Never expose upstream text or chat contents in logs/model-visible errors.
        raise HTTPException(503, 'Shared memory is temporarily unavailable.')
    return response.json() if response.content else {}


async def recall(request, user, messages, model):
    if await policy(request, user, model) not in {'read', 'write'}:
        return ''
    from open_webui.utils.misc import get_content_from_message, get_last_user_message_item

    target = get_last_user_message_item(messages)
    query = get_content_from_message(target) if target else ''
    try:
        result = await connector('POST', '/recall', {'query': (query or '')[-4000:]})
        return result.get('context', '')[:6000]
    except Exception:
        log.warning('Honcho recall unavailable; continuing without shared memory.')
        return ''


async def capture(request, user, model, metadata, messages, assistant_message):
    if metadata.get('_honcho_write_at_start') is not True:
        return
    if await policy(request, user, model, metadata) != 'write':
        return
    # Regenerations and tool-result resumptions must not backfill read-only turns.
    if metadata.get('regeneration_message_id') or metadata.get('assistant_message_id'):
        return
    uid = metadata.get('user_message_id')
    aid = metadata.get('message_id')
    if not uid or not aid:
        return
    from open_webui.utils.misc import get_content_from_message

    user_message = next((m for m in messages if m.get('id') == uid and m.get('role') == 'user'), None)
    if not user_message:
        return
    final = next((m for m in reversed(messages) if m.get('id') == aid and m.get('role') == 'assistant'), None)
    final = final or assistant_message
    if not final or final.get('error') or final.get('finish_reason') in {'error', 'cancelled'}:
        return
    items = []
    for role, message, mid in [('user', user_message, uid), ('assistant', final, aid)]:
        content = get_content_from_message(message)
        if not isinstance(content, str) or not content.strip():
            continue
        items.append({'id': mid, 'role': role, 'content': content, 'timestamp': message.get('timestamp')})
    if not items or items[-1]['role'] != 'assistant':
        return
    try:
        await connector(
            'POST',
            '/capture',
            {
                'chat_id': metadata['chat_id'],
                'model_id': model.get('id'),
                'messages': items,
            },
            timeout=3,
        )
    except Exception:
        log.error('Honcho capture was not acknowledged; check connector delivery status.')


async def memory_tool(operation, request, user, metadata, *, query='', content='', memory_id=''):
    mode = await policy(request, user, metadata=metadata)
    if mode not in {'read', 'write'}:
        return {'error': 'Shared memory is disabled for this conversation.'}
    if operation in {'add', 'replace', 'delete'} and mode != 'write':
        return {'error': 'Honcho updates are disabled for this model.'}
    if operation == 'search':
        return await connector('POST', '/recall', {'query': query[-4000:]})
    if operation == 'list':
        return await connector('GET', '/profile')
    if operation in {'add', 'replace'}:
        key = hashlib.sha256((str(metadata.get('message_id')) + operation + memory_id + content).encode()).hexdigest()
        return await connector(
            'POST',
            '/conclusions',
            {
                'content': content[:8000],
                'replaces': memory_id if operation == 'replace' else None,
                'operation_id': key,
            },
        )
    if operation == 'delete':
        from urllib.parse import quote

        return await connector('DELETE', '/conclusions/' + quote(memory_id, safe=''))
    return {'error': 'Unsupported shared memory operation.'}
