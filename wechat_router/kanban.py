"""Hermes' public CLI is the only board write boundary."""

import asyncio
import json
import os
import re
import shutil
from dataclasses import replace


class KanbanError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class KanbanClient:
    def __init__(self, cfg, home):
        self.cfg = cfg
        self.home = str(home)
        self.executable = shutil.which(cfg.hermes_bin)
        if not self.executable:
            raise KanbanError("hermes_cli_not_found")

    def for_board(self, board):
        return KanbanClient(replace(self.cfg, board=board), self.home)

    async def _run(self, *args, json_output=False):
        env = {**os.environ, "HERMES_HOME": self.home}
        proc = await asyncio.create_subprocess_exec(
            self.executable, "kanban", "--board", self.cfg.board, *args,
            env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), self.cfg.cli_timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode:
            raise KanbanError("hermes_cli_failed")
        if len(stdout) > 4 * 1024 * 1024:
            raise KanbanError("hermes_cli_output_too_large")
        if json_output:
            try:
                return json.loads(stdout)
            except (ValueError, UnicodeError):
                raise KanbanError("hermes_cli_invalid_json") from None
        return None

    async def check_profile(self, name):
        # `assignees` includes retired assignees from task history; require an on-disk profile.
        from pathlib import Path
        home = Path(self.home)
        root = home.parent.parent if home.parent.name == "profiles" else home
        if name == "default":
            present = (root / "config.yaml").is_file()
        else:
            present = (root / "profiles" / name / "config.yaml").is_file()
        if not present:
            raise KanbanError("assignee_profile_missing")

    async def show(self, task_id):
        if not re.fullmatch(r"t_[A-Za-z0-9_-]+", task_id):
            raise KanbanError("invalid_task_id")
        value = await self._run("show", task_id, "--json", json_output=True)
        if not isinstance(value, dict) or not isinstance(value.get("task"), dict):
            raise KanbanError("hermes_task_invalid")
        return value

    async def create(self, effect, body, key, parent=None):
        await self.check_profile(effect["profile"])
        args = ["create", effect["title"], "--body", body, "--assignee", effect["profile"],
                "--idempotency-key", key, "--created-by", "wechat-router", "--json"]
        if parent:
            args += ["--parent", parent]
        value = await self._run(*args, json_output=True)
        task_id = value.get("id") if isinstance(value, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r"t_[A-Za-z0-9_-]+", task_id):
            raise KanbanError("hermes_create_invalid")
        return task_id

    async def comment_once(self, task_id, marker, body):
        previous = await self.show(task_id)
        if any(marker in c.get("body", "") for c in previous.get("comments", [])):
            return previous
        await self._run("comment", task_id, f"{marker}\n{body}", "--author", "wechat-router")
        return previous
