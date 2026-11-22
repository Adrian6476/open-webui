"""Personal memory tools bound to the authenticated enrolled account."""

from open_webui.utils import honcho
from open_webui.utils.json_codec import JSONCodec


async def search_memories(query: str, __request__=None, __user__=None, __metadata__=None) -> str:
    """Recall information about the user relevant to a question. Query in natural language."""
    return JSONCodec.dumps(await honcho.memory_tool('search', __request__, __user__, __metadata__ or {}, query=query))


async def list_memories(__request__=None, __user__=None, __metadata__=None) -> str:
    """Read the user's shared profile and saved conclusions, including IDs for corrections."""
    return JSONCodec.dumps(await honcho.memory_tool('list', __request__, __user__, __metadata__ or {}))


async def add_memory(content: str, __request__=None, __user__=None, __metadata__=None) -> str:
    """Save an enduring user fact, preference or confirmed plan in English. Do not invent facts."""
    return JSONCodec.dumps(await honcho.memory_tool('add', __request__, __user__, __metadata__ or {}, content=content))


async def replace_memory_content(
    memory_id: str, content: str, __request__=None, __user__=None, __metadata__=None
) -> str:
    """Correct a saved conclusion by ID. Preserve historical dates and distinguish plans from facts."""
    return JSONCodec.dumps(
        await honcho.memory_tool(
            'replace',
            __request__,
            __user__,
            __metadata__ or {},
            content=content,
            memory_id=memory_id,
        )
    )


async def delete_memory(memory_id: str, __request__=None, __user__=None, __metadata__=None) -> str:
    """Delete one incorrect conclusion by ID. This does not erase its source conversation."""
    return JSONCodec.dumps(
        await honcho.memory_tool(
            'delete',
            __request__,
            __user__,
            __metadata__ or {},
            memory_id=memory_id,
        )
    )
