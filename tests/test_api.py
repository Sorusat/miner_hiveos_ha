"""End-to-end test of the digest client against a fake miner.

Run: python tests/test_api.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# api.py must be importable without Home Assistant installed, so stub the
# package and load it by path instead of importing custom_components, whose
# __init__ pulls in homeassistant.
import importlib.util  # noqa: E402
import types  # noqa: E402


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pkg = types.ModuleType("hiveos_miner")
pkg.__path__ = [str(ROOT / "custom_components" / "hiveos_miner")]
sys.modules["hiveos_miner"] = pkg

_load("hiveos_miner.const", ROOT / "custom_components" / "hiveos_miner" / "const.py")
api_module = _load(
    "hiveos_miner.api", ROOT / "custom_components" / "hiveos_miner" / "api.py"
)

HiveosMinerApi = api_module.HiveosMinerApi
HiveosMinerError = api_module.HiveosMinerError
derive_state = api_module.derive_state
parse_miner_status = api_module.parse_miner_status

# sensor.py only needs the formatting helper, not the whole HA entity stack.
import re as _re  # noqa: E402

_source = (ROOT / "custom_components" / "hiveos_miner" / "sensor.py").read_text(
    encoding="utf-8"
)
_match = _re.search(
    r"def _format_uptime\(.*?\n(?=\n\ndef )", _source, _re.S
)
_namespace: dict = {}
exec(compile(_match.group(0), "sensor.py", "exec"), _namespace)
format_uptime = _namespace["_format_uptime"]

from fake_miner import FULL_STATUS, STATE, ZEROED_STATUS, FakeMiner  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got: object, want: object) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        FAILURES.append(name)


async def main() -> int:
    with FakeMiner() as miner:
        host = miner.host
        api = HiveosMinerApi(host, "root", "root")

        print("digest authentication")
        check("validate accepts correct credentials", await api.async_validate(), True)

        print("\nstatus parsing")
        raw = await api.async_get("/cgi-bin/get_miner_status.cgi")
        parsed = parse_miner_status(raw, "bmminer ready")
        check("state", parsed["state"], "mining")
        check("hashrate", parsed["hashrate"], 9697.69)
        check("temp_max", parsed["temp_max"], 53.0)
        check("power", parsed["power"], 1599.0)
        check("boards", parsed["boards_alive"], 3)
        check("version", parsed["miner_version"], "49.0.1.3")
        check("pools filtered", len(parsed["pools"]), 1)
        await api.close()

        print("\nstart / resume / stop")
        api = HiveosMinerApi(host, "root", "root")
        await api.async_start()
        check("payload zeroed after start", await api.async_get("/cgi-bin/get_miner_status.cgi"), ZEROED_STATUS)
        check("state zeroed + no latch", parse_miner_status(ZEROED_STATUS, "")["state"], "stopped")
        check("state zeroed + latch", parse_miner_status(ZEROED_STATUS, "", True)["state"], "starting")

        await api.async_stop()
        check(
            "stop shows suspended in log",
            parse_miner_status(await api.async_get("/cgi-bin/get_miner_status.cgi"), STATE["log"])["state"],
            "suspended",
        )
        await api.close()

        print("\nwrong credentials are rejected")
        bad = HiveosMinerApi(host, "root", "wrong")
        try:
            await bad.async_validate()
            check("raises on bad password", "no exception", "HiveosMinerError")
        except HiveosMinerError:
            check("raises on bad password", "HiveosMinerError", "HiveosMinerError")
        await bad.close()

        print("\nunreachable miner fails fast, does not hang")
        dead = HiveosMinerApi("127.0.0.1:9", "root", "root")
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            await dead.async_validate()
            check("raises on refused connection", "no exception", "HiveosMinerError")
        except HiveosMinerError:
            check("raises on refused connection", "HiveosMinerError", "HiveosMinerError")
        elapsed = loop.time() - started
        check("fails under 12s", elapsed < 12, True)
        await dead.close()

    print("\nstate matrix")
    for label, data, log, latch, want in [
        ("resume, log silent", ZEROED_STATUS, "", True, "starting"),
        ("log says Starting", ZEROED_STATUS, "Starting...", False, "starting"),
        ("log says SUSPENDED", FULL_STATUS, "INFO SUSPENDED", False, "suspended"),
        ("mining", FULL_STATUS, "", False, "mining"),
        ("powered off", ZEROED_STATUS, "", False, "stopped"),
    ]:
        got = parse_miner_status(data, log, latch)["state"]
        check(label, got, want)
    check("derive_state is pure", derive_state(5000.0, "", False), "mining")

    print("\nuptime formatting")
    for minutes, want in [
        (0, "0 мин"),
        (5, "5 мин"),
        (59, "59 мин"),
        (60, "1 ч 0 мин"),
        (4852, "3 дн 8 ч 52 мин"),
        (1440, "1 дн 0 ч 0 мин"),
        (None, None),
    ]:
        check(f"{minutes} min", format_uptime(minutes), want)

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))