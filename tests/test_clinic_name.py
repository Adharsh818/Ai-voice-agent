"""
Another clinic sets CLINIC_NAME / CLINIC_SHORT_NAME in .env and Emma says its
name everywhere: greetings, "who are you?", wrong numbers, recovery calls, the
model's instructions and the web pages. (Built at import, so each check runs
in a fresh interpreter.)
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHECK = r"""
import json, pathlib
import config, prompts, ai_engine, pages
spoken = list(config.GREETINGS) + list(config.TIME_GREETINGS.values()) + list(config.DEEPGRAM_KEYTERMS[:1])
for line in ("capability.who", "wrong_number"):
    spoken += list(prompts.VARIANTS[line])
page = pages.html(pathlib.Path("static/dashboard/index.html")).body.decode("utf-8")
print(json.dumps({"spoken": spoken, "nlu": ai_engine.EMMA_NLU_SYSTEM_PROMPT[:200], "page": page[:2000],
                  "cid": config.TELEPHONY_CLINIC_CID}))
"""


def run(env_extra: dict) -> dict:
    import json
    env = dict(os.environ, **env_extra)
    out = subprocess.run([sys.executable, "-c", CHECK], cwd=ROOT, env=env, capture_output=True, text=True,
                         timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


class ClinicNameTests(unittest.TestCase):
    def test_another_clinic_never_hears_pearl_dental(self):
        got = run({"CLINIC_NAME": "Smile Care Dental Clinic", "CLINIC_SHORT_NAME": "Smile Care",
                   "CLINIC_CITY": "Mysuru"})
        for line in got["spoken"]:
            self.assertNotIn("Pearl", line)
        self.assertTrue(all("Smile Care" in g for g in got["spoken"][:7]))
        self.assertIn("Smile Care Dental Clinic in Mysuru", got["nlu"])
        self.assertIn("Smile Care", got["page"])
        self.assertNotIn("Pearl", got["page"])

    def test_default_is_unchanged(self):
        got = run({})
        self.assertIn("Emma at Pearl Dental", got["spoken"][0])
        self.assertIn("Pearl Dental Clinic in Bengaluru", got["nlu"])


if __name__ == "__main__":
    unittest.main()
