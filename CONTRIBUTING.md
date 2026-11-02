# Contributing to Freezeframe

Thanks for helping. Freezeframe is small on purpose: plain Python, no web framework, and a dashboard in vanilla JavaScript.

## Setting up

```bash
git clone https://github.com/atharv146/freezeframe
cd freezeframe
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,menubar]'
.venv/bin/python -m pytest
.venv/bin/freezeframe open --demo     # the dashboard with a made-up recording
```

## Good first contributions

- **Describe an app.** Add an entry to `KNOWN` in `src/freezeframe/knowledge.py`: what it is, what's normal for it, and what to do when it misbehaves, in words a non-technical person would understand. Use `_app`, `_bg` or `_s` for apps you use, background helpers, and parts of the OS.
- **Windows and Linux.** Heat and throttling aren't detected there yet, and Linux has no app hang reports. `platforms/` is where OS-specific code lives.
- **Hardware details.** `hardware.py` has the least testing on Windows and Linux. A run of `freezeframe computer` on your machine, with anything wrong or missing, makes a useful issue.

## Ground rules

- Keep it private: nothing leaves the computer unless the person turns it on (as with the optional AI descriptions).
- Write for people who aren't technical. If a sentence needs jargon, explain the jargon.
- Add a test for new behaviour; `tests/conftest.py` has a demo recording you can query.
