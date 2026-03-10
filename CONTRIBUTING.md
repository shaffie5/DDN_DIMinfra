# Contributing

This guide explains how to commit changes to the repository.

## Prerequisites

- [Git](https://git-scm.com/) installed locally
- [Python 3](https://www.python.org/) with `pip`
- A GitHub account with access to this repository

## Setup

1. **Clone the repository**

   ```bash
   git clone https://github.com/shaffie5/DDN_DIMinfra.git
   cd DDN_DIMinfra
   ```

2. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv .venv
   # Linux / macOS
   source .venv/bin/activate
   # Windows PowerShell
   .\.venv\Scripts\Activate.ps1

   pip install -r requirements.txt
   ```

## Making Changes

1. **Create a branch**

   Always work on a feature or fix branch, not directly on `main`.

   ```bash
   git checkout -b your-branch-name
   ```

   Use a short, descriptive name such as `fix/excel-export-header` or `feature/add-email-validation`.

2. **Make your changes**

   Edit the relevant files. Run the app locally to verify:

   ```bash
   streamlit run app.py
   ```

3. **Stage your changes**

   ```bash
   # Stage specific files
   git add app.py excel_export.py

   # Or stage everything
   git add .
   ```

4. **Commit**

   Write a clear, concise commit message describing *what* changed and *why*.

   ```bash
   git commit -m "Fix signature header row in Excel export"
   ```

5. **Push your branch**

   ```bash
   git push origin your-branch-name
   ```

## Opening a Pull Request

1. Go to the repository on GitHub: <https://github.com/shaffie5/DDN_DIMinfra>
2. Click **Compare & pull request** for your branch.
3. Fill in a title and description explaining the change.
4. Request a review from a team member if applicable.
5. Once approved, merge the pull request.

## Tips

- **Pull before you push** — run `git pull origin main` and rebase or merge to keep your branch up to date.
- **Keep commits focused** — each commit should represent one logical change.
- **Test locally** before pushing by running `streamlit run app.py` and verifying the affected functionality.
