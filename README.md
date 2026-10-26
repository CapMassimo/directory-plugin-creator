# SharePoint directory search plugin template

This repository helps you make a search plugin for **one folder synced from a SharePoint site's default Documents library**. It reads the local synced files, builds small search cards in that folder, and generates a plugin that finds those cards through the Microsoft SharePoint connection. The plugin does not run a server or open original files during a search.

The example settings and test files are fictional. Your own files, extracted text, and model checkpoints stay outside this repository in the `state_dir` you choose. The published cards can contain summaries and tags, so review them and their SharePoint permissions before sharing the plugin.

## Before you start

- Sync the complete target folder with OneDrive and confirm it is up to date. The scanner will read online-only files as needed. Use a folder in the site's **default Documents library**; `source_folder` is its path relative to that library's root.
- Install Python and the packages in `requirements.txt` (`python -m pip install -r requirements.txt`). The scanner covers text, HTML, email, PDF, Word, PowerPoint, and several Excel formats. Unsupported or unreadable files remain listed with a named exception.
- Install and sign in to the Codex CLI for text and image analysis. If your network needs a trusted corporate certificate bundle, use `--ca-certificate PATH` with the enrichment and vision commands. Do not disable TLS verification.
- Connect the Microsoft SharePoint app in the plugin's destination account. Users need access to the index cards and to any source files they open; installing this plugin grants neither permission.

## 1. Describe your directory

Copy `config.example.json` to `config.json` and edit it. `config.json` is ignored by Git.

| Setting | What to enter |
| --- | --- |
| `source_root` | Absolute local path to the synced folder you want to search. |
| `state_dir` | Absolute **local, unsynced** path for full extracted text and checkpoints; keep it outside `source_root`. |
| `site_url` | HTTPS URL of the SharePoint site that owns the folder. |
| `library_path` | URL path of that site's default Documents library, usually `Shared Documents`. |
| `source_folder` | Folder path relative to the library root, such as `Projects/Current`; use `""` for the whole library. |
| `index_folder` | Name of the generated index folder inside `source_root`. The scanner excludes it from later scans. |
| `freshness_hours` | Age after which the plugin warns that the catalog may be stale. |
| `plugin` | Local plugin name, display name, description, and developer name. |
| `custom_fields` | Optional subject-specific fields. Give each a `key`, reader-friendly `label`, and evidence-based `guidance`. |

Core search fields are summary, key terms, likely user intents, use cases, confidence, and artifact type. Custom fields are lists of short values. For a project folder, `project_code` and `workstream` may help; for another subject, choose fields that people actually search for. The model leaves a field empty when the file does not support it. Identifiers help discovery, but this template does not promise exact-code lookup.

Keep personal details and confidential names out of the shared index. If you change custom field definitions later, rerun the scan and analysis before publishing; the old analyses will no longer satisfy validation. If you change the source path, site, library, subfolder, or index folder, choose a new `state_dir`.

## 2. Generate the plugin

```powershell
python setup.py --config config.json
```

This creates `plugin/plugin.json`, a search skill, plugin instructions, acceptance prompts, and `.agents/plugins/marketplace.json`. These files are generated from `config.json`; rerun setup after changing plugin or SharePoint settings. Edit the configuration and template instructions rather than the generated copies. The package follows [OpenAI's portable plugin layout](https://developers.openai.com/plugins/build/plugins).

## 3. Build the index

Run these commands from this repository. A large first scan or analysis may take several runs. The model runners save checkpoints and report how much remains.

```powershell
python directory_index.py --config config.json scan
python directory_vision.py --config config.json
python directory_enrich.py --config config.json
python directory_index.py --config config.json pending
python directory_index.py --config config.json validate
python directory_index.py --config config.json publish
```

Repeat the image and text commands until they report zero remaining and `pending` prints `[]`. For large text backfills, `python directory_continue.py --config config.json` runs the text command in bounded, resumable batches. `validate` requires every readable file to have an analysis; named extraction exceptions are allowed. `publish` checks that the scanned files are still in place, then writes `search_index.yaml`, `cards/`, `terms/`, and `catalog-status.txt` inside the configured index folder. It updates status last so the plugin can spot an interrupted publication. Wait for OneDrive to sync the published files before using the plugin.

If a OneDrive placeholder cannot be opened, save a verified copy of the same file from SharePoint locally, then use `directory_recover.py --config config.json --relative-path "path/in/source/folder.docx" --copy-from "C:/path/to/copy.docx" --remote-modified "2026-01-01T12:00:00Z"`. The helper checks the SharePoint modification time, size, Office integrity, and the unchanged local placeholder before caching the copy. Then rerun `scan`. Keep downloaded copies and the state directory in an approved local location.

## 4. Install and check the plugin

The generated marketplace is at `.agents/plugins/marketplace.json` and points to `./plugin`. Add this repository as a local plugin marketplace, restart the ChatGPT desktop app, install the generated plugin, and ensure Microsoft SharePoint is connected. Follow the [local installation steps](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually) for your client.

Run the prompts in `plugin/skills/find-directory-files/references/test-cases.md`. Confirm that searches stay inside the index cards folder, term-shard lookup works when scoped SharePoint search returns nothing, and every offered file and folder link is visible to the current user. Review a few cards for accuracy and sensitive content. SharePoint access to the index folder and source files is managed separately.

## Keeping it current

After files change, run `scan`, both model runners, `pending`, `validate`, and `publish` again. Do not run the two model runners at the same time. If a published path disappeared or moved, first verify the old SharePoint location is gone. Then create a JSON file with the current state manifest's `scan_id` and its exact removed-path list:

```json
{"scan_id":"value-from-manifest","removed_paths":["Old Folder/file.docx"]}
```

Pass it to `publish --confirm-removals confirmation.json`. Publication stops if removals are unverified, a file changed after scanning, or analysis is incomplete. The last published catalog remains in place.

To automate maintenance, use the [scheduled maintenance runbook](docs/MAINTENANCE.md). It includes a ready-to-use task request and the timeout and checkpoint procedure. Set the task up after filling in `config.json`; each user chooses their own schedule. Check its first runs in the desktop app's **Scheduled** view. The repository itself does not create a scheduled task.

## If the maintainer changes

Follow the [ownership transfer guide](docs/OWNERSHIP_TRANSFER.md), even if the former maintainer's account and computer are gone. The source files and published catalog are in SharePoint; local extracted text and checkpoints can be rebuilt. A new maintainer needs their own SharePoint access, local sync, configuration, Codex sign-in, plugin installation, and scheduled task. If an existing catalog is still present, run `python directory_handoff.py --config config.json` in the new environment before publishing. It saves a local snapshot and imports the previous file list so removals still require verification. Keep the [handoff record](docs/HANDOFF_RECORD.example.md) in approved team storage outside this public repository.

## Run the offline checks

```powershell
python -m unittest discover -v
```

The tests use temporary fictional directories and mocked model responses. They do not need a SharePoint account or publish anything remotely.
