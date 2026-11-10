"""Public error codes never include remote exception messages or credentials."""


class MCPError(RuntimeError):
    code = "mcp_error"


class ConfigurationError(MCPError):
    code = "configuration_error"


class ToolDenied(MCPError):
    code = "tool_denied"


class ToolUnavailable(MCPError):
    code = "tool_unavailable"


class InvalidArguments(MCPError):
    code = "invalid_arguments"


class SourceUnavailable(MCPError):
    code = "source_unavailable"


class RemoteToolError(MCPError):
    code = "remote_tool_error"


class FixtureMissing(MCPError):
    code = "fixture_missing"


class FixtureInvalid(MCPError):
    code = "fixture_invalid"


class SecretInFixture(MCPError):
    code = "secret_in_fixture"


class AuthenticationRequired(MCPError):
    code = "authentication_required"
