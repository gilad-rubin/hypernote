"""Tests for Hypernote CLI — verify command structure and help text."""

from click.testing import CliRunner
from hypernote.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "hypernote" in result.output.lower()


def test_all_command_groups_exist():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    for group in ["observe", "edit", "execute", "jobs", "runtime", "checkpoints", "workspace", "setup"]:
        assert group in result.output, f"Missing command group: {group}"


def test_observe_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["observe", "--help"])
    assert result.exit_code == 0
    for cmd in ["cat", "status", "list"]:
        assert cmd in result.output


def test_edit_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["edit", "--help"])
    assert result.exit_code == 0
    for cmd in ["insert", "replace", "delete", "clear"]:
        assert cmd in result.output


def test_execute_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["execute", "--help"])
    assert result.exit_code == 0
    for cmd in ["cell", "run-all", "insert-and-run", "restart", "interrupt"]:
        assert cmd in result.output


def test_jobs_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["jobs", "--help"])
    assert result.exit_code == 0
    for cmd in ["get", "list", "await", "send-stdin"]:
        assert cmd in result.output


def test_runtime_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["runtime", "--help"])
    assert result.exit_code == 0
    for cmd in ["status", "open", "stop", "recover"]:
        assert cmd in result.output


def test_checkpoints_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["checkpoints", "--help"])
    assert result.exit_code == 0
    for cmd in ["create", "list", "restore", "delete"]:
        assert cmd in result.output


def test_workspace_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "--help"])
    assert result.exit_code == 0
    for cmd in ["open", "list"]:
        assert cmd in result.output


def test_setup_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "--help"])
    assert result.exit_code == 0
    for cmd in ["doctor", "mcp-status"]:
        assert cmd in result.output


def test_setup_mcp_status():
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "mcp-status"])
    assert result.exit_code == 0
    assert "MCP server" in result.output
