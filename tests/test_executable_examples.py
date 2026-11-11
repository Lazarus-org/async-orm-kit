"""Execute the actual public getting-started snippets in isolated interpreters."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

RUNNER = """from __future__ import annotations
import ast, asyncio, inspect, json, sys
blocks, kind = json.load(sys.stdin)
async def execute(source):
    code = compile(source, '<documentation>', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    pending = eval(code, globals())
    if inspect.isawaitable(pending):
        await pending
async def main():
    try:
        if kind == 'readme':
            for block in blocks:
                await execute(block)
            assert len(authors) == 1 and authors[0].name == 'Ada'
            assert authors[0].posts[0].title == 'Async SQL'
        else:
            for block in blocks[:4]:
                await execute(block)
            await create_dev_schema()
            user = await add_user('ada@example.com')
            assert user.id == 1 and user.active
            assert (await list_active_users())[0].email == 'ada@example.com'
            assert (await get_one('ada@example.com')).id == 1
            for block in blocks[4:]:
                await execute(block)
            async with SessionFactory() as session:
                assert (await User.objects.get(session, id=1)).login_count == 1
    finally:
        if 'engine' in globals():
            await engine.dispose()
asyncio.run(main())
"""


@pytest.mark.parametrize("kind", ["readme", "quickstart"])
def test_public_examples_execute_with_native_sqlite(kind, tmp_path):
    if kind == "readme":
        blocks = re.findall(
            r"```python\n(.*?)\n```", (ROOT / "README.md").read_text(), re.S
        )
        start = next(
            i for i, code in enumerate(blocks) if "class Author(Model):" in code
        )
        blocks = blocks[start : start + 4]
    else:
        text = (ROOT / "docs/quickstart.rst").read_text()
        blocks = []
        for chunk in text.split(".. code-block:: python\n")[1:]:
            lines = []
            for line in chunk.lstrip("\n").splitlines():
                if line and not line.startswith("   "):
                    break
                lines.append(line[3:] if line.startswith("   ") else "")
            blocks.append("\n".join(lines))
    blocks = [
        re.sub(r"sqlite\+aiosqlite:///\./\w+\.db", "sqlite+aiosqlite:///:memory:", code)
        for code in blocks
    ]
    result = subprocess.run(
        [sys.executable, "-I", "-c", RUNNER],
        input=json.dumps([blocks, kind]),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
