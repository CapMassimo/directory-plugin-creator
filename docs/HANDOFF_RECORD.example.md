# Directory search handoff record — example fields

Copy this form to an approved **team-controlled location outside the public template repository**. An LLM can fill most fields from the live SharePoint site, published catalog, and configuration. Keep links and access decisions current. Do not include passwords, tokens, private keys, source documents, or extracted text.

## People and access

- New maintainer and backup maintainer:
- SharePoint site owner or administrator:
- Workspace plugin administrator:
- Approved reader group and index-folder access:
- Source-folder read access and index-folder write access checked on:

## Source and catalog

- SharePoint site URL:
- Default Documents library URL path:
- Source subfolder relative to library:
- Index folder inside source:
- Last verified catalog generation and UTC scan time:
- Catalog backup location and access owner:
- Source inventory count, named exceptions, and unresolved gaps:
- Was the former catalog imported as a removal baseline? If not, why?

## Repository and new operating environment

- Team-controlled repository location and version:
- New machine or managed host:
- Local synced `source_root` (host-specific):
- Local unsynced `state_dir` (host-specific):
- Python and package installation method:
- Codex CLI sign-in owner and approved CA bundle source (no secret material):
- Plugin name, display name, freshness threshold, and custom fields:
- Marketplace/install location and normal reader test result:

## Scheduled maintenance

- New task owner, name, cadence, time zone, and chat/project:
- First successful run and published generation:
- Former task retired or confirmed absent by:
- Backup maintainer's recovery path:

## Evidence and open work

- Sync-health check:
- `pending` and `validate` results:
- Removals checked against live SharePoint:
- Card, term shard, file, and folder retrieval checks:
- Admin actions still required, owner, and due date:
- Cutover accepted by and date:
