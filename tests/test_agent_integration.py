"""Integration tests: local LLM + OpenRouter (easy/medium/hard) + planner reasoning."""

import platform

import pytest
from _test_support import (
    HARD_MAX_REPLANS,
    HARD_MAX_STEPS,
    HARD_MAX_TASKS,
    MED_MAX_REPLANS,
    MED_MAX_STEPS,
    MED_MAX_TASKS,
    assert_command_output,
    assert_executable_output,
    assert_file,
    int_run,
    log,
    or_run,
)
from conftest import skip_no_llm, skip_no_openrouter

pytestmark = pytest.mark.live_llm

# --- Local integration tests (require llama-server on :8080) ---


@skip_no_llm
class TestIntegration:
    """Live LLM tests. Slow (~30-90s each at ~3 tok/s). Require llama-server on :8080."""

    def test_create_and_read_file(self, tmp_path):
        """LLM creates a file and reads it back."""
        result = int_run(
            f"Create a file called hello.txt in {tmp_path} containing 'hello world', then read it to verify.",
            str(tmp_path),
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "hello.txt", "hello")

    def test_shell_and_write(self, tmp_path):
        """LLM runs a shell command and writes output to a file."""
        result = int_run(f"Run 'uname -s' and write its output to {tmp_path}/os.txt", str(tmp_path))
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "os.txt", platform.system())

    def test_multi_step_build(self, tmp_path):
        """LLM creates a C file, compiles it, and runs it."""
        result = int_run(
            f"In {tmp_path}: create main.c that prints 'AGENT_OK', compile with cc -o main main.c, run ./main",
            str(tmp_path),
        )
        state = result["state"]
        assert_file(tmp_path / "main.c", "AGENT_OK")
        assert result["status"] == "complete", f"Agent failed. Errors: {state['errors']}"
        assert_executable_output(tmp_path / "main", "AGENT_OK")


