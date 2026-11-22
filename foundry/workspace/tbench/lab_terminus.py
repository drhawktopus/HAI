"""LabTerminus: Terminus-2 exactly as published, plus ERRANDS (lab_errands.py). Loaded by Harbor with
`--agent-import-path lab_terminus:LabTerminus`; the helper's model is reached through a second relay
(`helper_api_base`) whose items go to the helper's model-window order. Changed: the prompt template gains one
paragraph (lab_errands.LEAD_ADDENDUM, before "Task Description:"); answers may carry "errands"; finished reports are
appended to the lead's next terminal output. Nothing else in Terminus's prompt or loop changes.
"""
import asyncio
import json
import time

from harbor.agents.terminus_2.terminus_2 import Terminus2, Terminus2Options
from harbor.llms.lite_llm import LiteLLM

import lab_errands as E


class LabTerminusOptions(Terminus2Options):
    """Terminus-2's options plus the helper's endpoint (Harbor validates --ak against this schema)."""
    helper_api_base: str | None = None
    helper_ctx: int | None = None
    errand_mode: str | None = None      # "look" (default, the A4 arm) | "commands" (B8: + RUN in a private copy, FETCH)


class LabTerminus(Terminus2):
    options_model = LabTerminusOptions
    @staticmethod
    def name() -> str:
        return "lab-terminus"

    def __init__(self, *args, helper_api_base=None, helper_ctx=None, errand_mode=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._mode = errand_mode or "look"
        assert self._mode in E.MODES, "errand_mode %r is not one of %s" % (self._mode, E.MODES)
        marker = "Task Description:"
        assert marker in self._prompt_template, "Terminus template changed: no %r to place the errand note" % marker
        addendum = E.LEAD_ADDENDUM if self._mode == "look" else E.LEAD_ADDENDUM_COMMANDS
        note = addendum.strip().replace("{", "{{").replace("}", "}}")     # the template goes through .format()
        self._prompt_template = self._prompt_template.replace(marker, note + "\n\n" + marker, 1)
        info = {"max_input_tokens": int(helper_ctx or 0), "max_output_tokens": int(helper_ctx or 0),
                "input_cost_per_token": 0.0, "output_cost_per_token": 0.0}
        self._helper = LiteLLM(model_name="openai/lab-helper", api_base=helper_api_base,
                               model_info=info if helper_ctx else None)
        self._errands, self._errand_log, self._lab_env, self._n = [], [], None, 0

    async def run(self, instruction, environment, context):
        self._lab_env = environment
        try:
            return await super().run(instruction, environment, context)
        finally:
            for brief, task in self._errands:
                task.cancel()
            try:
                (self.logs_dir / "errands.json").write_text(json.dumps(self._errand_log, indent=1))
            except Exception:                                                # noqa: BLE001 - never raise in cleanup
                pass

    async def _ask_helper(self, msgs):
        history, last = msgs[:-1], msgs[-1]["content"]
        resp = await self._helper.call(prompt=last, message_history=history)
        return resp.content or ""

    async def _look(self, cmd):
        r = await self._lab_env.exec(cmd)
        return ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip() or "(no output)"

    def _tools(self):
        """(run, fetch) for ONE errand: RUN executes in that errand's private copy of the working directory (made on
        its first run, `cp -a` of the container's working directory, so the lead's files are never written); FETCH
        reads one page with the container's curl or wget (the URL was checked by lab_errands.fetch_refusal)."""
        self._n += 1
        copy, made = "/tmp/lab-errand-%d" % self._n, [None]

        async def run(cmd):
            if made[0] is None:     # never copy `/` into itself: a container with no working directory gets no runs
                r = await self._look('d=$(pwd); if [ "$d" = "/" ]; then echo "NOCOPY: the working directory is /"; '
                                     'else mkdir -p %s && cp -a "$d"/. %s/ && echo COPIED; fi' % (copy, copy))
                made[0] = "COPIED" in r
                if not made[0]:
                    self._copy_error = r[:300]
            if not made[0]:
                return "cannot run: the errand's private copy could not be made (%s)" % getattr(self, "_copy_error", "")
            return await self._look("cd %s && %s" % (copy, cmd))

        async def fetch(url):
            return await self._look("curl -sSL '%s' 2>&1 || wget -qO- '%s' 2>&1" % (url, url))
        return run, fetch

    async def _errand(self, brief):
        t0 = time.time()
        try:
            run, fetch = self._tools() if self._mode == "commands" else (None, None)
            rec = await E.run_errand(brief, self._ask_helper, self._look, limit=self._limit_output_length,
                                     mode=self._mode, run=run, fetch=fetch)
        except Exception as e:                                               # noqa: BLE001 - a broken errand is a report
            rec = {"brief": brief, "status": "error", "report": "errand failed: %s" % type(e).__name__,
                   "turns": 0, "looks": 0}
        rec["wall_s"] = round(time.time() - t0, 1)
        self._errand_log.append(rec)
        return rec

    async def _handle_llm_interaction(self, chat, prompt, original_instruction="", session=None):
        out = await super()._handle_llm_interaction(chat, prompt, original_instruction, session)
        for brief in E.briefs_of(out[5].content if out[5] is not None else ""):
            self._errands.append((brief, asyncio.ensure_future(self._errand(brief))))
        return out

    async def _execute_commands(self, commands, session):
        timed_out, output = await super()._execute_commands(commands, session)
        done = [t.result() for b, t in self._errands if t.done()]
        pending = [b for b, t in self._errands if not t.done()]
        self._errands = [(b, t) for b, t in self._errands if not t.done()]
        return timed_out, output + E.reports_block(done, pending)
