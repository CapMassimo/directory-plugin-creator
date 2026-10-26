# Transfer ownership without the former operator

Use this guide when the person who built or maintained the plugin has left and their account, computer, chats, local files, and credentials cannot be used. An LLM can perform the inventory, configuration, rebuild, checks, and documentation. A SharePoint or workspace administrator may need to grant access or manage an old workspace installation.

## What has to move

There is no hosted database in this template. The **source files** and the published **search catalog** (`search_index.yaml`, `cards/`, `terms/`, and `catalog-status.txt`) live in a SharePoint Documents library. OneDrive sync gives the maintainer a local copy. `state_dir` holds extracted text, analysis checkpoints, and the previous publication's file list on the maintainer's computer. The generated `plugin/` and `.agents/plugins/marketplace.json` come from the repository and `config.json`. A scheduled task, a Codex CLI sign-in, and a Microsoft SharePoint connection belong to the account that created them; arrange new ones for the successor.

| Item | Successor's action |
| --- | --- |
| SharePoint source and index folder | Obtain access through a current site or library owner; confirm the successor can read source files and write the index folder. Check access separately for intended plugin users. |
| Repository and configuration | Obtain a team-controlled copy of this repository. Recreate `config.json` with the successor's local paths and the same SharePoint location. Generate the plugin again. |
| Published catalog | Keep a verified copy. If it is complete and still at the same SharePoint location, import its file list with `directory_handoff.py` before the successor publishes. |
| Local `state_dir` | Create a new, unsynced directory and rebuild. The old computer and checkpoints are optional audit material, not a dependency. Do not copy a state directory with old absolute paths into a new configuration. |
| Accounts and scheduled work | Sign in with the successor's approved accounts. Connect SharePoint, install or republish the plugin, and create a new scheduled task. Have an administrator retire the old task or publication if it still exists. |

## 1. Recover access and record the facts