@skip_no_llm
class TestIntegrationMedium:
    """Medium difficulty: LLM must recover from errors within a task (0 replans expected)."""

    def test_fix_python_syntax_error(self, tmp_path):
        """LLM writes a broken Python file, runs it (fails), fixes it, runs again."""
        broken = tmp_path / "greet.py"
        broken.write_text('print("hello"\n')
        result = int_run(
            "Run python3 greet.py — it has a syntax error. Fix the error in greet.py and run it again successfully.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS,
            max_tasks=MED_MAX_TASKS,
            max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", (
            f"Agent failed to self-correct. Errors: {result['state']['errors']}"
        )
        fixed_text = broken.read_text()
        assert "print" in fixed_text, f"File was overwritten unexpectedly: {fixed_text[:200]}"
        assert_command_output(["python3", "greet.py"], tmp_path, "hello")

    def test_fix_missing_include(self, tmp_path):
        """LLM compiles a C file missing #include <stdio.h>, fixes it, compiles again."""
        broken_c = tmp_path / "fix_me.c"
        broken_c.write_text('int main() { printf("FIXED\\n"); return 0; }\n')
        result = int_run(
            f"Compile {broken_c} with 'cc -o {tmp_path}/fix_me {broken_c}'. "
            f"It will fail because stdio.h is not included. "
            f"Read the error, add '#include <stdio.h>' to {broken_c}, compile again, then run {tmp_path}/fix_me.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS,
            max_tasks=MED_MAX_TASKS,
            max_steps=MED_MAX_STEPS,
        )
        fixed_text = broken_c.read_text()
        assert "stdio.h" in fixed_text, (
            f"Expected #include <stdio.h> in fixed file, got: {fixed_text[:200]}"
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_executable_output(tmp_path / "fix_me", "FIXED")

    def test_create_missing_file_then_use(self, tmp_path):
        """LLM tries to read a non-existent file, then creates and reads it."""
        result = int_run(
            f"Create a file called data.txt containing 'RECOVERED' in {tmp_path}, then read it to verify the content.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS,
            max_tasks=MED_MAX_TASKS,
            max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "data.txt", "RECOVERED")


@skip_no_llm
class TestIntegrationHard:
    """Hard difficulty: LLM must fail a task and replan to succeed."""

    def test_replan_build_with_dependency(self, tmp_path):
        """First plan fails because a header file is missing. Replan creates it first."""
        result = int_run(
            f"In {tmp_path}: compile and run a C program. "
            f"The program main.c should '#include \"msg.h\"' and call 'printf(\"%s\\n\", MSG);'. "
            f"The header msg.h should '#define MSG \"REPLAN_OK\"'. "
            f"Compile with 'cc -o {tmp_path}/main {tmp_path}/main.c', then run {tmp_path}/main.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "main.c", "msg.h")
        assert_file(tmp_path / "msg.h", "REPLAN_OK")
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_executable_output(tmp_path / "main", "REPLAN_OK")

    def test_replan_fix_wrong_command(self, tmp_path):
        """Agent tries a command that doesn't exist, replans with the correct approach."""
        result = int_run(
            f"In {tmp_path}: get the current date and save it to {tmp_path}/today.txt. "
            f"First try using the command 'datex' (which doesn't exist). "
            f"When that fails, replan and use the correct 'date' command instead.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "today.txt")
        text = (tmp_path / "today.txt").read_text()
        assert len(text.strip()) > 0, "today.txt is empty"
        plan_events = [e for e in result["log"] if e["event"] == "plan"]
        if len(plan_events) >= 2:
            log(f"VERIFIED: {len(plan_events)} plan attempts (replan exercised)")

    def test_replan_multi_step_recovery(self, tmp_path):
        """Complex task: write Python script, run it (it imports a missing module),
        replan to install/fix the dependency, run again."""
        script = tmp_path / "app.py"
        script.write_text(
            "import json\n"
            'with open("config.json") as f:\n'
            "    cfg = json.load(f)\n"
            'print("APP_" + cfg["status"])\n'
        )
        result = int_run(
            f"Run 'python3 {script}'. It will fail because config.json doesn't exist in {tmp_path}. "
            f'Create {tmp_path}/config.json with content \'{{"status": "SUCCESS"}}\', then run the script again.',
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "config.json", "SUCCESS")
        if result["status"] == "complete":
            all_outputs = " ".join(
                s.get("output", "") for s in result["state"].get("last_steps", [])
            )
            assert "APP_SUCCESS" in all_outputs or len(result["state"]["completed_tasks"]) >= 2, (
                f"Expected APP_SUCCESS in output. Completed: {result['state']['completed_tasks']}"
            )


# --- OpenRouter integration tests (gemma-4-26b-a4b via Parasail/bf16) ---


@skip_no_openrouter
class TestOpenRouterEasy:
    """Easy tests via OpenRouter (gemma-4-26b-a4b)."""

    def test_create_and_read_file(self, tmp_path):
        result = or_run(
            f"Create a file called hello.txt in {tmp_path} containing 'hello world', then read it to verify.",
            str(tmp_path),
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "hello.txt", "hello")

    def test_shell_and_write(self, tmp_path):
        result = or_run(f"Run 'uname -s' and write its output to {tmp_path}/os.txt", str(tmp_path))
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "os.txt")

    def test_multi_step_build(self, tmp_path):
        result = or_run(
            f"In {tmp_path}: create main.c that prints 'AGENT_OK', compile with cc -o main main.c, run ./main",
            str(tmp_path),
        )
        assert_file(tmp_path / "main.c", "AGENT_OK")
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_executable_output(tmp_path / "main", "AGENT_OK")


@skip_no_openrouter
class TestOpenRouterMedium:
    """Medium tests via OpenRouter. Tests error recovery + done emission."""

    def test_fix_python_syntax_error(self, tmp_path):
        broken = tmp_path / "greet.py"
        broken.write_text('print("hello"\n')
        result = or_run(
            "Run python3 greet.py — it has a syntax error. Fix the error in greet.py and run it again successfully.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS,
            max_tasks=MED_MAX_TASKS,
            max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        fixed_text = broken.read_text()
        assert "print" in fixed_text
        assert_command_output(["python3", "greet.py"], tmp_path, "hello")

    def test_fix_missing_include(self, tmp_path):
        broken_c = tmp_path / "fix_me.c"
        broken_c.write_text('int main() { printf("FIXED\\n"); return 0; }\n')
        result = or_run(
            f"Compile {broken_c} with 'cc -o {tmp_path}/fix_me {broken_c}'. "
            f"It will fail because stdio.h is not included. "
            f"Read the error, add '#include <stdio.h>' to {broken_c}, compile again, then run {tmp_path}/fix_me.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS,
            max_tasks=MED_MAX_TASKS,
            max_steps=MED_MAX_STEPS,
        )
        fixed_text = broken_c.read_text()
        assert "stdio.h" in fixed_text
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_executable_output(tmp_path / "fix_me", "FIXED")

    def test_create_missing_file_then_use(self, tmp_path):
        result = or_run(
            f"Create a file called data.txt containing 'RECOVERED' in {tmp_path}, then read it to verify the content.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS,
            max_tasks=MED_MAX_TASKS,
            max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_file(tmp_path / "data.txt", "RECOVERED")


@skip_no_openrouter
class TestOpenRouterHard:
    """Hard tests via OpenRouter. Tests replanning."""

    def test_replan_build_with_dependency(self, tmp_path):
        result = or_run(
            f"In {tmp_path}: compile and run a C program. "
            f"The program main.c should '#include \"msg.h\"' and call 'printf(\"%s\\n\", MSG);'. "
            f"The header msg.h should '#define MSG \"REPLAN_OK\"'. "
            f"Compile with 'cc -o {tmp_path}/main {tmp_path}/main.c', then run {tmp_path}/main.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "main.c", "msg.h")
        assert_file(tmp_path / "msg.h", "REPLAN_OK")
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        assert_executable_output(tmp_path / "main", "REPLAN_OK")

    def test_replan_fix_wrong_command(self, tmp_path):
        or_run(
            f"In {tmp_path}: get the current date and save it to {tmp_path}/today.txt. "
            f"First try using the command 'datex' (which doesn't exist). "
            f"When that fails, replan and use the correct 'date' command instead.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "today.txt")
        text = (tmp_path / "today.txt").read_text()
        assert len(text.strip()) > 0

    def test_replan_multi_step_recovery(self, tmp_path):
        script = tmp_path / "app.py"
        script.write_text(
            "import json\n"
            'with open("config.json") as f:\n'
            "    cfg = json.load(f)\n"
            'print("APP_" + cfg["status"])\n'
        )
        or_run(
            f"Run 'python3 {script}'. It will fail because config.json doesn't exist in {tmp_path}. "
            f'Create {tmp_path}/config.json with content \'{{"status": "SUCCESS"}}\', then run the script again.',
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "config.json", "SUCCESS")


# --- Planner reasoning integration tests ---


@skip_no_openrouter
class TestPlannerReasoningOpenRouter:
    """Planner reasoning integration tests via OpenRouter (gemma-4-26b-a4b)."""

    def test_build_with_dependency_fewer_replans(self, tmp_path):
        """Build with header dependency -- planner reasoning should reduce replans to <=1."""
        result = or_run(
            f"In {tmp_path}: compile and run a C program. "
            f"The program main.c should '#include \"msg.h\"' and call 'printf(\"%s\\n\", MSG);'. "
            f"The header msg.h should '#define MSG \"REPLAN_OK\"'. "
            f"Compile with 'cc -o {tmp_path}/main {tmp_path}/main.c', then run {tmp_path}/main.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "main.c", "msg.h")
        assert_file(tmp_path / "msg.h", "REPLAN_OK")
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        plan_events = [e for e in result["log"] if e["event"] == "plan"]
        replans = len(plan_events) - 1
        assert replans <= 1, f"Expected <=1 replans with planner reasoning, got {replans}"

    def test_plan_specificity(self, tmp_path):
        """Planner reasoning should produce task descriptions mentioning key dependencies."""
        result = or_run(
            f"In {tmp_path}: compile and run a C program. "
            f"The program main.c should '#include <stdio.h>' and '#include \"msg.h\"' "
            f"and call 'printf(\"%s\\n\", MSG);'. "
            f"The header msg.h should '#define MSG \"SPEC_OK\"'. "
            f"Compile with 'cc -o {tmp_path}/main {tmp_path}/main.c', then run {tmp_path}/main.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        plan_events = [e for e in result["log"] if e["event"] == "plan"]
        first_plan_tasks = plan_events[0]["tasks"]
        all_task_text = " ".join(first_plan_tasks).lower()
        has_specifics = any(
            kw in all_task_text for kw in ["stdio", "include", "msg.h", "header", "define"]
        )
        assert has_specifics, (
            f"Expected task descriptions to mention dependencies, got: {first_plan_tasks}"
        )


@skip_no_llm
class TestPlannerReasoningIntegration:
    """Planner reasoning integration tests (local, requires llama-server on :8080)."""

    def test_build_with_dependency_fewer_replans(self, tmp_path):
        """Build with header dependency -- planner reasoning should reduce replans to <=1."""
        result = int_run(
            f"In {tmp_path}: compile and run a C program. "
            f"The program main.c should '#include \"msg.h\"' and call 'printf(\"%s\\n\", MSG);'. "
            f"The header msg.h should '#define MSG \"REPLAN_OK\"'. "
            f"Compile with 'cc -o {tmp_path}/main {tmp_path}/main.c', then run {tmp_path}/main.",
            str(tmp_path),
            max_replans=HARD_MAX_REPLANS,
            max_tasks=HARD_MAX_TASKS,
            max_steps=HARD_MAX_STEPS,
        )
        assert_file(tmp_path / "main.c", "msg.h")
        assert_file(tmp_path / "msg.h", "REPLAN_OK")
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        plan_events = [e for e in result["log"] if e["event"] == "plan"]
        replans = len(plan_events) - 1
        assert replans <= 1, f"Expected <=1 replans with planner reasoning, got {replans}"

    def test_no_overlapping_tasks(self, tmp_path):
        """Fix python syntax -- planner reasoning should avoid redundant tasks."""
        broken = tmp_path / "greet.py"
        broken.write_text('print("hello"\n')
        result = int_run(
            "Run python3 greet.py — it has a syntax error. Fix the error in greet.py and run it again successfully.",
            str(tmp_path),
            max_replans=MED_MAX_REPLANS,
            max_tasks=MED_MAX_TASKS,
            max_steps=MED_MAX_STEPS,
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        plan_events = [e for e in result["log"] if e["event"] == "plan"]
        first_plan_tasks = plan_events[0]["tasks"]
        assert len(first_plan_tasks) <= 3, (
            f"Expected <=3 tasks, got {len(first_plan_tasks)}: {first_plan_tasks}"
        )
        fix_tasks = [
            t
            for t in first_plan_tasks
            if any(kw in t.lower() for kw in ["fix", "repair", "correct", "syntax error"])
        ]
        assert len(fix_tasks) <= 1, (
            f"Expected at most 1 fix/repair task, got {len(fix_tasks)}: {fix_tasks}"
        )

    def test_plan_uses_relative_paths(self, tmp_path):
        """Planner should use relative filenames, not absolute paths in task descriptions."""
        result = int_run(
            f"In {tmp_path}: create a file called output.txt containing 'hello', then read it.",
            str(tmp_path),
        )
        assert result["status"] == "complete", f"Agent failed. Errors: {result['state']['errors']}"
        plan_events = [e for e in result["log"] if e["event"] == "plan"]
        first_plan_tasks = plan_events[0]["tasks"]
        all_task_text = " ".join(first_plan_tasks)
        assert str(tmp_path) not in all_task_text, (
            f"Expected relative paths in task descriptions, got absolute: {first_plan_tasks}"
        )
