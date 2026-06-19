from runtime.supervisor import tool_modes


def test_local_file_access_mode_expands_to_model_tools():
    caller = {"file_access": "ask", "tools": {}}

    allowed = tool_modes.allowed_model_tools(caller)
    pinned = tool_modes.pinned_model_tools(caller)

    assert {"list_files", "read_file", "create_file", "edit_file", "write_file"} <= set(allowed)
    assert {"list_files", "read_file", "create_file", "edit_file", "write_file"} <= set(pinned)


def test_local_file_access_read_only_excludes_write_tools():
    caller = {"file_access": "read", "tools": {}}

    allowed = tool_modes.allowed_model_tools(caller)

    assert {"list_files", "read_file"} <= set(allowed)
    assert "create_file" not in allowed
    assert "edit_file" not in allowed
    assert "write_file" not in allowed
