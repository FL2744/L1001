# L1001 Translator

`L1001.py` translates long `.txt`, `.md`, `.markdown`, or `.docx` works and
produces a standalone HTML document. It processes the source in chunks, keeps
continuity information between chunks, retries temporary API failures, and
saves checkpoints so interrupted translations can be resumed.

## First-time setup on macOS

Open Terminal and run these commands in the project directory one at a time:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements_long_work_translator_html.txt
```

The `.venv` folder is a private Python environment for this translator. It
prevents package-installation errors from macOS or Homebrew's protected system
Python.

## API key

The easiest option is to run the program and answer **yes** when it asks whether
you want to enter an API key. The key is hidden while you type or paste it.

Alternatively, set the key for the current Terminal window:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

Or save it in a file named `.env` in your home directory:

```text
OPENAI_API_KEY=your-api-key-here
```

Do not share the API key or put it inside `L1001.py`.

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

For each new Terminal session, in your project directory:

```bash
source .venv/bin/activate
python3 L1001.py
```

If you use the `export OPENAI_API_KEY=...` method, repeat that command in each
new Terminal session before running the script. The interactive-entry and
`~/.env` methods do not require that export command.

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