1. Ask the SharePoint administrator or current site owner to identify the site, its default Documents library, the source subfolder, and the index folder. The successor needs permission to read the complete source and create, update, and remove files in the index folder. Check whether the index folder has unique permissions and whether users who will search can read its cards and term shards. Use team-managed groups where practical. A SharePoint administrator can manage site membership through **SharePoint admin center → Sites → Active sites → Membership**; site and library permissions may differ. See [Microsoft's site administration guide](https://learn.microsoft.com/sharepoint/get-started-new-admin-center) and [sharing and permissions guide](https://learn.microsoft.com/en-us/sharepoint/modern-experience-sharing-permissions).
2. If the source or published catalog was actually in the former employee's **personal OneDrive**, ask an administrator to preserve and move it into a team-owned SharePoint site before rebuilding. Do this promptly: access and retention depend on the organization's settings. This template does not target a personal OneDrive. See [Microsoft's departed-user OneDrive guidance](https://learn.microsoft.com/en-us/onedrive/retention-and-deletion) and [restoration guidance](https://learn.microsoft.com/en-us/sharepoint/restore-deleted-onedrive).
3. Obtain a team-controlled repository copy and the [handoff record example](HANDOFF_RECORD.example.md). Fill a real copy in an approved team location **outside this public repository**. Record the site URL, library path, source subfolder, index folder, plugin name and display text, custom fields, freshness threshold, schedule and time zone, Python/runtime requirements, SharePoint group owners, and links to the last known catalog and approved backups. Record where the certificate bundle comes from, not its private contents. Do not put credentials, tokens, source documents, or extracted text in the record.
4. If `config.json` is unavailable, read the existing `search_index.yaml` metadata for the site URL, library path, source folder, display name, and custom field definitions. Obtain the index folder and plugin name from the installed plugin or its marketplace, or choose new values. Confirm every value against the live SharePoint site; the catalog could be stale.

**Stop if the source itself is unavailable.** Have the administrator restore it from a permitted backup before indexing. A catalog of summaries cannot reconstruct missing original files. If the old catalog is unavailable, continue with a fresh build below, but state clearly that historical adds and removals cannot be compared with the former catalog.

## 2. Build a new local operating environment

1. On the successor's computer, sync the complete source folder from the shared site. Confirm OneDrive says it is up to date and verify a few known top-level and deep folders through SharePoint. Do not treat an incomplete local sync as deletions.
2. Install this repository, Python and `requirements.txt`, and the signed-in Codex CLI. Configure an approved CA bundle if the network requires one. Use the successor's own SharePoint connection; do not transfer the former user's token, browser profile, certificate private key, or login session.
3. Copy `config.example.json` to ignored `config.json`. Set `source_root` to the successor's synced folder and `state_dir` to a **new local unsynced directory outside the source**. Keep `site_url`, `library_path`, `source_folder`, and `index_folder` aligned with the live site. Copy the old custom field definitions if they are available; changing them causes the model analyses to be rebuilt. Run `python setup.py --config config.json` to regenerate the plugin and marketplace.

## 3. Preserve the old catalog and seed the removal baseline

If a complete catalog is still present in the same SharePoint folder, first fetch its `catalog-status.txt`, one card, and one term shard through SharePoint with the successor's account. Confirm the links work. Then run:

```powershell
python directory_handoff.py --config config.json
```

The helper checks the synced catalog's site and folder identity, generation, file count, cards, and term shards. It copies the current index into `state_dir/handoff_snapshots/<generation_id>/` and writes `published_paths.json` and `last_publish.json` in the new local state. Keep another approved backup of the published catalog in team-controlled storage. The snapshot is local and may contain sensitive summaries; keep it out of the repository. A fresh state **cannot overwrite an existing catalog** until this import succeeds. If the helper reports an incomplete or mismatched catalog, investigate sync and permissions instead of editing the baseline by hand.

If the old catalog cannot be recovered, or the source is moving to a different site or folder, use a **new `state_dir` and new `index_folder`**. Build a complete new catalog there, update `config.json`, regenerate the plugin, and test it before changing users' installation. Keep the old catalog intact if it still exists. Treat the first new scan as a new baseline; do not claim it proves what disappeared while the former owner was absent.

## 4. Rebuild and verify

Follow [the README](../README.md) and [maintenance runbook](MAINTENANCE.md): `scan`, image analysis, text analysis, `pending`, `validate`, then `publish`. A rebuild from a new state may reanalyze every readable file and take many batches. Do not publish a partial catalog or infer content from filenames. Review named extraction exceptions and sparse PDFs awaiting alternate extraction.

If the imported baseline lists paths absent from the new scan, verify **each old path** against live SharePoint. A rename still requires confirmation that its old location is gone. Use the current manifest's `scan_id` and exact verified path list in the `--confirm-removals` JSON described in the README. If any old location is still present or cannot be checked, stop; the old published catalog remains. After publication, wait for sync and compare the new `catalog-status.txt` with the local publication status. Fetch representative cards, term shards, files, and folders using the successor's SharePoint connection and a normal reader account.

## 5. Transfer the plugin and scheduled work

1. Install the newly generated local marketplace and plugin for the successor, then connect Microsoft SharePoint as that user. If the old plugin was shared through a workspace marketplace or published under the former user, ask a workspace administrator to transfer its marketplace source where supported or publish the regenerated package from a team-controlled source. Do not assume an old personal installation or connection will survive account deletion. See [OpenAI's plugin installation and sharing guide](https://developers.openai.com/plugins/build/plugins).
2. Run the generated acceptance prompts. Check scoped search, term-shard fallback, freshness warning, custom fields, live source-link verification, and access for a normal user. The plugin package does not grant SharePoint rights; check source and catalog permissions separately.
3. Use [scheduled maintenance](MAINTENANCE.md) to create a **new task** in the successor's local project/chat and chosen time zone. Test one run and inspect it in **Scheduled**. Arrange for a workspace administrator to disable or remove the former user's task if it remains active, so two jobs cannot write the same index. The new task must use the new computer, config, sign-ins, and paths. Local scheduled tasks need that computer and the desktop app running; see [OpenAI's scheduled-task guide](https://learn.chatgpt.com/docs/automations).
4. Update the approved handoff record with the new maintainer and backup owner, repo location, site/library/index URLs, configuration choices, task name and cadence, last verified generation, run result, and any unresolved exceptions. Keep a second team member or admin able to reach the repository and SharePoint content so the next transfer does not depend on one person.

The transfer is complete only when the successor can run a scan and a scheduled maintenance cycle, publish a complete generation, and retrieve results with a normal reader account **without using the former owner's account or computer**. If an admin action, missing source file, or unsupported workspace transfer prevents this, record that exact blocker and keep the last verified catalog available until the new one passes the checks.

## Prompt for the LLM performing the transfer

> Take over this directory search plugin with no access to the former operator. Read `README.md`, `AGENTS.md`, `docs/OWNERSHIP_TRANSFER.md`, and `docs/MAINTENANCE.md`. Inventory the repository, `config.json` if present, the synced source, and the published SharePoint catalog. Fill a handoff record in an approved team location outside this repository. Verify the successor's SharePoint permissions and sync health. Create a new local state, import the existing complete catalog with `directory_handoff.py` when available, then rebuild and validate the entire index. Confirm every proposed removal in SharePoint before publishing. Regenerate and install the plugin, test retrieval as the successor and a normal reader, and create a new scheduled maintenance task. Find and retire any old writer task through the appropriate admin. Do all available local and read-only work first; request only the admin access or facts you cannot obtain. Never use the former user's credentials, silently discard the old catalog, or claim the transfer complete before an end-to-end run succeeds. Report evidence, remaining work, and who owns each blocker.
