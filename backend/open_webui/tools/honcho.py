"""Personal memory tools bound to the authenticated enrolled account."""

from open_webui.utils import honcho
from open_webui.utils.json_codec import JSONCodec
from fastapi import HTTPException


async def _call(operation, request, user, metadata, **kwargs):
    try:
        result = await honcho.memory_tool(operation, request, user, metadata or {}, **kwargs)
        return JSONCodec.dumps({'backend': 'honcho', **result})
    except HTTPException as error:
        return JSONCodec.dumps({'backend': 'honcho', 'status': 'unavailable', 'error': str(error.detail)})


async def search_memories(query: str, __request__=None, __user__=None, __metadata__=None) -> str:
    """Recall the user's shared Honcho memory across assistants. Query in natural language.

    An unavailable result means retrieval failed, not that the user's memory is empty.
    """
    return await _call('search', __request__, __user__, __metadata__, query=query)


async def list_memories(__request__=None, __user__=None, __metadata__=None) -> str:
    """Read the user's shared Honcho profile and conclusions, including IDs for corrections.

    These are Honcho memories, not the retained native OpenWebUI archive.
    An unavailable result means retrieval failed, not that the user's memory is empty.
    """
    return await _call('list', __request__, __user__, __metadata__)


async def add_memory(content: str, __request__=None, __user__=None, __metadata__=None) -> str:
    """Save an enduring user fact, preference or confirmed plan in English. Do not invent facts."""
    return await _call('add', __request__, __user__, __metadata__, content=content)


async def replace_memory_content(
    memory_id: str, content: str, __request__=None, __user__=None, __metadata__=None
) -> str:
    """Correct a saved conclusion by ID. Preserve historical dates and distinguish plans from facts."""
    return await _call('replace', __request__, __user__, __metadata__, content=content, memory_id=memory_id)


async def delete_memory(memory_id: str, __request__=None, __user__=None, __metadata__=None) -> str:
    """Delete one incorrect conclusion by ID. This does not erase its source conversation."""
    return await _call('delete', __request__, __user__, __metadata__, memory_id=memory_id)
