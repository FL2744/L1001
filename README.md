# L1001 Long-Work HTML Translator

`L1001.py` translates long `.txt`, `.md`, `.markdown`, or `.docx` works and
produces a standalone HTML document. It processes the source in chunks, keeps
continuity information between chunks, retries temporary API failures, and
saves checkpoints so interrupted translations can be resumed.

## First-time setup on macOS

Open Terminal and run these commands one at a time in your project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements_long_work_translator_html.txt
```

The `.venv` folder is a private Python environment for this translator. It
prevents package-installation errors from macOS or Homebrew's protected system
Python.

## Choose a provider and enter its API key

At startup, the program offers two provider choices:

1. **OpenAI API** — uses the OpenAI Responses API and defaults to
   `gpt-5.4-nano`. Enter an OpenAI Platform API key when prompted.
2. **Virginia Tech ARC LLM API** — uses ARC's OpenAI-compatible Chat
   Completions endpoint at `https://llm-api.arc.vt.edu/api/v1`. Select an ARC
   model and enter your personal ARC API key when prompted.

To create an ARC key, sign in at <https://llm.arc.vt.edu/> and open
**User profile > Settings > Account > API keys**. ARC access is available to
Virginia Tech students, faculty, and staff.

For either provider, key input is hidden. The script keeps the key only in
memory for the current run; it does not print it, write it into the script, or
save it in the checkpoint. Do not share either API key.

## Run the translator

With the virtual environment active, run:

```bash
python3 L1001.py
```

Follow the prompts to select the source file, languages, translation style,
chunk size, provider, and annotation options. A source file can be entered as a
filename when it is in this directory, for example:

```text
preface.txt
```

Otherwise, enter its full path.

Keep the Terminal window open during translation. The HTML output and checkpoint
data are written alongside the source file. If a run is interrupted, run the
same command again and accept the resume option when prompted.

## Normal use after the first setup

For each new Terminal session. In your project directory:

```bash
source .venv/bin/activate
python3 L1001.py
```

Choose a provider and enter its API key each time the script starts.

When finished, you can leave the virtual environment with:

```bash
deactivate
```

## Updating dependencies

If the script later reports a missing or outdated package, activate the virtual
environment and reinstall the requirements in your project directory:

```bash
source .venv/bin/activate
python3 -m pip install --upgrade -r requirements_long_work_translator_html.txt
```

## Common mistake: `Invalid requirement: '"""'`

This happens when the Python script is mistakenly passed to `pip` as a
requirements file:

```bash
# Incorrect
python3 -m pip install -r L1001.py
```

Use the requirements file for installation, then run the Python script:

```bash
python3 -m pip install -r requirements_long_work_translator_html.txt
python3 L1001.py
```
