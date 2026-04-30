# Example Content

Sample posts demonstrating the PostPilot content-as-code format.

## Directory Structure

```
content/examples/
└── week-2025-01-06-to-2025-01-12/
    └── 2025-01-06/
        ├── 09-00.md   ← publishes at 9:00 AM
        ├── 12-00.md   ← publishes at 12:00 PM
        └── 15-00.md   ← publishes at 3:00 PM
```

## How It Works

- **Filename** = time slot (HH-MM format, 24h clock)
- **Parent directory** = date (YYYY-MM-DD)
- **Week folder** = weekly content batch

Write your post as plain Markdown. Run `export-txt` to generate the `.txt` that gets published.

## Try It

```bash
python3 cli.py export-txt threads --content-root content/examples
python3 cli.py build-queue threads --content-root content/examples/week-2025-01-06-to-2025-01-12
```
