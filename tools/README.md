# External Tools

This directory holds user-extensible tool modules. Each `.py` file here is auto-discovered at startup if `external_tools_dir` is set in your `aletheia.toml` and the tool name appears in `tools.enabled`.

## Tool plugin contract

Each module must export two things:

```python
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "your_tool_name",
        "description": "What the tool does (model-facing).",
        "parameters": {
            "type": "object",
            "properties": {
                "arg_name": {
                    "type": "string",
                    "description": "What this argument is for."
                }
            },
            "required": ["arg_name"],
        },
    },
}


def execute(arg_name: str) -> str:
    # Do the work. Return a string.
    return "result"
```

The schema follows OpenAI/Ollama function-calling JSON Schema. The `execute` function receives keyword arguments matching the schema and returns a string result.

## Notes

- Tool names must be unique across both built-in and external tools.
- Modules starting with `_` are skipped.
- Memory access uses the `PARTNER_CLIENT_MEMORY_DIR` environment variable (the client sets this before invoking tools).
- If you need network or filesystem access outside the memory dir, do it explicitly — there's no implicit sandboxing.

## Examples

See `partner_client/tools_builtin/` for built-in examples (`read_file.py`, `weather.py`, `search_web.py`, etc.).
